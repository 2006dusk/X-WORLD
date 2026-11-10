"""插件宿主：清单加载、子进程管理、JSON-RPC 2.0 通信与 hook 派发。

设计要点：

- 加载：扫描 ``app.config.plugins_dir()`` 下的每个子目录，读取
  ``plugins/<plugin_id>/manifest.yaml``；内置插件由 ``app.plugins.builtin``
  提供同格式清单，与磁盘插件走同一套校验与派发路径。
- 执行：第三方插件以子进程运行，stdio 承载 JSON-RPC 2.0，请求与响应均为单行 JSON。
  内置插件把同一份 JSON-RPC 报文交给进程内处理器，不另开特例通道。
- 隔离：每次调用都有超时上限（默认 5 秒，可经设置或构造参数覆盖）。插件崩溃、
  超时、返回非法 JSON 时由宿主捕获并记入调用日志，``dispatch`` 永不上抛，
  主流程不受影响。
- 版本协商：插件 apiVersion 与 ``HOST_API_RANGE`` 不匹配时拒绝加载，
  错误码 ``plugin_api_version_unsupported``。
- 状态：启停登记写入 ``app.store`` 的 settings，键名 ``plugins_state``。
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Protocol, Sequence, runtime_checkable

from app import store
from app.errors import ApiError

from .manifest import (
    BUILTIN_COMMAND,
    HOST_API_VERSION,
    MANIFEST_FILENAME,
    ManifestValidation,
    PluginManifest,
    load_manifest,
    range_matches,
    validate_manifest,
)

JSONRPC_VERSION = "2.0"
DEFAULT_TIMEOUT_SECONDS = 5.0
LOG_LIMIT = 200
PLUGINS_STATE_KEY = "plugins_state"
TIMEOUT_SETTING_KEY = "plugin_timeout_seconds"

HOOK_NAMES: tuple[str, ...] = (
    "preAssembly",
    "postAssembly",
    "preRequest",
    "postResponse",
    "turnStart",
    "turnEnd",
)

_TYPE_MAP: dict[str, Any] = {
    "str": str,
    "int": int,
    "float": (int, float),
    "bool": bool,
    "list": list,
    "dict": dict,
    "any": object,
}


# ── 错误 ──────────────────────────────────────────────────────


class PluginError(ApiError):
    """插件子系统错误，沿用 app.errors.ApiError 的统一形状。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        status_code: int = 400,
        detail: Any | None = None,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            retryable=retryable,
            status_code=status_code,
            detail=detail,
        )


# ── hook 契约 ─────────────────────────────────────────────────


@dataclass(frozen=True)
class HookContract:
    """单个 hook 的输入输出形状说明。"""

    name: str
    description: str
    input_schema: dict[str, tuple[str, bool]]
    output_schema: dict[str, tuple[str, bool]]

    def to_dict(self) -> dict[str, Any]:
        """转为接口返回形状。"""

        def render(schema: dict[str, tuple[str, bool]]) -> list[dict[str, Any]]:
            return [
                {"name": key, "type": type_name, "required": required}
                for key, (type_name, required) in schema.items()
            ]

        return {
            "name": self.name,
            "description": self.description,
            "input": render(self.input_schema),
            "output": render(self.output_schema),
        }


