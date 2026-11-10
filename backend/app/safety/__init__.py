"""安全相关工具。

当前只含日志脱敏过滤器。参考 StarHub 的 safety 层，裁剪到本项目实际需要的范围：
本项目是单人本地应用，没有密钥存储与多租户，因此不引入 key_store、sandbox、
resource_limiter 那几套。
"""

from __future__ import annotations

from .redact import REDACTED, RedactingFilter, redact

__all__ = ["REDACTED", "RedactingFilter", "redact"]
