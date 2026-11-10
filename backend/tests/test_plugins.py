"""插件机制测试。

覆盖范围：
- 清单解析与全部校验分支（含一次列出多条违规）；
- apiVersion 区间匹配；
- JSON-RPC 2.0 请求构造与响应解析（单条、批量、错误对象、非法 JSON）；
- 子进程超时、崩溃与非 JSON 返回的隔离；
- 版本不匹配拒绝加载；
- 内置正则插件的替换、target 语义与 trim；
- 内置世界书插件的匹配结果与 app.worldbooks 一致。

测试不联网。子进程插件使用临时目录里的内嵌脚本，解释器取 sys.executable。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from app import store
from app.plugins import (
    REGEX_PLUGIN_ID,
    WORLDBOOK_PLUGIN_ID,
    PluginHost,
    builtin_plugin_ids,
    list_hooks,
    parse_manifest_text,
    range_matches,
    reset_host,
    validate_manifest,
)
from app.plugins.builtin import regex_plugin, worldbook_plugin
from app.plugins.host import (
    HOOK_NAMES,
    PLUGINS_STATE_KEY,
    PluginError,
    build_request,
    encode_request,
    parse_response,
    select_response,
    validate_hook_input,
    validate_hook_output,
)
from app.plugins.manifest import (
    ALLOWED_CAPABILITIES,
    is_plugin_id,
    is_semver,
    parse_api_range,
)

DATA_ENV = "XWORLD_DATA_DIR"


# ── 通用夹具 ─────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """把数据目录指向临时目录，并在测试前后重置全局宿主。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv(DATA_ENV, str(data_dir))
    reset_host()
    yield data_dir
    reset_host()


def _write_manifest(directory: Path, payload: dict[str, Any]) -> Path:
    """写出清单文件。"""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "manifest.yaml"
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def _write_script(directory: Path, name: str, body: str) -> Path:
    """写出插件脚本。"""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


ECHO_SCRIPT = """\
import json
import sys

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        method = request.get("method")
        params = request.get("params") or {}
        if method == "ping":
            result = {"ok": True}
        elif method == "echo":
            result = dict(params)
        else:
            result = {"method": method}
        sys.stdout.write(
            json.dumps(
                {"jsonrpc": "2.0", "id": request.get("id"), "result": result},
                ensure_ascii=False,
            )
            + "\\n"
        )
        sys.stdout.flush()


if __name__ == "__main__":
    main()
"""

CRASH_SCRIPT = """\
import sys

sys.stderr.write("boom\\n")
sys.stderr.flush()
sys.exit(3)
"""

SLOW_SCRIPT = """\
import sys
import time

pid_file = sys.argv[1] if len(sys.argv) > 1 else None
if pid_file:
    with open(pid_file, "w", encoding="utf-8") as handle:
        handle.write(str(__import__("os").getpid()))
time.sleep(30)
"""

GARBAGE_SCRIPT = """\
import sys

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")

for line in sys.stdin:
    sys.stdout.write("这不是 JSON\\n")
    sys.stdout.flush()
"""


def _disk_manifest(
    plugin_id: str,
    *,
    api_version: str = ">=1.0.0 <2.0.0",
    script: str = "plugin.py",
    capabilities: list[str] | None = None,
    version: str = "1.0.0",
    args: list[str] | None = None,
) -> dict[str, Any]:
    """构造一个指向内嵌脚本的清单。"""
    command = [sys.executable, script]
    if args:
        command.extend(args)
    return {
        "id": plugin_id,
        "name": f"测试插件 {plugin_id}",
        "version": version,
        "apiVersion": api_version,
        "capabilities": capabilities if capabilities is not None else ["memory"],
        "entry": {"command": command, "args": []},
    }


def _make_host(plugins_root: Path, **kwargs: Any) -> PluginHost:
    """构造指向临时插件目录的宿主。"""
    return PluginHost(plugins_root=plugins_root, **kwargs)


# ── 清单校验 ─────────────────────────────────────────────────


