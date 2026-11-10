r"""结构化回合的生产链路验收。

走真实后端 /v1/chat/completions（内置预设、角色卡、世界书、真实历史落盘），
连跑多轮，统计：两块齐全率、对白带中文引号率、字数带内率、闸门触发与耗时。

事件语义：delta 带 block 字段指明归属的块；replace 事件表示后端重采样，
客户端要丢掉已显示正文，这里收到时清空已累积的块；blocks 事件是后端解析的
最终块，以它为准。

用法：
    python backend\tools\probe_struct_blocks.py
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

BASE = os.environ.get("XW_BACKEND", "http://127.0.0.1:8760")
SESSION = os.environ.get("XW_SESSION", "s_struct_blocks")

TURNS = [
    "这盘带子是谁的？",
    "你好",
    "你是谁",
    "外面雨还没停，你不去看看吗？",
    "帮我把这盘带子放完第二面。",
    "刚才敲门的是谁？",
    "你还记得那盘带子是从哪儿来的吗？",
    "外面雨还没停，你不去看看吗？",
]


def ratio(left: str, right: str) -> float:
    """两段文本的相似度。"""
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def run_turn(client, prompt: str) -> dict:
    """跑一轮，按事件语义累计块内容。"""
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
    blocks: dict = {}
    final_blocks: dict = {}
    labels: dict = {}
    events: list[str] = []
    reasons: list[str] = []
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
                    blocks = {}
                continue
            if not line.startswith("data:"):
                continue
            try:
                payload = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if name == "blocks":
                final_blocks = payload.get("blocks") or {}
                labels = payload.get("labels") or {}
                continue
            content = payload.get("content")
            if not isinstance(content, str) or not content:
                continue
            if first is None:
                first = time.time() - started
            block = payload.get("block")
            if block:
                blocks[block] = blocks.get(block, "") + content
    return {
        "status": status,
        "blocks": blocks,
        "final_blocks": final_blocks,
        "labels": labels,
        "events": events,
        "reasons": reasons,
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
            blocks = turn["final_blocks"] or turn["blocks"]
            scene = str(blocks.get("scene") or "")
            dialogue = str(blocks.get("dialogue") or "")
            text = (scene + chr(10) + dialogue).strip()
            chars = len(text)
            best_prev = 0.0
            identical = False
            for previous in texts:
                current = ratio(text, previous)
                if current > best_prev:
                    best_prev = current
                if text == previous:
                    identical = True
            rows.append({
                "turn": index + 1,
                "prompt": prompt,
                "chars": chars,
                "in_band": 150 <= chars <= 300,
                "scene_len": len(scene),
                "dialogue_len": len(dialogue),
                "blocks_complete": bool(scene) and bool(dialogue),
                "dialogue_has_quote": "“" in dialogue,
                "repeat_ratio": round(best_prev, 4),
                "identical": identical,
                "replace_events": turn["events"].count("replace"),
                "blocks_event": "blocks" in turn["events"],
                "ttft": round(turn["ttft"], 2) if turn["ttft"] else None,
                "elapsed": round(turn["elapsed"], 2),
                "scene": scene,
                "dialogue": dialogue,
            })
            texts.append(text)
            print(
                "T%d 字数=%d 带内=%s 两块齐全=%s 对白引号=%s 整轮相同=%s replace=%d %.2fs"
                % (
                    index + 1,
                    chars,
                    150 <= chars <= 300,
                    bool(scene) and bool(dialogue),
                    "“" in dialogue,
                    identical,
                    turn["events"].count("replace"),
                    turn["elapsed"],
                )
            )

    summary = {
        "turns": len(rows),
        "blocks_complete": sum(1 for row in rows if row["blocks_complete"]),
        "dialogue_has_quote": sum(1 for row in rows if row["dialogue_has_quote"]),
        "in_band": sum(1 for row in rows if row["in_band"]),
        "identical_turns": sum(1 for row in rows if row["identical"]),
        "blocks_events": sum(1 for row in rows if row["blocks_event"]),
        "replace_events": sum(row["replace_events"] for row in rows),
        "distinct_replies": len(set(row["scene"] + row["dialogue"] for row in rows)),
        "chars_min": min(row["chars"] for row in rows),
        "chars_max": max(row["chars"] for row in rows),
        "elapsed_avg": round(sum(row["elapsed"] for row in rows) / len(rows), 2),
    }
    print()
    print(json.dumps(summary, ensure_ascii=False))

    out = os.path.join(
        ROOT, "..", "docs", "实践情况", "data", "sweep", "struct_blocks.json"
    )
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump({"summary": summary, "rows": rows}, handle, ensure_ascii=False, indent=2)
    print("written " + out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
