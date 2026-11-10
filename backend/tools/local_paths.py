"""本机路径解析（工具脚本用）。

转发到 app.paths，工具脚本与后端共用同一份配置与解析规则。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.paths import local_path, model_file  # noqa: E402,F401
