"""文件数据存储层。

全部结构化数据以 JSON 为唯一权威格式，会话流水用 JSONL，Markdown 仅作可选旁挂。
本模块是数据访问的唯一入口，其他模块不得自行读写数据目录。

目录布局：

    %APPDATA%/X-WORLD/
      cards/<card_id>/card.json
      cards/<card_id>/notes.md
      world_books/<book_id>.json
      presets/<preset_id>.json
      sessions/<character_id>/<session_id>.jsonl
      sessions/<character_id>/<session_id>.md
      memories/<character_id>/<memory_id>.json
      memories/<character_id>/<memory_id>.md
      entities/<character_id>/<name>.json
      state/<character_id>/state.json
      settings.json
      profiles/<profile_id>.json
      index/index.json
      index/vectors.npy
      plugins/<plugin_id>/manifest.yaml
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

from . import config

GLOBAL_SCOPE = "global"

# 设置项的读改写要串行：后端的保活线程与接口线程会同时写 settings.json，
# 各自读一份再整份覆盖，谁后写谁把对方的改动抹掉。丢一次就够要命的：
# 「inference_managed」被抹掉，保活线程就此不再管推理服务。
_SETTINGS_LOCK = threading.Lock()


def _dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _rmdir_quiet(path: Path) -> None:
    """删空目录，删不掉就算了。目录里还剩东西时不强删。"""
    if not path.is_dir():
        return
    try:
        path.rmdir()
    except OSError:
        pass


def cards_dir() -> Path:
    return _dir(config.data_root() / "cards")


def card_dir(card_id: str) -> Path:
    return _dir(cards_dir() / card_id)


def world_books_dir() -> Path:
    return _dir(config.data_root() / "world_books")


def presets_dir() -> Path:
    return _dir(config.data_root() / "presets")


def sessions_dir(character_id: str = GLOBAL_SCOPE) -> Path:
    return _dir(config.data_root() / "sessions" / character_id)


def memories_dir(character_id: str = GLOBAL_SCOPE) -> Path:
    return _dir(config.data_root() / "memories" / character_id)


def entities_dir(character_id: str = GLOBAL_SCOPE) -> Path:
    return _dir(config.data_root() / "entities" / character_id)


def states_dir(character_id: str = GLOBAL_SCOPE) -> Path:
    """角色级世界状态目录。"""
    return _dir(config.data_root() / "state" / character_id)


def state_path(character_id: str = GLOBAL_SCOPE) -> Path:
    """角色级世界状态文件路径。"""
    return states_dir(character_id) / "state.json"


def profiles_dir() -> Path:
    return _dir(config.data_root() / "profiles")


def index_dir() -> Path:
    return _dir(config.data_root() / "index")


def settings_path() -> Path:
    return config.data_root() / "settings.json"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def read_json(path: Path, default: Any = None) -> Any:
    """读取 JSON 文件，文件不存在或损坏时返回默认值。"""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path: Path, payload: Any) -> Path:
    """原子写入 JSON 文件，先写临时文件再替换。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return path


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return path


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """读取 JSONL，损坏行跳过而非整体失败。"""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")


def _summary(item: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: item.get(key) for key in keys}


def list_cards() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for entry in cards_dir().iterdir():
        if not entry.is_dir():
            continue
        card = read_json(entry / "card.json")
        if isinstance(card, dict):
            result.append(_summary(card, ("id", "name", "spec_version", "created_at")))
    return result


def get_card(card_id: str) -> dict[str, Any] | None:
    card = read_json(card_dir(card_id) / "card.json")
    return card if isinstance(card, dict) else None


def save_card(card: dict[str, Any]) -> dict[str, Any]:
    card_id = str(card.get("id") or new_id("c"))
    payload = dict(card)
    payload["id"] = card_id
    payload.setdefault("created_at", _now())
    payload["updated_at"] = _now()
    write_json(card_dir(card_id) / "card.json", payload)
    return payload


def delete_card(card_id: str) -> bool:
    directory = cards_dir() / card_id
    if not directory.exists():
        return False
    for item in directory.iterdir():
        item.unlink(missing_ok=True)
    directory.rmdir()
    delete_character_data(card_id)
    return True


