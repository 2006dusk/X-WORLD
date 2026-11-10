"""回合要求写法探针。

实测发现：回合要求只写「对白用中文引号」时，模型仍会用英文引号。
本脚本对同一段场景尝试几种写法，比较中英文引号与字数，供内置预设定稿。

用法：
    python backend/tools/probe_directive.py
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import httpx

UPSTREAM = "http://127.0.0.1:8080"

CJK_QUOTE = re.compile(r"[“「『]")
ASCII_QUOTE = re.compile(r'"')

SYSTEM_BASE = (
    "你扮演角色卡中的角色，以该角色的第一人称身份回应。\n"
    "保持角色一致：只写该角色能知道的事，不解释设定，不提自己是 AI，不写 OOC 或元叙述。\n"
    "优先推进剧情：每次回复给出新的信息、动作或反应，让场景往前走，不原地重复。\n"
    "不替对方行动，不复述对方的输入。\n"
    "用中文写作。"
)

EXAMPLE = (
    "\n格式示例：雨点敲在铁皮屋顶上。他把手里的磁带放在调音台上，"
    "抬头看我：“这条街的尽头确实有家店。”"
)

CARD = (
    "在地址N经营地址U的调音师，沉默寡言，观察力极强。\n"
    "沉默、克制、偶尔流露温柔\n"
    "雨夜的地址N，地址U只剩一盏台灯"
)

VARIANTS: dict[str, tuple[str, str]] = {
    "v1_当前": (
        SYSTEM_BASE,
        "本次回复是一个完整回合。全文控制在 150 到 300 字之间。"
        "包含场景描写、人物动作和一到三句中文引号对白。写成完整一段，不分行。"
        "给出新信息或新动作推进剧情，不要重复上一段。不要写提纲、旁白总结或 OOC 说明。",
    ),
    "v2_禁用英文引号": (
        SYSTEM_BASE,
        "本次回复是一个完整回合。全文控制在 150 到 300 字之间。"
        "包含场景描写、人物动作和一到三句对白，对白必须用中文双引号，不要用英文引号。"
        "写成完整一段，不分行。给出新信息或新动作推进剧情，不要重复上一段。"
        "不要写提纲、旁白总结或 OOC 说明。",
    ),
    "v3_带格式示例": (
        SYSTEM_BASE + EXAMPLE,
        "本次回复是一个完整回合。全文控制在 150 到 300 字之间。"
        "包含场景描写、人物动作和一到三句对白，对白必须用中文双引号，不要用英文引号。"
        "写成完整一段，不分行。给出新信息或新动作推进剧情，不要重复上一段。"
        "不要写提纲、旁白总结或 OOC 说明。",
    ),
}

USER = "你还在地址U吗？"


def main() -> int:
    """对每种写法生成一次，报告中文字数与引号样式。"""
    for name, (system, directive) in VARIANTS.items():
        payload = {
            "model": "local",
            "messages": [
                {"role": "system", "content": f"{system}\n\n{CARD}"},
                {"role": "user", "content": f"{USER}\n\n{directive}"},
            ],
            "max_tokens": 900,
            "temperature": 0.8,
            "seed": 42,
        }
        started = time.perf_counter()
        resp = httpx.post(f"{UPSTREAM}/v1/chat/completions", json=payload, timeout=600.0)
        resp.raise_for_status()
        elapsed = time.perf_counter() - started
        body = resp.json()
        choice = (body.get("choices") or [{}])[0]
        text = str((choice.get("message") or {}).get("content") or "")
        cjk = len(CJK_QUOTE.findall(text))
        ascii_q = len(ASCII_QUOTE.findall(text))
        print(f"--- {name} ---")
        print(f"字数 {len(text)}，中文引号 {cjk}，英文引号 {ascii_q}，等待 {elapsed:.2f} s")
        print(text[:400])
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
