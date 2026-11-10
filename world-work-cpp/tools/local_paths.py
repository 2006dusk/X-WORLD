"""Resolve machine-local paths for the analysis probes.

Order: environment variable, then local.paths.json at the repository root,
then an error naming the key to set. The repository carries no machine path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

# 模型文件相对路径的前缀与目录键的对应关系。
MODEL_DIR_KEYS = {
    "hf": "models_hf_dir",
    "ms": "models_ms_dir",
    "local": "models_local_dir",
}


def _local_json() -> Path:
    """给出 local.paths.json 的位置，可用 XWORLD_LOCAL_PATHS 覆盖。"""
    override = os.environ.get("XWORLD_LOCAL_PATHS", "").strip()
    if override:
        return Path(override).expanduser()
    return _ROOT / "local.paths.json"


def _from_file(key: str) -> str:
    """从 local.paths.json 取一个键的值，文件缺失或格式不对时给空串。"""
    path = _local_json()
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    value = data.get(key, "")
    return value if isinstance(value, str) else ""


def resolve(key: str, env_var: str) -> str:
    """取一个本机路径：环境变量优先，再看 local.paths.json。"""
    value = os.environ.get(env_var, "").strip() or _from_file(key)
    if not value:
        raise SystemExit(
            "%s is not set: export %s, or put %r in %s"
            % (key, env_var, key, _local_json())
        )
    return value


def model_file(relative: str) -> str:
    """拼出模型文件的绝对路径，前缀 hf、ms、local 各自对应一个目录键。"""
    parts = [p for p in relative.replace("\\", "/").split("/") if p]
    if not parts:
        return ""
    key = MODEL_DIR_KEYS.get(parts[0])
    if key is None:
        return ""
    base = _from_file(key)
    if not base:
        return ""
    return str(Path(base).joinpath(*parts[1:]))


def model_path() -> str:
    """探针默认用的权重路径，可用 XWORLD_MODEL 覆盖。"""
    override = os.environ.get("XWORLD_MODEL", "").strip()
    if override:
        return override
    path = model_file("local/qwen3.5-9b/Qwen3.5-9B-UD-Q4_K_XL.gguf")
    if not path:
        raise SystemExit(
            "model path is not set: export XWORLD_MODEL, or put models_local_dir in %s"
            % _local_json()
        )
    return path