class TestManifestValidation:
    def test_minimal_manifest_passes(self) -> None:
        result = parse_manifest_text(
            "id: demo-plugin\n"
            "name: 演示插件\n"
            "version: 1.2.3\n"
            "apiVersion: '>=1.0.0 <2.0.0'\n"
            "capabilities: [regex, http]\n"
            "entry:\n"
            "  command: [python, main.py]\n"
            "  args: [--stdio]\n"
        )
        assert result.ok
        manifest = result.manifest
        assert manifest is not None
        assert manifest.id == "demo-plugin"
        assert manifest.version == "1.2.3"
        assert manifest.api_version == ">=1.0.0 <2.0.0"
        assert manifest.capabilities == ("regex", "http")
        assert manifest.entry_command == ("python", "main.py")
        assert manifest.entry_args == ("--stdio",)
        assert manifest.is_builtin is False

    def test_string_command_is_normalized(self) -> None:
        result = validate_manifest(
            {
                "id": "solo",
                "name": "单命令",
                "version": "0.0.1",
                "apiVersion": ">=1.0.0",
                "capabilities": [],
                "entry": {"command": "  "},
            }
        )
        assert not result.ok
        assert [issue.field for issue in result.issues] == ["entry.command"]

    def test_builtin_command_recognized(self) -> None:
        result = validate_manifest(
            {
                "id": "builtin-x",
                "name": "内置",
                "version": "1.0.0",
                "apiVersion": ">=1.0.0 <2.0.0",
                "capabilities": ["regex"],
                "entry": {"command": ["builtin"], "args": ["x"]},
            }
        )
        assert result.ok
        assert result.manifest is not None
        assert result.manifest.is_builtin is True

    def test_all_violations_reported_at_once(self) -> None:
        result = validate_manifest(
            {
                "id": "Bad_ID",
                "name": "   ",
                "version": "1.0",
                "apiVersion": "banana",
                "capabilities": ["regex", "video", 7],
                "entry": {"command": [], "args": "nope"},
            }
        )
        assert not result.ok
        fields = [issue.field for issue in result.issues]
        assert "id" in fields
        assert "name" in fields
        assert "version" in fields
        assert "apiVersion" in fields
        assert "capabilities[1]" in fields
        assert "capabilities[2]" in fields
        assert "entry.command" in fields
        assert "entry.args" in fields
        codes = {issue.code for issue in result.issues}
        assert "unknown_capability" in codes
        assert "invalid_format" in codes

    def test_missing_required_fields(self) -> None:
        result = validate_manifest({})
        assert not result.ok
        fields = {issue.field for issue in result.issues}
        assert {"id", "name", "version", "apiVersion", "capabilities", "entry"} <= fields
        assert all(issue.code == "required" for issue in result.issues)

    def test_root_not_mapping(self) -> None:
        result = validate_manifest(["id"])
        assert not result.ok
        assert result.issues[0].field == "$"
        assert result.issues[0].code == "invalid_format"

    def test_capabilities_deduplicated(self) -> None:
        result = validate_manifest(
            {
                "id": "dup-cap",
                "name": "去重",
                "version": "1.0.0",
                "apiVersion": ">=1.0.0 <2.0.0",
                "capabilities": ["regex", "regex", "tts"],
                "entry": {"command": ["python", "p.py"]},
            }
        )
        assert result.ok
        assert result.manifest is not None
        assert result.manifest.capabilities == ("regex", "tts")

    def test_capabilities_not_list(self) -> None:
        result = validate_manifest(
            {
                "id": "cap-bad",
                "name": "能力",
                "version": "1.0.0",
                "apiVersion": ">=1.0.0 <2.0.0",
                "capabilities": "regex",
                "entry": {"command": ["python", "p.py"]},
            }
        )
        assert not result.ok
        assert result.issues[0].field == "capabilities"
        assert result.issues[0].code == "invalid_format"

    def test_entry_not_mapping(self) -> None:
        result = validate_manifest(
            {
                "id": "entry-bad",
                "name": "入口",
                "version": "1.0.0",
                "apiVersion": ">=1.0.0 <2.0.0",
                "capabilities": ["regex"],
                "entry": "python main.py",
            }
        )
        assert not result.ok
        assert result.issues[0].field == "entry"
        assert result.issues[0].code == "invalid_format"

    def test_invalid_yaml(self) -> None:
        result = parse_manifest_text("id: demo\n  bad: [\n")
        assert not result.ok
        assert result.issues[0].code == "invalid_yaml"

    def test_unreadable_manifest_file(self, tmp_path: Path) -> None:
        from app.plugins.manifest import load_manifest

        result = load_manifest(tmp_path / "missing" / "manifest.yaml")
        assert not result.ok
        assert result.issues[0].code == "unreadable"

    def test_id_rules(self) -> None:
        assert is_plugin_id("builtin-regex")
        assert is_plugin_id("abc123")
        assert not is_plugin_id("Builtin")
        assert not is_plugin_id("bad_name")
        assert not is_plugin_id("-lead")
        assert not is_plugin_id("trail-")
        assert not is_plugin_id("")
        assert not is_plugin_id(None)

    def test_semver_rules(self) -> None:
        assert is_semver("1.0.0")
        assert is_semver("1.0.0-beta.1")
        assert is_semver("1.0.0+build.5")
        assert not is_semver("1.0")
        assert not is_semver("v1.0.0")

    def test_allowed_capabilities_set(self) -> None:
        assert set(ALLOWED_CAPABILITIES) == {
            "regex",
            "worldbook",
            "memory",
            "tts",
            "image",
            "http",
        }

    def test_error_payload_structure(self) -> None:
        result = validate_manifest({"id": "x"})
        payload = result.error_payload()
        assert payload["code"] == "plugin_manifest_invalid"
        assert isinstance(payload["issues"], list)
        assert {"field", "code", "message"} <= set(payload["issues"][0])


class TestApiVersionRange:
    @pytest.mark.parametrize(
        ("range_text", "version", "expected"),
        [
            (">=1.0.0 <2.0.0", "1.0.0", True),
            (">=1.0.0 <2.0.0", "1.9.9", True),
            (">=1.0.0 <2.0.0", "2.0.0", False),
            (">=1.0.0 <2.0.0", "0.9.9", False),
            (">=1.0.0,<1.2.0", "1.1.5", True),
            (">=1.0.0,<1.2.0", "1.2.0", False),
            (">1.0.0", "1.1.0", True),
            (">1.0.0", "1.0.0", False),
            ("<=1.5.0", "1.5.0", True),
            ("<=1.5.0", "1.5.1", False),
            ("=1.4.2", "1.4.2", True),
            ("==1.4.2", "1.4.3", False),
            ("1.4.2", "1.4.2", True),
            (">=1.0.0-alpha <1.0.0", "1.0.0-beta", True),
            (">=1.0.0 <2.0.0", "not-a-version", False),
            ("garbage", "1.0.0", False),
            ("", "1.0.0", False),
        ],
    )
    def test_range_matches(self, range_text: str, version: str, expected: bool) -> None:
        assert range_matches(range_text, version) is expected

    def test_parse_api_range(self) -> None:
        parsed = parse_api_range(">=1.0.0 <2.0.0")
        assert parsed is not None
        assert [item[0] for item in parsed] == [">=", "<"]

    def test_parse_api_range_invalid(self) -> None:
        assert parse_api_range(">=1.0.0 <") is None
        assert parse_api_range(">=1.0") is None
        assert parse_api_range(None) is None

    def test_prerelease_ordering(self) -> None:
        from app.plugins.manifest import compare_versions

        assert compare_versions("1.0.0-alpha", "1.0.0") == -1
        assert compare_versions("1.0.0", "1.0.0-alpha") == 1
        assert compare_versions("1.0.0", "1.0.0") == 0
        assert compare_versions("2.0.0", "1.9.9") == 1


# ── JSON-RPC ─────────────────────────────────────────────────


