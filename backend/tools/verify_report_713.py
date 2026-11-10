"""核验报告 7.13 节与相关口径的落地。

用法：
    python tools\verify_report_713.py
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
REPORT = ROOT.parent / "docs" / "实践情况" / "质量与速度平衡点.md"

CHECKS = [
    ("7.13 节存在", "### 7.13 成人向内容的五回合实测"),
    ("速率取法说明", "不带 usage，拿不到 completion_tokens"),
    ("五档表格", "目标带内"),
    ("拒绝结论", "拒绝关键词命中 0 次"),
    ("默认档复述", "第三轮与第二轮重合 95.3%"),
    ("2B 零复述", "五轮零复述"),
    ("9B 转义残留", "转义残留"),
    ("NQLSG 长度", "全部低于 150 字下沿"),
    ("第 8 节销项", "| 已覆盖 | 成人向内容表现"),
    ("第 10 节复述条目", "默认档的整轮复述要处理"),
    ("第 10 节 2B 条目", "2B 的轻量档地位可以上调"),
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
