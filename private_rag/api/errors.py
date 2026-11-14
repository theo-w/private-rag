"""API 错误类型与统一错误响应。"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """业务错误；由异常处理器转换为统一 JSON。"""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Optional[Any] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def request_id_of(request: Request) -> str:
    return str(getattr(request.state, "request_id", "") or "")


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: Optional[Any] = None,
) -> JSONResponse:
    request_id = request_id_of(request)
    return JSONResponse(
        status_code=status_code,
        headers={"X-Request-ID": request_id},
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details,
                "request_id": request_id,
            }
        },
    )


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return error_response(
        request,
        exc.status_code,
        exc.code,
        exc.message,
        exc.details,
    )


async def validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    # 只返回字段位置与原因，不回显用户输入，避免把敏感内容写进响应/日志
    details = [
        {
            "field": ".".join(str(part) for part in error.get("loc", [])),
            "message": error.get("msg", ""),
            "type": error.get("type", ""),
        }
        for error in exc.errors()
    ]
    return error_response(
        request,
        422,
        "invalid_request",
        "请求参数校验失败",
        details,
    )


async def http_error_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    if exc.status_code == 404:
        code, message = "not_found", "接口或资源不存在"
    elif exc.status_code == 405:
        code, message = "method_not_allowed", "请求方法不允许"
    else:
        code, message = "http_error", str(exc.detail)
    return error_response(request, exc.status_code, code, message)


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("未处理异常 request_id=%s", request_id_of(request))
    return error_response(
        request,
        500,
        "internal_error",
        "服务内部错误",
    )
