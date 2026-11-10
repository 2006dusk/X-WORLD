r"""回合长度标定实验。

历史压成最后一句后，复读归零但模型跟着写短（实测生产链路八轮 84 到 254 字，
带内率 3/8）。本实验只改回合要求的长度表达，量字数带内率：

  l150      —— 现行：全文不少于 150 字且不超过 300 字
  l180      —— 下限抬到 180 字
  l200      —— 下限抬到 200 字
  lquota    —— 150 到 300 字，另加结构配额（场景 60 到 100 字、动作 40 到 80 字）

消息序列用生产装配管线生成，历史按新规则整形，直连 llama.cpp server。

用法：
    python backend\tools\probe_length_tuning.py
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
from app.assembly.pipeline import assemble  # noqa: E402
from app.contracts import ChatRequest, XwOptions  # noqa: E402
from app.inference.repetition import copied_sentences  # noqa: E402

LLAMA = os.environ.get("XW_LLAMA", "http://127.0.0.1:8080")
CHARACTER = "c_demo"

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

VARIANTS = {
    "l150": {"min_chars": 150, "max_chars": 300},
    "lfloor180": {"min_chars": 180, "max_chars": 300},
    "lhint180": {"min_chars": 150, "max_chars": 300, "hint": 180},
    "lquota": {"min_chars": 150, "max_chars": 300, "quota": True},
}


def ratio(left: str, right: str) -> float:
    """两段文本的相似度。"""
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def build_preset(name: str, spec: dict) -> str:
    """按长度变体建一份临时预设。"""
    preset_id = "p_len_" + name
    payload = {
        "id": preset_id,
        "name": "长度标定 " + name,
        "prompts": [
            {
                "id": "rp_system",
                "name": "角色扮演契约",
                "content": preset_service.BUILTIN_SYSTEM_PROMPT,
                "role": "system",
                "order": 0,
                "enabled": True,
            }
        ],
        "output_spec": {
            "min_chars": spec["min_chars"],
            "max_chars": spec["max_chars"],
            "require_dialogue": True,
            "single_paragraph": True,
            "advance_plot": True,
            "forbid_meta": True,
        },
    }
    store.save_preset(payload)
    return preset_id


def directive_for(spec: dict) -> str:
    """回合要求文本；配额变体额外加结构配额。"""
    text = preset_service.render_output_spec({
        "min_chars": spec["min_chars"],
        "max_chars": spec["max_chars"],
        "require_dialogue": True,
        "single_paragraph": True,
        "advance_plot": True,
        "forbid_meta": True,
    })
    if spec.get("quota"):
        text = text + "场景描写写 60 到 100 字，人物动作写 40 到 80 字，对白一到三句。"
    if spec.get("hint"):
        text = text + "写到 " + str(spec["hint"]) + " 字左右。"
    return text


def generate(messages: list, directive: str) -> tuple:
    """把回合要求附在本轮输入末尾，直连推理服务生成一次。"""
    payload = dict(SAMPLING)
    payload["messages"] = list(messages)
    payload["messages"][-1] = {
        "role": "user",
        "content": payload["messages"][-1]["content"] + "\n\n" + directive,
    }
    started = time.time()
    with httpx.Client(timeout=240.0) as client:
        response = client.post(LLAMA + "/v1/chat/completions", json=payload)
    elapsed = time.time() - started
    if response.status_code != 200:
        raise RuntimeError(str(response.status_code) + ": " + response.text[:200])
    data = response.json()
    choice = (data.get("choices") or [{}])[0]
    return str(choice.get("message", {}).get("content") or ""), round(elapsed, 2)


def run_variant(name: str, spec: dict) -> dict:
    """跑一个长度变体。"""
    preset_id = build_preset(name, spec)
    session_id = "s_len_" + name
    directive = directive_for(spec)
    history: list = []
    texts: list = []
    rows: list = []

    for index, (prompt, keywords) in enumerate(TURNS):
        request = ChatRequest(
            model="xw-local-default",
            messages=[{"role": "user", "content": prompt}],
            session=session_id,
            stream=False,
            xw=XwOptions(
                character_id=CHARACTER,
                preset_id=preset_id,
                world_book_ids=["w_demo"],
                memory_mode="hybrid",
                max_context_tokens=8192,
            ),
        )
        context = assemble(request)
        messages = [{"role": m.role, "content": m.content} for m in context.messages]
        text, elapsed = generate(messages, directive)

        from_prev = copied_sentences(text, history[-1]) if history else []
        best = 0.0
        identical = False
        for previous in texts:
            best = max(best, ratio(text, previous))
            if text == previous:
                identical = True
        hit = any(word in text for word in keywords) if keywords else None

        rows.append({
            "turn": index + 1,
            "chars": len(text),
            "in_band": spec["min_chars"] <= len(text) <= spec["max_chars"],
            "in_150_300": 150 <= len(text) <= 300,
            "hits_prompt": hit,
            "copied_from_prev": len(from_prev),
            "max_ratio": round(best, 3),
            "identical": identical,
            "elapsed": elapsed,
            "text": text,
        })
        texts.append(text)
        store.append_message(session_id, CHARACTER, {"role": "user", "content": prompt})
        store.append_message(session_id, CHARACTER, {"role": "assistant", "content": text})
        history.append(text)

    scored = [row for row in rows if row["hits_prompt"] is not None]
    summary = {
        "turns": len(rows),
        "in_band": sum(1 for row in rows if row["in_band"]),
        "in_150_300": sum(1 for row in rows if row["in_150_300"]),
        "identical_turns": sum(1 for row in rows if row["identical"]),
        "turns_copy_prev": sum(1 for row in rows if row["copied_from_prev"]),
        "max_ratio": max(row["max_ratio"] for row in rows),
        "hit_rate": round(sum(1 for row in scored if row["hits_prompt"]) / len(scored), 3) if scored else None,
        "chars_min": min(row["chars"] for row in rows),
        "chars_max": max(row["chars"] for row in rows),
        "chars_avg": round(sum(row["chars"] for row in rows) / len(rows), 1),
        "elapsed_avg": round(sum(row["elapsed"] for row in rows) / len(rows), 2),
    }
    store.delete_preset(preset_id)
    return {"summary": summary, "rows": rows}


def main() -> int:
    """跑四个长度变体并落盘。"""
    results = {}
    for name, spec in VARIANTS.items():
        results[name] = run_variant(name, spec)
        print(name + " " + json.dumps(results[name]["summary"], ensure_ascii=False))

    out = os.path.join(
        ROOT, "..", "docs", "实践情况", "data", "sweep", "length_tuning.json"
    )
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(results, handle, ensure_ascii=False, indent=2)
    print("已写入 " + out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
