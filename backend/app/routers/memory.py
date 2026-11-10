"""记忆相关 HTTP 路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.errors import ApiError
from app.memory.extract import extract_from_messages
from app.memory.retrieval import forget_scan, hybrid_search
from app.memory.store import (
    archive_memory,
    get_entity,
    get_memory,
    list_entities,
    list_memories,
    save_entity,
)
from app.store import get_messages

router = APIRouter(tags=["memory"])


@router.get("/")
def list_memory_items(
    character_id: str | None = None,
    session_id: str | None = None,
    kind: str | None = None,
    archived: bool = False,
) -> JSONResponse:
    """记忆条目列表，可按角色、会话、类型与归档状态过滤。"""
    items = list_memories(
        character_id=character_id,
        session_id=session_id,
        kind=kind,
        include_archived=archived,
    )
    return JSONResponse(content={"items": items})


@router.get("/stats")
def memory_stats(character_id: str | None = None) -> JSONResponse:
    """记忆统计：总数、归档数、分类计数与平均重要度。"""
    items = list_memories(character_id=character_id, include_archived=True)
    kinds: dict[str, int] = {}
    archived = 0
    total_importance = 0.0
    for item in items:
        kind = str(item.get("kind") or "fact")
        kinds[kind] = kinds.get(kind, 0) + 1
        if item.get("archived"):
            archived += 1
        else:
            total_importance += float(item.get("importance") or 0.0)
    active = max(len(items) - archived, 0)
    return JSONResponse(
        content={
            "total": len(items),
            "archived": archived,
            "active": active,
            "by_kind": kinds,
            "average_importance": (total_importance / active) if active else 0.0,
        }
    )


@router.post("/search")
def search_memory(payload: dict[str, Any]) -> JSONResponse:
    """混合检索记忆，BM25 与向量权重可配。"""
    query = str(payload.get("query") or "")
    if not query:
        raise ApiError(code="query_required", message="检索词不能为空")
    hits = hybrid_search(
        query,
        character_id=payload.get("character_id"),
        top_k=int(payload.get("top_k") or 8),
        bm25_weight=float(payload.get("bm25_weight", 0.6)),
        vector_weight=float(payload.get("vector_weight", 0.4)),
        session_id=payload.get("session_id"),
        kind=payload.get("kind"),
        include_archived=bool(payload.get("include_archived")),
    )
    approximate = any(hit.get("approximate") for hit in hits) if hits else True
    return JSONResponse(content={"items": hits, "approximate": approximate})


@router.get("/entities")
def list_entity_items(character_id: str | None = None) -> JSONResponse:
    """实体列表，按角色隔离。"""
    return JSONResponse(content={"items": list_entities(character_id or "global")})


@router.put("/entities/{name}")
def update_entity(name: str, payload: dict[str, Any]) -> JSONResponse:
    """更新实体属性，属性按浅合并处理。"""
    current = get_entity(name, str(payload.get("character_id") or "global")) or {
        "name": name,
        "character_id": payload.get("character_id") or "global",
        "attributes": {},
    }
    attributes = dict(current.get("attributes") or {})
    attributes.update(payload.get("attributes") or {})
    current["attributes"] = attributes
    current["character_id"] = payload.get("character_id") or current.get("character_id")
    return JSONResponse(content=save_entity(current))


@router.post("/extract")
def run_extract(payload: dict[str, Any]) -> JSONResponse:
    """从指定会话的消息里抽取记忆条目。"""
    session_id = payload.get("session_id")
    if not session_id:
        raise ApiError(code="session_required", message="抽取需要 session_id")
    messages = get_messages(str(session_id), payload.get("character_id"))
    items = extract_from_messages(messages, payload.get("character_id"), str(session_id))
    return JSONResponse(content={"items": items, "count": len(items)})


@router.get("/forget-scan")
def scan_forgettable(
    character_id: str | None = None,
    threshold: float = 0.3,
    days: int = 30,
) -> JSONResponse:
    """扫描可遗忘的记忆，按重要度阈值与时间窗过滤。"""
    items = forget_scan(character_id=character_id, threshold=threshold, days=days)
    return JSONResponse(content={"items": items, "count": len(items)})


@router.get("/{memory_id}")
def get_memory_item(memory_id: str, character_id: str | None = None) -> JSONResponse:
    """按 id 取单条记忆。"""
    item = get_memory(memory_id, character_id)
    if item is None:
        raise ApiError(code="memory_not_found", message="记忆不存在", status_code=404)
    return JSONResponse(content=item)


@router.delete("/{memory_id}")
def archive_memory_item(memory_id: str, character_id: str | None = None) -> JSONResponse:
    """归档记忆条目，条目不直接删除。"""
    if not archive_memory(memory_id, character_id):
        raise ApiError(code="memory_not_found", message="记忆不存在", status_code=404)
    return JSONResponse(content={"ok": True, "id": memory_id})
