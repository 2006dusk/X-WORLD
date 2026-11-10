r"""写报告 7.15 节：五组高强度成人向场景与场景动作拆分。

数字全部从 explicit_5set.json 读，不手填。

用法：
    python tools\insert_section_715.py
"""

from __future__ import annotations

import json
import pathlib
import statistics
import sys

TOOLS_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))

from analyze_action_uptake import EXPECT  # noqa: E402
from analyze_scene_action import split_parts  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
REPORT = ROOT.parent / "docs" / "实践情况" / "质量与速度平衡点.md"
DATA = ROOT.parent / "docs" / "实践情况" / "data" / "sweep"
EXPL = DATA / "explicit_5set.json"
MARKER = "## 8. 数据来源与数据边界"
NL = chr(10)

ORDER = [
    ("arityflow-4b_q5_km", "ArityFlow 4B Q5_K_M，现行默认档"),
    ("qwen3_5-2b_ud", "Qwen3.5-2B-UD Q4_K_XL"),
    ("qwen3_5-4b_ud", "Qwen3.5-4B-UD Q4_K_XL"),
    ("qwen3_5-9b_ud", "Qwen3.5-9B-UD Q4_K_XL"),
    ("nqlsg-14b_q4_km", "NQLSG-Qwen2.5-14B Q4_K_M"),
]

GRAM = 8


def grams(text: str) -> set[str]:
    return {text[i : i + GRAM] for i in range(0, max(len(text) - GRAM, 0))}


def overlap(text: str, history: list[str]) -> float:
    if not history:
        return 0.0
    mine = grams(text)
    if not mine:
        return 0.0
    seen: set[str] = set()
    for item in history:
        seen |= grams(item)
    return round(len(mine & seen) / len(mine), 4)


def collect(row: dict) -> dict:
    parts_all = []
    uptake_total = 0
    uptake_hit = 0
    repeats = []
    scene_rows = []
    chars = []
    decodes = []
    refusals = []
    for scene in row.get("scenes") or []:
        sid = str(scene.get("id"))
        keys_list = EXPECT.get(sid) or []
        history: list[str] = []
        s_parts = []
        for turn in scene.get("turns") or []:
            text = str(turn.get("content") or "")
            m = turn.get("metrics") or {}
            p = split_parts(text)
            parts_all.append(p)
            s_parts.append(p)
            chars.append(int(m.get("chars") or 0))
            decodes.append(float(m.get("decode_tok_per_s") or 0))
            if m.get("refusal_hits"):
                refusals.append(sid + "场景第" + str(turn.get("turn")) + "轮")
            idx = int(turn.get("turn") or 0) - 1
            keys = keys_list[idx] if idx < len(keys_list) else []
            uptake_total += 1
            if keys and any(k in text for k in keys):
                uptake_hit += 1
            ov = overlap(text, history)
            if ov >= 0.9:
                repeats.append(sid + "第" + str(turn.get("turn")) + "轮")
            history.append(text)
        scene_rows.append({
            "id": sid, "name": scene.get("name"),
            "scene_ratio": statistics.fmean([p["scene_ratio"] for p in s_parts]) if s_parts else 0,
            "action_ratio": statistics.fmean([p["action_ratio"] for p in s_parts]) if s_parts else 0,
        })
    n = len(parts_all)
    return {
        "n": n,
        "scene_ratio": statistics.fmean([p["scene_ratio"] for p in parts_all]) if n else 0,
        "action_ratio": statistics.fmean([p["action_ratio"] for p in parts_all]) if n else 0,
        "dialogue_ratio": statistics.fmean([p["dialogue_ratio"] for p in parts_all]) if n else 0,
        "plain_ratio": statistics.fmean([p["plain_ratio"] for p in parts_all]) if n else 0,
        "scene_sent": statistics.fmean([p["scene_sentences"] for p in parts_all]) if n else 0,
        "action_sent": statistics.fmean([p["action_sentences"] for p in parts_all]) if n else 0,
        "no_scene": sum(1 for p in parts_all if p["scene_sentences"] == 0),
        "no_action": sum(1 for p in parts_all if p["action_sentences"] == 0),
        "uptake": uptake_hit, "uptake_total": uptake_total,
        "repeats": repeats, "chars": chars,
        "decode": statistics.fmean(decodes) if decodes else 0,
        "refusals": refusals, "scenes": scene_rows,
    }


def main() -> int:
    rows = {str(r.get("name")): r for r in json.loads(EXPL.read_text(encoding="utf-8"))}
    text = REPORT.read_text(encoding="utf-8")
    if "### 7.15" in text:
        print("7.15 节已存在，跳过")
        return 0

    L = []
    L.append("### 7.15 五组高强度场景与场景动作拆分")
    L.append("")
    L.append("7.13 与 7.14 两轮用的是同一组偏含蓄的亲密场景，模型写起来省力。这一轮换五组更露骨、动作更明确的场景：地址P、地址T镜前、旧沙发、地址W旧床垫、地址S车内，每组四个回合逐轮推进，用户每轮都给出明确的身体动作。口径与前两轮完全一致：同一套内置预设规格、回合要求附在本轮输入末尾、温度 0.8、seed 42、同一批五档权重。")
    L.append("")
    L.append("这一轮新增一个评估维度：场景描写与动作描写分开量。做法是确定性规则，不用裁判。先按引号把对白整段摘出来，再把剩下的叙述按句切分，逐句判定归属——命中环境与空间类词且不含身体动作类词的判为场景句，命中身体动作类词的判为动作句，两类都没命中的归为无归属叙述。")
    L.append("")
    L.append("| 配置 | 场景字数占比 | 动作字数占比 | 对白占比 | 无归属占比 | 每轮场景句 | 每轮动作句 | 无场景句 | 无动作句 | 动作承接 | 句式复读轮次 | 解码 tok/s | 拒绝 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for key, label in ORDER:
        row = rows.get(key)
        if not row or not row.get("scenes"):
            continue
        s = collect(row)
        L.append("| {} | {:.0%} | {:.0%} | {:.0%} | {:.0%} | {:.1f} | {:.1f} | {}/{} | {}/{} | {}/{} | {} | {:.1f} | {} |".format(
            label, s["scene_ratio"], s["action_ratio"], s["dialogue_ratio"], s["plain_ratio"],
            s["scene_sent"], s["action_sent"], s["no_scene"], s["n"], s["no_action"], s["n"],
            s["uptake"], s["uptake_total"],
            "、".join(s["repeats"]) if s["repeats"] else "无", s["decode"],
            "、".join(s["refusals"]) if s["refusals"] else "无"))
    L.append("")
    L.append("动作承接的口径是：用户这一轮给的动作关键词，是否出现在回复里。句式复读指本轮与同一场景前面轮次的重合度超过 90%。")
    L.append("")
    L.append("| 配置 | 十轮字数分布 |")
    L.append("|---|---|")
    for key, label in ORDER:
        row = rows.get(key)
        if not row or not row.get("scenes"):
            continue
        s = collect(row)
        L.append("| {} | {} |".format(label, "、".join(str(c) for c in s["chars"])))
    L.append("")
    idx = text.index(MARKER)
    REPORT.write_text(text[:idx] + NL.join(L) + NL + NL + text[idx:], encoding="utf-8")
    print("已写入 7.15 节表格")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
