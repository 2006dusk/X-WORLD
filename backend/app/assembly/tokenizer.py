"""Tokenizer 协议与实现。

定义 Tokenizer 协议 count(text) -> int，提供两类实现：
- ApproxTokenizer：字符启发式估算
- LlamaCppTokenizer：通过本地 OpenAI 兼容服务探测真实分词
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import httpx

from app.config import HOST

# 真实分词计数端点。自写服务端 world-work-cpp 提供
# POST /tokenize，请求体 {"content": 文本}，响应含 count 字段。
COUNT_PATH = "/tokenize"
COUNT_FIELD = "content"
PROBE_TEXT = "你好 world"

_CLIENT: httpx.Client | None = None


def _shared_client(timeout: float = 5.0) -> httpx.Client:
    """共享的 httpx 客户端。一次装配要计多次数，连接不逐次重建。"""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = httpx.Client(timeout=timeout)
    return _CLIENT


@runtime_checkable
class Tokenizer(Protocol):
    """分词器协议：提供 count(text) -> int 方法。"""

    def count(self, text: str) -> int:
        """返回文本的 token 计数。"""
        ...

    @property
    def approximate(self) -> bool:
        """是否使用了近似估算。"""
        ...

    @property
    def name(self) -> str:
        """分词器名称。"""
        ...


class ApproxTokenizer:
    """基于字符启发式的近似分词器。

    - 中文字符按 1 字 ≈ 1 token 估算
    - ASCII/英文按约 4 字符 ≈ 1 token 估算
    - 其他 Unicode 字符按 1 字 ≈ 1 token 估算
    """

    def __init__(self) -> None:
        self._name = "approx"

    @property
    def approximate(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return self._name

    def count(self, text: str) -> int:
        """估算 token 数。"""
        if not text:
            return 0
        tokens = 0
        i = 0
        ascii_run = 0
        while i < len(text):
            ch = text[i]
            if ord(ch) < 128:
                ascii_run += 1
            else:
                # 非 ASCII：先结算 ASCII 累积
                if ascii_run > 0:
                    tokens += max(1, ascii_run // 4)
                    ascii_run = 0
                # 中文 / CJK 统一表意文字范围
                if _is_cjk(ch):
                    tokens += 1
                else:
                    tokens += 1
            i += 1
        if ascii_run > 0:
            tokens += max(1, ascii_run // 4)
        return max(1, tokens)


def _is_cjk(ch: str) -> bool:
    """判断字符是否属于 CJK 统一表意文字。"""
    cp = ord(ch)
    return (
        (0x4E00 <= cp <= 0x9FFF)  # CJK Unified
        or (0x3400 <= cp <= 0x4DBF)  # CJK Extension A
        or (0x20000 <= cp <= 0x2A6DF)  # CJK Extension B
        or (0x2A700 <= cp <= 0x2B73F)  # CJK Extension C
        or (0x2B740 <= cp <= 0x2B81F)  # CJK Extension D
        or (0x2B820 <= cp <= 0x2CEAF)  # CJK Extension E
        or (0xF900 <= cp <= 0xFAFF)  # CJK Compatibility
        or (0x2F800 <= cp <= 0x2FA1F)  # CJK Compatibility Supplement
        or (0x3000 <= cp <= 0x303F)  # CJK Symbols
        or (0xFF00 <= cp <= 0xFFEF)  # Halfwidth/Fullwidth
        or (0xFE30 <= cp <= 0xFE4F)  # CJK Compatibility Forms
    )


class LlamaCppTokenizer:
    """通过本地推理服务的计数端点获取真实 token 计数。

    计数走 /tokenize，响应含 count 字段。端点不可达时自动回退到
    ApproxTokenizer，并标记 approximate=True。
    """

    def __init__(self, base_url: str = "", timeout: float = 5.0) -> None:
        self._base_url = (base_url or f"http://{HOST}:8080").rstrip("/")
        self._timeout = timeout
        self._name = "llamacpp"
        self._approx = ApproxTokenizer()
        self._available: bool | None = None  # None=未探测
        self._client = _shared_client(timeout)

    @property
    def approximate(self) -> bool:
        if self._available is None:
            self._probe()
        return self._available is False

    @property
    def name(self) -> str:
        return self._name

    def _probe(self) -> None:
        """探测计数端点是否可用。"""
        try:
            resp = self._client.post(
                f"{self._base_url}{COUNT_PATH}",
                json={COUNT_FIELD: PROBE_TEXT},
            )
            self._available = resp.status_code == 200
        except Exception:
            self._available = False

    def count(self, text: str) -> int:
        """获取真实 token 计数；端点不可用时回退到 ApproxTokenizer。"""
        if not text:
            return 0
        if self._available is None:
            self._probe()
        if self._available is False:
            return self._approx.count(text)

        try:
            resp = self._client.post(
                f"{self._base_url}{COUNT_PATH}",
                json={COUNT_FIELD: text},
            )
            if resp.status_code == 200:
                data = resp.json()
                count = data.get("count")
                if isinstance(count, int):
                    return count
            # 非 200 或响应形状异常：回退
            self._available = False
            return self._approx.count(text)
        except Exception:
            self._available = False
            return self._approx.count(text)


def get_tokenizer(profile: dict | None = None) -> Tokenizer:
    """根据 profile 选择分词器。

    profile 中可包含 tokenizer_type 字段：
    - "llamacpp" → LlamaCppTokenizer
    - 其他/缺失 → ApproxTokenizer
    """
    if profile is None:
        profile = {}
    tokenizer_type = profile.get("tokenizer_type", "")
    if tokenizer_type == "llamacpp":
        base_url = profile.get("tokenizer_url", "")
        return LlamaCppTokenizer(base_url=base_url)
    return ApproxTokenizer()