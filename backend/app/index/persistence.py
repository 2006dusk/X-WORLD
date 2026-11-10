"""索引落盘。

index/index.json 存条目元数据与 BM25 倒排，index/vectors.npy 存向量矩阵，
两者行序一一对应。索引可由源文件重建，源文件修改时间晚于索引时失效。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app import store as file_store

INDEX_VERSION = 1


def index_dir() -> Path:
    return file_store.index_dir()


def index_path() -> Path:
    return index_dir() / "index.json"


def vectors_path() -> Path:
    return index_dir() / "vectors.npy"


def save_index(payload: dict[str, Any]) -> Path:
    """写入索引元数据与倒排。"""
    return file_store.write_json(index_path(), payload)


def load_index() -> dict[str, Any] | None:
    """读取索引元数据。"""
    payload = file_store.read_json(index_path())
    return payload if isinstance(payload, dict) else None


def save_vectors(matrix: Any) -> Path | None:
    """写入向量矩阵，无 numpy 时跳过。"""
    try:
        import numpy as np
    except ImportError:
        return None
    path = vectors_path()
    np.save(path, matrix)
    return path


def load_vectors() -> Any | None:
    """读取向量矩阵，无 numpy 或文件缺失时返回 None。"""
    try:
        import numpy as np
    except ImportError:
        return None
    path = vectors_path()
    if not path.exists():
        return None
    try:
        return np.load(path)
    except (OSError, ValueError):
        return None


def index_is_stale(payload: dict[str, Any] | None) -> bool:
    """判断索引是否过期。

    判定依据为源文件修改时间与索引记录的源文件修改时间对比。
    """
    if not payload:
        return True
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return True
    for entry in entries:
        path = entry.get("path")
        recorded = entry.get("mtime")
        if not path or recorded is None:
            return True
        source = Path(path)
        if not source.exists():
            return True
        try:
            if source.stat().st_mtime > float(recorded) + 1e-6:
                return True
        except OSError:
            return True
    return False