def delete_character_data(card_id: str) -> dict[str, int]:
    """删掉一个角色名下的会话、记忆、实体与世界状态。

    删卡不删数据会留下孤儿聊天记录：卡已经不在了，会话文件还留着，
    界面上再也看不到也删不掉。删卡时一起清掉。
    """
    if not card_id or card_id == GLOBAL_SCOPE:
        return {"sessions": 0, "memories": 0, "entities": 0, "states": 0}
    counts = {"sessions": 0, "memories": 0, "entities": 0, "states": 0}
    root = config.data_root()
    for key, sub in (
        ("sessions", "sessions"),
        ("memories", "memories"),
        ("entities", "entities"),
    ):
        directory = root / sub / card_id
        if not directory.is_dir():
            continue
        for item in directory.iterdir():
            if item.is_file():
                item.unlink(missing_ok=True)
                counts[key] += 1
        _rmdir_quiet(directory)
    state_dir = root / "state" / card_id
    state_file = state_dir / "state.json"
    if state_file.is_file():
        state_file.unlink(missing_ok=True)
        counts["states"] = 1
    _rmdir_quiet(state_dir)
    return counts




# 按角色分目录的数据区。删角色只删文件没删目录时，空壳会堆在这里。
CHARACTER_DIRS = ("cards", "state", "sessions", "entities", "memories")


def prune_empty_card_dirs() -> int:
    """清掉角色目录里遗留的空壳目录，返回清掉的数量。

    历史上有几条删除路径只删了卡文件没删目录，空壳就一直堆在数据目录里。
    空壳对接口没有影响（list_cards 只认有 card.json 的目录），但目录越堆越多。
    角色数据目录里的空壳同样会堆：探针跑完删卡，state 与 sessions 下就留下
    一排空目录。这里一起清。

    只删一个文件都没有的空目录，不碰任何还带文件的目录；global 是全局作用域，
    空着也留着。删不掉就跳过。
    """
    removed = 0
    root = config.data_root()
    for sub in CHARACTER_DIRS:
        base = root / sub
        if not base.is_dir():
            continue
        for entry in base.iterdir():
            if not entry.is_dir() or entry.name == GLOBAL_SCOPE:
                continue
            try:
                if any(entry.iterdir()):
                    continue
                entry.rmdir()
                removed += 1
            except OSError:
                continue
    return removed


def list_world_books() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in sorted(world_books_dir().glob("*.json")):
        book = read_json(path)
        if isinstance(book, dict):
            result.append(_summary(book, ("id", "name", "enabled", "updated_at")))
    return result


def get_world_book(book_id: str) -> dict[str, Any] | None:
    book = read_json(world_books_dir() / f"{book_id}.json")
    return book if isinstance(book, dict) else None


def save_world_book(book: dict[str, Any]) -> dict[str, Any]:
    book_id = str(book.get("id") or new_id("w"))
    payload = dict(book)
    payload["id"] = book_id
    payload.setdefault("entries", [])
    payload.setdefault("enabled", True)
    payload["updated_at"] = _now()
    write_json(world_books_dir() / f"{book_id}.json", payload)
    return payload


def delete_world_book(book_id: str) -> bool:
    path = world_books_dir() / f"{book_id}.json"
    if not path.exists():
        return False
    path.unlink()
    return True


def list_presets() -> list[dict[str, Any]]:
    """列出预设。

    内置预设在摘要里带 builtin 标记：它由后端按版本刷新，用户改不动，
    前端据此把它从预设页挪到设置页只读展示。
    """
    result: list[dict[str, Any]] = []
    for path in sorted(presets_dir().glob("*.json")):
        preset = read_json(path)
        if isinstance(preset, dict):
            item = _summary(preset, ("id", "name", "updated_at"))
            item["builtin"] = bool(preset.get("builtin"))
            version = preset.get("builtin_version")
            if isinstance(version, int):
                item["builtin_version"] = version
            result.append(item)
    return result


def get_preset(preset_id: str) -> dict[str, Any] | None:
    preset = read_json(presets_dir() / f"{preset_id}.json")
    return preset if isinstance(preset, dict) else None


def save_preset(preset: dict[str, Any]) -> dict[str, Any]:
    preset_id = str(preset.get("id") or new_id("p"))
    payload = dict(preset)
    payload["id"] = preset_id
    payload.setdefault("prompts", [])
    payload["updated_at"] = _now()
    write_json(presets_dir() / f"{preset_id}.json", payload)
    return payload


def delete_preset(preset_id: str) -> bool:
    path = presets_dir() / f"{preset_id}.json"
    if not path.exists():
        return False
    path.unlink()
    return True


def _branch_count(rows: list[dict[str, Any]]) -> int:
    """这段对话有几条分支：一个节点下挂了两条以上子线就算一处分叉。

    列表页显示的是这个数，不显示会话 id 也不显示消息条数：一个人一条
    对话，他这段对话分出去几支才是有用的信息。
    """
    messages = [row for row in rows if row.get("role") != "session"]
    counts: dict[str, int] = {}
    previous = ""
    for index, row in enumerate(messages):
        raw_parent = row.get("parent_id")
        parent = previous if raw_parent is None else str(raw_parent)
        counts[parent] = counts.get(parent, 0) + 1
        raw_id = row.get("id")
        previous = raw_id if isinstance(raw_id, str) and raw_id else f"legacy-{index}"
    return sum(1 for value in counts.values() if value > 1)


