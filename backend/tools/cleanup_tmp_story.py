"""清理走查探针遗留的临时角色卡与会话。

探针跑到一半崩掉时（比如推理服务被掐），收尾那一步不会执行，
临时卡与临时会话就留在数据目录里。这个脚本按 TMP_STORY_ 前缀找出
这些残留并删干净，只删探针自己建的。

用法：
  python backend/tools/cleanup_tmp_story.py
"""

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import store  # noqa: E402


def main() -> int:
    """找出 TMP_STORY_ 开头的临时卡并连同会话与状态一起删掉。"""
    found = []
    for summary in store.list_cards():
        name = str(summary.get("name") or "")
        if name.startswith("TMP_STORY") or name.startswith("TMP_IDENT"):
            found.append((str(summary.get("id")), name))

    done = {"cards": 0, "sessions": 0, "states": 0}
    for card_id, name in found:
        for scope in (card_id, store.GLOBAL_SCOPE):
            directory = store.sessions_dir(scope)
            if directory.exists():
                for path in directory.glob("s_tmp_story_*.jsonl"):
                    path.unlink()
                    done["sessions"] += 1
        state_file = store.state_path(card_id)
        if state_file.exists():
            state_file.unlink()
            done["states"] += 1
        if store.delete_card(card_id):
            done["cards"] += 1

    print(json.dumps({"found": found, "done": done}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
