"""打印指定场景的逐轮原文，用于人工核对复读。

用法：
    python tools\peek_explicit_scene.py <场景 id> [配置名子串]
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = ROOT.parent / "docs" / "实践情况" / "data" / "sweep"
EXPL = DATA / "explicit_5set.json"


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        print("需要场景 id")
        return 1
    sid = argv[0]
    wanted = [a.lower() for a in argv[1:]]
    rows = json.loads(EXPL.read_text(encoding="utf-8"))
    for row in rows:
        name = str(row.get("name"))
        if wanted and not any(w in name.lower() for w in wanted):
            continue
        for scene in row.get("scenes") or []:
            if str(scene.get("id")) != sid:
                continue
            print("==", name, "场景", scene.get("id"), scene.get("name"))
            for turn in scene.get("turns") or []:
                m = turn.get("metrics") or {}
                print("  第{}轮 {}字 解码{} tok/s".format(turn.get("turn"), m.get("chars"), m.get("decode_tok_per_s")))
                print("    " + str(turn.get("content")))
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