def _session_summary(path: Path, scope: str) -> dict[str, Any]:
    """会话摘要。

    首行是会话头，取不到时按目录名与文件顺序兜底。会话头里记的是主角色，
    目录名是创建时的作用域：@ 换角色后目录不变，按目录过滤会漏掉会话。
    """
    rows = read_jsonl(path)
    head = rows[0] if rows and rows[0].get("role") == "session" else {}
    primary = str(head.get("character_id") or scope)
    roster = [str(item) for item in (head.get("characters") or []) if str(item)]
    if primary and primary not in roster:
        roster.insert(0, primary)
    return {
        "id": path.stem,
        "scope": scope,
        "character_id": primary,
        "characters": roster,
        "preset_id": head.get("preset_id"),
        "world_book_ids": [str(item) for item in (head.get("world_book_ids") or []) if str(item)],
        "region_id": str(head.get("region_id") or ""),
        "title": str(head.get("title") or ""),
        "created_at": head.get("created_at"),
        "message_count": max(len(rows) - 1, 0),
        "branch_count": _branch_count(rows),
    }


def canonical_session_id(character_id: str) -> str:
    """一个角色一条对话：会话 id 由角色决定。

    角色的对话始终是一条上下文，不按次新建：同一个人再次进入时回到
    同一条对话里继续，而不是多出一条一模一样的对话。
    """
    return f"s_{character_id}" if character_id else f"s_{GLOBAL_SCOPE}"


def _session_rank(summary: dict[str, Any]) -> tuple[int, int, str]:
    """会话的取舍权重：规范 id 优先，其次消息多的，再次创建时间新的。"""
    character_id = str(summary.get("character_id") or "")
    canonical = 1 if str(summary.get("id") or "") == canonical_session_id(character_id) else 0
    count = int(summary.get("message_count") or 0)
    return (canonical, count, str(summary.get("created_at") or ""))


def list_sessions(character_id: str | None = None) -> list[dict[str, Any]]:
    """列出会话：一个角色一条对话。

    character_id 按会话头的主角色过滤，不按目录名过滤。同一角色的多条
    历史会话只保留一条：规范 id 优先，其次消息最多的那条，再其次创建
    时间最新的那条。
    """
    best: dict[str, dict[str, Any]] = {}
    for scope in _session_scopes():
        for path in sorted(sessions_dir(scope).glob("*.jsonl")):
            summary = _session_summary(path, scope)
            key = str(summary.get("character_id") or scope)
            if character_id and key != character_id:
                continue
            current = best.get(key)
            if current is None or _session_rank(summary) > _session_rank(current):
                best[key] = summary
    return sorted(
        best.values(),
        key=lambda item: str(item.get("created_at") or ""),
        reverse=True,
    )


def session_summary(session_id: str) -> dict[str, Any]:
    """单个会话摘要。找不到会话时返回空字典。"""
    scope = session_scope(session_id)
    if not scope:
        return {}
    return _session_summary(sessions_dir(scope) / f"{session_id}.jsonl", scope)


def get_session_head(session_id: str, scope: str) -> dict[str, Any]:
    """读取会话头。文件不存在或首行不是会话头时返回空字典。"""
    path = sessions_dir(scope) / f"{session_id}.jsonl"
    rows = read_jsonl(path)
    if rows and rows[0].get("role") == "session":
        return dict(rows[0])
    return {}


def session_scope(session_id: str) -> str | None:
    """定位会话文件所在的作用域目录。

    会话与角色解耦后，作用域由会话文件自身决定：读写会话不再依赖当前发言
    角色，否则 @ 换角色会把消息写到另一个目录里，历史就断了。
    """
    for scope in _session_scopes():
        if (sessions_dir(scope) / f"{session_id}.jsonl").exists():
            return scope
    return None


def update_session_meta(
    session_id: str,
    scope: str,
    fields: dict[str, Any],
) -> dict[str, Any]:
    """更新会话头的元信息：世界位置、在场角色、主角色与标题。"""
    path = sessions_dir(scope) / f"{session_id}.jsonl"
    rows = read_jsonl(path)
    if not rows:
        return {}
    if rows[0].get("role") == "session":
        head = dict(rows[0])
        body = rows[1:]
    else:
        head = {
            "role": "session",
            "id": session_id,
            "character_id": scope,
            "created_at": _now(),
        }
        body = rows
    for key in ("character_id", "preset_id", "title", "region_id"):
        value = fields.get(key)
        if value is not None:
            head[key] = str(value)
    for key in ("world_book_ids", "characters"):
        value = fields.get(key)
        if isinstance(value, list):
            head[key] = [str(item) for item in value if str(item)]
    lines = [json.dumps(head, ensure_ascii=False)]
    lines.extend(json.dumps(row, ensure_ascii=False) for row in body)
    write_text(path, chr(10).join(lines) + chr(10))
    return head


