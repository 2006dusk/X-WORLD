r"""内置预设「格式示例」写法对照实验。

问题：内置预设给的是一段具体叙事示例（地址U雨夜），模型会把它当内容照抄，
逐字输出；下一轮再把上一轮抄出来的示例当上下文抄一遍。

本脚本对四种示例写法各跑同一组提示，逐轮记录字数、对白、场景占比、
与前一轮的相似度、与示例的相似度，最后横向比较。

用法：
    python backend\tools\probe_preset_ab.py
"""

from __future__ import annotations

import difflib
import json
import os
import re
import time

import httpx

BASE = os.environ.get("XW_BACKEND", "http://127.0.0.1:8760")

COMMON = (
    "你扮演角色卡中的角色，以该角色的第一人称身份回应。\n"
    "保持角色一致：只写该角色能知道的事，不解释设定，不提自己是 AI，不写 OOC 或元叙述。\n"
    "优先推进剧情：每次回复给出新的信息、动作或反应，让场景往前走，不原地重复。\n"
    "不替对方行动，不复述对方的输入。\n"
    "用中文写作。\n"
    "每次回复写一个完整回合：先写场景与环境的短描写，再写人物的动作，"
    "然后写一到三句对白，对白用中文引号，最后收在一个明确的动作或台词上。"
)

V0_EXAMPLE = (
    "格式示例：雨点敲在铁皮屋顶上，声音一阵密过一阵。他把手里的磁带放在调音台上，"
    "指腹沿着外壳的划痕慢慢抹过去，确认封条没有被撬过。台灯的光只照亮控制台的一角，"
    "旋钮上落着一层薄灰，看得出最近几天没人来。\n"
    "“你要是想找它的主人，”他没有抬头，“先把带子放完第二面再说。”"
    "说完他才抬起眼，目光在我脸上停了两秒，又落回那盘磁带上。"
    "窗外有辆卡车碾过积水，声音从巷口一路拖到街尾，才慢慢散掉。"
)

V1_EXAMPLE = (
    "格式示例（只示范排版结构，不要照抄里面的内容）："
    "<一句环境与时间的短描写>。<人物的一个具体动作>。"
    "“<第一句对白>”“<第二句对白>”。<人物的反应或收尾动作>。"
)

V3_EXAMPLE = (
    "格式示例（只示范排版，内容不要复用）："
    "清晨的公交站台只剩一个人，雾气把远处的站牌糊成一团白。"
    "他把围巾往上拉了拉，挡住了下半张脸。"
    "“末班车早就走了。”他对着空荡的站台说，“不过你要找的人，我倒是见过。”"
    "说完他才转过身，目光在我脸上停了两秒。"
)

VARIANTS = [
    ("v0_concrete_current", COMMON + "\n" + V0_EXAMPLE),
    ("v1_placeholder_skeleton", COMMON + "\n" + V1_EXAMPLE),
    ("v2_no_example", COMMON),
    ("v3_unrelated_scene", COMMON + "\n" + V3_EXAMPLE),
]

OUTPUT_SPEC = {
    "min_chars": 150,
    "max_chars": 300,
    "require_dialogue": True,
    "single_paragraph": True,
    "advance_plot": True,
    "forbid_meta": True,
}

PROMPTS = [
    "这盘带子是谁的？",
    "你好",
    "你是谁",
    "外面雨还没停，你不去看看吗？",
    "帮我把这盘带子放完第二面。",
    "刚才敲门的是谁？",
]

QUOTE_RE = re.compile("[“”「」]")
SENT_RE = re.compile("[。！？…]")
REPEAT_THRESHOLD = 0.90


