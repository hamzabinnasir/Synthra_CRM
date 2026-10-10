"""HTTP helpers: client IP, CSRF guard, refresh-cookie handling, security headers, request ids."""
from __future__ import annotations

import time
import uuid
from typing import Optional
from urllib.parse import urlparse

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from .config import Settings
from .errors import Forbidden
from .logging_setup import request_id_var

REFRESH_COOKIE = "crm_refresh"
REFRESH_COOKIE_PATH = "/api/auth"


def client_ip(request: Request, trusted_hops: int) -> Optional[str]:
    """The real client IP. X-Forwarded-For is trusted only for the configured number of proxy hops."""
    if trusted_hops > 0:
        parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if len(parts) >= trusted_hops:
            return parts[-trusted_hops]
    return request.client.host if request.client else None


def csrf_guard(request: Request, settings: Settings) -> None:
    """For cookie-authenticated endpoints: require a custom header (not sendable cross-site without
    CORS approval) and, when the browser sends an Origin, require it to be an allowed origin."""
    if request.headers.get("x-csrf-protection") != "1":
        raise Forbidden("Missing CSRF header.", code="csrf_failed")
    origin = request.headers.get("origin")
    if origin:
        parsed = urlparse(origin)
        normalized = f"{parsed.scheme}://{parsed.netloc}"
        if normalized not in settings.origins:
            raise Forbidden("Origin not allowed.", code="csrf_failed")


def set_refresh_cookie(response: Response, settings: Settings, token: str, max_age_seconds: int) -> None:
    response.set_cookie(
        REFRESH_COOKIE, token, max_age=max_age_seconds, path=REFRESH_COOKIE_PATH,
        httponly=True, secure=settings.cookie_secure, samesite="strict")


def clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH, httponly=True,
                           secure=settings.cookie_secure, samesite="strict")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, logs one line per request (no headers/bodies) and adds security headers."""

    def __init__(self, app, settings: Settings):
        super().__init__(app)
        self._production = settings.is_production

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id", "")[:64] or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
            response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        if self._production:
            response.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
        import logging
        logging.getLogger("app.access").info("request", extra={
            "method": request.method, "path": request.url.path, "status": response.status_code,
            "ms": round((time.perf_counter() - started) * 1000, 1)})
        return response
