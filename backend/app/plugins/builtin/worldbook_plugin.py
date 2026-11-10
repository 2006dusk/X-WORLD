"""内置世界书插件。

提供世界书条目注入接口：按给定文本命中条目，输出注入内容与命中明细。
匹配规则与 ``app.worldbooks.match_entries`` 保持一致：

- ``constant`` 条目常驻命中；
- 其余条目按 ``keys`` 逐项匹配，``keys`` 支持逗号分隔字符串或字符串数组；
- ``selective`` 条目在 ``keys`` 命中后，还需 ``secondary_keys`` 命中 ``secondary_text``；
- ``match_regex`` 为真时把关键词当正则，否则按字面量匹配；
- ``case_sensitive`` 为假时忽略大小写；
- 结果按 ``priority`` 降序排列，``priority`` 相同时保持原顺序（稳定排序）；
- 非法正则被跳过，不中断其它条目。

规则来源为 ``app.store`` 的世界书文件：``world_books/<book_id>.json`` 的 ``entries``。
宿主可经 JSON-RPC 调用 ``inline`` 取注入文本，或经 ``preAssembly`` / ``preRequest``
参与装配。本插件由主程序内联调用，但仍走与其他插件相同的 hook 派发接口。
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable

from app import store

SECONDARY_META_KEY = "_meta"


def _split_keys(keys: Any) -> list[str]:
    """把逗号分隔字符串或数组统一为关键词列表。"""
    if keys is None:
        return []
    if isinstance(keys, (list, tuple)):
        return [str(item).strip() for item in keys if str(item).strip()]
    if isinstance(keys, str):
        return [part.strip() for part in keys.split(",") if part.strip()]
    return []


def _keyword_hit(
    text: str,
    keywords: Any,
    *,
    case_sensitive: bool,
    match_regex: bool,
) -> bool:
    """任一关键词命中即返回 True；非法正则跳过。"""
    flags = 0 if case_sensitive else re.IGNORECASE
    for keyword in _split_keys(keywords):
        pattern = keyword if match_regex else re.escape(keyword)
        try:
            if re.compile(pattern, flags).search(text):
                return True
        except re.error:
            continue
    return False


def _secondary_keys(entry: dict[str, Any]) -> list[str]:
    """取条目的二级关键词，兼容 ``secondary_keys`` 与 ``_meta.secondary_keys``。"""
    keys = _split_keys(entry.get("secondary_keys"))
    if keys:
        return keys
    meta = entry.get(SECONDARY_META_KEY)
    if isinstance(meta, dict):
        return _split_keys(meta.get("secondary_keys"))
    return []


def reference_match_entries(
    entries: Iterable[dict[str, Any]],
    text: str,
    *,
    secondary_text: str = "",
) -> list[dict[str, Any]]:
    """内置参照实现，语义与 ``app.worldbooks.match_entries`` 一致。"""
    hits: list[tuple[int, int, dict[str, Any]]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        priority = entry.get("priority", 0)
        priority = priority if isinstance(priority, int) else 0
        case_sensitive = bool(entry.get("case_sensitive"))
        match_regex = bool(entry.get("match_regex"))

        if entry.get("constant"):
            hits.append((1, priority, index, entry))
            continue

        if not _keyword_hit(
            text,
            entry.get("keys"),
            case_sensitive=case_sensitive,
            match_regex=match_regex,
        ):
            continue

        if entry.get("selective"):
            secondary = _secondary_keys(entry)
            if not secondary:
                continue
            if not _keyword_hit(
                secondary_text,
                secondary,
                case_sensitive=case_sensitive,
                match_regex=match_regex,
            ):
                continue

        hits.append((0, priority, index, entry))

    hits.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [entry for _, _, _, entry in hits]


_EXTERNAL: list[Callable[..., list[dict[str, Any]]] | None] = []


def _external_matcher() -> Callable[..., list[dict[str, Any]]] | None:
    """尝试取 ``app.worldbooks.match_entries`` 作为权威实现，取不到时返回 None。"""
    if _EXTERNAL:
        return _EXTERNAL[0]
    matcher: Callable[..., list[dict[str, Any]]] | None = None
    try:
        from app import worldbooks

        candidate = getattr(worldbooks, "match_entries", None)
        if callable(candidate):
            matcher = candidate
    except Exception:  # noqa: BLE001 - 该模块由 T1 维护，导入失败时退回参照实现
        matcher = None
    _EXTERNAL.append(matcher)
    return matcher


def match_entries(
    entries: Iterable[dict[str, Any]],
    text: str,
    *,
    secondary_text: str = "",
) -> list[dict[str, Any]]:
    """命中世界书条目。

    优先委托 ``app.worldbooks.match_entries``，以保持与主装配层的一致；
    该模块不可导入时使用语义等价的参照实现。
    """
    items = [entry for entry in entries if isinstance(entry, dict)]
    matcher = _external_matcher()
    if matcher is not None:
        try:
            return list(matcher(items, text, secondary_text=secondary_text))
        except TypeError:
            return list(matcher(items, text))
    return reference_match_entries(items, text, secondary_text=secondary_text)


def load_entries(book_ids: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """按世界书 id 读取条目；未指定时取全部启用世界书。

    返回 (条目, 问题清单, 实际参与的世界书 id 列表)。
    """
    issues: list[dict[str, Any]] = []
    used: list[str] = []
    entries: list[dict[str, Any]] = []

    wanted: list[str] = []
    if book_ids is None:
        for summary in store.list_world_books():
            if not summary.get("enabled", True):
                continue
            wanted.append(str(summary.get("id")))
    elif isinstance(book_ids, str):
        wanted = [book_ids]
    elif isinstance(book_ids, (list, tuple)):
        wanted = [str(item) for item in book_ids]
    else:
        issues.append({"code": "invalid_format", "message": "world_book_ids 必须是数组"})
        return entries, issues, used

    for book_id in wanted:
        book = store.get_world_book(book_id)
        if not isinstance(book, dict):
            issues.append(
                {"book_id": book_id, "code": "world_book_not_found", "message": "世界书不存在"}
            )
            continue
        if not book.get("enabled", True):
            issues.append(
                {"book_id": book_id, "code": "world_book_disabled", "message": "世界书已停用"}
            )
            continue
        raw_entries = book.get("entries")
        if raw_entries is None:
            raw_entries = []
        if not isinstance(raw_entries, (list, tuple)):
            issues.append(
                {
                    "book_id": book_id,
                    "code": "invalid_entries",
                    "message": "世界书 entries 必须是数组",
                }
            )
            continue
        used.append(book_id)
        for index, entry in enumerate(raw_entries):
            if not isinstance(entry, dict):
                issues.append(
                    {
                        "book_id": book_id,
                        "index": index,
                        "code": "invalid_entry",
                        "message": "条目必须是对象",
                    }
                )
                continue
            normalized = dict(entry)
            normalized.setdefault("book_id", book_id)
            if not normalized.get("id"):
                normalized["id"] = f"{book_id}#{index}"
            entries.append(normalized)
    return entries, issues, used


def build_injection(
    entries: Iterable[dict[str, Any]],
    text: str,
    *,
    secondary_text: str = "",
) -> dict[str, Any]:
    """命中条目并拼装注入内容。"""
    hits = match_entries(entries, text, secondary_text=secondary_text)
    contents = [
        str(entry.get("content", ""))
        for entry in hits
        if str(entry.get("content", "")).strip()
    ]
    return {
        "entries": hits,
        "entry_ids": [str(entry.get("id", "")) for entry in hits],
        "content": "\n".join(contents),
        "tokens_estimate": sum(len(item) for item in contents),
    }


def resolve_text(params: dict[str, Any]) -> tuple[str, str]:
    """取主匹配文本与二级匹配文本，缺省用 user_input。"""
    primary = params.get("text")
    if not isinstance(primary, str) or not primary:
        primary = params.get("user_input", "")
    secondary = params.get("secondary_text")
    if not isinstance(secondary, str) or not secondary:
        secondary = primary
    return str(primary), str(secondary)


def _collect_messages_text(messages: Any, role: str) -> str:
    """拼接指定角色消息的内容。"""
    if not isinstance(messages, list):
        return ""
    parts: list[str] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != role:
            continue
        content = message.get("content")
        if isinstance(content, str) and content:
            parts.append(content)
    return "\n".join(parts)


# ── hook 处理 ─────────────────────────────────────────────────


def handle(method: str, params: dict[str, Any]) -> Any:
    """处理一次 JSON-RPC 调用。"""
    if method == "ping":
        return {"plugin": "builtin-worldbook", "ok": True}
    if method == "entries":
        entries, issues, books = load_entries(params.get("world_book_ids"))
        return {
            "books": books,
            "count": len(entries),
            "entries": entries,
            "issues": issues,
        }
    if method == "inline":
        entries, issues, books = load_entries(params.get("world_book_ids"))
        text, secondary_text = resolve_text(params)
        payload = build_injection(entries, text, secondary_text=secondary_text)
        return {
            "books": books,
            "entry_ids": payload["entry_ids"],
            "content": payload["content"],
            "tokens_estimate": payload["tokens_estimate"],
            "entries": [
                {
                    "id": str(entry.get("id", "")),
                    "book_id": entry.get("book_id"),
                    "priority": entry.get("priority", 0),
                    "content": entry.get("content", ""),
                }
                for entry in payload["entries"]
            ],
            "issues": issues,
        }
    if method in ("preAssembly", "turnStart"):
        entries, issues, books = load_entries(params.get("world_book_ids"))
        user_input = params.get("user_input")
        if not isinstance(user_input, str) or not user_input:
            user_input = _collect_messages_text(params.get("messages"), "user")
        text, secondary_text = resolve_text({**params, "text": user_input})
        payload = build_injection(entries, text, secondary_text=secondary_text)
        return {
            "context": {
                "world_info": payload["content"],
                "entry_ids": payload["entry_ids"],
                "books": books,
            },
            "notes": {"issues": issues, "tokens_estimate": payload["tokens_estimate"]},
        }
    if method == "preRequest":
        entries, issues, books = load_entries(params.get("world_book_ids"))
        user_input = _collect_messages_text(params.get("messages"), "user")
        text, secondary_text = resolve_text({**params, "text": user_input})
        payload = build_injection(entries, text, secondary_text=secondary_text)
        return {
            "notes": {
                "world_info": payload["content"],
                "entry_ids": payload["entry_ids"],
                "books": books,
                "issues": issues,
            }
        }
    # 未实现的 hook 视为无操作，避免派发时产生噪声错误。
    return {}
