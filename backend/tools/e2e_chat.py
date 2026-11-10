"""端到端聊天验证：装配 → 本地推理 → SSE。

要求本地 llama.cpp 兼容服务已在 127.0.0.1:8080 启动。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

probe_dir = Path(tempfile.mkdtemp(prefix="xw_e2e_"))
os.environ["XWORLD_DATA_DIR"] = str(probe_dir)

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

UPSTREAM = os.environ.get("XW_UPSTREAM", "http://127.0.0.1:8080")


def wait_upstream(timeout: float = 180.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{UPSTREAM}/v1/models", timeout=3.0)
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(3.0)
    return False


CARD = {
    "name": "角色K",
    "description": "在地址N经营地址U的调音师，沉默寡言，观察力极强。",
    "personality": "沉默、克制、偶尔流露温柔",
    "scenario": "雨夜的地址N，地址U只剩一盏台灯",
    "first_mes": "门铃响了一声。",
    "mes_example": "<START>",
}

PRESET = {
    "name": "基础预设",
    "prompts": [
        {"name": "角色扮演指令", "content": "你正在扮演角色K，回复保持角色一致。", "role": "system", "order": 0}
    ],
}

BOOK = {
    "name": "地址N",
    "entries": [
        {"keys": ["雨夜", "地址U"], "content": "地址N的雨夜很安静，霓虹灯在水洼里碎成一片。", "priority": 10}
    ],
}


def build_request(card_id: str, preset_id: str, book_id: str, stream: bool) -> dict:
    return {
        "model": "xw-local-default",
        "messages": [{"role": "user", "content": "你还在地址U吗？"}],
        "stream": stream,
        "xw": {
            "character_id": card_id,
            "preset_id": preset_id,
            "world_book_ids": [book_id],
            "memory_mode": "none",
            "max_context_tokens": 2048,
        },
    }


failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if condition else 'FAIL'}] {label}{(' -> ' + detail) if detail else ''}")
    if not condition:
        failures.append(label)


print("=== 等待本地推理服务 ===")
if not wait_upstream():
    print("上游推理服务未就绪，终止")
    sys.exit(2)
print("上游已就绪")

with TestClient(app) as client:
    card = client.post("/v1/cards/import", json=CARD).json()
    preset = client.post("/v1/presets/", json=PRESET).json()
    book = client.post("/v1/world-books/", json=BOOK).json()
    print(f"角色卡 {card['id']} / 预设 {preset['id']} / 世界书 {book['id']}")

    print("=== 非流式 ===")
    resp = client.post(
        "/v1/chat/completions", json=build_request(card["id"], preset["id"], book["id"], False)
    )
    check("非流式状态码", resp.status_code == 200, f"{resp.status_code} {resp.text[:200]}")
    if resp.status_code == 200:
        body = resp.json()
        text = body["choices"][0]["message"]["content"]
        check("返回正文非空", bool(text.strip()), text[:160])
        usage = body.get("usage") or {}
        check("usage 带 token 计数", usage.get("prompt_tokens", 0) > 0, json.dumps(usage, ensure_ascii=False)[:200])
        budget = usage.get("budget") or {}
        check("usage 带预算", "limit" in budget, json.dumps(budget, ensure_ascii=False))
        report = (body.get("xw") or {}).get("context_report") or {}
        segments = [segment.get("name") for segment in report.get("segments") or []]
        check("装配回执 9 段", len(segments) == 9, str(segments))
        check("回执含 template 与 tokenizer", bool(report.get("template")) and bool(report.get("tokenizer")), str(report.get("template")) + "/" + str(report.get("tokenizer")))
        world_segment = next((s for s in report.get("segments") or [] if s.get("name") == "world_info"), None)
        check("世界书段有命中", bool(world_segment and world_segment.get("tokens", 0) > 0), json.dumps(world_segment, ensure_ascii=False) if world_segment else "无")

    print("=== 流式 ===")
    event_names: list[str] = []
    payloads: list[dict] = []
    with client.stream(
        "POST", "/v1/chat/completions", json=build_request(card["id"], preset["id"], book["id"], True)
    ) as stream:
        check("流式状态码", stream.status_code == 200, str(stream.status_code))
        for line in stream.iter_lines():
            if line.startswith("event:"):
                event_names.append(line[6:].strip())
            elif line.startswith("data:"):
                try:
                    payloads.append(json.loads(line[5:].strip()))
                except json.JSONDecodeError:
                    pass
    check("首事件为 context", bool(event_names) and event_names[0] == "context", str(event_names[:6]))
    check("末事件为 done", bool(event_names) and event_names[-1] == "done", str(event_names[-3:]))
    check("含 delta 事件", "delta" in event_names, str(event_names))
    check("含 usage 事件", "usage" in event_names, str(event_names))
    check("含 reasoning 或无推理", True, str(event_names))

print()
if failures:
    print(f"FAILURES: {len(failures)} -> {failures}")
    sys.exit(1)
print("E2E PASSED")
