"""llama.cpp server 推理适配。

对接本地 llama.cpp server 的 OpenAI 兼容端点，服务地址经 app.store 的
settings 读取，默认 http://127.0.0.1:8080。

把上游差异归一为六种事件：context、delta、reasoning、usage、error、done。
上游不可达时抛 ApiError(code="backend_unavailable", retryable=True)。

计量归一：usage 统一为 contracts.Usage。
数据来源一律经 app.store，不 import app.db、不使用 sqlite3。
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import httpx

from app import store
from app.config import HOST, INFERENCE_CONNECT_TIMEOUT, INFERENCE_TIMEOUT
from app.contracts import BudgetInfo, ChatMessage, Usage
from app.errors import ApiError

from .base import ChatResult, InferenceAdapter, InferenceParams, StreamEvent

logger = logging.getLogger(__name__)
from .normalize import (
    QuoteStreamNormalizer,
    TEMPLATE_MARKERS,
    cut_at_template_marker,
    normalize_cjk_quotes,
)


class LlamaCppAdapter:
    """llama.cpp server 推理适配器。

    通过 OpenAI 兼容端点进行推理。
    """

    def __init__(self, base_url: str | None = None) -> None:
        if base_url is not None:
            self._base_url = base_url.rstrip("/")
        else:
            self._base_url = self._read_base_url()
        self._http_client: httpx.AsyncClient | None = None
        # 推理端是否支持按请求挂控制向量。探测一次就记住肯定的结果。
        self._cvec_support: bool | None = None

    @property
    def provider(self) -> str:
        return "llamacpp"

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
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(INFERENCE_TIMEOUT, connect=INFERENCE_CONNECT_TIMEOUT)
            )
        return self._http_client

    async def _supports_control_vector(self) -> bool:
        """推理端是否支持按请求挂控制向量。

        能力探测走 /props：自写服务端声明 control_vector.per_request，
        别的推理端没有这个字段，说明它不认这个请求字段。

        只缓存肯定的结果：探测本身失败时，下次还要再问一次。
        """
        if self._cvec_support is not None:
            return self._cvec_support
        try:
            resp = await self._client.get(f"{self._base_url}/props", timeout=10.0)
        except Exception:
            return False
        if resp.status_code != 200:
            return False
        try:
            props = resp.json()
        except ValueError:
            return False
        info = props.get("control_vector") if isinstance(props, dict) else None
        if isinstance(info, dict) and info.get("per_request"):
            self._cvec_support = True
            return True
        return False

    async def _drop_unsupported_control_vector(self, payload: dict) -> None:
        """推理端不认 control_vector 时，把它从请求体里去掉。

        没有这个字段的推理端会拒掉整个请求，不挂向量总比整轮失败好。
        """
        if "control_vector" not in payload:
            return
        if not await self._supports_control_vector():
            payload.pop("control_vector", None)

    async def chat(
        self,
        messages: list[ChatMessage],
        params: InferenceParams,
        profile: dict | None = None,
    ) -> ChatResult:
        """非流式补全。

        参数：
            messages: 已装配的消息序列。
            params: 推理参数。
            profile: 模型 profile 字典。

        返回：
            ChatResult。

        异常：
            ApiError(code="backend_unavailable") — 服务不可达。
        """
        payload = self._build_payload(messages, params, profile, stream=False)
        await self._drop_unsupported_control_vector(payload)
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
                message="无法连接推理服务，请确认 llama.cpp server 已启动",
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
        """流式补全，产出归一化事件序列。

        事件顺序：context、若干 delta、可选 reasoning、usage、done。
        错误时：error 事件后接 done。
        """
        payload = self._build_payload(messages, params, profile, stream=True)
        await self._drop_unsupported_control_vector(payload)
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
                cached_tokens = 0
                normalizer = QuoteStreamNormalizer()

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
                                data={"content": normalizer.feed(content)},
                            )
                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]

                    # 收集 usage
                    usage_data = chunk.get("usage")
                    if usage_data:
                        prompt_tokens = usage_data.get("prompt_tokens", prompt_tokens)
                        completion_tokens = usage_data.get(
                            "completion_tokens", completion_tokens
                        )
                        # 前缀复用命中的 token 数。上游没给这个字段时保持 0。
                        cached_tokens = usage_data.get("cached_tokens", cached_tokens)

                # 冲刷流式归一保留的半个引号
                tail = normalizer.flush()
                if tail:
                    yield StreamEvent(
                        event="delta",
                        data={"content": tail},
                    )

                # 发送 usage
                budget = BudgetInfo(limit=0, reserved_output=0, used=prompt_tokens)
                usage = Usage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    reasoning_tokens=0,
                    cached_tokens=cached_tokens,
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
                    "message": "无法连接推理服务，请确认 llama.cpp server 已启动",
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
        """构建 OpenAI 兼容请求体。"""
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
            "repeat_penalty": params.repetition_penalty,
        }

        # 合并 profile 中的额外参数
        if profile:
            for key in ("top_k", "min_p", "typical_p", "presence_penalty",
                         "frequency_penalty", "seed", "stop",
                         "dynatemp_range", "dynatemp_exponent",
                         "xtc_probability", "xtc_threshold",
                         "dry_multiplier", "dry_base", "dry_allowed_length",
                         "dry_penalty_last_n"):
                if key in profile:
                    payload[key] = profile[key]

        # 合并 extra 参数
        payload.update(params.extra)

        # 模板标记当 stop 发出去：模型被推离分布时会自己续写多轮对话，
        # 把 <|im_start|> 这类标记当正文吐出来。只在事后截断的话，标记后面的
        # token 已经白生成了（实测一轮能一路烧到 max_tokens 上限，白等好几秒）。
        # 服务端按累积内容匹配 stop，标记跨 token 切分也拦得住。
        stops = list(TEMPLATE_MARKERS)
        extra_stop = payload.get("stop")
        if isinstance(extra_stop, str):
            stops.append(extra_stop)
        elif isinstance(extra_stop, (list, tuple)):
            stops.extend(str(item) for item in extra_stop if item)
        payload["stop"] = stops
        return payload

    def _parse_response(self, data: dict) -> ChatResult:
        """解析非流式响应。"""
        choices = data.get("choices", [])
        content = ""
        reasoning = None
        finish_reason = "stop"
        if choices:
            choice = choices[0]
            message = choice.get("message", {})
            content = normalize_cjk_quotes(str(message.get("content", "") or ""))
            content = cut_at_template_marker(content)
            reasoning = message.get("reasoning_content")
            finish_reason = choice.get("finish_reason", "stop")

        # The server says when the control vector did not go on: the
        # vector belongs to one model, so a model swap leaves it unused.
        vector_info = data.get("control_vector")
        if isinstance(vector_info, dict) and vector_info.get("note"):
            logger.warning("控制向量未生效：%s", vector_info.get("note"))

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
