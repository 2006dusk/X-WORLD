r"""列出会话列表与每个会话的当前路径长度、最后一条消息。

用法：
    python backend\tools\list_sessions.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import store  # noqa: E402


def main() -> int:
    """列出会话。"""
    out_dir = ROOT.parent / "docs" / "实践情况" / "data" / "sweep"
    out_dir.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for item in store.list_sessions():
        sid = str(item.get("id", ""))
        cid = str(item.get("character_id", "") or "")
        rows = store.session_path(sid, cid, None)
        last = ""
        for row in reversed(rows):
            if row.get("role") == "assistant":
                last = str(row.get("content", ""))[:80].replace("\n", " ")
                break
        lines.append(
            f"{sid} | char={cid} | preset={item.get('preset_id')} | "
            f"path={len(rows)} | count={item.get('message_count')} | last={last}"
        )
    text = "\n".join(lines)
    with open(out_dir / "sessions.txt", "w", encoding="utf-8") as handle:
        handle.write(text)
    print(f"sessions={len(lines)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
