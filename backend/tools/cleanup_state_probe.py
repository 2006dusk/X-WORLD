r"""清理状态块实测留下的临时数据。

删掉对照预设与探针会话，探针角色的状态目录也一并删掉，
免得它们出现在会话列表与记忆视图里。

用法：
    F:pythonpython.exe toolscleanup_state_probe.py
"""

from __future__ import annotations

import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from app import store  # noqa: E402

PRESETS = ["p_ab_2block"]

SESSIONS = [
    "s_state_block",
    "s_state_block_v2",
    "s_state_block_v3",
    "s_state_block_v4",
    "s_ab_2b",
    "s_ab_3b",
    "s_ab2_2b",
    "s_ab2_3b",
]

CHARACTERS = ["c_ab_2b", "c_ab_3b", "c_ab2_2b", "c_ab2_3b"]


def main() -> int:
    """删除临时预设、探针会话与探针角色的状态目录。"""
    for preset_id in PRESETS:
        if store.delete_preset(preset_id):
            print("preset deleted " + preset_id)

    for session_id in SESSIONS:
        for scope in ("global",) + tuple(CHARACTERS):
            directory = store.sessions_dir(scope)
            for suffix in (".jsonl", ".md"):
                path = directory / (session_id + suffix)
                if path.exists():
                    path.unlink()
                    print("session deleted " + str(path))

    for character_id in CHARACTERS:
        directory = store.states_dir(character_id)
        if directory.exists():
            for item in directory.iterdir():
                item.unlink()
            directory.rmdir()
            print("state deleted " + str(directory))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
