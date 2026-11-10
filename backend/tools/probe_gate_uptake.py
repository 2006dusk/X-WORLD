# -*- coding: utf-8 -*-
r"""对白承接闸门的多轮真机验证（流式口径）。

用真实后端的流式接口跑两轮对话：先问有酒吗，再打招呼。
看第二轮的对白是在答旧问题还是接住了这一轮，并统计闸门触发次数与等待时间。

脚本会先备份角色级世界状态，跑完原样写回：探针走真实后端，
状态块会落盘到 state 目录，不还原会覆盖正在玩的会话状态。

用法：
    python backend\tools\probe_gate_uptake.py [重复次数]

输出：
    docs\实践情况\data\sweep\gate_uptake.json
    docs\实践情况\data\sweep\gate_uptake.txt
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

BASE = os.environ.get("XW_BACKEND", "http://127.0.0.1:8760")
CHARACTER = os.environ.get("XW_CHARACTER", "c_demo")
PRESET = os.environ.get("XW_PRESET", "p_builtin_rp")

GREET = ("晚上", "夜好", "你好", "问候", "回来", "睡", "晚安", "这么晚")
OLD = ("酒", "威士忌", "金酒", "喝")


def _sentences(text: str) -> int:
    return sum(1 for ch in text if ch in "。！？")


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
    """走真实后端流式跑一轮，返回对白、闸门触发与耗时。"""
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
    elapsed = time.perf_counter() - started
    dialogue = str(blocks.get("dialogue") or "")
    return {
        "dialogue": dialogue,
        "scene": str(blocks.get("scene") or ""),
        "replaces": replaces,
        "elapsed_s": round(elapsed, 2),
        "dialogue_sentences": _sentences(dialogue),
        "dialogue_chars": len(dialogue),
        "greets": any(word in dialogue for word in GREET),
        "answers_old": any(word in dialogue for word in OLD),
    }


def main() -> int:
    """跑验证。"""
    repeat = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    state_file = store.states_dir(CHARACTER) / "state.json"
    backup = state_file.with_suffix(".json.bak")
    had_state = state_file.exists()
    if had_state:
        shutil.copyfile(state_file, backup)

    rows: list[dict[str, Any]] = []
    lines: list[str] = []
    try:
        for index in range(repeat):
            session = "s_gate_uptake_%d" % index
            first = _turn(session, "有酒吗")
            second = _turn(session, "晚上好")
            rows.append({"session": session, "first": first, "second": second})
            lines.append(
                "run%d greet=%s old=%s sent=%d dlg=%d replaces=%s wait=%.2fs :: %s"
                % (
                    index,
                    second["greets"],
                    second["answers_old"],
                    second["dialogue_sentences"],
                    second["dialogue_chars"],
                    ",".join(second["replaces"]) or "-",
                    second["elapsed_s"],
                    second["dialogue"].replace("\n", " / ")[:110],
                )
            )
    finally:
        if had_state and backup.exists():
            shutil.copyfile(backup, state_file)
            backup.unlink()
            lines.append("state restored")

    second_rows = [row["second"] for row in rows]
    greets = sum(1 for row in second_rows if row["greets"])
    old = sum(1 for row in second_rows if row["answers_old"] and not row["greets"])
    sent = sum(row["dialogue_sentences"] for row in second_rows) / len(second_rows)
    wait = sum(row["elapsed_s"] for row in second_rows) / len(second_rows)
    fired = sum(1 for row in second_rows if row["replaces"])
    lines.append("")
    lines.append(
        "greet=%d/%d answer_old=%d/%d avg_sentences=%.2f gate_fired=%d/%d avg_wait=%.2fs"
        % (greets, len(rows), old, len(rows), sent, fired, len(rows), wait)
    )

    out_dir = ROOT.parent / "docs" / "实践情况" / "data" / "sweep"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "gate_uptake.json").write_text(
        json.dumps(
            {
                "repeat": repeat,
                "greet_rate": round(greets / len(rows), 3),
                "answer_old_rate": round(old / len(rows), 3),
                "avg_dialogue_sentences": round(sent, 2),
                "gate_fired": fired,
                "avg_wait_s": round(wait, 2),
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (out_dir / "gate_uptake.txt").write_text("\n".join(lines), encoding="utf-8")
    print("written " + str(out_dir / "gate_uptake.json"))
    print(
        "greet=%d/%d answer_old=%d/%d avg_sentences=%.2f gate_fired=%d/%d avg_wait=%.2fs"
        % (greets, len(rows), old, len(rows), sent, fired, len(rows), wait)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
