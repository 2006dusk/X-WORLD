"""通用 OpenAI 兼容适配。

与 llama.cpp 同接口，便于日后切换到任意 OpenAI 兼容后端
（Ollama、vLLM、KoboldCpp 等）。

地址经 app.store 的 settings 读取，默认 http://127.0.0.1:8080。
数据来源一律经 app.store，不 import app.db、不使用 sqlite3。
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from app import store
from app.config import HOST, INFERENCE_CONNECT_TIMEOUT, INFERENCE_TIMEOUT
from app.contracts import BudgetInfo, ChatMessage, Usage
from app.errors import ApiError

from .base import ChatResult, InferenceAdapter, InferenceParams, StreamEvent


class OpenAICompatAdapter:
    """通用 OpenAI 兼容推理适配器。

    行为与 LlamaCppAdapter 一致，便于切换后端时无需改动上层代码。
    """

    def __init__(self, base_url: str | None = None, api_key: str = "") -> None:
        if base_url is not None:
            self._base_url = base_url.rstrip("/")
        else:
            self._base_url = self._read_base_url()
        self._api_key = api_key
        self._http_client: httpx.AsyncClient | None = None

    @property
    def provider(self) -> str:
        return "openai_compat"

    def _read_base_url(self) -> str:
        """从 app.store 的 settings 读取推理服务地址，默认 http://127.0.0.1:8080。"""
        try:
            value = store.get_setting("inference_url")
            if isinstance(value, dict):
                url = value.get("url")
                if isinstance(url, str) and url:
                    return url.rstrip("/")
            elif isinstance(value, str) and value:
                return value.rstrip("/")
        except Exception:
            pass
        return f"http://{HOST}:8080"

    @property
    def _client(self) -> httpx.AsyncClient:
        """惰性创建 httpx 异步客户端。"""
        if self._http_client is None:
            headers = {"Content-Type": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(INFERENCE_TIMEOUT, connect=INFERENCE_CONNECT_TIMEOUT),
                headers=headers,
            )
        return self._http_client

    async def chat(
        self,
        messages: list[ChatMessage],
        params: InferenceParams,
        profile: dict | None = None,
    ) -> ChatResult:
        """非流式补全。"""
        payload = self._build_payload(messages, params, profile, stream=False)
        try:
            resp = await self._client.post(
                f"{self._base_url}/v1/chat/completions",
                json=payload,
            )
            if resp.status_code >= 500:
                raise ApiError(
                    code="backend_unavailable",
                    message=f"推理服务返回 {resp.status_code}",
                    retryable=True,
                    status_code=502,
                )
            if resp.status_code != 200:
                error_text = resp.text[:200]
                raise ApiError(
                    code="inference_error",
                    message=f"推理失败: {error_text}",
                    retryable=False,
                    status_code=502,
                )
            data = resp.json()
            return self._parse_response(data)
        except httpx.ConnectError:
            raise ApiError(
                code="backend_unavailable",
                message="无法连接推理服务",
                retryable=True,
                status_code=502,
            )
        except httpx.TimeoutException:
            raise ApiError(
                code="backend_unavailable",
                message="推理服务超时",
                retryable=True,
                status_code=502,
            )

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        params: InferenceParams,
        profile: dict | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """流式补全。"""
        payload = self._build_payload(messages, params, profile, stream=True)
        try:
            async with self._client.stream(
                "POST",
                f"{self._base_url}/v1/chat/completions",
                json=payload,
            ) as resp:
                if resp.status_code >= 500:
                    yield StreamEvent(
                        event="error",
                        data={
                            "code": "backend_unavailable",
                            "message": f"推理服务返回 {resp.status_code}",
                            "retryable": True,
                        },
                    )
                    yield StreamEvent(event="done", data={"finish_reason": "error"})
                    return
                if resp.status_code != 200:
                    error_text = ""
                    async for chunk in resp.aiter_text():
                        error_text += chunk
                        if len(error_text) > 200:
                            break
                    yield StreamEvent(
                        event="error",
                        data={
                            "code": "inference_error",
                            "message": f"推理失败: {error_text[:200]}",
                            "retryable": False,
                        },
                    )
                    yield StreamEvent(event="done", data={"finish_reason": "error"})
                    return

                finish_reason = "stop"
                prompt_tokens = 0
                completion_tokens = 0

                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[len("data:"):].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    choices = chunk.get("choices", [])
                    if choices:
                        choice = choices[0]
                        delta = choice.get("delta", {})
                        content = delta.get("content", "")
                        reasoning = delta.get("reasoning_content", "")

                        if reasoning:
                            yield StreamEvent(
                                event="reasoning",
                                data={"content": reasoning},
                            )
                        if content:
                            yield StreamEvent(
                                event="delta",
                                data={"content": content},
                            )
                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]

                    usage_data = chunk.get("usage")
                    if usage_data:
                        prompt_tokens = usage_data.get("prompt_tokens", prompt_tokens)
                        completion_tokens = usage_data.get(
                            "completion_tokens", completion_tokens
                        )

                budget = BudgetInfo(limit=0, reserved_output=0, used=prompt_tokens)
                usage = Usage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    reasoning_tokens=0,
                    cached_tokens=0,
                    budget=budget,
                )
                yield StreamEvent(event="usage", data=usage.model_dump())

                yield StreamEvent(
                    event="done",
                    data={"finish_reason": finish_reason},
                )
        except httpx.ConnectError:
            yield StreamEvent(
                event="error",
                data={
                    "code": "backend_unavailable",
                    "message": "无法连接推理服务",
                    "retryable": True,
                },
            )
            yield StreamEvent(event="done", data={"finish_reason": "error"})
        except httpx.TimeoutException:
            yield StreamEvent(
                event="error",
                data={
                    "code": "backend_unavailable",
                    "message": "推理服务超时",
                    "retryable": True,
                },
            )
            yield StreamEvent(event="done", data={"finish_reason": "error"})

    def _build_payload(
        self,
        messages: list[ChatMessage],
        params: InferenceParams,
        profile: dict | None,
        stream: bool,
    ) -> dict:
        """构建请求体。"""
        msgs = []
        for m in messages:
            d = {"role": m.role, "content": m.content}
            if m.reasoning:
                d["reasoning_content"] = m.reasoning
            msgs.append(d)

        payload: dict = {
            "messages": msgs,
            "stream": stream,
            "temperature": params.temperature,
            "top_p": params.top_p,
            "max_tokens": params.max_tokens,
        }

        if profile:
            for key in ("top_k", "min_p", "typical_p", "presence_penalty",
                         "frequency_penalty", "repetition_penalty", "seed", "stop"):
                if key in profile:
                    payload[key] = profile[key]

        payload.update(params.extra)
        return payload

    def _parse_response(self, data: dict) -> ChatResult:
        """解析响应。"""
        choices = data.get("choices", [])
        content = ""
        reasoning = None
        finish_reason = "stop"
        if choices:
            choice = choices[0]
            message = choice.get("message", {})
            content = message.get("content", "")
            reasoning = message.get("reasoning_content")
            finish_reason = choice.get("finish_reason", "stop")

        usage_data = data.get("usage", {})
        usage = Usage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            reasoning_tokens=usage_data.get("reasoning_tokens", 0),
            cached_tokens=usage_data.get("cached_tokens", 0),
            budget=BudgetInfo(
                limit=0,
                reserved_output=0,
                used=usage_data.get("prompt_tokens", 0),
            ),
        )

        return ChatResult(
            content=content,
            reasoning=reasoning,
            finish_reason=finish_reason,
            usage=usage,
        )
