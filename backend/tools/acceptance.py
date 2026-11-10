r"""全栈验收：真实 uvicorn 进程 + 契约端点 + 前端静态托管。

用法：
    python backend\tools\acceptance.py

在临时数据目录上启动后端子进程，逐项校验契约第 4 节端点；
frontend/dist 存在时校验根路径静态托管。前端使用 hash 路由，
因此无需 SPA 回退，只需根路径返回构建产物。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "OK  " if condition else "FAIL"
    print(f"[{mark}] {label}{(' -> ' + detail) if detail else ''}")
    if not condition:
        failures.append(label)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_ready(url: str, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{url}/healthz", timeout=2.0).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def main() -> int:
    data_dir = Path(tempfile.mkdtemp(prefix="xw_accept_"))
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ)
    env["XWORLD_DATA_DIR"] = str(data_dir)
    env["PYTHONPATH"] = str(BACKEND)

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(BACKEND),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_ready(base):
            print("后端未在 60 秒内就绪")
            return 1

        with httpx.Client(base_url=base, timeout=30.0) as c:
            print("=== 基础与设置 ===")
            r = c.get("/healthz")
            check("healthz", r.status_code == 200 and r.json().get("ok") is True, r.text[:120])
            r = c.put("/v1/settings/theme", json={"value": "dark"})
            check("设置单项写入", r.status_code == 200, r.text[:120])
            r = c.get("/v1/settings/theme")
            check("设置单项读取", r.status_code == 200 and r.json()["value"] == "dark", r.text[:120])
            r = c.post("/v1/settings/", json={"memory_mode": "hybrid", "max_context_tokens": 8192})
            check("设置批量写入", r.status_code == 200, r.text[:120])
            r = c.get("/v1/settings/")
            check("设置列表", r.status_code == 200 and "items" in r.json(), r.text[:120])
            r = c.get("/v1/settings/does_not_exist")
            check("设置缺失返回 404", r.status_code == 404, r.text[:120])

            print("=== 角色卡 ===")
            r = c.post(
                "/v1/cards/import",
                json={
                    "name": "角色K",
                    "description": "地址N地址U调音师",
                    "personality": "沉默",
                    "scenario": "雨夜",
                    "first_mes": "门铃响了一声。",
                    "mes_example": "<START>",
                },
            )
            check("卡片导入", r.status_code == 200, r.text[:160])
            card_id = r.json().get("id")
            r = c.get("/v1/cards/")
            check("卡片列表", r.status_code == 200 and len(r.json()["items"]) == 1, r.text[:160])
            r = c.get(f"/v1/cards/{card_id}")
            body = r.json()
            check(
                "卡片详情",
                r.status_code == 200 and "角色K" in json.dumps(body, ensure_ascii=False),
                r.text[:160],
            )
            r = c.put(f"/v1/cards/{card_id}", json={"name": "角色K", "description": "改写后的描述"})
            check("卡片更新", r.status_code == 200, r.text[:160])
            r = c.get(f"/v1/cards/{card_id}/export", params={"format": "json"})
            check("卡片 JSON 导出", r.status_code == 200 and "角色K" in r.text, r.text[:160])
            r = c.get(f"/v1/cards/{card_id}/export", params={"format": "md"})
            check(
                "卡片 Markdown 导出",
                r.status_code == 200 and "## " in r.text and "角色K" in r.text,
                r.text[:160],
            )

            print("=== 世界书 ===")
            r = c.post("/v1/world-books/", json={"name": "地址N", "entries": []})
            check("世界书新建", r.status_code == 200, r.text[:160])
            book_id = r.json().get("id")
            r = c.post(
                f"/v1/world-books/{book_id}/entries",
                json={"keys": ["王都"], "content": "王都的设定", "priority": 10},
            )
            check("条目新增", r.status_code == 200, r.text[:160])
            entry_id = r.json().get("id")
            r = c.put(
                f"/v1/world-books/{book_id}/entries/{entry_id}",
                json={"keys": ["王都"], "content": "改写后的设定", "priority": 12},
            )
            check("条目更新", r.status_code == 200, r.text[:160])
            r = c.post(f"/v1/world-books/{book_id}/match", json={"text": "我们前往王都"})
            check("条目匹配", r.status_code == 200 and bool(r.json()["items"]), r.text[:160])
            r = c.delete(f"/v1/world-books/{book_id}/entries/{entry_id}")
            check("条目删除", r.status_code == 200, r.text[:160])
            r = c.delete(f"/v1/world-books/{book_id}")
            check("世界书删除", r.status_code == 200, r.text[:160])

            print("=== 预设 ===")
            r = c.post(
                "/v1/presets/",
                json={
                    "name": "基础预设",
                    "prompts": [
                        {"name": "主提示", "content": "你是一个角色", "role": "system", "order": 0}
                    ],
                },
            )
            check("预设新建", r.status_code == 200, r.text[:160])
            preset_id = r.json().get("id")
            r = c.get("/v1/presets/")
            check("预设列表", r.status_code == 200 and bool(r.json()["items"]), r.text[:160])
            r = c.get(f"/v1/presets/{preset_id}/prompts")
            check("提示组分组", r.status_code == 200 and bool(r.json().get("system")), r.text[:160])
            r = c.put(f"/v1/presets/{preset_id}", json={"name": "基础预设改"})
            check("预设更新", r.status_code == 200, r.text[:160])
            r = c.delete(f"/v1/presets/{preset_id}")
            check("预设删除", r.status_code == 200, r.text[:160])

            print("=== 会话 ===")
            r = c.post("/v1/sessions/", json={"character_id": card_id, "title": "验收会话"})
            check("会话创建", r.status_code == 200, r.text[:200])
            session_id = r.json().get("id")
            r = c.get("/v1/sessions/", params={"character_id": card_id})
            check("会话列表", r.status_code == 200 and bool(r.json()["items"]), r.text[:160])
            r = c.post(
                f"/v1/sessions/{session_id}/messages",
                json={"role": "user", "content": "你好"},
            )
            check("会话追加消息", r.status_code == 200, r.text[:200])
            r = c.get(f"/v1/sessions/{session_id}/messages")
            check(
                "会话消息读回",
                r.status_code == 200 and r.json()["count"] == 1 and r.json()["items"][0]["content"] == "你好",
                r.text[:200],
            )
            r = c.post("/v1/sessions/s_not_exist/messages", json={"role": "user", "content": "x"})
            check("向不存在会话追加消息返回 404", r.status_code == 404, r.text[:160])
            r = c.get(f"/v1/sessions/{session_id}/export")
            check("会话 Markdown 导出", r.status_code == 200 and len(r.text) > 0, r.text[:160])
            r = c.delete(f"/v1/sessions/{session_id}")
            check("会话删除", r.status_code == 200, r.text[:160])

            print("=== 记忆 ===")
            r = c.post("/v1/memory/search", json={"query": "角色K", "top_k": 3})
            check("记忆检索", r.status_code == 200, r.text[:160])
            r = c.get("/v1/memory/stats")
            check("记忆统计", r.status_code == 200, r.text[:160])
            r = c.get("/v1/memory/entities")
            check("实体列表", r.status_code == 200, r.text[:160])
            r = c.get("/v1/memory/forget-scan")
            check("遗忘扫描", r.status_code == 200, r.text[:160])

            print("=== 插件 ===")
            r = c.get("/v1/plugins/")
            check("插件列表", r.status_code == 200, r.text[:200])
            plugins = r.json().get("items") or []
            r = c.get("/v1/plugins/hooks")
            check("hook 列表", r.status_code == 200 and bool(r.json()["items"]), r.text[:160])
            r = c.post("/v1/plugins/reload")
            check("插件重载", r.status_code == 200, r.text[:200])
            if plugins:
                pid = plugins[0].get("id") or plugins[0].get("plugin_id")
                r = c.post(f"/v1/plugins/{pid}/disable")
                check("插件停用", r.status_code == 200, r.text[:160])
                r = c.post(f"/v1/plugins/{pid}/enable")
                check("插件启用", r.status_code == 200, r.text[:160])
                r = c.get(f"/v1/plugins/{pid}/log")
                check("插件日志", r.status_code == 200, r.text[:160])

            print("=== 模型与装配 ===")
            r = c.get("/v1/models")
            check("模型清单", r.status_code == 200, r.text[:160])
            r = c.get("/v1/profiles")
            check("profile 列表", r.status_code == 200, r.text[:160])
            r = c.get("/v1/tokenizers")
            check("分词器清单", r.status_code == 200, r.text[:160])
            r = c.get(
                "/v1/prompt-preview",
                params={
                    "character_id": card_id,
                    "preset_id": "",
                    "world_book_ids": "",
                    "user_input": "继续",
                },
            )
            check("装配预览", r.status_code == 200, r.text[:240])

            print("=== 前端静态托管 ===")
            dist = ROOT / "frontend" / "dist" / "index.html"
            if dist.is_file():
                r = c.get("/")
                check(
                    "根路径返回构建产物",
                    r.status_code == 200 and "text/html" in r.headers.get("content-type", ""),
                    r.headers.get("content-type", "")[:80],
                )
                check("根路径含挂载点", "<div id=" in r.text, r.text[:120])
                assets = list((ROOT / "frontend" / "dist" / "assets").glob("*.js"))
                if assets:
                    r = c.get(f"/assets/{assets[0].name}")
                    check("静态资源可取", r.status_code == 200, assets[0].name)
            else:
                print("[SKIP] frontend/dist 不存在，跳过静态托管检查")

        print()
        if failures:
            print(f"FAILURES: {len(failures)} -> {failures}")
            return 1
        print("ALL ACCEPTANCE CHECKS PASSED")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