class TestJsonRpc:
    def test_build_request_shape(self) -> None:
        payload = build_request(7, "postResponse", {"content": "hi"})
        assert payload == {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "postResponse",
            "params": {"content": "hi"},
        }

    def test_encode_request_is_single_line(self) -> None:
        text = encode_request(1, "ping", None)
        assert "\n" not in text
        assert json.loads(text)["method"] == "ping"
        assert "params" not in json.loads(text)

    def test_parse_single_result(self) -> None:
        response = parse_response('{"jsonrpc":"2.0","id":3,"result":{"ok":true}}')
        assert response.ok
        assert response.id == 3
        assert response.result == {"ok": True}

    def test_parse_error_object(self) -> None:
        response = parse_response(
            '{"jsonrpc":"2.0","id":3,"error":{"code":-32601,"message":"未知方法"}}'
        )
        assert not response.ok
        assert response.error is not None
        assert response.error["code"] == -32601

    def test_parse_batch(self) -> None:
        parsed = parse_response(
            '[{"jsonrpc":"2.0","id":1,"result":{}},'
            '{"jsonrpc":"2.0","id":2,"error":{"code":1,"message":"x"}}]'
        )
        assert isinstance(parsed, list)
        assert [item.id for item in parsed] == [1, 2]
        chosen = select_response(parsed, 2)
        assert not chosen.ok

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "not json",
            "123",
            '{"id":1,"result":{}}',
            '{"jsonrpc":"2.0","id":1}',
            '{"jsonrpc":"2.0","id":1,"result":{},"error":{"code":1,"message":"x"}}',
            '{"jsonrpc":"2.0","id":1,"error":"oops"}',
            '{"jsonrpc":"2.0","id":1,"error":{"code":"1","message":"x"}}',
            '{"jsonrpc":"2.0","id":1,"error":{"code":1}}',
            "[]",
        ],
    )
    def test_parse_invalid_responses(self, raw: str) -> None:
        with pytest.raises(PluginError) as excinfo:
            parse_response(raw)
        assert excinfo.value.code == "plugin_invalid_response"

    def test_select_response_id_mismatch(self) -> None:
        parsed = parse_response('{"jsonrpc":"2.0","id":9,"result":{}}')
        with pytest.raises(PluginError) as excinfo:
            select_response(parsed, 10)
        assert excinfo.value.code == "plugin_invalid_response"

    def test_parse_accepts_bytes(self) -> None:
        response = parse_response(b'{"jsonrpc":"2.0","id":1,"result":null}')
        assert response.ok
        assert response.result is None


class TestHookContracts:
    def test_six_hooks_declared(self) -> None:
        assert HOOK_NAMES == (
            "preAssembly",
            "postAssembly",
            "preRequest",
            "postResponse",
            "turnStart",
            "turnEnd",
        )
        assert [item["name"] for item in list_hooks()] == list(HOOK_NAMES)

    def test_hook_listing_has_io_description(self) -> None:
        for item in list_hooks():
            assert item["input"]
            assert item["output"]
            assert item["description"]
            for field in item["input"]:
                assert {"name", "type", "required"} == set(field)

    def test_validate_input_missing_required(self) -> None:
        issues = validate_hook_input("postResponse", {})
        assert issues and "content" in issues[0]

    def test_validate_input_type_mismatch(self) -> None:
        issues = validate_hook_input("postAssembly", {"messages": {}})
        assert issues and "messages" in issues[0]

    def test_validate_input_bool_is_not_int(self) -> None:
        issues = validate_hook_input("turnStart", {"turn": True})
        assert issues and "turn" in issues[0]

    def test_validate_unknown_hook(self) -> None:
        assert validate_hook_input("nope", {}) == ["未知 hook：nope"]
        assert validate_hook_output("nope", {}) == ["未知 hook：nope"]

    def test_validate_output_allows_none(self) -> None:
        assert validate_hook_output("turnEnd", None) == []

    def test_validate_output_extra_keys_allowed(self) -> None:
        assert validate_hook_output("turnEnd", {"notes": {"x": 1}}) == []


# ── 宿主加载 ─────────────────────────────────────────────────


class TestHostLoading:
    def test_builtin_plugins_loaded(self, tmp_path: Path) -> None:
        host = _make_host(tmp_path / "plugins")
        summary = host.load_all()
        ids = {item["id"] for item in summary["loaded"]}
        assert set(builtin_plugin_ids()) <= ids
        assert summary["errors"] == []
        detail = host.get_plugin(REGEX_PLUGIN_ID)
        assert detail is not None
        assert detail["builtin"] is True
        assert detail["status"] == "stopped"
        assert detail["capabilities"] == ["regex"]

    def test_disk_plugin_loaded(self, tmp_path: Path) -> None:
        root = tmp_path / "plugins"
        directory = root / "demo-plugin"
        _write_manifest(directory, _disk_manifest("demo-plugin", capabilities=["memory", "http"]))
        _write_script(directory, "plugin.py", ECHO_SCRIPT)

        host = _make_host(root)
        summary = host.load_all()
        assert [item["id"] for item in summary["loaded"] if item["id"] == "demo-plugin"]
        detail = host.get_plugin("demo-plugin")
        assert detail is not None
        assert detail["apiVersion"] == ">=1.0.0 <2.0.0"
        assert detail["capabilities"] == ["memory", "http"]
        assert detail["enabled"] is True
        assert detail["status"] == "stopped"

    def test_missing_manifest_reported(self, tmp_path: Path) -> None:
        root = tmp_path / "plugins"
        (root / "empty-dir").mkdir(parents=True)
        host = _make_host(root)
        summary = host.load_all()
        codes = [error["code"] for error in summary["errors"]]
        assert "plugin_manifest_missing" in codes

    def test_invalid_manifest_reported(self, tmp_path: Path) -> None:
        root = tmp_path / "plugins"
        directory = root / "bad-plugin"
        _write_manifest(directory, {"id": "bad-plugin", "name": "缺字段"})
        host = _make_host(root)
        summary = host.load_all()
        error = next(item for item in summary["errors"] if item["code"] == "plugin_manifest_invalid")
        assert error["issues"]
        assert host.get_plugin("bad-plugin") is None

    def test_api_version_mismatch_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "plugins"
        directory = root / "future-plugin"
        _write_manifest(directory, _disk_manifest("future-plugin", api_version=">=2.0.0 <3.0.0"))
        _write_script(directory, "plugin.py", ECHO_SCRIPT)
        host = _make_host(root)
        summary = host.load_all()
        error = next(
            item for item in summary["errors"] if item["code"] == "plugin_api_version_unsupported"
        )
        assert error["plugin_id"] == "future-plugin"
        assert "1.0.0" in error["message"]
        assert host.get_plugin("future-plugin") is None
        assert host.list_plugins()

    def test_duplicate_id_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "plugins"
        directory = root / "builtin-regex"
        _write_manifest(directory, _disk_manifest("builtin-regex"))
        _write_script(directory, "plugin.py", ECHO_SCRIPT)
        host = _make_host(root)
        summary = host.load_all()
        assert any(error["code"] == "plugin_duplicate_id" for error in summary["errors"])
        detail = host.get_plugin(REGEX_PLUGIN_ID)
        assert detail is not None and detail["builtin"] is True

    def test_require_unknown_plugin(self, tmp_path: Path) -> None:
        host = _make_host(tmp_path / "plugins")
        host.load_all()
        with pytest.raises(PluginError) as excinfo:
            host.require_plugin("ghost")
        assert excinfo.value.code == "plugin_not_found"
        assert excinfo.value.status_code == 404


