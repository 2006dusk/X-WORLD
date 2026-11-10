"""打包产物启动入口。

源码运行与 PyInstaller 冻结运行共用同一入口：
先执行首启引导，再拉起 uvicorn 服务，托管前端构建产物。
"""

from __future__ import annotations

import sys
from pathlib import Path


def _backend_root() -> Path:
    """源码运行时的后端根目录。"""
    return Path(__file__).resolve().parent.parent


def _prepare_sys_path() -> None:
    """源码运行时把后端根目录加入模块搜索路径。"""
    root = _backend_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def main(argv: list[str] | None = None) -> int:
    """启动本地后端服务。"""
    args = list(sys.argv[1:] if argv is None else argv)
    _prepare_sys_path()

    import uvicorn

    from app import config
    from app.main import app
    from app.onboarding import bootstrap

    report = bootstrap()
    print(f"[X-WORLD] 数据目录：{report['data_dir']}（{report['status']}）")
    if report["settings_applied"]:
        print("[X-WORLD] 已补齐设置：" + "、".join(report["settings_applied"]))
    for step in report["steps"]:
        print(f"[X-WORLD] 引导步骤 {step['id']}：{step['detail']}")

    host = args[0] if len(args) > 0 else config.WEB_HOST
    port = int(args[1]) if len(args) > 1 else config.DEFAULT_PORT
    print(f"[X-WORLD] 服务监听 http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
