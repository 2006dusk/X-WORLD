"""常驻运行：把后端挂成不会自己关掉的后台服务。

问题：后端与推理服务通常挂在终端窗口或后台任务下面，那些宿主一收工，
挂在下面的进程跟着死，网页就断了，长会话被打断。

这个脚本拉起一个脱离会话的监督器进程：监督器守着后端，后端没了就重新
拉起。推理服务本身是脱离后端的独立进程（见 app/inference/launcher.py），
后端重启时按端口认领回来，权重不用重载。

用法：
  F:/python/python.exe backend/packaging/serve.py start
  F:/python/python.exe backend/packaging/serve.py status
  F:/python/python.exe backend/packaging/serve.py stop
  F:/python/python.exe backend/packaging/serve.py stop --with-inference
  F:/python/python.exe backend/packaging/serve.py restart

只动本机进程，不写系统配置，不注册计划任务。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import config  # noqa: E402
from app import store  # noqa: E402
from app.inference.launcher import pid_on_port  # noqa: E402

# 探活一律走本机回环，不跟着监听地址走。
PROBE_HOST = "127.0.0.1"
# 监督器多久看一次后端。
POLL = 5.0
# 后端起来要多久：引导、建目录、刷新内置预设，很快；权重不由后端加载。
READY_TIMEOUT = 90.0


def run_dir() -> Path:
    """运行时状态目录。放进程号这类只在本次运行里有意义的东西。"""
    path = config.data_root() / "run"
    path.mkdir(parents=True, exist_ok=True)
    return path


def pid_file() -> Path:
    """监督器的进程号文件。"""
    return run_dir() / "supervisor.pid"


def log_path() -> Path:
    """监督器日志。"""
    return config.logs_dir() / "supervisor.log"


def web_host_file() -> Path:
    """网页服务监听地址的存档。

    这是运行时配置，跟 pid 文件放一处：不属于用户数据，不进数据表。
    记下来是为了下次 start 不用再带一遍参数——局域网访问开着就一直在。
    """
    return run_dir() / "web_host.txt"


def read_web_host() -> str:
    """读上次选的监听地址，没有就用环境变量，再没有就只听本机。"""
    try:
        saved = web_host_file().read_text(encoding="utf-8").strip()
    except OSError:
        saved = ""
    if saved:
        return saved
    return os.environ.get("XWORLD_WEB_HOST") or "127.0.0.1"


def write_web_host(host: str) -> None:
    """记住监听地址。"""
    try:
        web_host_file().write_text(host, encoding="utf-8")
    except OSError:
        pass


def _env_with_web_host(host: str) -> dict[str, str]:
    """带上网页服务监听地址的环境变量。

    监督器与后端都用它起：后端从 app.config 的 WEB_HOST 读这个值。
    """
    env = dict(os.environ)
    env["XWORLD_WEB_HOST"] = host
    return env


def log_line(message: str) -> None:
    """往监督器日志写一行。"""
    try:
        with open(log_path(), "a", encoding="utf-8") as handle:
            handle.write(f"[{time.strftime('%H:%M:%S')}] {message}\n")
    except OSError:
        pass


def http_ok(url: str, timeout: float = 3.0) -> bool:
    """地址是否返回 200。

    urllib 的响应对象上状态码叫 status，status_code 是 requests 的属性；
    写错会被这里的 except 吞掉，探活一律判成不通。
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except Exception as exc:  # noqa: BLE001 - 探活失败按不通处理
        log_line(f"探活 {url} 失败：{type(exc).__name__}: {exc}")
        return False


def backend_url() -> str:
    """后端地址。"""
    return f"http://{PROBE_HOST}:{config.DEFAULT_PORT}"


def backend_alive() -> bool:
    """后端是否在应答。"""
    return http_ok(backend_url() + "/healthz")


def inference_url() -> str:
    """推理服务地址，取设置里的生效地址。"""
    try:
        value = store.get_setting("inference_url")
    except Exception:  # noqa: BLE001 - 读不到就用缺省地址
        value = None
    if isinstance(value, dict):
        url = value.get("url")
        if isinstance(url, str) and url:
            return url.rstrip("/")
    if isinstance(value, str) and value:
        return value.rstrip("/")
    return f"http://{PROBE_HOST}:8080"


