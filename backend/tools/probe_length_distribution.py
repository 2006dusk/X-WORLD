r"""直接采样模型的自然输出长度分布，不经过后端闸门。

用于验证假设：长度闸门重写是否有效。
后端 chat.py 的 _retry_params 只换 seed 与温度，同参数下输出长度分布不变，
所以「不足下限就丢弃重写」救不回来，却让耗时翻倍、界面闪一下。

做法：直连推理服务 http://127.0.0.1:8080/v1/chat/completions，
用与后端相同的提示（system 段 + 回合要求 + 状态提示），连续采样 N 次，
统计每次的正文总字数（场景块 + 对白块）。

用法：
    python backend\tools\probe_length_distribution.py [次数]
输出：
    docs\实践情况\data\sweep\length_distribution.json
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402

from app import presets  # noqa: E402
from app.inference.structure import STRUCTURE_BLOCKS, split_blocks  # noqa: E402
from local_paths import model_file  # noqa: E402


INFERENCE = os.environ.get("XW_INFERENCE", "http://127.0.0.1:8080")
MODEL = os.environ.get(
    "XW_MODEL",
    model_file("hf/ArityFlow-Qwen3-4B-Instruct-2507-RolePlay.Q5_K_M.gguf"),
)
STRUCTURE = ["scene", "dialogue", "state"]

# 与后端一致的提示拼法：system 提示组 + 回合要求 + 状态提示。
PROMPTS = [
    "外面雨还没停，你不去看看吗？",
    "我们去看看吧，顺便找个地方坐坐。",
    "前面有家地址V，我们进去吧。",
    "点单，看看喝什么。",
    "看菜单。",
    "你刚才说的那首老歌，叫什么名字？",
]

STATE_HINT = (
    "当前状态（上一轮结束时的事实，场景块必须与它一致，不要写人物已经在别处的画面）："
    + chr(10)
    + "当前位置：地址N_地址V"
    + chr(10)
    + "人物位置：靠在吧台后"
    + chr(10)
    + "装备物品：防水外套、钱包、耳机线头"
    + chr(10)
    + "地图：地址N_地址V"
    + chr(10)
    + "时间：04:30"
)


def build_messages(prompt: str) -> list[dict]:
    """拼出与后端一致的消息序列。"""
    system = presets.BUILTIN_SYSTEM_PROMPT
    directive = presets.render_output_spec(presets.BUILTIN_OUTPUT_SPEC)
    user = prompt
    if directive:
        user = user + chr(10) + chr(10) + directive
    user = user + chr(10) + chr(10) + STATE_HINT
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def sample(client: httpx.Client, prompt: str, seed: int) -> dict:
    """采一次，记录正文块字数与总耗时。"""
    payload = {
        "model": MODEL,
        "messages": build_messages(prompt),
        "stream": False,
        "temperature": 0.7,
        "top_p": 0.9,
        "max_tokens": 768,
        "repetition_penalty": 1.1,
        "seed": seed,
    }
    started = time.time()
    resp = client.post(INFERENCE + "/v1/chat/completions", json=payload, timeout=300.0)
    resp.raise_for_status()
    elapsed = time.time() - started
    data = resp.json()
    content = data["choices"][0]["message"].get("content") or ""
    blocks = split_blocks(content, STRUCTURE)
    lengths = {name: len(blocks.get(name, "")) for name in STRUCTURE}
    body = lengths["scene"] + lengths["dialogue"]
    return {
        "prompt": prompt,
        "seed": seed,
        "blocks": lengths,
        "body_chars": body,
        "in_band": 150 <= body <= 300,
        "state_parsed": bool(
            re.search(r"(当前位置|人物位置|装备物品|地图|时间)\s*[：:]", blocks.get("state", ""))
        ),
        "elapsed": round(elapsed, 2),
        "raw": content,
    }


def main() -> int:
    """采样并落盘。"""
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    samples = []
    with httpx.Client() as client:
        for index in range(rounds):
            for turn, prompt in enumerate(PROMPTS):
                row = sample(client, prompt, 20261106 + index * 10 + turn)
                samples.append(row)
                print(
                    "seed=%d 字数=%3d 带内=%s 块=%s %.2fs"
                    % (
                        row["seed"],
                        row["body_chars"],
                        row["in_band"],
                        row["blocks"],
                        row["elapsed"],
                    )
                )
    bodies = [row["body_chars"] for row in samples]
    summary = {
        "samples": len(samples),
        "body_min": min(bodies),
        "body_max": max(bodies),
        "body_mean": round(sum(bodies) / len(bodies), 1),
        "body_median": sorted(bodies)[len(bodies) // 2],
        "in_band_count": sum(1 for row in samples if row["in_band"]),
        "in_band_rate": round(
            sum(1 for row in samples if row["in_band"]) / len(samples), 3
        ),
        "under_150": sum(1 for body in bodies if body < 150),
        "elapsed_mean": round(
            sum(row["elapsed"] for row in samples) / len(samples), 2
        ),
    }
    print()
    print(json.dumps(summary, ensure_ascii=False))

    out = ROOT.parent / "docs" / "实践情况" / "data" / "sweep" / "length_distribution.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"summary": summary, "samples": samples}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("written " + str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
