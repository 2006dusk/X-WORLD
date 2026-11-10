"""研究脚本起自写推理服务端的公共工具。

推理端只有自写服务端 world-work-cpp 一套，源码在仓库的 world-work-cpp/。
这些脚本原先拉的是 Python 版 llama_cpp.server：参数名用下划线写法，
KV 缓存取整数枚举（0=f16、1=q8_0、2=q4_0）。自写服务端用连字符写法，
KV 取字符串枚举。差异收在本模块：脚本只给模型路径与参数，
命令行怎么拼由这里负责。

用法：
    proc, log_path, handle = start_server(model_path, args, port)
    ...
    stop_server(proc, log_path, handle)
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

HOST = "127.0.0.1"

# 旧服务端 KV 缓存的整数枚举到自写服务端的字符串枚举。
KV_ENUM = {"0": "f16", "1": "q8_0", "2": "q4_0"}

# 带值的参数：翻译时要把后面的值一起搬过去。
_VALUED = ("--n_ctx", "--n_gpu_layers", "--type_k", "--type_v", "--chat_format")

# 仓库根：backend/tools/wwc_server.py 往上两级。
REPO_ROOT = Path(__file__).resolve().parents[2]
WWC_BUILD_DIR = REPO_ROOT / "world-work-cpp" / "build"
WWC_EXE_NAME = "world-work-cpp"
ENV_EXE = "XWORLD_WWC_PATH"


def wwc_executable() -> str:
    """找自写服务端可执行文件。

    顺序：环境变量 → 仓库内编译产物 → 无扩展名的同名文件。
    找不到就说清怎么编译：这是最常见的配置问题。
    """
    override = os.environ.get(ENV_EXE)
    if override:
        path = Path(override)
        if path.is_file():
            return str(path)
        raise FileNotFoundError(f"{ENV_EXE} 指向的文件不存在：{path}")

    names = [WWC_EXE_NAME + ".exe", WWC_EXE_NAME] if os.name == "nt" else [WWC_EXE_NAME]
    for name in names:
        candidate = WWC_BUILD_DIR / name
        if candidate.is_file():
            return str(candidate)

    build_script = REPO_ROOT / "world-work-cpp" / "build.ps1"
    raise FileNotFoundError(
        f"找不到自写推理服务端可执行文件（{WWC_BUILD_DIR}）。"
        f"先编译一次：powershell -NoProfile -ExecutionPolicy Bypass -File \"{build_script}\""
    )


def translate_args(args: list[str]) -> list[str]:
    """把旧服务端写法翻译成自写服务端写法。

    留着这层是为了让脚本里已经写好的用例表不用整张重写：
    --n_ctx → --ctx-size，--n_gpu_layers → --n-gpu-layers，
    --type_k/--type_v 的整数枚举 → --cache-type-k/--cache-type-v 的字符串枚举，
    --chat_format 直接丢掉：自写服务端固定用模型自带模板。
    """
    out: list[str] = []
    index = 0
    while index < len(args):
        token = str(args[index])
        value = str(args[index + 1]) if index + 1 < len(args) else ""
        if token == "--n_ctx":
            out += ["--ctx-size", value]
        elif token == "--n_gpu_layers":
            out += ["--n-gpu-layers", value]
        elif token in ("--type_k", "--type_v"):
            name = "--cache-type-k" if token == "--type_k" else "--cache-type-v"
            out += [name, KV_ENUM.get(value, value)]
        elif token == "--chat_format":
            pass
        else:
            out.append(token)
        index += 2 if token in _VALUED else 1
    return out


def server_command(model: str, args: list[str], port: int, host: str = HOST) -> list[str]:
    """拼出拉起服务端的完整命令。"""
    return [
        wwc_executable(),
        "--model",
        str(model),
        "--host",
        host,
        "--port",
        str(port),
        *translate_args([str(item) for item in args]),
    ]


def wait_ready(base: str, proc: subprocess.Popen, timeout: float = 420.0) -> bool:
    """等服务可用：进程还在，且端口上应答的就是它。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            if httpx.get(f"{base}/v1/models", timeout=2.0).status_code == 200:
                return True
        except Exception:  # noqa: BLE001 - 没起来就继续等
            pass
        time.sleep(0.5)
    return False


def start_server(
    model: str,
    args: list[str],
    port: int,
    host: str = HOST,
) -> tuple[subprocess.Popen, Path, object]:
    """拉起一个服务端，返回（进程, 日志路径, 日志句柄）。"""
    cmd = server_command(model, args, port, host)
    handle, log_name = tempfile.mkstemp(prefix="xworld_wwc_", suffix=".log")
    os.close(handle)
    log_path = Path(log_name)
    stderr_handle = open(log_path, "ab", buffering=0)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=stderr_handle)
    return proc, log_path, stderr_handle


def stop_server(proc: subprocess.Popen, log_path: Path, handle: object) -> None:
    """停掉服务端并清掉日志。"""
    proc.terminate()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
    try:
        handle.close()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - 句柄关不掉不影响收尾
        pass
    try:
        log_path.unlink()
    except OSError:
        pass
