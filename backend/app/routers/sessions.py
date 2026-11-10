"""会话 HTTP 路由。

会话按 character_id 分目录存放，路径形如 sessions/<character_id>/<session_id>.jsonl。
调用方未显式给出 character_id 时，由 _resolve_scope 按会话文件反查所属作用域，
避免消息写到 global 目录而与列表读取不一致。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from app import presets as preset_service
from app import store as file_store
from app.errors import ApiError
from app.inference.structure import block_names, display_labels
from app.state import relocate_state
from app.worldbooks import find_region

router = APIRouter(tags=["sessions"])


def _relocate_owner(session: dict[str, Any], region_id: str) -> None:
    """换地区：对话的主人搬家，场景随人走。

    地区绑在人身上，不在对话上：换地区就是给这个人挪地方，他绑定的地区
    随之更新。原地点的场景陈设属于上一个地方，留着会被当成新地方的实情。
    随身物品留着，时间不动，场景由下一轮模型按区域设定重建。
    """
    found = find_region(region_id)
    if not found:
        return
    label = str(found.get("label") or "")
    character_id = str(session.get("character_id") or "")
    if not label or not character_id:
        return
    state = file_store.get_state(character_id)
    file_store.save_state(relocate_state(state, label, region_id), character_id)


def _bound_region(character_id: str) -> str:
    """角色绑定的地区：他人在哪。"""
    state = file_store.get_state(character_id) or {}
    return str(state.get("region_id") or "")


def _sync_location(session_id: str, scope: str, character_id: str) -> None:
    """按角色绑定的地区同步对话的世界与区域。

    切换人物时对话的位置跟着他走：不是「在这个地区看到那个人」，
    而是他人在哪，这段对话就在哪。
    """
    region_id = _bound_region(character_id)
    if not region_id:
        return
    found = find_region(region_id)
    if not found:
        return
    book_id = str(found.get("book_id") or "")
    file_store.update_session_meta(
        session_id,
        scope,
        {
            "world_book_ids": [book_id] if book_id else [],
            "region_id": region_id,
        },
    )


def _display_labels(character_id: str | None) -> dict[str, str]:
    """默认预设声明的块名到展示标签。取不到时返回空表。"""
    preset_id = preset_service.resolve_preset_id(None)
    if not preset_id:
        return {}
    preset = preset_service.get_preset(preset_id)
    if not isinstance(preset, dict):
        return {}
    spec = preset.get("output_spec")
    if not isinstance(spec, dict):
        return {}
    return display_labels(block_names(spec.get("structure")))


def _resolve_scope(session_id: str, character_id: str | None) -> str:
    """定位会话所属作用域。

    会话文件自身所在目录优先：会话与角色解耦后，@ 换角色时目录不变，
    按当前发言角色定位会找不到会话。显式传入的 character_id 只在没有
    会话文件时用于新建流程。
    """
    found = file_store.session_scope(session_id)
    if found:
        return found
    if character_id:
        return character_id
    return file_store.GLOBAL_SCOPE


def _session_path(session_id: str, scope: str):
    """会话文件路径。"""
    return file_store.sessions_dir(scope) / f"{session_id}.jsonl"


@router.get("/")
def list_sessions_endpoint(
    character_id: str | None = None,
    world_book_id: str | None = None,
    region_id: str | None = None,
) -> JSONResponse:
    """列出会话。

    character_id 按主角色过滤，world_book_id 按世界过滤，region_id 按
    对话所在的区域过滤。
    """
    items = file_store.list_sessions(character_id)
    if world_book_id:
        items = [
            item
            for item in items
            if world_book_id in (item.get("world_book_ids") or [])
        ]
    if region_id:
        items = [
            item for item in items if str(item.get("region_id") or "") == region_id
        ]
    return JSONResponse(content={"items": items})


@router.post("/")
def create_session_endpoint(payload: dict[str, Any]) -> JSONResponse:
    """创建会话。

    character_id 是主角色，world_book_ids 是会话的世界位置。新建对话先定
    世界位置，再定角色，两个都写进会话头。
    """
    character_id = str(payload.get("character_id") or file_store.GLOBAL_SCOPE)
    preset_id = payload.get("preset_id")
    raw_books = payload.get("world_book_ids")
    world_book_ids = [str(item) for item in raw_books if str(item)] if isinstance(raw_books, list) else []
    raw_characters = payload.get("characters")
    characters = [str(item) for item in raw_characters if str(item)] if isinstance(raw_characters, list) else []
    region_id = str(payload.get("region_id") or "")
    # 角色绑定了地区就以他为准：对话的位置跟着人走
    bound = _bound_region(character_id)
    if bound:
        found = find_region(bound)
        if found:
            region_id = bound
            book_id = str(found.get("book_id") or "")
            if book_id:
                world_book_ids = [book_id]
    canonical_id = file_store.canonical_session_id(character_id)
    scope = file_store.session_scope(canonical_id)
    old_region = ""
    if scope:
        head = file_store.get_session_head(canonical_id, scope)
        old_region = str(head.get("region_id") or "")
    session = file_store.create_session(
        character_id,
        preset_id,
        world_book_ids,
        characters,
        region_id,
    )
    if region_id and region_id != old_region:
        _relocate_owner(session, region_id)
    return JSONResponse(content=session)


@router.put("/{id}")
def update_session_endpoint(id: str, payload: dict[str, Any]) -> JSONResponse:
    """更新会话元信息：世界位置、在场角色、主角色与标题。

    换地区时主人跟着搬家；换人时对话的位置跟着他走。
    """
    scope = _resolve_scope(id, payload.get("character_id"))
    before = file_store.get_session_head(id, scope)
    old_region = str(before.get("region_id") or "")
    old_owner = str(before.get("character_id") or "")
    head = file_store.update_session_meta(id, scope, payload)
    if not head:
        raise ApiError(code="session_not_found", message="会话不存在", status_code=404)
    summary = file_store.session_summary(id) or head
    new_region = str(summary.get("region_id") or "")
    new_owner = str(summary.get("character_id") or "")
    if new_region and new_region != old_region:
        _relocate_owner(summary, new_region)
    elif new_owner and new_owner != old_owner:
        _sync_location(id, scope, new_owner)
        summary = file_store.session_summary(id) or summary
    return JSONResponse(content=summary)


@router.get("/{id}")
def get_session_endpoint(id: str, character_id: str | None = None) -> JSONResponse:
    """读取会话。"""
    session = file_store.session_summary(id)
    if not session:
        raise ApiError(code="session_not_found", message="会话不存在", status_code=404)
    return JSONResponse(content=session)


@router.delete("/{id}")
def delete_session_endpoint(id: str, character_id: str | None = None) -> JSONResponse:
    """删除会话。"""
    scope = _resolve_scope(id, character_id)
    path = _session_path(id, scope)
    if not path.exists():
        raise ApiError(code="session_not_found", message="会话不存在", status_code=404)
    path.unlink()
    return JSONResponse(content={"ok": True, "id": id})


@router.get("/{id}/messages")
def list_messages_endpoint(
    id: str,
    character_id: str | None = None,
    limit: int | None = None,
    path: int | None = None,
    tree: int | None = None,
) -> JSONResponse:
    """列出会话消息。

    不带参数时是文件顺序的全部消息，与旧行为一致。带 path=1 时返回当前
    分支路径的消息，带 tree=1 时附整棵分支树的节点表。
    """
    scope = _resolve_scope(id, character_id)
    if not _session_path(id, scope).exists():
        raise ApiError(code="session_not_found", message="会话不存在", status_code=404)
    if path or tree:
        snapshot = file_store.session_snapshot(id, scope)
        snapshot["labels"] = _display_labels(character_id)
        return JSONResponse(content=snapshot)
    messages = file_store.get_messages(id, scope, limit)
    return JSONResponse(content={"items": messages, "count": len(messages)})


@router.post("/{id}/switch")
def switch_branch_endpoint(
    id: str,
    payload: dict[str, Any],
    character_id: str | None = None,
) -> JSONResponse:
    """把 head 切到指定叶子，返回该分支路径。"""
    scope = _resolve_scope(id, character_id)
    if not _session_path(id, scope).exists():
        raise ApiError(code="session_not_found", message="会话不存在", status_code=404)
    leaf_id = str(payload.get("leaf_id") or "")
    tree = file_store.session_tree(id, scope)
    if leaf_id not in (tree.get("nodes") or {}):
        raise ApiError(code="session_node_not_found", message="节点不存在", status_code=404)
    file_store.set_head(id, scope, leaf_id)
    snapshot = file_store.session_snapshot(id, scope)
    snapshot["labels"] = _display_labels(character_id)
    return JSONResponse(content=snapshot)


@router.delete("/{id}/branches")
def delete_branch_endpoint(
    id: str,
    nodes: str = "",
    character_id: str | None = None,
) -> JSONResponse:
    """删除一条分支的一段。

    nodes 是要删的节点 id，逗号分隔：前端按折叠后的分组算出这一段含哪
    几个节点。删的是支线的一段，不是整段对话：分叉点分出去的其他支线
    照旧留着。返回删完后的当前路径与分支树。
    """
    scope = _resolve_scope(id, character_id)
    if not _session_path(id, scope).exists():
        raise ApiError(code="session_not_found", message="会话不存在", status_code=404)
    node_ids = [item.strip() for item in nodes.split(",") if item.strip()]
    if not node_ids:
        raise ApiError(code="branch_nodes_required", message="没有指定要删的节点")
    result = file_store.delete_branch(id, scope, node_ids)
    if not result.get("deleted"):
        raise ApiError(code="session_node_not_found", message="分支不存在", status_code=404)
    snapshot = file_store.session_snapshot(id, scope)
    snapshot["labels"] = _display_labels(character_id)
    return JSONResponse(content=snapshot)


@router.post("/{id}/messages")
def append_message_endpoint(
    id: str,
    payload: dict[str, Any],
    character_id: str | None = None,
) -> JSONResponse:
    """追加会话消息。"""
    scope = _resolve_scope(id, character_id)
    if not _session_path(id, scope).exists():
        raise ApiError(code="session_not_found", message="会话不存在", status_code=404)
    message = file_store.append_message(id, scope, payload)
    return JSONResponse(content=message)


@router.get("/{id}/export")
def export_session_endpoint(
    id: str,
    character_id: str | None = None,
    format: str = "md",
) -> Response:
    """导出会话逐字稿。"""
    if format != "md":
        raise ApiError(code="format_unsupported", message="仅支持 md 导出")
    scope = _resolve_scope(id, character_id)
    if not _session_path(id, scope).exists():
        raise ApiError(code="session_not_found", message="会话不存在", status_code=404)
    text = file_store.export_session_markdown(id, scope)
    return Response(
        content=text.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{id}.md"'},
    )
