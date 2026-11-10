"""世界书读写与匹配。

全部经 app.store 落地为 JSON 文件，不引入任何数据库。
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from app import store as file_store
from app.cards.spec import to_internal_v1
from app.errors import ApiError

TIER_SELECTIVE = 2
TIER_CONSTANT = 1
TIER_KEYWORD = 0

# 条目类型：普通条目靠关键词命中，区域是世界的地点，由对话选定后强制注入。
ENTRY_KIND = "entry"
REGION_KIND = "region"


def is_region(entry: dict[str, Any]) -> bool:
    """条目是否是区域。"""
    return str(entry.get("kind") or ENTRY_KIND) == REGION_KIND


def region_name(entry: dict[str, Any]) -> str:
    """区域的显示名。依次取 name、comment，最后退回条目 id。"""
    for key in ("name", "comment"):
        value = str(entry.get(key) or "").strip()
        if value:
            return value
    return str(entry.get("id") or "")


def list_regions(book_id: str) -> list[dict[str, Any]]:
    """列出世界书里的区域条目。"""
    book = get_book(book_id)
    if book is None:
        raise ApiError(code="world_book_not_found", message="世界书不存在", status_code=404)
    result: list[dict[str, Any]] = []
    for entry in book.get("entries") or []:
        if not isinstance(entry, dict) or not is_region(entry):
            continue
        result.append(
            {
                "id": str(entry.get("id") or ""),
                "book_id": book_id,
                "name": region_name(entry),
                "content": str(entry.get("content") or ""),
            }
        )
    return result


def _split_keys(keys: str | list[str] | None) -> list[str]:
    if not keys:
        return []
    if isinstance(keys, str):
        return [part.strip() for part in keys.split(",") if part.strip()]
    return [str(part).strip() for part in keys if str(part).strip()]


def _secondary_keys(entry: dict[str, Any]) -> list[str]:
    keys = _split_keys(entry.get("secondary_keys"))
    if keys:
        return keys
    meta = entry.get("_meta")
    if isinstance(meta, dict):
        return _split_keys(meta.get("secondary_keys"))
    return []


def _keyword_hit(text: str, keys: Iterable[str], case_sensitive: bool, match_regex: bool) -> bool:
    haystack = text if case_sensitive else text.lower()
    for key in keys:
        if not key:
            continue
        if match_regex:
            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                if re.search(key, haystack, flags):
                    return True
            except re.error:
                continue
            continue
        needle = key if case_sensitive else key.lower()
        if needle in haystack:
            return True
    return False


def match_entries(
    entries: Iterable[dict[str, Any]],
    text: str,
    *,
    secondary_text: str = "",
    **_ignored: Any,
) -> list[dict[str, Any]]:
    """命中世界书条目。

    命中分三层：selective 命中、constant 常驻、关键词命中。
    同层内按 priority 降序、原次序升序排列。
    """
    hits: list[tuple[int, int, int, dict[str, Any]]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        priority = entry.get("priority", 0)
        priority = priority if isinstance(priority, int) else 0
        case_sensitive = bool(entry.get("case_sensitive"))
        match_regex = bool(entry.get("match_regex"))

        if entry.get("constant"):
            hits.append((TIER_CONSTANT, priority, index, entry))
            continue

        if not _keyword_hit(
            text, _split_keys(entry.get("keys")), case_sensitive, match_regex
        ):
            continue

        if entry.get("selective"):
            secondary = _secondary_keys(entry)
            if not secondary:
                continue
            if not _keyword_hit(
                secondary_text, secondary, case_sensitive, match_regex
            ):
                continue
            hits.append((TIER_SELECTIVE, priority, index, entry))
            continue

        hits.append((TIER_KEYWORD, priority, index, entry))

    hits.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [entry for _, _, _, entry in hits]


def find_region(region_id: str) -> dict[str, Any] | None:
    """按条目 id 找区域。

    返回所属世界书、区域名与地点标签。标签按「世界书名_区域名」拼，
    与状态里的地图写法一致，换区域时角色直接搬到这个标签下。
    """
    if not region_id:
        return None
    for book in list_books():
        book_id = str(book.get("id") or "")
        full = get_book(book_id)
        if not isinstance(full, dict):
            continue
        for entry in full.get("entries") or []:
            if not isinstance(entry, dict) or not is_region(entry):
                continue
            if str(entry.get("id") or "") != str(region_id):
                continue
            book_name = str(full.get("name") or book_id).strip()
            name = region_name(entry)
            label = f"{book_name}_{name}" if book_name else name
            return {
                "book_id": book_id,
                "book_name": book_name,
                "name": name,
                "label": label,
            }
    return None


def list_books(character_id: str | None = None) -> list[dict[str, Any]]:
    """列出世界书。"""
    return file_store.list_world_books()


def get_book(book_id: str) -> dict[str, Any] | None:
    """读取世界书。"""
    return file_store.get_world_book(book_id)


def save_book(book: dict[str, Any]) -> dict[str, Any]:
    """写入世界书。"""
    if not isinstance(book, dict):
        raise ApiError(code="world_book_invalid", message="世界书必须是 JSON 对象")
    return file_store.save_world_book(book)


def delete_book(book_id: str) -> bool:
    """删除世界书。"""
    return file_store.delete_world_book(book_id)


def card_entry_content(card_id: str) -> tuple[str, str]:
    """从角色卡生成世界书条目正文，返回 (正文, 角色名)。

    卡片不存在时返回空正文。取的是卡片自己的字段，不做二次加工。
    """
    card = file_store.get_card(card_id)
    if not isinstance(card, dict):
        return "", ""
    payload = card.get("data")
    payload = payload if isinstance(payload, dict) else {}
    # 旧格式卡（description / personality）走同一套映射。
    payload = to_internal_v1(payload)
    name = str(card.get("name") or payload.get("name") or "").strip()
    lines: list[str] = []
    if name:
        lines.append("【" + name + "】")
    for field, label in (
        ("identity", "身份与处境"),
        ("behavior", "行为与细节"),
    ):
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            lines.append(label + "：" + value.strip())
    return chr(10).join(lines), name


def _card_ref_of(item: dict[str, Any]) -> dict[str, Any] | None:
    """条目上的卡片引用。必须是带 card_id 的对象，否则视为没有引用。"""
    ref = item.get("card_ref")
    if not isinstance(ref, dict):
        return None
    card_id = str(ref.get("card_id") or "").strip()
    if not card_id:
        return None
    return {"card_id": card_id, "name": str(ref.get("name") or "")}


def _apply_card_ref(item: dict[str, Any]) -> None:
    """把卡片引用落到条目上。

    引用是活的：正文字段由角色卡生成，卡片改了正文跟着变。关键词留空时
    用角色名兜底，提到他就会命中。
    """
    ref = _card_ref_of(item)
    if not ref:
        item.pop("card_ref", None)
        return
    content, name = card_entry_content(ref["card_id"])
    item["card_ref"] = {
        "card_id": ref["card_id"],
        "name": name or ref.get("name") or "",
    }
    if content:
        item["content"] = content
    if not item.get("keys") and name:
        item["keys"] = [name]


def refresh_card_refs(card_id: str) -> int:
    """卡片改动后同步引用它的世界书条目。

    引用是活的：卡片改了，条目正文跟着重算。卡片被删掉时保留条目里已有的
    正文，不抹掉用户已经写好的内容。返回被更新的世界书数量。
    """
    updated = 0
    for summary in file_store.list_world_books():
        book_id = str(summary.get("id") or "")
        if not book_id:
            continue
        book = get_book(book_id)
        if book is None:
            continue
        entries = book.get("entries")
        if not isinstance(entries, list):
            continue
        changed = False
        for item in entries:
            if not isinstance(item, dict):
                continue
            ref = _card_ref_of(item)
            if not ref or ref["card_id"] != str(card_id):
                continue
            content, name = card_entry_content(str(card_id))
            if not content:
                continue
            if item.get("content") != content:
                item["content"] = content
                changed = True
            if name and (item.get("card_ref") or {}).get("name") != name:
                item["card_ref"] = {"card_id": str(card_id), "name": name}
                changed = True
        if changed:
            book["entries"] = entries
            file_store.save_world_book(book)
            updated += 1
    return updated


def create_entry(book_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    """在世界书中新增条目。"""
    book = get_book(book_id)
    if book is None:
        raise ApiError(code="world_book_not_found", message="世界书不存在", status_code=404)
    items = list(book.get("entries") or [])
    item = dict(entry)
    item.setdefault("id", file_store.new_id("e"))
    item.setdefault("keys", [])
    item.setdefault("content", "")
    item.setdefault("priority", 0)
    item.setdefault("position", 0)
    item.setdefault("constant", False)
    item.setdefault("selective", False)
    item.setdefault("recursion", 0)
    item.setdefault("case_sensitive", False)
    item.setdefault("match_regex", False)
    item.setdefault("kind", ENTRY_KIND)
    _apply_card_ref(item)
    items.append(item)
    book["entries"] = items
    file_store.save_world_book(book)
    return item


def update_entry(book_id: str, entry_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    """更新世界书条目。"""
    book = get_book(book_id)
    if book is None:
        raise ApiError(code="world_book_not_found", message="世界书不存在", status_code=404)
    items = list(book.get("entries") or [])
    for index, item in enumerate(items):
        if str(item.get("id")) == str(entry_id):
            merged = dict(item)
            merged.update(entry)
            merged["id"] = item.get("id")
            _apply_card_ref(merged)
            items[index] = merged
            book["entries"] = items
            file_store.save_world_book(book)
            return merged
    raise ApiError(code="world_entry_not_found", message="世界书条目不存在", status_code=404)


def delete_entry(book_id: str, entry_id: str) -> bool:
    """删除世界书条目。"""
    book = get_book(book_id)
    if book is None:
        raise ApiError(code="world_book_not_found", message="世界书不存在", status_code=404)
    items = list(book.get("entries") or [])
    remaining = [item for item in items if str(item.get("id")) != str(entry_id)]
    if len(remaining) == len(items):
        return False
    book["entries"] = remaining
    file_store.save_world_book(book)
    return True


def list_entries(book_id: str) -> list[dict[str, Any]]:
    """列出世界书条目。"""
    book = get_book(book_id)
    if book is None:
        raise ApiError(code="world_book_not_found", message="世界书不存在", status_code=404)
    return list(book.get("entries") or [])