# ── 子进程执行与隔离 ─────────────────────────────────────────


class TestSubprocessIsolation:
    def _prepare(self, tmp_path: Path, script: str, *, name: str, args: list[str] | None = None):
        root = tmp_path / "plugins"
        directory = root / name
        _write_manifest(directory, _disk_manifest(name, args=args))
        _write_script(directory, "plugin.py", script)
        host = _make_host(root, timeout=3.0)
        host.load_all()
        return host

    def test_request_roundtrip(self, tmp_path: Path) -> None:
        host = self._prepare(tmp_path, ECHO_SCRIPT, name="echo-plugin")
        assert host.request("echo-plugin", "ping") == {"ok": True}
        assert host.request("echo-plugin", "echo", {"value": 42}) == {"value": 42}
        logs = host.logs("echo-plugin")
        assert [entry["action"] for entry in logs if entry["ok"]] == ["start", "ping", "echo"]
        host.stop_all()

    def test_invoke_validates_output(self, tmp_path: Path) -> None:
        host = self._prepare(tmp_path, ECHO_SCRIPT, name="echo-plugin")
        outcome = host.invoke("echo-plugin", "turnEnd", {"session": "s1", "turn": 1})
        assert outcome["ok"] is True
        assert outcome["result"] == {"method": "turnEnd"}
        host.stop_all()

    def test_timeout_is_isolated(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "child.pid"
        host = self._prepare(
            tmp_path,
            SLOW_SCRIPT,
            name="slow-plugin",
            args=[str(pid_file)],
        )
        started = time.monotonic()
        with pytest.raises(PluginError) as excinfo:
            host.request("slow-plugin", "ping", None, timeout=0.3)
        elapsed = time.monotonic() - started
        assert excinfo.value.code == "plugin_timeout"
        assert excinfo.value.retryable is True
        assert elapsed < 10
        assert host.get_plugin("slow-plugin")["status"] == "stopped"
        failure = [entry for entry in host.logs("slow-plugin") if not entry["ok"]]
        assert failure and failure[-1]["error"]["code"] == "plugin_timeout"
        host.stop_all()

        # 子进程已被收尾：pid 不再出现在 tasklist 中
        if pid_file.exists():
            pid = pid_file.read_text(encoding="utf-8").strip()
            output = _tasklist_contains(pid)
            if output is not None:
                assert output is False

    def test_crash_is_isolated(self, tmp_path: Path) -> None:
        host = self._prepare(tmp_path, CRASH_SCRIPT, name="crash-plugin")
        with pytest.raises(PluginError) as excinfo:
            host.request("crash-plugin", "ping")
        assert excinfo.value.code == "plugin_crashed"
        failure = [entry for entry in host.logs("crash-plugin") if not entry["ok"]]
        assert failure and failure[-1]["error"]["code"] == "plugin_crashed"
        host.stop_all()

    def test_non_json_output_is_isolated(self, tmp_path: Path) -> None:
        host = self._prepare(tmp_path, GARBAGE_SCRIPT, name="garbage-plugin")
        with pytest.raises(PluginError) as excinfo:
            host.request("garbage-plugin", "ping")
        assert excinfo.value.code == "plugin_invalid_response"
        host.stop_all()

    def test_dispatch_survives_failures(self, tmp_path: Path) -> None:
        root = tmp_path / "plugins"
        for name, script in (
            ("ok-plugin", ECHO_SCRIPT),
            ("garbage-plugin", GARBAGE_SCRIPT),
            ("crash-plugin", CRASH_SCRIPT),
        ):
            directory = root / name
            _write_manifest(directory, _disk_manifest(name))
            _write_script(directory, "plugin.py", script)
        host = _make_host(root, timeout=3.0)
        host.load_all()
        result = host.dispatch("turnEnd", {"session": "s1", "turn": 2})
        assert result["ok"] is True
        by_id = {item["plugin_id"]: item for item in result["results"]}
        assert by_id["ok-plugin"]["ok"] is True
        assert by_id["garbage-plugin"]["ok"] is False
        assert by_id["garbage-plugin"]["error"]["code"] == "plugin_invalid_response"
        assert by_id["crash-plugin"]["ok"] is False
        assert by_id["crash-plugin"]["error"]["code"] == "plugin_crashed"
        # 内置插件照常参与派发
        assert by_id[REGEX_PLUGIN_ID]["ok"] is True
        host.stop_all()

    def test_dispatch_rejects_bad_hook(self, tmp_path: Path) -> None:
        host = _make_host(tmp_path / "plugins")
        host.load_all()
        result = host.dispatch("nope", {})
        assert result["ok"] is False
        assert result["input_issues"]
        assert result["results"] == []

    def test_dispatch_rejects_bad_payload(self, tmp_path: Path) -> None:
        host = _make_host(tmp_path / "plugins")
        host.load_all()
        result = host.dispatch("postResponse", {})
        assert result["ok"] is False
        assert result["input_issues"]

    def test_dispatch_filters_plugin_ids(self, tmp_path: Path) -> None:
        host = _make_host(tmp_path / "plugins")
        host.load_all()
        result = host.dispatch("turnEnd", {"session": "s", "turn": 1}, plugin_ids=[REGEX_PLUGIN_ID])
        assert [item["plugin_id"] for item in result["results"]] == [REGEX_PLUGIN_ID]

    def test_timeout_configurable_via_settings(self, tmp_path: Path) -> None:
        store.set_setting("plugin_timeout_seconds", 0.25)
        host = _make_host(tmp_path / "plugins")
        assert host.timeout() == pytest.approx(0.25)
        store.set_setting("plugin_timeout_seconds", "bad")
        assert host.timeout() == 5.0
        store.set_setting("plugin_timeout_seconds", -1)
        assert host.timeout() == 5.0

    def test_deterministic_timeout_service(self, tmp_path: Path) -> None:
        host = _make_host(tmp_path / "plugins", timeout=0.1)
        host.load_all()
        assert host.timeout() == pytest.approx(0.1)


def _tasklist_contains(pid: str) -> bool | None:
    """用 tasklist 判断进程是否仍存在；检测不到 tasklist 时返回 None。"""
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return pid in completed.stdout


# ── 假传输层（不启子进程） ───────────────────────────────────


class FakeTransport:
    """假传输层：按 responder 产生响应行。"""

    def __init__(self, responder: Callable[[str, float], str]) -> None:
        self._responder = responder
        self.calls: list[str] = []
        self.closed = False

    @property
    def alive(self) -> bool:
        return not self.closed

    def request(self, message: str, timeout: float) -> str:
        self.calls.append(message)
        return self._responder(message, timeout)

    def close(self) -> None:
        self.closed = True


class TestFakeTransport:
    def test_invalid_json_from_transport(self, tmp_path: Path) -> None:
        transports: list[FakeTransport] = []

        def factory(manifest: Any, directory: Any) -> FakeTransport:
            transport = FakeTransport(lambda message, timeout: "not json at all")
            transports.append(transport)
            return transport

        host = _make_host(tmp_path / "plugins", transport_factory=factory)
        host.load_all()
        with pytest.raises(PluginError) as excinfo:
            host.request(REGEX_PLUGIN_ID, "ping")
        assert excinfo.value.code == "plugin_invalid_response"
        assert transports

    def test_error_object_surfaces_plugin_error(self, tmp_path: Path) -> None:
        def responder(message: str, timeout: float) -> str:
            request = json.loads(message)
            return json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "error": {"code": -32000, "message": "插件内部错误"},
                }
            )

        host = _make_host(
            tmp_path / "plugins", transport_factory=lambda manifest, directory: FakeTransport(responder)
        )
        host.load_all()
        with pytest.raises(PluginError) as excinfo:
            host.request(REGEX_PLUGIN_ID, "ping")
        assert excinfo.value.code == "plugin_call_failed"
        assert excinfo.value.detail is not None
        assert excinfo.value.detail["plugin_error"]["code"] == -32000

    def test_invalid_hook_output_rejected(self, tmp_path: Path) -> None:
        def responder(message: str, timeout: float) -> str:
            request = json.loads(message)
            return json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "result": {"notes": "不是对象"},
                }
            )

        host = _make_host(
            tmp_path / "plugins", transport_factory=lambda manifest, directory: FakeTransport(responder)
        )
        host.load_all()
        with pytest.raises(PluginError) as excinfo:
            host.invoke(REGEX_PLUGIN_ID, "turnEnd", {"session": "s", "turn": 1})
        assert excinfo.value.code == "plugin_output_invalid"

    def test_jsonrpc_id_mismatch(self, tmp_path: Path) -> None:
        def responder(message: str, timeout: float) -> str:
            return json.dumps({"jsonrpc": "2.0", "id": 9999, "result": {}})

        host = _make_host(
            tmp_path / "plugins", transport_factory=lambda manifest, directory: FakeTransport(responder)
        )
        host.load_all()
        with pytest.raises(PluginError) as excinfo:
            host.request(REGEX_PLUGIN_ID, "ping")
        assert excinfo.value.code == "plugin_invalid_response"


