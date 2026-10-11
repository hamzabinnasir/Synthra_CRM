"""/api/comments: list, reply publicly or privately, hide, delete (platform first), bulk delete.

Deleting always calls the platform API first and removes the database record only when the platform
confirmed it (or says the comment is already gone), so the dashboard never shows a comment as deleted
while it is still visible to the public.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

import asyncpg
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ..crm import store
from ..errors import NotFound
from ..platforms.meta import MetaClient, MetaError
from ..security.tokens import AccessClaims
from .common import (COMMENT_CHANNEL, audit, comment_platform, meta_errors, platform_client, safe_path_id, throttle,
                     workspace_pool)
from .deps import current_claims

router = APIRouter(prefix="/api/comments", tags=["comments"])


class ReplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=2000)
    mode: Literal["public", "private"] = "public"


class HideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hidden: bool = True


class BotIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active: bool


class IdsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: List[str] = Field(min_length=1, max_length=100)


async def _must_exist(pool: asyncpg.Pool, platform: str, comment_id: str) -> dict:
    comment = await store.get_comment(pool, platform, safe_path_id(comment_id))
    if comment is None:
        raise NotFound("Comment not found.")
    return comment


async def _delete_one(pool: asyncpg.Pool, client: MetaClient, platform: str, comment_id: str, actor: str) -> int:
    try:
        await client.delete_comment(comment_id)
    except MetaError as exc:
        if not exc.is_gone:
            raise                                       # keep the record: the comment still exists publicly
    return await store.delete_comment_records(pool, platform, comment_id, actor)


@router.get("/{platform}")
async def list_comments(platform: str, q: Optional[str] = Query(None, max_length=100), hidden: Optional[bool] = None,
                        unread: bool = False, classification: Optional[str] = Query(None, max_length=30),
                        before: Optional[datetime] = None, limit: int = Query(50, ge=1, le=100),
                        claims: AccessClaims = Depends(current_claims), pool: asyncpg.Pool = Depends(workspace_pool)):
    items = await store.list_comments(pool, comment_platform(platform), q=q, hidden=hidden, unread_only=unread,
                                      classification=classification, limit=limit, before=before)
    return {"comments": items}


@router.post("/{platform}/read")
async def mark_read(platform: str, body: IdsIn, claims: AccessClaims = Depends(current_claims),
                    pool: asyncpg.Pool = Depends(workspace_pool)):
    return {"updated": await store.mark_comments_read(pool, comment_platform(platform), body.ids)}


@router.post("/{platform}/bulk-delete")
async def bulk_delete(platform: str, body: IdsIn, request: Request, claims: AccessClaims = Depends(current_claims),
                      pool: asyncpg.Pool = Depends(workspace_pool)):
    platform = comment_platform(platform)
    await throttle(request, claims, "comment-delete", 1000)
    results, deleted = [], 0
    async with platform_client(request, claims, COMMENT_CHANNEL[platform]) as (client, _):
        for raw in dict.fromkeys(body.ids):
            try:
                await _delete_one(pool, client, platform, safe_path_id(raw), str(claims.user_id))
                results.append({"id": raw, "ok": True})
                deleted += 1
            except MetaError as exc:
                results.append({"id": raw, "ok": False, "error": exc.describe()})
                if exc.is_auth:
                    break                               # every further call would fail the same way
            except NotFound:
                results.append({"id": raw, "ok": False, "error": "Invalid comment id."})
    await audit(request, claims, "comments_bulk_deleted", platform=platform, requested=len(body.ids), deleted=deleted)
    return {"deleted": deleted, "results": results}


@router.post("/{platform}/{comment_id}/reply")
async def reply(platform: str, comment_id: str, body: ReplyIn, request: Request,
                claims: AccessClaims = Depends(current_claims), pool: asyncpg.Pool = Depends(workspace_pool)):
    platform = comment_platform(platform)
    await throttle(request, claims, "comment-reply", 600)
    comment = await _must_exist(pool, platform, comment_id)
    async with platform_client(request, claims, COMMENT_CHANNEL[platform]) as (client, _):
        with meta_errors():
            if body.mode == "private":
                await client.private_reply(comment_id, body.text)
            else:
                await client.public_reply(comment_id, body.text)
    await store.save_comment_result(pool, platform, comment_id, classification=comment.get("classification"),
                                    topic=comment.get("topic"), action=f"agent_{body.mode}", bot_reply=body.text)
    await store.mark_comments_read(pool, platform, [comment_id])
    return {"ok": True}


@router.post("/{platform}/{comment_id}/hide", status_code=204)
async def hide(platform: str, comment_id: str, body: HideIn, request: Request,
               claims: AccessClaims = Depends(current_claims), pool: asyncpg.Pool = Depends(workspace_pool)):
    platform = comment_platform(platform)
    await _must_exist(pool, platform, comment_id)
    async with platform_client(request, claims, COMMENT_CHANNEL[platform]) as (client, _):
        with meta_errors():
            await client.hide_comment(comment_id, body.hidden)
    await store.set_comment_hidden(pool, platform, comment_id, body.hidden)


@router.post("/{platform}/{comment_id}/bot", status_code=204)
async def toggle_bot(platform: str, comment_id: str, body: BotIn, claims: AccessClaims = Depends(current_claims),
                     pool: asyncpg.Pool = Depends(workspace_pool)):
    if not await store.set_comment_bot(pool, comment_page(platform), safe_path_id(comment_id), body.active):
        raise NotFound("Comment not found.")


def comment_page(platform: str) -> str:
    return comment_platform(platform)


@router.delete("/{platform}/{comment_id}", status_code=204)
async def delete(platform: str, comment_id: str, request: Request, claims: AccessClaims = Depends(current_claims),
                 pool: asyncpg.Pool = Depends(workspace_pool)):
    platform = comment_platform(platform)
    await throttle(request, claims, "comment-delete", 1000)
    await _must_exist(pool, platform, comment_id)
    async with platform_client(request, claims, COMMENT_CHANNEL[platform]) as (client, _):
        with meta_errors():
            await _delete_one(pool, client, platform, comment_id, str(claims.user_id))
    await audit(request, claims, "comment_deleted", platform=platform)
