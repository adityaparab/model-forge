"""The one error shape: ``{"error": {"kind": <str>, "message": <str>, ...extra}}``.

Kinds: ``identity``, ``role``, ``not_found``, ``not_ready``, ``brief``, ``profile``,
``approval``, ``state``, ``validation``, ``settings``, ``internal``.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from modelforge_lineage.observability import get_logger

__all__ = ["ApiError", "error_response", "install"]

_log = get_logger(component="api")


class ApiError(Exception):
    """An error the API answers with ``status_code`` and the error shape."""

    def __init__(self, status_code: int, kind: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.kind = kind
        self.message = message
        self.extra = extra


def error_response(status_code: int, kind: str, message: str, **extra: Any) -> JSONResponse:
    body = {"error": {"kind": kind, "message": message, **jsonable_encoder(extra)}}
    return JSONResponse(status_code=status_code, content=body)


async def _api_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return error_response(exc.status_code, exc.kind, exc.message, **exc.extra)


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return error_response(422, "validation", "request validation failed", detail=exc.errors())


async def _internal_error(request: Request, exc: Exception) -> JSONResponse:
    _log.error(
        "unhandled exception",
        method=request.method,
        path=request.url.path,
        type=type(exc).__name__,
        error=str(exc),
    )
    return error_response(500, "internal", "internal error", type=type(exc).__name__)


def install(app: FastAPI) -> None:
    """Register the handlers that map every error the API writes to the one shape."""
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _internal_error)
