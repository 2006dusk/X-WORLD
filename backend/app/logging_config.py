"""统一日志配置。

参考 StarHub 的 core/logging_config.py，做了两处裁剪：
- 不做结构化日志字段，本项目没有日志采集端点，文本格式足够；
- 保留轮转与脱敏，轮转防止日志文件无限增长，脱敏防止密钥进日志。

日志文件落在数据目录的 logs 子目录，由 app.config.logs_dir() 给出，
模块内不出现硬编码路径。
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from app.config import logs_dir
from app.safety.redact import RedactingFilter

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
LOG_FILE_NAME = "xworld-backend.log"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3

_configured = False


def setup_logging(level: str = "INFO") -> logging.Logger:
    """配置根日志记录器：控制台加轮转文件，重复调用不叠加处理器。"""
    global _configured
    root = logging.getLogger()
    if _configured:
        return root

    resolved = getattr(logging, str(level).upper(), logging.INFO)
    root.setLevel(resolved)

    formatter = logging.Formatter(LOG_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(RedactingFilter())
    root.addHandler(console)

    try:
        path = logs_dir() / LOG_FILE_NAME
        file_handler = RotatingFileHandler(
            path,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8-sig",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(RedactingFilter())
        root.addHandler(file_handler)
    except OSError:
        root.warning("日志文件不可写，仅输出到控制台")

    _configured = True
    return root


def get_logger(name: str) -> logging.Logger:
    """取模块日志记录器。"""
    return logging.getLogger(name)
