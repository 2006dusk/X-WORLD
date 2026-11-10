# -*- coding: utf-8 -*-
r"""V2 引入角色全链路实测：自动建卡 → AI 补全 → 配角段注入 → 再次出场。

量六件事：
1. 回合末有没有自动落成配角卡（不用人点确认）；
2. 卡上记的来源对不对（用户带出来的 / 模型带出来的）；
3. 关系程度判得对不对（相逢 / 相识 / 相熟）；
4. 待完善的卡进不进 supporting 段（不该进：设定还空着）；
5. AI 补全之后，三层设定与状态有没有填上，待完善标记有没有摘掉；
6. 补全之后再走一轮，配角有没有真的出场。

全程用自建的临时主角卡、临时会话与临时配角卡，收尾只删自己建的东西。

用法：
    python backend\tools\probe_v2_supporting.py
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
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import config  # noqa: E402
from app.assembly.pipeline import assemble  # noqa: E402
from app.contracts import ChatMessage, ChatRequest, XwOptions  # noqa: E402

from probe_supporting_common import (  # noqa: E402
    BASE,
    delete_card,
    find_supporting_card,
    http,
    supporting_ids,
    wait_for_fill,
)

PRESET = os.environ.get("XW_PRESET", "p_builtin_rp")
TMP_SESSION = "s_tmp_v2_probe"
MAIN_NAME = "角色J"
NEW_PERSON = "角色I"
FILL_WAIT_SECONDS = 120

MAIN_CARD = ""
NPC_CARD = ""


def _create_card(name: str, data: dict[str, Any]) -> dict[str, Any]:
    return http("POST", "/v1/cards/", {"name": name, "spec_version": "v1", "data": data})


def _turn(text: str) -> dict[str, Any]:
    """走真实后端跑一轮，返回正文与耗时。"""
    payload = {
        "model": "xw-local-default",
        "messages": [{"role": "user", "content": text}],
        "session": TMP_SESSION,
        "stream": True,
        "xw": {
            "character_id": MAIN_CARD,
            "preset_id": PRESET,
            "world_book_ids": [],
            "memory_mode": "hybrid",
            "max_context_tokens": 16384,
        },
    }
    started = time.perf_counter()
    body = ""
    with httpx.stream("POST", BASE + "/v1/chat/completions", json=payload, timeout=900.0) as response:
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
    return {"body": body, "elapsed_s": round(time.perf_counter() - started, 2)}


def _system_prompt(user_input: str, session: str) -> str:
    """进程内跑一次装配，拿完整的 system 段（接口预览会截断到 200 字）。"""
    request = ChatRequest(
        model="xw-local-default",
        messages=[ChatMessage(role="user", content=user_input)],
        session=session,
        stream=False,
        xw=XwOptions(
            character_id=MAIN_CARD,
            preset_id=PRESET,
            world_book_ids=[],
            memory_mode="hybrid",
            max_context_tokens=16384,
        ),
    )
    ctx = assemble(request)
    return next((msg.content for msg in ctx.messages if msg.role == "system"), "")


def _cleanup(extra: list[str] | None = None) -> dict[str, Any]:
    """删掉自己建的卡、会话与记忆目录，并回报还有没有留下东西。

    配角卡是自动建的，回合里冒出来的每个人都可能落一张卡。收尾要把
    本次探针期间新增的配角卡一并删掉——它们落在全局卡库里，留着就是
    污染用户的卡。
    """
    targets = [card_id for card_id in (MAIN_CARD, NPC_CARD) if card_id]
    targets += [card_id for card_id in (extra or []) if card_id]
    for card_id in targets:
        delete_card(card_id)
    root = config.data_root()
    for folder in ("sessions", "memories", "state"):
        path = root / folder / MAIN_CARD
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    left = []
    for card_id in targets:
        if (root / "cards" / card_id).exists():
            left.append(card_id)
    for folder in ("sessions", "memories", "state"):
        if (root / folder / MAIN_CARD).exists():
            left.append(folder + "/" + MAIN_CARD)
    return {"left": left}


def main() -> None:
    global MAIN_CARD, NPC_CARD
    out: dict[str, Any] = {}
    before_ids = supporting_ids()
    try:
        main_card = _create_card(
            MAIN_NAME,
            {
                "name": MAIN_NAME,
                "identity": "角色J，二十七岁，某出版社的主编。夜里独自留在地址R改稿。",
                "behavior": "遇到陌生人先打量，不轻易表态；手边永远有笔和校样。",
                "speech": "短句，语气克制，问话先于评价，不说教。",
                "tags": [],
            },
        )
        MAIN_CARD = str(main_card["id"])

        # 第一轮：用户给出一个新人物的名字。
        turn1 = _turn(
            "（深夜，地址R门外有人敲门。那人自称角色I，说有份急件要交给你，"
            "手上还捏着一张对折的信纸）你怎么处理？"
        )
        out["turn1_elapsed_s"] = turn1["elapsed_s"]
        out["turn1_len"] = len(turn1["body"])

        # 自动建卡：回合末就该有一张配角卡，不用人点确认。
        card = find_supporting_card(NEW_PERSON)
        out["auto_created"] = card is not None
        if card is None:
            raise SystemExit("没有自动建出配角卡")
        NPC_CARD = str(card.get("id") or "")
        data = card.get("data") or {}
        out["role"] = data.get("role")
        out["introduced_by"] = data.get("introduced_by")
        out["relation"] = data.get("relation")
        out["pending_before_fill"] = bool(data.get("pending"))

        # 待完善的卡不进 supporting 段。
        pending_text = _system_prompt("角色I还在门外吗？", TMP_SESSION)
        out["pending_not_injected"] = NEW_PERSON not in pending_text

        # AI 补全：后台跑，等它把待完善标记摘掉。
        filled = wait_for_fill(NPC_CARD, limit=FILL_WAIT_SECONDS)
        out["fill_finished"] = filled is not None
        out["fill_error"] = str(((card.get("data") or {}).get("fill_error")) or "")
        if filled is not None:
            fdata = filled.get("data") or {}
            out["pending_after_fill"] = bool(fdata.get("pending"))
            out["identity"] = str(fdata.get("identity") or "")[:60]
            out["behavior"] = str(fdata.get("behavior") or "")[:60]
            out["speech"] = str(fdata.get("speech") or "")[:60]
            out["state"] = fdata.get("state")

        # 补全之后，提到名字时配角段要进提示词。
        text = _system_prompt("角色I还在门外吗？", TMP_SESSION)
        out["injected_after_fill"] = NEW_PERSON in text
        out["supporting_before_character"] = (
            text.find(NEW_PERSON) < text.find("角色：" + MAIN_NAME)
            if NEW_PERSON in text
            else None
        )
        out["has_boundary"] = "叙述视角仍然是主角" in text

        # 第二轮：真实走一轮，看配角有没有出场。
        turn2 = _turn("（隔着门问他）你到底是谁？这封信是谁让你送的？")
        out["turn2_elapsed_s"] = turn2["elapsed_s"]
        out["turn2_len"] = len(turn2["body"])
        out["turn2_named"] = NEW_PERSON in turn2["body"]
    finally:
        # 探针期间自动建出来的配角卡，一并删掉。
        extra = sorted(supporting_ids() - before_ids - {MAIN_CARD, NPC_CARD})
        out["auto_created_extra"] = extra
        out["cleanup_left"] = _cleanup(extra)
    print(json.dumps(out, ensure_ascii=True, indent=1))


if __name__ == "__main__":
    main()
