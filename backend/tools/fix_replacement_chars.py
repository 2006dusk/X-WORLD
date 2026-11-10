r"""修掉源码里被写坏的中文字符（U+FFFD）。

只改含 U+FFFD 的行，按行号替换成正确文本，其余内容原样保留。

用法：
    python backend\tools\fix_replacement_chars.py
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

FIXES: dict[str, dict[int, str]] = {
    "backend/app/assembly/pipeline.py": {
        476: "    身份锚点比状态更权威：状态说在哪，身份说他是哪个人。",
    },
    "backend/app/routers/chat.py": {
        320: "    口径与 SillyTavern 一致：AI 只写角色本人，不替用户决定说什么或做什么，",
    },
}


def main() -> int:
    """按行修。"""
    changed = 0
    for relative, lines_map in FIXES.items():
        path = ROOT / relative
        raw = path.read_bytes().decode("utf-8")
        newline = "\r\n" if "\r\n" in raw else "\n"
        lines = raw.replace("\r\n", "\n").split("\n")
        for number, text in lines_map.items():
            index = number - 1
            if index >= len(lines):
                print(f"skip {relative}:{number} out of range")
                continue
            if "\ufffd" not in lines[index]:
                print(f"skip {relative}:{number} no bad char")
                continue
            lines[index] = text
            changed += 1
        path.write_bytes(newline.join(lines).encode("utf-8"))
        print(f"fixed {relative}")
    print(f"changed={changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
