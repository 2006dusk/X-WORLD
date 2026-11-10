r"""真机探针：自动回合（用户不说话，AI 自己往下走）。

跑法：python backend/tools/probe_auto_turn.py

链路：
1. 建一个临时角色卡并开一段独立对话，先说一句普通的话。
2. 走自动回合的 continue -> 落盘里没有空的用户消息，回复带动作与对白。
3. 走自动回合的 describe -> 落盘里没有对白块，回复只有环境描写。

临时角色卡、探针会话与探针角色的状态目录在结束时删掉，不留在数据目录里。
结果只在终端回显，不落新的持久化数据表。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from app import store  # noqa: E402

BASE = "http://127.0.0.1:8760"
PRESET_ID = "p_builtin_rp"
CARD_NAME = "自动回合探针角色"
CARD_DESC = (
    "沈砚，二十八岁，市图书馆的夜班管理员。闭馆后独自守在大厅的值班桌前，"
    "桌上有一盏台灯与一台旧收音机，窗外是雨夜的停车场"
)


def req(
    method: str,
    path: str,
    payload: Any = None,
    timeout: int = 240,
) -> tuple[int, Any]:
    """发一个 JSON 请求，返回状态码与解析后的正文。"""
    url = BASE + path
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        status = exc.code
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body


def chat(
    session_id: str,
    card_id: str,
    prompt: str,
    auto_turn: str = "",
) -> tuple[int, Any]:
    """跑一轮会话生成。auto_turn 非空时是自动回合。"""
    body = {
        "model": "xw-local-default",
        "messages": [{"role": "user", "content": prompt}],
        "session": session_id,
        "stream": False,
        "xw": {
            "character_id": card_id,
            "preset_id": PRESET_ID,
            "world_book_ids": [],
            "memory_mode": "hybrid",
            "max_context_tokens": 8192,
            "auto_turn": auto_turn,
        },
    }
    return req("POST", "/v1/chat/completions", body)


def filled(blocks: dict | None) -> list[str]:
    """块字典里写了内容的块名。

    块字典的键恒等于声明的结构：没写出来的块值是空串。
    判断这一轮有没有场景块要看有没有内容，不是看键在不在。
    """
    return sorted(name for name, value in (blocks or {}).items() if str(value).strip())


def rows_of(session_id: str, card_id: str) -> list[dict]:
    """读会话流水。"""
    scope = store.session_scope(session_id) or card_id
    return list(store.session_path(session_id, scope))


def cleanup(session_id: str, card_id: str) -> None:
    """删掉探针自己建的会话、角色卡与状态目录。"""
    req("DELETE", "/v1/sessions/" + session_id + "?character_id=" + card_id)
    req("DELETE", "/v1/cards/" + card_id)
    directory = store.states_dir(card_id)
    if directory.exists():
        for item in directory.iterdir():
            item.unlink()
        directory.rmdir()


def main() -> int:
    """按链路跑一遍，逐项给出断言。"""
    report: dict[str, Any] = {}

    status, card = req(
        "POST",
        "/v1/cards/",
        {
            "name": CARD_NAME,
            "spec_version": "v2",
            "data": {
                "name": "沈砚",
                "description": CARD_DESC,
                "personality": "安静、话少",
                "tags": ["图书馆", "夜班"],
            },
        },
    )
    card_id = str(card.get("id") or "")
    report["card_created"] = status == 200 and bool(card_id)

    status, session = req(
        "POST",
        "/v1/sessions/",
        {"character_id": card_id, "world_book_ids": [], "region_id": ""},
    )
    session_id = str(session.get("id") or "")
    report["session_created"] = status == 200 and bool(session_id)

    try:
        status, _data = chat(session_id, card_id, "外面还在下雨。")
        report["normal_turn_status"] = status

        status, data = chat(session_id, card_id, "", "continue")
        report["continue_status"] = status
        message = (data.get("choices") or [{}])[0].get("message") or {}
        report["continue_blocks"] = filled(message.get("blocks"))
        report["continue_has_scene"] = "scene" in report["continue_blocks"]
        report["continue_has_action"] = "action" in report["continue_blocks"]
        report["continue_chars"] = len(str(message.get("content") or ""))
        report["continue_text"] = str(message.get("content") or "")[:400]

        rows = rows_of(session_id, card_id)
        report["roles_after_continue"] = [str(row.get("role")) for row in rows]
        report["empty_user_rows"] = sum(
            1
            for row in rows
            if row.get("role") == "user" and not str(row.get("content") or "").strip()
        )

        status, data = chat(session_id, card_id, "", "describe")
        report["describe_status"] = status
        message = (data.get("choices") or [{}])[0].get("message") or {}
        blocks = message.get("blocks") or {}
        report["describe_blocks"] = filled(blocks)
        report["describe_has_dialogue"] = "dialogue" in report["describe_blocks"]
        report["describe_chars"] = len(str(message.get("content") or ""))

        rows = rows_of(session_id, card_id)
        report["roles_after_describe"] = [str(row.get("role")) for row in rows]
        report["empty_user_rows_final"] = sum(
            1
            for row in rows
            if row.get("role") == "user" and not str(row.get("content") or "").strip()
        )
        last = rows[-1] if rows else {}
        report["last_row_blocks"] = filled(last.get("blocks"))
        report["last_row_chars"] = len(str(last.get("content") or ""))
        report["last_row_text"] = str(last.get("content") or "")[:400]

        # 普通回合里的块分布：动作块每轮都有，环境描写块不出现。
        normal_blocks: list[list[str]] = []
        normal_scene_chars: list[int] = []
        normal_action_chars: list[int] = []
        normal_dialogue_chars: list[int] = []
        for prompt in (
            "外面还在下雨。",
            "你手边那本书是什么？",
            "再等等，我还没说完。",
            "你还记得上次我来的时候吗？",
            "灯是不是该换了？",
            "门响了。",
            "我该走了。",
            "外面天亮了吗？",
        ):
            status, data = chat(session_id, card_id, prompt)
            message = (data.get("choices") or [{}])[0].get("message") or {}
            blocks = message.get("blocks") or {}
            normal_blocks.append(filled(blocks))
            normal_scene_chars.append(len(str(blocks.get("scene") or "")))
            normal_action_chars.append(len(str(blocks.get("action") or "")))
            normal_dialogue_chars.append(len(str(blocks.get("dialogue") or "")))
        report["normal_blocks"] = normal_blocks
        report["normal_scene_chars"] = normal_scene_chars
        report["normal_action_chars"] = normal_action_chars
        report["normal_dialogue_chars"] = normal_dialogue_chars
        report["normal_scene_ratio"] = round(
            sum(1 for keys in normal_blocks if "scene" in keys) / max(1, len(normal_blocks)),
            2,
        )
        report["normal_action_ratio"] = round(
            sum(1 for keys in normal_blocks if "action" in keys) / max(1, len(normal_blocks)),
            2,
        )
    finally:
        cleanup(session_id, card_id)
        report["cleaned"] = not store.get_card(card_id)

    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
