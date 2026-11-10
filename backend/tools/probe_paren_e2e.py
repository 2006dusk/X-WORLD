r"""括号即剧情：真实链路复验探针。

走完整生产链路（后端 HTTP 接口、装配、闸门、落盘），验证三件事：
  1. 用户圆括号指定的剧情写进了动作块（不是只在对白里嘴上说）。
  2. 回合里有实际发生的事，不只是角色站着说话。
  3. 台词不长篇说教，也不换个措辞把同一个威胁再说一遍。

做法：把角色A的角色卡与世界状态复制成临时角色，建临时会话，
再把真实会话的前六轮复制进去当上下文，逐轮发括号输入。
只动自己造的东西：cleanup 子命令删掉临时会话与临时角色卡。

跑法：
  F:/python/python.exe backend/tools/probe_paren_e2e.py seed
  F:/python/python.exe backend/tools/probe_paren_e2e.py run --card <id> --session <id>
  F:/python/python.exe backend/tools/probe_paren_e2e.py cleanup --card <id> --session <id>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import store  # noqa: E402

WEB = os.environ.get("XWORLD_WEB", "http://127.0.0.1:8760")
REAL_CARD = "c_card_01"
REAL_SESSION = "s_c_card_01"
LEAF = "m_362d953a86a6"
WORLD = "w_a6154bb9b4b8"
REGION = "e_eaff8133bd29"
PRESET = "p_builtin_rp"
TEMP_NAME = "TMP_PROBE_PAREN"

TURNS = [
    "（顺着她的节奏慢慢进入吟叫）",
    "（看着她的假阳具一点一点进来）",
    "（我不小心腿软了，她便真的让屋外的人进来替她调教）",
    "（她的两个贴身侍女把我牵着，开始了调教）",
]

PREACH = ("懂了吗", "记住了", "记好了", "听好了", "从今往后", "你给我记住",
          "记住这", "莫要忘", "别忘了", "记在心里")


def seed() -> dict[str, str]:
    """造临时角色卡与临时会话，并复制真实会话的前六轮当上下文。"""
    source = store.get_card(REAL_CARD)
    payload = dict(source) if isinstance(source, dict) else {}
    data = payload.get("data")
    payload["data"] = dict(data) if isinstance(data, dict) else {}
    payload["name"] = TEMP_NAME
    payload.pop("id", None)
    card = store.save_card(payload)
    card_id = str(card["id"])

    state = store.get_state(REAL_CARD)
    if isinstance(state, dict) and state:
        store.save_state(dict(state), card_id)

    session_id = "s_tmp_paren_" + card_id.replace("-", "")[-8:]
    store.ensure_session(session_id, card_id, PRESET)
    parent = ""
    for row in store.session_path(REAL_SESSION, REAL_CARD, LEAF):
        item = {
            "role": str(row.get("role", "user")),
            "content": str(row.get("content", "")),
        }
        blocks = row.get("blocks")
        if isinstance(blocks, dict):
            item["blocks"] = blocks
        created = store.branch_message(session_id, card_id, parent, item)
        parent = str(created.get("id") or "")
    return {"card_id": card_id, "session_id": session_id}


def cleanup(card_id: str, session_id: str) -> dict[str, bool]:
    """删掉临时会话与临时角色卡。"""
    session_gone = False
    for scope in (card_id, store.GLOBAL_SCOPE):
        path = store.sessions_dir(scope) / (session_id + ".jsonl")
        if path.exists():
            path.unlink()
            session_gone = True
    card_gone = store.delete_card(card_id)
    return {"session": session_gone, "card": card_gone}


def ask(card_id: str, session_id: str, user_input: str) -> dict:
    """走真实 HTTP 接口生成一轮，拿回结构化块。"""
    body = {
        "model": "xw-local-default",
        "messages": [{"role": "user", "content": user_input}],
        "session": session_id,
        "stream": False,
        "xw": {
            "character_id": card_id,
            "preset_id": PRESET,
            "world_book_ids": [WORLD],
            "region_id": REGION,
            "memory_mode": "hybrid",
            "max_context_tokens": 16384,
        },
    }
    request = urllib.request.Request(
        WEB + "/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        data = json.load(response)
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    blocks = message.get("blocks") or {}
    return {
        "action": str(blocks.get("action") or ""),
        "dialogue": str(blocks.get("dialogue") or ""),
    }


def run(card_id: str, session_id: str) -> int:
    """逐轮发括号输入，把动作块与对白打出来。"""
    for index, user_input in enumerate(TURNS):
        result = ask(card_id, session_id, user_input)
        action = result["action"]
        dialogue = result["dialogue"]
        print(json.dumps({
            "turn": index + 1,
            "user": user_input,
            "action_chars": len(action),
            "dialogue_chars": len(dialogue),
            "preach": any(word in dialogue for word in PREACH),
            "action": action,
            "dialogue": dialogue,
        }, ensure_ascii=True))
    return 0


def main() -> int:
    """入口。"""
    parser = argparse.ArgumentParser(description="括号即剧情真实链路复验")
    parser.add_argument("action", choices=["seed", "run", "cleanup"])
    parser.add_argument("--card", default="")
    parser.add_argument("--session", default="")
    args = parser.parse_args()
    if args.action == "seed":
        print(json.dumps(seed(), ensure_ascii=True))
        return 0
    if args.action == "run":
        return run(args.card, args.session)
    print(json.dumps(cleanup(args.card, args.session), ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
