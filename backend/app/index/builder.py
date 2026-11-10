"""索引构建。

从记忆源文件构建 BM25 倒排与向量矩阵。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app import store as file_store
from app.memory.embedder import get_embedder, tokenize

from .persistence import INDEX_VERSION, save_index, save_vectors


def _memory_scopes(character_id: str | None) -> list[str]:
    if character_id:
        return [character_id]
    base = file_store.config.data_root() / "memories"
    if not base.exists():
        return [file_store.GLOBAL_SCOPE]
    return sorted(entry.name for entry in base.iterdir() if entry.is_dir())


def _source_paths(character_id: str | None) -> list[Path]:
    paths: list[Path] = []
    for scope in _memory_scopes(character_id):
        paths.extend(sorted(file_store.memories_dir(scope).glob("*.json")))
    return paths


def build_index(character_id: str | None = None) -> dict[str, Any]:
    """构建索引并落盘，返回索引载荷。"""
    paths = _source_paths(character_id)
    entries: list[dict[str, Any]] = []
    documents: list[str] = []
    for path in paths:
        item = file_store.read_json(path)
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "")
        entries.append(
            {
                "id": str(item.get("id") or path.stem),
                "character_id": item.get("character_id"),
                "session_id": item.get("session_id"),
                "kind": item.get("kind"),
                "importance": item.get("importance", 0.5),
                "archived": bool(item.get("archived")),
                "path": str(path),
                "mtime": path.stat().st_mtime,
                "length": len(tokenize(content)),
            }
        )
        documents.append(content)

    inverted: dict[str, list[list[int]]] = {}
    lengths: list[int] = []
    for index, content in enumerate(documents):
        tokens = tokenize(content)
        lengths.append(len(tokens))
        counts: dict[str, int] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
        for token, count in counts.items():
            inverted.setdefault(token, []).append([index, count])

    total_length = sum(lengths)
    average = (total_length / len(lengths)) if lengths else 0.0
    payload: dict[str, Any] = {
        "version": INDEX_VERSION,
        "character_id": character_id,
        "entries": entries,
        "inverted": inverted,
        "lengths": lengths,
        "average_length": average,
        "total_documents": len(documents),
    }
    save_index(payload)

    embedder = get_embedder()
    vectors = embedder.embed(documents)
    try:
        import numpy as np

        matrix = np.asarray(vectors, dtype="float32")
        if matrix.size == 0:
            matrix = np.zeros((0, 512), dtype="float32")
        save_vectors(matrix)
    except ImportError:
        pass
    return payload
