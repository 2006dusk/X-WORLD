r"""历史形态对照实验：上一轮回复在上下文里保留多少。

上一轮实测确认：复读的直接来源是模型自己的上一轮回复全文留在上下文里。
本实验固定角色卡注入（不含 mes_example）与预设（回合要求只写一遍），
只改历史里 assistant 消息的形态：
  full          —— 原样保留（对照）
  last_sent     —— 只保留最后一句完整句
  tail60        —— 保留末尾 60 字
  tail40        —— 保留末尾 40 字
  last_sent_1turn —— 只带最近一轮，且只保留最后一句

除复读指标外，还测「是否回应本轮输入」：回复里是否出现本轮提示的关键词，
用来确认压复读没有把模型逼成无视用户输入。

用法：
    python backend\tools\probe_history_shape.py
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

TURNS = [
    ("这盘带子是谁的？", ["带子", "磁带"]),
    ("你好", []),
    ("你是谁", ["谁"]),
    ("外面雨还没停，你不去看看吗？", ["雨"]),
    ("帮我把这盘带子放完第二面。", ["带子", "第二面"]),
    ("刚才敲门的是谁？", ["敲门"]),
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


def last_sentence(text: str) -> str:
    """取最后一句以句末标点收尾的完整句；没有则退回末尾 40 字。"""
    sentences = split_sentences(text)
    for sentence in reversed(sentences):
        if sentence.endswith(("。", "！", "？")):
            return sentence
    return text[-40:]


def shape(text: str, variant: str) -> str:
    """按变体裁剪 assistant 历史。"""
    if variant == "full":
        return text
    if variant == "last_sent":
        return last_sentence(text)
    if variant == "tail60":
        return text[-60:]
    if variant == "tail40":
        return text[-40:]
    return text


def world_entries(prompt: str) -> list:
    """按用户输入匹配世界书条目。"""
    entries = []
    book = store.get_world_book("w_demo")
    if isinstance(book, dict):
        for index, entry in enumerate(book.get("entries") or []):
            if not isinstance(entry, dict):
                continue
            keys = entry.get("keys", "")
            if isinstance(keys, list):
                keys = ", ".join(str(k).strip() for k in keys if str(k).strip())
            entries.append({
                "id": str(entry.get("id") or "w_demo__" + str(index)),
                "book_id": "w_demo",
                "keys": str(keys),
                "content": str(entry.get("content", "")),
                "priority": int(entry.get("priority", 100)),
            })
    return match_entries(entries, prompt)


def build_messages(variant: str, prompt: str, history: list) -> list:
    """拼出完整消息序列。"""
    preset = store.get_preset(PRESET_ID)
    parts = []
    for group in preset.get("prompts") or []:
        if str(group.get("role")) == "system" and str(group.get("content", "")).strip():
            parts.append(str(group["content"]))

    data = (store.get_card(CHARACTER) or {}).get("data") or {}
    for field in ("description", "personality", "scenario"):
        value = str(data.get(field, "")).strip()
        if value:
            parts.append(value)

    system_text = "\n".join(parts)
    for entry in world_entries(prompt):
        system_text = system_text + "\n" + str(entry.content)

    messages = [{"role": "system", "content": system_text}]

    used = history
    for keep in (1, 2, 3):
        if variant == "last_sent_" + str(keep) + "turn":
            used = history[-2 * keep:] if len(history) >= 2 * keep else history
            break
    for item in used:
        content = item["content"]
        if item["role"] == "assistant":
            content = shape(content, "last_sent" if variant.startswith("last_sent") else variant)
        messages.append({"role": item["role"], "content": content})

    spec = preset.get("output_spec")
    directive = preset_service.render_output_spec(spec if isinstance(spec, dict) else {})
    user_text = prompt + "\n\n" + directive if directive else prompt
    messages.append({"role": "user", "content": user_text})
    return messages


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
    for index, (prompt, keywords) in enumerate(TURNS):
        messages = build_messages(variant, prompt, history)
        text, elapsed = generate(messages)

        from_prev = copied_sentences(text, history[-1]["content"]) if history else []
        best = 0.0
        identical = False
        for previous in texts:
            best = max(best, ratio(text, previous))
            if text == previous:
                identical = True
        hit = None
        if keywords:
            hit = any(word in text for word in keywords)

        rows.append({
            "turn": index + 1,
            "prompt": prompt,
            "chars": len(text),
            "in_band": 150 <= len(text) <= 300,
            "hits_prompt": hit,
            "copied_from_prev": len(from_prev),
            "max_ratio": round(best, 3),
            "identical": identical,
            "elapsed": elapsed,
            "text": text,
        })
        texts.append(text)
        history.append({"role": "user", "content": prompt})
        history.append({"role": "assistant", "content": text})

    scored = [row for row in rows if row["hits_prompt"] is not None]
    summary = {
        "turns": len(rows),
        "in_band": sum(1 for row in rows if row["in_band"]),
        "identical_turns": sum(1 for row in rows if row["identical"]),
        "turns_copy_prev": sum(1 for row in rows if row["copied_from_prev"]),
        "max_ratio": max(row["max_ratio"] for row in rows),
        "hit_rate": round(sum(1 for row in scored if row["hits_prompt"]) / len(scored), 3) if scored else None,
        "chars_min": min(row["chars"] for row in rows),
        "chars_max": max(row["chars"] for row in rows),
        "elapsed_avg": round(sum(row["elapsed"] for row in rows) / len(rows), 2),
    }
    return {"summary": summary, "rows": rows}


def main() -> int:
    """跑变体并落盘。命令行参数可指定变体名，默认跑全部。"""
    variants = sys.argv[1:] or [
        "full", "last_sent", "tail60", "tail40", "last_sent_1turn",
        "last_sent_2turn", "last_sent_3turn",
    ]
    results = {}
    for variant in variants:
        results[variant] = run_variant(variant)
        print(variant + " " + json.dumps(results[variant]["summary"], ensure_ascii=False))

    out = os.path.join(
        ROOT, "..", "docs", "实践情况", "data", "sweep", "history_shape.json"
    )
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(results, handle, ensure_ascii=False, indent=2)
    print("已写入 " + out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
