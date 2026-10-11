"""Small queries used by the bot engine and the dashboard API that are not part of the core store."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import asyncpg

from . import store


async def message_id_by_mid(pool: asyncpg.Pool, platform: str, mid: str) -> Optional[int]:
    return await pool.fetchval("select id from crm.messages where platform = $1 and mid = $2", platform, mid)


async def get_message(pool: asyncpg.Pool, message_id: int) -> Optional[Dict[str, Any]]:
    return store.row(await pool.fetchrow("select * from crm.messages where id = $1", message_id))


async def reply_exists_after(pool: asyncpg.Pool, psid: str, message_id: int) -> bool:
    """True when the bot or an agent already answered after this message (used to make retries idempotent)."""
    return bool(await pool.fetchval(
        "select 1 from crm.messages where psid = $1 and id > $2 and sender in ('bot', 'agent') limit 1", psid, message_id))


async def has_recent_notification(pool: asyncpg.Pool, type_: str, target_id: str, minutes: int) -> bool:
    return bool(await pool.fetchval(
        "select 1 from crm.notifications where type = $1 and target_id = $2 "
        "and created_at > now() - make_interval(mins => $3) limit 1", type_, target_id, minutes))


async def comment_by_id(pool: asyncpg.Pool, platform: str, comment_id: str) -> Optional[Dict[str, Any]]:
    return await store.get_comment(pool, platform, comment_id)


async def existing_comment_ids(pool: asyncpg.Pool, platform: str, ids: List[str]) -> List[str]:
    rows = await pool.fetch("select comment_id from crm.comments where platform = $1 and comment_id = any($2::text[])",
                            platform, ids)
    return [r["comment_id"] for r in rows]


async def search_customers_count(pool: asyncpg.Pool) -> int:
    return int(await pool.fetchval("select count(*) from crm.customers") or 0)
