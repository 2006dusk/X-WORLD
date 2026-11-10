"""角色卡 HTTP 路由。"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app.cards import (
    export_card_json,
    export_card_markdown,
    get_card,
    import_card_json,
    list_cards,
    save_card,
)
from app.cards.spec import ROLE_PRIMARY, TAG_VOCABULARY
from app.errors import ApiError
from app.store import get_state
from app.worldbooks import refresh_card_refs

router = APIRouter(tags=["cards"])


@router.get("/")
def list_cards_endpoint(role: str = "", pending: str = "") -> JSONResponse:
    """列出角色卡。

    每张卡带上角色绑定的地区：他人在哪，切换人物时对话的位置跟着他走。
    role 参数按角色定位过滤：primary 是主要人物，supporting 是对话中由
    模型引入的配角。认不出来的取值不匹配任何卡，返回空列表而不是主角卡。
    pending 参数按待完善过滤：pending=1 只给还没补全的配角卡。
    """
    items = list_cards()
    if role:
        wanted = str(role).strip().lower()
        items = [
            item
            for item in items
            if str(item.get("role") or ROLE_PRIMARY).lower() == wanted
        ]
    if pending:
        wanted_pending = str(pending).strip().lower() in ("1", "true", "yes")
        items = [item for item in items if bool(item.get("pending")) == wanted_pending]
    for item in items:
        state = get_state(str(item.get("id") or "")) or {}
        item["region_id"] = str(state.get("region_id") or "")
    return JSONResponse(content={"items": items})


@router.get("/tags")
def list_card_tags_endpoint() -> JSONResponse:
    """列出角色卡的受控标签词表。

    标签进提示词时写成「角色类型标签（必须符合）：……」，写什么就是给模型
    下了什么类型的约束，所以词表要能对得上、粒度要一致。编辑器拿它做候选
    标签，卡片里就不容易各处乱写。
    """
    return JSONResponse(content={"items": list(TAG_VOCABULARY)})


@router.post("/{id}/autofill")
def autofill_card_endpoint(id: str, payload: dict[str, Any] | None = None) -> JSONResponse:
    """让模型补全这张配角卡。

    自动建出来的配角卡只有名字，三层设定与状态是空的，补全由模型写。
    补全成功卡就摘掉待完善标记；失败则留在待完善，并把原因记在卡上。
    """
    card = get_card(id)
    if card is None:
        raise ApiError(code="card_not_found", message="角色卡不存在", status_code=404)
    context = ""
    if isinstance(payload, dict):
        context = str(payload.get("context") or "")
    from app.cards.auto_fill import fill_card

    result = fill_card(id, context)
    if not result.get("ok"):
        raise ApiError(
            code="card_fill_failed",
            message=str(result.get("error") or "补全失败"),
            status_code=502,
        )
    saved = result.get("card")
    if isinstance(saved, dict):
        refresh_card_refs(id)
    return JSONResponse(content=saved or card)


@router.get("/{id}")
def get_card_endpoint(id: str) -> JSONResponse:
    """读取角色卡。"""
    card = get_card(id)
    if card is None:
        raise ApiError(code="card_not_found", message="角色卡不存在", status_code=404)
    return JSONResponse(content=card)


@router.post("/")
def create_card_endpoint(payload: dict[str, Any]) -> JSONResponse:
    """新建角色卡。"""
    card = save_card(payload)
    refresh_card_refs(str(card.get("id") or ""))
    return JSONResponse(content=card)


@router.put("/{id}")
def update_card_endpoint(id: str, payload: dict[str, Any]) -> JSONResponse:
    """更新角色卡。"""
    current = get_card(id)
    if current is None:
        raise ApiError(code="card_not_found", message="角色卡不存在", status_code=404)
    merged = dict(payload)
    merged["id"] = id
    merged.setdefault("data", current.get("data"))
    merged.setdefault("spec_version", current.get("spec_version"))
    merged.setdefault("name", current.get("name"))
    card = save_card(merged)
    # 世界书里引用了这张卡的条目要跟着更新：引用是活的。
    refresh_card_refs(id)
    return JSONResponse(content=card)


@router.delete("/{id}")
def delete_card_endpoint(id: str) -> JSONResponse:
    """删除角色卡。"""
    from app.cards import delete_card

    if not delete_card(id):
        raise ApiError(code="card_not_found", message="角色卡不存在", status_code=404)
    refresh_card_refs(id)
    return JSONResponse(content={"ok": True, "id": id})


@router.post("/import")
async def import_card_endpoint(request: Request) -> JSONResponse:
    """导入角色卡，接受 JSON 正文或 multipart 上传文件。"""
    content_type = request.headers.get("content-type", "")
    raw: bytes
    if "multipart/form-data" in content_type:
        form = await request.form()
        upload = form.get("file")
        if upload is not None and hasattr(upload, "read"):
            raw = await upload.read()
        else:
            text = form.get("json_body")
            raw = str(text).encode("utf-8") if text else b""
    else:
        raw = await request.body()
    if not raw:
        raise ApiError(code="card_invalid", message="缺少角色卡内容")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiError(code="card_invalid", message=f"角色卡解析失败：{exc}") from exc
    card = import_card_json(data)
    refresh_card_refs(str(card.get("id") or ""))
    return JSONResponse(content=card)


@router.get("/{id}/export")
def export_card_endpoint(id: str, format: str = "json") -> Response:
    """导出角色卡，format 取 json 或 md。"""
    if format == "md":
        text = export_card_markdown(id)
        return Response(
            content=text.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{id}.md"'},
        )
    payload = export_card_json(id)
    return Response(
        content=payload,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{id}.json"'},
    )
