"""首次启动引导。

本模块只提供与接口层无关的纯函数与薄封装：
目录状态判定、默认设置补齐、引导步骤生成。
数据读写一律经 app.store，本模块不直接改写数据目录中的数据文件。
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.presets import BUILTIN_PRESET_ID

# 目录状态取值
STATUS_OK = "ok"
STATUS_MISSING = "missing"
STATUS_EMPTY = "empty"
STATUS_NOT_WRITABLE = "not_writable"

# 首次启动必须存在的设置键
REQUIRED_SETTING_KEYS: tuple[str, ...] = (
    "inference_url",
    "active_model",
    "active_profile",
    "default_character_id",
    "default_preset_id",
    "memory_mode",
    "max_context_tokens",
    "reserved_output_tokens",
    "bm25_weight",
    "vector_weight",
    "audit_enabled",
    "theme",
)


@dataclass(frozen=True)
class DataDirStatus:
    """数据目录状态。"""

    path: Path
    status: str
    exists: bool
    writable: bool
    file_count: int

    @property
    def ok(self) -> bool:
        """目录是否可直接使用。"""
        return self.status == STATUS_OK

    @property
    def first_run(self) -> bool:
        """是否属于首次运行（目录缺失或为空）。"""
        return self.status in (STATUS_MISSING, STATUS_EMPTY)


def default_settings() -> dict[str, Any]:
    """默认设置。键集合与 REQUIRED_SETTING_KEYS 一致。"""
    return {
        "inference_url": "http://127.0.0.1:8080",
        "active_model": "xw-local-default",
        "active_profile": "p_default",
        "default_character_id": "",
        "default_preset_id": BUILTIN_PRESET_ID,
        "memory_mode": "hybrid",
        "max_context_tokens": 16384,
        "reserved_output_tokens": 1024,
        "bm25_weight": 0.6,
        "vector_weight": 0.4,
        "audit_enabled": False,
        "theme": "dark",
    }


def _probe_writable(path: Path) -> bool:
    """在目录内创建临时文件探测可写性。"""
    try:
        with tempfile.NamedTemporaryFile(dir=str(path), prefix=".xw_probe_", delete=True):
            return True
    except OSError:
        return False


def check_data_dir(path: Path | str) -> DataDirStatus:
    """判定数据目录状态。

    目录不存在记为 missing；存在但为空记为 empty；存在且不可写记为 not_writable；
    存在、可写且非空记为 ok。路径指向文件时按 not_writable 处理。
    """
    target = Path(path)
    if not target.exists():
        # 最近的已存在祖先若是文件，该目录永远无法创建，按不可写处理
        ancestor = target.parent
        while not ancestor.exists() and ancestor != ancestor.parent:
            ancestor = ancestor.parent
        if ancestor.exists() and not ancestor.is_dir():
            return DataDirStatus(target, STATUS_NOT_WRITABLE, False, False, 0)
        return DataDirStatus(target, STATUS_MISSING, False, False, 0)
    if not target.is_dir():
        return DataDirStatus(target, STATUS_NOT_WRITABLE, True, False, 0)
    if not _probe_writable(target):
        return DataDirStatus(target, STATUS_NOT_WRITABLE, True, False, 0)
    count = sum(1 for entry in target.iterdir())
    status = STATUS_OK if count > 0 else STATUS_EMPTY
    return DataDirStatus(target, status, True, True, count)


def apply_defaults(
    settings: dict[str, Any] | None,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """补齐缺失的设置键。

    只写入不存在的键，既有值一律保留。返回新的字典，不修改入参。
    """
    merged: dict[str, Any] = dict(settings or {})
    source = defaults if defaults is not None else default_settings()
    for key, value in source.items():
        if key not in merged:
            merged[key] = value
    return merged


def missing_keys(settings: dict[str, Any] | None) -> list[str]:
    """列出缺失的必需设置键。"""
    current = settings or {}
    return [key for key in REQUIRED_SETTING_KEYS if key not in current]


def plan(
    status: DataDirStatus,
    settings: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """按当前状态生成引导步骤。

    步骤随目录状态与设置完整度变化，已满足的条件不重复出现。
    """
    steps: list[dict[str, str]] = []
    if status.status == STATUS_MISSING:
        steps.append(
            {
                "id": "create_data_dir",
                "title": "创建数据目录",
                "detail": f"数据目录不存在，启动时自动创建 {status.path}。",
            }
        )
    elif status.status == STATUS_NOT_WRITABLE:
        steps.append(
            {
                "id": "fix_permission",
                "title": "修复数据目录权限",
                "detail": f"数据目录不可写，请检查 {status.path} 的权限或占用。",
            }
        )
    elif status.status == STATUS_EMPTY:
        steps.append(
            {
                "id": "init_data",
                "title": "初始化本地数据",
                "detail": "数据目录为空，将写入默认设置并建立各资源子目录。",
            }
        )

    gaps = missing_keys(settings)
    if gaps:
        steps.append(
            {
                "id": "fill_settings",
                "title": "补齐默认设置",
                "detail": "缺少设置项：" + "、".join(gaps),
            }
        )

    inference_url = str((settings or {}).get("inference_url") or "")
    if not inference_url:
        steps.append(
            {
                "id": "set_inference_url",
                "title": "配置推理服务地址",
                "detail": "在设置中填写本地 llama.cpp 兼容服务地址，默认 127.0.0.1:8080。",
            }
        )

    steps.append(
        {
            "id": "check_models",
            "title": "检查本地模型",
            "detail": "确认模型目录下已有 gguf 权重，否则先下载再启动推理服务。",
        }
    )
    return steps


def bootstrap() -> dict[str, Any]:
    """启动前准备。

    建目录、补默认设置，返回引导报告。数据读写经 app.store，本模块不直接改写数据文件。
    """
    from app import store
    from app.presets import ensure_builtin_preset

    ensure_builtin_preset()

    status = check_data_dir(store.config.data_root())
    if status.status == STATUS_MISSING:
        store.config.data_root().mkdir(parents=True, exist_ok=True)
        status = check_data_dir(store.config.data_root())

    current = store.read_json(store.settings_path(), {})
    if not isinstance(current, dict):
        current = {}
    merged = apply_defaults(current)
    if merged != current:
        store.write_json(store.settings_path(), merged)

    return {
        "data_dir": str(status.path),
        "status": status.status,
        "first_run": status.first_run,
        "settings_applied": sorted(missing_keys(current)),
        "steps": plan(status, merged),
    }


def environment_report() -> dict[str, Any]:
    """运行环境摘要，用于首启提示。"""
    return {
        "data_dir_env": os.environ.get("XWORLD_DATA_DIR", ""),
        "appdata": os.environ.get("APPDATA", ""),
        "platform": os.name,
    }
