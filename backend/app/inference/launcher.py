"""本地推理服务管理器。

客户端的推理端是独立的推理服务进程：模型权重常驻显存，换档位就意味着
换进程。只有自写服务端 world-work-cpp 一套（可执行入口与参数名见
servers.py）。本模块负责按模型档案拉起、停止与切换这些进程，并把生效
地址写回设置。

五条约束：
1. 进程信息只放内存。服务是运行时资源，后端重启即失效，不落盘成数据表。
2. 同时只留一个服务。8GB 显存装不下两个大档位并存，换档位先停后起；
   宿主内存同样只装得下一个——每个实例光 logits 数组就要 15.2 GiB
   （16384 × 248320 × 4 字节），起第二个会直接 numpy._ArrayMemoryError 退出。
3. 权重路径一律来自模型档案，本模块不写死盘符。
4. 同一个权重已经在跑就不重启：重新加载权重要一两分钟，而机器放不下第二个。
   硬起第二个的代价是「把好端端的服务杀了，又起不来」。
5. 探活看进程与端口，不靠 HTTP：两套服务端都只有一个槽位，忙起来
   连 /v1/models 都要排队，把「忙」误判成「死」会引发破坏性重启。

用法（接口层）：
    POST /v1/inference/start {"profile_id": "fast"}
    POST /v1/inference/stop {}
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app import store
from app.config import HOST, logs_dir

from . import servers
from .traits import (
    SERVER_KIND_WWC,
    ModelProfile,
    default_profile,
    profile_from_dict,
    resolve_profile,
)

logger = logging.getLogger(__name__)

# 端口从 8080 起找空闲端口。地址写进设置后前端就用它连推理服务。
BASE_PORT = 8080
# 扫端口找已有服务的范围。
SCAN_PORTS = 64
# 首次启动要加载权重并捕获 CUDA graph，实测首轮可能超过 100 秒。
READY_TIMEOUT = 420.0
READY_INTERVAL = 1.0


@dataclass
class ManagedService:
    """一个推理服务进程。

    proc 为空表示这是认领来的进程：后端重启前就在跑，不是这次拉起的，
    进程信息拿不到，只能按端口处理。
    """

    profile_id: str
    name: str
    port: int
    base_url: str
    proc: subprocess.Popen | None
    log_path: Path
    started_at: float
    ready: bool = True
    server_kind: str = SERVER_KIND_WWC

    def to_dict(self) -> dict[str, Any]:
        """对外形状。"""
        return {
            "profile_id": self.profile_id,
            "name": self.name,
            "server_kind": self.server_kind,
            "port": self.port,
            "base_url": self.base_url,
            "pid": self.proc.pid if self.proc else None,
            "running": self.is_running(),
            "adopted": self.proc is None,
            "state": service_state(self),
            "log_path": str(self.log_path),
            "started_at": self.started_at,
        }

    def is_running(self) -> bool:
        """服务是否还活着。"""
        if self.proc is not None:
            return self.proc.poll() is None
        return port_open(self.port)


# 托管中的服务，按 profile_id 索引。只在内存里。
_SERVICES: dict[str, ManagedService] = {}


def free_port(start: int = BASE_PORT) -> int:
    """从 start 起找一个空闲端口。"""
    for port in range(start, start + SCAN_PORTS):
        with socket.socket() as sock:
            try:
                sock.bind((HOST, port))
            except OSError:
                continue
            return port
    raise RuntimeError("没有可用端口")


def profile_by_id(profile_id: str) -> ModelProfile:
    """按 id 取档案：内置档位优先，其次是 store 里存的 profile。"""
    for profile in default_profiles():
        if profile.id == profile_id:
            return profile
    stored = store.get_profile(profile_id)
    if isinstance(stored, dict):
        return profile_from_dict(stored)
    return default_profile(profile_id)


def default_profiles() -> tuple[ModelProfile, ...]:
    """内置档位表。"""
    from .traits import DEFAULT_PROFILES

    return DEFAULT_PROFILES


def _spawn_flags() -> dict[str, Any]:
    """推理服务进程的脱离标志。

    权重常驻显存，服务进程不该跟着后端一起死：后端重启时权重还在，
    新后端按端口认领回来即可，不必再等一次加载（一两分钟）。
    """
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0)
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return {"creationflags": flags}
    return {"start_new_session": True}


def is_managed() -> bool:
    """推理服务该不该由后端保活。

    由谁最后启动或停止档位决定：启动过就归后端管，停止过就不管——
    停了还自动拉回来，与用户的意思相反。

    设置项没写过时按「不管」处理：全新启动不该顺手抓走十几 GB 内存去
    加载权重。要常驻的用户启动一次档位即可，之后就会自动保持。
    """
    try:
        value = store.get_setting("inference_managed")
    except Exception:  # noqa: BLE001 - 读不到就按不管处理
        return False
    if value is None:
        return False
    if isinstance(value, dict):
        value = value.get("enabled")
    if value is None:
        return False
    return bool(value)


def port_open(port: int, timeout: float = 1.0) -> bool:
    """端口上有没有人在听。

    只做 TCP 连接，不发 HTTP：推理服务只有一个槽位，
    一次长生成会把后面的请求全堵住，用 HTTP 探活会把「忙」误判成「死」。
    """
    try:
        with socket.create_connection((HOST, port), timeout=timeout):
            return True
    except OSError:
        return False


def service_state(service: ManagedService) -> str:
    """服务状态：up / loading / down。

    loading 是「进程还在但权重没加载完」，端口还没开。这种状态最容易被
    误判成死了：把它当死就会杀掉正在加载的进程再起一个，而机器放不下第二个。
    """
    if service.proc is not None:
        if service.proc.poll() is not None:
            return "down"
        return "up" if service.ready else "loading"
    return "up" if port_open(service.port) else "down"


def service_for_port(port: int) -> ManagedService | None:
    """按端口找托管中的服务。"""
    for service in _SERVICES.values():
        if service.port == port:
            return service
    return None


def list_port_owners() -> dict[int, int]:
    """一次 netstat 拿到所有监听端口对应的进程号。"""
    owners: dict[int, int] = {}
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
    except Exception:  # noqa: BLE001 - 取不到就当没有
        return owners
    for line in out.splitlines():
        if "LISTENING" not in line:
            continue
        parts = line.split()
        if len(parts) < 5 or ":" not in parts[1]:
            continue
        try:
            port = int(parts[1].rsplit(":", 1)[1])
            pid = int(parts[-1])
        except ValueError:
            continue
        owners.setdefault(port, pid)
    return owners


def pid_on_port(port: int) -> int | None:
    """按端口找进程号。认领来的进程没有句柄，只能这样收尾。"""
    return list_port_owners().get(port)


def process_command_line(pid: int) -> str:
    """取进程的命令行。

    用来判断端口上那个进程跑的是哪个权重：问服务本身不可靠，
    它正忙着生成时连 /v1/models 都要排队。
    """
    if os.name != "nt":
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as handle:
                return handle.read().decode("utf-8", "replace").replace("\x00", " ")
        except OSError:
            return ""
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
    except Exception:  # noqa: BLE001 - 取不到就当没有
        return ""
    return out.strip()


def detect_running(model_path: Path) -> tuple[int, int] | None:
    """找已经在跑的同权重服务，返回（端口, 进程号）。

    认端口上的进程、读它的命令行来判断跑的是不是同一个权重。
    """
    for port, pid in list_port_owners().items():
        if not BASE_PORT <= port < BASE_PORT + SCAN_PORTS:
            continue
        command = process_command_line(pid)
        if not command:
            continue
        if not servers.is_inference_command(command):
            continue
        if model_path.name in command:
            return port, pid
    return None


def _kill_pid(pid: int) -> bool:
    """按进程号收掉进程。"""
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            timeout=30,
        )
    except Exception:  # noqa: BLE001 - 收不掉就靠端口探测确认
        logger.warning("没能结束进程 %s", pid)
        return False
    return True


def _kill_port_owner(port: int) -> bool:
    """把占用端口的进程收掉。"""
    pid = pid_on_port(port)
    if not pid:
        return False
    return _kill_pid(pid)


def _served_model(port: int) -> str:
    """端口上那个服务加载的是哪个权重。"""
    try:
        resp = httpx.get(f"http://{HOST}:{port}/v1/models", timeout=3.0)
        resp.raise_for_status()
        entries = (resp.json() or {}).get("data") or []
        for item in entries:
            if isinstance(item, dict) and item.get("id"):
                return str(item["id"])
    except Exception:  # noqa: BLE001 - 探测失败按空处理
        return ""
    return ""


def _wait_ready(base_url: str, proc: subprocess.Popen, port: int, timeout: float) -> bool:
    """等服务可用。

    两件事都要满足：进程还在，且端口上应答的就是这个进程。只看 HTTP 响应
    会把「别家的服务」当成自己的——端口上本来就有个旧实例时，新进程会
    因端口占用退出，HTTP 却照样答得上。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        if pid_on_port(port) == proc.pid:
            try:
                resp = httpx.get(f"{base_url}/v1/models", timeout=5.0)
                if resp.status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
        time.sleep(READY_INTERVAL)
    return False


