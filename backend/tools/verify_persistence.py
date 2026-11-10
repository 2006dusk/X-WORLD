"""常驻自愈的端到端验证脚本。

验三件事，都是拿真实进程做，不靠模拟：

1. 杀后端 → 监督器自动把它拉起来 → 推理服务不被带走（进程号不变，
   权重不用重载）。
2. 杀推理服务 → 后端的保活线程按生效档位把它拉回来 → 端口重新应答。
3. 监督器与发起它的终端无关：监督器的父进程没了它照样活着。

用法（在工程根目录或 backend 目录下都能跑）：
    python backend/tools/verify_persistence.py --scenario backend
    python backend/tools/verify_persistence.py --scenario inference
    python backend/tools/verify_persistence.py --scenario all

只动本机进程，不写数据目录里的任何表。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app import config  # noqa: E402
from app.inference.launcher import pid_on_port  # noqa: E402

BACKEND_PORT = config.DEFAULT_PORT
INFERENCE_PORT = 8080
# 杀后端到监督器把它拉起来并就绪的等待上限。
BACKEND_WAIT = 120.0
# 杀推理服务到保活线程把它拉回来的等待上限：要重新加载权重，一两分钟起。
INFERENCE_WAIT = 600.0


def _now() -> float:
    """当前时间戳。"""
    return time.time()


def http_ok(url: str, timeout: float = 3.0) -> bool:
    """地址是否返回 200。

    urllib 的响应对象上状态码叫 status，status_code 是 requests 的属性。
    """
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except Exception:  # noqa: BLE001 - 探活失败按不通处理
        return False


def kill_pid(pid: int | None) -> bool:
    """只杀这个进程，不带子进程。

    推理服务是后端的子进程，带 /T 收树会把它顺带弄死，
    那样就测不出「后端重启不丢权重」了。
    """
    if not pid:
        return False
    result = subprocess.run(
        ["taskkill", "/PID", str(pid), "/F"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode == 0


def wait_for(predicate, timeout: float, interval: float = 1.0) -> tuple[bool, float]:
    """等条件成立，返回（是否成立, 用了多少秒）。"""
    deadline = _now() + timeout
    while _now() < deadline:
        if predicate():
            return True, _now() - (deadline - timeout)
        time.sleep(interval)
    return False, timeout


def supervisor_pid() -> int | None:
    """监督器进程号。"""
    try:
        raw = (config.data_root() / "run" / "supervisor.pid").read_text(encoding="utf-8").strip()
        return int(raw)
    except (OSError, ValueError):
        return None


def scenario_backend() -> dict[str, Any]:
    """杀后端，看监督器能不能把它拉回来，推理服务会不会被带走。"""
    before_backend = pid_on_port(BACKEND_PORT)
    before_inference = pid_on_port(INFERENCE_PORT)
    supervisor = supervisor_pid()
    if not before_backend:
        return {"ok": False, "error": "后端没在跑，先用 serve.py start 拉起来"}

    killed = kill_pid(before_backend)
    started, waited = wait_for(
        lambda: (pid_on_port(BACKEND_PORT) or 0) not in (0, before_backend),
        BACKEND_WAIT,
    )
    after_backend = pid_on_port(BACKEND_PORT)
    after_inference = pid_on_port(INFERENCE_PORT)
    healthy = http_ok(f"http://127.0.0.1:{BACKEND_PORT}/healthz")
    inference_healthy = http_ok(f"http://127.0.0.1:{INFERENCE_PORT}/v1/models")

    return {
        "ok": bool(killed and started and healthy and after_inference == before_inference),
        "killed_backend": before_backend,
        "new_backend": after_backend,
        "restarted": started,
        "waited_s": round(waited, 1),
        "backend_healthy": healthy,
        "inference_pid_before": before_inference,
        "inference_pid_after": after_inference,
        "inference_survived": after_inference == before_inference,
        "inference_healthy": inference_healthy,
        "supervisor": supervisor,
        "supervisor_alive": supervisor is not None and _process_alive(supervisor),
    }


def scenario_inference() -> dict[str, Any]:
    """杀推理服务，看后端的保活线程能不能把它拉回来。"""
    before = pid_on_port(INFERENCE_PORT)
    if not before:
        return {"ok": False, "error": "推理服务没在跑"}

    killed = kill_pid(before)
    started, waited = wait_for(
        lambda: (pid_on_port(INFERENCE_PORT) or 0) not in (0, before),
        INFERENCE_WAIT,
        interval=2.0,
    )
    after = pid_on_port(INFERENCE_PORT)
    healthy = http_ok(f"http://127.0.0.1:{INFERENCE_PORT}/v1/models")

    return {
        "ok": bool(killed and started and healthy),
        "killed_inference": before,
        "new_inference": after,
        "restarted": started,
        "waited_s": round(waited, 1),
        "inference_healthy": healthy,
    }


def scenario_supervisor() -> dict[str, Any]:
    """监督器是不是与发起它的终端无关。"""
    pid = supervisor_pid()
    if not pid:
        return {"ok": False, "error": "没有监督器 pid 文件"}
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').ParentProcessId",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
    except Exception:  # noqa: BLE001 - 取不到就按未知处理
        out = ""
    parent = None
    for line in out.splitlines():
        line = line.strip()
        if line.isdigit():
            parent = int(line)
            break
    alive = _process_alive(pid)
    return {
        "ok": alive,
        "supervisor": pid,
        "parent": parent,
        "parent_alive": parent is not None and parent != 0 and _process_alive(parent),
    }


def _process_alive(pid: int) -> bool:
    """进程还在不在。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
    except Exception:  # noqa: BLE001 - 查不到就按不在处理
        return False
    return str(pid) in out


def main() -> int:
    """入口。"""
    parser = argparse.ArgumentParser(description="X-WORLD 常驻自愈验证")
    parser.add_argument(
        "--scenario",
        choices=["backend", "inference", "supervisor", "all"],
        default="all",
        help="要验的场景",
    )
    args = parser.parse_args()

    results: dict[str, Any] = {}
    if args.scenario in ("backend", "all"):
        results["backend"] = scenario_backend()
    if args.scenario in ("inference", "all"):
        results["inference"] = scenario_inference()
    if args.scenario in ("supervisor", "all"):
        results["supervisor"] = scenario_supervisor()

    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(item.get("ok") for item in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
