"""全局设置 HTTP 路由。

设置以 JSON 文件形式存放于数据目录的 settings.json，经 app.store 读写。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import store as file_store
from app.errors import ApiError

router = APIRouter(tags=["settings"])

PUBLIC_KEYS = (
    "inference_url",
    "active_model",
    "active_profile",
    "default_character_id",
    "default_preset_id",
    "memory_mode",
    "max_context_tokens",
    "reserved_output_tokens",
    "history_policy",
    "persona",
    "bm25_weight",
    "vector_weight",
    "audit_enabled",
    "theme",
)


@router.get("/")
def list_settings() -> JSONResponse:
    """列出全部设置。"""
    settings = file_store.read_json(file_store.settings_path(), {})
    if not isinstance(settings, dict):
        settings = {}
    return JSONResponse(content={"items": settings, "keys": list(PUBLIC_KEYS)})


@router.get("/{key}")
def get_setting(key: str) -> JSONResponse:
    """读取单项设置。"""
    settings = file_store.read_json(file_store.settings_path(), {})
    if not isinstance(settings, dict) or key not in settings:
        raise ApiError(code="setting_not_found", message="设置项不存在", status_code=404)
    return JSONResponse(content={"key": key, "value": settings[key]})


@router.put("/{key}")
def put_setting(key: str, payload: dict[str, Any]) -> JSONResponse:
    """写入单项设置。"""
    if "value" not in payload:
        raise ApiError(code="setting_invalid", message="缺少 value 字段")
    file_store.set_setting(key, payload["value"])
    return JSONResponse(content={"ok": True, "key": key, "value": payload["value"]})


@router.post("/")
def post_settings(payload: dict[str, Any]) -> JSONResponse:
    """批量写入设置。"""
    if not isinstance(payload, dict):
        raise ApiError(code="setting_invalid", message="设置体必须是 JSON 对象")
    for key, value in payload.items():
        file_store.set_setting(str(key), value)
    settings = file_store.read_json(file_store.settings_path(), {})
    return JSONResponse(content={"ok": True, "items": settings})
