r"""括号即剧情：回合写法对照探针 v2。

问题：用户圆括号写的指定剧情模型不照做；回合只有嘴没有事；角色长篇说教、
换个措辞把同一个威胁再说一遍。

口径：绕过后端闸门，直连推理服务。上下文用真实会话的装配口径
（assemble 装出 system 与回合要求，历史按生产口径整形），只有提示文本按
变体替换，其余逐字相同。

指标：
  act_hit      括号指定的事真的写进了动作块（按事件组逐组判定）
  talk_only    括号指定的事只出现在对白里 —— 这就是光说不做
  third        动作块里出现了用户之外的人
  act_quote    动作块里混进了台词（分块错位）
  no_dialogue  这一轮对白块是空的
  preach       对白里出现说教标记
  repeat       正文里出现重复家族词
  overlap      相邻两轮对白四字重合率
  act_overlap  相邻两轮动作四字重合率（模型照抄上一轮动作的迹象）
  gate         生产角色归属闸门对这轮的判定

跑法：F:/python/python.exe backend/tools/probe_paren_v2.py
只打印到终端，不落新的持久化数据表，也不写会话流水。

对照的是内置预设 v23 的提示文本（本轮改动前的生产状态）。替换锚点
ACTION_HINT_OLD 与 SYSTEM_ACTION_OLD 逐字取自 v23；内置预设升到 v24 之后
这两个锚点不再命中，重跑本探针量到的是 v24 的现状，不是当初的对照。
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

import httpx  # noqa: E402

from app import store  # noqa: E402
from app.assembly.pipeline import _shape_history, assemble  # noqa: E402
from app.contracts import ChatMessage, ChatRequest, XwOptions  # noqa: E402
from app.inference.structure import split_blocks  # noqa: E402
from app.routers.chat import _role_owner_violation  # noqa: E402

INFER = os.environ.get("XW_INFER", "http://127.0.0.1:8080")
SESSION = "s_c_card_01"
CHARACTER = "c_card_01"
CHAR_NAME = "角色A"
WORLD = "w_a6154bb9b4b8"
REGION = "e_eaff8133bd29"
LEAF = "m_362d953a86a6"
BLOCKS = ["action", "scene", "dialogue", "state"]

SAMPLING = {
    "temperature": 0.7,
    "top_p": 0.9,
    "max_tokens": 512,
    "repeat_penalty": 1.1,
}

# 真实会话新分支的后六轮用户输入，逐字取自落盘数据。
# expect 是事件组：每一组都要在动作块里命中才算括号落实。
TURNS = [
    ("（顺着她的节奏慢慢进入吟叫）",
     [["吟叫"], ["进入", "挺进", "抵入", "没入", "深入"]]),
    ("（看着她的假阳具一点一点进来）",
     [["假阳具"], ["进来", "进入", "没入", "推进"]]),
    ("好大……好深", []),
    ("（我不小心腿软了，她便真的让屋外的人进来替她调教）",
     [["宫女", "侍女", "她们", "两人", "两名"], ["进来", "入内", "调教", "动手"]]),
    ("你们，别过来", []),
    ("（她的两个贴身侍女把我牵着，开始了调教）",
     [["侍女", "宫女", "她们", "两个"], ["牵", "拉", "拽", "调教", "上手"]]),
]

PREACH = ("懂了吗", "记住了", "记好了", "听好了", "听明白了", "从今往后",
          "以后这宫里", "你给我记住", "记在心里", "懂不懂")
REPEAT_FAMILY = ("废人", "规矩", "听我的", "我说了算", "由我", "顺从",
                 "不听话", "不乖", "寒碜", "不成体统")
THIRD_PARTY = ("宫女", "侍女", "她们", "两人", "两名", "丫鬟", "宫人")

HISTORY_DIALOGUE_CHARS = 160

ACTION_HINT_OLD = (
    "写角色此刻的一个动作或反应，一句话，最多 25 字。"
    "不要写光线、雨声、窗外、夜色这类环境词。"
    "用第三人称写角色本人，不要出现「你」或「我」；"
    "不要写对方的动作、心理或台词，也不要在这里写任何引号里的台词；"
    "承接用户这一轮做的事，写角色对它的反应。"
)

ACTION_HINT_NEW = (
    "写这一格实际发生的事，一句话，最多 35 字。"
    "用户指定的剧情已经发生，把它带来的场面写出来："
    "谁做了什么、在场的人对用户做了什么、道具有没有被用上。"
    "用第三人称写在场的人，提到用户时写「你」或「对方」；"
    "不要写光线、雨声、窗外、夜色这类环境词；"
    "不要在这里写任何引号里的台词。"
)

SYSTEM_ACTION_OLD = "动作只写角色身上发生的事，一句话，最多 25 字。"

SYSTEM_ACTION_NEW = (
    "动作写这一格实际发生的事，一句话，最多 35 字："
    "角色本人的动作或反应，以及用户指定的剧情带来的场面变化。"
)

PAREN_RULE = (
    "用户消息里圆括号内的内容是用户指定的剧情，已经发生："
    "照着它往下写，把这一格该发生的事写出来，别绕开、别改成别的意思。"
)

DIALOGUE_TAIL = "台词是对用户说的，像在跟对方搭话，不要自言自语，也不要只写旁观式的感慨"

DIALOGUE_EXTRA = (
    "不要重复上一轮已经说过的意思与威胁，换一个新的进展；"
    "台词贴着当前场面，不要长篇说教或总结道理。"
)

VARIANTS: dict[str, dict[str, Any]] = {
    "v0_baseline": {},
    "vA_action": {
        "replace_action_hint": (ACTION_HINT_OLD, ACTION_HINT_NEW),
        "replace_system_action": (SYSTEM_ACTION_OLD, SYSTEM_ACTION_NEW),
    },
    "vB_paren": {"append_user": PAREN_RULE},
    "vC_dialogue": {"append_dialogue_hint": DIALOGUE_EXTRA},
    "vD_all": {
        "append_user": PAREN_RULE,
        "replace_action_hint": (ACTION_HINT_OLD, ACTION_HINT_NEW),
        "replace_system_action": (SYSTEM_ACTION_OLD, SYSTEM_ACTION_NEW),
        "append_dialogue_hint": DIALOGUE_EXTRA,
    },
    "vE_all_history": {
        "append_user": PAREN_RULE,
        "replace_action_hint": (ACTION_HINT_OLD, ACTION_HINT_NEW),
        "replace_system_action": (SYSTEM_ACTION_OLD, SYSTEM_ACTION_NEW),
        "append_dialogue_hint": DIALOGUE_EXTRA,
        "keep_action_history": True,
    },
}


def has_paren(text: str) -> bool:
    """用户这一轮有没有用圆括号指定剧情。"""
    return ("（" in text) or ("(" in text)


def build_base() -> tuple[str, list[dict[str, Any]], str]:
    """真实上下文：系统段、原始历史行与回合要求。

    装配与历史整形都走后端自己的口径，探针只换提示文本。
    """
    request = ChatRequest(
        model="xw-local-default",
        messages=[ChatMessage(role="user", content=TURNS[0][0])],
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
    user_input = TURNS[0][0]
    directive = context.messages[-1].content[len(user_input):].strip()
    rows = [
        {
            "role": str(row.get("role", "user")),
            "content": str(row.get("content", "")),
            "blocks": row.get("blocks") if isinstance(row.get("blocks"), dict) else None,
        }
        for row in store.session_path(SESSION, CHARACTER, LEAF)
    ]
    return system, rows, directive


def shape_with_action(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """生产整形的变体：角色回复除台词外还留一句动作结果。

    生产口径是角色回复只留台词，动作块不进历史，场面连续性因此断掉。
    """
    rows: list[dict[str, Any]] = []
    for item in items[-8:]:
        row = dict(item)
        if str(row.get("role", "")) == "assistant":
            blocks = row.get("blocks") or {}
            dialogue = str(blocks.get("dialogue") or "").strip()
            action = str(blocks.get("action") or "").strip()
            if len(dialogue) > HISTORY_DIALOGUE_CHARS:
                dialogue = dialogue[-HISTORY_DIALOGUE_CHARS:]
            if action and dialogue:
                row["content"] = action + chr(10) + dialogue
            else:
                row["content"] = dialogue or action
            row.pop("blocks", None)
        rows.append(row)
    while rows and str(rows[0].get("role", "")) == "assistant":
        rows.pop(0)
    return rows


def apply_variant(
    system: str,
    directive: str,
    variant: dict[str, Any],
    user_input: str,
) -> tuple[str, str]:
    """把变体应用到系统段与回合要求上。"""
    replace_action = variant.get("replace_action_hint")
    if replace_action:
        directive = directive.replace(replace_action[0], replace_action[1])
    replace_system = variant.get("replace_system_action")
    if replace_system:
        system = system.replace(replace_system[0], replace_system[1])
    append_dialogue = variant.get("append_dialogue_hint")
    if append_dialogue:
        directive = directive.replace(DIALOGUE_TAIL, DIALOGUE_TAIL + append_dialogue)
    append_user = variant.get("append_user")
    if append_user and has_paren(user_input):
        directive = directive + chr(10) + chr(10) + append_user
    return system, directive


def call_model(messages: list[dict[str, str]]) -> str:
    """直连推理服务要一段回复。"""
    payload = dict(SAMPLING)
    payload["messages"] = messages
    payload["stream"] = False
    with httpx.Client(timeout=180.0) as client:
        resp = client.post(INFER + "/v1/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
    return str(data["choices"][0]["message"].get("content") or "")


def grams(text: str, size: int = 4) -> set[str]:
    """四字一组，用于量相邻两轮的重合。"""
    clean = "".join(ch for ch in text if ch.isalnum())
    return {clean[i:i + size] for i in range(max(0, len(clean) - size + 1))}


def overlap_ratio(left: set[str], right: set[str]) -> float:
    """两个集合的重合率。"""
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def run_variant(name: str, variant: dict[str, Any]) -> dict[str, Any]:
    """跑一个变体，量各项指标并把逐轮结果打出来。"""
    base_system, base_rows, base_directive = build_base()
    history = list(base_rows)
    rows_out: list[dict[str, Any]] = []
    dialogues: list[str] = []
    actions: list[str] = []
    for index, (user_input, expect) in enumerate(TURNS):
        system, directive = apply_variant(base_system, base_directive, variant, user_input)
        if variant.get("keep_action_history"):
            shaped = shape_with_action([dict(item) for item in history])
        else:
            shaped = _shape_history([dict(item) for item in history])
        messages = [{"role": "system", "content": system}]
        for item in shaped:
            messages.append({"role": item["role"], "content": item["content"]})
        messages.append({"role": "user", "content": user_input + chr(10) + chr(10) + directive})
        started = time.time()
        raw = call_model(messages)
        elapsed = time.time() - started
        blocks = split_blocks(raw, BLOCKS)
        action = str(blocks.get("action") or "").strip()
        scene = str(blocks.get("scene") or "").strip()
        dialogue = str(blocks.get("dialogue") or "").strip()
        body = action + chr(10) + scene + chr(10) + dialogue
        groups_action = [any(word in action for word in group) for group in expect]
        groups_body = [any(word in body for word in group) for group in expect]
        act_hit = bool(expect) and all(groups_action)
        talk_only = bool(expect) and all(groups_body) and not act_hit
        ov = 0.0
        if dialogues:
            ov = overlap_ratio(grams(dialogues[-1]), grams(dialogue))
        act_ov = 0.0
        if actions:
            act_ov = overlap_ratio(grams(actions[-1]), grams(action))
        dialogues.append(dialogue)
        actions.append(action)
        row = {
            "turn": index + 1,
            "act_hit": act_hit,
            "talk_only": talk_only,
            "groups_action": groups_action,
            "action_chars": len(action),
            "scene_chars": len(scene),
            "dialogue_chars": len(dialogue),
            "preach": any(word in dialogue for word in PREACH),
            "repeat": sum(1 for word in REPEAT_FAMILY if word in body),
            "third": any(word in action for word in THIRD_PARTY),
            "act_quote": ("“" in action) or ("「" in action) or ('"' in action),
            "no_dialogue": not dialogue,
            "overlap": round(ov, 3),
            "act_overlap": round(act_ov, 3),
            "gate": _role_owner_violation(blocks, BLOCKS, CHAR_NAME),
            "seconds": round(elapsed, 1),
            "action_text": action[:110],
            "dialogue_text": dialogue[:70],
        }
        rows_out.append(row)
        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": raw, "blocks": blocks})
    judged = [row for row, turn in zip(rows_out, TURNS) if turn[1]]
    summary = {
        "variant": name,
        "act_hit": round(sum(1 for r in judged if r["act_hit"]) / len(judged), 3) if judged else 0,
        "talk_only": round(sum(1 for r in judged if r["talk_only"]) / len(judged), 3) if judged else 0,
        "action_chars": round(sum(r["action_chars"] for r in rows_out) / len(rows_out), 1),
        "dialogue_chars": round(sum(r["dialogue_chars"] for r in rows_out) / len(rows_out), 1),
        "preach_rate": round(sum(1 for r in rows_out if r["preach"]) / len(rows_out), 3),
        "repeat_avg": round(sum(r["repeat"] for r in rows_out) / len(rows_out), 2),
        "third_rate": round(sum(1 for r in rows_out if r["third"]) / len(rows_out), 3),
        "act_quote_rate": round(sum(1 for r in rows_out if r["act_quote"]) / len(rows_out), 3),
        "no_dialogue_rate": round(sum(1 for r in rows_out if r["no_dialogue"]) / len(rows_out), 3),
        "gate_hits": sum(1 for r in rows_out if r["gate"]),
        "overlap_avg": round(sum(r["overlap"] for r in rows_out[1:]) / max(1, len(rows_out) - 1), 3),
        "act_overlap_avg": round(sum(r["act_overlap"] for r in rows_out[1:]) / max(1, len(rows_out) - 1), 3),
    }
    return {"summary": summary, "rows": rows_out}


def main() -> int:
    """入口。"""
    results = []
    for name, variant in VARIANTS.items():
        result = run_variant(name, variant)
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