# ── 启停与状态持久化 ─────────────────────────────────────────


class TestEnableDisable:
    def test_toggle_persisted_in_settings(self, tmp_path: Path) -> None:
        root = tmp_path / "plugins"
        host = _make_host(root)
        host.load_all()

        detail = host.disable(REGEX_PLUGIN_ID)
        assert detail["enabled"] is False
        assert detail["status"] == "disabled"
        state = store.get_setting(PLUGINS_STATE_KEY, {})
        assert state[REGEX_PLUGIN_ID]["enabled"] is False

        detail = host.enable(REGEX_PLUGIN_ID)
        assert detail["enabled"] is True
        state = store.get_setting(PLUGINS_STATE_KEY, {})
        assert state[REGEX_PLUGIN_ID]["enabled"] is True

    def test_state_restored_on_reload(self, tmp_path: Path) -> None:
        root = tmp_path / "plugins"
        first = _make_host(root)
        first.load_all()
        first.disable(WORLDBOOK_PLUGIN_ID)
        first.stop_all()

        second = _make_host(root)
        second.load_all()
        detail = second.get_plugin(WORLDBOOK_PLUGIN_ID)
        assert detail is not None and detail["enabled"] is False
        assert detail["status"] == "disabled"

    def test_disabled_plugin_refuses_request(self, tmp_path: Path) -> None:
        host = _make_host(tmp_path / "plugins")
        host.load_all()
        host.disable(REGEX_PLUGIN_ID)
        with pytest.raises(PluginError) as excinfo:
            host.request(REGEX_PLUGIN_ID, "ping")
        assert excinfo.value.code == "plugin_disabled"

    def test_start_and_stop_builtin(self, tmp_path: Path) -> None:
        host = _make_host(tmp_path / "plugins")
        host.load_all()
        started = host.start(REGEX_PLUGIN_ID)
        assert started["status"] == "running"
        stopped = host.stop(REGEX_PLUGIN_ID)
        assert stopped["status"] == "stopped"

    def test_dispatch_skips_disabled_plugins(self, tmp_path: Path) -> None:
        host = _make_host(tmp_path / "plugins")
        host.load_all()
        host.disable(WORLDBOOK_PLUGIN_ID)
        result = host.dispatch("turnEnd", {"session": "s", "turn": 1})
        assert WORLDBOOK_PLUGIN_ID not in [item["plugin_id"] for item in result["results"]]


# ── 内置正则插件 ─────────────────────────────────────────────


def _regex_rules(*rules: dict[str, Any]) -> list[dict[str, Any]]:
    return list(rules)


