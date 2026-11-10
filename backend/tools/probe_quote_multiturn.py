"""回合要求在多轮下的引号表现探针。

单轮下模型偶尔用英文引号，多轮里还会跟着上一轮自己的输出继续写英文引号。
本脚本对几种写法各跑两轮，第二轮带上第一轮自己的输出，比较中英文引号数量。

用法：
    python backend/tools/probe_quote_multiturn.py
"""

from __future__ import annotations

import json
import re
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
    "用中文写作。\n"
    "每次回复写一个完整回合：先写场景与环境的短描写，再写人物的动作，"
    "然后写一到三句对白，对白用中文引号，最后收在一个明确的动作或台词上。"
)

EXAMPLE = (
    "\n格式示例：雨点敲在铁皮屋顶上，声音一阵密过一阵。他把手里的磁带放在调音台上，"
    "指腹沿着外壳的划痕慢慢抹过去，确认封条没有搓过。台灯的光只照亮控制台的一角，"
    "旋钮上落着一层薄灰，看得出最近几天没人来。\n"
    "“你要是想找它的主人，”他没有抬头，“先把带子放完第二面再说。”"
    "说完他才抬起眼，目光在我脸上停了两秒，又落回那盘磁带上。"
    "窗外有辆卡车碾过积水，声音从巷口一路拖到街尾，才慢慢散掉。"
)

CARD = (
    "在地址N经营地址U的调音师，沉默僕言，观察力极强。\n"
    "沉默、克制、偶尔流露温柔\n"
    "雨夜的地址N，地址U只剩一盏台灯"
)

DIRECTIVE = (
    "本次回复是一个完整回合。全文不少于 150 字且不超过 300 字。"
    "包含场景描写、人物动作和一到三句对白，{quote_rule}"
    "写成完整一段，不分行。给出新信息或新动作推进剧情，不要重复上一段。"
    "不要写提纲、旁白总结或 OOC 说明。"
)

VARIANTS = {
    "a_current": (
        SYSTEM_BASE + EXAMPLE,
        DIRECTIVE.format(quote_rule="对白必须用中文双引号，不要用英文引号。"),
    ),
    "b_forbid_ascii": (
        SYSTEM_BASE + EXAMPLE,
        DIRECTIVE.format(quote_rule="对白一律用中文双引号“”包裹，禁止使用英文双引号。"),
    ),
    "c_forbid_ascii_demo": (
        SYSTEM_BASE + EXAMPLE + "\n本回合所有对白都要写成中文引号，例如：他说：“好。”",
        DIRECTIVE.format(quote_rule="对白一律用中文双引号“”包裹，禁止使用英文双引号。"),
    ),
}

USER_TURNS = [
    "我推开门，手里攕着一盘无人认领的磁带，把它放在调音台上。",
    "磁带开始转动，传出女人的嗯唱。我伸手去按停止键，指尖停在按钮上方。",
]


def ask(directive: str, system: str, history: list[dict[str, str]], user: str) -> tuple[str, float]:
    """发一次请求，返回正文与耗时。"""
    payload = {
        "model": "local",
        "messages": [{"role": "system", "content": system + "\n\n" + CARD}]
        + history
        + [{"role": "user", "content": user + "\n\n" + directive}],
        "max_tokens": 900,
        "temperature": 0.7,
    }
    started = time.perf_counter()
    resp = httpx.post(UPSTREAM + "/v1/chat/completions", json=payload, timeout=600.0)
    resp.raise_for_status()
    elapsed = time.perf_counter() - started
    body = resp.json()
    choice = (body.get("choices") or [{}])[0]
    return str((choice.get("message") or {}).get("content") or ""), elapsed


def main() -> int:
    """对每种写法跑两轮，第二轮带上第一轮自己的输出。"""
    rows = []
    for name, (system, directive) in VARIANTS.items():
        print("--- " + name + " ---")
        history: list[dict[str, str]] = []
        for index, user in enumerate(USER_TURNS, 1):
            text, elapsed = ask(directive, system, history, user)
            cjk = len(CJK_QUOTE.findall(text))
            ascii_q = len(ASCII_QUOTE.findall(text))
            print(
                "第 %d 轮：%d 字，中文引号 %d，英文引号 %d，等待 %.2f s"
                % (index, len(text), cjk, ascii_q, elapsed)
            )
            rows.append({"variant": name, "turn": index, "chars": len(text), "cjk": cjk, "ascii": ascii_q, "wait_s": round(elapsed, 2), "text": text})
            history = history + [
                {"role": "user", "content": user},
                {"role": "assistant", "content": text},
            ]
        print()
    out = Path(__file__).resolve().parent.parent.parent.parent / "docs" / "实践情况" / "data" / "probe_quote_multiturn.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
