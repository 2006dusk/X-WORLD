"""自写推理服务端的定位、命令拼装与进程识别。

只有一套服务端：``world-work-cpp``。源码在仓库的 ``world-work-cpp/``，
编译产物在它自己的 ``build/`` 目录。它比原先并存的 Python 版多三样能力：
按请求挂控制向量、原地换模型、运行时生成声线并做似然判定。

本模块只管三件事：可执行文件在哪、命令行怎么拼、怎么认出「这个进程是
推理服务」。放在一起是因为它们互相依赖：拼命令要知道 exe 在哪，
认进程要知道命令行长什么样。
"""

from __future__ import annotations

import os
from pathlib import Path

from .traits import ModelProfile

# 覆盖可执行文件位置的环境变量。打包运行或换机器时用。
ENV_EXE = "XWORLD_WWC_PATH"

# 仓库根：backend/app/inference/servers.py 往上三级。
REPO_ROOT = Path(__file__).resolve().parents[3]

# 编译产物目录与可执行文件名。
WWC_BUILD_DIR = REPO_ROOT / "world-work-cpp" / "build"
WWC_EXE_NAME = "world-work-cpp"

# 命令行里认出推理服务的标志。
INFERENCE_COMMAND_MARKER = WWC_EXE_NAME


def wwc_executable() -> Path:
    """找自写服务端可执行文件。

    顺序：环境变量 → 仓库内编译产物 → 无扩展名的同名文件。
    找不到就抛错并把编译命令写进错误信息：这是最常见的配置问题，
    不该让人对着「文件不存在」猜下一步做什么。
    """
    override = os.environ.get(ENV_EXE)
    if override:
        path = Path(override)
        if path.is_file():
            return path
        raise FileNotFoundError(f"{ENV_EXE} 指向的文件不存在：{path}")

    names = [WWC_EXE_NAME + ".exe", WWC_EXE_NAME] if os.name == "nt" else [WWC_EXE_NAME]
    for name in names:
        candidate = WWC_BUILD_DIR / name
        if candidate.is_file():
            return candidate

    build_script = REPO_ROOT / "world-work-cpp" / "build.ps1"
    raise FileNotFoundError(
        f"找不到自写推理服务端可执行文件（{WWC_BUILD_DIR}）。"
        f"先编译一次：powershell -NoProfile -ExecutionPolicy Bypass -File \"{build_script}\""
    )


def server_command(profile: ModelProfile, host: str, port: int) -> list[str]:
    """拼出拉起某个档位的完整命令。"""
    return [
        str(wwc_executable()),
        "--host",
        host,
        "--port",
        str(port),
        *profile.server_args(),
    ]


def is_inference_command(command: str) -> bool:
    """命令行看起来是不是推理服务。

    认领与收尾都靠它筛进程：端口上可能有别的程序占着，不能一律当成
    推理服务去认领或杀掉。
    """
    if not command:
        return False
    return INFERENCE_COMMAND_MARKER in command.lower()