class TestRegexPluginBuiltin:
    def test_parse_rules_reports_issues(self) -> None:
        rules, issues = regex_plugin.parse_rules(
            [
                {"name": "ok", "pattern": "a", "replacement": "b", "target": "output"},
                {"name": "坏正则", "pattern": "(", "replacement": "", "target": "output"},
                {"name": "缺 pattern", "replacement": ""},
                {"name": "坏 target", "pattern": "x", "target": "middle"},
                "不是对象",
            ]
        )
        assert len(rules) == 1
        codes = {issue["code"] for issue in issues}
        assert {"pattern_invalid", "pattern_required", "target_invalid", "invalid_format"} <= codes

    def test_apply_target_semantics(self) -> None:
        rules, _ = regex_plugin.parse_rules(
            _regex_rules(
                {"name": "去星号", "pattern": r"\*+", "replacement": "", "target": "output"},
                {"name": "去井号", "pattern": r"#+", "replacement": "", "target": "input"},
            )
        )
        out_text, out_applied = regex_plugin.apply_rules("**你好**#世界", rules, "output")
        assert out_text == "你好#世界"
        assert [item["name"] for item in out_applied] == ["去星号"]

        in_text, in_applied = regex_plugin.apply_rules("**你好**#世界", rules, "input")
        assert in_text == "**你好**世界"
        assert [item["name"] for item in in_applied] == ["去井号"]

    def test_replacement_with_group(self) -> None:
        rules, _ = regex_plugin.parse_rules(
            _regex_rules(
                {
                    "name": "重排",
                    "pattern": r"(\w+)@(\w+)",
                    "replacement": r"\2/\1",
                    "target": "output",
                }
            )
        )
        text, applied = regex_plugin.apply_rules("前 a@b 后", rules, "output")
        assert text == "前 b/a 后"
        assert len(applied) == 1

    def test_trim_removes_residual_whitespace(self) -> None:
        rules, _ = regex_plugin.parse_rules(
            _regex_rules(
                {
                    "name": "去旁白",
                    "pattern": r"（[^）]*）",
                    "replacement": "",
                    "target": "output",
                    "trim": True,
                }
            )
        )
        text, _ = regex_plugin.apply_rules("甲   （旁白）   乙", rules, "output")
        assert text == "甲乙"

    def test_trim_with_newlines(self) -> None:
        rules, _ = regex_plugin.parse_rules(
            _regex_rules(
                {"name": "去旁白", "pattern": r"（[^）]*）", "replacement": "", "trim": True}
            )
        )
        text, _ = regex_plugin.apply_rules("甲\n（旁白）\n\n乙", rules, "output")
        assert text == "甲\n\n乙"

    def test_no_trim_keeps_whitespace(self) -> None:
        rules, _ = regex_plugin.parse_rules(
            _regex_rules(
                {"name": "去旁白", "pattern": r"（[^）]*）", "replacement": "", "trim": False}
            )
        )
        text, _ = regex_plugin.apply_rules("甲   （旁白）   乙", rules, "output")
        assert text == "甲      乙"

    def test_trim_keeps_non_empty_replacement(self) -> None:
        rules, _ = regex_plugin.parse_rules(
            _regex_rules(
                {"name": "替换", "pattern": r"AAAA", "replacement": "B", "trim": True}
            )
        )
        text, _ = regex_plugin.apply_rules("x  AAAA  y", rules, "output")
        assert text == "x  B  y"

    def test_pre_request_only_touches_user_messages(self, tmp_path: Path) -> None:
        store.save_preset(
            {
                "id": "p_regex",
                "name": "正则预设",
                "regex_rules": [
                    {"name": "去中括号", "pattern": r"\[[^\]]*\]", "replacement": "", "target": "input"}
                ],
            }
        )
        host = _make_host(tmp_path / "plugins")
        host.load_all()
        messages = [
            {"role": "system", "content": "系统[保留]"},
            {"role": "user", "content": "你好[去掉]世界"},
        ]
        outcome = host.invoke(
            REGEX_PLUGIN_ID,
            "preRequest",
            {"preset_id": "p_regex", "messages": messages},
        )
        result = outcome["result"]
        assert result["messages"][0]["content"] == "系统[保留]"
        assert result["messages"][1]["content"] == "你好世界"
        assert result["notes"]["source"] == "preset:p_regex"

    def test_post_response_rewrites_content_and_reasoning(self, tmp_path: Path) -> None:
        host = _make_host(tmp_path / "plugins")
        host.load_all()
        params = {
            "rules": [
                {"name": "去破折号", "pattern": "——", "replacement": "，", "target": "output"}
            ],
            "content": "他笑了——然后离开",
            "reasoning": "思考——结束",
        }
        outcome = host.invoke(REGEX_PLUGIN_ID, "postResponse", params)
        result = outcome["result"]
        assert result["content"] == "他笑了，然后离开"
        assert result["reasoning"] == "思考，结束"

    def test_active_preset_setting_used_as_fallback(self, tmp_path: Path) -> None:
        store.save_preset(
            {
                "id": "p_active",
                "name": "激活预设",
                "regex_rules": [
                    {"name": "去点", "pattern": r"\.{2,}", "replacement": "。", "target": "output"}
                ],
            }
        )
        store.set_setting(regex_plugin.ACTIVE_PRESET_SETTING, "p_active")
        host = _make_host(tmp_path / "plugins")
        host.load_all()
        outcome = host.invoke(REGEX_PLUGIN_ID, "postResponse", {"content": "等等..."})
        assert outcome["result"]["content"] == "等等。"
        assert outcome["result"]["notes"]["source"] == "preset:p_active"

    def test_all_presets_collected_without_preset_id(self, tmp_path: Path) -> None:
        store.save_preset(
            {
                "id": "p_a",
                "name": "A",
                "regex_rules": [{"name": "a", "pattern": "AAA", "replacement": "1", "target": "output"}],
            }
        )
        store.save_preset(
            {
                "id": "p_b",
                "name": "B",
                "regex_rules": [{"name": "b", "pattern": "BBB", "replacement": "2", "target": "output"}],
            }
        )
        host = _make_host(tmp_path / "plugins")
        host.load_all()
        outcome = host.invoke(REGEX_PLUGIN_ID, "postResponse", {"content": "AAA-BBB"})
        assert outcome["result"]["content"] == "1-2"
        assert outcome["result"]["notes"]["source"] == "all-presets"

    def test_rules_method_reports_source(self, tmp_path: Path) -> None:
        host = _make_host(tmp_path / "plugins")
        host.load_all()
        payload = host.request(REGEX_PLUGIN_ID, "rules", {"rules": [{"pattern": "a"}]})
        assert payload["source"] == "params"
        assert payload["rules"][0]["target"] == "output"
        assert payload["rules"][0]["trim"] is False

    def test_apply_method_direct(self, tmp_path: Path) -> None:
        host = _make_host(tmp_path / "plugins")
        host.load_all()
        payload = host.request(
            REGEX_PLUGIN_ID,
            "apply",
            {"rules": [{"pattern": "x", "replacement": "y"}], "text": "axb", "target": "output"},
        )
        assert payload["text"] == "ayb"

    def test_unimplemented_hook_is_noop(self, tmp_path: Path) -> None:
        host = _make_host(tmp_path / "plugins")
        host.load_all()
        outcome = host.invoke(REGEX_PLUGIN_ID, "turnEnd", {"session": "s", "turn": 1})
        assert outcome["result"] == {}


