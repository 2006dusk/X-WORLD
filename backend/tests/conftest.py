# -*- coding: utf-8 -*-
"""pytest 共享夹具与测试替身。

仓库原先没有 conftest：data_dir 夹具在二十多个测试文件里各写一份，
假适配器也在几个文件里各写一份。data_dir 干的事只有一件——把数据目录
钉到临时目录；假适配器干的事也只有一件——按顺序吐正文。两件都在这里
写一份，各测试文件直接用。

需要额外播种（角色卡、世界书、预设）的测试文件仍然自己定义 data_dir，
模块内的同名夹具会盖掉这里这份。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.contracts import BudgetInfo, Usage
from app.inference.base import ChatResult, StreamEvent

# 用量里的预算账。BudgetInfo 的三个字段都是必填的，写 BudgetInfo() 会直接
# 抛校验错误——原来三个测试文件里的假适配器都是这么写的，只是谁也没走到
# 非流式那条路，错误一直藏着。
_BUDGET = BudgetInfo(limit=1000, reserved_output=20, used=30)


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把数据目录指向临时目录，测试之间互不干扰。"""
    monkeypatch.setenv("XWORLD_DATA_DIR", str(tmp_path))
    return tmp_path


class FakeAdapter:
    """按预设顺序返回正文的假适配器。"""

    def __init__(self, texts: str | list[str] = "", error: Exception | None = None) -> None:
        self.texts = [texts] if isinstance(texts, str) else list(texts)
        self.error = error
        self.calls = 0
        self.seen: list[list[Any]] = []

    @property
    def provider(self) -> str:
        """适配器标识。"""
        return "fake"

    def _next(self) -> str:
        """取下一段正文。给了 error 就先抛错，模拟推理服务不可用。"""
        if self.error is not None:
            raise self.error
        index = min(self.calls, len(self.texts) - 1)
        self.calls += 1
        return self.texts[index]

    async def chat(self, messages, params, profile=None) -> ChatResult:
        """非流式补全。"""
        self.seen.append(list(messages))
        return ChatResult(
            content=self._next(),
            finish_reason="stop",
            usage=Usage(prompt_tokens=10, completion_tokens=20, budget=_BUDGET),
        )

    async def stream_chat(self, messages, params, profile=None):
        """流式补全，按整段正文给一个 delta。"""
        self.seen.append(list(messages))
        text = self._next()
        yield StreamEvent(event="delta", data={"content": text})
        yield StreamEvent(
            event="usage",
            data=Usage(prompt_tokens=10, completion_tokens=20, budget=_BUDGET).model_dump(),
        )
        yield StreamEvent(event="done", data={"finish_reason": "stop"})
