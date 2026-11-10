"""世界状态 HTTP 路由。

状态由模型每轮回复自动更新并落盘，这里提供读取与角色换区域时的搬迁。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import store
from app.errors import ApiError
from app.state import relocate_state, state_payload
from app.worldbooks import find_region

router = APIRouter(tags=["state"])


@router.get("/")
def get_world_state(character_id: str | None = None) -> JSONResponse:
    """角色级世界状态。

    状态按角色存：每个人有自己的位置与随身物品。没有状态时返回字段齐备
    的空对象，不报 404，前端可直接绑定。
    """
    state = store.get_state(character_id or store.GLOBAL_SCOPE)
    return JSONResponse(content=state_payload(state, character_id or store.GLOBAL_SCOPE))


@router.post("/relocate")
def relocate_world_state(payload: dict[str, Any]) -> JSONResponse:
    """角色换区域：场景随人走，随身物品与时间不动。

    对话里换区域时角色也换了地方，原来的场景陈设属于上一个地方，
    留着会被当成新地方的实情，模型照着写就把旧场景带过来了。
    地点与场景物品按新区域重置，随身物品留着，下一轮由模型按区域设定
    重建场景。
    """
    character_id = str(payload.get("character_id") or store.GLOBAL_SCOPE)
    region_id = str(payload.get("region_id") or "").strip()
    label = str(payload.get("region") or "").strip()
    if not label and region_id:
        found = find_region(region_id)
        if found is not None:
            label = str(found.get("label") or "")
    if not label:
        raise ApiError(code="region_required", message="缺少区域", status_code=400)
    state = relocate_state(store.get_state(character_id), label, region_id)
    saved = store.save_state(state, character_id)
    return JSONResponse(content=state_payload(saved, character_id))
