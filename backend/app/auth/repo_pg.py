"""PostgreSQL implementation of the AuthRepository contract (see models.py / memory_repo.py)."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple
from uuid import UUID

import asyncpg

from .models import ClaimOutcome, EmailTaken, Tenant, User
from .windows import window_bounds


def _user(row: asyncpg.Record) -> User:
    return User(row["id"], row["email"], row["email_verified_at"], row["password_hash"],
                row["display_name"], row["created_at"])


class PgAuthRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # ---- users / tenants -------------------------------------------------------------------
    async def create_user_with_tenant(self, *, email, password_hash, display_name, workspace_name, now):
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                try:
                    u = await conn.fetchrow(
                        "insert into users (email, password_hash, display_name, created_at, updated_at) "
                        "values ($1, $2, $3, $4, $4) returning *",
                        email, password_hash, display_name, now)
                except asyncpg.UniqueViolationError as exc:
                    raise EmailTaken(email) from exc
                t = await conn.fetchrow(
                    "insert into tenants (name, owner_user_id, created_at) values ($1, $2, $3) returning *",
                    workspace_name, u["id"], now)
                await conn.execute(
                    "insert into tenant_members (tenant_id, user_id, role, created_at) values ($1, $2, 'owner', $3)",
                    t["id"], u["id"], now)
        return _user(u), Tenant(t["id"], t["name"], t["owner_user_id"])

    async def get_user_by_email(self, email):
        row = await self._pool.fetchrow("select * from users where email = $1", email)
        return _user(row) if row else None

    async def get_user(self, user_id):
        row = await self._pool.fetchrow("select * from users where id = $1", user_id)
        return _user(row) if row else None

    async def get_primary_tenant(self, user_id):
        row = await self._pool.fetchrow(
            "select t.* from tenants t join tenant_members m on m.tenant_id = t.id "
            "where m.user_id = $1 order by m.created_at limit 1", user_id)
        return Tenant(row["id"], row["name"], row["owner_user_id"]) if row else None

    async def mark_email_verified(self, user_id, now):
        await self._pool.execute(
            "update users set email_verified_at = coalesce(email_verified_at, $2), updated_at = $2 where id = $1",
            user_id, now)

    async def set_password(self, user_id, password_hash, now):
        await self._pool.execute(
            "update users set password_hash = $2, updated_at = $3 where id = $1", user_id, password_hash, now)

    # ---- sessions / refresh tokens ---------------------------------------------------------
    async def create_session(self, *, user_id, ip, user_agent, now):
        return await self._pool.fetchval(
            "insert into sessions (user_id, created_at, last_used_at, ip, user_agent) "
            "values ($1, $2, $2, $3, $4) returning id",
            user_id, now, (ip or "")[:64] or None, (user_agent or "")[:400] or None)

    async def insert_refresh_token(self, *, session_id, token_hash, expires_at):
        await self._pool.execute(
            "insert into refresh_tokens (session_id, token_hash, expires_at) values ($1, $2, $3)",
            session_id, token_hash, expires_at)

    async def claim_refresh_token(self, token_hash, now):
        # Single atomic statement: only one concurrent caller can win a given token.
        won = await self._pool.fetchrow(
            "update refresh_tokens rt set used_at = $2 "
            "where rt.token_hash = $1 and rt.used_at is null and rt.expires_at > $2 "
            "and exists (select 1 from sessions s where s.id = rt.session_id and s.revoked_at is null) "
            "returning rt.session_id, (select user_id from sessions where id = rt.session_id) as user_id",
            token_hash, now)
        if won:
            return ClaimOutcome("ok", won["session_id"], won["user_id"])
        row = await self._pool.fetchrow(
            "select rt.session_id, rt.used_at, rt.expires_at, s.user_id, s.revoked_at "
            "from refresh_tokens rt join sessions s on s.id = rt.session_id where rt.token_hash = $1",
            token_hash)
        if row is None:
            return ClaimOutcome("unknown")
        if row["revoked_at"] is not None:
            return ClaimOutcome("revoked", row["session_id"], row["user_id"])
        if row["used_at"] is not None:
            return ClaimOutcome("reused", row["session_id"], row["user_id"], row["used_at"])
        return ClaimOutcome("expired", row["session_id"], row["user_id"])

    async def session_id_for_refresh_hash(self, token_hash):
        return await self._pool.fetchval("select session_id from refresh_tokens where token_hash = $1", token_hash)

    async def touch_session(self, session_id, now):
        await self._pool.execute("update sessions set last_used_at = $2 where id = $1", session_id, now)

    async def revoke_session(self, session_id, reason, now):
        await self._pool.execute(
            "update sessions set revoked_at = $2, revoked_reason = $3 where id = $1 and revoked_at is null",
            session_id, now, reason)

    async def revoke_all_sessions(self, user_id, reason, now):
        await self._pool.execute(
            "update sessions set revoked_at = $2, revoked_reason = $3 where user_id = $1 and revoked_at is null",
            user_id, now, reason)

    async def is_session_active(self, session_id):
        return bool(await self._pool.fetchval(
            "select 1 from sessions where id = $1 and revoked_at is null", session_id))

    # ---- one-time tokens -------------------------------------------------------------------
    async def create_auth_token(self, *, user_id, purpose, token_hash, expires_at):
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "delete from auth_tokens where user_id = $1 and purpose = $2 and used_at is null",
                    user_id, purpose)
                await conn.execute(
                    "insert into auth_tokens (user_id, purpose, token_hash, expires_at) values ($1, $2, $3, $4)",
                    user_id, purpose, token_hash, expires_at)

    async def consume_auth_token(self, *, token_hash, purpose, now):
        return await self._pool.fetchval(
            "update auth_tokens set used_at = $3 where token_hash = $1 and purpose = $2 "
            "and used_at is null and expires_at > $3 returning user_id",
            token_hash, purpose, now)

    # ---- rate limiting ---------------------------------------------------------------------
    async def rate_hit(self, key, window_seconds, now) -> Tuple[int, int]:
        start, retry = window_bounds(now, window_seconds)
        count = await self._pool.fetchval(
            "insert into rate_limits (key, window_start, count) values ($1, $2, 1) "
            "on conflict (key, window_start) do update set count = rate_limits.count + 1 returning count",
            key, start)
        return count, retry

    async def rate_count(self, key, window_seconds, now) -> Tuple[int, int]:
        start, retry = window_bounds(now, window_seconds)
        count = await self._pool.fetchval(
            "select count from rate_limits where key = $1 and window_start = $2", key, start)
        return count or 0, retry

    async def rate_reset(self, key):
        await self._pool.execute("delete from rate_limits where key = $1", key)

    # ---- audit / housekeeping --------------------------------------------------------------
    async def audit(self, *, event, now, user_id=None, tenant_id=None, ip=None, user_agent=None, detail=None):
        import json
        await self._pool.execute(
            "insert into audit_log (tenant_id, user_id, event, ip, user_agent, detail, created_at) "
            "values ($1, $2, $3, $4, $5, $6::jsonb, $7)",
            tenant_id, user_id, event, ip, (user_agent or "")[:400] or None, json.dumps(detail or {}), now)

    async def purge_expired(self, now):
        await self._pool.execute("delete from rate_limits where window_start < $1", now - timedelta(days=1))
        await self._pool.execute("delete from refresh_tokens where expires_at < $1", now - timedelta(days=7))
        await self._pool.execute(
            "delete from auth_tokens where expires_at < $1", now - timedelta(days=7))

    async def ping(self):
        return bool(await self._pool.fetchval("select 1"))
