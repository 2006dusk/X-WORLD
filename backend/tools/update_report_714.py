r"""同步报告第 8 节与第 10 节：十轮记忆与稳定性结果入档。

用法：
    python tools\update_report_714.py
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
REPORT = ROOT.parent / "docs" / "实践情况" / "质量与速度平衡点.md"
NL = chr(10)

OLD1 = "| 已覆盖 | 成人向内容表现，7.13 节五档各五轮，拒绝 0 次；同时记录整轮复述与长度带内率 |"
NEW1 = (OLD1 + NL
        + "| 已覆盖 | 十轮记忆与稳定性，7.14 节五档各十轮，事实召回、事实矛盾、整轮复述、字数漂移与上下文预算 |")

OLD2 = "| 未覆盖 | 2B 的客户端链路复测只有三轮，每轮四个回合，样本量小；长度下沿与复述的频率尚未量化到可下结论的程度 |"
NEW2 = "| 已覆盖 | 2B 的客户端链路复测三轮共十二回合，加上 7.13 节五轮与 7.14 节十轮，共十七个成人向回合；长度下沿与复述的频率已量化 |"

OLD3 = "14. 2B 的轻量档地位可以上调。7.13 节成人向五轮零复述、解码 105.94 tok/s、单轮等待 1.865 秒，五轮全部落在目标带内；对照默认档三轮逐字复述。2B 此前只因悬疑场景的一次复述被降级，这一轮证据说明它在成人向场景下更稳。挂第二档前仍需按 7.8 节放宽长度下沿。"
ITEM15 = "15. 4B Q4_K_XL 要补进候选。7.14 节十轮实测：Qwen3.5-4B-UD 十轮全部落在目标带内，回应率 10/10，整轮复述只有第三轮一次，之后七轮未复发，记忆召回 4/8。默认档 ArityFlow 4B Q5_K_M 在同一场景复发五次、回应率 5/10。两者同体积同族，可直接对比。"
ITEM16 = "16. 长会话的复读阈值要定。7.14 节显示各档退化点不同：默认档第三轮开始复读，2B 到第十轮才塌，4B 只第三轮一次。客户端应加一条规则：检测到与前一轮重合超过 90% 时重采样或降低温度，而不是让复读一路延续。"
NEW3 = OLD3 + NL + ITEM15 + NL + ITEM16


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
