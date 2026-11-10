r"""同步报告第 8 节与第 10 节，销掉五组高强度场景相关待办。

用法：
    python tools\update_report_715.py
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
REPORT = ROOT.parent / "docs" / "实践情况" / "质量与速度平衡点.md"
NL = chr(10)

OLD1 = "| 已覆盖 | 十轮记忆与稳定性，7.14 节五档各十轮，事实召回、事实矛盾、整轮复述、字数漂移与上下文预算 |"
NEW1 = (OLD1 + NL
        + "| 已覆盖 | 五组高强度成人向场景，7.15 节五档各二十轮，新增动作承接率与场景动作拆分两项指标 |")

OLD2 = "| 未覆盖 | 不同温度的方差，温度固定在 0.8 |"
NEW2 = ("| 已覆盖 | 拒绝行为已在 7.13 与 7.15 两轮共一百二十五轮验证，五档零拒绝 |"
        + NL
        + "| 未覆盖 | 不同温度的方差，温度固定在 0.8 |")

OLD3 = "16. 长会话的复读阈值要定。7.14 节显示各档退化点不同：默认档第三轮开始复读，2B 到第十轮才塌，4B 只第三轮一次。客户端应加一条规则：检测到与前一轮重合超过 90% 时重采样或降低温度，而不是让复读一路延续。"
I17 = "17. 场景描写要单独给示范。7.15 节实测：五档的场景描写占比都低于两成，4B 与 9B 各有十轮完全没有场景句，动作描写占比却高达 77% 与 76%。内置预设的格式示例里场景段落偏短，要写成两到三句具体的环境细节，而不是一句雨声。预设改完后要复测场景占比。"
I18 = "18. 客户端加动作承接校验。7.15 节显示默认档动作承接只有 14/20，是五档最低，而它的字数与格式指标都合格，光看长度指标发现不了这个问题。装配管线在模型返回后检查回复是否包含用户本轮的关键动作词，缺失就重采样。这一道闸比复读检测更早触发，应放在前面。"
NEW3 = OLD3 + NL + I17 + NL + I18


def main() -> int:
    text = REPORT.read_text(encoding="utf-8")
    changes = [(OLD1, NEW1), (OLD2, NEW2), (OLD3, NEW3)]
    missing = []
    for old, new in changes:
        if old not in text:
            missing.append(old[:40])
            continue
        text = text.replace(old, new, 1)
    REPORT.write_text(text, encoding="utf-8")
    if missing:
        print("未找到：")
        for m in missing:
            print("  -", m)
        return 1
    print("第 8 节与第 10 节已同步")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
