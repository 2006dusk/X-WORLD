# -*- coding: utf-8 -*-
r"""历史里留着漂移内容时，新引擎会不会被拽回旧场景。

把 s_demo 当前路径的最后几轮复制到一个副本会话，再发一轮，看场景是否
还落在咖啡与厨房。s_demo 本身不写入。

用法：
    python backend\tools\probe_history_drift.py [源会话] [轮数]
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

BACKEND = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from app import store  # noqa: E402

BASE = os.environ.get("XW_BACKEND", "http://127.0.0.1:8760")
CHARACTER = os.environ.get("XW_CHARACTER", "c_demo")
PRESET = os.environ.get("XW_PRESET", "p_builtin_rp")
COPY = "s_role_owner_hist"

DRIFT_WORDS = ("厨房", "咖啡", "牛奶", "吧台", "店员", "老板", "顾客", "奶泡", "咖啡豆")
PLACE_WORDS = ("地址U", "控制台", "台灯", "地址N", "巷子", "录音设备", "耳机")


def _payload(session: str, text: str) -> dict[str, Any]:
    return {
        "model": "xw-local-default",
        "messages": [{"role": "user", "content": text}],
        "session": session,
        "stream": True,
        "xw": {
            "character_id": CHARACTER,
            "preset_id": PRESET,
            "world_book_ids": ["w_demo"],
            "memory_mode": "hybrid",
            "max_context_tokens": 16384,
        },
    }


def _turn(session: str, text: str) -> dict[str, Any]:
    """跑一轮，返回场景与对白。"""
    started = time.perf_counter()
    blocks: dict[str, str] = {}
    replaces: list[str] = []
    with httpx.stream(
        "POST",
        BASE + "/v1/chat/completions",
        json=_payload(session, text),
        timeout=600.0,
    ) as response:
        response.raise_for_status()
        event = ""
        for raw in response.iter_lines():
            if raw.startswith("event: "):
                event = raw[7:].strip()
                continue
            if not raw.startswith("data: "):
                continue
            data = json.loads(raw[6:])
            if event == "replace":
                replaces.append(str(data.get("reason") or ""))
            elif event == "blocks":
                blocks = dict(data.get("blocks") or {})
    return {
        "scene": str(blocks.get("scene") or ""),
        "dialogue": str(blocks.get("dialogue") or ""),
        "replaces": replaces,
        "elapsed_s": round(time.perf_counter() - started, 2),
    }


def main() -> int:
    """复制历史并跑一轮。"""
    source = sys.argv[1] if len(sys.argv) > 1 else "s_demo"
    turns = int(sys.argv[2]) if len(sys.argv) > 2 else 4

    rows = store.session_path(source, CHARACTER, None)
    tail = rows[-turns * 2 :] if turns else []
    for row in tail:
        payload = {
            "role": str(row.get("role", "user")),
            "content": str(row.get("content", "")),
        }
        blocks = row.get("blocks")
        if isinstance(blocks, dict):
            payload["blocks"] = blocks
        store.append_message(COPY, CHARACTER, payload)

    result = _turn(COPY, "我推开门走进去")
    scene = result["scene"]
    drift = [word for word in DRIFT_WORDS if word in scene]
    place = [word for word in PLACE_WORDS if word in scene]

    out_dir = ROOT.parent / "docs" / "实践情况" / "data" / "sweep"
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "源会话 %s，复制轮数 %d" % (source, turns),
        "闸门 %s，耗时 %.2f 秒" % (",".join(result["replaces"]), result["elapsed_s"]),
        "漂移词 %s，地点词 %s" % (drift, place),
        "",
        "【场景】" + scene,
        "",
        "【对话】" + result["dialogue"],
    ]
    (out_dir / "history_drift.txt").write_text("\n".join(lines), encoding="utf-8")
    print("drift=%s place=%s" % (drift, place))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
