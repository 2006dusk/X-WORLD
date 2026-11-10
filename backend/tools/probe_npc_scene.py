# -*- coding: utf-8 -*-
r"""配角同场实测：主角引入配角、触发事件、两人对话。

场景：角色D（主角，温柔学姐）在地址K找角色C（配角，地址L社长）。
三轮：引入配角 → 触发事件 → 对话追问。

两组对照，提示词内容不同，其余完全一致：
  A 现状——配角卡挂世界书条目（card_ref），条目正文自动生成，只有 identity
    与 behavior，丢 speech，也没有「这是场上的另一个人」的标识。
  B 增强——同一张卡，条目正文补上 speech，并加一句身份边界。

量五件事：配角有没有出场、配角有没有台词、台词归给谁、台词像谁的口吻、
有没有替用户说话。

用法：
    python backend\tools\probe_npc_scene.py

收尾：删掉两个临时世界书与两个临时会话，主角状态原样写回。
"""

from __future__ import annotations

import json
import os
import re
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
from app.worldbooks import create_entry, delete_book, find_region, save_book  # noqa: E402

BASE = os.environ.get("XW_BACKEND", "http://127.0.0.1:8760")
PRESET = os.environ.get("XW_PRESET", "p_builtin_rp")
MAIN_NAME = "角色D"
NPC_NAME = "角色C"
BOOK_A = "w_tmp_npc_scene_a"
BOOK_B = "w_tmp_npc_scene_b"

INPUTS = [
    "我在馆门口，角色C在里面吗？帮我叫一下她。",
    "（把外套递给她）外面有人在喊你的名字，像是社里出了什么事。",
    "（看着她）你刚才瞒了我什么？那个人是谁？",
]

QUOTE = re.compile("[“「]([^”」]{1,160})[”」]")
# 配角的口吻特征：从她卡里的 speech 层取的（嗓门大、说话直、只说当下的小事）。
NPC_STYLE = ["还不练", "几点了", "鞋刷", "靶子", "练什么", "咋", "谁啊", "嘁", "得了吧", "走了", "快点"]
# 主角的口吻特征：从她卡里的 speech 层取的（声音轻、语速慢、爱说软话）。
MAIN_STYLE = ["乖", "这样啊", "嗯", "补习", "题", "学姐", "别急", "慢慢"]

MAIN_CARD = ""


def _card_by_name(name: str) -> dict[str, Any]:
    for card in store.list_cards():
        if str(card.get("name") or "") == name:
            return card
    raise SystemExit("找不到角色卡: " + name)


def _payload(session: str, text: str, books: list[str], region: str) -> dict[str, Any]:
    return {
        "model": "xw-local-default",
        "messages": [{"role": "user", "content": text}],
        "session": session,
        "stream": True,
        "xw": {
            "character_id": MAIN_CARD,
            "preset_id": PRESET,
            "world_book_ids": books,
            "region_id": region,
            "memory_mode": "hybrid",
            "max_context_tokens": 16384,
        },
    }


