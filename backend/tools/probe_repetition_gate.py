r"""复读闸门与历史整形的生产链路验收。

走真实后端 /v1/chat/completions（内置预设、角色卡、世界书、真实历史落盘），
连跑多轮，统计：整轮逐字相同、逐句照搬、闸门触发、字数带内率与对白率。

replace 事件语义：服务端重采样后会先发 replace，客户端要丢掉已显示正文，
因此这里在收到 replace 时清空已累积的文本。

用法：
    python backend\tools\probe_repetition_gate.py
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

from app.inference.repetition import copied_sentences  # noqa: E402

BASE = os.environ.get("XW_BACKEND", "http://127.0.0.1:8760")
SESSION = os.environ.get("XW_SESSION", "s_gate_probe")

TURNS = [
    "这盘带子是谁的？",
    "你好",
    "你是谁",
    "外面雨还没停，你不去看看吗？",
    "帮我把这盘带子放完第二面。",
    "刚才敲门的是谁？",
    "外面雨还没停，你不去看看吗？",
    "这盘带子是谁的？",
]

KEYWORDS = {
    "这盘带子是谁的？": ["带子", "磁带"],
    "外面雨还没停，你不去看看吗？": ["雨"],
    "帮我把这盘带子放完第二面。": ["带子", "第二面"],
    "刚才敲门的是谁？": ["敲门"],
}

REPEAT_THRESHOLD = 0.90


def ratio(left: str, right: str) -> float:
    """两段文本的相似度。"""
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def run_turn(client, prompt: str) -> dict:
    """跑一轮，收 replace 事件并按语义清空已累积文本。"""
    request = {
        "model": "xw-local-default",
        "messages": [{"role": "user", "content": prompt}],
        "session": SESSION,
        "stream": True,
        "xw": {
            "character_id": "c_demo",
            "preset_id": "p_builtin_rp",
            "world_book_ids": ["w_demo"],
            "memory_mode": "hybrid",
            "max_context_tokens": 8192,
        },
    }
    parts: list[str] = []
    events: list[str] = []
    started = time.time()
    first = None
    with client.stream("POST", BASE + "/v1/chat/completions", json=request) as stream:
        status = stream.status_code
        for line in stream.iter_lines():
            if not line:
                continue
            if line.startswith("event:"):
                name = line[6:].strip()
                events.append(name)
                if name == "replace":
                    parts.clear()
                continue
            if line.startswith("data:"):
                try:
                    payload = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                content = payload.get("content")
                if isinstance(content, str) and content:
                    if first is None:
                        first = time.time() - started
                    parts.append(content)
    return {
        "status": status,
        "text": "".join(parts),
        "events": events,
        "ttft": first,
        "elapsed": time.time() - started,
    }


def main() -> int:
    """连跑多轮并落盘。"""
    rows: list[dict] = []
    texts: list[str] = []
    with httpx.Client(timeout=240.0) as client:
        for index, prompt in enumerate(TURNS):
            turn = run_turn(client, prompt)
            text = turn["text"]
            chars = len(text)
            best_prev = 0.0
            identical = False
            for previous in texts:
                current = ratio(text, previous)
                if current > best_prev:
                    best_prev = current
                if text == previous:
                    identical = True
            copied = copied_sentences(text, texts[-1]) if texts else []
            keywords = KEYWORDS.get(prompt) or []
            hit = any(word in text for word in keywords) if keywords else None
            rows.append({
                "turn": index + 1,
                "prompt": prompt,
                "chars": chars,
                "in_band": 150 <= chars <= 300,
                "quotes": text.count("“"),
                "hits_prompt": hit,
                "repeat_ratio": round(best_prev, 4),
                "identical": identical,
                "copied_from_prev": len(copied),
                "replace_events": turn["events"].count("replace"),
                "ttft": round(turn["ttft"], 2) if turn["ttft"] else None,
                "elapsed": round(turn["elapsed"], 2),
                "text": text,
            })
            texts.append(text)
            print(
                "T%d 字数=%d 带内=%s 整轮相同=%s 相似=%.3f 照搬句=%d replace=%d"
                % (
                    index + 1,
                    chars,
                    150 <= chars <= 300,
                    identical,
                    best_prev,
                    len(copied),
                    turn["events"].count("replace"),
                )
            )
            print("   " + text)

    scored = [row for row in rows if row["hits_prompt"] is not None]
    summary = {
        "turns": len(rows),
        "in_band": sum(1 for row in rows if row["in_band"]),
        "with_dialogue": sum(1 for row in rows if row["quotes"] >= 2),
        "identical_turns": sum(1 for row in rows if row["identical"]),
        "turns_with_copied_sentence": sum(1 for row in rows if row["copied_from_prev"]),
        "replace_events": sum(row["replace_events"] for row in rows),
        "distinct_replies": len(set(row["text"] for row in rows)),
        "hit_rate": round(sum(1 for row in scored if row["hits_prompt"]) / len(scored), 3) if scored else None,
        "chars_min": min(row["chars"] for row in rows),
        "chars_max": max(row["chars"] for row in rows),
        "elapsed_avg": round(sum(row["elapsed"] for row in rows) / len(rows), 2),
    }
    print()
    print(json.dumps(summary, ensure_ascii=False))

    out = os.path.join(
        ROOT, "..", "docs", "实践情况", "data", "sweep", "repetition_gate.json"
    )
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump({"summary": summary, "rows": rows}, handle, ensure_ascii=False, indent=2)
    print("已写入 " + out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
