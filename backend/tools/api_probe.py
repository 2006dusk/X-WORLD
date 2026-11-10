"""端到端接口探针。

在临时数据目录上跑一遍主要端点，验证文件化数据层与接口契约。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

probe_dir = Path(tempfile.mkdtemp(prefix="xw_probe_"))
os.environ["XWORLD_DATA_DIR"] = str(probe_dir)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

CARD = {
    "name": "角色K",
    "description": "在地址N经营地址U的调音师",
    "personality": "沉默",
    "scenario": "雨夜的地址N",
    "first_mes": "门铃响了一声。",
    "mes_example": "<START>",
}

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "OK  " if condition else "FAIL"
    print(f"[{mark}] {label}{(' -> ' + detail) if detail else ''}")
    if not condition:
        failures.append(label)


with TestClient(app) as client:
    print("=== 基础 ===")
    resp = client.get("/healthz")
    check("healthz", resp.status_code == 200, resp.text.strip())

    print("=== 角色卡 ===")
    resp = client.post("/v1/cards/import", json=CARD)
    check("卡片导入", resp.status_code == 200, resp.text[:160])
    card_id = resp.json().get("id") if resp.status_code == 200 else None
    resp = client.get("/v1/cards/")
    check("卡片列表", resp.status_code == 200 and len(resp.json()["items"]) == 1)
    resp = client.get(f"/v1/cards/{card_id}")
    check("卡片详情", resp.status_code == 200 and resp.json()["data"]["name"] == "角色K")
    resp = client.get(f"/v1/cards/{card_id}/export", params={"format": "json"})
    check("卡片 JSON 导出", resp.status_code == 200 and "角色K" in resp.text)
    resp = client.get(f"/v1/cards/{card_id}/export", params={"format": "md"})
    check("卡片 Markdown 导出", resp.status_code == 200 and "## 描述" in resp.text)

    print("=== 世界书 ===")
    resp = client.post("/v1/world-books/", json={"name": "地址N", "entries": []})
    check("世界书新建", resp.status_code == 200, resp.text[:160])
    book_id = resp.json().get("id") if resp.status_code == 200 else None
    resp = client.post(
        f"/v1/world-books/{book_id}/entries",
        json={"keys": ["王都"], "content": "王都的设定", "priority": 10},
    )
    check("条目新增", resp.status_code == 200, resp.text[:160])
    resp = client.post(f"/v1/world-books/{book_id}/match", json={"text": "我们前往王都"})
    check("条目匹配", resp.status_code == 200 and resp.json()["items"], resp.text[:160])

    print("=== 预设 ===")
    resp = client.post(
        "/v1/presets/",
        json={
            "name": "基础预设",
            "prompts": [
                {"name": "主提示", "content": "你是一个角色", "role": "system", "order": 0}
            ],
        },
    )
    check("预设新建", resp.status_code == 200, resp.text[:160])
    preset_id = resp.json().get("id") if resp.status_code == 200 else None
    resp = client.get(f"/v1/presets/{preset_id}/prompts")
    check("提示组分组", resp.status_code == 200 and resp.json()["system"], resp.text[:160])

    print("=== 会话 ===")
    resp = client.post("/v1/sessions/", json={"character_id": card_id, "preset_id": preset_id})
    check("会话创建", resp.status_code == 200, resp.text[:160])
    session_id = resp.json().get("id") if resp.status_code == 200 else None
    resp = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "你好"},
        params={"character_id": card_id},
    )
    check("消息追加", resp.status_code == 200, resp.text[:160])
    resp = client.get(f"/v1/sessions/{session_id}/messages", params={"character_id": card_id})
    check("消息读取", resp.status_code == 200 and resp.json()["count"] == 1, resp.text[:160])
    resp = client.get(f"/v1/sessions/{session_id}/export", params={"character_id": card_id})
    check("会话导出", resp.status_code == 200 and "你好" in resp.text)

    print("=== 记忆与索引 ===")
    resp = client.post(
        "/v1/memory/extract",
        json={"session_id": session_id, "character_id": card_id},
    )
    check("记忆抽取", resp.status_code == 200, resp.text[:160])
    resp = client.post("/v1/memory/search", json={"query": "角色K", "top_k": 3})
    check("记忆检索", resp.status_code == 200, resp.text[:160])
    resp = client.get("/v1/memory/stats")
    check("记忆统计", resp.status_code == 200, resp.text[:160])

    print("=== 装配预览 ===")
    resp = client.get(
        "/v1/prompt-preview",
        params={
            "character_id": card_id,
            "preset_id": preset_id,
            "world_book_ids": book_id,
            "user_input": "继续",
        },
    )
    check("装配预览", resp.status_code == 200, resp.text[:240])

    print("=== 插件 ===")
    resp = client.get("/v1/plugins/")
    check("插件列表", resp.status_code == 200, resp.text[:160])
    resp = client.get("/v1/plugins/hooks")
    check("hook 列表", resp.status_code == 200, resp.text[:160])

    print("=== 模型与 profile ===")
    resp = client.get("/v1/models")
    check("模型清单", resp.status_code == 200, resp.text[:160])
    resp = client.get("/v1/tokenizers")
    check("分词器清单", resp.status_code == 200, resp.text[:160])

print()
if failures:
    print(f"FAILURES: {len(failures)} -> {failures}")
    sys.exit(1)
print("ALL PROBES PASSED")
