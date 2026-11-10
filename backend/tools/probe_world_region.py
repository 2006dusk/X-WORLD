"""真机探针：世界、区域与角色的关系。

跑法：python backend/tools/probe_world_region.py

链路：选世界与区域建对话 -> 预览装配确认区域被注入 -> 两个角色各说一轮 ->
核对消息落在同一段对话、状态各存各的 -> 世界书条目随卡片改动同步。
输出写 UTF-8 文件，终端不回显中文。
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

BASE = "http://127.0.0.1:8760"
OUT = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "docs"
    / "实践情况"
    / "data"
    / "sweep"
    / "world_region_probe.json"
)

CARD_NAME = "角色L（探针临时角色）"
# 主角色也用临时卡。探针不得碰真实角色：早前版本直接用 c_demo 当主角色，
# 收尾时删掉 s_c_demo，把那位角色的整段对话一起删没了。
MAIN_CARD_NAME = "角色K（探针临时角色）"


def req(method: str, path: str, payload: Any = None) -> tuple[int, Any]:
    """发一个 JSON 请求，返回状态码与解析后的正文。"""
    url = BASE + path
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            body = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        status = exc.code
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body


def main() -> int:
    """跑完整条链路并落盘结果。"""
    report: dict[str, Any] = {"steps": []}

    def note(name: str, detail: Any) -> None:
        report["steps"].append({"step": name, "detail": detail})

    status, card = req(
        "POST",
        "/v1/cards/",
        {
            "name": CARD_NAME,
            "spec_version": "v2",
            "data": {
                "name": CARD_NAME,
                "description": "探针用的临时角色，地址N夜班便利店店员",
                "personality": "话少",
                "scenario": "雨夜便利店",
            },
        },
    )
    card_id = str(card.get("id") or "")
    note("create_card", {"status": status, "id": card_id})

    status, main_card = req(
        "POST",
        "/v1/cards/",
        {
            "name": MAIN_CARD_NAME,
            "spec_version": "v2",
            "data": {
                "name": MAIN_CARD_NAME,
                "description": "探针用的临时主角色，地址N地址U",
                "personality": "话少",
                "scenario": "地址N的地址U",
            },
        },
    )
    main_character = str(main_card.get("id") or "")
    note("create_main_card", {"status": status, "id": main_character})

    status, books = req("GET", "/v1/world-books/")
    book_id = ""
    for item in books.get("items", []):
        book_id = str(item.get("id") or "")
        break
    note("world_books", {"status": status, "book_id": book_id})

    status, regions = req("GET", "/v1/world-books/" + book_id + "/regions")
    region_items = regions.get("items", []) if isinstance(regions, dict) else []
    region_id = str(region_items[0].get("id") or "") if region_items else ""
    region_label = str(region_items[0].get("name") or "") if region_items else ""
    note("regions", {"status": status, "items": region_items})

    status, session = req(
        "POST",
        "/v1/sessions/",
        {
            "character_id": main_character,
            "preset_id": "p_builtin_rp",
            "world_book_ids": [book_id],
            "region_id": region_id,
        },
    )
    session_id = str(session.get("id") or "")
    report["session_id"] = session_id
    report["region_id"] = region_id
    report["region_name"] = region_label
    note("create_session", {"status": status, "session": session})

    status, preview = req(
        "GET",
        "/v1/prompt-preview?session="
        + session_id
        + "&character_id="
        + main_character
        + "&preset_id=p_builtin_rp&world_book_ids="
        + book_id
        + "&region_id="
        + region_id
        + "&user_input="
        + urllib.parse.quote("现在几点？"),
    )
    preview_text = json.dumps(preview, ensure_ascii=False)
    report["preview_world_info_entries"] = []
    if isinstance(preview, dict):
        for segment in preview.get("segments", []):
            if segment.get("name") == "world_info":
                report["preview_world_info_entries"] = segment.get("entries") or []
    # 区域条目出现在 world_info 段里就算注入成功：预览正文按 200 字截断，
    # 不能用正文里的文字判断。
    report["region_injected"] = region_id in (report["preview_world_info_entries"] or [])
    report["region_label_in_preview"] = region_label in preview_text
    note("preview", {"region_injected": report["region_injected"]})

    turns = []
    for speaker in (main_character, card_id):
        status, result = req(
            "POST",
            "/v1/chat/completions",
            {
                "model": "xw-local-default",
                "messages": [{"role": "user", "content": "@%s 现在几点？" % speaker}],
                "session": session_id,
                "stream": False,
                "xw": {
                    "character_id": speaker,
                    "preset_id": "p_builtin_rp",
                    "world_book_ids": [book_id],
                    "region_id": region_id,
                    "memory_mode": "hybrid",
                    "max_context_tokens": 16384,
                },
            },
        )
        text = ""
        if isinstance(result, dict):
            choices = result.get("choices") or []
            if choices:
                text = str((choices[0].get("message") or {}).get("content") or "")
        turns.append({"speaker": speaker, "status": status, "reply": text[:240]})
    report["turns"] = turns
    note("chat_turns", turns)

    status, messages = req("GET", "/v1/sessions/" + session_id + "/messages")
    rows = messages.get("items", []) if isinstance(messages, dict) else []
    report["message_count"] = len(rows)
    report["message_roles"] = [str(row.get("role")) for row in rows]

    status, state_main = req("GET", "/v1/state/?character_id=" + main_character)
    status, state_other = req("GET", "/v1/state/?character_id=" + card_id)
    report["state_main"] = state_main
    report["state_other"] = state_other
    report["state_split"] = (
        state_main.get("location") != state_other.get("location")
        if isinstance(state_main, dict) and isinstance(state_other, dict)
        else False
    )

    status, listing = req("GET", "/v1/sessions/")
    target = {}
    for item in listing.get("items", []):
        if str(item.get("id")) == session_id:
            target = item
    report["session_listing"] = target

    # 只删探针自己建的东西：会话必须挂在临时主角色名下才可删。
    if card_id:
        req("DELETE", "/v1/cards/" + card_id)
    if main_character:
        req("DELETE", "/v1/cards/" + main_character)
    if session_id and main_character and session_id == "s_" + main_character:
        req("DELETE", "/v1/sessions/" + session_id)
    note("cleanup", {"card": card_id, "main_card": main_character, "session": session_id})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("probe written:", OUT)
    print("region_injected:", report["region_injected"])
    print("message_count:", report["message_count"])
    print("state_split:", report["state_split"])
    print("main_location:", (report["state_main"] or {}).get("location"))
    print("other_location:", (report["state_other"] or {}).get("location"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
