"""推理适配抽象接口。

定义 InferenceAdapter 协议、ChatResult、InferenceParams、StreamEvent。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol, runtime_checkable

from app.contracts import ChatMessage, Usage


@dataclass
class StreamEvent:
    """流式事件。

    event 取值：context、delta、reasoning、usage、error、done。
    """

    event: str
    data: dict | None = None


@dataclass
class InferenceParams:
    """推理参数。"""

    temperature: float = 0.7
    top_p: float = 0.9
    max_tokens: int = 512
    repetition_penalty: float = 1.1
    extra: dict = field(default_factory=dict)


@dataclass
class ChatResult:
    """非流式推理结果。"""

    content: str
    reasoning: str | None = None
    finish_reason: str = "stop"
    usage: Usage | None = None


@runtime_checkable
class InferenceAdapter(Protocol):
    """推理适配器抽象接口。

    实现类需提供 chat 与 stream_chat 两个方法，以及 provider 属性。
    """

    async def chat(
        self,
        messages: list[ChatMessage],
        params: InferenceParams,
        profile: dict | None = None,
    ) -> ChatResult:
        """非流式调用，返回 ChatResult。"""
        ...

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        params: InferenceParams,
        profile: dict | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """流式调用，产出 StreamEvent 序列。

        事件顺序：context、若干 delta、可选 reasoning、usage、done。
        """
        ...

    @property
    def provider(self) -> str:
        """推理提供者标识。"""
        ...