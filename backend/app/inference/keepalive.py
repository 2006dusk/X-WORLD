"""推理服务保活。

推理服务是独立进程（llama.cpp server），可能因为显存不足、驱动异常或被
手动收掉而退出。后端还活着的时候没人管它，聊天就会一路报「无法连接
推理服务」。

保活只在该发生的时候发生：谁最后启动或停止过档位由 app/inference/launcher.py
记在设置里，用户主动停过就不拉回来。

判定「服务是不是真死了」不能只看 HTTP：推理服务只有一个槽位，
一次长生成会把后面的请求全堵住，此时 /v1/models 要排队甚至超时。
把「忙」当成「死」，保活就会去重启一个好端端的服务——而这台机器放不下
第二个实例，重启的结果是把服务弄没了。所以这里分三态：
up（活着）、busy（端口在但答不上来，正忙着）、down（真没了）。

线程是守护线程，进程退出即结束。档案与服务参数由 launcher 决定，
本模块只管「什么时候去看一眼、看不到了怎么办」。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

from . import launcher
from .service import base_url

logger = logging.getLogger(__name__)

# 多久探一次。
INTERVAL = 20.0
# 连续几次探不到才动手：一次抖动不该触发一次一两分钟的权重加载。
MISS_THRESHOLD = 2


def _port_of(url: str) -> int:
    """从地址里取端口。"""
    tail = url.rsplit(":", 1)[-1] if ":" in url else ""
    try:
        return int(tail.split("/")[0])
    except ValueError:
        return launcher.BASE_PORT


def probe(url: str | None = None, timeout: float = 5.0) -> str:
    """探活：up / busy / down。

    只有 down 才算服务没了。busy 是端口开着但答不上来——服务正忙着生成，
    那是正常状态，不该去动它。
    """
    target = (url or base_url()).rstrip("/")
    port = _port_of(target)

    service = launcher.service_for_port(port)
    if service is not None:
        state = launcher.service_state(service)
        return "up" if state in ("up", "loading") else "down"

    if not launcher.port_open(port):
        return "down"

    try:
        resp = httpx.get(f"{target}/v1/models", timeout=timeout)
    except httpx.TimeoutException:
        return "busy"
    except httpx.HTTPError:
        return "down"
    return "up" if resp.status_code == 200 else "busy"


def restart() -> dict[str, Any]:
    """按生效档位把推理服务拉回来。失败只记日志，不往上抛。"""
    try:
        service = launcher.ensure_running() or {}
    except Exception as exc:  # noqa: BLE001 - 拉不起来不影响后端本身
        logger.warning("推理服务拉起失败：%s", exc)
        return {"action": "failed", "error": f"{type(exc).__name__}: {exc}"}
    logger.info("推理服务已重新拉起：%s", service.get("base_url"))
    return {
        "action": "restarted",
        "profile_id": service.get("profile_id"),
        "base_url": service.get("base_url"),
    }


def tick(streak: int = 0, misses: int = MISS_THRESHOLD) -> tuple[int, dict[str, Any]]:
    """探一次，按需把服务拉回来。

    返回（连续未达次数，这一次的结论）。连续未达够 misses 次才动手：
    一次抖动不该触发一次一两分钟的权重加载。busy 不计入未达。
    """
    if not launcher.is_managed():
        return 0, {"action": "skipped", "reason": "not_managed"}
    state = probe()
    if state in ("up", "busy"):
        return 0, {"action": "ok", "state": state}
    streak += 1
    if streak < misses:
        return streak, {"action": "waiting", "streak": streak}
    return 0, restart()


def loop(interval: float = INTERVAL, misses: int = MISS_THRESHOLD) -> None:
    """保活主循环。"""
    streak = 0
    while True:
        try:
            streak, _ = tick(streak, misses)
        except Exception as exc:  # noqa: BLE001 - 保活线程不能因为一次异常就退出
            logger.warning("保活检查出错：%s", exc)
        time.sleep(interval)


def start() -> threading.Thread:
    """起保活线程。

    线程是守护线程：后端退出它跟着退出，推理服务本身是脱离后端的独立
    进程，不受这个线程的影响。
    """
    thread = threading.Thread(target=loop, name="xworld-inference-keepalive", daemon=True)
    thread.start()
    logger.info("推理服务保活已启动")
    return thread
