"""预设 HTTP 路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.errors import ApiError
from app.presets import (
    delete_preset,
    get_preset,
    list_presets,
    list_prompt_groups,
    save_preset,
    split_prompt_groups,
)

router = APIRouter(tags=["presets"])


@router.get("/")
def list_presets_endpoint() -> JSONResponse:
    """列出预设。"""
    return JSONResponse(content={"items": list_presets()})


@router.get("/{id}")
def get_preset_endpoint(id: str) -> JSONResponse:
    """读取预设。"""
    preset = get_preset(id)
    if preset is None:
        raise ApiError(code="preset_not_found", message="预设不存在", status_code=404)
    return JSONResponse(content=preset)


@router.get("/{id}/prompts")
def list_prompts_endpoint(id: str) -> JSONResponse:
    """列出预设提示组。"""
    system, others = split_prompt_groups(id)
    return JSONResponse(content={"system": system, "others": others, "all": list_prompt_groups(id)})


@router.post("/")
def create_preset_endpoint(payload: dict[str, Any]) -> JSONResponse:
    """新建预设。"""
    return JSONResponse(content=save_preset(payload))


@router.put("/{id}")
def update_preset_endpoint(id: str, payload: dict[str, Any]) -> JSONResponse:
    """更新预设。"""
    current = get_preset(id)
    if current is None:
        raise ApiError(code="preset_not_found", message="预设不存在", status_code=404)
    merged = dict(payload)
    merged["id"] = id
    merged.setdefault("prompts", current.get("prompts"))
    merged.setdefault("name", current.get("name"))
    return JSONResponse(content=save_preset(merged))


@router.delete("/{id}")
def delete_preset_endpoint(id: str) -> JSONResponse:
    """删除预设。"""
    if not delete_preset(id):
        raise ApiError(code="preset_not_found", message="预设不存在", status_code=404)
    return JSONResponse(content={"ok": True, "id": id})