def url_port(url: str) -> int:
    """从地址里取端口，取不到按 8080。"""
    tail = url.rsplit(":", 1)[-1] if ":" in url else ""
    try:
        return int(tail.split("/")[0])
    except ValueError:
        return 8080


def inference_alive() -> bool:
    """推理服务是否在应答。"""
    return http_ok(inference_url() + "/v1/models")


def process_alive(pid: int | None) -> bool:
    """进程还在不在。"""
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=20,
            ).stdout
        except Exception:  # noqa: BLE001 - 查不到就按不在处理
            return False
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def supervisor_pid() -> int | None:
    """监督器进程号。"""
    try:
        raw = pid_file().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def supervisor_alive() -> bool:
    """监督器是否还在。"""
    return process_alive(supervisor_pid())


def kill_process(pid: int | None) -> bool:
    """只收掉这个进程，不带子进程。

    推理服务是后端的子进程（Windows 上父进程死了它照样活着），
    用 /T 连树一起收会把它顺带弄死，权重白加载。
    """
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                timeout=30,
            )
        except Exception:  # noqa: BLE001 - 收不掉就靠端口探测确认
            return False
        return True
    import signal

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    return True


def kill_tree(pid: int | None) -> bool:
    """连同子进程一起收掉。"""
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=30,
            )
        except Exception:  # noqa: BLE001 - 收不掉就靠端口探测确认
            return False
        return True
    import signal

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    return True


def spawn_flags(breakaway: bool = True) -> dict[str, Any]:
    """脱离当前会话的标志。

    终端窗口或后台任务结束时，挂在它们下面的进程会被一起收掉。监督器
    要活得比宿主久，标志就是为此设的。作业对象不允许脱离时退一步，
    只带新进程组：至少不跟着同一个窗口一起关。
    """
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0)
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if breakaway:
            flags |= getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
        return {"creationflags": flags}
    return {"start_new_session": True}


def spawn_backend() -> subprocess.Popen:
    """拉起后端进程。"""
    handle = open(log_path(), "ab", buffering=0)
    return subprocess.Popen(
        [sys.executable, "packaging/launcher.py"],
        cwd=str(BACKEND),
        env=dict(os.environ),
        stdin=subprocess.DEVNULL,
        stdout=handle,
        stderr=handle,
    )


def spawn_supervisor(web_host: str) -> subprocess.Popen:
    """拉起监督器进程。"""
    handle = open(log_path(), "ab", buffering=0)
    cmd = [sys.executable, str(Path(__file__).resolve()), "supervise"]
    kwargs: dict[str, Any] = {
        "cwd": str(BACKEND),
        "env": _env_with_web_host(web_host),
        "stdin": subprocess.DEVNULL,
        "stdout": handle,
        "stderr": handle,
    }
    try:
        return subprocess.Popen(cmd, **kwargs, **spawn_flags())
    except OSError:
        return subprocess.Popen(cmd, **kwargs, **spawn_flags(breakaway=False))


