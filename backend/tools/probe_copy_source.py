r"""复制型复读的素材来源对照实验。

已知：模型在上下文里看到具体叙事文本时，会把它当正文素材逐字搬走。
候选素材来源有三处：角色卡的 scenario 与 mes_example、世界书条目、自己的上一轮回复。

本实验直连 llama.cpp server，手工拼消息序列，每个变体只改一处：
  as_is     —— 与生产装配一致（角色卡 scenario + mes_example 全量注入，历史原样）
  no_mes    —— 角色卡不注入 mes_example
  no_scen   —— 角色卡只注入 description 与 personality
  marked    —— 全量注入，但给 scenario 与 mes_example 加「不要照抄」标注
  hist_short—— 角色卡照旧，历史里的上一轮回复只保留末尾 40 字

指标：照抄角色卡的句数、照抄上一轮的句数、与历史轮次的最大相似度。

用法：
    python backend\tools\probe_copy_source.py
"""

from __future__ import annotations

import difflib
import json
import os
import sys
import time

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BACKEND)

import httpx  # noqa: E402

from app import presets as preset_service  # noqa: E402
from app import store  # noqa: E402
from app.assembly.matching import match_entries  # noqa: E402
from app.inference.repetition import copied_sentences, split_sentences  # noqa: E402

LLAMA = os.environ.get("XW_LLAMA", "http://127.0.0.1:8080")
CHARACTER = "c_demo"
PRESET_ID = "p_ab_v2_no_example"
HISTORY_SHORT = 40

TURNS = [
    "这盘带子是谁的？",
    "你好",
    "你是谁",
    "外面雨还没停，你不去看看吗？",
    "帮我把这盘带子放完第二面。",
    "刚才敲门的是谁？",
]

SAMPLING = {
    "temperature": 0.7,
    "top_p": 0.9,
    "max_tokens": 512,
    "repeat_penalty": 1.1,
    "top_k": 40,
    "min_p": 0.05,
}


def ratio(left, right) -> float:
    """两段文本的相似度。"""
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def card_payload() -> dict:
    """角色卡的 data 段。"""
    card = store.get_card(CHARACTER)
    data = card.get("data") if isinstance(card, dict) else None
    return data if isinstance(data, dict) else {}


def world_entries(prompt: str) -> list:
    """按用户输入匹配世界书条目，归一成匹配层需要的字段。"""
    entries = []
    for book_id in ("w_demo",):
        book = store.get_world_book(book_id)
        if not isinstance(book, dict):
            continue
        for index, entry in enumerate(book.get("entries") or []):
            if not isinstance(entry, dict):
                continue
            keys = entry.get("keys", "")
            if isinstance(keys, list):
                keys = ", ".join(str(k).strip() for k in keys if str(k).strip())
            entries.append({
                "id": str(entry.get("id") or book_id + "__" + str(index)),
                "book_id": book_id,
                "keys": str(keys),
                "content": str(entry.get("content", "")),
                "priority": int(entry.get("priority", 100)),
            })
    return match_entries(entries, prompt)


def build_system(variant: str) -> tuple:
    """按变体拼 system 段，返回正文与「可被照抄的素材句」列表。"""
    preset = store.get_preset(PRESET_ID)
    parts = []
    for group in preset.get("prompts") or []:
        if str(group.get("role")) == "system" and str(group.get("content", "")).strip():
            parts.append(str(group["content"]))

    data = card_payload()
    description = str(data.get("description", "")).strip()
    personality = str(data.get("personality", "")).strip()
    scenario = str(data.get("scenario", "")).strip()
    mes_example = str(data.get("mes_example", "")).strip()

    material = []
    if variant in ("as_is", "marked", "hist_short"):
        parts.append(description)
        parts.append(personality)
        if variant == "marked":
            parts.append("【场景设定，只作背景，不要照抄原文】" + scenario)
            parts.append("【示例对话，只参考语气，不要照抄原句】" + mes_example)
        else:
            parts.append(scenario)
            parts.append(mes_example)
        material.extend(split_sentences(scenario))
        material.extend(split_sentences(mes_example))
    elif variant == "mes_stripped":
        parts.append(description)
        parts.append(personality)
        parts.append(scenario)
        cleaned = mes_example.replace("<START>", "")
        cleaned = cleaned.replace("{{user}}:", "").replace("{{char}}:", "")
        parts.append(cleaned)
        material.extend(split_sentences(scenario))
        material.extend(split_sentences(cleaned))
    elif variant == "no_mes":
        parts.append(description)
        parts.append(personality)
        parts.append(scenario)
        material.extend(split_sentences(scenario))
    else:
        parts.append(description)
        parts.append(personality)

    return "\n".join(p for p in parts if p), material


