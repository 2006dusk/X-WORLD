"""核验报告 7.14 节落地。

用法：
    python tools\verify_report_714.py
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
REPORT = ROOT.parent / "docs" / "实践情况" / "quality_tmp.md"
REPORT = ROOT.parent / "docs" / "实践情况" / "质量与速度平衡点.md"

CHECKS = [
    ("7.14 节存在", "### 7.14 十轮记忆与稳定性实测"),
    ("记忆口径", "白瓷杯、左手腕的疤、台灯、肩膀"),
    ("召回表", "记忆召回 | 事实矛盾"),
    ("默认档五次复发", "复发了五次"),
    ("4B 十轮结论", "4B 这一档在十轮里的表现最好"),
    ("2B 第十轮", "2B 前九轮几乎完美，第十轮塌了"),
    ("9B 零复读", "9B 零复读但偏长"),
    ("NQLSG 结论", "唯一零复读且零矛盾"),
    ("速率不衰减", "五档都没有明显衰减"),
    ("上下文预算", "第 21 轮占满"),
    ("第 8 节十轮条目", "十轮记忆与稳定性，7.14 节"),
    ("第 10 节 4B 候选", "4B Q4_K_XL 要补进候选"),
    ("第 10 节复读阈值", "长会话的复读阈值要定"),
]


def main() -> int:
    text = REPORT.read_text(encoding="utf-8")
    bad = 0
    for name, needle in CHECKS:
        ok = needle in text
        print("  [" + ("OK" if ok else "FAIL") + "] " + name)
        if not ok:
            bad += 1
    print("")
    for i, line in enumerate(text.splitlines(), 1):
        if line.startswith("### 7.1"):
            print("  " + str(i) + ": " + line)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
