r"""探针：配角卡挂进世界书后，与主角段在提示词里怎么并存。

已知问题：把配角卡挂成世界书条目（card_ref）时，条目正文只取 identity 与
behavior，丢 speech 与 tags，也没有「这是场上的另一个人」的标识，与主角段
除了排版没有任何语义差别。

本探针用临时世界书挂一张配角卡，与主角同场装配一轮，看提示词实际长什么样。
收尾删掉临时世界书，不碰真实数据。

用法：
    python backend\tools\probe_npc_card.py
    python backend\tools\probe_npc_card.py --npc 角色C --main 角色A
"""

from __future__ import annotations

import argparse
import json
import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from app import store
from app.assembly.pipeline import assemble
from app.contracts import ChatMessage, ChatRequest, XwOptions
from app.worldbooks import create_entry, delete_book, save_book

TMP_BOOK = "w_tmp_npc_probe"


def _card_id_by_name(name: str) -> str:
    """按角色名找卡 id。找不到就报错，不猜。"""
    for card in store.list_cards():
        if str(card.get("name") or "") == name:
            return str(card.get("id") or "")
    raise SystemExit("找不到角色卡: " + name)


def _region_of(card_id: str) -> str:
    """从角色状态里取绑定地区。状态是角色绑定地区的唯一落点。"""
    path = store.state_path(card_id)
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(data.get("region_id") or "")


def main() -> int:
    """跑一次装配，把三段正文打印出来。"""
    parser = argparse.ArgumentParser(description="配角卡与主角段的并存探针")
    parser.add_argument("--npc", default="角色C", help="配角卡的角色名")
    parser.add_argument("--main", default="角色A", help="主角卡的角色名")
    args = parser.parse_args()

    npc_card = _card_id_by_name(args.npc)
    main_card = _card_id_by_name(args.main)
    region = _region_of(main_card)

    try:
        save_book({"id": TMP_BOOK, "name": "探针临时世界书", "enabled": True, "entries": []})
        entry = create_entry(
            TMP_BOOK,
            {
                "kind": "entry",
                "name": "临时配角",
                "comment": "临时配角",
                "keys": [args.npc],
                "priority": 12,
                "card_ref": {"card_id": npc_card, "name": args.npc},
            },
        )
        request = ChatRequest(
            model="xw-local-default",
            messages=[ChatMessage(role="user", content=args.npc + "今天也在")],
            stream=False,
            xw=XwOptions(
                character_id=main_card,
                preset_id="p_builtin_rp",
                world_book_ids=[TMP_BOOK],
                region_id=region or None,
                memory_mode="hybrid",
                max_context_tokens=16384,
            ),
        )
        ctx = assemble(request)
        segs = {seg.name: seg for seg in ctx.report.segments}
        system = ""
        for msg in ctx.messages:
            if msg.role == "system":
                system = str(msg.content or "")
                break
        print("===== 主角卡:", main_card, " 配角卡:", npc_card, " 地区:", region)
        print("===== character 段 tokens:", segs["character"].tokens)
        print("===== world_info 段 tokens:", segs["world_info"].tokens)
        print("===== world_info 命中条目:", segs["world_info"].entries)
        print()
        print("----- 提示词正文（截 1600 字）-----")
        print(system[:1600])
        print()
        print("----- 配角条目正文长度:", len(str(entry.get("content") or "")))
        print("----- 配角条目正文-----")
        print(str(entry.get("content") or "")[:700])
        return 0
    finally:
        delete_book(TMP_BOOK)
        print()
        print("[cleanup] 临时世界书已删:", not store.get_world_book(TMP_BOOK))


if __name__ == "__main__":
    sys.exit(main())
