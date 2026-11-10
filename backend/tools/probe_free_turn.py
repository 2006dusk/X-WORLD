r"""自由回合契约的生产链路探针。

走真实后端 SSE 链路（后端在 127.0.0.1:8760），检查新的回合契约是否生效：

- 场景块与对白块可选、顺序自由，模型可以先开口说话；
- 回合不设字数下限，长度闸门不触发：流里不应出现 reason=length 的 replace；
- 环境描写用第三人称，场景块里不出现「你」「我」；对白块里允许「我」；
- 状态块仍然写出并能解析成五个字段；
- 闲聊回合（一句「你好」）也能正常走完，不被硬凑成场景加对白。

用法：
    python backend\tools\probe_free_turn.py
输出：
    docs\实践情况\data\sweep\free_turn.json
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[2]

API = os.environ.get("XW_BACKEND", "http://127.0.0.1:8760")
SESSION = os.environ.get("XW_SESSION", "s_free_turn")
CHARACTER = os.environ.get("XW_CHARACTER", "c_demo")
PRESET = os.environ.get("XW_PRESET", "p_builtin_rp")

# 一轮剧情推进加一句闲聊，用来验证两种输入都被正常处理。
TURNS = [
    "外面雨还没停，你不去看看吗？",
    "前面有家地址V，我们进去吧。",
    "点单，看看喝什么。",
    "你好",
]


def request_body(prompt: str) -> dict:
    """构造一轮会话生成请求。"""
    return {
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


def run_turn(client: httpx.Client, prompt: str) -> dict:
    """跑一轮，收齐 SSE 事件后统计这一轮的形态。"""
    started = time.time()
    events: list[dict] = []
    deltas: list[tuple[str, str]] = []
    with client.stream(
        "POST",
        API + "/v1/chat/completions",
        json=request_body(prompt),
        timeout=300.0,
    ) as response:
        response.raise_for_status()
        name = ""
        for line in response.iter_lines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                payload = line[5:].strip()
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                events.append({"event": name, "data": data})
                if name == "delta":
                    deltas.append((str(data.get("block") or ""), str(data.get("content") or "")))

    blocks: dict[str, str] = {}
    for item in events:
        if item["event"] == "blocks":
            blocks = dict(item["data"].get("blocks") or {})
    state = None
    for item in events:
        if item["event"] == "state":
            state = item["data"].get("state")
    replaces = [
        item["data"].get("reason")
        for item in events
        if item["event"] == "replace"
    ]
    done = next((i for i in events if i["event"] == "done"), None)

    scene = str(blocks.get("scene") or "")
    dialogue = str(blocks.get("dialogue") or "")
    body = len(scene) + len(dialogue)
    order = [k for k, v in blocks.items() if v]
    return {
        "prompt": prompt,
        "block_order": order,
        "blocks": blocks,
        "body_chars": body,
        "scene_has_you": "你" in scene,
        "scene_has_wo": "我" in scene,
        "dialogue_has_wo": "我" in dialogue,
        "opened_with_dialogue": bool(order) and order[0] == "dialogue",
        "has_scene": bool(scene),
        "has_dialogue": bool(dialogue),
        "state_fields": sorted((state or {}).keys()),
        "replace_reasons": replaces,
        "finished": done is not None,
        "elapsed": round(time.time() - started, 2),
        "event_names": [i["event"] for i in events],
    }


def main() -> int:
    """跑完所有轮次并落盘。"""
    rows: list[dict] = []
    with httpx.Client() as client:
        for prompt in TURNS:
            row = run_turn(client, prompt)
            rows.append(row)
            print(
                "轮次=%d 字数=%3d 块序=%s 场景带你=%s 场景带我=%s 替换=%s %.2fs"
                % (
                    len(rows),
                    row["body_chars"],
                    ",".join(row["block_order"]),
                    row["scene_has_you"],
                    row["scene_has_wo"],
                    row["replace_reasons"],
                    row["elapsed"],
                )
            )

    summary = {
        "turns": len(rows),
        "avg_body_chars": round(sum(r["body_chars"] for r in rows) / len(rows), 1),
        "opened_with_dialogue": sum(1 for r in rows if r["opened_with_dialogue"]),
        "no_scene_turns": sum(1 for r in rows if not r["has_scene"]),
        "no_dialogue_turns": sum(1 for r in rows if not r["has_dialogue"]),
        "scene_contains_you": sum(1 for r in rows if r["scene_has_you"]),
        "scene_contains_wo": sum(1 for r in rows if r["scene_has_wo"]),
        "length_rewrites": sum(
            1 for r in rows if "length" in [str(x) for x in r["replace_reasons"]]
        ),
        "all_finished": all(r["finished"] for r in rows),
    }
    print()
    print(json.dumps(summary, ensure_ascii=False))

    out = ROOT.parent / "docs" / "实践情况" / "data" / "sweep" / "free_turn.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("written " + str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
