r"""同步构建状态文档的工程规模行。

用正则替换，避免行内容随工具增减而变化时匹配失败。

用法：
    python tools\update_build_status.py
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
DOC = ROOT.parent / "docs" / "项目能力" / "能力台账.md"

PATTERN = re.compile(r"\| 后端 Python 文件 \|[^\n]*\|")


def count(folder: pathlib.Path) -> tuple[int, int]:
    files = 0
    lines = 0
    if not folder.exists():
        return 0, 0
    for path in folder.rglob("*.py"):
        files += 1
        try:
            lines += len(path.read_text(encoding="utf-8").splitlines())
        except (OSError, UnicodeDecodeError):
            pass
    return files, lines


def main() -> int:
    app = count(BACKEND / "app")
    tests = count(BACKEND / "tests")
    tools = count(BACKEND / "tools")
    total_files = app[0] + tests[0] + tools[0]
    total_lines = app[1] + tests[1] + tools[1]
    print("应用层 {} 个 {} 行".format(app[0], app[1]))
    print("测试 {} 个 {} 行".format(tests[0], tests[1]))
    print("工具 {} 个 {} 行".format(tools[0], tools[1]))
    print("合计 {} 个 约 {} 行".format(total_files, total_lines))
    new = "| 后端 Python 文件 | 应用层 {} 个 {} 行，测试 {} 个 {} 行，工具 {} 个 {} 行，合计 {} 个约 {} 行 |".format(
        app[0], app[1], tests[0], tests[1], tools[0], tools[1], total_files, total_lines)
    text = DOC.read_text(encoding="utf-8")
    if not PATTERN.search(text):
        print("未找到工程规模行")
        return 1
    DOC.write_text(PATTERN.sub(new, text, count=1), encoding="utf-8")
    print("构建状态文档已同步")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