def _stop_service(service: ManagedService) -> None:
    """停掉一个服务进程。"""
    if service.proc is not None:
        if service.proc.poll() is None:
            service.proc.terminate()
            try:
                service.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                service.proc.kill()
    else:
        _kill_port_owner(service.port)
    logger.info("推理服务已停止：%s（端口 %s）", service.profile_id, service.port)


def _adopt(profile: ModelProfile, port: int, pid: int) -> ManagedService:
    """把已经在跑的实例登记进来。

    权重不用重载，界面上的档位状态也跟实际跑着的对得上。
    """
    service = ManagedService(
        profile_id=profile.id,
        name=profile.name,
        port=port,
        base_url=f"http://{HOST}:{port}",
        proc=None,
        log_path=logs_dir() / f"inference-{profile.id}.log",
        started_at=time.time(),
        ready=True,
        server_kind=SERVER_KIND_WWC,
    )
    _SERVICES[profile.id] = service
    store.set_setting("inference_url", service.base_url)
    store.set_setting("active_model", {"model_id": profile.id, "profile_id": profile.id})
    store.set_setting("inference_managed", True)
    return service


def adopt_existing() -> dict[str, Any] | None:
    """后端启动时认领已经在跑的推理服务。

    推理服务是脱离后端的独立进程，后端重启它还活着，权重也还常驻。
    认领回来就不用再等一次加载（一两分钟）。
    """
    for port, pid in list_port_owners().items():
        if not BASE_PORT <= port < BASE_PORT + SCAN_PORTS:
            continue
        command = process_command_line(pid)
        if not servers.is_inference_command(command):
            continue
        for profile in default_profiles():
            if Path(profile.model_path).name in command:
                service = _adopt(profile, port, pid)
                logger.info(
                    "认领已在运行的推理服务：%s → %s（pid %s）",
                    profile.id,
                    service.base_url,
                    pid,
                )
                return service.to_dict()
    return None


