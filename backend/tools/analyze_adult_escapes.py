"""量化成人向五回合的转义残留与引号形态。

用法：
    python tools\analyze_adult_escapes.py
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
ADULT = ROOT.parent / "docs" / "实践情况" / "data" / "sweep" / "adult.json"


def main() -> int:
    rows = json.loads(ADULT.read_text(encoding="utf-8"))
    print("== 转义残留与引号形态 ==")
    for row in rows:
        turns = row.get("turns") or []
        if not turns:
            continue
        esc = 0
        ascii_q = 0
        cjk_q = 0
        total_q = 0
        for turn in turns:
            text = str(turn.get("content") or "")
            if chr(92) + chr(34) in text:
                esc += 1
            cjk_q += text.count(chr(0x201C))
            ascii_q += text.count(chr(34))
        print("  {} 转义残留轮次 {}/{} 中文引号 {} 个 ASCII 引号 {} 个".format(
            row.get("name"), esc, len(turns), cjk_q, ascii_q))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
