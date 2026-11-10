# -*- coding: utf-8 -*-
"""V2 配角探针共用的后端访问helper。

probe_v2_supporting.py 与 probe_v2_long_context.py 都要做同一几件事：
打后端接口、按名字找配角卡、列配角卡 id、等 AI 补全把待完善标记摘掉、
删卡。这些函数原来在两个文件里各写一份，改一处漏一处。这里收成一处，
两个探针都从这里取。

用法（探针脚本里）：
    from probe_supporting_common import http, find_supporting_card
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

BASE = os.environ.get("XW_BACKEND", "http://127.0.0.1:8760")

# 等 AI 补全的默认上限（秒）。补全要跑一轮本地推理，慢，给足时间。
FILL_WAIT_SECONDS = 240


def http(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 60.0,
) -> Any:
    """打后端接口，返回解析后的 JSON。"""
    response = httpx.request(method, BASE + path, json=payload, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def find_supporting_card(name: str) -> dict[str, Any] | None:
    """按名字找配角卡，找不到返回 None。"""
    items = http("GET", "/v1/cards/", params={"role": "supporting"}).get("items", [])
    for item in items:
        if str(item.get("name") or "") == name:
            return http("GET", "/v1/cards/" + str(item.get("id") or ""))
    return None


def supporting_ids() -> set[str]:
    """当前所有配角卡的 id。"""
    items = http("GET", "/v1/cards/", params={"role": "supporting"}).get("items", [])
    return {str(item.get("id") or "") for item in items if item.get("id")}


def wait_for_fill(card_id: str, limit: float = FILL_WAIT_SECONDS) -> dict[str, Any] | None:
    """等 AI 补全把待完善标记摘掉。超时返回 None。"""
    deadline = time.perf_counter() + limit
    while time.perf_counter() < deadline:
        card = http("GET", "/v1/cards/" + card_id)
        data = card.get("data") or {}
        if not data.get("pending"):
            return card
        time.sleep(2.0)
    return None


def delete_card(card_id: str) -> bool:
    """删卡。刚写完的文件可能被占，失败就重试几次。"""
    for _ in range(5):
        try:
            http("DELETE", "/v1/cards/" + card_id)
            return True
        except Exception:
            time.sleep(0.6)
    return False
