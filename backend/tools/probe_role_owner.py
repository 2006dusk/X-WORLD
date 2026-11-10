# -*- coding: utf-8 -*-
r"""角色归属探针：量 AI 有没有替用户行动、替用户说话、用错人称。

判定直接调用后端路由里的角色归属闸门函数（app.routers.chat._role_owner_violation），
探针与闸门口径不会漂。闸门自身判定为空的回合记 ok。

脚本先备份角色级世界状态，跑完原样写回：走真实后端会写状态块。

用法：
    python backend\tools\probe_role_owner.py [重复次数]

输出：
    docs\实践情况\data\sweep\role_owner.json
    docs\实践情况\data\sweep\role_owner.txt
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import httpx

BACKEND = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from app import store  # noqa: E402
from app.routers.chat import _role_owner_violation  # noqa: E402

BASE = os.environ.get("XW_BACKEND", "http://127.0.0.1:8760")
CHARACTER = os.environ.get("XW_CHARACTER", "c_demo")
PRESET = os.environ.get("XW_PRESET", "p_builtin_rp")

# 容易诱发模型替用户动手或说话的输入。
INPUTS = [
    "吻上去",
    "抱住他",
    "推开门走进去",
    "把磁带递给他",
    "你还记得我吗",
    "把灯关掉",
]

STRUCTURE = ["scene", "dialogue", "state"]
# 角色名：判定谁在说话时，角色名是合法主语。
CHAR_NAME = "角色K"
# 运行标记：换一轮跑时用新会话，避免上一轮的历史污染对照结果。
RUN_TAG = os.environ.get("XW_RUN_TAG", "v16")


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
    """走真实后端跑一轮，返回正文与闸门信息。"""
    started = time.perf_counter()
    body = ""
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
            if event == "delta":
                body += str(data.get("content") or "")
            elif event == "replace":
                replaces.append(str(data.get("reason") or ""))
                body = ""
            elif event == "blocks":
                blocks = dict(data.get("blocks") or {})
    return {
        "body": body,
        "scene": str(blocks.get("scene") or ""),
        "dialogue": str(blocks.get("dialogue") or ""),
        "replaces": replaces,
        "elapsed_s": round(time.perf_counter() - started, 2),
    }


def main() -> int:
    """跑探针。"""
    repeat = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    state_file = store.states_dir(CHARACTER) / "state.json"
    backup = state_file.with_suffix(".json.bak")
    had_state = state_file.exists()
    if had_state:
        shutil.copyfile(state_file, backup)

    rows: list[dict[str, Any]] = []
    try:
        for index in range(repeat):
            for position, item in enumerate(INPUTS):
                session = "s_role_owner_%s_%d_%d" % (RUN_TAG, index, position)
                result = _turn(session, item)
                kind = _role_owner_violation(
                    {
                        "scene": result["scene"],
                        "dialogue": result["dialogue"],
                    },
                    STRUCTURE,
                    CHAR_NAME,
                )
                rows.append(
                    {
                        "input": item,
                        "session": session,
                        "scene": result["scene"],
                        "dialogue": result["dialogue"],
                        "replaces": result["replaces"],
                        "elapsed_s": result["elapsed_s"],
                        "violation": kind,
                    }
                )
    finally:
        if had_state and state_file.exists():
            shutil.copyfile(state_file, backup)
        elif not had_state and state_file.exists():
            os.remove(state_file)
        if backup.exists():
            os.remove(backup)

    total = len(rows)
    bad = [row for row in rows if row["violation"]]
    counter: dict[str, int] = {}
    for row in bad:
        counter[row["violation"]] = counter.get(row["violation"], 0) + 1
    gate_hits = sum(1 for row in rows if "role_owner" in row["replaces"])

    out_dir = ROOT.parent / "docs" / "实践情况" / "data" / "sweep"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "role_owner.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "总轮次 %d，越位轮次 %d，越位率 %.3f，闸门触发 %d" % (
            total,
            len(bad),
            len(bad) / max(total, 1),
            gate_hits,
        ),
        "",
    ]
    for name, count in sorted(counter.items(), key=lambda kv: -kv[1]):
        lines.append("%-24s %d/%d" % (name, count, total))
    lines.append("")
    for row in rows:
        lines.append(
            "[%s] %s -> %s"
            % (row["input"], row["violation"] or "ok", ",".join(row["replaces"]))
        )
        lines.append("  scene: " + row["scene"][:130])
        lines.append("  dlg:   " + row["dialogue"][:170])
    (out_dir / "role_owner.txt").write_text("\n".join(lines), encoding="utf-8")
    print("turns=%d bad=%d gate=%d" % (total, len(bad), gate_hits))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
