r"""抓取真实 SSE 事件序列，定位「内容消失后重新生成」的触发点。

用法：python backend\tools\probe_sse_sequence.py [轮数]
输出：docs\实践情况\data\sweep\sse_sequence.json
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT.parent / "docs" / "实践情况" / "data" / "sweep" / "sse_sequence.json"

URL = "http://127.0.0.1:8760/v1/chat/completions"

PROMPTS = [
    "我推开吱呀作响的木门，探头往里看。",
    "我把手里那封没拆的信举到她面前。",
    "外面雨声忽然大了，我往窗边靠了靠。",
]


def one_turn(client: httpx.Client, session: str, prompt: str) -> dict:
    """跑一轮流式生成，记录事件顺序与每轮增量。"""
    payload = {
        "model": "xw-local-default",
        "messages": [{"role": "user", "content": prompt}],
        "session": session,
        "stream": True,
        "xw": {
            "character_id": "c_demo",
            "preset_id": "p_demo",
            "world_book_ids": ["w_demo"],
            "memory_mode": "hybrid",
            "max_context_tokens": 8192,
        },
    }
    events = []
    with client.stream("POST", URL, json=payload, timeout=300.0) as resp:
        resp.raise_for_status()
        event_name = "message"
        for line in resp.iter_lines():
            if not line:
                continue
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:])
                events.append({"event": event_name, "data": data})
    rounds = []
    current = {"events": [], "delta_chars": 0, "blocks": {}}
    for item in events:
        name = item["event"]
        current["events"].append(name)
        if name == "delta":
            data = item["data"]
            text = data.get("content") or ""
            current["delta_chars"] += len(text)
            block = data.get("block")
            if block:
                current["blocks"][block] = current["blocks"].get(block, 0) + len(text)
        elif name == "replace":
            rounds.append(current)
            current = {"events": [], "delta_chars": 0, "blocks": {}}
    rounds.append(current)
    final_blocks = None
    state_event = None
    replace_reasons = [item["data"].get("reason") for item in events if item["event"] == "replace"]
    for item in events:
        if item["event"] == "blocks":
            final_blocks = item["data"]
        if item["event"] == "state":
            state_event = item["data"]
    return {
        "prompt": prompt,
        "event_names": [item["event"] for item in events],
        "replace_reasons": replace_reasons,
        "rounds": rounds,
        "final_blocks": final_blocks,
        "state": state_event,
    }


def main() -> None:
    """主流程。"""
    turns = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    session = "s_probe_sse_" + os.environ.get("XW_SESSION_SUFFIX", "a")
    results = []
    with httpx.Client() as client:
        for prompt in PROMPTS[:turns]:
            results.append(one_turn(client, session, prompt))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    for item in results:
        print("prompt:", item["prompt"])
        print("  replace_reasons:", item["replace_reasons"])
        for index, rnd in enumerate(item["rounds"]):
            print(
                "  round", index,
                "delta_chars", rnd["delta_chars"],
                "blocks", rnd["blocks"],
            )
        fb = item["final_blocks"] or {}
        lengths = {}
        for key, value in (fb.get("blocks") or {}).items():
            lengths[key] = len(value)
        print("  final block chars:", lengths)
    print("written:", OUT)


if __name__ == "__main__":
    main()