def _stop_others() -> list[str]:
    """收掉别的推理服务。

    显存与宿主内存都只装得下一个实例：换档位必须先把旧的收掉。
    托管中的服务按句柄收，托管之外的按端口找进程收。
    """
    stopped: list[str] = []
    for service in list(_SERVICES.values()):
        _stop_service(service)
        _SERVICES.pop(service.profile_id, None)
        stopped.append(service.profile_id)
    for port, pid in list_port_owners().items():
        if not BASE_PORT <= port < BASE_PORT + SCAN_PORTS:
            continue
        command = process_command_line(pid)
        if not servers.is_inference_command(command):
            continue
        if _kill_pid(pid):
            stopped.append(f"pid:{pid}")
    return stopped


def stop(profile_id: str | None = None) -> dict[str, Any]:
    """停止指定档位的服务，不给 id 就全停。

    这是用户的明确意图：停掉之后不再保活，避免停了又被拉回来。
    """
    targets = list(_SERVICES.values())
    if profile_id:
        targets = [item for item in targets if item.profile_id == profile_id]
    stopped: list[str] = []
    for service in targets:
        _stop_service(service)
        _SERVICES.pop(service.profile_id, None)
        stopped.append(service.profile_id)
    for port, pid in list_port_owners().items():
        if not BASE_PORT <= port < BASE_PORT + SCAN_PORTS:
            continue
        if not servers.is_inference_command(process_command_line(pid)):
            continue
        if _kill_pid(pid):
            stopped.append(f"pid:{pid}")
    store.set_setting("inference_managed", False)
    return {"ok": True, "stopped": stopped}


