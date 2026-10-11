"""/api analytics, notifications, activity feed and the live-update stream (Server-Sent Events).

The stream sends a small "marks" fingerprint (see store.activity_marks) whenever something changed, and the
dashboard refetches only what it needs. It never carries message text. Browsers cannot put an Authorization
header on EventSource, so the dashboard reads it with fetch(); the stream ends after MAX_STREAM_SECONDS, which
is shorter than the access token lifetime, so every reconnect is authenticated with a fresh token.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import AsyncIterator, Dict, List, Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ..crm import store
from ..errors import AppError
from ..security.tokens import AccessClaims
from .common import workspace_pool
from .deps import current_claims

log = logging.getLogger("app.insights")
router = APIRouter(prefix="/api", tags=["insights"])

POLL_SECONDS = 3
HEARTBEAT_SECONDS = 15
MAX_STREAM_SECONDS = 540
MAX_STREAMS_PER_TENANT = 5
_open_streams: Dict[UUID, int] = defaultdict(int)


class TooManyStreams(AppError):
    status, code = 429, "too_many_streams"


class ReadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: Optional[List[int]] = Field(None, max_length=200)     # omitted means "mark everything read"


@router.get("/analytics")
async def analytics(days: int = Query(7, ge=1, le=90), claims: AccessClaims = Depends(current_claims),
                    pool: asyncpg.Pool = Depends(workspace_pool)):
    return await store.analytics(pool, days)


@router.get("/notifications")
async def notifications(unread: bool = False, before_id: Optional[int] = Query(None, ge=1),
                        limit: int = Query(50, ge=1, le=100), claims: AccessClaims = Depends(current_claims),
                        pool: asyncpg.Pool = Depends(workspace_pool)):
    items = await store.list_notifications(pool, unread_only=unread, limit=limit, before_id=before_id)
    return {"notifications": items, "unread": await store.unread_notification_count(pool)}


@router.get("/notifications/unread-count")
async def unread_count(claims: AccessClaims = Depends(current_claims), pool: asyncpg.Pool = Depends(workspace_pool)):
    return {"unread": await store.unread_notification_count(pool)}


@router.post("/notifications/read")
async def mark_read(body: ReadIn, claims: AccessClaims = Depends(current_claims),
                    pool: asyncpg.Pool = Depends(workspace_pool)):
    return {"updated": await store.mark_notifications_read(pool, body.ids)}


@router.get("/activity")
async def activity(limit: int = Query(100, ge=1, le=200), claims: AccessClaims = Depends(current_claims),
                   pool: asyncpg.Pool = Depends(workspace_pool)):
    return {"activity": await store.list_activity(pool, limit)}


async def _stream(request: Request, tenant_id: UUID) -> AsyncIterator[str]:
    _open_streams[tenant_id] += 1
    loop = asyncio.get_running_loop()
    started = last_beat = loop.time()
    last: Optional[Dict[str, int]] = None
    try:
        yield "retry: 5000\n\n"
        while loop.time() - started < MAX_STREAM_SECONDS:
            if await request.is_disconnected():
                return
            marks = None
            try:
                # Fetch the pool each round so the connection cache sees it as in use and does not reap it.
                pool = await request.app.state.tenant_db.pool(tenant_id)
                marks = await store.activity_marks(pool)
            except Exception as exc:
                log.warning("sse_poll_failed", extra={"error": type(exc).__name__})
            if marks is not None and marks != last:
                last = marks
                yield f"event: marks\ndata: {json.dumps(marks)}\n\n"
                last_beat = loop.time()
            elif loop.time() - last_beat >= HEARTBEAT_SECONDS:
                yield ": keep-alive\n\n"
                last_beat = loop.time()
            await asyncio.sleep(POLL_SECONDS)
        yield "event: reconnect\ndata: {}\n\n"
    finally:
        _open_streams[tenant_id] -= 1
        if _open_streams[tenant_id] <= 0:
            _open_streams.pop(tenant_id, None)


@router.get("/events")
async def events(request: Request, claims: AccessClaims = Depends(current_claims),
                 pool: asyncpg.Pool = Depends(workspace_pool)):
    """Live updates. `pool` is resolved only to fail early with the usual 'connect your database' message."""
    if _open_streams.get(claims.tenant_id, 0) >= MAX_STREAMS_PER_TENANT:
        raise TooManyStreams("Too many dashboard tabs are open for this workspace. Close one and retry.")
    return StreamingResponse(_stream(request, claims.tenant_id), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
