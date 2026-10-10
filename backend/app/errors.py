"""Consistent error type and response format: {"error": {"code", "message", "request_id", "details"?}}."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .logging_setup import request_id_var


class AppError(Exception):
    status = 500
    code = "internal_error"

    def __init__(self, message: str = "Something went wrong.", *, code: Optional[str] = None,
                 headers: Optional[Dict[str, str]] = None, details: Any = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.headers = headers or {}
        self.details = details


class BadRequest(AppError):
    status, code = 400, "bad_request"


class Unauthorized(AppError):
    status, code = 401, "unauthorized"


class Forbidden(AppError):
    status, code = 403, "forbidden"


class NotFound(AppError):
    status, code = 404, "not_found"


class Conflict(AppError):
    status, code = 409, "conflict"


class TooManyRequests(AppError):
    status, code = 429, "rate_limited"

    def __init__(self, retry_after: int, message: str = "Too many attempts. Please try again later."):
        retry_after = max(1, int(retry_after))
        super().__init__(message, headers={"Retry-After": str(retry_after)})
        self.retry_after = retry_after


def _body(code: str, message: str, details: Any = None) -> Dict[str, Any]:
    error: Dict[str, Any] = {"code": code, "message": message, "request_id": request_id_var.get()}
    if details is not None:
        error["details"] = details
    return {"error": error}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(_body(exc.code, exc.message, exc.details), status_code=exc.status, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        # Never echo submitted values back (they may contain passwords or tokens).
        details = [{"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]}
                   for e in exc.errors()]
        return JSONResponse(_body("validation_error", "Some fields are invalid.", details), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        return JSONResponse(_body("http_error", str(exc.detail)), status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        import logging
        logging.getLogger("app").exception("unhandled_error")
        return JSONResponse(_body("internal_error", "Something went wrong."), status_code=500)
