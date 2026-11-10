r"""用装配管线同一套规则清洗角色级世界状态，落盘回写。

背景：模型早期的一次漂移被写进世界状态（角色K在厨房做咖啡），状态提示
又要求场景与状态一致，漂移因此每轮被固化。渲染期过滤已经在装配管线里
生效，这里把落盘的状态也清一遍，让右侧面板与模型看到的一致。

用法：
    python backend\tools\clean_world_state.py [角色id] [世界书id...]

示例：
    python backend\tools\clean_world_state.py c_demo w_demo
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import store  # noqa: E402
from app.assembly.pipeline import _filtered_state  # noqa: E402
from app.contracts import XwOptions  # noqa: E402


def main() -> int:
    """清洗并写回。"""
    character_id = sys.argv[1] if len(sys.argv) > 1 else "c_demo"
    books = sys.argv[2:] or ["w_demo"]
    xw = XwOptions(character_id=character_id, world_book_ids=books)
    before = store.get_state(character_id) or {}
    cleaned = _filtered_state(xw)
    if not cleaned:
        print("no state to clean")
        return 0
    store.save_state(cleaned, character_id)
    after = store.get_state(character_id) or {}
    print("position: %r -> %r" % (before.get("position"), after.get("position")))
    print(
        "scene_items: %s -> %s"
        % (before.get("scene_items"), after.get("scene_items"))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
