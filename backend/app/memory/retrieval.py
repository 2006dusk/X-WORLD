"""混合检索。

BM25 与向量检索按权重合并。索引缺失或过期时自动重建。
无 numpy 或无向量文件时退化为纯关键词检索，并标记 approximate。
"""

from __future__ import annotations

import math
from typing import Any

from app.index.builder import build_index
from app.index.persistence import index_is_stale, load_index, load_vectors
from app.memory.embedder import get_embedder, tokenize

K1 = 1.5
B = 0.75


def _bm25(query: str, payload: dict[str, Any]) -> dict[int, float]:
    inverted = payload.get("inverted") or {}
    lengths = payload.get("lengths") or []
    total = int(payload.get("total_documents") or 0)
    if not inverted or not total:
        return {}
    average = float(payload.get("average_length") or 0.0) or 1.0
    scores: dict[int, float] = {}
    for token in tokenize(query):
        postings = inverted.get(token)
        if not postings:
            continue
        frequency = len(postings)
        idf = math.log(1.0 + (total - frequency + 0.5) / (frequency + 0.5))
        for index, count in postings:
            length = lengths[index] if index < len(lengths) else 0
            denominator = count + K1 * (1.0 - B + B * (length / average))
            scores[index] = scores.get(index, 0.0) + idf * (count * (K1 + 1.0)) / denominator
    return scores


def _vector(query: str, payload: dict[str, Any]) -> dict[int, float] | None:
    matrix = load_vectors()
    if matrix is None:
        return None
    try:
        import numpy as np
    except ImportError:
        return None
    if getattr(matrix, "shape", (0,))[0] == 0:
        return None
    embedder = get_embedder()
    vector = np.asarray(embedder.embed([query])[0], dtype="float32")
    norm = float(np.linalg.norm(vector))
    if norm == 0.0:
        return None
    matrix_norm = np.linalg.norm(matrix, axis=1)
    matrix_norm[matrix_norm == 0.0] = 1.0
    similarity = (matrix @ vector) / matrix_norm
    return {index: float(value) for index, value in enumerate(similarity)}


def hybrid_search(
    query: str,
    character_id: str | None = None,
    top_k: int = 8,
    bm25_weight: float = 0.6,
    vector_weight: float = 0.4,
    session_id: str | None = None,
    kind: str | None = None,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    """混合检索记忆条目。"""
    payload = load_index()
    if index_is_stale(payload):
        payload = build_index(character_id)
    if not payload:
        return []

    entries = payload.get("entries") or []
    keyword_scores = _bm25(query, payload)
    vector_scores = _vector(query, payload)
    approximate = vector_scores is None

    hits: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if not include_archived and entry.get("archived"):
            continue
        if character_id and entry.get("character_id") not in (None, character_id):
            continue
        if session_id and entry.get("session_id") != session_id:
            continue
        if kind and entry.get("kind") != kind:
            continue
        keyword = keyword_scores.get(index, 0.0)
        vector = (vector_scores or {}).get(index, 0.0)
        score = bm25_weight * keyword + vector_weight * vector
        if score <= 0.0 and keyword <= 0.0 and vector <= 0.0:
            continue
        hits.append(
            {
                "id": entry.get("id"),
                "character_id": entry.get("character_id"),
                "session_id": entry.get("session_id"),
                "kind": entry.get("kind"),
                "importance": entry.get("importance", 0.5),
                "score": score,
                "bm25": keyword,
                "vector": vector,
                "approximate": approximate,
            }
        )
    hits.sort(key=lambda item: (-item["score"], -float(item.get("importance") or 0.0)))
    return hits[:top_k]


def forget_scan(
    character_id: str | None = None,
    threshold: float = 0.3,
    days: int = 30,
) -> list[dict[str, Any]]:
    """扫描低重要度且长期未命中的记忆候选，只返回不删除。"""
    from datetime import datetime, timedelta, timezone

    from app.memory.store import list_memories

    limit = datetime.now(timezone.utc) - timedelta(days=days)
    candidates: list[dict[str, Any]] = []
    for item in list_memories(character_id=character_id):
        if float(item.get("importance") or 0.0) >= threshold:
            continue
        stamp = item.get("last_access_at") or item.get("created_at")
        if not stamp:
            continue
        try:
            moment = datetime.fromisoformat(str(stamp))
        except ValueError:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        if moment >= limit:
            continue
        candidates.append(item)
    return candidates