def _session_scopes() -> list[str]:
    base = config.data_root() / "sessions"
    if not base.exists():
        return [GLOBAL_SCOPE]
    return sorted(entry.name for entry in base.iterdir() if entry.is_dir())


def get_session(session_id: str, character_id: str | None = None) -> dict[str, Any] | None:
    for scope in ([character_id] if character_id else _session_scopes()):
        path = sessions_dir(scope) / f"{session_id}.jsonl"
        if not path.exists():
            continue
        rows = read_jsonl(path)
        head = rows[0] if rows else {}
        return {
            "id": session_id,
            "character_id": scope,
            "preset_id": head.get("preset_id"),
            "created_at": head.get("created_at"),
            "message_count": max(len(rows) - 1, 0),
        }
    return None


def create_session(
    character_id: str,
    preset_id: str | None = None,
    world_book_ids: list[str] | None = None,
    characters: list[str] | None = None,
    region_id: str | None = None,
) -> dict[str, Any]:
    """新建或继续一个角色的对话。

    一个角色一条对话：角色已经有对话时继续它，只更新世界位置与在场
    角色，不新建第二条一模一样的对话。character_id 是主角色；
    world_book_ids 是会话引用的世界；region_id 是这段对话所在的世界
    区域；characters 是会话涉及的角色清单，主角色排第一个。
    """
    session_id = canonical_session_id(character_id)
    path = sessions_dir(character_id) / f"{session_id}.jsonl"
    books = [str(item) for item in (world_book_ids or []) if str(item)]
    roster = [str(item) for item in (characters or []) if str(item)]
    if character_id and character_id not in roster:
        roster.insert(0, character_id)
    if path.exists():
        head = get_session_head(session_id, character_id)
        fields: dict[str, Any] = {}
        if books:
            fields["world_book_ids"] = books
        if region_id:
            fields["region_id"] = str(region_id)
        if preset_id:
            fields["preset_id"] = str(preset_id)
        merged = [str(item) for item in (head.get("characters") or [])]
        merged.extend(item for item in roster if item not in merged)
        if merged:
            fields["characters"] = merged
        update_session_meta(session_id, character_id, fields)
        return session_summary(session_id)
    head = {
        "role": "session",
        "id": session_id,
        "character_id": character_id,
        "preset_id": preset_id,
        "world_book_ids": books,
        "region_id": str(region_id) if region_id else "",
        "characters": roster,
        "created_at": _now(),
    }
    append_jsonl(path, head)
    return {
        "id": session_id,
        "scope": character_id,
        "character_id": character_id,
        "characters": roster,
        "preset_id": preset_id,
        "world_book_ids": books,
        "region_id": str(region_id) if region_id else "",
        "title": "",
        "created_at": head["created_at"],
        "message_count": 0,
    }


def ensure_session(
    session_id: str,
    character_id: str,
    preset_id: str | None = None,
    world_book_ids: list[str] | None = None,
) -> dict[str, Any]:
    """确保会话文件存在，且首行是会话头。

    聊天路由直接写消息时不会先建会话。缺了头行，
    会话列表里的 created_at 会是空值、message_count 也会少算一条，
    这里按需补一行头，已有头行时原样保留。
    """
    path = sessions_dir(character_id) / f"{session_id}.jsonl"
    rows = read_jsonl(path) if path.exists() else []
    if rows and rows[0].get("role") == "session":
        head = rows[0]
        return {
            "id": session_id,
            "scope": character_id,
            "character_id": str(head.get("character_id") or character_id),
            "characters": [str(item) for item in (head.get("characters") or [])],
            "preset_id": head.get("preset_id"),
            "world_book_ids": [str(item) for item in (head.get("world_book_ids") or [])],
            "created_at": head.get("created_at"),
            "message_count": len(rows) - 1,
        }
    books = [str(item) for item in (world_book_ids or []) if str(item)]
    roster = [character_id] if character_id else []
    head = {
        "role": "session",
        "id": session_id,
        "character_id": character_id,
        "preset_id": preset_id,
        "world_book_ids": books,
        "characters": roster,
        "created_at": _now(),
    }
    lines = [json.dumps(head, ensure_ascii=False)]
    lines.extend(json.dumps(row, ensure_ascii=False) for row in rows)
    write_text(path, chr(10).join(lines) + chr(10))
    return {
        "id": session_id,
        "scope": character_id,
        "character_id": character_id,
        "characters": roster,
        "preset_id": preset_id,
        "world_book_ids": books,
        "created_at": head["created_at"],
        "message_count": len(rows),
    }


