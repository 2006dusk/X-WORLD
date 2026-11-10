"""内置预设多轮推进验证。

单轮写得好不等于能推进剧情。本脚本走客户端真实链路：建会话、追加用户消息、
调用生成接口、追加助手消息，逐轮记录长度、对白、关键词与与前文的内容重合度。

全程不带 preset_id，预设解析走「默认预设 → 内置预设」的回退链。

要求本地 llama.cpp 兼容服务已在 127.0.0.1:8080 启动。

用法：
    F:/python/python.exe backend/tools/verify_builtin_multiturn.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

probe_dir = Path(tempfile.mkdtemp(prefix="xw_multiturn_"))
os.environ["XWORLD_DATA_DIR"] = str(probe_dir)

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

UPSTREAM = os.environ.get("XW_UPSTREAM", "http://127.0.0.1:8080")
DIALOGUE_RE = re.compile(r"[“「『]([^”」』]*)")
KEYWORDS = ("角色K", "地址U", "磁带", "雨", "台灯")
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

TURNS = [
    "我推开门，手里攥着一盘无人认领的磁带，把它放在调音台上。",
    "磁带开始转动，传出女人的哼唱。我伸手去按停止键，指尖停在按钮上方。",
    "便签上的名字我认出来了，是我妹妹的笔迹。她三年前失踪了。",
    "磁带里忽然出现第二个声音，它在叫我的名字，一遍又一遍。",
]


def grams(text: str, size: int = 8) -> set[str]:
    """取定长片段集合，用于算两轮内容重合度。"""
    return {text[index : index + size] for index in range(max(len(text) - size, 0))}


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


failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    """记录一条验收结论。"""
    print(f"[{'OK  ' if condition else 'FAIL'}] {label}{(' -> ' + detail) if detail else ''}")
    if not condition:
        failures.append(label)


print("=== 等待本地推理服务 ===")
if not wait_upstream():
    print("上游推理服务未就绪，终止")
    sys.exit(2)
print("上游已就绪")

previous: set[str] = set()
rows: list[tuple[int, int, float, int, int]] = []
dump: list[dict[str, object]] = []

with TestClient(app) as client:
    card = client.post("/v1/cards/import", json=CARD).json()
    session = client.post(
        "/v1/sessions/", json={"character_id": card["id"], "preset_id": None}
    ).json()
    print(f"角色卡 {card['id']} / 会话 {session['id']}")

    for index, turn in enumerate(TURNS, 1):
        client.post(
            f"/v1/sessions/{session['id']}/messages",
            json={"role": "user", "content": turn},
            params={"character_id": card["id"]},
        )
        started = time.perf_counter()
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "xw-local-default",
                "messages": [{"role": "user", "content": turn}],
                "session": session["id"],
                "stream": False,
                "xw": {
                    "character_id": card["id"],
                    "memory_mode": "none",
                    "max_context_tokens": 4096,
                },
            },
        )
        elapsed = time.perf_counter() - started
        check(f"第 {index} 轮状态码 200", resp.status_code == 200, f"{resp.status_code} {resp.text[:160]}")
        if resp.status_code != 200:
            continue
        text = resp.json()["choices"][0]["message"]["content"]
        client.post(
            f"/v1/sessions/{session['id']}/messages",
            json={"role": "assistant", "content": text},
            params={"character_id": card["id"]},
        )
        dialogues = DIALOGUE_RE.findall(text)
        hits = sum(1 for word in KEYWORDS if word in text)
        current = grams(text)
        overlap = 0.0
        if previous:
            overlap = len(current & previous) / max(len(current), 1)
        previous = current
        rows.append((len(text), len(dialogues), elapsed, hits, int(overlap * 100)))
        dump.append({"turn": index, "chars": len(text), "dialogues": len(dialogues), "wait_s": round(elapsed, 2), "keywords": [w for w in KEYWORDS if w in text], "overlap_pct": int(overlap * 100), "text": text})
        print(f"第 {index} 轮：{len(text)} 字，对白 {len(dialogues)} 段，关键词 {hits} 个，"
              f"与前文重合 {int(overlap * 100)}%，等待 {elapsed:.2f} s")
        print(text)
        print()

out_dir = Path(__file__).resolve().parent.parent.parent.parent / "docs" / "实践情况" / "data"
out_dir.mkdir(parents=True, exist_ok=True)
(out_dir / "verify_builtin_multiturn.json").write_text(
    json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8"
)

check("四轮都拿到回合", len(rows) == 4, str(len(rows)))
if rows:
    check("每轮都落在 150 到 300 字", all(MIN_CHARS <= row[0] <= MAX_CHARS for row in rows),
          str([row[0] for row in rows]))
    check("每轮都带中文引号对白", all(row[1] >= 1 for row in rows), str([row[1] for row in rows]))
    check("每轮都命中场景关键词", all(row[3] >= 1 for row in rows), str([row[3] for row in rows]))
    check("没有逐轮缩短", rows[-1][0] >= rows[0][0] * 0.6, str([row[0] for row in rows]))
    # 复述上一轮是 4B 档的已知短板，单列为观察项，不计入硬性失败。
    repeat_pct = [row[4] for row in rows]
    check("与前文重合低于 50%", all(row[4] < 50 for row in rows), str(repeat_pct))
    if any(row[4] >= 50 for row in rows):
        print("注意：有轮次复述上一轮，重合度 " + str(repeat_pct))

print()
if failures:
    print(f"FAILURES: {len(failures)} -> {failures}")
    sys.exit(1)
print("MULTI TURN VERIFIED")
