r"""合并扫描结果文件。

sweep_models / multi_turn / probe_logic 都只覆盖自己这一轮跑过的档位，
分批跑的时候要先把旧文件另存，再把新结果并回去，避免丢历史档位。

用法：
    python backend\tools\merge_sweep_json.py <输出文件> <输入文件> ...
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def load(path: Path) -> list[dict]:
    """读一个结果文件。"""
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data if isinstance(data, list) else list(data.values())
    return [r for r in rows if isinstance(r, dict)]


def main() -> int:
    """按 name 去重合并，输出文件写最后一份，其余为新增。"""
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    out = Path(sys.argv[1])
    inputs = [Path(p) for p in sys.argv[2:]]
    merged: dict[str, dict] = {}
    order: list[str] = []
    for path in inputs:
        for row in load(path):
            key = str(row.get("name") or len(order))
            if key not in merged:
                order.append(key)
            merged[key] = row
    out.write_text(json.dumps([merged[k] for k in order], ensure_ascii=False, indent=2), encoding="utf-8")
    print("已合并 %d 个档位 → %s" % (len(order), out))
    for key in order:
        print("  " + key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
