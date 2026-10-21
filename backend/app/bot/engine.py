"""Bot engine: turns queued webhook events into stored conversations, replies and comment moderation.

One handler is registered per channel (see BotEngine.handlers). Everything is written for multi-tenancy:
credentials and the database pool are resolved per event, nothing is cached across workspaces.

Reliability rules
* Handlers are idempotent. A retried event never creates a second customer message, and a customer
  message that already has a bot/agent reply after it is not answered twice.
* Retryable failures (rate limits, network, provider down) are raised so the queue retries with backoff.
  Permanent failures raise PermanentError so the event is buried instead of retried forever.
* Nothing from a payload, token or provider error text is logged or stored in an error message.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from uuid import UUID

import asyncpg
import httpx

from ..channels.registry import CHANNELS, Channel
from ..channels.service import ChannelService
from ..config import Settings
from ..crm import lookups, store
from ..jobs.queue import MAX_ATTEMPTS, QueuedEvent
from ..jobs.worker import PermanentError
from ..kb.service import KnowledgeBase
from ..llm import client as llm
from ..llm.budget import LLMBudget
from ..platforms.factory import build_client, llm_config
from ..platforms.meta import TIMEOUT, MetaClient, MetaError
from ..tenantdb.provisioner import DatabaseSetupError
from ..tenantdb.service import TenantDatabaseService
from . import parsing, prompts
from . import text as textlib

log = logging.getLogger("app.bot")

Handler = Callable[[QueuedEvent], Awaitable[None]]
_PLACEHOLDER = re.compile(r"\[[A-Za-z ]+\]")
MAX_BOT_REPLIES_PER_THREAD = 5      # safety stop for nested comment threads


@dataclass
class Ctx:
    tenant_id: UUID
    channel: Channel
    creds: Dict[str, Any]
    pool: asyncpg.Pool
    client: MetaClient
    attempts: int

    @property
    def behaviour(self) -> Dict[str, str]:
        return self.creds["behaviour"]

    @property
    def platform(self) -> str:
        return self.channel.platform

    @property
    def account_id(self) -> str:
        return str(self.creds["values"].get(self.channel.account_field, ""))


class BotEngine:
    def __init__(self, *, control_pool: asyncpg.Pool, channels: ChannelService, tenant_db: TenantDatabaseService,
                 kb: KnowledgeBase, budget: LLMBudget, settings: Settings,
                 http: Optional[httpx.AsyncClient] = None) -> None:
        self._control, self._channels, self._tenant_db = control_pool, channels, tenant_db
        self._kb, self._budget, self._settings = kb, budget, settings
        self.http = http or httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False)
        self._owns_http = http is None

    async def aclose(self) -> None:
        if self._owns_http:
            await self.http.aclose()

    def handlers(self) -> Dict[str, Handler]:
        return {channel_id: self._make_handler(channel_id) for channel_id in CHANNELS}

    def _make_handler(self, channel_id: str) -> Handler:
        async def handle(event: QueuedEvent) -> None:
            await self.handle(channel_id, event)
        return handle

    # ---- entry point -------------------------------------------------------------------------
    async def handle(self, channel_id: str, event: QueuedEvent) -> None:
        channel = CHANNELS[channel_id]
        creds = await self._channels.credentials(event.tenant_id, channel_id)
        if creds is None or not creds["enabled"]:
            return                                     # switched off after the event was queued
        try:
            pool = await self._tenant_db.pool(event.tenant_id)
        except DatabaseSetupError as exc:
            if exc.code == "not_connected":
                raise PermanentError("tenant database disconnected")
            raise                                      # not migrated yet: retry, the owner may be finishing setup
        client = build_client(self._settings, channel, creds, http=self.http)
        if client is None:
            raise PermanentError("channel credentials incomplete")
        ctx = Ctx(event.tenant_id, channel, creds, pool, client, event.attempts)
        try:
            if channel.kind == "inbox":
                await self._inbox(ctx, event)
            else:
                await self._comments(ctx, event)
        except MetaError as exc:
            await self._meta_failure(ctx, exc)

    async def _meta_failure(self, ctx: Ctx, exc: MetaError) -> None:
        if exc.is_auth:
            await self._flag_channel(ctx, exc.describe())
            raise PermanentError(exc.describe())
        if exc.retryable and ctx.attempts < MAX_ATTEMPTS:
            raise exc
        raise PermanentError(exc.describe())

    async def _flag_channel(self, ctx: Ctx, message: str) -> None:
        await self._control.execute(
            "update channel_configs set status = 'error', last_error = $3, last_tested_at = $4 "
            "where tenant_id = $1 and channel = $2", ctx.tenant_id, ctx.channel.id, message[:480], datetime.now(timezone.utc))
        await self._notify_once(ctx, "channel_error", ctx.channel.id, message, minutes=60)

    async def _notify_once(self, ctx: Ctx, type_: str, target: str, preview: str, *, minutes: int = 60) -> None:
        try:
            if await lookups.has_recent_notification(ctx.pool, type_, target, minutes):
                return
            await store.create_notification(ctx.pool, type=type_, platform=ctx.platform, source_type="system",
                                            target_id=target, message_preview=preview,
                                            metadata={"channel": ctx.channel.id})
        except Exception as exc:                        # a notification must never fail the event
            log.warning("notify_failed", extra={"error": type(exc).__name__})

    # ---- inbox ---------------------------------------------------------------------------------
    async def _inbox(self, ctx: Ctx, event: QueuedEvent) -> None:
        if event.kind == "status":
            return
        if event.kind == "unsend":
            mid = parsing.unsent_mid(event.data)
            message_id = await lookups.message_id_by_mid(ctx.pool, ctx.platform, mid) if mid else None
            if message_id:
                await store.delete_message(ctx.pool, message_id, "customer")
            return
        if ctx.platform == "whatsapp":
            inbound = parsing.parse_whatsapp(event.data)
        else:
            inbound = parsing.parse_messaging(ctx.platform, ctx.account_id, event.data)
        if inbound is None:
            return
        if inbound.is_echo:
            await self._echo(ctx, inbound)
        else:
            await self._customer_message(ctx, inbound, event)

    async def _echo(self, ctx: Ctx, inbound: parsing.Inbound) -> None:
        """A message sent BY the business account. Ours are already stored; anything else is a person."""
        if inbound.mid and await store.message_exists(ctx.pool, ctx.platform, inbound.mid):
            return
        await asyncio.sleep(2.0)                        # our own send may still be recording its message
        if inbound.mid and await store.message_exists(ctx.pool, ctx.platform, inbound.mid):
            return
        psid = store.psid_for(ctx.platform, inbound.external_id)
        await store.upsert_customer(ctx.pool, psid, ctx.platform)
        inserted = await store.insert_message(ctx.pool, psid=psid, platform=ctx.platform, sender="agent",
                                              text=inbound.text, mid=inbound.mid, created_at=inbound.created_at)
        if inserted and ctx.behaviour.get("pause_on_human") == "true":
            await store.set_bot_active(ctx.pool, psid, False)
            await store.log_activity(ctx.pool, "bot_paused_human_reply", {"platform": ctx.platform})

    async def _customer_message(self, ctx: Ctx, inbound: parsing.Inbound, event: QueuedEvent) -> None:
        pool, platform = ctx.pool, ctx.platform
        psid = store.psid_for(platform, inbound.external_id)
        existing = await lookups.message_id_by_mid(pool, platform, inbound.mid) if inbound.mid else None
        fresh = existing is None
        if not fresh and (event.attempts <= 1 or await lookups.reply_exists_after(pool, psid, existing)):
            return                                      # a real duplicate, or already answered
        customer = await store.upsert_customer(pool, psid, platform, name=inbound.name, username=inbound.username)
        if fresh:
            customer = await self._enrich_profile(ctx, psid, inbound, customer)
            media = await self._store_media(ctx, inbound)
            message_id = await store.insert_message(
                pool, psid=psid, platform=platform, sender="customer", text=inbound.text, mid=inbound.mid,
                created_at=inbound.created_at, **media)
            if message_id is None:
                return                                  # suppressed (history cleared or message deleted)
            customer = await store.get_customer(pool, psid) or customer
            if customer.get("unread_count") == 1:
                await store.create_notification(
                    pool, type="new_message", platform=platform, source_type="conversation", target_id=psid,
                    customer_id=psid, customer_name=customer.get("name"), customer_username=customer.get("username"),
                    message_preview=inbound.text)
        await self._maybe_reply(ctx, inbound, psid, customer)

    async def _enrich_profile(self, ctx: Ctx, psid: str, inbound: parsing.Inbound,
                              customer: Dict[str, Any]) -> Dict[str, Any]:
        if ctx.platform == "whatsapp":
            return customer
        name = customer.get("name") or ""
        if name and not name.isdigit():
            return customer
        profile = await ctx.client.profile(inbound.external_id)
        if not profile:
            return customer
        return await store.upsert_customer(ctx.pool, psid, ctx.platform, name=profile.get("name"),
                                           username=profile.get("username"),
                                           profile_pic_url=profile.get("profile_pic_url")) or customer

    async def _store_media(self, ctx: Ctx, inbound: parsing.Inbound) -> Dict[str, Optional[str]]:
        """Copy inbound media into the workspace database now: Meta's URLs expire within minutes."""
        media = inbound.media
        if media is None:
            return {}
        base: Dict[str, Optional[str]] = {"media_type": media.type, "media_mime_type": media.mime,
                                          "media_filename": media.filename}
        try:
            url, mime = media.url, media.mime
            if media.media_id:
                info = await ctx.client.whatsapp_media(media.media_id)
                url, mime = info.get("url"), info.get("mime_type") or mime
            if not url:
                raise ValueError("no media url")
            data, fetched_mime = await ctx.client.download(url, self._settings.max_media_bytes)
            mime = mime or fetched_mime
            media_id = await store.save_media(ctx.pool, data, mime or "application/octet-stream", media.filename)
            return {**base, "media_url": f"media:{media_id}", "media_mime_type": mime, "media_status": "stored"}
        except (MetaError, ValueError, httpx.HTTPError):
            return {**base, "media_status": "failed"}

    async def _maybe_reply(self, ctx: Ctx, inbound: parsing.Inbound, psid: str, customer: Dict[str, Any]) -> None:
        if not ctx.creds["bot_enabled"] or not customer.get("bot_active", True):
            return
        text, external = inbound.text, inbound.external_id
        if textlib.is_human_handoff_request(text):
            await self._send_bot(ctx, psid, external, ctx.behaviour.get("handoff_message") or prompts.DEFAULT_HANDOFF_MESSAGE,
                                 inbound.created_at)
            await self._request_handoff(ctx, psid, customer, "requested")
            return
        media_only = bool(_PLACEHOLDER.fullmatch(text)) or bool(
            inbound.media and inbound.media.filename and text == inbound.media.filename)
        if media_only:
            await self._send_bot(ctx, psid, external, prompts.MEDIA_ONLY_REPLY, inbound.created_at)
            return
        small = textlib.small_talk_reply(text)
        if small:
            await self._send_bot(ctx, psid, external, small, inbound.created_at)
            return
        reply, handoff = await self._answer(ctx, psid, text)
        if handoff and not reply:
            reply = ctx.behaviour.get("handoff_message") or prompts.DEFAULT_HANDOFF_MESSAGE
        await self._send_bot(ctx, psid, external, reply, inbound.created_at)
        if handoff:
            await self._request_handoff(ctx, psid, customer, "bot_unsure")

    async def _answer(self, ctx: Ctx, psid: str, latest: str) -> Tuple[str, bool]:
        """Returns (reply, wants_handoff). Falls back to the canned message when the AI is unavailable."""
        fallback = ctx.behaviour.get("fallback_message") or prompts.DEFAULT_FALLBACK_MESSAGE
        cfg = llm_config(ctx.creds)
        if not cfg.api_key:
            await self._notify_once(ctx, "bot_error", ctx.channel.id,
                                    "The bot is on but no AI key is saved for this channel.")
            return fallback, False
        if not await self._budget.take(ctx.tenant_id):
            await self._notify_once(ctx, "bot_error", ctx.channel.id, "The daily AI call limit has been reached.")
            return fallback, False
        context = await self._retrieve(ctx, latest)
        history = await store.recent_history(ctx.pool, psid, 11)
        if history and history[-1]["sender"] == "customer" and history[-1]["text"] == latest:
            history = history[:-1]                      # the latest message is passed separately
        try:
            raw = await llm.complete(cfg, prompts.build_system_prompt(ctx.behaviour, kind="inbox"),
                                     prompts.build_inbox_prompt(history, context, latest),
                                     timeout=self._settings.llm_timeout_seconds)
        except llm.LLMError as exc:
            if exc.retryable and ctx.attempts < MAX_ATTEMPTS:
                raise
            await self._notify_once(ctx, "bot_error", ctx.channel.id, exc.message)
            return fallback, False
        handoff = prompts.HANDOFF_MARKER in raw
        return textlib.tidy_reply(raw.replace(prompts.HANDOFF_MARKER, "")), handoff

    async def _retrieve(self, ctx: Ctx, query: str) -> List[str]:
        try:
            return await self._kb.retrieve(ctx.pool, query)
        except Exception as exc:                        # no knowledge base must not stop the bot answering
            log.warning("kb_retrieve_failed", extra={"error": type(exc).__name__})
            return []

    async def _send_bot(self, ctx: Ctx, psid: str, external_id: str, text: str,
                        received_at: Optional[datetime] = None) -> None:
        text = (text or "").strip()
        if not text:
            return
        mid = await ctx.client.send_text(external_id, text)
        await store.insert_message(ctx.pool, psid=psid, platform=ctx.platform, sender="bot", text=text, mid=mid)
        if received_at:
            seconds = (datetime.now(timezone.utc) - received_at).total_seconds()
            if 0 <= seconds <= 3600:
                await store.record_metric(ctx.pool, "response_time", ctx.platform, round(seconds, 2))

    async def _request_handoff(self, ctx: Ctx, psid: str, customer: Dict[str, Any], reason: str) -> None:
        await store.request_handoff(ctx.pool, psid)
        await store.record_metric(ctx.pool, "handoff", ctx.platform)
        await store.create_notification(
            ctx.pool, type="handoff", platform=ctx.platform, source_type="conversation", target_id=psid,
            customer_id=psid, customer_name=customer.get("name"), customer_username=customer.get("username"),
            message_preview=customer.get("last_message_text") or "", metadata={"reason": reason})

    # ---- comments ------------------------------------------------------------------------------
    async def _comments(self, ctx: Ctx, event: QueuedEvent) -> None:
        pool, platform = ctx.pool, ctx.platform
        if event.kind == "comment_removed":
            comment_id = parsing.comment_id_from_removal(platform, event.data)
            if comment_id:
                await store.delete_comment_records(pool, platform, comment_id, "platform", "comment_removed_on_platform")
            return
        parsed = parsing.parse_comment(platform, event.data)
        if parsed is None or (parsed.from_id and parsed.from_id == ctx.account_id):
            return                                      # unusable, or our own reply
        inserted = await store.insert_comment(
            pool, platform=platform, comment_id=parsed.comment_id, post_id=parsed.post_id, parent_id=parsed.parent_id,
            from_id=parsed.from_id, from_name=parsed.from_name, from_username=parsed.from_username,
            message=parsed.message, created_at=parsed.created_at)
        if inserted:
            who = parsed.from_name or parsed.from_username
            await store.create_notification(
                pool, type="new_comment", platform=platform, source_type="comment", target_id=parsed.comment_id,
                customer_id=parsed.from_id, customer_name=who, customer_username=parsed.from_username,
                message_preview=parsed.message)
        else:
            existing = await store.get_comment(pool, platform, parsed.comment_id)
            if existing is None or existing.get("action"):
                return                                  # deleted before, or already processed
        await self._process_comment(ctx, parsed)

    async def _process_comment(self, ctx: Ctx, parsed: parsing.ParsedComment) -> None:
        pool, platform, behaviour = ctx.pool, ctx.platform, ctx.behaviour
        cid = parsed.comment_id

        async def record(action: str, verdict: Optional[Dict[str, Any]] = None, reply: Optional[str] = None,
                         hidden: Optional[bool] = None) -> None:
            verdict = verdict or {}
            await store.save_comment_result(pool, platform, cid, classification=verdict.get("classification"),
                                            topic=verdict.get("topic"), action=action, bot_reply=reply, hidden=hidden)

        if not ctx.creds["bot_enabled"]:
            await record("bot_off")
            return
        if parsed.from_id:
            commenter = await store.get_customer(pool, store.psid_for(platform, parsed.from_id))
            if commenter and not commenter.get("comment_bot_active", True):
                await record("skipped_customer_off")
                return
        verdict = await self._classify(ctx, parsed.message)
        tone = verdict["classification"]
        mode = None
        if tone == "spam":
            mode = behaviour.get("spam_action", "none")
        elif tone == "abusive" or verdict["severe"]:
            mode = behaviour.get("abuse_action", "none")
        if mode in ("hide", "delete"):
            outcome = await self._moderate(ctx, parsed, verdict, mode)
            if outcome in ("hidden", "deleted"):
                return
            await record("moderation_failed", verdict)
            return
        is_reply = bool(parsed.parent_id and parsed.parent_id != parsed.post_id)
        if tone == "spam" or not parsed.message.strip():
            await record("recorded", verdict)
            return
        if is_reply and not await self._nested_reply_wanted(ctx, parsed):
            await record("recorded", verdict)            # chatter between customers: stored, never answered
            return
        private = bool(verdict["private"])
        reply = await self._comment_reply(ctx, parsed, tone, private)
        done = await self._send_comment_reply(ctx, parsed, reply, tone, private)
        await record(done[0], verdict, done[1])

    async def _nested_reply_wanted(self, ctx: Ctx, parsed: parsing.ParsedComment) -> bool:
        """Decide whether a comment inside a thread is aimed at us. Customer-to-customer chatter is ignored.

        Meta flattens threads to two levels, so a reply to OUR reply arrives with the top-level comment as its
        parent. We answer when any of these holds:
          1. the comment mentions our account;
          2. the top-level comment was written by our account;
          3. the comment right before it in the thread is ours and the commenter was already in the thread.
        Our own replies are not stored locally, so the thread is read from the platform. A hard cap on our replies
        per thread stops endless back-and-forth (for example with another bot).
        """
        client, parent = ctx.client, parsed.parent_id
        if not parent:
            return False
        own_id = ctx.account_id
        try:
            own_name = await client.own_username()
            if own_name and re.search(rf"(?<![\w.])@{re.escape(own_name)}(?![\w.])", parsed.message.casefold()):
                mention = True
            else:
                mention = await client.comment_tags_us(parsed.comment_id)
            top = await client.comment_author(parent)
            thread = await client.thread_replies(parent)
        except MetaError as exc:
            if exc.is_auth or (exc.retryable and ctx.attempts < MAX_ATTEMPTS):
                raise                                   # let the queue retry; do not silently skip
            return False

        def is_us(author_id: Optional[str], username: Optional[str]) -> bool:
            return bool((author_id and author_id == own_id) or (own_name and username and username == own_name))

        def is_commenter(author_id: Optional[str], username: Optional[str]) -> bool:
            if parsed.from_id and author_id:
                return author_id == parsed.from_id
            mine = (parsed.from_username or "").lstrip("@").casefold()
            return bool(mine and username and username == mine)

        ours = sum(1 for r in thread if is_us(r["author_id"], r["username"]))
        if ours >= MAX_BOT_REPLIES_PER_THREAD:
            return False
        if mention or is_us(top["id"], top["username"]):
            return True
        position = next((i for i, r in enumerate(thread) if r["id"] == parsed.comment_id), len(thread))
        before = thread[:position]
        if not before or not is_us(before[-1]["author_id"], before[-1]["username"]):
            return False
        return is_commenter(top["id"], top["username"]) or any(
            is_commenter(r["author_id"], r["username"]) for r in before)

    async def _classify(self, ctx: Ctx, message: str) -> Dict[str, Any]:
        if not message.strip() or textlib.looks_like_spam(message):
            return textlib.heuristic_classification(message)
        cfg = llm_config(ctx.creds)
        if not cfg.api_key or not await self._budget.take(ctx.tenant_id):
            return textlib.heuristic_classification(message)
        try:
            raw = await llm.complete(cfg, prompts.CLASSIFY_SYSTEM, prompts.build_classify_prompt(message),
                                     timeout=self._settings.llm_timeout_seconds, retries=1)
        except llm.LLMError:
            return textlib.heuristic_classification(message)
        return textlib.parse_classification(raw, message)

    async def _comment_reply(self, ctx: Ctx, parsed: parsing.ParsedComment, tone: str, private: bool) -> str:
        canned = prompts.canned_comment_reply(tone, private=private, platform=ctx.platform)
        if tone in ("abusive", "off_topic"):
            return canned
        cfg = llm_config(ctx.creds)
        if not cfg.api_key or not await self._budget.take(ctx.tenant_id):
            return canned
        context = await self._retrieve(ctx, parsed.message)
        try:
            raw = await llm.complete(
                cfg, prompts.build_system_prompt(ctx.behaviour, kind="comment"),
                prompts.build_comment_prompt(parsed.message, tone, context, private=private, platform=ctx.platform),
                timeout=self._settings.llm_timeout_seconds, retries=1)
        except llm.LLMError:
            return canned
        if prompts.HANDOFF_MARKER in raw:
            return canned                               # the model could not answer from the business information
        return textlib.tidy_reply(raw, 900) or canned

    async def _send_comment_reply(self, ctx: Ctx, parsed: parsing.ParsedComment, reply: str, tone: str,
                                  private: bool) -> Tuple[str, str]:
        """Returns (action, text actually sent). Private replies fall back to a public pointer to DM."""
        cid = parsed.comment_id
        try:
            if private:
                mid = await ctx.client.private_reply(cid, reply)
                await self._log_private_reply(ctx, parsed, reply, mid)
                return "replied_private", reply
            await ctx.client.public_reply(cid, reply)
            return "replied_public", reply
        except MetaError as exc:
            if exc.is_auth or (exc.retryable and ctx.attempts < MAX_ATTEMPTS):
                raise
            if not private:
                return "reply_failed", ""
        pointer = prompts.canned_comment_reply(tone, private=False, platform=ctx.platform)
        try:
            await ctx.client.public_reply(cid, pointer)
            return "replied_public", pointer
        except MetaError as exc:
            if exc.is_auth:
                raise
            return "reply_failed", ""

    async def _log_private_reply(self, ctx: Ctx, parsed: parsing.ParsedComment, reply: str, mid: Optional[str]) -> None:
        """Show the private reply in the conversation view so the later DM thread has context."""
        if not parsed.from_id:
            return
        try:
            psid = store.psid_for(ctx.platform, parsed.from_id)
            await store.upsert_customer(ctx.pool, psid, ctx.platform, name=parsed.from_name,
                                        username=parsed.from_username)
            await store.insert_message(ctx.pool, psid=psid, platform=ctx.platform, sender="bot", text=reply,
                                       mid=mid, comment_id=parsed.comment_id)
        except Exception as exc:
            log.warning("private_reply_log_failed", extra={"error": type(exc).__name__})

    async def _moderate(self, ctx: Ctx, parsed: parsing.ParsedComment, verdict: Dict[str, Any], mode: str) -> str:
        pool, platform, cid = ctx.pool, ctx.platform, parsed.comment_id
        label = verdict.get("classification", "")
        if mode == "delete":
            try:
                await ctx.client.delete_comment(cid)
            except MetaError as exc:
                if exc.is_auth:
                    raise
                if not exc.is_gone:
                    mode = "hide"                       # could not delete: hiding is the next best protection
            if mode == "delete":
                await store.delete_comment_records(pool, platform, cid, "bot", "comment_deleted_by_bot")
                await self._moderation_note(ctx, parsed, label, "deleted")
                return "deleted"
        try:
            await ctx.client.hide_comment(cid, True)
        except MetaError as exc:
            if exc.is_auth:
                raise
            return "failed"
        await store.save_comment_result(pool, platform, cid, classification=label, topic=verdict.get("topic"),
                                        action="hidden", hidden=True)
        await self._moderation_note(ctx, parsed, label, "hidden")
        return "hidden"

    async def _moderation_note(self, ctx: Ctx, parsed: parsing.ParsedComment, label: str, outcome: str) -> None:
        await store.create_notification(
            ctx.pool, type="moderation", platform=ctx.platform, source_type="comment", target_id=parsed.comment_id,
            customer_id=parsed.from_id, customer_name=parsed.from_name or parsed.from_username,
            customer_username=parsed.from_username, message_preview=parsed.message,
            metadata={"classification": label, "outcome": outcome})
        await store.log_activity(ctx.pool, f"comment_{outcome}_by_bot", {"platform": ctx.platform, "class": label})
