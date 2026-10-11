"""All queries against a workspace's OWN database (the `crm` schema). Every function takes the pool that
belongs to the calling workspace, so tenant isolation is physical: there is no tenant_id column to forget.

Writes go through the schema's guards: message inserts dedupe on (platform, mid) and are suppressed after
a history clear or a delete; comment inserts are suppressed for tombstoned (deleted) comments.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID

import asyncpg

PLATFORMS = ("whatsapp", "facebook", "instagram")
_PREFIX = {"instagram": "ig_", "whatsapp": "wa_"}


def psid_for(platform: str, external_id: Any) -> str:
    value, prefix = str(external_id), _PREFIX.get(platform)
    return f"{prefix}{value}" if prefix and not value.startswith(prefix) else value


def external_id(psid: str, platform: str) -> str:
    prefix = _PREFIX.get(platform)
    return psid[len(prefix):] if prefix and psid.startswith(prefix) else psid


def _value(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, str) and False:
        return v
    return v


def row(record: Optional[asyncpg.Record]) -> Optional[Dict[str, Any]]:
    return None if record is None else {k: _value(v) for k, v in dict(record).items()}


def rows(records: Sequence[asyncpg.Record]) -> List[Dict[str, Any]]:
    return [row(r) for r in records]  # type: ignore[misc]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------------------------------------
# Customers and conversations
# ------------------------------------------------------------------------------------------------
async def upsert_customer(pool: asyncpg.Pool, psid: str, platform: str, *, name: Optional[str] = None,
                          username: Optional[str] = None, profile_pic_url: Optional[str] = None) -> Dict[str, Any]:
    """Create the customer or enrich it. A real name is never overwritten; a numeric placeholder is."""
    record = await pool.fetchrow(
        "insert into crm.customers (psid, platform, name, username, profile_pic_url) values ($1, $2, $3, $4, $5) "
        "on conflict (psid) do update set "
        " name = case when crm.customers.name is null or crm.customers.name ~ '^[0-9]+$' "
        "        then coalesce(excluded.name, crm.customers.name) else crm.customers.name end, "
        " username = coalesce(crm.customers.username, excluded.username), "
        " profile_pic_url = coalesce(excluded.profile_pic_url, crm.customers.profile_pic_url) "
        "returning *", psid, platform, (name or None), (username or None), (profile_pic_url or None))
    return row(record)  # type: ignore[return-value]


async def get_customer(pool: asyncpg.Pool, psid: str) -> Optional[Dict[str, Any]]:
    return row(await pool.fetchrow("select * from crm.customers where psid = $1", psid))


_CONV_COLUMNS = (
    "c.psid, c.platform, c.name, c.custom_name, c.username, c.profile_pic_url, c.bot_active, c.comment_bot_active, "
    "c.handoff_requested, c.handoff_at, c.last_message_text, c.last_customer_message_at, c.last_activity_at, "
    "c.unread_count, coalesce(nullif(c.custom_name, ''), nullif(c.name, ''), nullif(c.username, ''), c.psid) as display_name")


async def list_conversations(pool: asyncpg.Pool, platform: Optional[str] = None, *, q: Optional[str] = None,
                             unread_only: bool = False, handoff_only: bool = False, limit: int = 50,
                             before: Optional[datetime] = None) -> List[Dict[str, Any]]:
    like = f"%{q.strip().replace('%', '').replace('_', '')}%" if q and q.strip() else None
    records = await pool.fetch(
        f"select {_CONV_COLUMNS} from crm.customers c "
        "where (c.last_message_text is not null or c.history_cleared_at is not null) "
        " and ($1::text is null or c.platform = $1) "
        " and ($2::text is null or c.name ilike $2 or c.custom_name ilike $2 or c.username ilike $2 or c.psid ilike $2 "
        "      or c.last_message_text ilike $2) "
        " and (not $3 or c.unread_count > 0) and (not $4 or c.handoff_requested) "
        " and ($5::timestamptz is null or c.last_activity_at < $5) "
        "order by c.last_activity_at desc limit $6",
        platform, like, unread_only, handoff_only, before, max(1, min(limit, 100)))
    return rows(records)


async def get_messages(pool: asyncpg.Pool, psid: str, *, limit: int = 100, before_id: Optional[int] = None) -> List[Dict[str, Any]]:
    records = await pool.fetch(
        "select id, psid, platform, sender, text, comment_id, mid, media_url, media_type, media_mime_type, "
        "media_filename, media_status, created_at from crm.messages "
        "where psid = $1 and ($2::bigint is null or id < $2) order by id desc limit $3",
        psid, before_id, max(1, min(limit, 200)))
    return rows(list(reversed(records)))


async def recent_history(pool: asyncpg.Pool, psid: str, limit: int = 10) -> List[Dict[str, str]]:
    records = await pool.fetch("select sender, text from crm.messages where psid = $1 and text <> '' "
                               "order by id desc limit $2", psid, limit)
    return [{"sender": r["sender"], "text": r["text"]} for r in reversed(records)]


async def insert_message(pool: asyncpg.Pool, *, psid: str, platform: str, sender: str, text: str,
                         mid: Optional[str] = None, comment_id: Optional[str] = None,
                         created_at: Optional[datetime] = None, media_url: Optional[str] = None,
                         media_type: Optional[str] = None, media_mime_type: Optional[str] = None,
                         media_filename: Optional[str] = None, media_status: Optional[str] = None) -> Optional[int]:
    """Returns the new message id, or None when it was a duplicate or suppressed (cleared/deleted history)."""
    return await pool.fetchval(
        "insert into crm.messages (psid, platform, sender, text, mid, comment_id, created_at, media_url, media_type, "
        "media_mime_type, media_filename, media_status) values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12) "
        "on conflict (platform, mid) where mid is not null do nothing returning id",
        psid, platform, sender, text or "", mid, comment_id, created_at or _now(), media_url, media_type,
        media_mime_type, media_filename, media_status)


async def message_exists(pool: asyncpg.Pool, platform: str, mid: str) -> bool:
    return bool(await pool.fetchval("select 1 from crm.messages where platform = $1 and mid = $2", platform, mid))


async def mark_read(pool: asyncpg.Pool, psid: str) -> None:
    await pool.execute("select crm.mark_conversation_read($1)", psid)


async def set_bot_active(pool: asyncpg.Pool, psid: str, active: bool) -> bool:
    return (await pool.execute("update crm.customers set bot_active = $2 where psid = $1", psid, active)) == "UPDATE 1"


async def request_handoff(pool: asyncpg.Pool, psid: str) -> None:
    await pool.execute("update crm.customers set bot_active = false, handoff_requested = true, "
                       "handoff_at = coalesce(handoff_at, now()) where psid = $1", psid)


async def resolve_handoff(pool: asyncpg.Pool, psid: str, *, resume_bot: bool) -> bool:
    return (await pool.execute(
        "update crm.customers set handoff_requested = false, handoff_at = null, bot_active = case when $2 then true else bot_active end "
        "where psid = $1", psid, resume_bot)) == "UPDATE 1"


async def rename_customer(pool: asyncpg.Pool, psid: str, custom_name: Optional[str]) -> bool:
    return (await pool.execute("update crm.customers set custom_name = $2 where psid = $1", psid,
                               (custom_name or "").strip()[:100] or None)) == "UPDATE 1"


async def set_comment_bot_for_customer(pool: asyncpg.Pool, psid: str, active: bool) -> None:
    await pool.execute("update crm.customers set comment_bot_active = $2 where psid = $1", psid, active)


async def clear_history(pool: asyncpg.Pool, psid: str, actor: str) -> Dict[str, Any]:
    result = await pool.fetchval("select crm.clear_conversation_history($1, $2)", psid, actor)
    return json.loads(result) if isinstance(result, str) else dict(result)


async def delete_message(pool: asyncpg.Pool, message_id: int, actor: str) -> int:
    return await pool.fetchval("select crm.delete_single_message($1, $2)", message_id, actor)


# ------------------------------------------------------------------------------------------------
# Comments
# ------------------------------------------------------------------------------------------------
async def get_comment(pool: asyncpg.Pool, platform: str, comment_id: str) -> Optional[Dict[str, Any]]:
    return row(await pool.fetchrow("select * from crm.comments where platform = $1 and comment_id = $2", platform, comment_id))


async def insert_comment(pool: asyncpg.Pool, *, platform: str, comment_id: str, post_id: Optional[str],
                         parent_id: Optional[str], from_id: Optional[str], from_name: Optional[str],
                         from_username: Optional[str], message: str, created_at: Optional[datetime] = None) -> bool:
    """False when the comment already exists or was deleted before (tombstone)."""
    got = await pool.fetchval(
        "insert into crm.comments (platform, comment_id, post_id, parent_id, from_id, from_name, from_username, message, created_at) "
        "values ($1, $2, $3, $4, $5, $6, $7, $8, $9) on conflict (platform, comment_id) do nothing returning comment_id",
        platform, comment_id, post_id, parent_id, from_id, from_name, from_username, message or "", created_at or _now())
    return got is not None


async def save_comment_result(pool: asyncpg.Pool, platform: str, comment_id: str, *, classification: Optional[str],
                              topic: Optional[str], action: str, bot_reply: Optional[str] = None,
                              hidden: Optional[bool] = None) -> None:
    await pool.execute(
        "update crm.comments set classification = $3, topic = $4, action = $5, "
        "bot_reply = coalesce($6, bot_reply), hidden = coalesce($7, hidden) where platform = $1 and comment_id = $2",
        platform, comment_id, classification, topic, action, bot_reply, hidden)


async def list_comments(pool: asyncpg.Pool, platform: Optional[str] = None, *, q: Optional[str] = None,
                        hidden: Optional[bool] = None, unread_only: bool = False, classification: Optional[str] = None,
                        limit: int = 50, before: Optional[datetime] = None) -> List[Dict[str, Any]]:
    like = f"%{q.strip().replace('%', '').replace('_', '')}%" if q and q.strip() else None
    records = await pool.fetch(
        "select platform, comment_id, post_id, parent_id, from_id, from_name, from_username, message, classification, "
        "topic, action, bot_reply, bot_active, hidden, is_read, created_at from crm.comments "
        "where ($1::text is null or platform = $1) and ($2::text is null or message ilike $2 or from_name ilike $2 or from_username ilike $2) "
        " and ($3::boolean is null or hidden = $3) and (not $4 or is_read = false) "
        " and ($5::text is null or classification = $5) and ($6::timestamptz is null or created_at < $6) "
        "order by created_at desc limit $7",
        platform, like, hidden, unread_only, classification, before, max(1, min(limit, 100)))
    return rows(records)


async def mark_comments_read(pool: asyncpg.Pool, platform: str, ids: Sequence[str]) -> int:
    done = await pool.execute("update crm.comments set is_read = true where platform = $1 and comment_id = any($2::text[])",
                              platform, list(ids))
    return int(done.split()[-1])


async def set_comment_hidden(pool: asyncpg.Pool, platform: str, comment_id: str, hidden: bool) -> None:
    await pool.execute("update crm.comments set hidden = $3 where platform = $1 and comment_id = $2", platform, comment_id, hidden)


async def set_comment_bot(pool: asyncpg.Pool, platform: str, comment_id: str, active: bool) -> bool:
    return (await pool.execute("update crm.comments set bot_active = $3 where platform = $1 and comment_id = $2",
                               platform, comment_id, active)) == "UPDATE 1"


async def delete_comment_records(pool: asyncpg.Pool, platform: str, comment_id: str, actor: str,
                                 action_type: str = "comment_deleted") -> int:
    """Remove the rows and tombstone the id. Call ONLY after the platform confirmed the delete."""
    return await pool.fetchval("select crm.delete_comment_records($1, $2, $3, $4)", comment_id, platform, actor, action_type)


# ------------------------------------------------------------------------------------------------
# Notifications, activity, media
# ------------------------------------------------------------------------------------------------
async def create_notification(pool: asyncpg.Pool, *, type: str, platform: str, source_type: str, target_id: str,
                              customer_id: Optional[str] = None, customer_name: Optional[str] = None,
                              customer_username: Optional[str] = None, message_preview: str = "",
                              metadata: Optional[Dict[str, Any]] = None) -> None:
    await pool.execute(
        "insert into crm.notifications (type, platform, source_type, customer_id, customer_name, customer_username, "
        "target_id, message_preview, metadata) values ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)",
        type, platform, source_type, customer_id, customer_name, customer_username, target_id,
        (message_preview or "")[:300], json.dumps(metadata or {}))


async def list_notifications(pool: asyncpg.Pool, *, unread_only: bool = False, limit: int = 50,
                             before_id: Optional[int] = None) -> List[Dict[str, Any]]:
    records = await pool.fetch(
        "select id, type, platform, source_type, customer_id, customer_name, customer_username, target_id, "
        "message_preview, is_read, metadata, created_at from crm.notifications "
        "where (not $1 or is_read = false) and ($2::bigint is null or id < $2) order by id desc limit $3",
        unread_only, before_id, max(1, min(limit, 100)))
    out = rows(records)
    for item in out:
        if isinstance(item.get("metadata"), str):
            item["metadata"] = json.loads(item["metadata"])
    return out


async def mark_notifications_read(pool: asyncpg.Pool, ids: Optional[Sequence[int]] = None) -> int:
    if ids is None:
        done = await pool.execute("update crm.notifications set is_read = true where is_read = false")
    else:
        done = await pool.execute("update crm.notifications set is_read = true where id = any($1::bigint[]) and is_read = false", list(ids))
    return int(done.split()[-1])


async def unread_notification_count(pool: asyncpg.Pool) -> int:
    return await pool.fetchval("select count(*) from crm.notifications where is_read = false")


async def log_activity(pool: asyncpg.Pool, event: str, detail: Optional[Dict[str, Any]] = None, level: str = "info") -> None:
    """Never lets logging break the caller."""
    try:
        await pool.execute("insert into crm.activity_logs (level, event, detail) values ($1, $2, $3::jsonb)",
                           level, event[:120], json.dumps(detail or {}, default=str))
    except Exception:
        pass


async def list_activity(pool: asyncpg.Pool, limit: int = 100) -> List[Dict[str, Any]]:
    out = rows(await pool.fetch("select id, level, event, detail, created_at from crm.activity_logs order by id desc limit $1",
                                max(1, min(limit, 200))))
    for item in out:
        if isinstance(item.get("detail"), str):
            item["detail"] = json.loads(item["detail"])
    return out


async def save_media(pool: asyncpg.Pool, data: bytes, mime_type: str, filename: Optional[str]) -> str:
    return str(await pool.fetchval("insert into crm.media (mime_type, filename, size_bytes, data) values ($1, $2, $3, $4) returning id",
                                   mime_type[:120], (filename or None) and filename[:200], len(data), data))


async def get_media(pool: asyncpg.Pool, media_id: UUID) -> Optional[asyncpg.Record]:
    return await pool.fetchrow("select id, mime_type, filename, size_bytes, data from crm.media where id = $1", media_id)


# ------------------------------------------------------------------------------------------------
# Metrics and analytics
# ------------------------------------------------------------------------------------------------
async def record_metric(pool: asyncpg.Pool, kind: str, platform: str, value: Optional[float] = None) -> None:
    try:
        await pool.execute("insert into crm.metrics_events (kind, platform, value) values ($1, $2, $3)", kind, platform, value)
    except Exception:
        pass


async def activity_marks(pool: asyncpg.Pool) -> Dict[str, Any]:
    """A cheap fingerprint of 'something changed', polled by the live-update stream."""
    r = await pool.fetchrow(
        "select (select coalesce(max(id), 0) from crm.messages) as messages, "
        "(select coalesce(sum(unread_count), 0) from crm.customers) as unread, "
        "(select coalesce(max(id), 0) from crm.notifications) as notifications, "
        "(select count(*) from crm.notifications where is_read = false) as notifications_unread, "
        "(select count(*) from crm.comments) as comments, "
        "(select count(*) from crm.comments where hidden) as comments_hidden, "
        "(select count(*) from crm.customers where handoff_requested) as handoffs")
    return {k: int(v) for k, v in dict(r).items()}


async def analytics(pool: asyncpg.Pool, days: int = 7) -> Dict[str, Any]:
    days = max(1, min(int(days), 90))
    daily = await pool.fetch(
        "select d::date as day, "
        " coalesce(sum(n) filter (where sender = 'customer'), 0)::int as customer, "
        " coalesce(sum(n) filter (where sender = 'bot'), 0)::int as bot, "
        " coalesce(sum(n) filter (where sender = 'agent'), 0)::int as agent "
        "from generate_series((now() - make_interval(days => $1 - 1))::date, now()::date, interval '1 day') d "
        "left join (select created_at::date as day, sender, count(*) as n from crm.messages "
        "           where created_at >= (now() - make_interval(days => $1 - 1))::date group by 1, 2) m on m.day = d::date "
        "group by d order by d", days)
    by_platform = await pool.fetch(
        "select platform, count(*) filter (where sender = 'customer')::int as customer, "
        "count(*) filter (where sender = 'bot')::int as bot, count(*) filter (where sender = 'agent')::int as agent "
        "from crm.messages where created_at >= now() - make_interval(days => $1) group by platform order by platform", days)
    response = await pool.fetchrow(
        "select count(*)::int as samples, avg(value) as avg, percentile_cont(0.5) within group (order by value) as p50, "
        "percentile_cont(0.9) within group (order by value) as p90 from crm.metrics_events "
        "where kind = 'response_time' and created_at >= now() - make_interval(days => $1)", days)
    handoff = await pool.fetchrow(
        "select (select count(*) from crm.metrics_events where kind = 'handoff' and created_at >= now() - make_interval(days => $1))::int as handoffs, "
        "(select count(distinct psid) from crm.messages where sender = 'customer' and created_at >= now() - make_interval(days => $1))::int as conversations, "
        "(select count(*) from crm.customers where handoff_requested)::int as open_handoffs", days)
    comments = await pool.fetch(
        "select coalesce(classification, 'unclassified') as classification, count(*)::int as n from crm.comments "
        "where created_at >= now() - make_interval(days => $1) group by 1 order by 2 desc", days)
    actions = await pool.fetch(
        "select coalesce(action, 'pending') as action, count(*)::int as n from crm.comments "
        "where created_at >= now() - make_interval(days => $1) group by 1 order by 2 desc", days)
    totals = await pool.fetchrow(
        "select (select count(*) from crm.customers where unread_count > 0)::int as unread_conversations, "
        "(select count(*) from crm.comments where is_read = false)::int as unread_comments, "
        "(select count(*) from crm.kb_documents)::int as kb_documents")
    conversations = handoff["conversations"]
    return {
        "days": days,
        "daily": [{"date": r["day"].isoformat(), "customer": r["customer"], "bot": r["bot"], "agent": r["agent"]} for r in daily],
        "by_platform": [dict(r) for r in by_platform],
        "response_time": {"samples": response["samples"], "avg_seconds": round(response["avg"], 2) if response["avg"] is not None else None,
                          "p50_seconds": round(response["p50"], 2) if response["p50"] is not None else None,
                          "p90_seconds": round(response["p90"], 2) if response["p90"] is not None else None},
        "handoff": {"count": handoff["handoffs"], "conversations": conversations, "open": handoff["open_handoffs"],
                    "rate": round(handoff["handoffs"] / conversations, 4) if conversations else 0.0},
        "comments": {"by_classification": [dict(r) for r in comments], "by_action": [dict(r) for r in actions]},
        "totals": dict(totals),
    }
