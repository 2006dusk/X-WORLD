"""内置预设端到端验证。

验证内置 RP 预设这条链路真的成立：
- 请求不带 preset_id 时，装配回退到内置预设；
- 回合要求按 output_spec 渲染并附在本轮输入末尾；
- 模型在本地推理服务上写出的回合落在 150 到 300 字区间且带中文引号对白。

要求本地 llama.cpp 兼容服务已在 127.0.0.1:8080 启动。

用法：
    F:/python/python.exe backend/tools/verify_builtin_preset.py
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

probe_dir = Path(tempfile.mkdtemp(prefix="xw_builtin_"))
os.environ["XWORLD_DATA_DIR"] = str(probe_dir)

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

UPSTREAM = os.environ.get("XW_UPSTREAM", "http://127.0.0.1:8080")
DIALOGUE_RE = re.compile(r"[“「『]([^”」』]*)")
MIN_CHARS = 150
MAX_CHARS = 300

CARD = {
    "name": "角色K",
    "description": "在地址N经营地址U的调音师，沉默寡言，观察力极强。",
    "personality": "沉默、克制、偶尔流露温柔",
    "scenario": "雨夜的地址N，地址U只剩一盏台灯",
    "first_mes": "门铃响了一声。",
    "mes_example": "<START>",
}

USER_INPUT = "你还在地址U吗？"

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    """记录一条验收结论。"""
    print(f"[{'OK  ' if condition else 'FAIL'}] {label}{(' -> ' + detail) if detail else ''}")
    if not condition:
        failures.append(label)


def wait_upstream(timeout: float = 300.0) -> bool:
    """等本地推理服务就绪。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{UPSTREAM}/v1/models", timeout=3.0)
            if resp.status_code == 200:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(3.0)
    return False


print("=== 等待本地推理服务 ===")
if not wait_upstream():
    print("上游推理服务未就绪，终止")
    sys.exit(2)
print("上游已就绪")

with TestClient(app) as client:
    card = client.post("/v1/cards/import", json=CARD).json()
    print(f"角色卡 {card['id']}")

    print("=== 装配预览（不带 preset_id） ===")
    preview = client.get(
        "/v1/prompt-preview",
        params={"character_id": card["id"], "user_input": USER_INPUT},
    ).json()
    messages = preview.get("messages") or []
    system_text = "".join(m.get("content", "") for m in messages if m.get("role") == "system")
    user_text = "".join(m.get("content", "") for m in messages if m.get("role") == "user")
    check("回退到内置预设：system 段非空", bool(system_text.strip()), system_text[:120])
    check("回合要求附在本轮输入末尾", "150" in user_text and "300" in user_text, user_text[-160:])
    check("回合要求写明对白要求", "引号" in user_text, user_text[-160:])

    print("=== 生成回合（不带 preset_id，连跑三轮） ===")
    rounds: list[tuple[int, int, float, str]] = []
    for index in range(3):
        started = time.perf_counter()
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "xw-local-default",
                "messages": [{"role": "user", "content": USER_INPUT}],
                "stream": False,
                "xw": {
                    "character_id": card["id"],
                    "memory_mode": "none",
                    "max_context_tokens": 4096,
                },
            },
        )
        elapsed = time.perf_counter() - started
        check(f"第 {index + 1} 轮状态码 200", resp.status_code == 200, f"{resp.status_code} {resp.text[:160]}")
        if resp.status_code != 200:
            continue
        body = resp.json()
        text = body["choices"][0]["message"]["content"]
        dialogues = DIALOGUE_RE.findall(text)
        rounds.append((len(text), len(dialogues), elapsed, text))
        print(f"第 {index + 1} 轮：{len(text)} 字，对白 {len(dialogues)} 段，等待 {elapsed:.2f} s")
        print(text)
        print()

    check("三轮都拿到回合", len(rounds) == 3, str(len(rounds)))
    in_band = [item for item in rounds if MIN_CHARS <= item[0] <= MAX_CHARS]
    with_dialogue = [item for item in rounds if item[1] >= 1]
    check("三轮都落在 150 到 300 字", len(in_band) == len(rounds), str([item[0] for item in rounds]))
    check("三轮都带中文引号对白", len(with_dialogue) == len(rounds), str([item[1] for item in rounds]))
    check("不复述用户输入", all("你还在地址U吗" not in item[3] for item in rounds), "")

print()
if failures:
    print(f"FAILURES: {len(failures)} -> {failures}")
    sys.exit(1)
print("BUILTIN PRESET VERIFIED")
