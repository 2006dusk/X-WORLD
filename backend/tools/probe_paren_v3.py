r"""括号即剧情：回合写法对照探针 v3。

在 v2 结论上加种子重复，压住 n=6 的噪声，并单独测说教与换措辞重复。

v2 已测到的：
  - 动作块口径从「角色此刻的动作或反应」改成「这一格实际发生的事」，
    括号指定的事才会写进动作块（vA、vD、vE 的原文可证）。
  - 括号规则单独加没有用（vB 落实率 0），必须与动作块口径一起上。
  - 动作块进历史后场面连续性最好（vE 四轮全部写出场面）。
  - 说教与换措辞重复仍没解决：vD/vE 的对白里反复出现「记住…」「从今往后…」。

v3 测：
  R0 基线（生产现状）
  R1 场景版：动作块口径 + 结构要求 + 括号规则 + 动作块进历史
  R2 场景版 + 换一套对白要求（只说此刻的话，不解释心思，不重复威胁）
  R3 场景版 + 对白字数下限 100 改 60 + 历史里台词窗口 160 改 400

每个变体跑两个种子，指标取两轮平均。

跑法：F:/python/python.exe backend/tools/probe_paren_v3.py
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
from app.assembly.pipeline import _last_quoted, _shape_history, assemble  # noqa: E402
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

TURNS = [
    ("（顺着她的节奏慢慢进入吟叫）",
     [["进入", "挺进", "抵入", "没入", "深入", "推入", "塞入", "按倒", "碾"]]),
    ("（看着她的假阳具一点一点进来）",
     [["假阳具", "器具", "那物"], ["进来", "进入", "没入", "推进", "探入", "推入", "没"]]),
    ("好大……好深", []),
    ("（我不小心腿软了，她便真的让屋外的人进来替她调教）",
     [["宫女", "侍女", "她们", "两人", "两名"],
      ["涌入", "鱼贯而入", "进来", "入内", "上前", "按住", "按在", "按跪", "跪",
       "动手", "解下", "托起", "拉住", "拽", "扑"]]),
    ("你们，别过来", []),
    ("（她的两个贴身侍女把我牵着，开始了调教）",
     [["侍女", "宫女", "她们", "两个"],
      ["牵", "拉", "拽", "调教", "上手", "按住", "按紧", "扯"]]),
]

PREACH = ("懂了吗", "记住了", "记好了", "听好了", "听明白了", "从今往后",
          "以后这宫里", "你给我记住", "记在心里", "懂不懂", "记住这",
          "莫要忘", "别忘了")
REPEAT_FAMILY = ("废人", "规矩", "听我的", "我说了算", "由我", "顺从",
                 "不听话", "不乖", "寒碜", "不成体统", "分担", "臣妾若",
                 "明日早朝", "日后如何")
THIRD_PARTY = ("宫女", "侍女", "她们", "两人", "两名", "丫鬟", "宫人")

HISTORY_DIALOGUE_CHARS = 160
HISTORY_KEEP = 8
NO_SPEECH_MARKER = "（这一轮没有台词）"

ACTION_HINT_OLD = (
    "写角色此刻的一个动作或反应，一句话，最多 25 字。"
    "不要写光线、雨声、窗外、夜色这类环境词。"
    "用第三人称写角色本人，不要出现「你」或「我」；"
    "不要写对方的动作、心理或台词，也不要在这里写任何引号里的台词；"
    "承接用户这一轮做的事，写角色对它的反应。"
)

ACTION_HINT_NEW = (
    "写这一格实际发生的事，一句话，最多 35 字："
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

STRUCTURE_REQ_OLD = (
    "【动作】块与【对话】块都要写："
    "动作块写角色此刻的动作或反应，对话块写他说的话。"
)

STRUCTURE_REQ_NEW = (
    "【动作】块与【对话】块都要写："
    "动作块写这一格实际发生的事，对话块写角色说出口的话。"
    "台词只写在【对话】块里。"
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

DIALOGUE_QUIET = (
    "台词只说此刻角色会对用户说出口的话："
    "不解释角色自己的心思与立场，不总结道理，"
    "不重复上一轮说过的威胁或叮嘱，说一个新的进展。"
)

FLOOR_OLD = "至少写两句，合计不少于 100 字"
FLOOR_NEW = "至少写两句，合计不少于 60 字"

SCENE_BASE = {
    "replace_action_hint": (ACTION_HINT_OLD, ACTION_HINT_NEW),
    "replace_system_action": (SYSTEM_ACTION_OLD, SYSTEM_ACTION_NEW),
    "replace_structure_req": (STRUCTURE_REQ_OLD, STRUCTURE_REQ_NEW),
    "append_user": PAREN_RULE,
    "keep_action_history": True,
}

VARIANTS: dict[str, dict[str, Any]] = {
    "R0_baseline": {},
    "R1_scene": dict(SCENE_BASE, append_dialogue_hint=DIALOGUE_EXTRA),
    "R2_quiet": dict(SCENE_BASE, append_dialogue_hint=DIALOGUE_QUIET),
    "R3_floor": dict(
        SCENE_BASE,
        append_dialogue_hint=DIALOGUE_QUIET,
        replace_floor=(FLOOR_OLD, FLOOR_NEW),
        dialogue_window=400,
    ),
}

SEEDS = (101, 202)


def has_paren(text: str) -> bool:
    """用户这一轮有没有用圆括号指定剧情。"""
    return ("（" in text) or ("(" in text)


def build_base() -> tuple[str, list[dict[str, Any]], str]:
    """真实上下文：系统段、原始历史行与回合要求。"""
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


def shape_history(
    items: list[dict[str, Any]],
    keep_action: bool = False,
    window: int = HISTORY_DIALOGUE_CHARS,
) -> list[dict[str, Any]]:
    """生产整形的可调版本：动作块是否进历史、台词窗口多长。

    keep_action 为假且窗口是默认值时，结果与生产 _shape_history 一致。
    """
    rows: list[dict[str, Any]] = []
    for item in items[-HISTORY_KEEP:]:
        row = dict(item)
        if str(row.get("role", "")) == "assistant":
            blocks = row.get("blocks") or {}
            dialogue = str(blocks.get("dialogue") or "").strip()
            action = str(blocks.get("action") or "").strip()
            if dialogue:
                if len(dialogue) > window:
                    dialogue = dialogue[-window:]
            else:
                quoted = _last_quoted(str(row.get("content") or ""))
                dialogue = quoted if quoted else NO_SPEECH_MARKER
            if keep_action and action:
                row["content"] = action + chr(10) + dialogue
            else:
                row["content"] = dialogue
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
    replace_req = variant.get("replace_structure_req")
    if replace_req:
        directive = directive.replace(replace_req[0], replace_req[1])
    replace_floor = variant.get("replace_floor")
    if replace_floor:
        directive = directive.replace(replace_floor[0], replace_floor[1])
    append_dialogue = variant.get("append_dialogue_hint")
    if append_dialogue:
        directive = directive.replace(DIALOGUE_TAIL, DIALOGUE_TAIL + append_dialogue)
    append_user = variant.get("append_user")
    if append_user and has_paren(user_input):
        directive = directive + chr(10) + chr(10) + append_user
    return system, directive


def call_model(messages: list[dict[str, str]], seed: int) -> str:
    """直连推理服务要一段回复。"""
    payload = dict(SAMPLING)
    payload["seed"] = seed
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


def run_variant(name: str, variant: dict[str, Any], seed: int) -> dict[str, Any]:
    """跑一个变体的一个种子。"""
    base_system, base_rows, base_directive = build_base()
    history = list(base_rows)
    rows_out: list[dict[str, Any]] = []
    dialogues: list[str] = []
    actions: list[str] = []
    keep_action = bool(variant.get("keep_action_history"))
    window = int(variant.get("dialogue_window") or HISTORY_DIALOGUE_CHARS)
    for index, (user_input, expect) in enumerate(TURNS):
        system, directive = apply_variant(base_system, base_directive, variant, user_input)
        shaped = shape_history(
            [dict(item) for item in history], keep_action=keep_action, window=window
        )
        messages = [{"role": "system", "content": system}]
        for item in shaped:
            messages.append({"role": item["role"], "content": item["content"]})
        messages.append({"role": "user", "content": user_input + chr(10) + chr(10) + directive})
        started = time.time()
        raw = call_model(messages, seed)
        elapsed = time.time() - started
        blocks = split_blocks(raw, BLOCKS)
        action = str(blocks.get("action") or "").strip()
        dialogue = str(blocks.get("dialogue") or "").strip()
        body = action + chr(10) + dialogue
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
            "seed": seed,
            "turn": index + 1,
            "act_hit": act_hit,
            "talk_only": talk_only,
            "action_chars": len(action),
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
            "action_text": action[:120],
            "dialogue_text": dialogue[:120],
        }
        rows_out.append(row)
        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": raw, "blocks": blocks})
    judged = [row for row, turn in zip(rows_out, TURNS) if turn[1]]
    summary = {
        "variant": name,
        "seed": seed,
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
