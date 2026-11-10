"""后端应用工厂。

路由按 ROUTER_SPECS 动态装载：尚未产出的任务模块被跳过，
使主干在并行施工期间始终可启动。
"""

from __future__ import annotations

import importlib
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from .errors import ApiError, error_response
from .logging_config import get_logger, setup_logging

logger = get_logger("app.main")

ROUTER_SPECS: list[tuple[str, str]] = [
    ("cards", "/v1/cards"),
    ("worldbooks", "/v1/world-books"),
    ("presets", "/v1/presets"),
    ("sessions", "/v1/sessions"),
    ("chat", "/v1"),
    ("preview", "/v1"),
    ("models", "/v1"),
    ("profiles", "/v1/profiles"),
    ("memory", "/v1/memory"),
    ("state", "/v1/state"),
    ("plugins", "/v1/plugins"),
    ("settings", "/v1/settings"),
    ("voices", "/v1/voices"),
]

# 集合端点：列表与创建都挂在这几个前缀下。
# 契约文档写的是不带尾斜杠的写法，而前端产物静态挂在 "/" 上会先于路由命中，
# Starlette 自带的斜杠重定向因此失效，不带斜杠的请求会落到静态目录上变成 404。
# 这里在路由之前补斜杠，让契约里的写法和实现对得上。
COLLECTION_PREFIXES = frozenset(
    {
        "/v1/cards",
        "/v1/memory",
        "/v1/plugins",
        "/v1/presets",
        "/v1/sessions",
        "/v1/settings",
        "/v1/state",
        "/v1/voices",
        "/v1/world-books",
    }
)


class CollectionSlashAlias:
    """集合前缀补尾斜杠，等价于 Starlette 自带的斜杠重定向。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """只改写 http 请求的 path，其余作用域原样传下去。"""
        if scope.get("type") == "http":
            path = scope.get("path", "")
            if path in COLLECTION_PREFIXES:
                scope = {**scope, "path": path + "/"}
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from . import store
    from .presets import ensure_builtin_preset

    setup_logging()
    ensure_builtin_preset()
    store.cards_dir()
    store.prune_empty_card_dirs()
    store.world_books_dir()
    store.presets_dir()
    store.profiles_dir()
    # 推理服务是独立进程，可能自己退出；保活线程盯着，掉了拉回来。
    from .inference import keepalive, launcher

    # 后端重启时推理服务往往还活着（它是脱离后端的独立进程），
    # 先按端口认领回来：权重不用重载，档位状态也跟实际对得上。
    launcher.adopt_existing()
    keepalive.start()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="X-WORLD Backend", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # 放在 CORS 之后：补斜杠只影响路由匹配，不该改写跨域头的判定
    app.add_middleware(CollectionSlashAlias)

    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc)

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        return {"ok": True, "service": "xworld-backend"}

    for module_name, prefix in ROUTER_SPECS:
        try:
            module = importlib.import_module(f"app.routers.{module_name}")
        except ModuleNotFoundError as exc:
            logger.warning("路由 %s 未装载：%s", module_name, exc)
            continue
        router = getattr(module, "router", None)
        if router is not None:
            app.include_router(router, prefix=prefix)

    _mount_frontend(app)
    return app


def _frontend_dist() -> Path:
    """定位前端构建产物。

    源码运行时为 <工程根>/frontend/dist；打包运行时为 PyInstaller 解包目录
    sys._MEIPASS/frontend/dist。两者都取不到时返回源码布局路径，由调用方判断。
    """
    here = Path(__file__).resolve()
    candidates = [here.parent.parent.parent / "frontend" / "dist"]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "frontend" / "dist")
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def _mount_frontend(app: FastAPI) -> None:
    """前端为浏览器形态，产物存在时由后端进程一并托管。"""
    dist = _frontend_dist()
    if not dist.is_dir():
        logger.warning("未找到前端构建产物：%s", dist)
        return
    app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")


app = create_app()
