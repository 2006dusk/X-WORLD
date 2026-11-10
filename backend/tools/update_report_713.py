r"""同步报告第 8 节数据边界与第 10 节后续动作，销掉成人向未覆盖项。

用法：
    python tools\update_report_713.py
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
REPORT = ROOT.parent / "docs" / "实践情况" / "质量与速度平衡点.md"
NL = chr(10)

OLD1 = "| 未覆盖 | 成人向内容表现、不同温度的方差 |"
NEW1 = ("| 已覆盖 | 成人向内容表现，7.13 节五档各五轮，拒绝 0 次；同时记录整轮复述与长度带内率 |"
        + NL +
        "| 未覆盖 | 不同温度的方差，温度固定在 0.8 |")

OLD2 = "10. 中文 RP 微调模型的主要缺陷是体裁而非规模：语料里多用括号动作流，不含引号对白。后续若要接这一类模型，先在预设里补齐对白样例，再谈换模型。"
NEW2 = (OLD2 + NL
        + "13. 默认档的整轮复述要处理。7.13 节实测：ArityFlow 4B Q5_K_M 在成人向场景五轮里有三轮与前文逐字相同，回应率只有 2/5。这是该权重的稳定行为而非偶发，要么调高重复惩罚，要么换档。"
        + NL
        + "14. 2B 的轻量档地位可以上调。7.13 节成人向五轮零复述、解码 105.94 tok/s、单轮等待 1.865 秒，五轮全部落在目标带内；对照默认档三轮逐字复述。2B 此前只因悬疑场景的一次复述被降级，这一轮证据说明它在成人向场景下更稳。挂第二档前仍需按 7.8 节放宽长度下沿。")


def main() -> int:
    text = REPORT.read_text(encoding="utf-8")
    changes = [(OLD1, NEW1), (OLD2, NEW2)]
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
