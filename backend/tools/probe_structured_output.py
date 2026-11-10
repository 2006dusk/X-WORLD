r"""结构化输出对照实验：场景块加对白块。

现状是单段输出，场景与对白混在一段里。7.17 节实测 16 轮里 3 轮完全没有
对白，这道缺口提示层补不上。本实验比较两种回合要求：

  single —— 现行：写成完整一段，包含场景、动作与一到三句对白
  blocks —— 分块写：先写【场景】再写【对话】，两块都要有

指标：两块标记的合规率、对白块是否带中文引号、对白块缺失轮数、
总字数带内率、复读情况。走真实后端链路。

用法：
    python backend\tools\probe_structured_output.py
"""

from __future__ import annotations

import difflib
import json
import os
import re
import sys
import time

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BACKEND)

import httpx  # noqa: E402

from app import presets as preset_service  # noqa: E402
from app import store  # noqa: E402

BASE = os.environ.get("XW_BACKEND", "http://127.0.0.1:8760")
SCENE_LABEL = "【场景】"
DIALOGUE_LABEL = "【对话】"

TURNS = [
    "这盘带子是谁的？",
    "你好",
    "你是谁",
    "外面雨还没停，你不去看看吗？",
    "刚才敲门的是谁？",
]

VARIANTS = {
    "single": {
        "single_paragraph": True,
        "structure": None,
    },
    "blocks": {
        "single_paragraph": False,
        "structure": ["scene", "dialogue"],
    },
}


def ratio(left: str, right: str) -> float:
    """两段文本的相似度。"""
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def build_preset(name: str, variant: dict) -> str:
    """按变体建一份临时预设。"""
    preset_id = "p_struct_" + name
    spec = {
        "min_chars": 150,
        "max_chars": 300,
        "require_dialogue": True,
        "single_paragraph": variant["single_paragraph"],
        "advance_plot": True,
        "forbid_meta": True,
    }
    if variant.get("structure"):
        spec["structure"] = list(variant["structure"])
    store.save_preset({
        "id": preset_id,
        "name": "结构对照 " + name,
        "prompts": [
            {
                "id": "rp_system",
                "name": "角色扮演契约",
                "content": preset_service.BUILTIN_SYSTEM_PROMPT,
                "role": "system",
                "order": 0,
                "enabled": True,
            }
        ],
        "output_spec": spec,
    })
    return preset_id


def run_turn(client, session: str, preset_id: str, prompt: str) -> dict:
    """跑一轮，replace 事件后清空已累积文本。"""
    request = {
        "model": "xw-local-default",
        "messages": [{"role": "user", "content": prompt}],
        "session": session,
        "stream": True,
        "xw": {
            "character_id": "c_demo",
            "preset_id": preset_id,
            "world_book_ids": ["w_demo"],
            "memory_mode": "hybrid",
            "max_context_tokens": 8192,
        },
    }
    parts: list[str] = []
    events: list[str] = []
    started = time.time()
    with client.stream("POST", BASE + "/v1/chat/completions", json=request) as stream:
        for line in stream.iter_lines():
            if not line:
                continue
            if line.startswith("event:"):
                name = line[6:].strip()
                events.append(name)
                if name == "replace":
                    parts.clear()
                continue
            if line.startswith("data:"):
                try:
                    payload = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                content = payload.get("content")
                if isinstance(content, str) and content:
                    parts.append(content)
    return {
        "text": "".join(parts),
        "events": events,
        "elapsed": time.time() - started,
    }


def split_blocks(text: str) -> dict:
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
    plain = text.replace(SCENE_LABEL, "").replace(DIALOGUE_LABEL, "")
    return {"scene": scene.strip(), "dialogue": dialogue.strip(), "plain": plain.strip()}


