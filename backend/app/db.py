"""Connection pool for the control-plane database (users, tenants, sessions, encrypted credentials)."""
from __future__ import annotations

import asyncpg


async def create_pool(dsn: str) -> asyncpg.Pool:
    # statement_cache_size=0 keeps this compatible with PgBouncer / Supabase pooler (transaction mode).
    return await asyncpg.create_pool(
        dsn, min_size=1, max_size=10, command_timeout=30, statement_cache_size=0,
    )