HOOK_CONTRACTS: dict[str, HookContract] = {
    "preAssembly": HookContract(
        name="preAssembly",
        description="装配前调用，可改写参与装配的输入或注入额外上下文。",
        input_schema={
            "session": ("str", False),
            "character_id": ("str", False),
            "preset_id": ("str", False),
            "world_book_ids": ("list", False),
            "user_input": ("str", True),
            "messages": ("list", False),
        },
        output_schema={
            "messages": ("list", False),
            "context": ("dict", False),
            "notes": ("dict", False),
        },
    ),
    "postAssembly": HookContract(
        name="postAssembly",
        description="装配后调用，可校验或裁剪消息序列与段落。",
        input_schema={
            "session": ("str", False),
            "messages": ("list", True),
            "segments": ("list", False),
            "template": ("str", False),
        },
        output_schema={
            "messages": ("list", False),
            "segments": ("list", False),
            "truncated": ("bool", False),
            "notes": ("dict", False),
        },
    ),
    "preRequest": HookContract(
        name="preRequest",
        description="请求发出前调用，可改写模型参数与消息序列。",
        input_schema={
            "session": ("str", False),
            "model": ("str", False),
            "params": ("dict", False),
            "messages": ("list", True),
        },
        output_schema={
            "model": ("str", False),
            "params": ("dict", False),
            "messages": ("list", False),
            "stop": ("bool", False),
            "notes": ("dict", False),
        },
    ),
    "postResponse": HookContract(
        name="postResponse",
        description="响应返回后调用，可改写正文与思考内容。",
        input_schema={
            "session": ("str", False),
            "content": ("str", True),
            "reasoning": ("str", False),
            "usage": ("dict", False),
        },
        output_schema={
            "content": ("str", False),
            "reasoning": ("str", False),
            "notes": ("dict", False),
        },
    ),
    "turnStart": HookContract(
        name="turnStart",
        description="每轮开始调用，可推进状态或注入主动消息。",
        input_schema={
            "session": ("str", False),
            "turn": ("int", False),
            "character_id": ("str", False),
            "messages": ("list", False),
        },
        output_schema={
            "messages": ("list", False),
            "context": ("dict", False),
            "notes": ("dict", False),
        },
    ),
    "turnEnd": HookContract(
        name="turnEnd",
        description="每轮结束调用，可落库、统计或产出记忆条目。",
        input_schema={
            "session": ("str", False),
            "turn": ("int", False),
            "messages": ("list", False),
            "usage": ("dict", False),
        },
        output_schema={
            "memories": ("list", False),
            "notes": ("dict", False),
        },
    ),
}


def _check_schema(
    payload: Any,
    schema: dict[str, tuple[str, bool]],
    label: str,
) -> list[str]:
    """按 schema 做基本形状校验，返回问题描述列表。"""
    if not isinstance(payload, dict):
        return [f"{label} 必须是对象"]
    issues: list[str] = []
    for name, (type_name, required) in schema.items():
        if name not in payload:
            if required:
                issues.append(f"{label} 缺少必填字段 {name}")
            continue
        value = payload[name]
        if value is None:
            continue
        expected = _TYPE_MAP[type_name]
        if type_name == "int" and isinstance(value, bool):
            issues.append(f"{label}.{name} 类型应为 int")
            continue
        if type_name == "bool" and not isinstance(value, bool):
            issues.append(f"{label}.{name} 类型应为 bool")
            continue
        if not isinstance(value, expected):
            issues.append(f"{label}.{name} 类型应为 {type_name}")
    return issues


def validate_hook_input(hook: str, payload: Any) -> list[str]:
    """校验 hook 输入形状。"""
    contract = HOOK_CONTRACTS.get(hook)
    if contract is None:
        return [f"未知 hook：{hook}"]
    return _check_schema(payload, contract.input_schema, "输入")


def validate_hook_output(hook: str, payload: Any) -> list[str]:
    """校验 hook 输出形状。"""
    contract = HOOK_CONTRACTS.get(hook)
    if contract is None:
        return [f"未知 hook：{hook}"]
    if payload is None:
        return []
    return _check_schema(payload, contract.output_schema, "输出")


def list_hooks() -> list[dict[str, Any]]:
    """列出六个 hook 及其输入输出结构说明。"""
    return [HOOK_CONTRACTS[name].to_dict() for name in HOOK_NAMES]


# ── JSON-RPC 2.0 ─────────────────────────────────────────────


@dataclass
class JsonRpcResponse:
    """单条 JSON-RPC 响应。"""

    id: Any = None
    result: Any = None
    error: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        """是否为成功响应。"""
        return self.error is None

    def to_dict(self) -> dict[str, Any]:
        """转为可序列化字典。"""
        payload: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "id": self.id}
        if self.error is not None:
            payload["error"] = self.error
        else:
            payload["result"] = self.result
        return payload


