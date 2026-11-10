r"""建一个临时对照预设：结构只有场景与对白两块。

用于「加状态块到底吃掉多少正文篇幅」的同条件 A/B。跑完由清理脚本删除。

用法：
    F:pythonpython.exe toolsprobe_state_preset.py
"""

from __future__ import annotations

import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from app import presets as preset_service  # noqa: E402
from app import store  # noqa: E402

TWO_BLOCK_PRESET_ID = "p_ab_2block"


def main() -> int:
    """写出两块结构的对照预设。"""
    preset_service.ensure_builtin_preset()
    payload = preset_service.builtin_preset()
    payload["id"] = TWO_BLOCK_PRESET_ID
    payload["name"] = "对照预设（两块）"
    payload["builtin"] = False
    payload["output_spec"] = dict(payload["output_spec"])
    payload["output_spec"]["structure"] = ["scene", "dialogue"]
    store.save_preset(payload)
    print("written " + TWO_BLOCK_PRESET_ID)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
