"""角色卡读写。

数据以 JSON 为唯一权威形态，不实现 PNG 隐写。
全部读写经 app.store 落地为文件，不引入任何数据库。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app import store as file_store
from app.errors import ApiError

from .spec import (
    DROPPED_FIELDS,
    INTERNAL_V1_FIELDS,
    ROLE_PRIMARY,
    V2_COMMON_FIELDS,
    normalize_role,
    parse_card,
    to_internal_v1,
)


def _payload_of(card: dict[str, Any]) -> dict[str, Any]:
    payload = card.get("data")
    return payload if isinstance(payload, dict) else {}


def list_cards() -> list[dict[str, Any]]:
    """列出角色卡摘要。"""
    result: list[dict[str, Any]] = []
    for summary in file_store.list_cards():
        card = file_store.get_card(str(summary.get("id")))
        if card is None:
            continue
        payload = _payload_of(card)
        result.append(
            {
                "id": card.get("id"),
                "name": card.get("name") or payload.get("name"),
                "spec_version": card.get("spec_version"),
                "role": normalize_role(payload.get("role")),
                "pending": bool(payload.get("pending")),
                "introduced_by": str(payload.get("introduced_by") or ""),
                "relation": str(payload.get("relation") or ""),
                "created_at": card.get("created_at"),
                "updated_at": card.get("updated_at"),
            }
        )
    return result


def get_card(card_id: str) -> dict[str, Any] | None:
    """读取角色卡，返回内部结构。"""
    card = file_store.get_card(card_id)
    if card is None:
        return None
    payload = _payload_of(card)
    return {
        "id": card.get("id"),
        "name": card.get("name") or payload.get("name"),
        "spec_version": card.get("spec_version"),
        "created_at": card.get("created_at"),
        "updated_at": card.get("updated_at"),
        "data": payload,
        "raw": card.get("raw") if isinstance(card.get("raw"), dict) else {},
    }


def _is_v3_raw(card: dict[str, Any]) -> bool:
    marker = card.get("spec")
    return isinstance(marker, str) and marker.startswith("charcard_spec")


def save_card(card: dict[str, Any]) -> dict[str, Any]:
    """写入角色卡，输入可为原始规范数据或已归一化结构。

    保存前丢弃 DROPPED_FIELDS 里的字段：这些字段本项目不用，留在卡片里
    只会让场景与开场白两处旧设定继续影响装配。
    """
    internal = (
        "data" in card
        and isinstance(card.get("data"), dict)
        and "spec_version" in card
        and not _is_v3_raw(card)
    )
    if internal:
        payload = dict(card["data"])
        raw = card.get("raw") if isinstance(card.get("raw"), dict) else {}
        name = card.get("name") or payload.get("name")
    else:
        parsed = parse_card(card)
        if parsed["spec"] == "unknown":
            raise ApiError(code="card_invalid", message="无法识别角色卡规范版本")
        payload = dict(parsed["data"])
        raw = {key: value for key, value in card.items() if key not in parsed["data"]}
        name = payload.get("name")

    # 内部卡一律是 v1 三层：外部卡导入后也转成这一套，卡面格式不再分叉。
    payload = to_internal_v1(payload)
    spec = "v1"
    name = name or payload.get("name")

    stored = file_store.save_card(
        {
            "id": card.get("id"),
            "name": name,
            "spec_version": spec,
            "data": payload,
            "raw": raw,
        }
    )
    return get_card(str(stored["id"])) or stored


def delete_card(card_id: str) -> bool:
    """删除角色卡及其目录。"""
    return file_store.delete_card(card_id)


def import_card_json(data: dict[str, Any]) -> dict[str, Any]:
    """导入角色卡 JSON，接受 V1、V2、V3 三种规范。"""
    if not isinstance(data, dict):
        raise ApiError(code="card_invalid", message="角色卡必须是 JSON 对象")
    return save_card(data)


def read_json_card(data: dict[str, Any]) -> dict[str, Any]:
    """读取角色卡 JSON 数据并归一化。"""
    return import_card_json(data)


def import_card_file(path: str | Path) -> dict[str, Any]:
    """从文件导入角色卡 JSON。"""
    source = Path(path)
    if not source.exists():
        raise ApiError(code="card_not_found", message="角色卡文件不存在", status_code=404)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ApiError(code="card_invalid", message=f"角色卡解析失败：{exc}") from exc
    return import_card_json(data)


def export_card_json(card_id: str) -> bytes:
    """导出角色卡 JSON 字节。"""
    card = get_card(card_id)
    if card is None:
        raise ApiError(code="card_not_found", message="角色卡不存在", status_code=404)
    payload = {
        "spec": card.get("spec_version"),
        "spec_version": card.get("spec_version"),
        "id": card.get("id"),
        "name": card.get("name"),
        "data": card.get("data"),
        "raw": card.get("raw"),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def export_card_markdown(card_id: str) -> str:
    """导出角色卡 Markdown 逐字稿。

    YAML front-matter 承载名称与规范版本，正文按字段分节。
    Markdown 为派生形态，删除后可由 JSON 重建。
    """
    card = get_card(card_id)
    if card is None:
        raise ApiError(code="card_not_found", message="角色卡不存在", status_code=404)
    payload = card.get("data") or {}
    lines = [
        "---",
        f"name: {card.get('name') or ''}",
        f"spec: {card.get('spec_version') or 'v2'}",
        f"id: {card.get('id') or ''}",
        "---",
        "",
        f"# {card.get('name') or '未命名角色'}",
        "",
    ]
    titles = {
        "identity": "身份与处境",
        "behavior": "行为与细节",
        "speech": "说话方式",
        "mes_example": "对话示例",
        "system_prompt": "系统提示",
        "post_history_instructions": "历史后指令",
        "creator_notes": "创作者备注",
    }
    for field, title in titles.items():
        value = payload.get(field)
        if not value:
            continue
        lines.append(f"## {title}")
        lines.append("")
        lines.append(str(value))
        lines.append("")
    return "\n".join(lines)


def card_text_fields(card: dict[str, Any]) -> dict[str, str]:
    """取角色的文本字段，供装配层使用。

    内部 v1 卡的三层（identity、behavior、speech）都在里面。
    """
    payload = _payload_of(card)
    result: dict[str, str] = {}
    for field in (*INTERNAL_V1_FIELDS, "system_prompt", "post_history_instructions"):
        value = payload.get(field)
        if isinstance(value, str) and value:
            result[field] = value
    return result
