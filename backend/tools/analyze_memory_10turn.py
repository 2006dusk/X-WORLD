r"""分析十轮记忆与稳定性结果。

四项指标：

1. 记忆召回：事实埋入后，后续轮次里是否还出现对应词；
2. 事实矛盾：模型是否推翻已确立的物件或身体部位；
3. 稳定性：整轮复述轮数、与前文最大重合、字数漂移；
4. 速率衰减：上下文增长后解码速率与首字延迟的变化。

用法：
    python tools\analyze_memory_10turn.py
"""

from __future__ import annotations

import json
import pathlib
import re
import statistics

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = ROOT.parent / "docs" / "实践情况" / "data" / "sweep"
MEM = DATA / "memory_10turn.json"

# 事实关键词与召回检验词。
FACTS = {
    "手腕": ["手腕"],
    "后颈": ["后颈"],
    "沙发": ["沙发"],
    "心口": ["心口", "胸口"],
    "白瓷杯": ["白瓷杯", "瓷杯", "杯子"],
    "左手腕": ["左手腕", "左手", "手腕", "疤"],
    "台灯": ["台灯"],
    "肩膀": ["肩"],
}

CONTRADICTIONS = [
    ("白瓷杯", r"没有杯子|杯子不见了|换了个杯子|不是白瓷杯", "推翻白瓷杯"),
    ("左手腕", r"右手腕|是右手|左手没有疤|没有疤", "推翻左手腕的疤"),
    ("台灯", r"台灯坏了|没有台灯|换了个灯", "推翻台灯"),
    ("沙发", r"没有沙发|沙发不见了|换了个沙发", "推翻沙发"),
]


def main() -> int:
    rows = json.loads(MEM.read_text(encoding="utf-8"))
    print("== 十轮记忆与稳定性 ==")
    for row in rows:
        turns = row.get("turns") or []
        if not turns:
            continue
        name = str(row.get("name"))
        print("==", name, row.get("params"), row.get("quant"))

        # 记忆召回
        planted = {}
        for t in turns:
            fact = t.get("fact")
            if fact:
                planted.setdefault(fact, t["turn"])
        recalled = []
        missed = []
        for fact, at in planted.items():
            keys = FACTS.get(fact) or [fact]
            hit_turns = []
            for t in turns:
                if t["turn"] <= at:
                    continue
                if any(k in t["content"] for k in keys):
                    hit_turns.append(t["turn"])
            if hit_turns:
                recalled.append((fact, at, hit_turns))
            else:
                missed.append((fact, at))
        print("  记忆召回 {}/{}".format(len(recalled), len(planted)))
        for fact, at, hits in recalled:
            print("    第{}轮埋的「{}」在第{}轮被提起".format(at, fact, hits))
        for fact, at in missed:
            print("    第{}轮埋的「{}」后文未再出现".format(at, fact))

        # 事实矛盾
        contradictions = []
        for fact, pattern, label in CONTRADICTIONS:
            at = planted.get(fact)
            if not at:
                continue
            for t in turns:
                if t["turn"] <= at:
                    continue
                if re.search(pattern, t["content"]):
                    contradictions.append("第{}轮：{}".format(t["turn"], label))
        print("  事实矛盾 {}".format("、".join(contradictions) if contradictions else "无"))

        # 稳定性
        overlaps = [float((t.get("metrics") or {}).get("overlap_history") or 0) for t in turns]
        repeats = [t["turn"] for t, o in zip(turns, overlaps) if o >= 0.9]
        chars = [int((t.get("metrics") or {}).get("chars") or 0) for t in turns]
        band = [t["turn"] for t in turns if (t.get("metrics") or {}).get("in_length_band")]
        responded = [t["turn"] for t in turns if (t.get("metrics") or {}).get("responded")]
        decodes = [float((t.get("metrics") or {}).get("decode_tok_per_s") or 0) for t in turns]
        ttfts = [float((t.get("metrics") or {}).get("ttft_s") or 0) for t in turns]
        prompts = [int((t.get("metrics") or {}).get("prompt_tokens") or 0) for t in turns]
        head = statistics.fmean(decodes[:3]) if decodes else 0
        tail = statistics.fmean(decodes[-3:]) if decodes else 0
        print("  字数 {}".format(chars))
        print("  目标带内轮次 {}  回应轮次 {}/{}".format(band, len(responded), len(turns)))
        print("  整轮复述轮次 {}（{}）  最大重合 {:.0%}".format(
            repeats, "、".join(str(r) for r in repeats) if repeats else "无", max(overlaps) if overlaps else 0))
        print("  上下文 {} -> {} tok  解码 {:.1f} -> {:.1f} tok/s（衰减 {:.0%}）  首字 {:.3f} -> {:.3f} s".format(
            prompts[0] if prompts else 0, prompts[-1] if prompts else 0, head, tail,
            ((head - tail) / head) if head else 0, ttfts[0] if ttfts else 0, ttfts[-1] if ttfts else 0))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
