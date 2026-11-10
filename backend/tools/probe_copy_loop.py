r"""复制型复读的单变量对照实验。

现象：内置预设落库后，模型在多轮里把自己的上一轮回复逐字搬过来。
两个候选原因：
  A. 内置预设的 system 提示里那句回合要求被写了两遍。
  B. 上下文里带着自己上一轮回复，模型进入复制模式。

本实验直连 llama.cpp server（不经后端路由），因此不含复读闸门与段落压平，
测到的是模型本身的原始行为。每个变体只改一个变量：
  dup      —— 用内置预设 p_builtin_rp（含重复句）
  once     —— 用对照预设 p_ab_v2_no_example（除该句只写一遍外完全相同）
  once_cold—— 同 once，但每轮都用新会话，无历史

用法：
    python backend\tools\probe_copy_loop.py
"""

from __future__ import annotations

import difflib
import json
import os
import sys
import time

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(BACKEND))
sys.path.insert(0, BACKEND)

import httpx  # noqa: E402

from app import store  # noqa: E402
from app.assembly.pipeline import assemble  # noqa: E402
from app.contracts import ChatRequest, XwOptions  # noqa: E402
from app.inference.repetition import copied_sentences, split_sentences  # noqa: E402

LLAMA = os.environ.get("XW_LLAMA", "http://127.0.0.1:8080")
CHARACTER = "c_demo"
WORLD_BOOKS = ["w_demo"]

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


def ratio(left: str, right: str) -> float:
    """两段文本的相似度。"""
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def internal_dup(text: str) -> float:
    """单次生成内的重复度：重复出现的句子占比。"""
    parts = [s.rstrip("。！？").strip() for s in split_sentences(text)]
    parts = [s for s in parts if len(s) >= 10]
    if len(parts) < 2:
        return 0.0
    return round(1 - len(set(parts)) / len(parts), 3)


def build_messages(session_id: str, preset_id: str, prompt: str):
    """按生产装配口径生成消息序列。"""
    request = ChatRequest(
        model="xw-local-default",
        messages=[{"role": "user", "content": prompt}],
        session=session_id,
        stream=False,
        xw=XwOptions(
            character_id=CHARACTER,
            preset_id=preset_id,
            world_book_ids=list(WORLD_BOOKS),
            memory_mode="hybrid",
            max_context_tokens=8192,
        ),
    )
    context = assemble(request)
    return context.messages, context.report


def generate(messages) -> tuple[str, float, str]:
    """直连推理服务生成一次，返回正文、耗时与结束原因。"""
    payload = dict(SAMPLING)
    payload["messages"] = [
        {"role": item.role, "content": item.content} for item in messages
    ]
    started = time.time()
    with httpx.Client(timeout=240.0) as client:
        response = client.post(LLAMA + "/v1/chat/completions", json=payload)
    elapsed = time.time() - started
    if response.status_code != 200:
        raise RuntimeError("推理返回 " + str(response.status_code) + ": " + response.text[:200])
    data = response.json()
    choice = (data.get("choices") or [{}])[0]
    content = str(choice.get("message", {}).get("content") or "")
    return content, elapsed, str(choice.get("finish_reason") or "")


def run_variant(name: str, preset_id: str, cold: bool) -> dict:
    """跑一个变体，返回逐轮记录与汇总。"""
    base_session = "s_copy_" + name
    rows: list[dict] = []
    texts: list[str] = []

    for index, prompt in enumerate(TURNS):
        session_id = base_session + "_t" + str(index + 1) if cold else base_session
        if not cold:
            store.ensure_session(session_id, CHARACTER, preset_id)
        messages, report = build_messages(session_id, preset_id, prompt)
        text, elapsed, finish_reason = generate(messages)

        best_ratio = 0.0
        identical = False
        for previous in texts:
            current = ratio(text, previous)
            if current > best_ratio:
                best_ratio = current
            if text == previous:
                identical = True
        copied = copied_sentences(text, texts[-1]) if texts else []
        rows.append({
            "turn": index + 1,
            "prompt": prompt,
            "chars": len(text),
            "in_band": 150 <= len(text) <= 300,
            "quotes": text.count("“"),
            "identical_to_earlier": identical,
            "max_ratio_vs_earlier": round(best_ratio, 3),
            "copied_from_prev": len(copied),
            "internal_dup": internal_dup(text),
            "finish_reason": finish_reason,
            "history_tokens": next(
                (seg.tokens for seg in report.segments if seg.name == "history"), 0
            ),
            "elapsed": round(elapsed, 2),
            "text": text,
        })
        texts.append(text)

        if not cold:
            store.append_message(session_id, CHARACTER, {"role": "user", "content": prompt})
            store.append_message(
                session_id, CHARACTER, {"role": "assistant", "content": text}
            )

    summary = {
        "turns": len(rows),
        "in_band": sum(1 for row in rows if row["in_band"]),
        "with_dialogue": sum(1 for row in rows if row["quotes"] >= 2),
        "identical_turns": sum(1 for row in rows if row["identical_to_earlier"]),
        "turns_with_copied_sentence": sum(1 for row in rows if row["copied_from_prev"]),
        "distinct_replies": len(set(row["text"] for row in rows)),
        "max_ratio": max(row["max_ratio_vs_earlier"] for row in rows),
        "chars_min": min(row["chars"] for row in rows),
        "chars_max": max(row["chars"] for row in rows),
        "elapsed_avg": round(sum(row["elapsed"] for row in rows) / len(rows), 2),
    }
    return {"summary": summary, "rows": rows}


def main() -> int:
    """跑三个变体并落盘。"""
    variants = [
        ("dup", "p_builtin_rp", False),
        ("once", "p_ab_v2_no_example", False),
        ("once_cold", "p_ab_v2_no_example", True),
    ]
    results: dict = {}
    for name, preset_id, cold in variants:
        result = run_variant(name, preset_id, cold)
        results[name] = {"preset_id": preset_id, "cold": cold, **result}
        print(name + " " + json.dumps(result["summary"], ensure_ascii=False))

    out = os.path.join(
        ROOT, "..", "docs", "实践情况", "data", "sweep", "copy_loop.json"
    )
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(results, handle, ensure_ascii=False, indent=2)
    print("已写入 " + out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
