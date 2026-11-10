"""推理服务自愈与并发写的回归测试。

三组要守住的行为：
1. 同一个权重已经在跑时不重启（重启会重新加载权重，而本机放不下第二个实例）。
2. 状态分三态：up / loading / down，只有 down 才算服务没了。
3. 设置项并发写不丢键（丢一次「inference_managed」，保活就永久失效）。
"""

from __future__ import annotations

import socket
import threading
from pathlib import Path
from typing import Any

import pytest

from app import store
from app.inference import launcher


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把数据目录指向临时目录，并清掉进程内的服务登记。"""
    monkeypatch.setenv("XWORLD_DATA_DIR", str(tmp_path))
    launcher._SERVICES.clear()
    yield tmp_path
    launcher._SERVICES.clear()


class _FakeProc:
    """够用的子进程替身。"""

    def __init__(self, pid: int = 4242, alive: bool = True) -> None:
        self.pid = pid
        self._alive = alive

    def poll(self) -> int | None:
        """进程结束返回码，还活着返回 None。"""
        return None if self._alive else 1


def test_detect_running_matches_the_model_file_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """认端口上的进程、看命令行，判断跑的是不是同一个权重。"""
    monkeypatch.setattr(
        launcher,
        "list_port_owners",
        lambda: {8080: 111, 9000: 222},
    )

    def fake_command(pid: int) -> str:
        if pid == 111:
            return "C:\\x\\world-work-cpp.exe --model D:\\models\\a\\model.gguf --port 8080"
        return "python -m something.else"

    monkeypatch.setattr(launcher, "process_command_line", fake_command)

    found = launcher.detect_running(Path("D:/models/a/model.gguf"))
    assert found == (8080, 111)


def test_detect_running_ignores_unrelated_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    """端口上有别的进程不算推理服务。"""
    monkeypatch.setattr(launcher, "list_port_owners", lambda: {8080: 111})
    monkeypatch.setattr(launcher, "process_command_line", lambda pid: "python -m other.server")

    assert launcher.detect_running(Path("D:/models/a/model.gguf")) is None


def test_start_adopts_a_running_instance_without_spawning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一个权重已经在跑就沿用，不重启也不新起一个。"""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    monkeypatch.setattr(launcher, "detect_running", lambda model_path: (port, 5150))

    def fail_spawn(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("已有实例在跑，不该再起一个")

    monkeypatch.setattr(launcher.subprocess, "Popen", fail_spawn)

    try:
        service = launcher.start("quality")

        assert service["port"] == port
        assert service["adopted"] is True
        assert service["running"] is True
        assert store.get_setting("inference_url") == f"http://127.0.0.1:{port}"
    finally:
        sock.close()


def test_ensure_running_keeps_a_healthy_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """服务还在就不该重启：重启要重新加载权重，而且机器放不下第二个实例。"""
    service = launcher.ManagedService(
        profile_id="quality",
        name="质量档",
        port=8080,
        base_url="http://127.0.0.1:8080",
        proc=_FakeProc(),
        log_path=Path("x.log"),
        started_at=0.0,
        ready=True,
    )
    launcher._SERVICES["quality"] = service

    def fail_start(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("服务还活着，不该重启")

    monkeypatch.setattr(launcher, "start", fail_start)

    result = launcher.ensure_running("quality")
    assert result["port"] == 8080
    assert result["running"] is True


def test_service_state_separates_loading_from_down() -> None:
    """进程在但没就绪是 loading，不能当成死了。"""
    loading = launcher.ManagedService(
        profile_id="quality",
        name="质量档",
        port=8080,
        base_url="http://127.0.0.1:8080",
        proc=_FakeProc(),
        log_path=Path("x.log"),
        started_at=0.0,
        ready=False,
    )
    dead = launcher.ManagedService(
        profile_id="quality",
        name="质量档",
        port=8081,
        base_url="http://127.0.0.1:8081",
        proc=_FakeProc(alive=False),
        log_path=Path("x.log"),
        started_at=0.0,
        ready=True,
    )

    assert launcher.service_state(loading) == "loading"
    assert launcher.service_state(dead) == "down"


def test_concurrent_settings_writes_keep_every_key() -> None:
    """并发写设置项不能互相抹掉。

    保活线程与接口线程会同时写 settings.json，谁后写谁把对方的改动覆盖掉；
    丢一次「inference_managed」，保活就此永久失效。
    """
    def writer(key: str, value: int) -> None:
        for _ in range(40):
            store.set_setting(key, value)

    threads = [
        threading.Thread(target=writer, args=(f"key_{index}", index))
        for index in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    settings = store.read_json(store.settings_path(), {})
    for index in range(4):
        assert settings.get(f"key_{index}") == index
