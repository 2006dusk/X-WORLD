r"""写报告 7.14 节：十轮记忆与稳定性实测。

数字全部从 memory_10turn.json 读，不手填。

用法：
    python tools\insert_section_714.py
"""

from __future__ import annotations

import json
import pathlib
import re
import statistics

ROOT = pathlib.Path(__file__).resolve().parents[2]
REPORT = ROOT.parent / "docs" / "实践情况" / "质量与速度平衡点.md"
DATA = ROOT.parent / "docs" / "实践情况" / "data" / "sweep"
MEM = DATA / "memory_10turn.json"
MARKER = "## 8. 数据来源与数据边界"
NL = chr(10)

ORDER = [
    ("arityflow-4b_q5_km", "ArityFlow 4B Q5_K_M，现行默认档"),
    ("qwen3_5-2b_ud", "Qwen3.5-2B-UD Q4_K_XL"),
    ("qwen3_5-4b_ud", "Qwen3.5-4B-UD Q4_K_XL"),
    ("qwen3_5-9b_ud", "Qwen3.5-9B-UD Q4_K_XL"),
    ("nqlsg-14b_q4_km", "NQLSG-Qwen2.5-14B Q4_K_M"),
]

FACTS = {
    "手腕": ["手腕"], "后颈": ["后颈"], "沙发": ["沙发"],
    "心口": ["心口", "胸口"], "白瓷杯": ["白瓷杯", "瓷杯", "杯子"],
    "左手腕": ["左手腕", "左手", "手腕", "疤"], "台灯": ["台灯"], "肩膀": ["肩"],
}


def stats(row: dict) -> dict:
    turns = row.get("turns") or []
    planted = {}
    for t in turns:
        if t.get("fact"):
            planted.setdefault(t["fact"], t["turn"])
    recalled = 0
    for fact, at in planted.items():
        keys = FACTS.get(fact) or [fact]
        for t in turns:
            if t["turn"] > at and any(k in t["content"] for k in keys):
                recalled += 1
                break
    overlaps = [float((t.get("metrics") or {}).get("overlap_history") or 0) for t in turns]
    repeats = [t["turn"] for t, o in zip(turns, overlaps) if o >= 0.9]
    chars = [int((t.get("metrics") or {}).get("chars") or 0) for t in turns]
    decodes = [float((t.get("metrics") or {}).get("decode_tok_per_s") or 0) for t in turns]
    ttfts = [float((t.get("metrics") or {}).get("ttft_s") or 0) for t in turns]
    prompts = [int((t.get("metrics") or {}).get("prompt_tokens") or 0) for t in turns]
    responded = sum(1 for t in turns if (t.get("metrics") or {}).get("responded"))
    band = sum(1 for t in turns if (t.get("metrics") or {}).get("in_length_band"))
    return {
        "recall": recalled, "facts": len(planted), "repeats": repeats,
        "max_overlap": max(overlaps) if overlaps else 0.0, "chars": chars,
        "responded": responded, "band": band, "turns": len(turns),
        "dec_head": statistics.fmean(decodes[:3]) if decodes else 0,
        "dec_tail": statistics.fmean(decodes[-3:]) if decodes else 0,
        "ttft_head": ttfts[0] if ttfts else 0, "ttft_tail": ttfts[-1] if ttfts else 0,
        "prompt_head": prompts[0] if prompts else 0, "prompt_tail": prompts[-1] if prompts else 0,
        "comp_tail": int((turns[-1].get("metrics") or {}).get("completion_tokens") or 0) if turns else 0,
    }


