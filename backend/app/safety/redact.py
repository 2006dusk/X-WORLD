"""日志敏感信息脱敏。

参考 StarHub 的 safety/sensitive_filter.py，裁剪到日志场景：
只保留识别密钥与令牌的匹配规则，去掉扫描源码、审计告警那部分。

过滤做成 logging.Filter 而不是格式化器，原因是格式化器要自己拼格式，
脱敏在格式化之前做，异常堆栈与结构化字段都还会原样保留。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

REDACTED = "***REDACTED***"


@dataclass(frozen=True)
class RedactPattern:
    """一条脱敏规则。"""

    name: str
    regex: re.Pattern[str]


DEFAULT_PATTERNS: tuple[RedactPattern, ...] = (
    RedactPattern("openai_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    RedactPattern("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    RedactPattern("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")),
    RedactPattern("aws_key_id", re.compile(r"AKIA[0-9A-Z]{16}")),
    RedactPattern("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    RedactPattern("jwt", re.compile(r"eyJ[A-Za-z0-9_-]+.eyJ[A-Za-z0-9_-]+.[A-Za-z0-9_-]+")),
    RedactPattern("bearer_token", re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._-]{16,}")),
    RedactPattern(
        "assigned_secret",
        re.compile(r"(?i)((?:api[_-]?key|password|secret|token)\s*[=:]\s*)[\"'][^\"']{8,}[\"']"),
    ),
)


def redact(text: str) -> str:
    """把文本里的密钥与令牌替换为占位符。"""
    if not text:
        return text
    out = text
    for rule in DEFAULT_PATTERNS:
        if rule.name == "bearer_token":
            out = rule.regex.sub(lambda m: m.group(1) + REDACTED, out)
        elif rule.name == "assigned_secret":
            out = rule.regex.sub(lambda m: m.group(1) + REDACTED, out)
        else:
            out = rule.regex.sub(REDACTED, out)
    return out


class RedactingFilter(logging.Filter):
    """给日志记录器挂上脱敏过滤器。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - 格式化失败时保留原记录，交由 logging 报错
            return True
        redacted = redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True
