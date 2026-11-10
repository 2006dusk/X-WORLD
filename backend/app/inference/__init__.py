"""推理适配层。

提供统一的推理适配接口与 llama.cpp / OpenAI 兼容实现。
模型档位声明在 traits，服务状态探测在 service。
"""

from __future__ import annotations

from .base import ChatResult, InferenceAdapter, InferenceParams, StreamEvent
from .service import base_url, probe_service
from .traits import DEFAULT_PROFILES, ModelProfile, default_profile, resolve_profile

__all__ = [
    "ChatResult",
    "DEFAULT_PROFILES",
    "InferenceAdapter",
    "InferenceParams",
    "ModelProfile",
    "StreamEvent",
    "base_url",
    "default_profile",
    "probe_service",
    "resolve_profile",
]