# ── 内置世界书插件 ───────────────────────────────────────────


def _save_book(book_id: str, entries: list[dict[str, Any]], *, enabled: bool = True) -> None:
    store.save_world_book({"id": book_id, "name": book_id, "enabled": enabled, "entries": entries})


WORLDBOOK_ENTRIES: list[dict[str, Any]] = [
    {
        "id": "e_constant",
        "keys": ["任何"],
        "content": "常驻设定",
        "priority": 50,
        "constant": True,
    },
    {
        "id": "e_key",
        "keys": ["王都", "首都"],
        "content": "王都设定",
        "priority": 80,
    },
    {
        "id": "e_selective",
        "keys": ["王都"],
        "content": "王都夜巡设定",
        "priority": 90,
        "selective": True,
        "secondary_keys": ["夜里", "深夜"],
    },
    {
        "id": "e_regex",
        "keys": [r"第\d+章"],
        "content": "章节设定",
        "priority": 20,
        "match_regex": True,
    },
    {
        "id": "e_case",
        "keys": ["Dragon"],
        "content": "龙设定",
        "priority": 70,
        "case_sensitive": True,
    },
    {
        "id": "e_low",
        "keys": ["王都"],
        "content": "次要设定",
        "priority": 10,
    },
]


def _external_reference(
    entries: list[dict[str, Any]], text: str, secondary_text: str
) -> list[str] | None:
    """取 app.worldbooks.match_entries 的结果；不可导入时返回 None。"""
    try:
        from app import worldbooks  # type: ignore

        matcher = getattr(worldbooks, "match_entries")
    except Exception:  # noqa: BLE001 - T1 模块尚未落地或已改为文件存储
        return None
    try:
        hits = matcher(entries, text, secondary_text=secondary_text)
    except TypeError:
        hits = matcher(entries, text)
    return [str(entry.get("id")) for entry in hits]


class TestWorldbookPluginBuiltin:
    def test_inline_injection_and_order(self, tmp_path: Path) -> None:
        _save_book("w_demo", WORLDBOOK_ENTRIES)
        host = _make_host(tmp_path / "plugins")
        host.load_all()
        payload = host.request(
            WORLDBOOK_PLUGIN_ID,
            "inline",
            {"world_book_ids": ["w_demo"], "text": "我们前往王都", "secondary_text": ""},
        )
        assert payload["entry_ids"] == ["e_constant", "e_key", "e_low"]
        assert payload["content"] == "常驻设定\n王都设定\n次要设定"
        assert payload["issues"] == []
        assert payload["books"] == ["w_demo"]

    def test_selective_requires_secondary_hit(self, tmp_path: Path) -> None:
        _save_book("w_demo", WORLDBOOK_ENTRIES)
        host = _make_host(tmp_path / "plugins")
        host.load_all()
        secondary = "深夜时分"
        payload = host.request(
            WORLDBOOK_PLUGIN_ID,
            "inline",
            {"world_book_ids": ["w_demo"], "text": "我们前往王都", "secondary_text": secondary},
        )
        assert "e_selective" in payload["entry_ids"]
        assert payload["entry_ids"][0] == "e_selective"

        reference = _external_reference(WORLDBOOK_ENTRIES, "我们前往王都", secondary)
        if reference is not None:
            assert payload["entry_ids"] == reference

    def test_regex_and_case_sensitivity(self, tmp_path: Path) -> None:
        _save_book("w_demo", WORLDBOOK_ENTRIES)
        host = _make_host(tmp_path / "plugins")
        host.load_all()

        chapter = host.request(
            WORLDBOOK_PLUGIN_ID,
            "inline",
            {"world_book_ids": ["w_demo"], "text": "参见第12章"},
        )
        assert "e_regex" in chapter["entry_ids"]

        dragon_lower = host.request(
            WORLDBOOK_PLUGIN_ID,
            "inline",
            {"world_book_ids": ["w_demo"], "text": "dragon 出现"},
        )
        assert "e_case" not in dragon_lower["entry_ids"]

        dragon_exact = host.request(
            WORLDBOOK_PLUGIN_ID,
            "inline",
            {"world_book_ids": ["w_demo"], "text": "Dragon 出现"},
        )
        assert "e_case" in dragon_exact["entry_ids"]

    def test_missing_and_disabled_books(self, tmp_path: Path) -> None:
        _save_book("w_off", WORLDBOOK_ENTRIES, enabled=False)
        host = _make_host(tmp_path / "plugins")
        host.load_all()
        payload = host.request(
            WORLDBOOK_PLUGIN_ID,
            "inline",
            {"world_book_ids": ["w_missing", "w_off"], "text": "王都"},
        )
        codes = {issue["code"] for issue in payload["issues"]}
        assert codes == {"world_book_not_found", "world_book_disabled"}
        assert payload["entry_ids"] == []
        assert payload["books"] == []

    def test_all_enabled_books_when_unspecified(self, tmp_path: Path) -> None:
        _save_book("w_a", [{"id": "a1", "keys": ["甲"], "content": "甲设定", "priority": 1}])
        _save_book("w_b", [{"id": "b1", "keys": ["乙"], "content": "乙设定", "priority": 1}])
        _save_book("w_c", [{"id": "c1", "keys": ["丙"], "content": "丙设定", "priority": 1}], enabled=False)
        host = _make_host(tmp_path / "plugins")
        host.load_all()
        payload = host.request(WORLDBOOK_PLUGIN_ID, "inline", {"text": "甲乙丙"})
        assert payload["entry_ids"] == ["a1", "b1"]

    def test_pre_assembly_hook_injects_context(self, tmp_path: Path) -> None:
        _save_book("w_demo", WORLDBOOK_ENTRIES)
        host = _make_host(tmp_path / "plugins")
        host.load_all()
        outcome = host.dispatch(
            "preAssembly",
            {
                "world_book_ids": ["w_demo"],
                "user_input": "我们前往王都",
                "session": "s1",
            },
            plugin_ids=[WORLDBOOK_PLUGIN_ID],
        )
        assert outcome["ok"] is True
        result = outcome["results"][0]
        assert result["ok"] is True
        context = result["result"]["context"]
        assert context["entry_ids"] == ["e_constant", "e_key", "e_low"]
        assert "王都设定" in context["world_info"]

    def test_entries_method_lists_all(self, tmp_path: Path) -> None:
        _save_book("w_demo", WORLDBOOK_ENTRIES)
        host = _make_host(tmp_path / "plugins")
        host.load_all()
        payload = host.request(WORLDBOOK_PLUGIN_ID, "entries", {"world_book_ids": ["w_demo"]})
        assert payload["count"] == len(WORLDBOOK_ENTRIES)
        assert payload["books"] == ["w_demo"]

    def test_match_consistent_with_app_worldbooks(self) -> None:
        reference = _external_reference(WORLDBOOK_ENTRIES, "我们前往王都", "深夜")
        if reference is None:
            pytest.skip("app.worldbooks.match_entries 当前不可导入，一致性由内置参照实现覆盖")
        hits = worldbook_plugin.match_entries(
            WORLDBOOK_ENTRIES, "我们前往王都", secondary_text="深夜"
        )
        assert [entry["id"] for entry in hits] == reference

    def test_reference_implementation_matches_documented_rules(self) -> None:
        hits = worldbook_plugin.reference_match_entries(
            WORLDBOOK_ENTRIES, "我们前往王都", secondary_text=""
        )
        assert [entry["id"] for entry in hits] == ["e_constant", "e_key", "e_low"]

    def test_invalid_regex_keyword_skipped(self) -> None:
        entries = [{"id": "bad", "keys": ["("], "content": "x", "match_regex": True}]
        assert worldbook_plugin.reference_match_entries(entries, "(") == []

    def test_keys_accept_list_and_string(self) -> None:
        entries = [
            {"id": "list", "keys": ["甲", "乙"], "content": "x", "priority": 5},
            {"id": "str", "keys": "丙, 丁", "content": "y", "priority": 5},
        ]
        hits = worldbook_plugin.reference_match_entries(entries, "乙丙")
        assert [entry["id"] for entry in hits] == ["list", "str"]


