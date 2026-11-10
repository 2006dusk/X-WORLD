"""结构化错误。

所有对外错误统一为 {"error": {"code", "message", "retryable"}} 形状，
与前端事件集的第 4.2 节错误事件保持一致。
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


class ApiError(Exception):
    """对外抛出的业务错误。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        status_code: int = 400,
        detail: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code
        self.detail = detail


def error_response(exc: ApiError) -> JSONResponse:
    body: dict[str, Any] = {
        "error": {
            "code": exc.code,
            "message": exc.message,
            "retryable": exc.retryable,
        }
    }
    if exc.detail is not None:
        body["error"]["detail"] = exc.detail
    return JSONResponse(status_code=exc.status_code, content=body)
