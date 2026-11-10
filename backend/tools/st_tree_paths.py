r"""从已保存的 SillyTavern 源码树里筛出提示引擎相关的文件路径。

用法：
    python backend\tools\st_tree_paths.py [正则]

只读。输出写到 docs/实践情况/data/sweep/st_paths.txt。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TREE = ROOT.parent / "docs" / "理论支持" / "_src" / "_st_tree.json"

PATTERN = sys.argv[1] if len(sys.argv) > 1 else r"prompt|preset|template|instruct|persona|character"


def main() -> int:
    """筛路径。"""
    data = json.loads(TREE.read_text(encoding="utf-8"))
    rx = re.compile(PATTERN, re.IGNORECASE)
    hits = [
        node["path"]
        for node in data.get("tree", [])
        if node.get("type") == "blob" and rx.search(node.get("path", ""))
    ]
    out = ROOT.parent / "docs" / "实践情况" / "data" / "sweep" / "st_paths.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(hits), encoding="utf-8")
    print(f"hits={len(hits)} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
