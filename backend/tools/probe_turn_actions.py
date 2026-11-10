r"""轮次动作的生产链路探针。

验证前端「重新生成」与「修改」两个按钮对应的后端路径：

- turn_action=regenerate：丢掉会话流水最后一条角色回复，用同样的用户输入
  再答一次。流水条数不变，最后一条角色回复内容变了。
- turn_action=revise：丢掉上一轮输入与回复，用本轮输入替换后重跑。
  流水条数不变，最后一条用户输入换成新文本。

用法：
    python backend\tools\probe_turn_actions.py
输出：
    docs\实践情况\data\sweep\turn_actions.json
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from app import store  # noqa: E402

API = os.environ.get("XW_BACKEND", "http://127.0.0.1:8760")
SESSION = os.environ.get("XW_SESSION", "s_turn_action")
CHARACTER = "c_demo"
PRESET = "p_builtin_rp"


def request_body(prompt: str, action: str) -> dict:
    """构造一轮带轮次动作的请求。"""
    return {
        "model": "xw-local-default",
        "messages": [{"role": "user", "content": prompt}],
        "session": SESSION,
        "stream": False,
        "xw": {
            "character_id": CHARACTER,
            "preset_id": PRESET,
            "world_book_ids": ["w_demo"],
            "memory_mode": "hybrid",
            "max_context_tokens": 8192,
        },
        "turn_action": action,
    }


def ask(client: httpx.Client, prompt: str, action: str) -> dict:
    """跑一轮非流式请求，返回角色回复正文。"""
    response = client.post(
        API + "/v1/chat/completions", json=request_body(prompt, action), timeout=300.0
    )
    response.raise_for_status()
    data = response.json()
    return {
        "content": data["choices"][0]["message"].get("content") or "",
        "blocks": data["choices"][0]["message"].get("blocks") or {},
    }


def rows() -> list[dict]:
    """读回会话流水。"""
    return store.get_messages(SESSION, CHARACTER)


def main() -> int:
    """跑一轮常规对话，再分别验证重新生成与改写。"""
    steps: list[dict] = []
    with httpx.Client() as client:
        first = ask(client, "外面雨还没停。", "append")
        second = ask(client, "前面有家地址V，我们进去吧。", "append")
        after_two = rows()
        steps.append(
            {
                "step": "append_x2",
                "row_count": len(after_two),
                "last_assistant": after_two[-1].get("content"),
            }
        )

        again = ask(client, "前面有家地址V，我们进去吧。", "regenerate")
        after_regen = rows()
        steps.append(
            {
                "step": "regenerate",
                "row_count": len(after_regen),
                "same_row_count": len(after_regen) == len(after_two),
                "user_rows_kept": [r.get("content") for r in after_regen if r.get("role") == "user"]
                == [r.get("content") for r in after_two if r.get("role") == "user"],
                "reply_changed": after_regen[-1].get("content") != after_two[-1].get("content"),
                "last_assistant": after_regen[-1].get("content"),
            }
        )

        revised = ask(client, "我们换个地方吧。", "revise")
        after_revise = rows()
        users = [r.get("content") for r in after_revise if r.get("role") == "user"]
        steps.append(
            {
                "step": "revise",
                "row_count": len(after_revise),
                "same_row_count": len(after_revise) == len(after_regen),
                "last_user_input": users[-1] if users else "",
                "input_replaced": bool(users) and users[-1] == "我们换个地方吧。",
                "last_assistant": after_revise[-1].get("content"),
            }
        )

    summary = {
        "append_rows": steps[0]["row_count"],
        "regenerate_keeps_row_count": bool(steps[1].get("same_row_count")),
        "regenerate_keeps_user_rows": bool(steps[1].get("user_rows_kept")),
        "regenerate_changes_reply": bool(steps[1].get("reply_changed")),
        "revise_keeps_row_count": bool(steps[2].get("same_row_count")),
        "revise_replaces_input": bool(steps[2].get("input_replaced")),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for step in steps:
        print()
        print(step["step"], "->", json.dumps(step, ensure_ascii=False)[:400])

    out = ROOT.parent / "docs" / "实践情况" / "data" / "sweep" / "turn_actions.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"summary": summary, "steps": steps}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print()
    print("written " + str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