def append_message(
    session_id: str,
    character_id: str,
    message: dict[str, Any],
) -> dict[str, Any]:
    path = sessions_dir(character_id) / f"{session_id}.jsonl"
    row = dict(message)
    row.setdefault("created_at", _now())
    append_jsonl(path, row)
    return row


def get_messages(
    session_id: str,
    character_id: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    for scope in ([character_id] if character_id else _session_scopes()):
        path = sessions_dir(scope) / f"{session_id}.jsonl"
        if not path.exists():
            continue
        rows = read_jsonl(path)
        messages = [row for row in rows if row.get("role") != "session"]
        return messages[-limit:] if limit else messages
    return []


def truncate_messages(
    session_id: str,
    character_id: str | None = None,
    keep: int = 0,
) -> int:
    """截断会话流水：只保留前 keep 条消息，其余删除，返回被删除的条数。

    会话文件首行是会话头，不计入消息条数，截断时保留。keep 为 0 表示
    清空消息但保留会话头。找不到会话文件时返回 0。
    """
    for scope in ([character_id] if character_id else _session_scopes()):
        path = sessions_dir(scope) / f"{session_id}.jsonl"
        if not path.exists():
            continue
        rows = read_jsonl(path)
        head = rows[0] if rows and rows[0].get("role") == "session" else None
        messages = [row for row in rows if row.get("role") != "session"]
        kept = messages[:keep] if keep > 0 else []
        lines: list[str] = []
        if head is not None:
            lines.append(json.dumps(head, ensure_ascii=False))
        lines.extend(json.dumps(row, ensure_ascii=False) for row in kept)
        write_text(path, chr(10).join(lines) + (chr(10) if lines else ""))
        return len(messages) - len(kept)
    return 0


def _node_id(row: dict[str, Any], index: int, session_id: str) -> str:
    """消息节点 id：优先用行里的 id，旧数据按文件顺序派生。

    派生只在内存里生效，不回写文件。规则依赖文件顺序，
    手工重排会话文件会打乱派生结果。
    """
    raw = row.get("id")
    if isinstance(raw, str) and raw:
        return raw
    return "legacy-" + session_id + "-" + str(index)


def session_tree(session_id: str, character_id: str | None = None) -> dict[str, Any]:
    """把会话流水读成树。

    返回 head（当前路径末端的节点 id）、nodes（节点 id 到消息行）、
    children（父节点 id 到子节点 id 列表）与 order（文件顺序的节点 id）。
    同一父节点下的多个子节点是兄弟分支，可切回。旧数据没有 id 与
    parent_id 时按文件顺序串联，派生结果只在内存里生效。
    """
    for scope in ([character_id] if character_id else _session_scopes()):
        path = sessions_dir(scope) / f"{session_id}.jsonl"
        if not path.exists():
            continue
        rows = read_jsonl(path)
        head_row = rows[0] if rows and rows[0].get("role") == "session" else None
        messages = [row for row in rows if row.get("role") != "session"]
        nodes: dict[str, dict[str, Any]] = {}
        children: dict[str, list[str]] = {}
        order: list[str] = []
        previous = ""
        for index, row in enumerate(messages):
            node_id = _node_id(row, index, session_id)
            raw_parent = row.get("parent_id")
            if raw_parent is None:
                # 旧数据没有 parent_id，按文件顺序串联。
                parent = previous
            else:
                # 显式写的空串是根节点，不是接在上一条后面。
                parent = str(raw_parent)
            node = dict(row)
            node["id"] = node_id
            node["parent_id"] = parent
            nodes[node_id] = node
            children.setdefault(parent, []).append(node_id)
            order.append(node_id)
            previous = node_id
        head = ""
        if head_row is not None:
            raw_head = head_row.get("head_id")
            if isinstance(raw_head, str) and raw_head in nodes:
                head = raw_head
        if not head and order:
            head = order[-1]
        return {"head": head, "nodes": nodes, "children": children, "order": order}
    return {"head": "", "nodes": {}, "children": {}, "order": []}


def delete_branch(session_id: str, scope: str, node_ids: list[str]) -> dict[str, Any]:
    """删掉一条分支的一段：给定的这些节点从会话流水里移除。

    删的是一段支线，不是整段对话。被删节点的子节点重挂到它最近的未被
    删掉的祖先上：分叉点删掉后，它分出去的其他支线还在，只是从上一节点
    直接长出来。head 落在被删的段里时回退到最近的未被删掉的祖先。
    """
    path = sessions_dir(scope) / f"{session_id}.jsonl"
    rows = read_jsonl(path)
    if not rows:
        return {"deleted": 0, "head": ""}
    head_row = rows[0] if rows[0].get("role") == "session" else None
    messages = [row for row in rows if row.get("role") != "session"]
    nodes: dict[str, dict[str, Any]] = {}
    children: dict[str, list[str]] = {}
    index_of: dict[str, int] = {}
    previous = ""
    for index, row in enumerate(messages):
        current_id = _node_id(row, index, session_id)
        raw_parent = row.get("parent_id")
        parent = previous if raw_parent is None else str(raw_parent)
        node = dict(row)
        node["id"] = current_id
        node["parent_id"] = parent
        nodes[current_id] = node
        children.setdefault(parent, []).append(current_id)
        index_of[current_id] = index
        previous = current_id
    doomed = {item for item in node_ids if item in nodes}
    if not doomed:
        return {"deleted": 0, "head": ""}

    def nearest_ancestor(node_id: str) -> str:
        """最近的未被删掉的祖先。没有就返回空串。"""
        current = str(nodes.get(node_id, {}).get("parent_id") or "")
        seen: set[str] = set()
        while current and current in nodes and current not in seen:
            if current not in doomed:
                return current
            seen.add(current)
            current = str(nodes[current].get("parent_id") or "")
        return ""

    # 被删节点下面留着的子节点重挂到最近的幸存祖先上
    for current_id in doomed:
        anchor = nearest_ancestor(current_id)
        for kid in children.get(current_id, []):
            if kid in doomed:
                continue
            index = index_of[kid]
            new_row = dict(messages[index])
            new_row["parent_id"] = anchor
            messages[index] = new_row
            kid_node = dict(nodes[kid])
            kid_node["parent_id"] = anchor
            nodes[kid] = kid_node
    dropped = {index_of[item] for item in doomed}
    kept = [row for index, row in enumerate(messages) if index not in dropped]
    current_head = ""
    if head_row is not None:
        raw_head = head_row.get("head_id")
        current_head = raw_head if isinstance(raw_head, str) else ""
    if not current_head and messages:
        current_head = _node_id(messages[-1], len(messages) - 1, session_id)
    new_head = current_head
    if current_head in doomed:
        new_head = nearest_ancestor(current_head)
    lines: list[str] = []
    if head_row is not None:
        new_head_row = dict(head_row)
        new_head_row["head_id"] = new_head
        lines.append(json.dumps(new_head_row, ensure_ascii=False))
    lines.extend(json.dumps(row, ensure_ascii=False) for row in kept)
    write_text(path, chr(10).join(lines) + (chr(10) if lines else ""))
    return {"deleted": len(doomed), "head": new_head}


def session_path(
    session_id: str,
    character_id: str | None = None,
    leaf_id: str | None = None,
) -> list[dict[str, Any]]:
    """当前路径的消息列表：从 head 沿 parent 回溯再反转成正序。

    leaf_id 为空时用会话头的 head；旧会话没有 head 时按文件顺序。
    """
    tree = session_tree(session_id, character_id)
    leaf = leaf_id if leaf_id is not None else str(tree.get("head") or "")
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    current = leaf
    while current and current in tree["nodes"] and current not in seen:
        seen.add(current)
        node = tree["nodes"][current]
        chain.append(node)
        current = str(node.get("parent_id") or "")
    chain.reverse()
    return chain


def set_head(session_id: str, character_id: str, head_id: str) -> None:
    """把会话头的 head_id 指向指定节点；会话头缺失时补一行。"""
    path = sessions_dir(character_id) / f"{session_id}.jsonl"
    rows = read_jsonl(path)
    if not rows:
        return
    if rows[0].get("role") == "session":
        head = dict(rows[0])
        body = rows[1:]
    else:
        head = {
            "role": "session",
            "id": session_id,
            "character_id": character_id,
            "created_at": _now(),
        }
        body = rows
    head["head_id"] = head_id
    lines = [json.dumps(head, ensure_ascii=False)]
    lines.extend(json.dumps(row, ensure_ascii=False) for row in body)
    write_text(path, "\n".join(lines) + "\n")


def branch_message(
    session_id: str,
    character_id: str,
    parent_id: str,
    message: dict[str, Any],
) -> dict[str, Any]:
    """在 parent_id 下追加一条消息，并把 head 移到新消息。

    数据层只追加不删除：旧节点保留为兄弟分支，可切回。
    parent_id 为空串表示从根分叉。
    """
    tree = session_tree(session_id, character_id)
    if parent_id and parent_id not in tree["nodes"]:
        raise KeyError(parent_id)
    row = dict(message)
    row["id"] = new_id("m")
    row["parent_id"] = parent_id
    row.setdefault("created_at", _now())
    append_jsonl(sessions_dir(character_id) / f"{session_id}.jsonl", row)
    set_head(session_id, character_id, str(row["id"]))
    return row


def replace_message(
    session_id: str,
    character_id: str,
    node_id: str,
    message: dict[str, Any],
) -> dict[str, Any] | None:
    """原地替换一条消息：节点 id 与父节点不变，正文与块换掉。

    重新生成走这里。新回复占住原来那条回复的位置，树上不新增兄弟节点，
    所以重新生成本身不产生分支；被替换节点之后的那几轮仍留在树上，
    继续对话时另起一条分支，同一轮于是有两个版本。

    找不到节点时返回 None，调用方按原路径处理。
    """
    path = sessions_dir(character_id) / f"{session_id}.jsonl"
    if not path.exists():
        return None
    rows = read_jsonl(path)
    head = rows[0] if rows and rows[0].get("role") == "session" else None
    body = rows[1:] if head is not None else rows
    replaced: dict[str, Any] | None = None
    lines: list[str] = []
    if head is not None:
        lines.append(json.dumps(head, ensure_ascii=False))
    for row in body:
        if str(row.get("id") or "") != str(node_id):
            lines.append(json.dumps(row, ensure_ascii=False))
            continue
        merged = dict(row)
        merged["role"] = str(message.get("role") or row.get("role") or "assistant")
        merged["content"] = str(message.get("content") or "")
        if isinstance(message.get("blocks"), dict):
            merged["blocks"] = message["blocks"]
        else:
            merged.pop("blocks", None)
        merged["created_at"] = _now()
        replaced = merged
        lines.append(json.dumps(merged, ensure_ascii=False))
    if replaced is None:
        return None
    write_text(path, chr(10).join(lines) + chr(10))
    return replaced


def _summarize(node: dict[str, Any]) -> str:
    """节点摘要：角色回复取对白，用户输入取原文，去空白后截前 24 字。"""
    text = ""
    blocks = node.get("blocks")
    if isinstance(blocks, dict):
        text = str(blocks.get("dialogue") or "")
    if not text:
        text = str(node.get("content") or "")
    flat = "".join(text.split())
    return flat[:24]


def session_snapshot(session_id: str, character_id: str | None = None) -> dict[str, Any]:
    """会话快照：当前路径的完整消息与整棵分支树。

    items 是当前路径，顺序即对话顺序；tree 是全部节点，含兄弟分支，
    供前端画分支树。两者的节点 id 一致，可直接对应。
    tree 里的节点带正文：前端只在悬停某个点时显示那一组对话，
    平时不铺开，铺开会把整段对话重复一遍。
    """
    tree = session_tree(session_id, character_id)
    nodes = tree.get("nodes") or {}
    children = tree.get("children") or {}
    items: list[dict[str, Any]] = []
    for node in session_path(session_id, character_id):
        items.append(
            {
                "id": str(node.get("id") or ""),
                "parent_id": str(node.get("parent_id") or ""),
                "role": str(node.get("role") or "user"),
                "content": str(node.get("content") or ""),
                "blocks": node.get("blocks") if isinstance(node.get("blocks"), dict) else None,
                "summary": _summarize(node),
                "created_at": node.get("created_at"),
            }
        )
    tree_nodes: list[dict[str, Any]] = []
    for node_id in tree.get("order") or []:
        node = nodes.get(node_id) or {}
        tree_nodes.append(
            {
                "id": node_id,
                "parent_id": str(node.get("parent_id") or ""),
                "role": str(node.get("role") or "user"),
                "summary": _summarize(node),
                "content": str(node.get("content") or ""),
                "created_at": node.get("created_at"),
                "children": list(children.get(node_id) or []),
            }
        )
    return {
        "head_id": str(tree.get("head") or ""),
        "items": items,
        "tree": tree_nodes,
        "count": len(items),
    }


def export_session_markdown(session_id: str, character_id: str | None = None) -> str:
    messages = get_messages(session_id, character_id)
    lines: list[str] = [f"# 会话 {session_id}", ""]
    for message in messages:
        role = message.get("role", "unknown")
        content = message.get("content", "")
        lines.append(f"## {role}")
        lines.append("")
        lines.append(str(content))
        lines.append("")
    return "\n".join(lines)


def list_memories(
    character_id: str | None = None,
    session_id: str | None = None,
    kind: str | None = None,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    scopes = [character_id] if character_id else _memory_scopes()
    result: list[dict[str, Any]] = []
    for scope in scopes:
        for path in sorted(memories_dir(scope).glob("*.json")):
            item = read_json(path)
            if not isinstance(item, dict):
                continue
            if not include_archived and item.get("archived"):
                continue
            if session_id and item.get("session_id") != session_id:
                continue
            if kind and item.get("kind") != kind:
                continue
            result.append(item)
    return result


def _memory_scopes() -> list[str]:
    base = config.data_root() / "memories"
    if not base.exists():
        return [GLOBAL_SCOPE]
    return sorted(entry.name for entry in base.iterdir() if entry.is_dir())


def get_memory(memory_id: str, character_id: str | None = None) -> dict[str, Any] | None:
    scopes = [character_id] if character_id else _memory_scopes()
    for scope in scopes:
        item = read_json(memories_dir(scope) / f"{memory_id}.json")
        if isinstance(item, dict):
            return item
    return None


def save_memory(memory: dict[str, Any]) -> dict[str, Any]:
    character_id = str(memory.get("character_id") or GLOBAL_SCOPE)
    memory_id = str(memory.get("id") or new_id("m"))
    payload = dict(memory)
    payload["id"] = memory_id
    payload["character_id"] = character_id
    payload.setdefault("kind", "fact")
    payload.setdefault("importance", 0.5)
    payload.setdefault("created_at", _now())
    payload.setdefault("archived", False)
    payload.setdefault("last_access_at", payload["created_at"])
    write_json(memories_dir(character_id) / f"{memory_id}.json", payload)
    return payload


def archive_memory(memory_id: str, character_id: str | None = None) -> bool:
    for scope in ([character_id] if character_id else _memory_scopes()):
        path = memories_dir(scope) / f"{memory_id}.json"
        item = read_json(path)
        if isinstance(item, dict):
            item["archived"] = True
            item["updated_at"] = _now()
            write_json(path, item)
            return True
    return False


def list_entities(character_id: str = GLOBAL_SCOPE) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in sorted(entities_dir(character_id).glob("*.json")):
        item = read_json(path)
        if isinstance(item, dict):
            result.append(item)
    return result


def get_entity(name: str, character_id: str = GLOBAL_SCOPE) -> dict[str, Any] | None:
    item = read_json(entities_dir(character_id) / f"{name}.json")
    return item if isinstance(item, dict) else None


def save_entity(entity: dict[str, Any]) -> dict[str, Any]:
    character_id = str(entity.get("character_id") or GLOBAL_SCOPE)
    name = str(entity.get("name") or "").strip()
    if not name:
        raise ValueError("实体名不能为空")
    payload = dict(entity)
    payload["name"] = name
    payload["character_id"] = character_id
    payload.setdefault("attributes", {})
    payload["updated_at"] = _now()
    write_json(entities_dir(character_id) / f"{name}.json", payload)
    return payload


def get_state(character_id: str = GLOBAL_SCOPE) -> dict[str, Any] | None:
    """读取角色级世界状态，没有时返回 None。"""
    state = read_json(state_path(character_id))
    return state if isinstance(state, dict) else None


def save_state(state: dict[str, Any], character_id: str = GLOBAL_SCOPE) -> dict[str, Any]:
    """写入角色级世界状态，返回落盘后的载荷。"""
    payload = dict(state)
    payload["character_id"] = character_id
    payload["updated_at"] = _now()
    write_json(state_path(character_id), payload)
    return payload


def get_setting(key: str, default: Any = None) -> Any:
    settings = read_json(settings_path(), {})
    if not isinstance(settings, dict):
        return default
    return settings.get(key, default)


def set_setting(key: str, value: Any) -> None:
    with _SETTINGS_LOCK:
        settings = read_json(settings_path(), {})
        if not isinstance(settings, dict):
            settings = {}
        settings[key] = value
        write_json(settings_path(), settings)


def list_profiles() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in sorted(profiles_dir().glob("*.json")):
        profile = read_json(path)
        if isinstance(profile, dict):
            result.append(profile)
    return result


def get_profile(profile_id: str) -> dict[str, Any] | None:
    profile = read_json(profiles_dir() / f"{profile_id}.json")
    return profile if isinstance(profile, dict) else None


def save_profile(profile: dict[str, Any]) -> dict[str, Any]:
    profile_id = str(profile.get("id") or new_id("pf"))
    payload = dict(profile)
    payload["id"] = profile_id
    payload.setdefault("name", profile_id)
    payload.setdefault("params", {})
    payload["updated_at"] = _now()
    write_json(profiles_dir() / f"{profile_id}.json", payload)
    return payload


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")
