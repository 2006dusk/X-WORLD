"""本机路径解析。

模型权重、编译依赖与模型库目录这些机器相关的路径写在仓库根的 local.paths.json，
该文件不入库，格式见同目录的 local.paths.example.json。
解析顺序：环境变量、local.paths.json、缺省值。

模型文件用「目录键 + 相对路径」表示，前缀对应一个目录键：
    hf/ArityFlow-Qwen3-4B-Instruct-2507-RolePlay.Q5_K_M.gguf
    ms/lumi_q4.gguf
    local/qwen3.5-9b/Qwen3.5-9B-UD-Q4_K_XL.gguf
"""

from __future__ import annotations

import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_PATHS_ENV = "XWORLD_LOCAL_PATHS"
LOCAL_PATHS_FILE = "local.paths.json"

# 模型文件相对路径的前缀与目录键的对应关系。
MODEL_DIR_KEYS = {
    "hf": "models_hf_dir",
    "ms": "models_ms_dir",
    "local": "models_local_dir",
}


def local_paths_file() -> Path | None:
    """给出 local.paths.json 的位置，文件不存在时给 None。"""
    override = os.environ.get(LOCAL_PATHS_ENV, "").strip()
    if override:
        path = Path(override).expanduser()
        return path if path.is_file() else None
    candidate = REPO_ROOT / LOCAL_PATHS_FILE
    return candidate if candidate.is_file() else None


def _entries() -> dict[str, str]:
    """读出 local.paths.json 里的键值，只留字符串。"""
    path = local_paths_file()
    if path is None:
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, str)}


def local_path(key: str, env_var: str | None = None, default: str = "") -> str:
    """取一个本机路径：环境变量优先，再看 local.paths.json。"""
    if env_var:
        value = os.environ.get(env_var, "").strip()
        if value:
            return value
    return _entries().get(key, "").strip() or default


def model_file(relative: str) -> str:
    """把模型库里的相对路径拼成绝对路径。

    relative 形如 hf/xxx.gguf、local/qwen3.5-9b/xxx.gguf。
    目录键没配时给空串。
    """
    parts = [p for p in relative.replace("\\", "/").split("/") if p]
    if not parts:
        return ""
    key = MODEL_DIR_KEYS.get(parts[0])
    if key is None:
        return ""
    base = local_path(key)
    if not base:
        return ""
    return str(Path(base).joinpath(*parts[1:]))
