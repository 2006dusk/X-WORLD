"""内置插件包。

内置正则与世界书插件以同格式清单注册，宿主按 id 取处理器，
与第三方插件共用一套 JSON-RPC 与 hook 派发接口，不另开特例通道。
"""

from __future__ import annotations

from typing import Any, Callable, Iterator

from . import regex_plugin, worldbook_plugin

REGEX_PLUGIN_ID = "builtin-regex"
WORLDBOOK_PLUGIN_ID = "builtin-worldbook"

BUILTIN_MANIFESTS: tuple[dict[str, Any], ...] = (
    {
        "id": REGEX_PLUGIN_ID,
        "name": "内置正则插件",
        "version": "1.0.0",
        "apiVersion": ">=1.0.0 <2.0.0",
        "capabilities": ["regex"],
        "entry": {"command": ["builtin"], "args": ["regex_plugin"]},
    },
    {
        "id": WORLDBOOK_PLUGIN_ID,
        "name": "内置世界书插件",
        "version": "1.0.0",
        "apiVersion": ">=1.0.0 <2.0.0",
        "capabilities": ["worldbook"],
        "entry": {"command": ["builtin"], "args": ["worldbook_plugin"]},
    },
)

_HANDLERS: dict[str, Callable[[str, dict[str, Any]], Any]] = {
    REGEX_PLUGIN_ID: regex_plugin.handle,
    WORLDBOOK_PLUGIN_ID: worldbook_plugin.handle,
}


def iter_builtin_manifests() -> Iterator[dict[str, Any]]:
    """依次产出内置插件清单的副本。"""
    for manifest in BUILTIN_MANIFESTS:
        yield {
            **manifest,
            "capabilities": list(manifest["capabilities"]),
            "entry": {
                "command": list(manifest["entry"]["command"]),
                "args": list(manifest["entry"]["args"]),
            },
        }


def builtin_plugin_ids() -> tuple[str, ...]:
    """内置插件 id 列表。"""
    return tuple(_HANDLERS)


def get_builtin_handler(plugin_id: str) -> Callable[[str, dict[str, Any]], Any]:
    """取内置插件处理器，未知 id 抛 KeyError。"""
    return _HANDLERS[plugin_id]
