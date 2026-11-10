r"""导出五组高强度场景的对话原文与场景动作拆分。

用法：
    python tools\export_explicit_doc.py
"""

from __future__ import annotations

import json
import pathlib
import sys

TOOLS_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))

from analyze_scene_action import split_parts  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = ROOT.parent / "docs" / "实践情况" / "data" / "sweep"
EXPL = DATA / "explicit_5set.json"
OUT = DATA / "explicit_5set_transcript.md"
NL = chr(10)

ORDER = [
    ("arityflow-4b_q5_km", "ArityFlow 4B Q5_K_M（现行默认档）"),
    ("qwen3_5-2b_ud", "Qwen3.5-2B-UD Q4_K_XL"),
    ("qwen3_5-4b_ud", "Qwen3.5-4B-UD Q4_K_XL"),
    ("qwen3_5-9b_ud", "Qwen3.5-9B-UD Q4_K_XL"),
    ("nqlsg-14b_q4_km", "NQLSG-Qwen2.5-14B Q4_K_M"),
]


def main() -> int:
    rows = {str(r.get("name")): r for r in json.loads(EXPL.read_text(encoding="utf-8"))}
    L = []
    L.append("# 五组高强度成人向场景：对话原文与场景动作拆分")
    L.append("")
    L.append("五组场景依次是地址P、地址T镜前、旧沙发、地址W旧床垫、地址S车内，每组四个回合逐轮推进。")
    L.append("回合要求用客户端内置预设的同一套规格，附在本轮输入末尾。温度 0.8，seed 42。")
    L.append("")
    L.append("每轮下方按引号摘出对白，其余叙述按句拆成场景描写、动作描写与无归属叙述三块，判定用确定性词表。")
    L.append("")
    for key, label in ORDER:
        row = rows.get(key)
        if not row or not row.get("scenes"):
            continue
        L.append("---")
        L.append("")
        L.append("## " + label)
        L.append("")
        for scene in row.get("scenes") or []:
            L.append("### 场景 {} {}".format(scene.get("id"), scene.get("name")))
            L.append("")
            for turn in scene.get("turns") or []:
                m = turn.get("metrics") or {}
                p = split_parts(str(turn.get("content") or ""))
                L.append("#### 第{}轮".format(turn.get("turn")))
                L.append("")
                L.append("**用户**：" + str(turn.get("user")))
                L.append("")
                L.append("**角色K**：" + str(turn.get("content")))
                L.append("")
                L.append("- 场景描写：" + ("；".join(p["scene_text"]) if p["scene_text"] else "无"))
                L.append("- 动作描写：" + ("；".join(p["action_text"]) if p["action_text"] else "无"))
                L.append("- 对白：" + ("；".join(p["dialogue_text"]) if p["dialogue_text"] else "无"))
                L.append("")
                L.append("> {} 字，场景 {:.0%}，动作 {:.0%}，对白 {:.0%}；场景句 {} 动作句 {} 对白 {} 段；TTFT {} s，解码 {} tok/s，总 {} s，提示 {} tok，生成 {} tok，拒绝 {}".format(
                    m.get("chars"), p["scene_ratio"], p["action_ratio"], p["dialogue_ratio"],
                    p["scene_sentences"], p["action_sentences"], p["dialogue_count"],
                    m.get("ttft_s"), m.get("decode_tok_per_s"), m.get("total_s"),
                    m.get("prompt_tokens"), m.get("completion_tokens"),
                    "、".join(m.get("refusal_hits") or []) or "无"))
                L.append("")
    OUT.write_text(NL.join(L), encoding="utf-8")
    print("已写入 " + str(OUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
