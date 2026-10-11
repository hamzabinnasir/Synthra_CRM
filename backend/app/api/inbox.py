"""/api/inbox: conversations, messages, replies from a person, bot toggle, hand-off, history clean-up."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

import asyncpg
from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from ..crm import lookups, store
from ..errors import BadRequest, NotFound
from ..security.tokens import AccessClaims
from .common import (INBOX_CHANNEL, audit, inbox_platform, meta_errors, platform_client, safe_path_id, throttle,
                     workspace_pool)
from .deps import current_claims
from .media import signed_media_url

router = APIRouter(prefix="/api/inbox", tags=["inbox"])

MAX_IMAGE_BYTES = 5_000_000
IMAGE_TYPES = {"image/jpeg", "image/png"}


class SendIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=4000)


class BotIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active: bool


class ResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resume_bot: bool = True


class RenameIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: Optional[str] = Field(None, max_length=100)


async def _customer(pool: asyncpg.Pool, platform: str, psid: str) -> Dict[str, Any]:
    customer = await store.get_customer(pool, safe_path_id(psid))
    if customer is None or customer["platform"] != platform:
        raise NotFound("Conversation not found.")
    customer["display_name"] = (customer.get("custom_name") or customer.get("name") or customer.get("username")
                                or customer["psid"])
    customer.pop("rolling_summary", None)
    return customer


@router.get("/{platform}/conversations")
async def conversations(platform: str, q: Optional[str] = Query(None, max_length=100), unread: bool = False,
                        handoff: bool = False, before: Optional[datetime] = None,
                        limit: int = Query(50, ge=1, le=100), claims: AccessClaims = Depends(current_claims),
                        pool: asyncpg.Pool = Depends(workspace_pool)):
    items = await store.list_conversations(pool, inbox_platform(platform), q=q, unread_only=unread,
                                           handoff_only=handoff, limit=limit, before=before)
    return {"conversations": items}


@router.get("/{platform}/conversations/{psid}/messages")
async def messages(platform: str, psid: str, before_id: Optional[int] = None, limit: int = Query(100, ge=1, le=200),
                   claims: AccessClaims = Depends(current_claims), pool: asyncpg.Pool = Depends(workspace_pool)):
    customer = await _customer(pool, inbox_platform(platform), psid)
    return {"customer": customer, "messages": await store.get_messages(pool, psid, limit=limit, before_id=before_id)}


@router.post("/{platform}/conversations/{psid}/read", status_code=204)
async def mark_read(platform: str, psid: str, claims: AccessClaims = Depends(current_claims),
                    pool: asyncpg.Pool = Depends(workspace_pool)):
    await _customer(pool, inbox_platform(platform), psid)
    await store.mark_read(pool, psid)


async def _agent_sent(request: Request, claims: AccessClaims, pool: asyncpg.Pool, platform: str, psid: str,
                      creds: Dict[str, Any], **message: Any) -> Optional[int]:
    message_id = await store.insert_message(pool, psid=psid, platform=platform, sender="agent", **message)
    await store.mark_read(pool, psid)
    if creds["behaviour"].get("pause_on_human") == "true":
        await store.set_bot_active(pool, psid, False)
    return message_id


@router.post("/{platform}/conversations/{psid}/send")
async def send(platform: str, psid: str, body: SendIn, request: Request, claims: AccessClaims = Depends(current_claims),
               pool: asyncpg.Pool = Depends(workspace_pool)):
    platform = inbox_platform(platform)
    await throttle(request, claims, "send", 1200)
    await _customer(pool, platform, psid)
    async with platform_client(request, claims, INBOX_CHANNEL[platform]) as (client, creds):
        with meta_errors():
            mid = await client.send_text(store.external_id(psid, platform), body.text, agent=True)
    message_id = await _agent_sent(request, claims, pool, platform, psid, creds, text=body.text, mid=mid)
    return {"id": message_id}


@router.post("/{platform}/conversations/{psid}/send-image")
async def send_image(platform: str, psid: str, request: Request, file: UploadFile = File(...),
                     claims: AccessClaims = Depends(current_claims), pool: asyncpg.Pool = Depends(workspace_pool)):
    platform = inbox_platform(platform)
    await throttle(request, claims, "send-image", 200)
    await _customer(pool, platform, psid)
    if (file.content_type or "") not in IMAGE_TYPES:
        raise BadRequest("Only JPEG or PNG images can be sent.", code="unsupported_media")
    data = await file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise BadRequest("That image is larger than 5 MB.", code="file_too_large")
    if not (data.startswith(b"\xff\xd8\xff") or data.startswith(b"\x89PNG\r\n\x1a\n")):
        raise BadRequest("That file is not a valid JPEG or PNG image.", code="unsupported_media")
    media_id = await store.save_media(pool, data, file.content_type, file.filename)
    url = signed_media_url(request.app.state.settings, claims.tenant_id, media_id)
    async with platform_client(request, claims, INBOX_CHANNEL[platform]) as (client, creds):
        with meta_errors():
            mid = await client.send_image(store.external_id(psid, platform), url)
    message_id = await _agent_sent(request, claims, pool, platform, psid, creds, text="", mid=mid,
                                   media_url=f"media:{media_id}", media_type="image", media_mime_type=file.content_type,
                                   media_filename=(file.filename or "image")[:200], media_status="stored")
    return {"id": message_id}


@router.post("/{platform}/conversations/{psid}/bot", status_code=204)
async def toggle_bot(platform: str, psid: str, body: BotIn, claims: AccessClaims = Depends(current_claims),
                     pool: asyncpg.Pool = Depends(workspace_pool)):
    await _customer(pool, inbox_platform(platform), psid)
    await store.set_bot_active(pool, psid, body.active)


@router.post("/{platform}/conversations/{psid}/handoff/resolve", status_code=204)
async def resolve(platform: str, psid: str, body: ResolveIn, claims: AccessClaims = Depends(current_claims),
                  pool: asyncpg.Pool = Depends(workspace_pool)):
    await _customer(pool, inbox_platform(platform), psid)
    await store.resolve_handoff(pool, psid, resume_bot=body.resume_bot)


@router.patch("/{platform}/conversations/{psid}", status_code=204)
async def rename(platform: str, psid: str, body: RenameIn, claims: AccessClaims = Depends(current_claims),
                 pool: asyncpg.Pool = Depends(workspace_pool)):
    await _customer(pool, inbox_platform(platform), psid)
    await store.rename_customer(pool, psid, body.name)


@router.delete("/{platform}/conversations/{psid}/history")
async def clear_history(platform: str, psid: str, request: Request, claims: AccessClaims = Depends(current_claims),
                        pool: asyncpg.Pool = Depends(workspace_pool)):
    await _customer(pool, inbox_platform(platform), psid)
    result = await store.clear_history(pool, psid, str(claims.user_id))
    await audit(request, claims, "chat_history_cleared", platform=platform)
    return result


@router.delete("/{platform}/messages/{message_id}", status_code=204)
async def delete_message(platform: str, message_id: int, request: Request,
                         claims: AccessClaims = Depends(current_claims), pool: asyncpg.Pool = Depends(workspace_pool)):
    message = await lookups.get_message(pool, message_id)
    if message is None or message["platform"] != inbox_platform(platform):
        raise NotFound("Message not found.")
    await store.delete_message(pool, message_id, str(claims.user_id))
