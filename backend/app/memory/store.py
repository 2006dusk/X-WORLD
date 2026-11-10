"""记忆与实体的读写。

全部经 app.store 落地为文件，不引入任何数据库。
"""

from __future__ import annotations

from typing import Any

from app import store as file_store


def save_memory(memory: dict[str, Any]) -> dict[str, Any]:
    """写入记忆条目。"""
    return file_store.save_memory(memory)


def list_memories(
    character_id: str | None = None,
    session_id: str | None = None,
    kind: str | None = None,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    """列出记忆条目。"""
    return file_store.list_memories(
        character_id=character_id,
        session_id=session_id,
        kind=kind,
        include_archived=include_archived,
    )


def get_memory(memory_id: str, character_id: str | None = None) -> dict[str, Any] | None:
    """读取单条记忆。"""
    return file_store.get_memory(memory_id, character_id)


def archive_memory(memory_id: str, character_id: str | None = None) -> bool:
    """归档记忆，不物理删除。"""
    return file_store.archive_memory(memory_id, character_id)


def save_entity(entity: dict[str, Any]) -> dict[str, Any]:
    """写入实体状态。"""
    return file_store.save_entity(entity)


def list_entities(character_id: str = file_store.GLOBAL_SCOPE) -> list[dict[str, Any]]:
    """列出实体状态。"""
    return file_store.list_entities(character_id)


def get_entity(name: str, character_id: str = file_store.GLOBAL_SCOPE) -> dict[str, Any] | None:
    """读取单个实体。"""
    return file_store.get_entity(name, character_id)
