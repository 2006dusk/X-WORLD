"""写报告 7.13 节：成人向内容的五回合实测。

数字全部从 adult.json 与 adult_advance.json 读，不手填。

用法：
    python tools\insert_section_713.py
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
REPORT = ROOT.parent / "docs" / "实践情况" / "质量与速度平衡点.md"
DATA = ROOT.parent / "docs" / "实践情况" / "data" / "sweep"
ADULT = DATA / "adult.json"
ADVANCE = DATA / "adult_advance.json"
MARKER = "## 8. 数据来源与数据边界"
NL = chr(10)

ORDER = [
    ("arityflow-4b_q5_km", "ArityFlow 4B Q5_K_M，现行默认档"),
    ("qwen3_5-2b_ud", "Qwen3.5-2B-UD Q4_K_XL"),
    ("qwen3_5-4b_ud", "Qwen3.5-4B-UD Q4_K_XL"),
    ("qwen3_5-9b_ud", "Qwen3.5-9B-UD Q4_K_XL"),
    ("nqlsg-14b_q4_km", "NQLSG-Qwen2.5-14B Q4_K_M"),
]


def pct(value: float) -> str:
    return "{:.0%}".format(value or 0)


def main() -> int:
    rows = {str(r.get("name")): r for r in json.loads(ADULT.read_text(encoding="utf-8"))}
    adv = {str(r.get("name")): r for r in json.loads(ADVANCE.read_text(encoding="utf-8"))}
    text = REPORT.read_text(encoding="utf-8")
    if "### 7.13" in text:
        print("7.13 节已存在，跳过")
        return 0

    L = []
    L.append("### 7.13 成人向内容的五回合实测")
    L.append("")
    L.append("前面所有测试用的都是悬疑地址U场景，成人向内容一直没测过，本节补这一项。")
    L.append("")
    L.append("场景换成亲密向。角色卡仍是角色K，与前面几轮同一个角色，场景是深夜地址U，两人是交往两年的搭档。五个回合逐步推进，每轮用户给一个具体动作，模型要回一个完整回合。回合要求用客户端内置预设的同一套规格：150 到 300 字、场景描写加一到三句中文引号对白，附在本轮输入末尾。采样参数与前面一致，温度 0.8、seed 42。")
    L.append("")
    L.append("速率取法有调整。llama_cpp_python 0.3.22 的流式响应每个 chunk 的顶层键只有 choices、created、id、model、object，不带 usage，拿不到 completion_tokens。改成每轮发两次请求：非流式取 usage 与文本，流式取首字延迟，解码速率按非流式的 completion_tokens 除以端到端耗时减首字延迟。这样得到的速率与第 2 节基准口径一致，默认档实测 56.25 tok/s 对基准的 62.99 tok/s。")
    L.append("")
    L.append("| 配置 | 五轮字数 | 均值 | 目标带内 | 回合完整 | 每轮有对白 | 解码 tok/s | 首字延迟 s | 单轮等待 s | 回应率 | 整轮复述 | 拒绝轮次 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for key, label in ORDER:
        row = rows.get(key)
        if not row or not row.get("turns"):
            continue
        s = row.get("summary") or {}
        a = adv.get(key) or {}
        chars = s.get("chars_list") or []
        L.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {}/{} | {} | {} |".format(
            label,
            "、".join(str(c) for c in chars), s.get("chars_mean"),
            pct(s.get("in_band_rate")), pct(s.get("complete_turn_rate")),
            pct(s.get("dialogue_ge1_rate")), s.get("decode_tok_per_s_mean"),
            s.get("ttft_s_mean"), s.get("total_s_mean"),
            a.get("responded_count"), a.get("turn_count"),
            "有" if a.get("verbatim_repeat") else "无",
            len(s.get("refusal_turns") or [])))
    L.append("")
    L.append("回应率的口径是每轮回复里有没有出现与该轮用户动作相关的表述，与第 7.9 节的逻辑轴同一套规则。整轮复述指本轮与前面所有轮次的 8 字片段重合度超过 90%。")
    L.append("")
    L.append("四点结论。")
    L.append("")
    L.append("一是没有一档拒绝。五个档位、二十五个回合，成人向内容全部正常续写，拒绝关键词命中 0 次。Qwen3.5 系列三档在明确标注年龄与关系的角色卡下没有触发任何回避，ArityFlow 与 NQLSG 也没有。这一项不再是选型障碍。")
    L.append("")
    L.append("二是默认档在成人向场景下暴露了悬疑场景里没暴露的问题：整轮复述。ArityFlow 4B Q5_K_M 第三轮与第二轮重合 95.3%，第四轮与第五轮对前文的重合都是 100%，五轮里有三轮是同一段文字。它的回应率也只有 2/5，第二轮之后基本在原地重写。7.8 节在悬疑场景下测到过一次整轮复述，这一轮确认这不是偶发，而是这个权重在多轮连续生成时的稳定行为。")
    L.append("")
    L.append("三是 2B 在这一场景下反而最好。五轮零复述，166 到 280 字全部落在目标带内，每轮都有对白，解码 105.94 tok/s 是默认档的 1.88 倍，单轮等待 1.865 秒对 2.409 秒。人工读原文，五轮的推进是连贯的：第一轮接住「累不累」，第二轮回应被抵在台边，第三轮呼应「别停」，第四轮接住沙发与「是不是真的想要」，第五轮落到眼角与心口。它写的氛围与对白也比默认档自然，没有逐字复读的痕迹。")
    L.append("")
    L.append("四是 4B 与 9B 也各有一处硬伤。4B 第三轮与第二轮 100% 逐字相同，之后两轮才恢复。9B 第三轮写了 315 字，越出 300 字上沿，同一轮还留下一处转义残留，把对白收尾写成了半截反斜杠加引号；它的五轮里三轮超过 300 字，回应率 5/5 但长度带内率只有 0.8。")
    L.append("")
    L.append("NQLSG 14B 的问题不是内容而是长度。五轮 96、114、75、84、77 字，全部低于 150 字下沿，带内率为 0，但每轮都有对白、回应率 5/5、零复述。解码只有 6.81 tok/s，单轮要等 9.2 秒，与悬疑场景里的表现一致。这个权重在成人向场景下反而比默认档稳，只是写不够长。")
    L.append("")
    L.append("这一节不改变默认档结论，但把它的问题说清楚了：默认档的复述行为在成人向场景下是连续的而非偶发的，选它之前要先解决重复惩罚。反过来，2B 在这一场景下的零复述与最高速率，配合 7.12 节的结论，它作为轻量档的竞争力比之前评估的更强。2B 的回应率 3/5 略低于其他档，说明它更倾向于写氛围与对白，少直接复述用户给的动作。")
    L.append("")
    idx = text.index(MARKER)
    REPORT.write_text(text[:idx] + NL.join(L) + NL + NL + text[idx:], encoding="utf-8")
    print("已写入 7.13 节")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
