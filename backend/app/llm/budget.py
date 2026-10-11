"""Per-workspace daily AI call budget.

One atomic statement both checks and increments the counter, so concurrent workers can never overshoot
the cap. Days are UTC. The counter lives in the control database (table llm_usage).
"""
from __future__ import annotations

from typing import Dict
from uuid import UUID

import asyncpg

_TAKE = """
insert into llm_usage (tenant_id, day, calls)
values ($1, (now() at time zone 'utc')::date, 1)
on conflict (tenant_id, day) do update set calls = llm_usage.calls + 1
  where llm_usage.calls < $2
returning calls
"""


class LLMBudget:
    def __init__(self, pool: asyncpg.Pool, daily_limit: int) -> None:
        self._pool, self.daily_limit = pool, daily_limit

    async def take(self, tenant_id: UUID) -> bool:
        """Reserve one AI call. False means today's cap is used up."""
        return await self._pool.fetchval(_TAKE, tenant_id, self.daily_limit) is not None

    async def usage(self, tenant_id: UUID) -> Dict[str, int]:
        used = await self._pool.fetchval(
            "select calls from llm_usage where tenant_id = $1 and day = (now() at time zone 'utc')::date", tenant_id)
        return {"used": int(used or 0), "limit": self.daily_limit}

    async def purge(self, keep_days: int = 35) -> int:
        rows = await self._pool.fetch(
            "delete from llm_usage where day < (now() at time zone 'utc')::date - $1::int returning 1", keep_days)
        return len(rows)
