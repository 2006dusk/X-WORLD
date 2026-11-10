r"""把多轮评测结果导成可读的 UTF-8 文本，供报告引用。

用法：
    python backend\tools\dump_multi_turn.py [输出文件]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = TOOLS_DIR.parent
SWEEP_DIR = BACKEND_ROOT.parent.parent / "docs" / "实践情况" / "data" / "sweep"
OUT_FILE = SWEEP_DIR / "multi_turn_dump.md"


def main() -> int:
    """导出。"""
    path = SWEEP_DIR / "multi_turn.json"
    if not path.exists():
        print("缺少 multi_turn.json")
        return 1
    rows = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(rows, dict):
        rows = list(rows.values())
    lines = ["# 多轮评测记录", ""]
    for row in rows:
        lines.append("## " + str(row.get("name")))
        lines.append("")
        lines.append("- 参数：" + str(row.get("params")) + "，量化：" + str(row.get("quant"))
                     + "，备注：" + str(row.get("note")))
        judge = {str(j.get("turn")): j for j in (row.get("judge") or []) if isinstance(j, dict)}
        for turn in row.get("turns") or []:
            m = turn.get("metrics") or {}
            lines.append("")
            lines.append("### 第 %s 轮" % turn.get("turn"))
            lines.append("")
            lines.append("- 用户：" + str(turn.get("user")))
            lines.append("- 字数 %s，对白 %s 段，中文引号 %s，ASCII 引号 %s，与前文重合 %s，耗时 %s s"
                         % (m.get("chars"), m.get("dialogue_count"), m.get("cjk_dialogue_count"),
                            m.get("ascii_dialogue_count"), m.get("overlap_history"), m.get("wall_s")))
            j = judge.get(str(turn.get("turn"))) or {}
            if j:
                lines.append("- 裁判推进分 %s，新信息 %s，动作 %s，对白 %s"
                             % (j.get("progress"), j.get("new_info"), j.get("action"), j.get("dialogue")))
                lines.append("- 裁判理由：" + str(j.get("reason")))
            lines.append("")
            lines.append("> " + str(turn.get("content")).replace("\n", "\n> "))
        lines.append("")
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT_FILE
    target.write_text("\n".join(lines), encoding="utf-8")
    print("已写入 %s" % target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
