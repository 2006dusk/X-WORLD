"""导出指定会话里最近若干条 AI 回复的正文，用于人工核对角色视角。

用法：
    python backend\tools\dump_session_replies.py s_demo c_demo 14

只读，不写任何数据文件。输出写到 docs/实践情况/data/sweep/
下的 dump_<session>.md，用 UTF-8 写，避免控制台 GBK 乱码。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import store  # noqa: E402
from app.inference.structure import split_blocks  # noqa: E402


def _rows(session_id: str, character_id: str) -> list[dict]:
    """读会话文件的全部记录行。"""
    path = store.sessions_dir(character_id) / f"{session_id}.jsonl"
    if not path.exists():
        return []
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def main() -> int:
    """导出最近 N 条 AI 回复的正文。"""
    session_id = sys.argv[1] if len(sys.argv) > 1 else "s_demo"
    character_id = sys.argv[2] if len(sys.argv) > 2 else "c_demo"
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 12

    rows = _rows(session_id, character_id)
    replies = [r for r in rows if r.get("role") == "assistant"]
    picked = replies[-limit:]

    out_dir = ROOT.parent / "docs" / "实践情况" / "data" / "sweep"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"dump_{session_id}.md"

    lines: list[str] = [f"# 会话 {session_id} 最近 {len(picked)} 条 AI 回复", ""]
    for index, row in enumerate(picked, start=1):
        blocks = row.get("blocks")
        content = str(row.get("content", ""))
        scene = ""
        dialogue = ""
        state = ""
        if isinstance(blocks, dict):
            scene = str(blocks.get("scene", "") or "")
            dialogue = str(blocks.get("dialogue", "") or "")
            state = str(blocks.get("state", "") or "")
        else:
            parsed = split_blocks(content)
            scene = parsed.get("scene", "")
            dialogue = parsed.get("dialogue", "")
            state = parsed.get("state", "")
        lines.append(f"## #{index} id={row.get('id')} parent={row.get('parent_id')}")
        lines.append(f"- role={row.get('role')} created={row.get('created_at')}")
        lines.append("")
        lines.append("【场景】")
        lines.append(scene)
        lines.append("")
        lines.append("【对话】")
        lines.append(dialogue)
        lines.append("")
        lines.append("【状态】")
        lines.append(state)
        lines.append("")
        lines.append("---")
        lines.append("")

    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    print(f"wrote {out_path} replies={len(picked)} total_assistant={len(replies)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
