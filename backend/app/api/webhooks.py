"""Public webhook endpoints, one URL per workspace and channel:  /webhooks/{tenant_id}/{channel}

Security rules
* Meta's X-Hub-Signature-256 is verified with the workspace's own app secret against the raw bytes.
* Every failure (unknown workspace, unknown channel, unconfigured, wrong token, bad signature) returns the
  same bare 403, so the endpoint cannot be used to discover which workspaces exist.
* The body is size-limited while it is read, nothing is parsed before the signature is valid, and nothing
  from the payload is logged.
* The event is written to the durable queue before Meta receives its 200, so an acknowledged event is never lost.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from ..channels import events as event_parser
from ..channels import signature
from ..channels.registry import get_channel
from ..errors import TooManyRequests

log = logging.getLogger("app.webhooks")
router = APIRouter(tags=["webhooks"], include_in_schema=False)


def _deny() -> PlainTextResponse:
    return PlainTextResponse("Forbidden", status_code=403)


def _uuid(value: str) -> Optional[UUID]:
    try:
        return UUID(value)
    except ValueError:
        return None


async def _read_limited(request: Request, limit: int) -> Optional[bytes]:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        return None
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


async def _hit(request: Request, key: str, window: int, limit: int) -> None:
    repo = request.app.state.auth_service.repo
    count, retry = await repo.rate_hit(key, window, datetime.now(timezone.utc))
    if count > limit:
        raise TooManyRequests(retry)


@router.get("/webhooks/{tenant_id}/{channel_id}")
async def verify(tenant_id: str, channel_id: str, request: Request):
    """Meta's one-time subscription handshake."""
    tid = _uuid(tenant_id)
    if tid is None or get_channel(channel_id) is None:
        return _deny()
    await _hit(request, f"wh:verify:{tid}", 3600, 120)
    config = await request.app.state.channels.webhook_config(tid, channel_id)
    params = request.query_params
    challenge = signature.safe_challenge(params.get("hub.challenge"))
    if (config and params.get("hub.mode") == "subscribe" and challenge
            and signature.valid_verify_token(config.verify_token, params.get("hub.verify_token"))):
        return PlainTextResponse(challenge)
    return _deny()


@router.post("/webhooks/{tenant_id}/{channel_id}")
async def receive(tenant_id: str, channel_id: str, request: Request):
    settings = request.app.state.settings
    tid, channel = _uuid(tenant_id), get_channel(channel_id)
    if tid is None or channel is None:
        return _deny()
    body = await _read_limited(request, settings.webhook_max_body_bytes)
    if body is None:
        return PlainTextResponse("Payload too large", status_code=413)

    config = await request.app.state.channels.webhook_config(tid, channel_id)
    if config is None or not signature.valid_signature(config.app_secret, body, request.headers.get("x-hub-signature-256")):
        log.warning("webhook_rejected", extra={"channel": channel_id, "tenant": str(tid)})
        return _deny()
    if not config.enabled:
        return JSONResponse({"status": "ignored", "reason": "channel_disabled"})
    await _hit(request, f"wh:rate:{tid}", 60, settings.webhook_rate_per_minute)

    try:
        payload = json.loads(body)
    except ValueError:
        return PlainTextResponse("Invalid JSON", status_code=400)
    extracted = event_parser.extract(channel, config.account_id, payload)
    queued = await request.app.state.queue.enqueue(tid, channel_id, extracted.events)
    return JSONResponse({"received": len(extracted.events), "queued": queued, "skipped": extracted.skipped})