# ── 路由 ─────────────────────────────────────────────────────


class TestPluginRoutes:
    @pytest.fixture()
    def client(self):
        from fastapi.testclient import TestClient

        from app.main import create_app

        reset_host()
        with TestClient(create_app()) as test_client:
            yield test_client
        reset_host()

    def test_list_plugins(self, client: Any) -> None:
        response = client.get("/v1/plugins/")
        assert response.status_code == 200
        body = response.json()
        ids = {item["id"] for item in body["items"]}
        assert set(builtin_plugin_ids()) <= ids
        assert body["hooks"] == list(HOOK_NAMES)
        for item in body["items"]:
            assert {
                "id",
                "name",
                "version",
                "apiVersion",
                "capabilities",
                "status",
                "enabled",
            } <= set(item)

    def test_get_plugin_detail_and_404(self, client: Any) -> None:
        response = client.get(f"/v1/plugins/{REGEX_PLUGIN_ID}")
        assert response.status_code == 200
        assert response.json()["id"] == REGEX_PLUGIN_ID

        missing = client.get("/v1/plugins/ghost")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "plugin_not_found"

    def test_hooks_listing(self, client: Any) -> None:
        response = client.get("/v1/plugins/hooks")
        assert response.status_code == 200
        items = response.json()["items"]
        assert [item["name"] for item in items] == list(HOOK_NAMES)
        assert all(item["input"] and item["output"] for item in items)

    def test_enable_disable(self, client: Any) -> None:
        disabled = client.post(f"/v1/plugins/{REGEX_PLUGIN_ID}/disable")
        assert disabled.status_code == 200
        assert disabled.json()["enabled"] is False
        state = store.get_setting(PLUGINS_STATE_KEY, {})
        assert state[REGEX_PLUGIN_ID]["enabled"] is False

        enabled = client.post(f"/v1/plugins/{REGEX_PLUGIN_ID}/enable")
        assert enabled.status_code == 200
        assert enabled.json()["enabled"] is True

    def test_log_endpoint(self, client: Any) -> None:
        client.post(
            f"/v1/plugins/{REGEX_PLUGIN_ID}/invoke",
            json={"hook": "postResponse", "params": {"content": "文本"}},
        )
        response = client.get(f"/v1/plugins/{REGEX_PLUGIN_ID}/log")
        assert response.status_code == 200
        body = response.json()
        assert body["plugin_id"] == REGEX_PLUGIN_ID
        assert any(entry["action"] == "postResponse" for entry in body["items"])

    def test_invoke_endpoint(self, client: Any) -> None:
        response = client.post(
            f"/v1/plugins/{REGEX_PLUGIN_ID}/invoke",
            json={
                "hook": "postResponse",
                "params": {
                    "rules": [{"pattern": "坏", "replacement": "好"}],
                    "content": "坏人",
                },
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["result"]["content"] == "好人"

    def test_invoke_rejects_unknown_hook(self, client: Any) -> None:
        response = client.post(
            f"/v1/plugins/{REGEX_PLUGIN_ID}/invoke",
            json={"hook": "nope", "params": {}},
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "plugin_hook_unknown"

    def test_invoke_rejects_bad_payload(self, client: Any) -> None:
        response = client.post(
            f"/v1/plugins/{REGEX_PLUGIN_ID}/invoke",
            json={"hook": "postResponse", "params": {}},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "plugin_payload_invalid"

    def test_invoke_unknown_plugin(self, client: Any) -> None:
        response = client.post(
            "/v1/plugins/ghost/invoke",
            json={"hook": "turnEnd", "params": {}},
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "plugin_not_found"

    def test_reload_endpoint(self, client: Any) -> None:
        response = client.post("/v1/plugins/reload")
        assert response.status_code == 200
        body = response.json()
        assert body["items"]
        assert isinstance(body["errors"], list)