def build_request(
    request_id: Any,
    method: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造 JSON-RPC 2.0 请求对象。"""
    message: dict[str, Any] = {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "method": method,
    }
    if params is not None:
        message["params"] = params
    return message


def encode_request(
    request_id: Any,
    method: str,
    params: dict[str, Any] | None = None,
) -> str:
    """构造单行 JSON-RPC 请求文本。"""
    return json.dumps(build_request(request_id, method, params), ensure_ascii=False)


def _parse_single_response(data: Any) -> JsonRpcResponse:
    """解析单条响应对象，非法时抛 PluginError。"""
    if not isinstance(data, dict):
        raise PluginError(
            code="plugin_invalid_response",
            message="插件返回的不是 JSON 对象",
            status_code=502,
        )
    if data.get("jsonrpc") != JSONRPC_VERSION:
        raise PluginError(
            code="plugin_invalid_response",
            message="插件响应缺少 jsonrpc 2.0 标记",
            status_code=502,
        )
    has_result = "result" in data
    has_error = "error" in data
    if has_result == has_error:
        raise PluginError(
            code="plugin_invalid_response",
            message="插件响应必须且只能包含 result 或 error 之一",
            status_code=502,
        )
    error = data.get("error")
    if has_error:
        if not isinstance(error, dict):
            raise PluginError(
                code="plugin_invalid_response",
                message="插件响应 error 必须是对象",
                status_code=502,
            )
        if not isinstance(error.get("code"), int):
            raise PluginError(
                code="plugin_invalid_response",
                message="插件响应 error.code 必须是整数",
                status_code=502,
            )
        if not isinstance(error.get("message"), str):
            raise PluginError(
                code="plugin_invalid_response",
                message="插件响应 error.message 必须是字符串",
                status_code=502,
            )
    return JsonRpcResponse(id=data.get("id"), result=data.get("result"), error=error)


def parse_response(raw: str | bytes | dict[str, Any] | list[Any]) -> Any:
    """解析单行响应或批量响应。

    参数:
        raw: 原始单行文本、字节串，或已解析的对象（用于假传输层）。

    返回:
        单条响应返回 ``JsonRpcResponse``，批量响应返回列表。
    """
    if isinstance(raw, (str, bytes)):
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        text = text.strip()
        if not text:
            raise PluginError(
                code="plugin_invalid_response",
                message="插件返回空行",
                status_code=502,
            )
        try:
            data: Any = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PluginError(
                code="plugin_invalid_response",
                message="插件返回的不是合法 JSON",
                status_code=502,
                detail={"raw": text[:200], "reason": str(exc)},
            ) from exc
    else:
        data = raw

    if isinstance(data, list):
        if not data:
            raise PluginError(
                code="plugin_invalid_response",
                message="批量响应不能为空数组",
                status_code=502,
            )
        return [_parse_single_response(item) for item in data]
    return _parse_single_response(data)


def select_response(parsed: Any, request_id: Any) -> JsonRpcResponse:
    """从解析结果中挑出与请求 id 对应的响应。"""
    candidates = parsed if isinstance(parsed, list) else [parsed]
    for candidate in candidates:
        if candidate.id == request_id:
            return candidate
    raise PluginError(
        code="plugin_invalid_response",
        message="插件响应的 id 与请求不匹配",
        status_code=502,
        detail={"request_id": request_id, "ids": [c.id for c in candidates]},
    )


# ── 传输层 ───────────────────────────────────────────────────


@runtime_checkable
class Transport(Protocol):
    """传输层协议：一行请求文本换一行响应文本。"""

    def request(self, message: str, timeout: float) -> str:
        """发送单行请求并返回单行响应。"""

    def close(self) -> None:
        """释放底层资源。"""

    @property
    def alive(self) -> bool:
        """底层通道是否仍可用。"""


class StdioTransport:
    """子进程 stdio 传输层。

    标准输出按行读入队列，请求侧带超时等待；标准错误由后台线程收尾，
    只保留最近若干行用于错误说明。
    """

    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        stderr_limit: int = 20,
    ) -> None:
        self._command = list(command)
        self._stderr: queue.Queue[str] = queue.Queue(maxsize=stderr_limit)
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._closed = False
        try:
            self._process = subprocess.Popen(
                self._command,
                cwd=str(cwd) if cwd is not None else None,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise PluginError(
                code="plugin_start_failed",
                message=f"插件入口命令不存在：{self._command[0] if self._command else ''}",
                status_code=500,
                detail={"command": self._command},
            ) from exc
        except OSError as exc:
            raise PluginError(
                code="plugin_start_failed",
                message=f"插件进程启动失败：{exc}",
                status_code=500,
                detail={"command": self._command},
            ) from exc

        self._stdout_thread = threading.Thread(
            target=self._pump_stdout, name="xw-plugin-stdout", daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=self._pump_stderr, name="xw-plugin-stderr", daemon=True
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    # 内部泵线程

    def _pump_stdout(self) -> None:
        stream = self._process.stdout
        if stream is None:
            self._lines.put(None)
            return
        try:
            for line in stream:
                self._lines.put(line.rstrip("\r\n"))
        except (OSError, ValueError):
            pass
        finally:
            self._lines.put(None)

    def _pump_stderr(self) -> None:
        stream = self._process.stderr
        if stream is None:
            return
        try:
            for line in stream:
                text = line.rstrip("\r\n")
                if not text:
                    continue
                try:
                    self._stderr.put_nowait(text)
                except queue.Full:
                    try:
                        self._stderr.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        self._stderr.put_nowait(text)
                    except queue.Full:
                        pass
        except (OSError, ValueError):
            pass

    def _stderr_tail(self) -> list[str]:
        return list(self._stderr.queue)

    @property
    def alive(self) -> bool:
        """进程是否仍在运行。"""
        return not self._closed and self._process.poll() is None

    def request(self, message: str, timeout: float) -> str:
        """写入一行请求并等待一行响应，超时抛 plugin_timeout。"""
        if self._closed:
            raise PluginError(
                code="plugin_not_started",
                message="插件进程已停止",
                status_code=409,
            )
        stdin = self._process.stdin
        if stdin is None or self._process.poll() is not None:
            raise PluginError(
                code="plugin_crashed",
                message="插件进程已退出",
                status_code=502,
                detail={"exit_code": self._process.poll(), "stderr": self._stderr_tail()},
            )
        try:
            stdin.write(message.rstrip("\n") + "\n")
            stdin.flush()
        except (OSError, ValueError) as exc:
            self._abort()
            raise PluginError(
                code="plugin_crashed",
                message="写入插件进程失败",
                status_code=502,
                detail={"reason": str(exc), "stderr": self._stderr_tail()},
            ) from exc

        try:
            line = self._lines.get(timeout=max(timeout, 0.01))
        except queue.Empty:
            self._abort()
            raise PluginError(
                code="plugin_timeout",
                message=f"插件调用超时（{timeout:g} 秒）",
                retryable=True,
                status_code=504,
                detail={"timeout_seconds": timeout},
            ) from None

        if line is None:
            exit_code = self._process.poll()
            if exit_code is None:
                try:
                    exit_code = self._process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    exit_code = None
            self._abort()
            raise PluginError(
                code="plugin_crashed",
                message="插件进程在返回响应前退出",
                status_code=502,
                detail={"exit_code": exit_code, "stderr": self._stderr_tail()},
            )
        return line

    def _abort(self) -> None:
        """强制收尾进程，避免僵尸。"""
        self._closed = True
        process = self._process
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def close(self) -> None:
        """关闭通道并回收进程。"""
        self._abort()


class BuiltinTransport:
    """内置插件传输层：把同一份 JSON-RPC 报文交给进程内处理器。

    处理器签名为 ``handler(method, params) -> Any``，返回值作为 result。
    抛出的异常统一转为 plugin_crashed，保持与子进程插件一致的外在行为。
    """

    def __init__(self, handler: Callable[[str, dict[str, Any]], Any]) -> None:
        self._handler = handler
        self._closed = False

    @property
    def alive(self) -> bool:
        """内置插件始终可用（未被关闭时）。"""
        return not self._closed

    def request(self, message: str, timeout: float) -> str:
        """解析请求行并同步返回响应行。"""
        if self._closed:
            raise PluginError(
                code="plugin_not_started",
                message="内置插件已停止",
                status_code=409,
            )
        try:
            payload = json.loads(message)
        except json.JSONDecodeError as exc:
            raise PluginError(
                code="plugin_invalid_request",
                message="内置插件收到的请求不是合法 JSON",
                status_code=500,
                detail={"reason": str(exc)},
            ) from exc
        method = payload.get("method")
        params = payload.get("params") or {}
        if not isinstance(method, str):
            raise PluginError(
                code="plugin_invalid_request",
                message="内置插件收到的请求缺少 method",
                status_code=500,
            )
        if not isinstance(params, dict):
            raise PluginError(
                code="plugin_invalid_request",
                message="内置插件收到的请求 params 必须是对象",
                status_code=500,
            )
        try:
            result = self._handler(method, params)
        except PluginError:
            raise
        except Exception as exc:  # noqa: BLE001 - 内置插件异常统一归一
            raise PluginError(
                code="plugin_crashed",
                message=f"内置插件执行失败：{exc}",
                status_code=502,
                detail={"method": method, "reason": repr(exc)},
            ) from exc
        response = JsonRpcResponse(id=payload.get("id"), result=result)
        return json.dumps(response.to_dict(), ensure_ascii=False)

    def close(self) -> None:
        """标记关闭。"""
        self._closed = True


# ── 已加载插件 ────────────────────────────────────────────────


@dataclass
class PluginLogEntry:
    """一条调用日志。"""

    at: str
    action: str
    ok: bool
    duration_ms: int = 0
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """转为可序列化字典。"""
        payload: dict[str, Any] = {
            "at": self.at,
            "action": self.action,
            "ok": self.ok,
            "duration_ms": self.duration_ms,
        }
        if self.error is not None:
            payload["error"] = self.error
        return payload


@dataclass
class LoadedPlugin:
    """宿主持有的插件实例。"""

    manifest: PluginManifest
    directory: Path | None = None
    enabled: bool = True
    transport: Transport | None = None
    request_seq: int = 0
    logs: list[PluginLogEntry] = field(default_factory=list)

    @property
    def id(self) -> str:
        """插件标识。"""
        return self.manifest.id

    @property
    def running(self) -> bool:
        """是否已启动且通道可用。"""
        return self.transport is not None and self.transport.alive

    @property
    def status(self) -> str:
        """插件状态：disabled / stopped / running。"""
        if not self.enabled:
            return "disabled"
        return "running" if self.running else "stopped"

    def next_request_id(self) -> int:
        """分配请求 id。"""
        self.request_seq += 1
        return self.request_seq

    def log(self, action: str, ok: bool, duration_ms: int = 0, error: dict | None = None) -> None:
        """追加一条日志，超出上限时丢弃最旧记录。"""
        self.logs.append(
            PluginLogEntry(
                at=_now(),
                action=action,
                ok=ok,
                duration_ms=duration_ms,
                error=error,
            )
        )
        if len(self.logs) > LOG_LIMIT:
            del self.logs[: len(self.logs) - LOG_LIMIT]

    def to_dict(self) -> dict[str, Any]:
        """转为接口返回形状。"""
        payload = self.manifest.to_dict()
        payload.update(
            {
                "status": self.status,
                "enabled": self.enabled,
                "builtin": self.manifest.is_builtin,
                "running": self.running,
                "directory": str(self.directory) if self.directory is not None else None,
            }
        )
        return payload


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── 宿主 ──────────────────────────────────────────────────────


class PluginHost:
    """插件宿主。

    参数:
        timeout: 单次调用超时秒数；为 None 时读设置 ``plugin_timeout_seconds``，
            缺省 5 秒。
        transport_factory: 传输层工厂，测试可注入假传输层；签名
            ``(manifest, directory) -> Transport``。
        plugins_root: 插件根目录，缺省 ``app.config.plugins_dir()``。
    """

    def __init__(
        self,
        *,
        timeout: float | None = None,
        transport_factory: Callable[[PluginManifest, Path | None], Transport] | None = None,
        plugins_root: Path | None = None,
    ) -> None:
        self._timeout = timeout
        self._transport_factory = transport_factory
        self._plugins_root = plugins_root
        self._plugins: dict[str, LoadedPlugin] = {}
        self._load_errors: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    # 目录与超时

    def plugins_root(self) -> Path:
        """插件根目录。"""
        if self._plugins_root is not None:
            return self._plugins_root
        from app import config

        return config.plugins_dir()

    def timeout(self) -> float:
        """单次调用超时秒数。"""
        if self._timeout is not None:
            return float(self._timeout)
        raw = store.get_setting(TIMEOUT_SETTING_KEY, DEFAULT_TIMEOUT_SECONDS)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return DEFAULT_TIMEOUT_SECONDS
        return value if value > 0 else DEFAULT_TIMEOUT_SECONDS

    # 状态持久化

    def _read_state(self) -> dict[str, Any]:
        state = store.get_setting(PLUGINS_STATE_KEY, {})
        return state if isinstance(state, dict) else {}

    def _write_state(self, state: dict[str, Any]) -> None:
        store.set_setting(PLUGINS_STATE_KEY, state)

    def _persist_enabled(self, plugin_id: str, enabled: bool) -> None:
        state = self._read_state()
        record = state.get(plugin_id)
        entry = dict(record) if isinstance(record, dict) else {}
        entry["enabled"] = enabled
        entry["updated_at"] = _now()
        state[plugin_id] = entry
        self._write_state(state)

    # 加载

    def load_all(self) -> dict[str, Any]:
        """加载磁盘插件与内置插件，返回加载汇总。"""
        with self._lock:
            self._load_errors = []
            self._load_builtin()
            self._load_from_disk()
            state = self._read_state()
            for plugin in self._plugins.values():
                record = state.get(plugin.id)
                if isinstance(record, dict) and "enabled" in record:
                    plugin.enabled = bool(record["enabled"])
            return {
                "loaded": [plugin.to_dict() for plugin in self._plugins.values()],
                "errors": list(self._load_errors),
            }

    def _load_builtin(self) -> None:
        """装载内置插件清单。"""
        try:
            from .builtin import iter_builtin_manifests
        except ImportError:  # pragma: no cover - builtin 包缺失时不影响第三方插件
            return
        for raw in iter_builtin_manifests():
            self._register_manifest(raw, directory=None, source="builtin")

    def _load_from_disk(self) -> None:
        """扫描插件根目录下每个子目录的 manifest.yaml。"""
        root = self.plugins_root()
        if not root.exists():
            return
        for directory in sorted(entry for entry in root.iterdir() if entry.is_dir()):
            manifest_path = directory / MANIFEST_FILENAME
            if not manifest_path.exists():
                self._load_errors.append(
                    {
                        "path": str(directory),
                        "code": "plugin_manifest_missing",
                        "message": f"插件目录缺少 {MANIFEST_FILENAME}",
                    }
                )
                continue
            validation = load_manifest(manifest_path)
            self._register_manifest(validation, directory=directory, source="disk")

    def _register_manifest(
        self,
        validation: ManifestValidation | dict[str, Any],
        *,
        directory: Path | None,
        source: str,
    ) -> None:
        """校验并登记单个清单。"""
        if isinstance(validation, dict):
            validation = validate_manifest(validation)
        manifest = validation.manifest
        if not validation.ok or manifest is None:
            self._load_errors.append(
                {
                    "path": str(directory) if directory is not None else source,
                    "code": "plugin_manifest_invalid",
                    "message": "插件清单校验失败",
                    "issues": [issue.to_dict() for issue in validation.issues],
                }
            )
            return

        if not range_matches(manifest.api_version, HOST_API_VERSION):
            self._load_errors.append(
                {
                    "path": str(directory) if directory is not None else source,
                    "plugin_id": manifest.id,
                    "code": "plugin_api_version_unsupported",
                    "message": (
                        f"插件 apiVersion {manifest.api_version} 与主程序支持的区间 "
                        f"{HOST_API_VERSION} 不兼容"
                    ),
                    "issues": [
                        {
                            "field": "apiVersion",
                            "code": "api_version_unsupported",
                            "message": f"主程序支持 1.x 区间，当前声明 {manifest.api_version}",
                        }
                    ],
                }
            )
            return

        if manifest.id in self._plugins:
            self._load_errors.append(
                {
                    "path": str(directory) if directory is not None else source,
                    "plugin_id": manifest.id,
                    "code": "plugin_duplicate_id",
                    "message": f"插件 id {manifest.id} 重复，已忽略后出现的一份",
                }
            )
            return

        self._plugins[manifest.id] = LoadedPlugin(manifest=manifest, directory=directory)

    # 查询

    def list_plugins(self) -> list[dict[str, Any]]:
        """列出已加载插件。"""
        with self._lock:
            return [plugin.to_dict() for plugin in self._plugins.values()]

    def get_plugin(self, plugin_id: str) -> dict[str, Any] | None:
        """读取插件详情，不存在返回 None。"""
        with self._lock:
            plugin = self._plugins.get(plugin_id)
            return plugin.to_dict() if plugin is not None else None

    def require_plugin(self, plugin_id: str) -> LoadedPlugin:
        """取插件实例，不存在时抛 plugin_not_found。"""
        plugin = self._plugins.get(plugin_id)
        if plugin is None:
            raise PluginError(
                code="plugin_not_found",
                message=f"插件不存在：{plugin_id}",
                status_code=404,
            )
        return plugin

    def load_errors(self) -> list[dict[str, Any]]:
        """返回最近一次加载的错误清单。"""
        return list(self._load_errors)

    def logs(self, plugin_id: str) -> list[dict[str, Any]]:
        """读取调用日志。"""
        with self._lock:
            plugin = self.require_plugin(plugin_id)
            return [entry.to_dict() for entry in plugin.logs]

    # 启停

    def enable(self, plugin_id: str) -> dict[str, Any]:
        """启用插件并持久化状态。"""
        with self._lock:
            plugin = self.require_plugin(plugin_id)
            plugin.enabled = True
            self._persist_enabled(plugin_id, True)
            plugin.log("enable", True)
            return plugin.to_dict()

    def disable(self, plugin_id: str) -> dict[str, Any]:
        """停用插件、停止其进程并持久化状态。"""
        with self._lock:
            plugin = self.require_plugin(plugin_id)
            plugin.enabled = False
            self._stop(plugin)
            self._persist_enabled(plugin_id, False)
            plugin.log("disable", True)
            return plugin.to_dict()

    # 进程管理

    def start(self, plugin_id: str) -> dict[str, Any]:
        """启动插件进程（内置插件等价于打开进程内通道）。"""
        with self._lock:
            plugin = self.require_plugin(plugin_id)
            if not plugin.enabled:
                raise PluginError(
                    code="plugin_disabled",
                    message=f"插件已停用：{plugin_id}",
                    status_code=409,
                )
            self._ensure_transport(plugin)
            return plugin.to_dict()

    def stop(self, plugin_id: str) -> dict[str, Any]:
        """停止插件进程。"""
        with self._lock:
            plugin = self.require_plugin(plugin_id)
            self._stop(plugin)
            plugin.log("stop", True)
            return plugin.to_dict()

    def stop_all(self) -> None:
        """停止全部插件进程。"""
        with self._lock:
            for plugin in self._plugins.values():
                self._stop(plugin)

    def _stop(self, plugin: LoadedPlugin) -> None:
        """关闭传输层并清空句柄。"""
        transport = plugin.transport
        plugin.transport = None
        if transport is None:
            return
        try:
            transport.close()
        except Exception:  # noqa: BLE001 - 关闭失败不影响宿主
            pass

    def _ensure_transport(self, plugin: LoadedPlugin) -> Transport:
        """按需创建传输层。"""
        if plugin.transport is not None and plugin.transport.alive:
            return plugin.transport
        self._stop(plugin)
        if self._transport_factory is not None:
            transport = self._transport_factory(plugin.manifest, plugin.directory)
        elif plugin.manifest.is_builtin:
            transport = self._create_builtin_transport(plugin)
        else:
            transport = self._create_stdio_transport(plugin)
        plugin.transport = transport
        plugin.log("start", True)
        return transport

    def _create_builtin_transport(self, plugin: LoadedPlugin) -> Transport:
        """构造内置插件传输层。"""
        from .builtin import get_builtin_handler

        try:
            handler = get_builtin_handler(plugin.id)
        except KeyError as exc:
            raise PluginError(
                code="plugin_entry_not_found",
                message=f"内置插件缺少处理器：{plugin.id}",
                status_code=500,
            ) from exc
        transport = BuiltinTransport(handler)
        # 内置插件在进程内调用，无法中断，超时由各处理器自行控制。
        return transport

    def _create_stdio_transport(self, plugin: LoadedPlugin) -> Transport:
        """构造子进程传输层。"""
        command = list(plugin.manifest.entry_command) + list(plugin.manifest.entry_args)
        if not command:
            raise PluginError(
                code="plugin_entry_missing",
                message=f"插件缺少入口命令：{plugin.id}",
                status_code=500,
            )
        if command[0] == BUILTIN_COMMAND:
            raise PluginError(
                code="plugin_entry_invalid",
                message=f"内置插件 id 未注册处理器：{plugin.id}",
                status_code=500,
            )
        return StdioTransport(command, cwd=plugin.directory)

    # 调用

    def request(
        self,
        plugin_id: str,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        """向插件发送一次 JSON-RPC 请求并返回 result。

        插件返回错误对象、超时或崩溃时抛 PluginError。
        """
        with self._lock:
            plugin = self.require_plugin(plugin_id)
            if not plugin.enabled:
                raise PluginError(
                    code="plugin_disabled",
                    message=f"插件已停用：{plugin_id}",
                    status_code=409,
                )
            transport = self._ensure_transport(plugin)
            request_id = plugin.next_request_id()
            message = encode_request(request_id, method, params)
            started = time.monotonic()
            try:
                raw = transport.request(message, timeout if timeout is not None else self.timeout())
            except PluginError as exc:
                plugin.log(
                    method,
                    False,
                    _elapsed_ms(started),
                    {"code": exc.code, "message": exc.message},
                )
                raise
            duration = _elapsed_ms(started)
            parsed = parse_response(raw)
            response = select_response(parsed, request_id)
            if not response.ok and response.error is not None:
                error = response.error
                plugin.log(
                    method,
                    False,
                    duration,
                    {"code": "plugin_error", "message": str(error.get("message"))},
                )
                raise PluginError(
                    code="plugin_call_failed",
                    message=f"插件返回错误：{error.get('message')}",
                    retryable=False,
                    status_code=502,
                    detail={"plugin_error": error},
                )
            plugin.log(method, True, duration)
            return response.result

    def invoke(
        self,
        plugin_id: str,
        hook: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """调用指定插件的某个 hook，做输入输出形状校验。"""
        if hook not in HOOK_CONTRACTS:
            raise PluginError(
                code="plugin_hook_unknown",
                message=f"未知 hook：{hook}",
                status_code=404,
                detail={"hooks": list(HOOK_NAMES)},
            )
        payload = params or {}
        issues = validate_hook_input(hook, payload)
        if issues:
            raise PluginError(
                code="plugin_payload_invalid",
                message="hook 输入不符合契约",
                status_code=400,
                detail={"issues": issues},
            )
        started = time.monotonic()
        result = self.request(plugin_id, hook, payload)
        issues_out = validate_hook_output(hook, result)
        if issues_out:
            raise PluginError(
                code="plugin_output_invalid",
                message="插件输出不符合契约",
                status_code=502,
                detail={"issues": issues_out},
            )
        return {
            "plugin_id": plugin_id,
            "hook": hook,
            "ok": True,
            "duration_ms": _elapsed_ms(started),
            "result": result,
        }

    def dispatch(
        self,
        hook: str,
        params: dict[str, Any] | None = None,
        *,
        plugin_ids: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """向全部启用插件派发 hook，任何插件失败都不影响其他插件与主流程。

        参数:
            hook: hook 名，必须是六个之一。
            params: hook 输入。
            plugin_ids: 限定参与的插件，缺省为全部启用插件。

        返回:
            ``{"hook", "ok", "input_issues", "results", "duration_ms"}``；
            ``results`` 每项含 plugin_id、ok、result 或 error。
        """
        started = time.monotonic()
        if hook not in HOOK_CONTRACTS:
            return {
                "hook": hook,
                "ok": False,
                "input_issues": [f"未知 hook：{hook}"],
                "results": [],
                "duration_ms": 0,
            }
        payload = params or {}
        issues = validate_hook_input(hook, payload)
        if issues:
            return {
                "hook": hook,
                "ok": False,
                "input_issues": issues,
                "results": [],
                "duration_ms": _elapsed_ms(started),
            }

        with self._lock:
            targets = (
                [self._plugins[pid] for pid in plugin_ids if pid in self._plugins]
                if plugin_ids is not None
                else list(self._plugins.values())
            )
            targets = [plugin for plugin in targets if plugin.enabled]

        results: list[dict[str, Any]] = []
        for plugin in targets:
            try:
                outcome = self.invoke(plugin.id, hook, payload)
                results.append(
                    {
                        "plugin_id": plugin.id,
                        "ok": True,
                        "result": outcome["result"],
                        "duration_ms": outcome["duration_ms"],
                    }
                )
            except PluginError as exc:
                results.append(
                    {
                        "plugin_id": plugin.id,
                        "ok": False,
                        "error": {
                            "code": exc.code,
                            "message": exc.message,
                            "retryable": exc.retryable,
                        },
                    }
                )
            except Exception as exc:  # noqa: BLE001 - 派发不可上抛
                results.append(
                    {
                        "plugin_id": plugin.id,
                        "ok": False,
                        "error": {
                            "code": "plugin_internal_error",
                            "message": str(exc),
                            "retryable": False,
                        },
                    }
                )
        return {
            "hook": hook,
            "ok": True,
            "input_issues": [],
            "results": results,
            "duration_ms": _elapsed_ms(started),
        }


def _elapsed_ms(started: float) -> int:
    """计算毫秒耗时。"""
    return int(round((time.monotonic() - started) * 1000))


# ── 全局宿主 ──────────────────────────────────────────────────

_HOST: PluginHost | None = None
_HOST_LOCK = threading.Lock()


def get_host() -> PluginHost:
    """获取全局宿主，首次访问时加载插件。"""
    global _HOST
    with _HOST_LOCK:
        if _HOST is None:
            host = PluginHost()
            host.load_all()
            _HOST = host
        return _HOST


def reset_host() -> None:
    """停止并丢弃全局宿主，供测试与重载使用。"""
    global _HOST
    with _HOST_LOCK:
        if _HOST is not None:
            _HOST.stop_all()
        _HOST = None


def iter_hook_names() -> Iterator[str]:
    """依次产出六个 hook 名。"""
    return iter(HOOK_NAMES)