def _turn(session: str, text: str, books: list[str], region: str) -> dict[str, Any]:
    """走真实后端跑一轮，返回正文、分块与耗时。"""
    started = time.perf_counter()
    body = ""
    blocks: dict[str, str] = {}
    with httpx.stream(
        "POST",
        BASE + "/v1/chat/completions",
        json=_payload(session, text, books, region),
        timeout=900.0,
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
            elif event == "blocks":
                blocks = dict(data.get("blocks") or {})
    return {
        "body": body,
        "scene": str(blocks.get("scene") or ""),
        "dialogue": str(blocks.get("dialogue") or ""),
        "elapsed_s": round(time.perf_counter() - started, 2),
    }


def _quotes(text: str) -> list[tuple[str, str]]:
    """把引号里的对白抠出来，并给每句找一个归属。

    归属看对白前 40 个字里出现的是配角名还是主角名；都没有就算未知
    （两个都是「她」，光看「她」判不了）。
    """
    rows: list[tuple[str, str]] = []
    for match in QUOTE.finditer(text):
        quote = match.group(1)
        window = text[max(0, match.start() - 40):match.start()]
        if NPC_NAME in window or "社长" in window:
            owner = "npc"
        elif MAIN_NAME in window or "学姐" in window:
            owner = "main"
        else:
            owner = "unknown"
        rows.append((owner, quote))
    return rows


def _style_of(quote: str) -> str:
    """按口吻特征词判断这句话更像谁。"""
    npc_hits = sum(1 for word in NPC_STYLE if word in quote)
    main_hits = sum(1 for word in MAIN_STYLE if word in quote)
    if npc_hits > main_hits:
        return "npc"
    if main_hits > npc_hits:
        return "main"
    return "none"


def _score(turn: dict[str, Any], text: str) -> dict[str, Any]:
    """给一轮打分。"""
    quotes = _quotes(text)
    owners = {"npc": 0, "main": 0, "unknown": 0}
    styles = {"npc": 0, "main": 0, "none": 0}
    for owner, quote in quotes:
        owners[owner] += 1
        styles[_style_of(quote)] += 1
    violation = _role_owner_violation(
        {"scene": turn["scene"], "dialogue": turn["dialogue"]},
        ["scene", "dialogue", "state"],
        MAIN_NAME,
    )
    return {
        "chars": len(text),
        "length_ok": 150 <= len(text) <= 300,
        "npc_present": NPC_NAME in text,
        "quotes": len(quotes),
        "owners": owners,
        "styles": styles,
        "violation": violation,
    }


def _card_layers(card_id: str) -> dict[str, str]:
    card = store.get_card(card_id)
    data = card.get("data") if isinstance(card, dict) else None
    data = data if isinstance(data, dict) else {}
    return {
        "identity": str(data.get("identity") or ""),
        "behavior": str(data.get("behavior") or ""),
        "speech": str(data.get("speech") or ""),
    }


def _delete_session(session: str) -> bool:
    try:
        response = httpx.delete(BASE + "/v1/sessions/" + session, timeout=30.0)
        return response.status_code < 400
    except Exception:
        return False


def main() -> int:
    """跑两组对照。"""
    global MAIN_CARD
    main_card = _card_by_name(MAIN_NAME)
    npc_card = _card_by_name(NPC_NAME)
    MAIN_CARD = str(main_card.get("id") or "")
    npc_id = str(npc_card.get("id") or "")
    region = str(npc_card.get("region_id") or "")
    found = find_region(region) or {}
    book_id = str(found.get("book_id") or "")
    layers = _card_layers(npc_id)

    # B 组用的边界句。--boundary fixed 换用改写后的措辞（见报告 9.3 节）。
    # 默认措辞是 2026-11-07 实测用的那一版：它被模型读成了「以配角为视角」。
    if os.environ.get("DBG_BOUNDARY") == "fixed":
        boundary = (
            "场上还有别人在场：" + NPC_NAME + "。她是配角，不是你要扮演的角色。"
            "你用第三人称写她：她说话、她做事，都由你从旁描写。"
            "但本回合的叙述视角仍然是主角（" + MAIN_NAME + "）——主角才是镜头所在，配角只是场景里的人。"
        )
    else:
        boundary = (
            "场上其他人（你只描述 ta 的言行，不要让 ta 代替你说话或行动，也不要把自己变成 ta）："
        )
    content_b = chr(10).join(
        [
            boundary,
            "【" + NPC_NAME + "】",
            "身份与处境：" + layers["identity"],
            "行为与细节：" + layers["behavior"],
            "说话方式：" + layers["speech"],
        ]
    )

    state_file = store.states_dir(MAIN_CARD) / "state.json"
    backup = state_file.with_suffix(".json.bak")
    had_state = state_file.exists()
    if had_state:
        shutil.copyfile(state_file, backup)

    sessions = {"a": "s_tmp_npc_scene_a", "b": "s_tmp_npc_scene_b"}
    results: dict[str, Any] = {}
    try:
        # A 组：现状。配角卡挂条目，正文由卡片自动生成。
        save_book({"id": BOOK_A, "name": "探针临时世界书A", "enabled": True, "entries": []})
        create_entry(
            BOOK_A,
            {
                "kind": "entry",
                "name": "临时配角A",
                "comment": "临时配角A",
                "keys": [NPC_NAME],
                "priority": 12,
                "card_ref": {"card_id": npc_id, "name": NPC_NAME},
            },
        )
        # B 组：增强。同一张卡，补 speech 与身份边界。
        save_book({"id": BOOK_B, "name": "探针临时世界书B", "enabled": True, "entries": []})
        create_entry(
            BOOK_B,
            {
                "kind": "entry",
                "name": "临时配角B",
                "comment": "临时配角B",
                "keys": [NPC_NAME],
                "priority": 12,
                "content": content_b,
            },
        )

        for group, book in (("a", BOOK_A), ("b", BOOK_B)):
            rows: list[dict[str, Any]] = []
            for index, item in enumerate(INPUTS):
                turn = _turn(sessions[group], item, [book_id, book], region)
                text = turn["scene"] + chr(10) + turn["dialogue"] or turn["body"]
                score = _score(turn, text)
                rows.append(
                    {
                        "input": item,
                        "text": text,
                        "elapsed_s": turn["elapsed_s"],
                        "score": score,
                    }
                )
                print(
                    "[%s] 第%d轮 %.1fs 字数=%d 配角出场=%s 台词数=%d 归属=%s 口吻=%s 越位=%s"
                    % (
                        group,
                        index + 1,
                        turn["elapsed_s"],
                        score["chars"],
                        score["npc_present"],
                        score["quotes"],
                        score["owners"],
                        score["styles"],
                        score["violation"] or "ok",
                    )
                )
            results[group] = rows
    finally:
        for book in (BOOK_A, BOOK_B):
            delete_book(book)
        for session in sessions.values():
            _delete_session(session)
        if had_state and state_file.exists():
            shutil.copyfile(state_file, backup)
        elif not had_state and state_file.exists():
            os.remove(state_file)
        if backup.exists():
            os.remove(backup)

    out = ROOT / "tmp" / "npc_scene.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for group in ("a", "b"):
        lines.append("========== 组 " + group.upper() + " ==========")
        for index, row in enumerate(results.get(group, [])):
            score = row["score"]
            lines.append(
                "--- 第%d轮 输入：%s（%.1fs，字数%d）"
                % (index + 1, row["input"], row["elapsed_s"], score["chars"])
            )
            lines.append(
                "    配角出场=%s 台词数=%d 归属=%s 口吻=%s 越位=%s 长度达标=%s"
                % (
                    score["npc_present"],
                    score["quotes"],
                    score["owners"],
                    score["styles"],
                    score["violation"] or "ok",
                    score["length_ok"],
                )
            )
            lines.append(row["text"])
            lines.append("")
    out.write_text(chr(10).join(lines), encoding="utf-8")
    print("[out]", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
