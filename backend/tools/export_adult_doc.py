"""把成人向五回合的对话原文整理成独立 Markdown 文档。

用法：
    python tools\export_adult_doc.py
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = ROOT.parent / "docs" / "实践情况" / "data" / "sweep"
ADULT = DATA / "adult.json"
ADVANCE = DATA / "adult_advance.json"
OUT = DATA / "adult_transcript.md"
NL = chr(10)

ORDER = [
    ("arityflow-4b_q5_km", "ArityFlow 4B Q5_K_M（现行默认档）"),
    ("qwen3_5-2b_ud", "Qwen3.5-2B-UD Q4_K_XL"),
    ("qwen3_5-4b_ud", "Qwen3.5-4B-UD Q4_K_XL"),
    ("qwen3_5-9b_ud", "Qwen3.5-9B-UD Q4_K_XL"),
    ("nqlsg-14b_q4_km", "NQLSG-Qwen2.5-14B Q4_K_M"),
]


def main() -> int:
    rows = {str(r.get("name")): r for r in json.loads(ADULT.read_text(encoding="utf-8"))}
    adv = {str(r.get("name")): r for r in json.loads(ADVANCE.read_text(encoding="utf-8"))}
    L = []
    L.append("# 成人向内容五回合实测：对话原文与速率记录")
    L.append("")
    L.append("场景为亲密向。角色卡与前面几轮同为角色K，场景是深夜地址U，两人是交往两年的搭档。")
    L.append("回合要求用客户端内置预设的同一套规格，附在本轮输入末尾。温度 0.8，seed 42。")
    L.append("")
    L.append("| 配置 | 解码 tok/s | 单轮等待 s | 目标带内 | 回应率 | 整轮复述 | 拒绝轮次 |")
    L.append("|---|---|---|---|---|---|---|")
    for key, label in ORDER:
        row = rows.get(key)
        if not row or not row.get("turns"):
            continue
        s = row.get("summary") or {}
        a = adv.get(key) or {}
        L.append("| {} | {} | {} | {:.0%} | {}/{} | {} | {} |".format(
            label, s.get("decode_tok_per_s_mean"), s.get("total_s_mean"),
            s.get("in_band_rate") or 0, a.get("responded_count"), a.get("turn_count"),
            "有" if a.get("verbatim_repeat") else "无", len(s.get("refusal_turns") or [])))
    for key, label in ORDER:
        row = rows.get(key)
        if not row or not row.get("turns"):
            continue
        L.append("")
        L.append("---")
        L.append("")
        L.append("## " + label)
        L.append("")
        s = row.get("summary") or {}
        L.append("字数 {}，均值 {}，目标带内 {:.0%}，解码 {} tok/s，首字延迟 {} s，单轮等待 {} s。".format(
            "、".join(str(c) for c in (s.get("chars_list") or [])), s.get("chars_mean"),
            s.get("in_band_rate") or 0, s.get("decode_tok_per_s_mean"),
            s.get("ttft_s_mean"), s.get("total_s_mean")))
        L.append("")
        for turn in row.get("turns") or []:
            m = turn.get("metrics") or {}
            L.append("### 第{}轮".format(turn.get("turn")))
            L.append("")
            L.append("**用户**：" + str(turn.get("user")))
            L.append("")
            L.append("**角色K**：" + str(turn.get("content")))
            L.append("")
            L.append("> {} 字，对白 {} 段，完整 {}，目标带内 {}，TTFT {} s，解码 {} tok/s，总 {} s，提示 {} tok，生成 {} tok，拒绝 {}".format(
                m.get("chars"), m.get("dialogue_count"), m.get("complete_turn"),
                m.get("in_length_band"), m.get("ttft_s"), m.get("decode_tok_per_s"),
                m.get("total_s"), m.get("prompt_tokens"), m.get("completion_tokens"),
                "、".join(m.get("refusal_hits") or []) or "无"))
            L.append("")
    OUT.write_text(NL.join(L), encoding="utf-8")
    print("已写入 " + str(OUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