def ratio(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def scene_ratio(text: str) -> float:
    """非对白句占总句数的比例，粗略代表场景描写密度。"""
    sentences = [s for s in SENT_RE.split(text) if s.strip()]
    if not sentences:
        return 0.0
    scene = sum(1 for s in sentences if not QUOTE_RE.search(s))
    return round(scene / len(sentences), 3)


def run_turn(client, preset_id, session_id, prompt) -> dict:
    request = {
        "model": "xw-local-default",
        "messages": [{"role": "user", "content": prompt}],
        "session": session_id,
        "stream": True,
        "xw": {
            "character_id": "c_demo",
            "preset_id": preset_id,
            "world_book_ids": ["w_demo"],
            "memory_mode": "hybrid",
            "max_context_tokens": 8192,
        },
    }
    parts = []
    events = []
    started = time.time()
    first = None
    with client.stream("POST", BASE + "/v1/chat/completions", json=request) as stream:
        status = stream.status_code
        for line in stream.iter_lines():
            if not line:
                continue
            if line.startswith("event:"):
                events.append(line[6:].strip())
            elif line.startswith("data:"):
                try:
                    payload = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if isinstance(payload.get("content"), str) and payload["content"]:
                    if first is None:
                        first = time.time() - started
                    parts.append(payload["content"])
    return {
        "status": status,
        "text": "".join(parts),
        "events": events,
        "ttft": first,
        "elapsed": time.time() - started,
    }


def main() -> int:
    results = {}

    with httpx.Client(base_url=BASE, timeout=60.0) as client:
        for variant_id, system_prompt in VARIANTS:
            preset_id = "p_ab_" + variant_id
            resp = client.post("/v1/presets/", json={
                "id": preset_id,
                "name": "AB " + variant_id,
                "prompts": [{
                    "id": "rp_system",
                    "name": "角色扮演契约",
                    "content": system_prompt,
                    "role": "system",
                    "order": 0,
                    "enabled": True,
                }],
                "output_spec": OUTPUT_SPEC,
                "regex_rules": [],
            })
            if resp.status_code != 200:
                print("[FAIL] " + variant_id + " preset HTTP " + str(resp.status_code))
                continue

            session_id = "s_ab_" + variant_id
            rows = []
            texts = []
            with httpx.Client(timeout=180.0) as chat:
                for index, prompt in enumerate(PROMPTS):
                    turn = run_turn(chat, preset_id, session_id, prompt)
                    text = turn["text"]
                    chars = len(text)
                    best_prev = 0.0
                    for prev in texts:
                        best_prev = max(best_prev, ratio(text, prev))
                    rows.append({
                        "turn": index + 1,
                        "prompt": prompt,
                        "chars": chars,
                        "in_band": 150 <= chars <= 300,
                        "quotes": len(QUOTE_RE.findall(text)),
                        "scene_ratio": scene_ratio(text),
                        "repeat_ratio": round(best_prev, 4),
                        "repeat": best_prev >= REPEAT_THRESHOLD,
                        "similar_to_v0_example": round(ratio(text, V0_EXAMPLE), 4),
                        "similar_to_v3_example": round(ratio(text, V3_EXAMPLE), 4),
                        "ttft": round(turn["ttft"], 2) if turn["ttft"] else None,
                        "elapsed": round(turn["elapsed"], 2),
                        "text": text,
                    })
                    texts.append(text)

            distinct = len(set(texts))
            results[variant_id] = {
                "rows": rows,
                "summary": {
                    "turns": len(rows),
                    "in_band": sum(1 for r in rows if r["in_band"]),
                    "with_dialogue": sum(1 for r in rows if r["quotes"] >= 2),
                    "repeat_turns": sum(1 for r in rows if r["repeat"]),
                    "distinct_replies": distinct,
                    "chars_min": min(r["chars"] for r in rows),
                    "chars_max": max(r["chars"] for r in rows),
                    "scene_ratio_avg": round(
                        sum(r["scene_ratio"] for r in rows) / len(rows), 3
                    ),
                    "elapsed_avg": round(
                        sum(r["elapsed"] for r in rows) / len(rows), 2
                    ),
                },
            }
            s = results[variant_id]["summary"]
            print(
                variant_id
                + " 带内=" + str(s["in_band"]) + "/" + str(s["turns"])
                + " 有对白=" + str(s["with_dialogue"])
                + " 复读轮=" + str(s["repeat_turns"])
                + " 不同回复=" + str(s["distinct_replies"])
                + " 字数=" + str(s["chars_min"]) + "-" + str(s["chars_max"])
                + " 场景占比=" + str(s["scene_ratio_avg"])
                + " 均耗时=" + str(s["elapsed_avg"]) + "s"
            )

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out = os.path.join(
        root, "..", "docs", "实践情况", "data", "sweep", "preset_ab.json"
    )
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump({
            "thresholds": {"min_chars": 150, "max_chars": 300, "repeat": REPEAT_THRESHOLD},
            "variants": results,
        }, handle, ensure_ascii=False, indent=2)
    print("已写入 " + out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