def start(profile_id: str, replace: bool = True) -> dict[str, Any]:
    """按档案拉起推理服务并把它设为生效地址。

    同一个权重已经在跑就直接沿用，不重启：重启要重新加载权重，
    而本机放不下第二个实例，硬起第二个的结果是「服务被杀又起不来」。
    """
    profile = profile_by_id(profile_id)
    model_path = Path(profile.model_path)
    if not model_path.exists():
        raise FileNotFoundError(f"权重文件不存在：{profile.model_path}")

    running = detect_running(model_path)
    if running is not None:
        port, pid = running
        service = _adopt(profile, port, pid)
        logger.info(
            "沿用已在运行的推理服务：%s → %s（pid %s）",
            profile.id,
            service.base_url,
            pid,
        )
        return service.to_dict()

    if replace:
        _stop_others()

    port = free_port()
    base_url = f"http://{HOST}:{port}"
    cmd = servers.server_command(profile, HOST, port)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    log_path = logs_dir() / f"inference-{profile.id}.log"
    handle = open(log_path, "ab", buffering=0)
    proc = subprocess.Popen(
        cmd,
        stdout=handle,
        stderr=handle,
        env=env,
        **_spawn_flags(),
    )
    logger.info("推理服务启动中：%s（端口 %s）", profile.id, port)
    if not _wait_ready(base_url, proc, port, READY_TIMEOUT):
        _stop_service(
            ManagedService(
                profile.id,
                profile.name,
                port,
                base_url,
                proc,
                log_path,
                time.time(),
                False,
            )
        )
        raise RuntimeError("推理服务启动超时或进程退出")
    service = ManagedService(
        profile_id=profile.id,
        name=profile.name,
        port=port,
        base_url=base_url,
        proc=proc,
        log_path=log_path,
        started_at=time.time(),
        ready=True,
        server_kind=SERVER_KIND_WWC,
    )
    _SERVICES[profile.id] = service
    store.set_setting("inference_url", base_url)
    store.set_setting("active_model", {"model_id": profile.id, "profile_id": profile.id})
    store.set_setting("inference_managed", True)
    logger.info("推理服务就绪：%s → %s", profile.id, base_url)
    return service.to_dict()


def ensure_running(profile_id: str | None = None) -> dict[str, Any]:
    """保证推理服务在跑。

    先看是不是已经在跑：在跑就沿用，不重启。重启要重新加载权重，
    而本机放不下第二个实例。
    """
    profile = resolve_profile(profile_id)
    service = _SERVICES.get(profile.id)
    if service is not None and service_state(service) in ("up", "loading"):
        return service.to_dict()
    running = detect_running(Path(profile.model_path))
    if running is not None:
        port, pid = running
        return _adopt(profile, port, pid).to_dict()
    return start(profile.id, replace=True)


def list_services() -> list[dict[str, Any]]:
    """列出托管中的服务。"""
    return [service.to_dict() for service in _SERVICES.values()]


def tiers() -> list[dict[str, Any]]:
    """档位清单：内置档位与 store 里的 profile 合并，带运行状态。"""
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for profile in default_profiles():
        seen.add(profile.id)
        items.append(_tier_row(profile))
    for stored in store.list_profiles():
        profile_id = str(stored.get("id") or "")
        if not profile_id or profile_id in seen:
            continue
        seen.add(profile_id)
        items.append(_tier_row(profile_from_dict(stored)))
    return items


def _tier_row(profile: ModelProfile) -> dict[str, Any]:
    """档位的一行展示数据。"""
    service = _SERVICES.get(profile.id)
    model_path = Path(profile.model_path)
    return {
        "profile_id": profile.id,
        "name": profile.name,
        "model_path": profile.model_path,
        "model_file": model_path.name,
        "model_exists": model_path.exists(),
        "size_mb": round(model_path.stat().st_size / 1048576) if model_path.exists() else None,
        "n_ctx": profile.n_ctx,
        "kv_type": profile.kv_type,
        "n_gpu_layers": profile.n_gpu_layers,
        "server_kind": SERVER_KIND_WWC,
        "measured": profile.measured,
        "running": bool(service and service_state(service) == "up"),
        "base_url": service.base_url if service else None,
    }


def active_profile() -> dict[str, Any]:
    """当前生效档位（按设置解析）。"""
    profile = resolve_profile()
    return profile.to_dict()
