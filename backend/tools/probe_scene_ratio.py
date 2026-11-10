r"""场景块频率 A/B 探针。

问题：普通回合里模型几乎每轮都写一段环境，场景与对白一比一。
用户要的是：只有明显换场景，或偶尔带一句描述时才写场景。

做法：绕过后端闸门，直连推理服务，对两种提示写法各跑同样几轮，
统计场景块的出现比例与字数。变体只改 output_spec.structure_prompt
（提示里广告哪几块），其余提示、采样参数、角色卡与输入逐字相同。

结论（2026-11-06 实测，六轮一组）：
  advertise_scene  提示里广告场景块：六轮里多轮写出整段环境。
  dialogue_only    提示里只讲对白与状态：六轮全部只有对白与状态。

跑法：python backend\tools\probe_scene_ratio.py
结果只打印到终端，不落新的持久化数据表。
"""

from __future__ import annotations

import json
import os
import sys
import time

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

import httpx  # noqa: E402

from app import presets as preset_service  # noqa: E402
from app.inference.structure import split_blocks  # noqa: E402

INFER = os.environ.get("XW_INFER", "http://127.0.0.1:8080")
STRUCTURE = ["dialogue", "scene", "state"]

# 与 backend/app/routers/chat.py 的 _build_params 一致。
SAMPLING = {
    "temperature": 0.7,
    "top_p": 0.9,
    "max_tokens": 768,
    "repeat_penalty": 1.1,
    "top_k": 40,
    "min_p": 0.05,
}

CARD = (
    "沈砚，二十八岁，市图书馆的夜班管理员。闭馆后独自守在大厅的值班桌前，"
    "桌上有一盏台灯与一台旧收音机，窗外是雨夜的停车场。"
)

PROMPTS = [
    "外面还在下雨。",
    "你手边那本书是什么？",
    "再等等，我还没说完。",
    "你还记得上次我来的时候吗？",
    "灯是不是该换了？",
    "门响了。",
]

# 变体：提示里广告哪几块。
VARIANTS = {
    "advertise_scene": ["scene", "dialogue", "state"],
    "dialogue_only": ["dialogue", "state"],
}


def requirement(variant: str) -> str:
    """按变体渲染回合要求。"""
    spec = dict(preset_service.BUILTIN_OUTPUT_SPEC)
    spec["structure_prompt"] = list(VARIANTS[variant])
    return preset_service.render_output_spec(spec)


def build_messages(variant: str, prompt: str) -> list[dict]:
    """构造一轮的消息序列：system 是角色契约，user 是输入加回合要求。"""
    system = preset_service.BUILTIN_SYSTEM_PROMPT + "\n\n角色：\n" + CARD
    user = prompt + "\n\n" + requirement(variant)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def sample(client: httpx.Client, variant: str, prompt: str) -> dict:
    """跑一轮，返回拆块后的字数。"""
    payload = dict(SAMPLING)
    payload["messages"] = build_messages(variant, prompt)
    payload["stream"] = False
    tick = time.time()
    response = client.post(INFER + "/v1/chat/completions", json=payload)
    elapsed = round(time.time() - tick, 2)
    if response.status_code != 200:
        raise RuntimeError("inference %d: %s" % (response.status_code, response.text[:200]))
    choice = (response.json().get("choices") or [{}])[0]
    text = str(choice.get("message", {}).get("content") or "")
    blocks = split_blocks(text, STRUCTURE)
    scene = str(blocks.get("scene") or "")
    dialogue = str(blocks.get("dialogue") or "")
    return {
        "scene_chars": len(scene),
        "dialogue_chars": len(dialogue),
        "state_chars": len(str(blocks.get("state") or "")),
        "filled": sorted(name for name, value in blocks.items() if str(value).strip()),
        "elapsed": elapsed,
        "scene_head": scene[:40].replace("\n", " "),
    }


def main() -> int:
    """按变体各跑同样几轮，给出场景块频率与字数。"""
    report: dict[str, object] = {"variants": {}}
    with httpx.Client(timeout=300.0) as client:
        for variant in VARIANTS:
            rows = [sample(client, variant, prompt) for prompt in PROMPTS]
            scene_rows = [row for row in rows if row["scene_chars"] > 0]
            report["variants"][variant] = {
                "scene_ratio": round(len(scene_rows) / max(1, len(rows)), 2),
                "scene_chars": [row["scene_chars"] for row in rows],
                "dialogue_chars": [row["dialogue_chars"] for row in rows],
                "no_dialogue_rows": sum(1 for row in rows if row["dialogue_chars"] == 0),
                "filled": [row["filled"] for row in rows],
                "scene_heads": [row["scene_head"] for row in scene_rows],
                "elapsed_avg": round(sum(row["elapsed"] for row in rows) / max(1, len(rows)), 2),
            }
            print(variant, json.dumps(report["variants"][variant], ensure_ascii=True))
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
