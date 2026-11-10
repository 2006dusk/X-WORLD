"""推理服务保活测试。

覆盖：保活开关的默认值与读写、探活的三态判定、以及「不该拉的时候不拉」。
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from app import store
from app.inference import keepalive, launcher


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把数据目录指向临时目录。"""
    monkeypatch.setenv("XWORLD_DATA_DIR", str(tmp_path))
    return tmp_path


def test_managed_flag_defaults_off() -> None:
    """没写过设置时不保活：全新启动不该顺手抓走十几 GB 内存加载权重。"""
    assert launcher.is_managed() is False


def test_managed_flag_round_trip() -> None:
    """保活开关能写能读，字典形状也认。"""
    store.set_setting("inference_managed", True)
    assert launcher.is_managed() is True
    store.set_setting("inference_managed", False)
    assert launcher.is_managed() is False
    store.set_setting("inference_managed", {"enabled": True})
    assert launcher.is_managed() is True
    store.set_setting("inference_managed", {"enabled": False})
    assert launcher.is_managed() is False


def test_probe_reports_unreachable_port() -> None:
    """没人监听的端口算不通。"""
    assert keepalive.probe(url="http://127.0.0.1:59999", timeout=1.0) == "down"


def test_probe_reports_reachable_service() -> None:
    """服务在应答时算通。"""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的约定
            body = json.dumps({"data": [{"id": "demo"}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            """安静一点，测试输出不要刷屏。"""

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        assert keepalive.probe(url=url, timeout=3.0) == "up"
    finally:
        server.shutdown()
        server.server_close()


def test_probe_reports_busy_when_service_does_not_answer() -> None:
    """端口开着却不应答是「忙」，不是「死」。

    推理服务只有一个槽位，一次长生成会把后面的请求全堵住。
    把忙当成死，保活就会去重启一个好端端的服务。
    """
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    thread = threading.Thread(target=lambda: sock.accept(), daemon=True)
    thread.start()
    try:
        assert keepalive.probe(url=f"http://127.0.0.1:{port}", timeout=0.5) == "busy"
    finally:
        sock.close()


def test_tick_skips_when_not_managed(monkeypatch: pytest.MonkeyPatch) -> None:
    """用户停过档位就不拉回来。"""
    called: list[str] = []
    monkeypatch.setattr(launcher, "ensure_running", lambda *a, **k: called.append("start"))
    store.set_setting("inference_managed", False)

    streak, result = keepalive.tick()

    assert called == []
    assert streak == 0
    assert result["action"] == "skipped"


def test_tick_waits_before_restarting(monkeypatch: pytest.MonkeyPatch) -> None:
    """连续没达够次数才动手。"""
    called: list[str] = []
    monkeypatch.setattr(launcher, "ensure_running", lambda *a, **k: called.append("start"))
    monkeypatch.setattr(keepalive, "probe", lambda *a, **k: "down")
    store.set_setting("inference_managed", True)

    streak, result = keepalive.tick(0, misses=2)
    assert called == []
    assert streak == 1
    assert result["action"] == "waiting"

    streak, result = keepalive.tick(streak, misses=2)
    assert called == ["start"]
    assert streak == 0
    assert result["action"] in {"restarted", "failed"}


def test_tick_does_not_restart_a_busy_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """服务正忙着生成时不许动手。"""
    called: list[str] = []
    monkeypatch.setattr(launcher, "ensure_running", lambda *a, **k: called.append("start"))
    monkeypatch.setattr(keepalive, "probe", lambda *a, **k: "busy")
    store.set_setting("inference_managed", True)

    streak, result = keepalive.tick(0, misses=2)
    streak, result = keepalive.tick(streak, misses=2)

    assert called == []
    assert streak == 0
    assert result["action"] == "ok"
    assert result["state"] == "busy"


def test_spawn_flags_detach_the_service() -> None:
    """推理服务进程的脱离标志：进程不随后端一起死。"""
    import os
    import subprocess

    flags = launcher._spawn_flags()
    if os.name == "nt":
        assert flags["creationflags"] & getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        assert flags["start_new_session"] is True
