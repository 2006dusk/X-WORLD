"""分支删除的界面实测探针。

造一段临时角色与临时会话：主线四轮，支线从第三轮分出去两轮。用来在浏览器
里点「删除这一段」，看请求与刷新是不是正常。

只动自己造的东西：cleanup 子命令删掉临时会话与临时角色卡。
数据目录跟后端一致，用 XWORLD_DATA_DIR 或默认位置。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import store  # noqa: E402

CARD_NAME = "TMP_PROBE_BRANCH"


def seed() -> dict[str, str]:
    """造临时角色卡与临时会话：主线四轮，支线两轮。"""
    card = store.save_card({"name": CARD_NAME})
    card_id = str(card["id"])
    session_id = store.canonical_session_id(card_id)
    first = store.branch_message(
        session_id, card_id, "", {"role": "user", "content": "AAA"}
    )
    second = store.branch_message(
        session_id, card_id, str(first["id"]), {"role": "assistant", "content": "BBB"}
    )
    third = store.branch_message(
        session_id, card_id, str(second["id"]), {"role": "user", "content": "CCC"}
    )
    store.branch_message(
        session_id, card_id, str(third["id"]), {"role": "assistant", "content": "DDD"}
    )
    store.branch_message(
        session_id, card_id, str(third["id"]), {"role": "assistant", "content": "EEE"}
    )
    return {"card_id": card_id, "session_id": session_id}


def cleanup(card_id: str, session_id: str) -> dict[str, bool]:
    """删掉临时会话与临时角色卡。"""
    session_gone = False
    for scope in (card_id, store.GLOBAL_SCOPE):
        path = store.sessions_dir(scope) / f"{session_id}.jsonl"
        if path.exists():
            path.unlink()
            session_gone = True
    card_gone = store.delete_card(card_id)
    return {"session": session_gone, "card": card_gone}


def main() -> int:
    """入口。"""
    parser = argparse.ArgumentParser(description="分支删除界面实测探针")
    parser.add_argument("action", choices=["seed", "cleanup"])
    parser.add_argument("--card", default="")
    parser.add_argument("--session", default="")
    args = parser.parse_args()
    if args.action == "seed":
        print(json.dumps(seed(), ensure_ascii=True))
        return 0
    print(json.dumps(cleanup(args.card, args.session), ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
