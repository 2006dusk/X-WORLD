r"""JSON 结构化输出对照实验。

问题：回合要求要求场景与对白，但有约两成轮次没有对白，提示层没有强制力。
方向：让模型输出固定结构的 JSON，客户端做二次格式加工。

服务端会不会按 schema 约束生成，以本实验的实测结果为准，不预设结论：
请求里带上 response_format 与 schema，看返回的文本是否真的符合 schema。

本实验直连本机推理服务端（默认 127.0.0.1:8080），比较三种输出：
  plain —— 现行单段文本
  json2 —— schema 只有 scene 与 dialogue
  json3 —— schema 有 scene、action、dialogue

指标：JSON 解析成功率、字段齐全率、各块字数、对白有无中文引号、
内容总字数带内率、轮间相似度与耗时。

用法：
    python backend\tools\probe_json_output.py
"""

from __future__ import annotations

import difflib
import json
import os
import sys
import time

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BACKEND)

import httpx  # noqa: E402

from app import presets as preset_service  # noqa: E402

LLAMA = os.environ.get("XW_LLAMA", "http://127.0.0.1:8080")

TURNS = [
    "这盘带子是谁的？",
    "你好",
    "你是谁",
    "外面雨还没停，你不去看看吗？",
    "刚才敲门的是谁？",
]

SAMPLING = {
    "temperature": 0.7,
    "top_p": 0.9,
    "max_tokens": 512,
    "repeat_penalty": 1.1,
    "top_k": 40,
    "min_p": 0.05,
}

SCHEMA_TWO = {
    "type": "object",
    "properties": {
        "scene": {"type": "string"},
        "dialogue": {"type": "string"},
    },
    "required": ["scene", "dialogue"],
}

SCHEMA_THREE = {
    "type": "object",
    "properties": {
        "scene": {"type": "string"},
        "action": {"type": "string"},
        "dialogue": {"type": "string"},
    },
    "required": ["scene", "action", "dialogue"],
}

DIRECTIVE_PLAIN = (
    "本次回复是一个完整回合。全文不少于 150 字且不超过 300 字。"
    "包含场景描写、人物动作和一到三句对白，对白必须用中文双引号，不要用英文引号。"
    "写成完整一段，不分行。给出新信息或新动作推进剧情，不要重复上一段。"
    "不要写提纲、旁白总结或 OOC 说明。"
)

DIRECTIVE_JSON_TWO = (
    "本次回复是一个完整回合。输出一个 JSON 对象，"
    "scene 字段写场景描写与人物动作，dialogue 字段写一到三句对白，"
    "对白必须用中文双引号不要用英文引号。"
    "scene 与 dialogue 的正文合计不少于 150 字且不超过 300 字。"
    "给出新信息或新动作推进剧情，不要重复上一段。"
    "不要写提纲、旁白总结或 OOC 说明。"
)

DIRECTIVE_JSON_THREE = (
    "本次回复是一个完整回合。输出一个 JSON 对象，"
    "scene 字段写环境描写，action 字段写人物动作，dialogue 字段写一到三句对白，"
    "对白必须用中文双引号不要用英文引号。"
    "三个字段的正文合计不少于 150 字且不超过 300 字。"
    "给出新信息或新动作推进剧情，不要重复上一段。"
    "不要写提纲、旁白总结或 OOC 说明。"
)

DIRECTIVE_JSON_PROMPT = (
    "本次回复是一个完整回合。只输出一个 JSON 对象，字段只有 scene 与 dialogue。"
    "scene 字段写场景描写与人物动作，dialogue 字段写一到三句对白，"
    "对白必须用中文双引号不要用英文引号。"
    "只输出 JSON 本身，不要加代码块标记，不要写解释、前缀或后缀。"
    "scene 与 dialogue 的正文合计不少于 150 字且不超过 300 字。"
    "给出新信息或新动作推进剧情，不要重复上一段。"
    "不要写提纲、旁白总结或 OOC 说明。"
)

DIRECTIVE_MARKERS = (
    "本次回复是一个完整回合。分两块写，每块以标记开头："
    "先写【场景】写环境与人物动作，再写【对话】写一到三句对白，"
    "对白必须用中文双引号不要用英文引号。"
    "两块之间换一行，两块都要有，不能缺。"
    "两块正文合计不少于 150 字且不超过 300 字。"
    "给出新信息或新动作推进剧情，不要重复上一段。"
    "不要写提纲、旁白总结或 OOC 说明。"
)

VARIANTS = {
    "plain": {"directive": DIRECTIVE_PLAIN, "schema": None},
    "markers": {"directive": DIRECTIVE_MARKERS, "schema": None},
    "json2": {"directive": DIRECTIVE_JSON_TWO, "schema": SCHEMA_TWO},
    "json3": {"directive": DIRECTIVE_JSON_THREE, "schema": SCHEMA_THREE},
    "json_prompt": {"directive": DIRECTIVE_JSON_PROMPT, "schema": None},
}

SCENE_LABEL = "【场景】"
DIALOGUE_LABEL = "【对话】"


def split_markers(text: str) -> tuple:
    """按标记拆出场景块与对白块。"""
    scene = ""
    dialogue = ""
    if SCENE_LABEL in text and DIALOGUE_LABEL in text:
        head, _, tail = text.partition(DIALOGUE_LABEL)
        scene = head.split(SCENE_LABEL, 1)[1] if SCENE_LABEL in head else ""
        dialogue = tail
    elif SCENE_LABEL in text:
        scene = text.split(SCENE_LABEL, 1)[1]
    elif DIALOGUE_LABEL in text:
        dialogue = text.split(DIALOGUE_LABEL, 1)[1]
    return scene.strip(), dialogue.strip()


