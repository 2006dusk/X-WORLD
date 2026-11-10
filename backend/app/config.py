"""数据目录与运行参数配置。

数据根目录按 XWORLD_DATA_DIR 环境变量覆盖，默认 %APPDATA%/X-WORLD。
所有路径一律经本模块获取，模块内不出现硬编码盘符。
"""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "X-WORLD"
HOST = "127.0.0.1"
# 网页服务的监听地址。局域网访问用 0.0.0.0，默认只监听本机。
# 推理服务仍用 HOST：模型服务不对外暴露。
WEB_HOST = os.environ.get("XWORLD_WEB_HOST", "127.0.0.1")
DEFAULT_PORT = 8760
DEFAULT_MAX_CONTEXT_TOKENS = 8192
DEFAULT_RESERVED_OUTPUT_TOKENS = 1024
# 本地推理调用的超时秒数。默认 600：本机 9B 量化模型在 16K 上下文下
# 生成一轮 150 到 300 字可能要一两分钟，120 秒会误判成超时并回 502。
# 用 XWORLD_INFERENCE_TIMEOUT 覆盖。
INFERENCE_TIMEOUT = float(os.environ.get("XWORLD_INFERENCE_TIMEOUT", "600"))
# 连接超时单独收紧：连不上要立刻报错，不要等满整个推理超时。
INFERENCE_CONNECT_TIMEOUT = float(
    os.environ.get("XWORLD_INFERENCE_CONNECT_TIMEOUT", "10")
)


def _base() -> Path:
    override = os.environ.get("XWORLD_DATA_DIR")
    if override:
        return Path(override).expanduser()
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_NAME
    return Path.home() / ".config" / APP_NAME


def data_root() -> Path:
    root = _base()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _sub(name: str) -> Path:
    path = data_root() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def workspaces_dir() -> Path:
    return _sub("workspaces")


def models_dir() -> Path:
    return _sub("models")


def plugins_dir() -> Path:
    return _sub("plugins")


def logs_dir() -> Path:
    return _sub("logs")


def workspace_dir(card_id: str) -> Path:
    path = workspaces_dir() / card_id
    path.mkdir(parents=True, exist_ok=True)
    return path
