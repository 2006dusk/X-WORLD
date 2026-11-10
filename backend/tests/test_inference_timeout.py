"""推理调用超时的约定测试。

本机 9B 量化模型在 16K 上下文下生成一轮要 17 到 132 秒。超时一旦按 120 秒算，
长上下文就会在模型还在算的时候被判超时，后端回 502「推理服务超时」。
所以超时必须可配置、默认足够长，而连接超时要单独收紧。
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

from app import config
from app.inference.llamacpp import LlamaCppAdapter
from app.inference.openai_compat import OpenAICompatAdapter

INFERENCE_DIR = Path(__file__).resolve().parent.parent / "app" / "inference"


def test_default_timeout_is_long_enough_for_local_models() -> None:
    """默认推理超时不低于 600 秒，连接超时不超过 30 秒。"""
    assert config.INFERENCE_TIMEOUT >= 600.0
    assert config.INFERENCE_CONNECT_TIMEOUT <= 30.0


def test_timeout_reads_env_override() -> None:
    """环境变量能覆盖默认超时。"""
    original = os.environ.get("XWORLD_INFERENCE_TIMEOUT")
    os.environ["XWORLD_INFERENCE_TIMEOUT"] = "900"
    try:
        reloaded = importlib.reload(config)
        assert reloaded.INFERENCE_TIMEOUT == 900.0
    finally:
        if original is None:
            os.environ.pop("XWORLD_INFERENCE_TIMEOUT", None)
        else:
            os.environ["XWORLD_INFERENCE_TIMEOUT"] = original
        importlib.reload(config)


def test_llamacpp_client_uses_configured_timeout() -> None:
    """llama.cpp 适配器的客户端用的是配置值。"""
    adapter = LlamaCppAdapter(base_url="http://127.0.0.1:8080")
    timeout = adapter._client.timeout
    assert timeout.read == config.INFERENCE_TIMEOUT
    assert timeout.connect == config.INFERENCE_CONNECT_TIMEOUT


def test_openai_compat_client_uses_configured_timeout() -> None:
    """通用兼容适配器的客户端用的是同一套配置。"""
    adapter = OpenAICompatAdapter(base_url="http://127.0.0.1:8080")
    timeout = adapter._client.timeout
    assert timeout.read == config.INFERENCE_TIMEOUT
    assert timeout.connect == config.INFERENCE_CONNECT_TIMEOUT


def test_no_adapter_hardcodes_two_minute_timeout() -> None:
    """适配器里不许再出现写死的 120 秒。"""
    offenders = []
    for path in sorted(INFERENCE_DIR.glob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if "timeout=120.0" in line or "timeout = 120.0" in line:
                offenders.append(f"{path.name}:{number}")
    assert not offenders, f"写死的 120 秒超时: {offenders}"
