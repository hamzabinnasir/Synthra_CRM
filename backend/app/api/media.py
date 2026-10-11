"""Media: authenticated download for the dashboard, and short-lived signed URLs so Meta can fetch images we send.

The dashboard cannot put an Authorization header on <img>/<audio> tags, so it downloads media through
fetch() and shows a blob URL. The public route exists only because Meta must be able to fetch an image
from a URL when we send one; the URL is signed with HMAC-SHA256, expires in 10 minutes and only serves
JPEG/PNG.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from ..config import Settings
from ..crm import store
from ..errors import NotFound, TooManyRequests
from ..security.tokens import AccessClaims
from .common import workspace_pool
from .deps import current_claims

router = APIRouter(prefix="/api/media", tags=["media"])
public_router = APIRouter(tags=["media"], include_in_schema=False)

# Types the browser may render inline. Everything else is forced to download.
INLINE = {"image/jpeg", "image/png", "image/gif", "image/webp", "audio/mpeg", "audio/ogg", "audio/mp4",
          "audio/aac", "audio/amr", "audio/webm", "video/mp4", "video/webm", "video/3gpp", "application/pdf"}
PUBLIC_TYPES = {"image/jpeg", "image/png"}
SIGNED_TTL_SECONDS = 600


def _signature(settings: Settings, tenant_id: UUID, media_id: str, expires: int) -> str:
    key = ("media-url:" + settings.jwt_secret).encode()
    return hmac.new(key, f"{tenant_id}:{media_id}:{expires}".encode(), hashlib.sha256).hexdigest()


def signed_media_url(settings: Settings, tenant_id: UUID, media_id: str) -> str:
    expires = int(time.time()) + SIGNED_TTL_SECONDS
    sig = _signature(settings, tenant_id, media_id, expires)
    return f"{settings.api_public_url.rstrip('/')}/public/media/{tenant_id}/{media_id}?exp={expires}&sig={sig}"


def _file_response(record, *, inline_types: set) -> Response:
    mime = record["mime_type"] if record["mime_type"] in inline_types else "application/octet-stream"
    disposition = "inline" if mime != "application/octet-stream" else "attachment"
    name = (record["filename"] or "file").replace('"', "").replace("\r", "").replace("\n", "")[:120]
    return Response(content=bytes(record["data"]), media_type=mime, headers={
        "Content-Disposition": f'{disposition}; filename="{name}"', "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=300"})


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise NotFound("Not found.")


@router.get("/{media_id}")
async def get_media(media_id: str, claims: AccessClaims = Depends(current_claims), pool=Depends(workspace_pool)):
    record = await store.get_media(pool, _uuid(media_id))
    if record is None:
        raise NotFound("That file no longer exists.")
    return _file_response(record, inline_types=INLINE)


@public_router.get("/public/media/{tenant_id}/{media_id}")
async def public_media(tenant_id: str, media_id: str, exp: int, sig: str, request: Request):
    tid, mid = _uuid(tenant_id), _uuid(media_id)
    settings = request.app.state.settings
    if exp < time.time() or not hmac.compare_digest(sig, _signature(settings, tid, str(mid), exp)):
        raise NotFound("Not found.")
    count, retry = await request.app.state.auth_service.repo.rate_hit(
        f"pubmedia:{tid}", 60, datetime.now(timezone.utc))
    if count > 120:
        raise TooManyRequests(retry)
    pool = await request.app.state.tenant_db.pool(tid)
    record = await store.get_media(pool, mid)
    if record is None or record["mime_type"] not in PUBLIC_TYPES:
        raise NotFound("Not found.")
    return _file_response(record, inline_types=PUBLIC_TYPES)
