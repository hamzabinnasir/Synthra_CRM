"""Durable, idempotent event queue on PostgreSQL (no extra service to host).

* enqueue() is called by the webhook handler before it answers Meta, so an accepted event is never lost.
* (tenant, channel, event_key) is unique, so redelivery by Meta is a harmless no-op.
* claim() uses FOR UPDATE SKIP LOCKED, so any number of workers can run side by side.
* Claims are capped per tenant per round, so one busy workspace cannot starve the others.
* Failed events retry with exponential backoff and become 'dead' after MAX_ATTEMPTS.
* When an event finishes its payload (customer messages) is erased; only the idempotency key is kept
  for RETENTION_DAYS and then deleted.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Sequence
from uuid import UUID

import asyncpg

MAX_ATTEMPTS = 6
BASE_BACKOFF_SECONDS = 10
MAX_BACKOFF_SECONDS = 3600
STALE_AFTER_SECONDS = 300
RETENTION_DAYS = 7

_CLAIM = """
with ready as (
  select id, row_number() over (partition by tenant_id order by id) as rn
  from webhook_events
  where status = 'pending' and run_after <= $3 and channel = any($2::text[])
),
picked as (
  select w.id
  from webhook_events w join ready r on r.id = w.id
  where r.rn <= $5
  order by r.rn, w.id
  limit $4
  for update of w skip locked
)
update webhook_events e
   set status = 'processing', locked_at = $3, locked_by = $1, attempts = e.attempts + 1
  from picked
 where e.id = picked.id
returning e.id, e.tenant_id, e.channel, e.event_key, e.payload, e.attempts
"""


@dataclass(frozen=True)
class QueuedEvent:
    id: int
    tenant_id: UUID
    channel: str
    key: str
    kind: str
    data: Dict[str, Any]
    attempts: int


def backoff_seconds(attempts: int) -> int:
    """Delay before the next try after `attempts` tries have failed: 20s, 40s, 80s ... capped at 1 hour."""
    return min(BASE_BACKOFF_SECONDS * (2 ** max(attempts, 1)), MAX_BACKOFF_SECONDS)


def _load(payload: Any) -> Dict[str, Any]:
    return json.loads(payload) if isinstance(payload, (str, bytes)) else dict(payload or {})


class EventQueue:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def enqueue(self, tenant_id: UUID, channel: str, events: Iterable[Any]) -> int:
        """Insert events; returns how many were new (duplicates are ignored)."""
        keys: List[str] = []
        payloads: List[str] = []
        for event in events:
            keys.append(event.key)
            payloads.append(json.dumps({"kind": event.kind, "data": event.data}, separators=(",", ":")))
        if not keys:
            return 0
        rows = await self._pool.fetch(
            "insert into webhook_events (tenant_id, channel, event_key, payload) "
            "select $1, $2, t.k, t.p::jsonb from unnest($3::text[], $4::text[]) as t(k, p) "
            "on conflict (tenant_id, channel, event_key) do nothing returning id",
            tenant_id, channel, keys, payloads)
        return len(rows)

    async def claim(self, worker_id: str, channels: Sequence[str], now: datetime, *,
                    limit: int = 10, per_tenant: int = 3) -> List[QueuedEvent]:
        if not channels or limit < 1:
            return []
        rows = await self._pool.fetch(_CLAIM, worker_id, list(channels), now, limit, per_tenant)
        events = []
        for row in rows:
            body = _load(row["payload"])
            events.append(QueuedEvent(row["id"], row["tenant_id"], row["channel"], row["event_key"],
                                      body.get("kind", ""), body.get("data", {}), row["attempts"]))
        return events

    async def complete(self, event_id: int, now: datetime) -> None:
        await self._pool.execute(
            "update webhook_events set status = 'done', payload = '{}'::jsonb, finished_at = $2, "
            "locked_at = null, locked_by = null, last_error = null where id = $1", event_id, now)

    async def fail(self, event_id: int, attempts: int, error: str, now: datetime, *, permanent: bool = False) -> str:
        """Record a failure. Returns the new status: 'pending' (will retry) or 'dead'."""
        message = error[:480]
        if permanent or attempts >= MAX_ATTEMPTS:
            await self._pool.execute(
                "update webhook_events set status = 'dead', payload = '{}'::jsonb, finished_at = $2, "
                "locked_at = null, locked_by = null, last_error = $3 where id = $1", event_id, now, message)
            return "dead"
        await self._pool.execute(
            "update webhook_events set status = 'pending', run_after = $2, locked_at = null, locked_by = null, "
            "last_error = $3 where id = $1", event_id, now + timedelta(seconds=backoff_seconds(attempts)), message)
        return "pending"

    async def reclaim_stale(self, now: datetime) -> int:
        """A worker that crashed mid-event leaves it 'processing'. Put those back (or bury them)."""
        rows = await self._pool.fetch(
            "update webhook_events set "
            " status = case when attempts >= $3 then 'dead' else 'pending' end, "
            " finished_at = case when attempts >= $3 then $1::timestamptz else null end, "
            " payload = case when attempts >= $3 then '{}'::jsonb else payload end, "
            " last_error = 'worker stopped while processing', locked_at = null, locked_by = null "
            "where status = 'processing' and locked_at < $2 returning id",
            now, now - timedelta(seconds=STALE_AFTER_SECONDS), MAX_ATTEMPTS)
        return len(rows)

    async def purge(self, now: datetime) -> int:
        rows = await self._pool.fetch(
            "delete from webhook_events where status in ('done', 'dead') and finished_at < $1 returning id",
            now - timedelta(days=RETENTION_DAYS))
        return len(rows)

    async def stats(self, tenant_id: UUID) -> Dict[str, int]:
        rows = await self._pool.fetch(
            "select status, count(*) as n from webhook_events where tenant_id = $1 group by status", tenant_id)
        counts = {"pending": 0, "processing": 0, "done": 0, "dead": 0}
        counts.update({r["status"]: r["n"] for r in rows})
        return counts
