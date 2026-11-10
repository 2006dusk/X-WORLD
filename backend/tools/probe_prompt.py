"""提示词形态对单回合输出长度的影响探针。

同一模型、同一样例场景，只换提示词形态，看各形态能拿到多少字的完整回合。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))

from bench_local import HOST, free_port  # noqa: E402
from wwc_server import start_server, stop_server, wait_ready  # noqa: E402

MODEL = r"E:\X-WORLD\models\hf\qwen2.5-14b-roleplay-zh-q4_k_m.gguf"
ARGS = ["--ctx-size", "8192", "--n-gpu-layers", "36", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0"]

ROLE = "你扮演角色K，在地址N经营地址U的调音师，沉默、观察力强。回复保持角色一致，不解释设定，不提及自己是 AI。用中文写作。"

SCENE = (
    "【场景】深夜，地址U只剩一盏台灯，雨点敲在铁皮屋顶上。\n"
    "【细节】控制台还亮着，桌角压着一张手写便签，上面是一个陌生名字。\n"
    "【你的行动】我推开门，手里攥着一盘无人认领的磁带，走到控制台前，把它放在调音台上。"
)

VARIANTS = [
    (
        "A 当前形态",
        ROLE + "\n本次回复必须是一个完整回合：先写场景与环境的短描写，再写人物的动作，然后写一到三句对白，对白用中文引号，最后收在一个明确的动作或台词上。全文约 300 至 500 字，不要写提纲，不要重复上一段。",
        SCENE,
    ),
    (
        "B 长度要求放在用户消息",
        ROLE,
        SCENE
        + "\n请把这个场景写成一个完整回合：先写环境与场景描写，再写角色K的动作，然后写至少三句中文引号对白，全文不少于 300 字，写到场景自然推进再收尾。",
    ),
    (
        "C 先给设定再要回合",
        ROLE
        + "\n你正在和对方共处一间深夜的地址U，对方刚把一盘无人认领的磁带放到调音台上。",
        "请续写这个场景。先写场景描写，再写动作与至少三句对白，全文不少于 300 字。",
    ),
]


def main() -> int:
    port = free_port()
    base = f"http://{HOST}:{port}"
    proc, log_path, handle = start_server(MODEL, ARGS, port)
    try:
        if not wait_ready(base, proc):
            print("服务启动超时")
            return 1
        time.sleep(1.0)
        for label, system, user in VARIANTS:
            resp = httpx.post(
                f"{base}/v1/chat/completions",
                json={
                    "model": "local",
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "max_tokens": 1200,
                    "temperature": 0.8,
                    "seed": 42,
                },
                timeout=1800.0,
            )
            resp.raise_for_status()
            body = resp.json()
            choice = (body.get("choices") or [{}])[0]
            text = str(choice.get("message", {}).get("content") or "")
            print(f"---- {label} : {len(text)} 字, finish={choice.get('finish_reason')}")
            print(text[:600].replace(chr(10), " / "))
            print()
    finally:
        stop_server(proc, log_path, handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
