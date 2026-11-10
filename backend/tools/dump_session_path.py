r"""按当前分支路径导出会话内容，带上用户输入，用于核对角色视角。

用法：
    python backend\tools\dump_session_path.py s_demo c_demo 24

只读。输出写到 docs/实践情况/data/sweep/path_<session>.md，UTF-8。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import store  # noqa: E402
from app.inference.structure import block_names, split_blocks  # noqa: E402


def _blocks(row: dict) -> tuple[str, str, str]:
    """取一行消息的场景、对白、状态三块。"""
    blocks = row.get("blocks")
    if isinstance(blocks, dict):
        return (
            str(blocks.get("scene", "") or ""),
            str(blocks.get("dialogue", "") or ""),
            str(blocks.get("state", "") or ""),
        )
    parsed = split_blocks(
        str(row.get("content", "")), block_names(["scene", "dialogue", "state"])
    )
    return (
        parsed.get("scene", ""),
        parsed.get("dialogue", ""),
        parsed.get("state", ""),
    )


def main() -> int:
    """导出当前路径。"""
    session_id = sys.argv[1] if len(sys.argv) > 1 else "s_demo"
    character_id = sys.argv[2] if len(sys.argv) > 2 else "c_demo"
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 24

    rows = store.session_path(session_id, character_id, None)
    picked = rows[-limit:]

    out_dir = ROOT.parent / "docs" / "实践情况" / "data" / "sweep"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"path_{session_id}.md"

    lines: list[str] = [f"# 会话 {session_id} 当前路径最后 {len(picked)} 条", ""]
    for index, row in enumerate(picked, start=1):
        role = str(row.get("role", ""))
        lines.append(f"## #{index} [{role}] id={row.get('id')}")
        if role == "assistant":
            scene, dialogue, state = _blocks(row)
            lines.append("【场景】" + scene)
            lines.append("")
            lines.append("【对话】" + dialogue)
            lines.append("")
            if state.strip():
                lines.append("【状态】" + state)
                lines.append("")
        else:
            lines.append(str(row.get("content", "")))
            lines.append("")
        lines.append("---")
        lines.append("")

    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    print(f"wrote {out_path} path_len={len(rows)} picked={len(picked)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