def wait_backend(timeout: float = READY_TIMEOUT) -> bool:
    """等后端起来。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if backend_alive():
            return True
        time.sleep(1.0)
    return False


def supervise() -> int:
    """监督器主循环：后端活着就守着，不在就拉起来。"""
    pid_file().write_text(str(os.getpid()), encoding="utf-8")
    log_line(f"监督器启动 pid={os.getpid()}")
    child: subprocess.Popen | None = None
    try:
        while True:
            if child is None or child.poll() is not None:
                if child is not None:
                    log_line(f"后端退出（码 {child.returncode}），重新拉起")
                child = spawn_backend()
                log_line(f"后端已拉起 pid={child.pid}")
                if not wait_backend():
                    log_line("后端没在预期时间内就绪，继续看着")
                elif pid_on_port(config.DEFAULT_PORT) != child.pid:
                    # 端口上应答的是别的后端：这个子进程多半是抢端口抢输了。
                    # 不停地重拉只会一直崩，先看看现状。
                    log_line(
                        f"端口 {config.DEFAULT_PORT} 上的后端不是本监督器的子进程"
                        f"（子进程 {child.pid}），暂不重拉"
                    )
            time.sleep(POLL)
    except KeyboardInterrupt:
        log_line("监督器收到中断，退出")
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
        try:
            pid_file().unlink(missing_ok=True)
        except OSError:
            pass
    return 0


def start(web_host: str | None = None) -> int:
    """以后台常驻方式启动。

    web_host 给网页服务的监听地址：127.0.0.1 只听本机，0.0.0.0 局域网可访问。
    不给就用上次的记录。这个地址只影响网页服务，推理服务始终只听本机。
    """
    host = web_host or read_web_host()
    if supervisor_alive():
        print(json.dumps(
            {"ok": True, "already_running": True, "supervisor_pid": supervisor_pid()},
            ensure_ascii=False,
        ))
        return 0
    # 接管顺序：先收旧监督器，再收旧后端。反了的话旧监督器会在几秒内
    # 又拉起一个后端，两个后端抢同一个端口，先起来的那个会一直崩。
    old_supervisor = supervisor_pid()
    if old_supervisor:
        kill_process(old_supervisor)
        time.sleep(1.0)
    old = pid_on_port(config.DEFAULT_PORT)
    if old:
        # 接管：把已经在跑的旧后端收掉，否则端口打架。
        # 只收后端本身：推理服务是它的子进程，要留着（权重别白加载）。
        kill_process(old)
        time.sleep(1.5)
    write_web_host(host)
    proc = spawn_supervisor(host)
    print(json.dumps(
        {
            "ok": True,
            "supervisor_pid": proc.pid,
            "backend": backend_url(),
            "web_host": host,
            "log": str(log_path()),
        },
        ensure_ascii=False,
    ))
    return 0


def stop(with_inference: bool = False) -> int:
    """停掉监督器与后端。

    推理服务默认保留：权重常驻显存与显存里，重启后端按端口认领即可，
    不用再等一次加载。要连它一起停就加 --with-inference。
    """
    done = {"supervisor": False, "backend": False, "inference": False}
    pid = supervisor_pid()
    if pid:
        done["supervisor"] = kill_process(pid)
    backend = pid_on_port(config.DEFAULT_PORT)
    if backend:
        done["backend"] = kill_process(backend)
    if with_inference:
        owner = pid_on_port(url_port(inference_url()))
        if owner:
            done["inference"] = kill_tree(owner)
    try:
        pid_file().unlink(missing_ok=True)
    except OSError:
        pass
    print(json.dumps({"ok": True, "stopped": done}, ensure_ascii=False))
    return 0


def status() -> int:
    """看后端与推理服务的状态。"""
    data = {
        "supervisor": {"pid": supervisor_pid(), "running": supervisor_alive()},
        "web_host": read_web_host(),
        "backend": {
            "url": backend_url(),
            "alive": backend_alive(),
            "pid": pid_on_port(config.DEFAULT_PORT),
        },
        "inference": {
            "url": inference_url(),
            "alive": inference_alive(),
            "pid": pid_on_port(url_port(inference_url())),
        },
    }
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def restart(web_host: str | None = None) -> int:
    """重启：停掉再起。"""
    stop()
    time.sleep(1.5)
    return start(web_host)


def main() -> int:
    """入口。"""
    parser = argparse.ArgumentParser(description="X-WORLD 常驻运行")
    parser.add_argument(
        "action",
        choices=["start", "stop", "status", "restart", "supervise"],
        help="supervise 是监督器自己的入口，由 start 拉起",
    )
    parser.add_argument(
        "--with-inference",
        action="store_true",
        help="停止时连推理服务一起停",
    )
    parser.add_argument(
        "--web-host",
        default=None,
        help="网页服务监听地址：127.0.0.1 只听本机，0.0.0.0 局域网可访问；不给就用上次的记录",
    )
    args = parser.parse_args()
    if args.action == "supervise":
        return supervise()
    if args.action == "start":
        return start(args.web_host)
    if args.action == "stop":
        return stop(with_inference=args.with_inference)
    if args.action == "restart":
        return restart(args.web_host)
    return status()


if __name__ == "__main__":
    raise SystemExit(main())