def run_variant(name: str, variant: dict) -> dict:
    """跑一个变体。"""
    preset_id = build_preset(name, variant)
    session = "s_struct_" + name
    rows: list[dict] = []
    texts: list[str] = []
    for index, prompt in enumerate(TURNS):
        result = run_turn(session=session, preset_id=preset_id, prompt=prompt, client=None)             if False else run_turn(client=None, session=session, preset_id=preset_id, prompt=prompt)
        text = result["text"]
        blocks = split_blocks(text)
        best = 0.0
        identical = False
        for previous in texts:
            best = max(best, ratio(text, previous))
            if text == previous:
                identical = True
        rows.append({
            "turn": index + 1,
            "prompt": prompt,
            "chars": len(blocks["plain"]),
            "in_band": 150 <= len(blocks["plain"]) <= 300,
            "has_scene_label": SCENE_LABEL in text,
            "has_dialogue_label": DIALOGUE_LABEL in text,
            "scene_len": len(blocks["scene"]),
            "dialogue_len": len(blocks["dialogue"]),
            "dialogue_has_quote": "“" in blocks["dialogue"],
            "dialogue_missing": not blocks["dialogue"],
            "max_ratio": round(best, 3),
            "identical": identical,
            "replace_events": result["events"].count("replace"),
            "elapsed": round(result["elapsed"], 2),
            "text": text,
        })
        texts.append(text)
        print(
            "  T%d 字数=%d 带内=%s 场景块=%d 对白块=%d 对白引号=%s 相似=%.3f"
            % (
                index + 1,
                len(blocks["plain"]),
                150 <= len(blocks["plain"]) <= 300,
                len(blocks["scene"]),
                len(blocks["dialogue"]),
                "“" in blocks["dialogue"],
                best,
            )
        )
        print("     " + text.replace(chr(10), " | "))

    summary = {
        "turns": len(rows),
        "in_band": sum(1 for row in rows if row["in_band"]),
        "with_scene_label": sum(1 for row in rows if row["has_scene_label"]),
        "with_dialogue_label": sum(1 for row in rows if row["has_dialogue_label"]),
        "dialogue_missing": sum(1 for row in rows if row["dialogue_missing"]),
        "dialogue_with_quote": sum(
            1 for row in rows if row["dialogue_has_quote"] and not row["dialogue_missing"]
        ),
        "identical_turns": sum(1 for row in rows if row["identical"]),
        "max_ratio": max(row["max_ratio"] for row in rows),
        "scene_len_avg": round(sum(row["scene_len"] for row in rows) / len(rows), 1),
        "dialogue_len_avg": round(sum(row["dialogue_len"] for row in rows) / len(rows), 1),
        "elapsed_avg": round(sum(row["elapsed"] for row in rows) / len(rows), 2),
    }
    store.delete_preset(preset_id)
    return {"summary": summary, "rows": rows}


def main() -> int:
    """跑两个变体并落盘。"""
    results: dict = {}
    with httpx.Client(timeout=240.0) as client:
        for name, variant in VARIANTS.items():
            print(name + "：")
            preset_id = build_preset(name, variant)
            session = "s_struct_" + name
            rows: list[dict] = []
            texts: list[str] = []
            for index, prompt in enumerate(TURNS):
                result = run_turn(client, session, preset_id, prompt)
                text = result["text"]
                blocks = split_blocks(text)
                best = 0.0
                identical = False
                for previous in texts:
                    best = max(best, ratio(text, previous))
                    if text == previous:
                        identical = True
                rows.append({
                    "turn": index + 1,
                    "prompt": prompt,
                    "chars": len(blocks["plain"]),
                    "in_band": 150 <= len(blocks["plain"]) <= 300,
                    "has_scene_label": SCENE_LABEL in text,
                    "has_dialogue_label": DIALOGUE_LABEL in text,
                    "scene_len": len(blocks["scene"]),
                    "dialogue_len": len(blocks["dialogue"]),
                    "dialogue_has_quote": "“" in blocks["dialogue"],
                    "dialogue_missing": not blocks["dialogue"],
                    "max_ratio": round(best, 3),
                    "identical": identical,
                    "replace_events": result["events"].count("replace"),
                    "elapsed": round(result["elapsed"], 2),
                    "text": text,
                })
                texts.append(text)
                print(
                    "  T%d 字数=%d 带内=%s 场景块=%d 对白块=%d 对白引号=%s 相似=%.3f"
                    % (
                        index + 1,
                        len(blocks["plain"]),
                        150 <= len(blocks["plain"]) <= 300,
                        len(blocks["scene"]),
                        len(blocks["dialogue"]),
                        "“" in blocks["dialogue"],
                        best,
                    )
                )
                print("     " + text.replace(chr(10), " | "))

            results[name] = {
                "summary": {
                    "turns": len(rows),
                    "in_band": sum(1 for row in rows if row["in_band"]),
                    "with_scene_label": sum(1 for row in rows if row["has_scene_label"]),
                    "with_dialogue_label": sum(1 for row in rows if row["has_dialogue_label"]),
                    "dialogue_missing": sum(1 for row in rows if row["dialogue_missing"]),
                    "dialogue_with_quote": sum(
                        1 for row in rows if row["dialogue_has_quote"] and not row["dialogue_missing"]
                    ),
                    "identical_turns": sum(1 for row in rows if row["identical"]),
                    "max_ratio": max(row["max_ratio"] for row in rows),
                    "scene_len_avg": round(sum(row["scene_len"] for row in rows) / len(rows), 1),
                    "dialogue_len_avg": round(sum(row["dialogue_len"] for row in rows) / len(rows), 1),
                    "elapsed_avg": round(sum(row["elapsed"] for row in rows) / len(rows), 2),
                },
                "rows": rows,
            }
            store.delete_preset(preset_id)
            print()

    for name in VARIANTS:
        print(name + " " + json.dumps(results[name]["summary"], ensure_ascii=False))

    out = os.path.join(
        ROOT, "..", "docs", "实践情况", "data", "sweep", "structured_output.json"
    )
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(results, handle, ensure_ascii=False, indent=2)
    print("已写入 " + out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
