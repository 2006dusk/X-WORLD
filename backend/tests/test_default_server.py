"""默认推理端是自写服务端 world-work-cpp。

Python 版 llama_cpp.server 的支持已经整体去掉：它没有控制向量、原地换模型
与运行时声线生成这三样能力，留着只会让人以为「换个服务端也行」。
这里守住四件事：内置档位只有自写服务端一套、参数名用连字符写法、
命令行拼装指向它的入口、进程识别认得它的命令行。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.inference import launcher, servers
from app.inference.traits import SERVER_KIND_WWC, ModelProfile


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把数据目录指向临时目录，并清掉进程内的服务登记。"""
    monkeypatch.setenv("XWORLD_DATA_DIR", str(tmp_path))
    launcher._SERVICES.clear()
    yield tmp_path
    launcher._SERVICES.clear()


def _profile(**kwargs: Any) -> ModelProfile:
    """造一个测试用档案。"""
    base: dict[str, Any] = {
        "id": "t",
        "name": "测试档",
        "model_path": "D:/models/a.gguf",
        "n_ctx": 16384,
        "n_gpu_layers": 999,
        "kv_type": "q8_0",
    }
    base.update(kwargs)
    return ModelProfile(**base)


def test_builtin_profiles_use_the_only_server_kind() -> None:
    """服务端只有一套，内置档位没有别的选择。"""
    assert SERVER_KIND_WWC == "world-work-cpp"


def test_server_args_use_dash_names() -> None:
    """自写服务端用连字符参数名，且不能混进 Python 版的写法。"""
    args = _profile().server_args()
    assert "--ctx-size" in args
    assert "--n-gpu-layers" in args
    assert "--cache-type-k" in args
    assert "--cache-type-v" in args
    assert "--n_ctx" not in args
    assert "--n_gpu_layers" not in args
    assert "--type_k" not in args


def test_f16_kv_omits_cache_type_flags() -> None:
    """KV 用 f16 时不传缓存量化参数，服务端默认就是 f16。"""
    args = _profile(kv_type="f16").server_args()
    assert "--cache-type-k" not in args
    assert "--cache-type-v" not in args


def test_server_command_points_at_wwc_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    """命令第一个是 exe，不是 python -m。"""
    monkeypatch.setattr(servers, "wwc_executable", lambda: Path("C:/x/world-work-cpp.exe"))
    cmd = servers.server_command(_profile(), "127.0.0.1", 8080)
    assert cmd[0].endswith("world-work-cpp.exe")
    assert "llama_cpp.server" not in cmd
    assert "--model" in cmd
    assert "8080" in cmd


def test_server_command_never_falls_back_to_python_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有退路：exe 缺失就报错，不偷偷换回 Python 版。"""
    monkeypatch.setattr(servers, "wwc_executable", lambda: Path("C:/x/world-work-cpp.exe"))
    cmd = servers.server_command(_profile(), "127.0.0.1", 8080)
    assert not any("python" in part for part in cmd)
    assert not any("llama_cpp" in part for part in cmd)


def test_is_inference_command_recognizes_the_server() -> None:
    """认领与收尾靠它筛进程。"""
    assert servers.is_inference_command("C:\\x\\world-work-cpp.exe --model D:\\a.gguf")
    assert not servers.is_inference_command("python -m llama_cpp.server --model D:\\a.gguf")
    assert not servers.is_inference_command("python -m other.server")
    assert not servers.is_inference_command("")


def test_wwc_executable_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """环境变量能指定 exe 位置（打包运行或换机器时用）。"""
    fake = tmp_path / "wwc.exe"
    fake.write_text("x", encoding="utf-8")
    monkeypatch.setenv(servers.ENV_EXE, str(fake))
    assert servers.wwc_executable() == fake


def test_wwc_executable_missing_points_at_build_script(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """找不到 exe 时要说清怎么编译，别让人对着「文件不存在」猜。"""
    monkeypatch.delenv(servers.ENV_EXE, raising=False)
    monkeypatch.setattr(servers, "WWC_BUILD_DIR", tmp_path)
    with pytest.raises(FileNotFoundError) as excinfo:
        servers.wwc_executable()
    assert "build.ps1" in str(excinfo.value)


def test_launcher_start_spawns_world_work_cpp(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """默认档位拉起的是自写服务端，命令行第一个是它的 exe。"""
    model = tmp_path / "m.gguf"
    model.write_bytes(b"x")
    monkeypatch.setattr(
        launcher,
        "profile_by_id",
        lambda profile_id: _profile(model_path=str(model)),
    )
    monkeypatch.setattr(launcher, "detect_running", lambda model_path: None)
    monkeypatch.setattr(launcher, "_stop_others", lambda: [])
    monkeypatch.setattr(launcher, "free_port", lambda *args, **kwargs: 8080)
    monkeypatch.setattr(launcher, "_wait_ready", lambda *args, **kwargs: True)

    captured: dict[str, Any] = {}

    class _FakeProc:
        pid = 1234

        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def kill(self) -> None:
            return None

    def fake_popen(cmd: list[str], **kwargs: Any) -> _FakeProc:
        captured["cmd"] = cmd
        return _FakeProc()

    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        servers,
        "wwc_executable",
        lambda: Path("C:/x/world-work-cpp.exe"),
    )

    service = launcher.start("t")

    cmd = captured["cmd"]
    assert cmd[0].endswith("world-work-cpp.exe")
    assert "llama_cpp.server" not in cmd
    assert service["server_kind"] == SERVER_KIND_WWC
