"""核验报告 7.15 节落地。

用法：
    python tools\verify_report_715.py
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
REPORT = ROOT.parent / "docs" / "实践情况" / "质量与速度平衡点.md"

CHECKS = [
    ("7.15 节存在", "### 7.15 五组高强度场景与场景动作拆分"),
    ("口径说明", "同一套内置预设规格"),
    ("拆分方法", "逐句判定归属"),
    ("指标表", "场景字数占比"),
    ("动作承接表", "动作承接"),
    ("零拒绝结论", "一百轮全部正常续写"),
    ("承接率差距", "默认档只有 14/20"),
    ("整段复读", "地址S车内场景"),
    ("场景占比低", "没有一档超过两成"),
    ("场景与对白不成正比", "写场景的能力与写对白的能力不成正比"),
    ("客户端含义", "对客户端的直接含义有三条"),
    ("推荐 4B", "Qwen3.5-4B-UD 是最优的一档"),
    ("第 8 节条目", "五组高强度成人向场景，7.15 节"),
    ("第 10 节场景示范", "场景描写要单独给示范"),
    ("第 10 节动作承接", "客户端加动作承接校验"),
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
