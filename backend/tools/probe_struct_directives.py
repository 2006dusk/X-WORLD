r"""结构化回合的回合要求单变量对照。

生产链路实测（backend/tools/probe_struct_blocks.py）里，字数带内率只有 3/8，
原文里模型在两块之外追加了一行括号写的「新信息：…」总结，把回合压短。

本探针在真实后端上建临时预设，一次只改一处说法，量四个变体：
  m0 现行口径
  m1 现行口径加每块下限
  m2 现行口径加禁止结尾总结
  m3 两者都加

用法：
    python backend\tools\probe_struct_directives.py
"""

from __future__ import annotations

import json
import os
import sys
import time

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BACKEND)

import httpx  # noqa: E402

from app import presets as preset_service  # noqa: E402

BASE = os.environ.get("XW_BACKEND", "http://127.0.0.1:8760")
SESSION = os.environ.get("XW_SESSION", "s_struct_directives")

TURNS = [
    "这盘带子是谁的？",
    "你好",
    "你是谁",
    "外面雨还没停，你不去看看吗？",
    "刚才敲门的是谁？",
]

MIN = 150
MAX = 300

BASE_SPEC: dict = {
    "min_chars": MIN,
    "max_chars": MAX,
    "require_dialogue": True,
    "structure": ["scene", "dialogue"],
    "advance_plot": True,
    "forbid_meta": True,
}

# 禁止在回合末尾追加合规说明的两种说法
VARIANTS = {
    "q0": ("现行 100/50", BASE_SPEC),
    "q1": ("场景 100 对白 80", {**BASE_SPEC, "structure_min": {"scene": 100, "dialogue": 80}}),
    "q2": ("场景 120 对白 60", {**BASE_SPEC, "structure_min": {"scene": 120, "dialogue": 60}}),
}


def render_with_extra(spec: dict) -> str:
    """渲染回合要求，并在末尾插入附加说法。"""
    tail = spec.get("_tail")
    base = {k: v for k, v in spec.items() if not k.startswith("_")}
    text = preset_service.render_output_spec(base)
    if not tail:
        return text
    return text + tail


def make_preset(preset_id: str, spec: dict) -> None:
    """建临时预设。"""
    preset_service.save_preset({
        "id": preset_id,
        "name": "结构探针 " + preset_id,
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
        "output_spec": {k: v for k, v in spec.items() if not k.startswith("_")},
        "regex_rules": [],
    })


def run_turn(client, prompt: str, preset_id: str) -> dict:
    """跑一轮，收最终块。"""
    request = {
        "model": "xw-local-default",
        "messages": [{"role": "user", "content": prompt}],
        "session": SESSION,
        "stream": True,
        "xw": {
            "character_id": "c_demo",
            "preset_id": preset_id,
            "world_book_ids": ["w_demo"],
            "memory_mode": "hybrid",
            "max_context_tokens": 8192,
        },
    }
    blocks: dict = {}
    final_blocks: dict = {}
    started = time.time()
    with client.stream("POST", BASE + "/v1/chat/completions", json=request) as stream:
        for line in stream.iter_lines():
            if not line:
                continue
            if line.startswith("event:"):
                if line[6:].strip() == "replace":
                    blocks = {}
                continue
            if not line.startswith("data:"):
                continue
            try:
                payload = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if "blocks" in payload:
                final_blocks = payload.get("blocks") or {}
                continue
            content = payload.get("content")
            block = payload.get("block")
            if isinstance(content, str) and content and block:
                blocks[block] = blocks.get(block, "") + content
    return {
        "blocks": final_blocks or blocks,
        "elapsed": time.time() - started,
    }


def main() -> int:
    """跑四个变体并落盘。"""
    rows: list[dict] = []
    with httpx.Client(timeout=240.0) as client:
        for name, (label, spec) in VARIANTS.items():
            preset_id = "p_probe_" + name
            make_preset(preset_id, spec)
            try:
                for index, prompt in enumerate(TURNS):
                    turn = run_turn(client, prompt, preset_id)
                    blocks = turn["blocks"]
                    scene = str(blocks.get("scene") or "")
                    dialogue = str(blocks.get("dialogue") or "")
                    total = len(scene) + len(dialogue)
                    tail = any(
                        line.strip().startswith("（")
                        for line in dialogue.splitlines()
                    )
                    rows.append({
                        "variant": name,
                        "label": label,
                        "turn": index + 1,
                        "scene_len": len(scene),
                        "dialogue_len": len(dialogue),
                        "total": total,
                        "in_band": MIN <= total <= MAX,
                        "tail_summary": tail,
                        "elapsed": round(turn["elapsed"], 2),
                        "scene": scene,
                        "dialogue": dialogue,
                    })
            finally:
                preset_service.delete_preset(preset_id)

    summary = {}
    for name in VARIANTS:
        group = [row for row in rows if row["variant"] == name]
        if not group:
            continue
        summary[name] = {
            "label": group[0]["label"],
            "turns": len(group),
            "in_band": sum(1 for row in group if row["in_band"]),
            "tail_summary": sum(1 for row in group if row["tail_summary"]),
            "total_avg": round(sum(row["total"] for row in group) / len(group), 1),
            "scene_avg": round(sum(row["scene_len"] for row in group) / len(group), 1),
            "dialogue_avg": round(
                sum(row["dialogue_len"] for row in group) / len(group), 1
            ),
            "elapsed_avg": round(sum(row["elapsed"] for row in group) / len(group), 2),
        }

    out = os.path.join(
        ROOT, "..", "docs", "实践情况", "data", "sweep", "struct_directives.json"
    )
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump({"summary": summary, "rows": rows}, handle, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    print("written " + out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
