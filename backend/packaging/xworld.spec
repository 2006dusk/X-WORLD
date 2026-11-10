# -*- mode: python ; coding: utf-8 -*-
r"""X-WORLD 打包规格。

用法：
    cd <仓库根>/backend
    python -m PyInstaller --clean --noconfirm packaging\xworld.spec

产物目录：backend\dist\xworld\
前端构建产物 frontend/dist 随包分发，由后端进程在根路径托管。
"""

from pathlib import Path

BASE = Path(SPECPATH).resolve()
BACKEND = BASE.parent
PROJECT = BACKEND.parent
FRONTEND_DIST = PROJECT / "frontend" / "dist"

if not FRONTEND_DIST.is_dir():
    raise SystemExit(
        "frontend/dist 不存在。请先在 <仓库根>/frontend "
        "执行 npm run build，再重新打包。"
    )

datas = [(str(FRONTEND_DIST), "frontend/dist")]

hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "yaml",
    # 路由由 main.py 动态装载，静态分析抓不到，必须逐个列出
    "app.main",
    "app.onboarding",
    "app.store",
    "app.config",
    "app.errors",
    "app.contracts",
    "app.presets",
    "app.worldbooks",
    "app.routers.cards",
    "app.routers.worldbooks",
    "app.routers.presets",
    "app.routers.sessions",
    "app.routers.chat",
    "app.routers.preview",
    "app.routers.models",
    "app.routers.profiles",
    "app.routers.memory",
    "app.routers.plugins",
    "app.routers.settings",
    "app.cards.loader",
    "app.cards.spec",
    "app.assembly.pipeline",
    "app.assembly.budget",
    "app.assembly.matching",
    "app.assembly.tokenizer",
    "app.assembly.templates",
    "app.inference.base",
    "app.inference.llamacpp",
    "app.inference.openai_compat",
    "app.memory.store",
    "app.memory.retrieval",
    "app.memory.extract",
    "app.memory.embedder",
    "app.index.builder",
    "app.index.persistence",
    "app.plugins.host",
    "app.plugins.manifest",
    "app.plugins.builtin",
    "app.plugins.builtin.regex_plugin",
    "app.plugins.builtin.worldbook_plugin",
]

a = Analysis(
    [str(BASE / "launcher.py")],
    pathex=[str(BACKEND)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "IPython", "notebook", "sqlite3"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="xworld",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="xworld",
)