def build_messages(variant: str, prompt: str, history: list) -> tuple:
    """拼出完整消息序列，返回消息列表与素材句。"""
    system_text, material = build_system(variant)
    for entry in world_entries(prompt):
        material.extend(split_sentences(str(entry.content)))
        system_text = system_text + "\n" + str(entry.content)

    messages = [{"role": "system", "content": system_text}]
    for item in history:
        content = item["content"]
        if variant == "hist_short" and item["role"] == "assistant":
            content = content[-HISTORY_SHORT:]
        messages.append({"role": item["role"], "content": content})

    spec = store.get_preset(PRESET_ID).get("output_spec")
    directive = preset_service.render_output_spec(spec if isinstance(spec, dict) else {})
    user_text = prompt + "\n\n" + directive if directive else prompt
    messages.append({"role": "user", "content": user_text})
    return messages, material


def generate(messages: list) -> tuple:
    """直连推理服务生成一次。"""
    payload = dict(SAMPLING)
    payload["messages"] = messages
    started = time.time()
    with httpx.Client(timeout=240.0) as client:
        response = client.post(LLAMA + "/v1/chat/completions", json=payload)
    elapsed = time.time() - started
    if response.status_code != 200:
        raise RuntimeError(str(response.status_code) + ": " + response.text[:200])
    data = response.json()
    choice = (data.get("choices") or [{}])[0]
    return str(choice.get("message", {}).get("content") or ""), round(elapsed, 2)


def run_variant(variant: str) -> dict:
    """跑一个变体。"""
    history: list = []
    texts: list = []
    rows: list = []
    for index, prompt in enumerate(TURNS):
        messages, material = build_messages(variant, prompt, history)
        text, elapsed = generate(messages)

        from_material = [
            sentence for sentence in material
            if len(sentence.rstrip("。！？").strip()) >= 15
            and sentence.rstrip("。！？").strip() in text
        ]
        from_prev = copied_sentences(text, history[-1]["content"]) if history else []
        best = 0.0
        identical = False
        for previous in texts:
            best = max(best, ratio(text, previous))
            if text == previous:
                identical = True

        rows.append({
            "turn": index + 1,
            "prompt": prompt,
            "chars": len(text),
            "in_band": 150 <= len(text) <= 300,
            "copied_from_material": len(from_material),
            "copied_from_prev": len(from_prev),
            "max_ratio": round(best, 3),
            "identical": identical,
            "elapsed": elapsed,
            "text": text,
        })
        texts.append(text)
        history.append({"role": "user", "content": prompt})
        history.append({"role": "assistant", "content": text})

    summary = {
        "turns": len(rows),
        "in_band": sum(1 for row in rows if row["in_band"]),
        "identical_turns": sum(1 for row in rows if row["identical"]),
        "turns_copy_material": sum(1 for row in rows if row["copied_from_material"]),
        "copy_material_total": sum(row["copied_from_material"] for row in rows),
        "turns_copy_prev": sum(1 for row in rows if row["copied_from_prev"]),
        "max_ratio": max(row["max_ratio"] for row in rows),
        "chars_min": min(row["chars"] for row in rows),
        "chars_max": max(row["chars"] for row in rows),
        "elapsed_avg": round(sum(row["elapsed"] for row in rows) / len(rows), 2),
    }
    return {"summary": summary, "rows": rows}


def main() -> int:
    """跑五个变体并落盘。"""
    results = {}
    variants = sys.argv[1:] or ("as_is", "no_mes", "no_scen", "marked", "hist_short", "mes_stripped")
    for variant in variants:
        results[variant] = run_variant(variant)
        print(variant + " " + json.dumps(results[variant]["summary"], ensure_ascii=False))

    out = os.path.join(
        ROOT, "..", "docs", "实践情况", "data", "sweep", "copy_source.json"
    )
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(results, handle, ensure_ascii=False, indent=2)
    print("已写入 " + out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