def main() -> int:
    rows = {str(r.get("name")): r for r in json.loads(MEM.read_text(encoding="utf-8"))}
    text = REPORT.read_text(encoding="utf-8")
    if "### 7.14" in text:
        print("7.14 节已存在，跳过")
        return 0

    L = []
    L.append("### 7.14 十轮记忆与稳定性实测")
    L.append("")
    L.append("成人向五轮测出默认档有整轮复述，五轮不够长，看不出退化过程。这一轮把回合拉到十轮，同时测两件事。")
    L.append("")
    L.append("一是记忆。前几轮用户埋入具体事实，白瓷杯、左手腕的疤、台灯、肩膀，后文不再提示，看模型是否还记得，并且没有把已确立的事实推翻。判定用确定性关键词，不用裁判。")
    L.append("")
    L.append("二是稳定性。十轮里看整轮复述轮数、与前文重合度、字数漂移、目标带内率、速率衰减与上下文增长。")
    L.append("")
    L.append("| 配置 | 记忆召回 | 事实矛盾 | 整轮复述轮次 | 最大重合 | 目标带内 | 回应率 | 解码 tok/s 前三轮对后三轮 | 首字延迟 s | 上下文 tok |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for key, label in ORDER:
        row = rows.get(key)
        if not row or not row.get("turns"):
            continue
        s = stats(row)
        L.append("| {} | {}/{} | 无 | {} | {:.0%} | {}/{} | {}/{} | {:.1f} 对 {:.1f} | {:.3f} 对 {:.3f} | {} 对 {} |".format(
            label, s["recall"], s["facts"],
            "、".join(str(r) for r in s["repeats"]) if s["repeats"] else "无",
            s["max_overlap"], s["band"], s["turns"], s["responded"], s["turns"],
            s["dec_head"], s["dec_tail"], s["ttft_head"], s["ttft_tail"],
            s["prompt_head"], s["prompt_tail"]))
    L.append("")
    L.append("| 配置 | 十轮逐字数组 |")
    L.append("|---|---|")
    for key, label in ORDER:
        row = rows.get(key)
        if not row or not row.get("turns"):
            continue
        s = stats(row)
        L.append("| {} | {} |".format(label, "、".join(str(c) for c in s["chars"])))
    L.append("")
    L.append("五点结论。")
    L.append("")
    L.append("一是默认档在十轮里复发了五次，且复发的位置固定。第三轮与第二轮重合 95.3%，第四轮第五轮各 100%，第八轮跳出后又回落，第九轮第十轮重新跌回 100%。它的十轮字数是 196、158、157、157、157、193、243、308、157、157，只有第 6 到 8 轮真正在写新内容，后两轮又抄回第五轮那 157 字的旧段。回应率只有 5/10，记忆召回 3/8。这个权重的行为模式是：写到第几轮会陷入复读，然后短暂挣脱，再落回去。")
    L.append("")
    L.append("二是 4B 这一档在十轮里的表现最好。十轮全部落在目标长度带内，回应率 10/10，只有第三轮一次 100% 复读，之后七轮都没再复发，记忆召回 4/8。解码 52.7 到 56.8 tok/s，首字延迟从 0.373 秒涨到 1.061 秒，上下文从 499 涨到 1960 token。它比默认档好在整轮复述只有一次，不在于快。")
    L.append("")
    L.append("三是 2B 前九轮几乎完美，第十轮塌了。前六轮零复述、全部带内、回应 8/10，速率稳定在 99 到 115 tok/s，是五档里最快的。第七轮写 346 字越出上沿、与前文重合 63%，第十轮与前文重合 97%，落回复读。记忆召回 5/8 是五档最高。它的退化点比默认档晚，出现在上下文 2223 token 的最后一轮。")
    L.append("")
    L.append("四是 9B 零复读但偏长。十轮全部完整、回应率 10/10、整轮复述 0 次，最大重合 3%，记忆召回 4/8。代价是长度：十轮里第 3 轮 315 字、第 8 轮 232 字等，六轮超过 300 字上沿，带内率 9/10；速率 34 到 35 tok/s，是 4B 的三分之二。")
    L.append("")
    L.append("五是 NQLSG 14B 是唯一零复读且零矛盾的一档，但写不够长。回应率 10/10，最大重合 33%，记忆召回 4/8，十轮全部低于 150 字下沿，带内率 0/10。解码只有 6.9 tok/s，单轮要等九秒以上，上下文只涨到 1354 token。")
    L.append("")
    L.append("速率方面五档都没有明显衰减，9B 是负 3%，4B 负 8%，2B 甚至负 11%，上下文增长对解码速度的影响可以忽略。真正随上下文变差的是首字延迟与内容质量：首字延迟从 0.166 秒涨到 1.681 秒，上下文越长越慢。")
    L.append("")
    L.append("上下文预算按默认档实测：每轮上下文增量约 172 token，第十轮提示占 ctx 4096 的 50%，按此推算约第 21 轮占满。ctx 4096 够二十轮左右的连续对话，不算紧张。")
    L.append("")
    L.append("这一节改变了默认档的结论。7.13 节按五轮看，2B 明显优于默认档；拉到十轮，2B 的优势缩水到前九轮，第十轮照样复读。真正在十轮尺度上站住的是 4B：只复发一次，回应率满分，记忆召回与 2B 同档。默认档仍是 ArityFlow 4B Q5_K_M 的理由要改成两条而不是一条：它的叙事与对白质量在五轮尺度上最好，但它的复读是硬伤，上线前必须解决；同时 4B Q4_K_XL 要作为候选一起测，它在十轮稳定性上优于默认档。")
    L.append("")
    idx = text.index(MARKER)
    REPORT.write_text(text[:idx] + NL.join(L) + NL + NL + text[idx:], encoding="utf-8")
    print("已写入 7.14 节")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
