"""Public webhook endpoints, one URL per workspace and PLATFORM:  /webhooks/{tenant_id}/{platform}

    whatsapp   -> whatsapp
    facebook   -> facebook_inbox + facebook_comments     (one Meta "page" subscription carries both)
    instagram  -> instagram_inbox + instagram_comments   (one Meta "instagram" subscription carries both)

Meta allows a single callback URL per object per app, so the three URLs above are everything a workspace
configures in Meta. A request is verified once, then fanned out: each configured channel of the platform
parses the payload for its own kind of event (messages or comments) and queues it under its own channel id,
which is what the bot engine's per-channel handlers consume.

Per-channel URLs (/webhooks/{tenant}/facebook_comments and so on) from earlier versions still work and serve
only that channel, so callbacks already saved in Meta keep delivering.

Security rules
* Meta's X-Hub-Signature-256 is verified with the workspace's own app secret against the raw bytes. A channel
  receives events only if its own app secret validates the signature.
* Every failure (unknown workspace, unknown platform, unconfigured, wrong token, bad signature) returns the
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
from ..channels.registry import resolve_webhook_target
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


@router.get("/webhooks/{tenant_id}/{target}")
async def verify(tenant_id: str, target: str, request: Request):
    """Meta's one-time subscription handshake. Succeeds if the token matches any channel of the platform."""
    tid = _uuid(tenant_id)
    platform, channels = resolve_webhook_target(target)
    if tid is None or platform is None:
        return _deny()
    await _hit(request, f"wh:verify:{tid}", 3600, 120)
    targets = await request.app.state.channels.webhook_targets(tid, channels)
    params = request.query_params
    challenge = signature.safe_challenge(params.get("hub.challenge"))
    supplied = params.get("hub.verify_token")
    token_ok = any([signature.valid_verify_token(t.config.verify_token, supplied) for t in targets])
    if params.get("hub.mode") == "subscribe" and challenge and token_ok:
        return PlainTextResponse(challenge)
    return _deny()


@router.post("/webhooks/{tenant_id}/{target}")
async def receive(tenant_id: str, target: str, request: Request):
    settings = request.app.state.settings
    tid = _uuid(tenant_id)
    platform, channels = resolve_webhook_target(target)
    if tid is None or platform is None:
        return _deny()
    body = await _read_limited(request, settings.webhook_max_body_bytes)
    if body is None:
        return PlainTextResponse("Payload too large", status_code=413)

    targets = await request.app.state.channels.webhook_targets(tid, channels)
    header = request.headers.get("x-hub-signature-256")
    signed = [t for t in targets if signature.valid_signature(t.config.app_secret, body, header)]
    if not signed:
        log.warning("webhook_rejected", extra={"channel": target, "tenant": str(tid)})
        return _deny()
    live = [t for t in signed if t.config.enabled]
    if not live:
        return JSONResponse({"status": "ignored", "reason": "channel_disabled"})
    await _hit(request, f"wh:rate:{tid}", 60, settings.webhook_rate_per_minute)

    try:
        payload = json.loads(body)
    except ValueError:
        return PlainTextResponse("Invalid JSON", status_code=400)

    received = queued = skipped = 0
    for t in live:
        extracted = event_parser.extract(t.channel, t.config.account_id, payload)
        received += len(extracted.events)
        skipped = max(skipped, extracted.skipped)       # the same entry is seen by each channel; count it once
        if extracted.events:
            queued += await request.app.state.queue.enqueue(tid, t.channel.id, extracted.events)
    return JSONResponse({"received": received, "queued": queued, "skipped": skipped})
