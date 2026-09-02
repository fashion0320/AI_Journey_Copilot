"""统一 API 响应模型与错误处理。"""

from __future__ import annotations

from typing import Any, Generic, Optional, TypeVar

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    code: int = Field(default=0, description="0 表示成功，非 0 表示错误码")
    message: str = "ok"
    data: Any = None

    @classmethod
    def success(cls, data: Any = None, message: str = "ok") -> "ApiResponse[Any]":
        return cls(code=0, message=message, data=data)

    @classmethod
    def error(cls, code: int, message: str) -> "ApiResponse[Any]":
        return cls(code=code, message=message, data=None)


class AppError(Exception):
    """业务异常基类。"""

    def __init__(self, code: int, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.code,
            "message": exc.message,
            "data": None,
        },
    )


async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={
            "code": 500,
            "message": f"internal server error: {exc}",
            "data": None,
        },
    )


def register_exception_handlers(app) -> None:  # type: ignore[no-untyped-def]
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(Exception, general_exception_handler)
