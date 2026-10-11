"""Helpers shared by the dashboard routers: workspace database pool, platform clients, throttling, audit."""
from __future__ import annotations

import re
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, Tuple

import asyncpg
from fastapi import Depends, Request

from ..channels.registry import CHANNELS
from ..errors import AppError, BadRequest, NotFound, TooManyRequests
from ..platforms.factory import build_client
from ..platforms.meta import MetaClient, MetaError
from ..security.tokens import AccessClaims
from ..tenantdb.provisioner import DatabaseSetupError
from .deps import current_claims

INBOX_CHANNEL = {"whatsapp": "whatsapp", "facebook": "facebook_inbox", "instagram": "instagram_inbox"}
COMMENT_CHANNEL = {"facebook": "facebook_comments", "instagram": "instagram_comments"}
_SAFE_ID = re.compile(r"^[A-Za-z0-9_\-]{1,100}$")


class PlatformError(AppError):
    """Meta (or another platform) refused or could not be reached. Never a 401, so the user stays signed in."""
    status, code = 502, "platform_error"


def safe_path_id(value: str) -> str:
    if not _SAFE_ID.match(value or ""):
        raise NotFound("Not found.")
    return value


def inbox_platform(platform: str) -> str:
    if platform not in INBOX_CHANNEL:
        raise NotFound("Unknown platform.")
    return platform


def comment_platform(platform: str) -> str:
    if platform not in COMMENT_CHANNEL:
        raise NotFound("Unknown platform.")
    return platform


async def workspace_pool(request: Request, claims: AccessClaims = Depends(current_claims)) -> asyncpg.Pool:
    """The calling workspace's own database. Raises a user-readable 400 until the database is set up."""
    try:
        return await request.app.state.tenant_db.pool(claims.tenant_id)
    except DatabaseSetupError as exc:
        raise BadRequest(exc.message, code=exc.code, details={"hint": exc.hint} if exc.hint else None)


async def throttle(request: Request, claims: AccessClaims, name: str, limit: int, window: int = 3600) -> None:
    repo = request.app.state.auth_service.repo
    count, retry = await repo.rate_hit(f"api:{name}:{claims.tenant_id}", window, datetime.now(timezone.utc))
    if count > limit:
        raise TooManyRequests(retry)


async def audit(request: Request, claims: AccessClaims, event: str, **detail: Any) -> None:
    await request.app.state.auth_service.repo.audit(
        event=event, now=datetime.now(timezone.utc), user_id=claims.user_id, tenant_id=claims.tenant_id,
        detail=detail or None)


@asynccontextmanager
async def platform_client(request: Request, claims: AccessClaims, channel_id: str
                          ) -> AsyncIterator[Tuple[MetaClient, Dict[str, Any]]]:
    """A Meta client for one of the workspace's channels, plus its decrypted configuration."""
    creds = await request.app.state.channels.credentials(claims.tenant_id, channel_id)
    client = build_client(request.app.state.settings, CHANNELS[channel_id], creds, http=request.app.state.http)
    if client is None or not creds["enabled"]:
        raise BadRequest("Connect and turn on this channel in Channel settings first.", code="channel_not_ready")
    yield client, creds


@contextmanager
def meta_errors():
    """Turn Meta failures into a safe message (never Meta's own error text)."""
    try:
        yield
    except MetaError as exc:
        if exc.is_auth or exc.status in (400, 403) or exc.code in (10, 200, 299):
            raise BadRequest(exc.describe(), code="platform_rejected")
        raise PlatformError(exc.describe())
