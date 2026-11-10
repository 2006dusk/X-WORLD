"""向量化。

默认实现为确定性哈希特征投影，只保证同一语料的相对排序稳定，
不具备语义泛化能力。语义检索需接入本地嵌入模型后替换本模块。
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol, Sequence

DIM = 512

_CJK = re.compile(r"[\u4e00-\u9fff]")
_WORD = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    """向量编码器接口。"""

    name: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """把文本序列编码为固定维度的单位向量。"""
        ...


def tokenize(text: str) -> list[str]:
    """中文按字切分，英文数字按词切分，统一小写。"""
    lowered = text.lower()
    tokens = _WORD.findall(lowered)
    tokens.extend(_CJK.findall(lowered))
    return tokens


def _feature_index(token: str) -> tuple[int, float]:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    index = value % DIM
    sign = 1.0 if (value >> 63) & 1 else -1.0
    return index, sign


class HashEmbedder:
    """确定性哈希嵌入。跨进程稳定，可复现，无外部依赖。"""

    name = "hash-512"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * DIM
            counts: dict[str, int] = {}
            for token in tokenize(text):
                counts[token] = counts.get(token, 0) + 1
            for token, count in counts.items():
                index, sign = _feature_index(token)
                vector[index] += sign * (1.0 + math.log(count))
            norm = math.sqrt(sum(value * value for value in vector))
            if norm > 0.0:
                vector = [value / norm for value in vector]
            vectors.append(vector)
        return vectors


_EMBEDDER: Embedder | None = None


def get_embedder() -> Embedder:
    """取得当前向量器。"""
    global _EMBEDDER
    if _EMBEDDER is None:
        _EMBEDDER = HashEmbedder()
    return _EMBEDDER
