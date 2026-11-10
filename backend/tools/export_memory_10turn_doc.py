r"""导出十轮记忆与稳定性对话原文。

用法：
    python tools\export_memory_10turn_doc.py
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = ROOT.parent / "docs" / "实践情况" / "data" / "sweep"
MEM = DATA / "memory_10turn.json"
OUT = DATA / "memory_10turn_transcript.md"
NL = chr(10)

ORDER = [
    ("arityflow-4b_q5_km", "ArityFlow 4B Q5_K_M（现行默认档）"),
    ("qwen3_5-2b_ud", "Qwen3.5-2B-UD Q4_K_XL"),
    ("qwen3_5-4b_ud", "Qwen3.5-4B-UD Q4_K_XL"),
    ("qwen3_5-9b_ud", "Qwen3.5-9B-UD Q4_K_XL"),
    ("nqlsg-14b_q4_km", "NQLSG-Qwen2.5-14B Q4_K_M"),
]


def main() -> int:
    rows = {str(r.get("name")): r for r in json.loads(MEM.read_text(encoding="utf-8"))}
    L = []
    L.append("# 十轮记忆与稳定性实测：对话原文与逐轮指标")
    L.append("")
    L.append("场景与角色卡与成人向五轮一致。前五轮是亲密向互动，后五轮引入新物件与身体细节，用于检验模型能否记住前面建立的事实并继续推进。")
    L.append("")
    L.append("每轮末尾附客户端内置预设的回合要求。温度 0.8，seed 42。")
    L.append("")
    for key, label in ORDER:
        row = rows.get(key)
        if not row or not row.get("turns"):
            continue
        L.append("---")
        L.append("")
        L.append("## " + label)
        L.append("")
        for turn in row.get("turns") or []:
            m = turn.get("metrics") or {}
            L.append("### 第{}轮".format(turn.get("turn")))
            L.append("")
            L.append("**用户**：" + str(turn.get("user")))
            L.append("")
            L.append("**角色K**：" + str(turn.get("content")))
            L.append("")
            L.append("> {} 字，对白 {} 段，完整 {}，目标带内 {}，与前文重合 {:.0%}，回应 {}，TTFT {} s，解码 {} tok/s，总 {} s，提示 {} tok，生成 {} tok".format(
                m.get("chars"), m.get("dialogue_count"), m.get("complete_turn"),
                m.get("in_length_band"), float(m.get("overlap_history") or 0),
                "是" if m.get("responded") else "否", m.get("ttft_s"), m.get("decode_tok_per_s"),
                m.get("total_s"), m.get("prompt_tokens"), m.get("completion_tokens")))
            L.append("")
    OUT.write_text(NL.join(L), encoding="utf-8")
    print("已写入 " + str(OUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
