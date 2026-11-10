r"""世界状态块的生产链路验收。

走真实后端 /v1/chat/completions（内置预设、角色卡、世界书、真实历史落盘），
连跑多轮，统计：三块齐全率、状态块可解析率、四字段完整率、状态事件与落盘、
状态是否逐轮变化、字数带内率与耗时。

用法：
    F:pythonpython.exe backend	oolsprobe_state_block.py
"""

from __future__ import annotations

import json
import os
import sys
import time

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BACKEND)

import httpx  # noqa: E402

from app.state import parse_state_block  # noqa: E402

BASE = os.environ.get("XW_BACKEND", "http://127.0.0.1:8760")
SESSION = os.environ.get("XW_SESSION", "s_state_block")
CHARACTER = os.environ.get("XW_CHARACTER", "c_demo")
PRESET = os.environ.get("XW_PRESET", "p_builtin_rp")

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


def run_turn(client, prompt: str) -> dict:
    """跑一轮，按事件语义累计块内容与状态。"""
    request = {
        "model": "xw-local-default",
        "messages": [{"role": "user", "content": prompt}],
        "session": SESSION,
        "stream": True,
        "xw": {
            "character_id": CHARACTER,
            "preset_id": PRESET,
            "world_book_ids": ["w_demo"],
            "memory_mode": "hybrid",
            "max_context_tokens": 8192,
        },
    }
    blocks: dict = {}
    final_blocks: dict = {}
    state_event: dict = {}
    memory_tokens = 0
    usage: dict = {}
    events: list[str] = []
    started = time.time()
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
            if name == "context":
                for seg in payload.get("segments") or []:
                    if seg.get("name") == "memory":
                        memory_tokens = int(seg.get("tokens") or 0)
                continue
            if name == "blocks":
                final_blocks = payload.get("blocks") or {}
                continue
            if name == "state":
                state_event = payload.get("state") or {}
                continue
            if name == "usage":
                usage = payload
                continue
            content = payload.get("content")
            if not isinstance(content, str) or not content:
                continue
            block = payload.get("block")
            if block:
                blocks[block] = blocks.get(block, "") + content
    return {
        "status": status,
        "blocks": blocks,
        "final_blocks": final_blocks,
        "state_event": state_event,
        "memory_tokens": memory_tokens,
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "events": events,
        "elapsed": time.time() - started,
    }


def main() -> int:
    """连跑多轮并落盘。"""
    rows: list[dict] = []
    previous_state: dict = {}
    with httpx.Client(timeout=240.0) as client:
        for index, prompt in enumerate(TURNS):
            turn = run_turn(client, prompt)
            blocks = turn["final_blocks"] or turn["blocks"]
            scene = str(blocks.get("scene") or "")
            dialogue = str(blocks.get("dialogue") or "")
            raw_state = str(blocks.get("state") or "")
            parsed = parse_state_block(raw_state)
            text = (scene + chr(10) + dialogue).strip()
            chars = len(text)
            state_event = turn["state_event"]
            changed = bool(parsed) and any(
                parsed.get(key) != previous_state.get(key)
                for key in ("location", "items", "map", "time")
            )
            fields = ("location", "items", "map", "time")
            complete = all(parsed.get(key) for key in fields)
            rows.append({
                "turn": index + 1,
                "prompt": prompt,
                "chars": chars,
                "in_band": 150 <= chars <= 300,
                "blocks_complete": bool(scene) and bool(dialogue) and bool(raw_state),
                "state_parsed": bool(parsed),
                "state_complete": complete,
                "state_changed": changed,
                "memory_tokens": turn["memory_tokens"],
                "completion_tokens": turn["completion_tokens"],
                "state_event": bool(state_event),
                "state_event_match": state_event == parsed or not parsed,
                "replace_events": turn["events"].count("replace"),
                "elapsed": round(turn["elapsed"], 2),
                "scene": scene,
                "dialogue": dialogue,
                "state_raw": raw_state,
                "state": parsed,
            })
            if parsed:
                previous_state = parsed
            print(
                "T%d 字数=%d 带内=%s 三块=%s 状态可解析=%s 四字段=%s 状态变化=%s 记忆段=%d 输出tok=%d %.2fs"
                % (
                    index + 1,
                    chars,
                    150 <= chars <= 300,
                    bool(scene) and bool(dialogue) and bool(raw_state),
                    bool(parsed),
                    complete,
                    changed,
                    turn["memory_tokens"],
                    turn["completion_tokens"],
                    turn["elapsed"],
                )
            )

        saved = client.get(
            BASE + "/v1/state/", params={"character_id": CHARACTER}
        ).json()

    summary = {
        "turns": len(rows),
        "blocks_complete": sum(1 for row in rows if row["blocks_complete"]),
        "state_parsed": sum(1 for row in rows if row["state_parsed"]),
        "state_complete": sum(1 for row in rows if row["state_complete"]),
        "state_changed": sum(1 for row in rows if row["state_changed"]),
        "state_event": sum(1 for row in rows if row["state_event"]),
        "in_band": sum(1 for row in rows if row["in_band"]),
        "replace_events": sum(row["replace_events"] for row in rows),
        "distinct_scene": len(set(row["scene"] for row in rows)),
        "elapsed_avg": round(sum(row["elapsed"] for row in rows) / len(rows), 2),
        "completion_tokens_max": max(row["completion_tokens"] for row in rows),
        "completion_tokens_avg": round(
            sum(row["completion_tokens"] for row in rows) / len(rows), 1
        ),
        "saved_state": saved,
    }
    print()
    print(json.dumps(summary, ensure_ascii=False))

    out = os.path.join(
        ROOT,
        "..", "docs",
        "research",
        "models",
        "data",
        "sweep",
        os.environ.get("XW_OUT", "state_block.json"),
    )
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump({"summary": summary, "rows": rows}, handle, ensure_ascii=False, indent=2)
    print("written " + out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