def strip_fence(text: str) -> str:
    """剥掉代码块围栏，取出 JSON 正文。"""
    stripped = text.strip()
    if stripped.startswith("```"):
        if "\n" in stripped:
            stripped = stripped.split("\n", 1)[-1]
        if stripped.endswith("```"):
            stripped = stripped[:-3]
    return stripped.strip()


def ratio(left: str, right: str) -> float:
    """两段文本的相似度。"""
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def build_messages(prompt: str, directive: str) -> list:
    """拼消息序列。"""
    return [
        {"role": "system", "content": preset_service.BUILTIN_SYSTEM_PROMPT},
        {"role": "user", "content": prompt + chr(10) * 2 + directive},
    ]


def generate(client, messages: list, schema) -> tuple:
    """发一次请求。schema 非空时按 schema 约束输出。"""
    payload = dict(SAMPLING)
    payload["messages"] = messages
    if schema is not None:
        payload["response_format"] = {"type": "json_object", "schema": schema}
    started = time.time()
    response = client.post(LLAMA + "/v1/chat/completions", json=payload)
    elapsed = time.time() - started
    if response.status_code != 200:
        raise RuntimeError(str(response.status_code) + ": " + response.text[:300])
    data = response.json()
    choice = (data.get("choices") or [{}])[0]
    usage = data.get("usage") or {}
    return (
        str(choice.get("message", {}).get("content") or ""),
        round(elapsed, 2),
        int(usage.get("completion_tokens") or 0),
    )


def run_variant(name: str, variant: dict) -> dict:
    """跑一个变体。"""
    rows: list[dict] = []
    texts: list[str] = []
    with httpx.Client(timeout=240.0) as client:
        for index, prompt in enumerate(TURNS):
            raw, elapsed, tokens = generate(
                client, build_messages(prompt, variant["directive"]), variant["schema"]
            )
            parsed = None
            error = ""
            fenced = False
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                error = str(exc)
                try:
                    parsed = json.loads(strip_fence(raw))
                    fenced = True
                    error = ""
                except json.JSONDecodeError:
                    parsed = None

            fields = parsed if isinstance(parsed, dict) else {}
            scene = str(fields.get("scene") or "")
            action = str(fields.get("action") or "")
            dialogue = str(fields.get("dialogue") or "")
            if not fields:
                scene, dialogue = split_markers(raw)
                action = ""
            content = scene + action + dialogue
            body = content if content else raw
            if scene or dialogue:
                fields = {"scene": scene, "action": action, "dialogue": dialogue}

            best = 0.0
            identical = False
            for previous in texts:
                best = max(best, ratio(body, previous))
                if body == previous:
                    identical = True

            rows.append({
                "turn": index + 1,
                "prompt": prompt,
                "parsed": parsed is not None,
                "parsed_fenced": fenced,
                "parse_error": error,
                "fields": sorted(fields.keys()) if fields else [],
                "marker_scene": SCENE_LABEL in raw,
                "marker_dialogue": DIALOGUE_LABEL in raw,
                "scene_len": len(scene),
                "action_len": len(action),
                "dialogue_len": len(dialogue),
                "dialogue_has_quote": "“" in dialogue,
                "content_len": len(content),
                "in_band": 150 <= len(content) <= 300,
                "completion_tokens": tokens,
                "max_ratio": round(best, 3),
                "identical": identical,
                "elapsed": elapsed,
                "raw": raw,
            })
            texts.append(body)
            print(
                "  T%d 解析=%s 字段=%s 场景=%d 动作=%d 对白=%d 引号=%s 带内=%s tok=%d %.2fs"
                % (
                    index + 1,
                    parsed is not None,
                    ",".join(sorted(fields.keys())) or "-",
                    len(scene),
                    len(action),
                    len(dialogue),
                    "“" in dialogue,
                    150 <= len(content) <= 300,
                    tokens,
                    elapsed,
                )
            )
            print("     " + raw.replace(chr(10), " | ")[:300])

    parsed_rows = [row for row in rows if row["parsed"]]
    summary = {
        "turns": len(rows),
        "parsed": len(parsed_rows),
        "parsed_fenced": sum(1 for row in rows if row.get("parsed_fenced")),
        "fields_complete": sum(
            1 for row in rows if row["fields"] and "scene" in row["fields"]
            and "dialogue" in row["fields"]
        ),
        "dialogue_has_quote": sum(1 for row in rows if row["dialogue_has_quote"]),
        "in_band": sum(1 for row in rows if row["in_band"]),
        "identical_turns": sum(1 for row in rows if row["identical"]),
        "max_ratio": max(row["max_ratio"] for row in rows),
        "content_len_avg": round(sum(row["content_len"] for row in rows) / len(rows), 1),
        "tokens_avg": round(sum(row["completion_tokens"] for row in rows) / len(rows), 1),
        "elapsed_avg": round(sum(row["elapsed"] for row in rows) / len(rows), 2),
    }
    return {"summary": summary, "rows": rows}


def main() -> int:
    """跑变体并落盘。命令行给出变体名时只跑这些变体，结果并入已有文件。"""
    wanted = [item for item in sys.argv[1:] if item in VARIANTS]
    names = wanted or list(VARIANTS.keys())
    out = os.path.join(
        ROOT, "..", "docs", "实践情况", "data", "sweep", "json_output.json"
    )
    results = {}
    if os.path.exists(out):
        try:
            with open(out, "r", encoding="utf-8") as handle:
                results = json.load(handle)
        except (OSError, json.JSONDecodeError):
            results = {}
    for name in names:
        print(name + "：")
        results[name] = run_variant(name, VARIANTS[name])
        print()

    for name in names:
        print(name + " " + json.dumps(results[name]["summary"], ensure_ascii=False))

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(results, handle, ensure_ascii=False, indent=2)
    print("已写入 " + out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
