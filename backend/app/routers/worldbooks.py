"""世界书 HTTP 路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.errors import ApiError
from app.worldbooks import (
    create_entry,
    delete_book,
    delete_entry,
    get_book,
    list_books,
    list_entries,
    list_regions,
    match_entries,
    save_book,
    update_entry,
)

router = APIRouter(tags=["worldbooks"])


@router.get("/")
def list_books_endpoint() -> JSONResponse:
    """列出世界书。"""
    return JSONResponse(content={"items": list_books()})


@router.get("/{id}")
def get_book_endpoint(id: str) -> JSONResponse:
    """读取世界书。"""
    book = get_book(id)
    if book is None:
        raise ApiError(code="world_book_not_found", message="世界书不存在", status_code=404)
    return JSONResponse(content=book)


@router.post("/")
def create_book_endpoint(payload: dict[str, Any]) -> JSONResponse:
    """新建世界书。"""
    return JSONResponse(content=save_book(payload))


@router.put("/{id}")
def update_book_endpoint(id: str, payload: dict[str, Any]) -> JSONResponse:
    """更新世界书。"""
    current = get_book(id)
    if current is None:
        raise ApiError(code="world_book_not_found", message="世界书不存在", status_code=404)
    merged = dict(payload)
    merged["id"] = id
    merged.setdefault("entries", current.get("entries"))
    merged.setdefault("name", current.get("name"))
    return JSONResponse(content=save_book(merged))


@router.delete("/{id}")
def delete_book_endpoint(id: str) -> JSONResponse:
    """删除世界书。"""
    if not delete_book(id):
        raise ApiError(code="world_book_not_found", message="世界书不存在", status_code=404)
    return JSONResponse(content={"ok": True, "id": id})


@router.get("/{id}/regions")
def list_regions_endpoint(id: str) -> JSONResponse:
    """列出世界书里的区域条目。

    区域是世界的地点，新建对话时选一个作为这段对话的所在位置。
    """
    return JSONResponse(content={"items": list_regions(id)})


@router.post("/{id}/entries")
def create_entry_endpoint(id: str, payload: dict[str, Any]) -> JSONResponse:
    """新增世界书条目。"""
    return JSONResponse(content=create_entry(id, payload))


@router.put("/{id}/entries/{entry_id}")
def update_entry_endpoint(id: str, entry_id: str, payload: dict[str, Any]) -> JSONResponse:
    """更新世界书条目。"""
    return JSONResponse(content=update_entry(id, entry_id, payload))


@router.delete("/{id}/entries/{entry_id}")
def delete_entry_endpoint(id: str, entry_id: str) -> JSONResponse:
    """删除世界书条目。"""
    if not delete_entry(id, entry_id):
        raise ApiError(code="world_entry_not_found", message="世界书条目不存在", status_code=404)
    return JSONResponse(content={"ok": True, "id": entry_id})


@router.post("/{id}/match")
def match_book_endpoint(id: str, payload: dict[str, Any]) -> JSONResponse:
    """匹配测试。"""
    entries = list_entries(id)
    hits = match_entries(
        entries,
        str(payload.get("text") or ""),
        secondary_text=str(payload.get("secondary_text") or ""),
    )
    return JSONResponse(
        content={
            "items": [hit.get("id") for hit in hits],
            "entries": hits,
        }
    )
