r"""汇总预设示例写法对照实验的结果。

读取 docs/实践情况/data/sweep/preset_ab.json，逐变体统计：
长度带内率、对白与引号样式、段落结构、跨轮复读、场景描写占比、耗时。

用法：
    python backend\tools\analyze_preset_ab.py
"""

from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "..", "docs", "实践情况", "data", "sweep", "preset_ab.json")
DST = os.path.join(ROOT, "..", "docs", "实践情况", "data", "sweep", "preset_ab_summary.md")

ASCII_QUOTE = chr(34)
CJK_OPEN = chr(8220)
CJK_CLOSE = chr(8221)
BLANK_LINE = chr(10) + chr(10)


def main() -> int:
    with open(SRC, encoding="utf-8") as handle:
        data = json.load(handle)

    lines = [
        "| 变体 | 带内 | 有对白 | 中文引号 | 英文引号 | 含空行 | 不同回复 | 复读轮 | 场景占比 | 字数 | 均耗时 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for name, variant in data["variants"].items():
        rows = variant["rows"]
        summary = variant["summary"]
        cjk_turns = sum(
            1 for row in rows if CJK_OPEN in row["text"] and CJK_CLOSE in row["text"]
        )
        ascii_turns = sum(1 for row in rows if ASCII_QUOTE in row["text"])
        blank_turns = sum(1 for row in rows if BLANK_LINE in row["text"])
        lines.append(
            "| {name} | {band}/{turns} | {dlg} | {cjk} | {asc} | {blank} | {dist} | {rep} | {scene} | {cmin}-{cmax} | {cost} |".format(
                name=name,
                band=summary["in_band"],
                turns=summary["turns"],
                dlg=summary["with_dialogue"],
                cjk=str(cjk_turns) + "/" + str(summary["turns"]),
                asc=str(ascii_turns) + "/" + str(summary["turns"]),
                blank=str(blank_turns) + "/" + str(summary["turns"]),
                dist=str(summary["distinct_replies"]),
                rep=str(summary["repeat_turns"]),
                scene=str(summary["scene_ratio_avg"]),
                cmin=summary["chars_min"],
                cmax=summary["chars_max"],
                cost=str(summary["elapsed_avg"]) + "s",
            )
        )

    lines.append("")
    lines.append("指标口径：带内=正文字数落在 150-300；有对白=中文引号至少一对；")
    lines.append("不同回复=本变体各轮正文去重后的种数；复读轮=与本变体此前某轮相似度不低于 0.90；")
    lines.append("场景占比=不含引号的句子数占总句数。")
    lines.append("")

    for name, variant in data["variants"].items():
        lines.append("## " + name)
        lines.append("")
        for row in variant["rows"]:
            lines.append(
                "- T{turn} 字数={chars} 带内={band} 引号={q} 复读={rep} | {text}".format(
                    turn=row["turn"],
                    chars=row["chars"],
                    band=row["in_band"],
                    q=row["quotes"],
                    rep=row["repeat_ratio"],
                    text=row["text"].replace(chr(10), " / "),
                )
            )
        lines.append("")

    with open(DST, "w", encoding="utf-8") as handle:
        handle.write(chr(10).join(lines))

    print(chr(10).join(lines[:6]))
    print("已写入 " + DST)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
