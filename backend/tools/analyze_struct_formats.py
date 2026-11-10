r"""结构化输出各形态的离线复算。

探针直连推理服务，不经过客户端的排版归一层，因此 JSON 形态的对白里
模型写的英文双引号没有被转成中文引号。客户端实际链路是：先解析 JSON，
再把 scene 与 dialogue 字段分别送进归一层。本脚本按真实链路复算，
避免把「模型写了英文引号」误判成「模型没写引号」。

同时复核 JSON 解析失败轮次的原因：缺右括号、被代码块包裹、还是超长截断。

用法：
    python backend\tools\analyze_struct_formats.py
"""

from __future__ import annotations

import json
import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BACKEND)

from app.inference.normalize import normalize_cjk_quotes  # noqa: E402

DATA = os.path.join(
    ROOT, "..", "docs", "实践情况", "data", "sweep", "json_output.json"
)

SCENE_LABEL = "【场景】"
DIALOGUE_LABEL = "【对话】"

ASCII_QUOTE = chr(34)


def strip_fence(text: str) -> str:
    """剥掉代码块围栏。"""
    stripped = text.strip()
    if stripped.startswith("```"):
        if "\n" in stripped:
            stripped = stripped.split("\n", 1)[-1]
        if stripped.endswith("```"):
            stripped = stripped[:-3]
    return stripped.strip()


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


def extract(row: dict) -> tuple:
    """取三块内容：先按 JSON 解析，再按标记拆，最后退回整段原文。

    返回 (场景, 动作, 对白)，与探针一致：总字数是三块之和。
    """
    raw = str(row.get("raw") or "")
    parsed = None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        try:
            parsed = json.loads(strip_fence(raw))
        except json.JSONDecodeError:
            parsed = None
    if isinstance(parsed, dict):
        return (
            str(parsed.get("scene") or ""),
            str(parsed.get("action") or ""),
            str(parsed.get("dialogue") or ""),
        )
    if SCENE_LABEL in raw or DIALOGUE_LABEL in raw:
        scene, dialogue = split_markers(raw)
        return scene, "", dialogue
    return "", "", raw


def parse_failure_reason(raw: str, tokens: int) -> str:
    """判断 JSON 解析失败的原因。"""
    body = strip_fence(raw)
    if not body.startswith("{"):
        return "不是 JSON 开头"
    if body.rstrip().endswith("}"):
        return "括号闭合但语法非法"
    if tokens >= 512:
        return "被 max_tokens 截断"
    return "缺右括号，未触顶"


def main() -> int:
    """复算并落盘。"""
    with open(DATA, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    report = {}
    for name, payload in data.items():
        rows = payload.get("rows") or []
        raw_quote = 0
        norm_quote = 0
        ascii_quote = 0
        in_band = 0
        in_band_parsed = 0
        lengths = []
        failures = []
        for row in rows:
            scene, action, dialogue = extract(row)
            content = scene + action + dialogue
            failed = name.startswith("json") and not row.get("parsed")
            if content and not failed:
                lengths.append(len(content))
            if ASCII_QUOTE in dialogue:
                ascii_quote += 1
            normalized = normalize_cjk_quotes(dialogue)
            if "“" in dialogue:
                raw_quote += 1
            if "“" in normalized:
                norm_quote += 1
            if 150 <= len(content) <= 300:
                in_band += 1
                if not failed:
                    in_band_parsed += 1
            if name.startswith("json") and not row.get("parsed"):
                failures.append(
                    {
                        "turn": row.get("turn"),
                        "reason": parse_failure_reason(
                            str(row.get("raw") or ""),
                            int(row.get("completion_tokens") or 0),
                        ),
                        "completion_tokens": row.get("completion_tokens"),
                    }
                )
        summary = payload.get("summary") or {}
        report[name] = {
            "turns": len(rows),
            "json_parsed": summary.get("parsed"),
            "dialogue_quote_raw": raw_quote,
            "dialogue_quote_after_normalize": norm_quote,
            "dialogue_used_ascii_quote": ascii_quote,
            "in_band": in_band,
            "in_band_parsed": in_band_parsed,
            "content_len_avg": round(sum(lengths) / len(lengths), 1) if lengths else 0,
            "tokens_avg": summary.get("tokens_avg"),
            "elapsed_avg": summary.get("elapsed_avg"),
            "identical_turns": summary.get("identical_turns"),
            "parse_failures": failures,
        }

    out = os.path.join(
        ROOT, "..", "docs", "实践情况", "data", "sweep", "struct_formats.json"
    )
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    print(json.dumps(report, ensure_ascii=True, indent=2))
    print("written " + out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
