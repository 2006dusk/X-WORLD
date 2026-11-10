r"""括号即剧情：说教与重复的对照探针 v4。

前一轮（probe_paren_v3.py）已定：动作块口径改成「这一格实际发生的事」，
括号落实率从 0.25 升到 0.75；台词混进动作块的比例从 42% 降到 8%。
剩下的是说教与换措辞重复，这轮单独测。

v4 的两处改动让口径更接近生产：
  - 解析走生产的 _normalize_blocks（台词归位后才是真实的块）。
  - 每轮按该轮的用户输入重新装配，括号指令只在本轮写了圆括号时出现。

变体（其余逐字相同，两个种子）：
  S0_prod     生产现状
  S1_floor60  对白字数下限 100 字改 60 字
  S2_short    系统提示加一句：台词不超过两三句，不复述上一轮的威胁与叮嘱

跑法：python backend/tools/probe_paren_v4.py
只打印到终端，不落新的持久化数据表，也不写会话流水。
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import httpx  # noqa: E402

from app.assembly.pipeline import _shape_history, assemble  # noqa: E402
from app.contracts import ChatMessage, ChatRequest, XwOptions  # noqa: E402
from app.inference.structure import split_blocks  # noqa: E402
from app.routers.chat import _normalize_blocks, _role_owner_violation  # noqa: E402

import probe_paren_v3 as V3  # noqa: E402

INFER = V3.INFER
SESSION = V3.SESSION
CHARACTER = V3.CHARACTER
CHAR_NAME = V3.CHAR_NAME
WORLD = V3.WORLD
REGION = V3.REGION
LEAF = V3.LEAF
BLOCKS = V3.BLOCKS
SAMPLING = V3.SAMPLING
TURNS = V3.TURNS
PREACH = V3.PREACH
REPEAT_FAMILY = V3.REPEAT_FAMILY
THIRD_PARTY = V3.THIRD_PARTY
FLOOR_OLD = V3.FLOOR_OLD
FLOOR_NEW = V3.FLOOR_NEW

SYSTEM_TAIL = (
    "\n台词是对用户说的：接住用户刚说的那句或刚做的动作再往下写，"
    "像在跟对方搭话；不要自言自语，也不要只写旁观式的感慨。"
)

SYSTEM_SHORT = (
    "\n台词不超过两三句，说完就停："
    "不复述上一轮的威胁与叮嘱，不讲道理，不解释角色自己的心思。"
)

VARIANTS: dict[str, dict[str, Any]] = {
    "S0_prod": {},
    "S1_floor60": {"replace_floor": (FLOOR_OLD, FLOOR_NEW)},
    "S2_short": {"replace_system_tail": (SYSTEM_TAIL, SYSTEM_TAIL + SYSTEM_SHORT)},
}

SEEDS = (303, 404)


def build_for(user_input: str) -> tuple[str, list[dict[str, Any]], str]:
    """按本轮的用户输入装配：括号指令只在本轮写了圆括号时出现。"""
    request = ChatRequest(
        model="xw-local-default",
        messages=[ChatMessage(role="user", content=user_input)],
        session=SESSION,
        stream=False,
        xw=XwOptions(
            character_id=CHARACTER,
            preset_id="p_builtin_rp",
            world_book_ids=[WORLD],
            region_id=REGION,
            memory_mode="hybrid",
            max_context_tokens=16384,
        ),
    )
    context = assemble(request, leaf_id=LEAF)
    system = context.messages[0].content
    directive = context.messages[-1].content[len(user_input):].strip()
    rows = [
        {
            "role": str(row.get("role", "user")),
            "content": str(row.get("content", "")),
            "blocks": row.get("blocks") if isinstance(row.get("blocks"), dict) else None,
        }
        for row in V3.store.session_path(SESSION, CHARACTER, LEAF)
    ]
    return system, rows, directive


def apply(system: str, directive: str, variant: dict[str, Any]) -> tuple[str, str]:
    """按变体改提示。"""
    replace_floor = variant.get("replace_floor")
    if replace_floor:
        directive = directive.replace(replace_floor[0], replace_floor[1])
    replace_tail = variant.get("replace_system_tail")
    if replace_tail:
        system = system.replace(replace_tail[0], replace_tail[1])
    return system, directive


def run_variant(name: str, variant: dict[str, Any], seed: int) -> dict[str, Any]:
    """跑一个变体的一个种子。"""
    history: list[dict[str, Any]] = []
    base_system, base_rows, _ = build_for(TURNS[0][0])
    history = list(base_rows)
    rows_out: list[dict[str, Any]] = []
    dialogues: list[str] = []
    for index, (user_input, expect) in enumerate(TURNS):
        system, _rows_now, directive = build_for(user_input)
        system, directive = apply(system, directive, variant)
        shaped = V3.shape_history([dict(item) for item in history], keep_action=True)
        messages = [{"role": "system", "content": system}]
        for item in shaped:
            messages.append({"role": item["role"], "content": item["content"]})
        messages.append({"role": "user", "content": user_input + chr(10) + chr(10) + directive})
        started = time.time()
        raw = V3.call_model(messages, seed)
        elapsed = time.time() - started
        blocks = _normalize_blocks(split_blocks(raw, BLOCKS), BLOCKS)
        action = str(blocks.get("action") or "").strip()
        dialogue = str(blocks.get("dialogue") or "").strip()
        body = action + chr(10) + dialogue
        groups_action = [any(word in action for word in group) for group in expect]
        act_hit = bool(expect) and all(groups_action)
        ov = 0.0
        if dialogues:
            ov = V3.overlap_ratio(V3.grams(dialogues[-1]), V3.grams(dialogue))
        dialogues.append(dialogue)
        row = {
            "seed": seed,
            "turn": index + 1,
            "act_hit": act_hit,
            "action_chars": len(action),
            "dialogue_chars": len(dialogue),
            "preach": any(word in dialogue for word in PREACH),
            "repeat": sum(1 for word in REPEAT_FAMILY if word in body),
            "third": any(word in action for word in THIRD_PARTY),
            "no_dialogue": not dialogue,
            "overlap": round(ov, 3),
            "gate": _role_owner_violation(blocks, BLOCKS, CHAR_NAME),
            "seconds": round(elapsed, 1),
            "action_text": action[:110],
            "dialogue_text": dialogue[:110],
        }
        rows_out.append(row)
        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": raw, "blocks": blocks})
    judged = [row for row, turn in zip(rows_out, TURNS) if turn[1]]
    summary = {
        "variant": name,
        "seed": seed,
        "act_hit": round(sum(1 for r in judged if r["act_hit"]) / len(judged), 3) if judged else 0,
        "action_chars": round(sum(r["action_chars"] for r in rows_out) / len(rows_out), 1),
        "dialogue_chars": round(sum(r["dialogue_chars"] for r in rows_out) / len(rows_out), 1),
        "preach_rate": round(sum(1 for r in rows_out if r["preach"]) / len(rows_out), 3),
        "repeat_avg": round(sum(r["repeat"] for r in rows_out) / len(rows_out), 2),
        "no_dialogue_rate": round(sum(1 for r in rows_out if r["no_dialogue"]) / len(rows_out), 3),
        "gate_hits": sum(1 for r in rows_out if r["gate"]),
        "overlap_avg": round(sum(r["overlap"] for r in rows_out[1:]) / max(1, len(rows_out) - 1), 3),
    }
    return {"summary": summary, "rows": rows_out}


def main() -> int:
    """入口。"""
    results = []
    for name, variant in VARIANTS.items():
        for seed in SEEDS:
            result = run_variant(name, variant, seed)
            results.append(result)
            print(json.dumps(result["summary"], ensure_ascii=True))
            for row in result["rows"]:
                print(json.dumps(row, ensure_ascii=True))
    print("=== summary ===")
    for result in results:
        print(json.dumps(result["summary"], ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
