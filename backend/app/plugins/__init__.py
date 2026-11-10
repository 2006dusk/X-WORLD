"""插件子系统。

- ``manifest``：YAML 清单解析与校验、apiVersion 区间匹配。
- ``host``：插件宿主，进程管理与 JSON-RPC 2.0 派发。
- ``builtin``：内置正则与世界书插件。
"""

from __future__ import annotations

from typing import Any

from .builtin import REGEX_PLUGIN_ID, WORLDBOOK_PLUGIN_ID, builtin_plugin_ids
from .host import (
    DEFAULT_TIMEOUT_SECONDS,
    HOOK_CONTRACTS,
    HOOK_NAMES,
    JSONRPC_VERSION,
    PLUGINS_STATE_KEY,
    BuiltinTransport,
    HookContract,
    JsonRpcResponse,
    LoadedPlugin,
    PluginError,
    PluginHost,
    StdioTransport,
    Transport,
    build_request,
    encode_request,
    get_host,
    list_hooks,
    parse_response,
    reset_host,
    select_response,
    validate_hook_input,
    validate_hook_output,
)
from .manifest import (
    ALLOWED_CAPABILITIES,
    HOST_API_RANGE,
    HOST_API_VERSION,
    MANIFEST_FILENAME,
    ManifestIssue,
    ManifestValidation,
    PluginManifest,
    is_plugin_id,
    is_semver,
    load_manifest,
    parse_api_range,
    parse_manifest_text,
    parse_version,
    range_matches,
    validate_manifest,
)

BUILTIN_PLUGIN_IDS: tuple[str, ...] = builtin_plugin_ids()


def invoke(
    hook: str,
    params: dict[str, Any],
    plugin_ids: list[str] | None = None,
) -> dict[str, Any]:
    """向全局宿主派发 hook 的便捷封装，失败不影响调用方。"""
    return get_host().dispatch(hook, params, plugin_ids=plugin_ids)


__all__ = [
    "ALLOWED_CAPABILITIES",
    "BUILTIN_PLUGIN_IDS",
    "BuiltinTransport",
    "DEFAULT_TIMEOUT_SECONDS",
    "HOOK_CONTRACTS",
    "HOOK_NAMES",
    "HOST_API_RANGE",
    "HOST_API_VERSION",
    "HookContract",
    "JSONRPC_VERSION",
    "JsonRpcResponse",
    "LoadedPlugin",
    "MANIFEST_FILENAME",
    "ManifestIssue",
    "ManifestValidation",
    "PLUGINS_STATE_KEY",
    "PluginError",
    "PluginHost",
    "PluginManifest",
    "REGEX_PLUGIN_ID",
    "StdioTransport",
    "Transport",
    "WORLDBOOK_PLUGIN_ID",
    "build_request",
    "builtin_plugin_ids",
    "encode_request",
    "get_host",
    "invoke",
    "is_plugin_id",
    "is_semver",
    "list_hooks",
    "load_manifest",
    "parse_api_range",
    "parse_manifest_text",
    "parse_response",
    "parse_version",
    "range_matches",
    "reset_host",
    "select_response",
    "validate_hook_input",
    "validate_hook_output",
    "validate_manifest",
]
