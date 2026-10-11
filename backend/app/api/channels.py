"""/api/channels routes: per-channel settings, test connection, verify-token rotation, queue health."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from ..channels.registry import CHANNEL_IDS, ValidationFailed
from ..channels.service import ChannelService
from ..errors import BadRequest, TooManyRequests
from ..jobs.queue import EventQueue
from ..security.tokens import AccessClaims
from .deps import current_claims

router = APIRouter(prefix="/api/channels", tags=["channels"])

MAX_FIELDS = 20


class SaveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Empty or missing credential fields mean "keep what is already saved".
    values: Dict[str, Optional[str]] = Field(default_factory=dict, max_length=MAX_FIELDS)
    behaviour: Dict[str, Optional[str]] = Field(default_factory=dict, max_length=MAX_FIELDS)
    enabled: Optional[bool] = None
    bot_enabled: Optional[bool] = None


def svc(request: Request) -> ChannelService:
    return request.app.state.channels


@contextmanager
def _user_errors():
    try:
        yield
    except ValidationFailed as exc:
        raise BadRequest("Some settings need attention.", code="invalid_settings", details={"fields": exc.errors})


async def _throttle(request: Request, claims: AccessClaims, name: str, limit: int) -> None:
    repo = request.app.state.auth_service.repo
    count, retry = await repo.rate_hit(f"channels:{name}:{claims.tenant_id}", 3600, datetime.now(timezone.utc))
    if count > limit:
        raise TooManyRequests(retry)


async def _audit(request: Request, claims: AccessClaims, event: str, **detail) -> None:
    await request.app.state.auth_service.repo.audit(
        event=event, now=datetime.now(timezone.utc), user_id=claims.user_id, tenant_id=claims.tenant_id, detail=detail or None)


@router.get("")
async def list_channels(claims: AccessClaims = Depends(current_claims), s: ChannelService = Depends(svc)):
    return {"channels": await s.list(claims.tenant_id)}


@router.get("/{channel_id}")
async def get_channel(channel_id: str, claims: AccessClaims = Depends(current_claims), s: ChannelService = Depends(svc)):
    return await s.get(claims.tenant_id, channel_id)


@router.put("/{channel_id}")
async def save_channel(channel_id: str, body: SaveIn, request: Request, claims: AccessClaims = Depends(current_claims),
                       s: ChannelService = Depends(svc)):
    await _throttle(request, claims, "save", 200)
    with _user_errors():
        result = await s.save(claims.tenant_id, channel_id, values=body.values, behaviour=body.behaviour,
                              enabled=body.enabled, bot_enabled=body.bot_enabled)
    # Audit field NAMES only, never values.
    await _audit(request, claims, "channel_saved", channel=channel_id, fields=sorted(k for k, v in body.values.items() if v),
                 enabled=result["enabled"], bot_enabled=result["bot_enabled"])
    return result


@router.post("/{channel_id}/test")
async def test_channel(channel_id: str, request: Request, claims: AccessClaims = Depends(current_claims),
                       s: ChannelService = Depends(svc)):
    await _throttle(request, claims, "test", 60)
    result = await s.test(claims.tenant_id, channel_id)
    await _audit(request, claims, "channel_tested", channel=channel_id, ok=result["ok"])
    return result


@router.post("/{channel_id}/verify-token/rotate")
async def rotate_token(channel_id: str, request: Request, claims: AccessClaims = Depends(current_claims),
                       s: ChannelService = Depends(svc)):
    await _throttle(request, claims, "rotate", 20)
    result = await s.rotate_verify_token(claims.tenant_id, channel_id)
    await _audit(request, claims, "verify_token_rotated", channel=channel_id)
    return result


@router.delete("/{channel_id}", status_code=204)
async def clear_channel(channel_id: str, request: Request, claims: AccessClaims = Depends(current_claims),
                        s: ChannelService = Depends(svc)):
    await s.clear(claims.tenant_id, channel_id)
    await _audit(request, claims, "channel_cleared", channel=channel_id)


@router.get("/-/queue")
async def queue_health(request: Request, claims: AccessClaims = Depends(current_claims)):
    """How many webhook events are waiting, running, finished or buried for this workspace."""
    queue: EventQueue = request.app.state.queue
    return {"channels": list(CHANNEL_IDS), **await queue.stats(claims.tenant_id)}
