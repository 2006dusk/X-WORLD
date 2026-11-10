"""插件 HTTP 路由，挂载于 /v1/plugins 前缀。

端点：

- ``GET /`` 列出已加载插件
- ``GET /hooks`` 列出六个 hook 及输入输出结构说明
- ``POST /reload`` 重新扫描插件目录
- ``GET /{plugin_id}`` 插件详情
- ``POST /{plugin_id}/enable`` 启用
- ``POST /{plugin_id}/disable`` 停用
- ``GET /{plugin_id}/log`` 调用日志
- ``POST /{plugin_id}/invoke`` 手动调用某 hook
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.errors import ApiError
from app.plugins.host import (
    HOOK_NAMES,
    PluginHost,
    get_host,
    list_hooks,
    reset_host,
)

router = APIRouter(tags=["plugins"])


class InvokeBody(BaseModel):
    """手动调用 hook 的请求体。"""

    hook: str
    params: dict[str, Any] = Field(default_factory=dict)


def _host() -> PluginHost:
    """取全局宿主。"""
    return get_host()


@router.get("/")
def list_plugins() -> JSONResponse:
    """列出已加载插件及其状态。"""
    host = _host()
    return JSONResponse(
        content={
            "items": host.list_plugins(),
            "hooks": list(HOOK_NAMES),
            "errors": host.load_errors(),
        }
    )


@router.get("/hooks")
def get_hooks() -> JSONResponse:
    """列出六个 hook 及其输入输出结构说明。"""
    return JSONResponse(content={"items": list_hooks()})


@router.post("/reload")
def reload_plugins() -> JSONResponse:
    """重新扫描插件目录并装载。"""
    reset_host()
    host = _host()
    return JSONResponse(content={"items": host.list_plugins(), "errors": host.load_errors()})


@router.get("/{plugin_id}")
def get_plugin(plugin_id: str) -> JSONResponse:
    """读取插件详情。"""
    plugin = _host().get_plugin(plugin_id)
    if plugin is None:
        raise ApiError(
            code="plugin_not_found",
            message=f"插件不存在：{plugin_id}",
            status_code=404,
        )
    return JSONResponse(content=plugin)


@router.post("/{plugin_id}/enable")
def enable_plugin(plugin_id: str) -> JSONResponse:
    """启用插件。"""
    return JSONResponse(content=_host().enable(plugin_id))


@router.post("/{plugin_id}/disable")
def disable_plugin(plugin_id: str) -> JSONResponse:
    """停用插件。"""
    return JSONResponse(content=_host().disable(plugin_id))


@router.get("/{plugin_id}/log")
def get_plugin_log(plugin_id: str, limit: int = 50) -> JSONResponse:
    """读取插件调用日志，返回最近 limit 条。"""
    logs = _host().logs(plugin_id)
    if limit > 0:
        logs = logs[-limit:]
    return JSONResponse(content={"plugin_id": plugin_id, "items": logs})


@router.post("/{plugin_id}/invoke")
def invoke_plugin(plugin_id: str, body: InvokeBody) -> JSONResponse:
    """手动调用插件的某个 hook，用于调试。"""
    outcome = _host().invoke(plugin_id, body.hook, body.params)
    return JSONResponse(content=outcome)
