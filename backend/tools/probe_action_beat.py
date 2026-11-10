r"""动作节拍写法 A/B 探针。

问题：动作块加回来之后，模型仍然把它写成一段景物（实测平均 79 字，
八轮里五轮混进「雨声」「窗外」「夜色」），回合又退成描述而不是互动。

做法：绕过后端闸门，直连推理服务，对几种动作提示写法各跑同样几轮，
量动作块字数与其中的环境词。变体只改动作块的写法提示与系统提示里
对应的那一句，其余提示、采样参数、角色卡与输入逐字相同。

跑法：python backend\tools\probe_action_beat.py
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
from app.inference import structure as structure_mod  # noqa: E402
from app.inference.structure import split_blocks  # noqa: E402

INFER = os.environ.get("XW_INFER", "http://127.0.0.1:8080")
STRUCTURE = ["action", "dialogue", "state"]

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

ENV_WORDS = (
    "光线",
    "灯光",
    "月色",
    "夜色",
    "风声",
    "雨声",
    "气息",
    "空气",
    "窗外",
    "昏暗",
    "明亮",
    "夕阳",
    "晨光",
    "烛",
    "色调",
    "气味",
)

SECOND_PERSON = ("你", "您", "尔", "咱")

CURRENT_HINT = (
    "写一到两句角色本人此刻的动作或反应：他做了什么、什么姿态、什么反应。"
    "用第三人称写角色本人，不要出现「你」或「我」；"
    "不要写对方的动作、心理或台词，也不要在这里写任何引号里的台词；"
    "承接用户这一轮做的事，写角色对它的反应；"
    "不要重复上一轮已经做过的动作；"
    "只写角色身上发生的事，一两句就够，不要写成长段。"
)

CURRENT_TAIL = "动作只写角色身上发生的事，一两句就够，不要写成长段。"

# 变体：动作块的写法提示，与系统提示里对应的那一句。
VARIANTS = {
    "v1_current": {
        "tail": CURRENT_TAIL,
        "hint": CURRENT_HINT,
    },
    "v2_budget": {
        "tail": "动作只写角色身上发生的事，一两句就够，合计不超过 40 字。",
        "hint": (
            "写一到两句角色本人此刻的动作或反应：他做了什么、什么姿态、什么反应。"
            "合计不超过 40 字。"
            "用第三人称写角色本人，不要出现「你」或「我」；"
            "不要写对方的动作、心理或台词，也不要在这里写任何引号里的台词；"
            "承接用户这一轮做的事，写角色对它的反应；"
            "不要重复上一轮已经做过的动作。"
        ),
    },
    "v3_body_first": {
        "tail": "动作只写角色身上发生的事，一到两句，合计不超过 40 字。",
        "hint": (
            "只写角色身上发生的事：他动了什么、是什么姿态、什么反应。"
            "写一到两句，合计不超过 40 字。"
            "用第三人称写角色本人，不要出现「你」或「我」；"
            "不要写对方的动作、心理或台词，也不要在这里写任何引号里的台词；"
            "承接用户这一轮做的事，写角色对它的反应；"
            "不要重复上一轮已经做过的动作。"
        ),
    },
    "v4_env_ban": {
        "tail": "动作只写角色身上发生的事，一两句就够，合计不超过 40 字。",
        "hint": (
            "写一到两句角色本人此刻的动作或反应：他做了什么、什么姿态、什么反应。"
            "合计不超过 40 字。"
            "不要写光线、雨声、窗外、夜色这类环境词。"
            "用第三人称写角色本人，不要出现「你」或「我」；"
            "不要写对方的动作、心理或台词，也不要在这里写任何引号里的台词；"
            "承接用户这一轮做的事，写角色对它的反应；"
            "不要重复上一轮已经做过的动作。"
        ),
    },
    "v5_one_sentence": {
        "tail": "动作只写角色身上发生的事，一句就够，不超过 25 字。",
        "hint": (
            "写角色此刻的一个动作或反应，一句就够，不超过 25 字。"
            "用第三人称写角色本人，不要出现「你」或「我」；"
            "不要写对方的动作、心理或台词，也不要在这里写任何引号里的台词；"
            "承接用户这一轮做的事，写角色对它的反应。"
        ),
    },
    # 第二轮：v4 的环境词禁令有效（环境词 0/6），但回合被压到 96 字。
    # 这三个变体把对白长度补回来，看回合总长能不能回到 150 字以上。
    "v6_combo": {
        "tail": "动作只写角色身上发生的事，一到两句，合计不超过 40 字。",
        "hint": (
            "写一到两句角色本人此刻的动作或反应：他做了什么、什么姿态、什么反应。"
            "合计不超过 40 字。"
            "不要写光线、雨声、窗外、夜色这类环境词。"
            "用第三人称写角色本人，不要出现「你」或「我」；"
            "不要写对方的动作、心理或台词，也不要在这里写任何引号里的台词；"
            "承接用户这一轮做的事，写角色对它的反应；"
            "不要重复上一轮已经做过的动作。"
        ),
    },
    "v7_combo_dialogue80": {
        "tail": "动作只写角色身上发生的事，一到两句，合计不超过 40 字。",
        "hint": (
            "写一到两句角色本人此刻的动作或反应：他做了什么、什么姿态、什么反应。"
            "合计不超过 40 字。"
            "不要写光线、雨声、窗外、夜色这类环境词。"
            "用第三人称写角色本人，不要出现「你」或「我」；"
            "不要写对方的动作、心理或台词，也不要在这里写任何引号里的台词；"
            "承接用户这一轮做的事，写角色对它的反应；"
            "不要重复上一轮已经做过的动作。"
        ),
        "dialogue_hint": (
            "只写角色自己说出口的台词，可以先开口；"
            "用中文双引号不要用英文引号；"
            "台词里可以用「我」；"
            "不要替对方说话或回答，也不要给对方的动作配音；"
            "不要在这里写状态行，状态只写在【状态】块里；"
            "对白必须直接回应用户这一轮说的话或动作，至少写两句，合计不少于 80 字；"
            "台词是对用户说的，像在跟对方搭话，不要自言自语，也不要只写旁观式的感慨"
        ),
    },
    "v11_one_short": {
        "tail": "动作只写角色身上发生的事，一句话，最多 25 字。",
        "hint": (
            "写角色此刻的一个动作或反应，一句话，最多 25 字。"
            "不要写光线、雨声、窗外、夜色这类环境词。"
            "用第三人称写角色本人，不要出现「你」或「我」；"
            "不要写对方的动作、心理或台词，也不要在这里写任何引号里的台词；"
            "承接用户这一轮做的事，写角色对它的反应。"
        ),
        "dialogue_hint": (
            "只写角色自己说出口的台词，可以先开口；"
            "用中文双引号不要用英文引号；"
            "台词里可以用「我」；"
            "不要替对方说话或回答，也不要给对方的动作配音；"
            "不要在这里写状态行，状态只写在【状态】块里；"
            "对白必须直接回应用户这一轮说的话或动作，至少写两句，合计不少于 100 字；"
            "台词是对用户说的，像在跟对方搭话，不要自言自语，也不要只写旁观式的感慨"
        ),
    },
    "v12_budget40_dialogue120": {
        "tail": "动作只写角色身上发生的事，一到两句，合计不超过 40 字。",
        "hint": (
            "写一到两句角色本人此刻的动作或反应：他做了什么、什么姿态、什么反应。"
            "合计不超过 40 字。"
            "不要写光线、雨声、窗外、夜色这类环境词。"
            "用第三人称写角色本人，不要出现「你」或「我」；"
            "不要写对方的动作、心理或台词，也不要在这里写任何引号里的台词；"
            "承接用户这一轮做的事，写角色对它的反应；"
            "不要重复上一轮已经做过的动作。"
        ),
        "dialogue_hint": (
            "只写角色自己说出口的台词，可以先开口；"
            "用中文双引号不要用英文引号；"
            "台词里可以用「我」；"
            "不要替对方说话或回答，也不要给对方的动作配音；"
            "不要在这里写状态行，状态只写在【状态】块里；"
            "对白必须直接回应用户这一轮说的话或动作，至少写两句，合计不少于 120 字；"
            "台词是对用户说的，像在跟对方搭话，不要自言自语，也不要只写旁观式的感慨"
        ),
    },
    "v8_no_budget": {
        "tail": "动作只写角色身上发生的事，一到两句就够。",
        "hint": (
            "写一到两句角色本人此刻的动作或反应：他做了什么、什么姿态、什么反应。"
            "不要写光线、雨声、窗外、夜色这类环境词。"
            "用第三人称写角色本人，不要出现「你」或「我」；"
            "不要写对方的动作、心理或台词，也不要在这里写任何引号里的台词；"
            "承接用户这一轮做的事，写角色对它的反应；"
            "不要重复上一轮已经做过的动作。"
        ),
        "dialogue_hint": (
            "只写角色自己说出口的台词，可以先开口；"
            "用中文双引号不要用英文引号；"
            "台词里可以用「我」；"
            "不要替对方说话或回答，也不要给对方的动作配音；"
            "不要在这里写状态行，状态只写在【状态】块里；"
            "对白必须直接回应用户这一轮说的话或动作，至少写两句，合计不少于 100 字；"
            "台词是对用户说的，像在跟对方搭话，不要自言自语，也不要只写旁观式的感慨"
        ),
    },
}


def system_text(variant: dict) -> str:
    """按变体拼系统提示：只换动作那一句，其余逐字相同。"""
    base = preset_service.BUILTIN_SYSTEM_PROMPT
    if base.endswith(CURRENT_TAIL):
        return base[: -len(CURRENT_TAIL)] + variant["tail"]
    return base + "\n" + variant["tail"]


def requirement(variant: dict) -> str:
    """回合要求。动作块与对白块提示按当前变体替换。"""
    original_dialogue = structure_mod.STRUCTURE_BLOCKS["dialogue"]
    if variant.get("dialogue_hint"):
        structure_mod.STRUCTURE_BLOCKS["dialogue"] = ("【对话】", variant["dialogue_hint"])
    try:
        return preset_service.render_output_spec(preset_service.BUILTIN_OUTPUT_SPEC)
    finally:
        structure_mod.STRUCTURE_BLOCKS["dialogue"] = original_dialogue


def build_messages(variant: dict, prompt: str) -> list[dict]:
    """构造一轮的消息序列：system 是角色契约，user 是输入加回合要求。"""
    return [
        {"role": "system", "content": system_text(variant) + "\n\n角色：\n" + CARD},
        {"role": "user", "content": prompt + "\n\n" + requirement(variant)},
    ]


def sample(client: httpx.Client, variant: dict, prompt: str) -> dict:
    """跑一轮，返回拆块后的字数与环境词。"""
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
    action = str(blocks.get("action") or "")
    dialogue = str(blocks.get("dialogue") or "")
    return {
        "action_chars": len(action),
        "action_env": [word for word in ENV_WORDS if word in action],
        "dialogue_chars": len(dialogue),
        "dialogue_sentences": sum(1 for ch in dialogue if ch in "。！？"),
        "dialogue_second_person": [word for word in SECOND_PERSON if word in dialogue],
        "filled": sorted(name for name, value in blocks.items() if str(value).strip()),
        "elapsed": elapsed,
        "action_head": action[:50].replace("\n", " "),
    }


def main() -> int:
    """按变体各跑同样几轮，给出动作块字数与环境词命中率。"""
    report: dict[str, object] = {"variants": {}}
    original = structure_mod.STRUCTURE_BLOCKS["action"]
    only = os.environ.get("XW_VARIANTS", "")
    picked = {
        name: variant
        for name, variant in VARIANTS.items()
        if not only or name in only.split(",")
    }
    with httpx.Client(timeout=300.0) as client:
        for name, variant in picked.items():
            structure_mod.STRUCTURE_BLOCKS["action"] = ("【动作】", variant["hint"])
            rows = [sample(client, variant, prompt) for prompt in PROMPTS]
            count = max(1, len(rows))
            report["variants"][name] = {
                "action_chars": [row["action_chars"] for row in rows],
                "action_chars_avg": round(
                    sum(row["action_chars"] for row in rows) / count, 1
                ),
                "action_env_ratio": round(
                    sum(1 for row in rows if row["action_env"]) / count, 2
                ),
                "action_missing": sum(1 for row in rows if row["action_chars"] == 0),
                "dialogue_chars_avg": round(
                    sum(row["dialogue_chars"] for row in rows) / count, 1
                ),
                "body_chars_avg": round(
                    sum(row["action_chars"] + row["dialogue_chars"] for row in rows)
                    / count,
                    1,
                ),
                "second_person_ratio": round(
                    sum(1 for row in rows if row["dialogue_second_person"]) / count, 2
                ),
                "filled": [row["filled"] for row in rows],
                "action_heads": [row["action_head"] for row in rows],
                "elapsed_avg": round(sum(row["elapsed"] for row in rows) / count, 2),
            }
            print(name, json.dumps(report["variants"][name], ensure_ascii=True))
    structure_mod.STRUCTURE_BLOCKS["action"] = original
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
