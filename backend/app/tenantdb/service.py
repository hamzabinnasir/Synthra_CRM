"""Per-workspace database connection: storage (encrypted), checks, migrations, and a pool cache."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

import asyncpg

from ..config import Settings
from ..security.crypto import SecretBox
from . import provisioner
from .provisioner import DatabaseSetupError, Target

log = logging.getLogger("app.tenantdb")

MAX_POOLS = 50
POOL_IDLE_SECONDS = 600


def _ctx(tenant_id: UUID) -> str:
    return f"{tenant_id}:database:url"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TenantDatabaseService:
    def __init__(self, control_pool: asyncpg.Pool, box: SecretBox, settings: Settings) -> None:
        self._control, self._box, self._allow_private = control_pool, box, settings.allow_private_db_hosts
        self._pools: Dict[UUID, Tuple[asyncpg.Pool, float]] = {}
        self._pool_lock = asyncio.Lock()

    # ---- helpers ---------------------------------------------------------------------------
    async def _target(self, tenant_id: UUID) -> Target:
        row = await self._control.fetchrow("select encrypted_url from tenant_databases where tenant_id = $1", tenant_id)
        if row is None:
            raise DatabaseSetupError("No database is connected yet.", code="not_connected")
        return provisioner.parse_url(self._box.decrypt(row["encrypted_url"], context=_ctx(tenant_id)))

    async def _open(self, target: Target) -> asyncpg.Connection:
        return await provisioner.connect(target, allow_private=self._allow_private)

    async def _record(self, tenant_id: UUID, *, status: str, error: Optional[str] = None,
                      version: Optional[str] = None) -> None:
        await self._control.execute(
            "update tenant_databases set status = $2, last_error = $3, schema_version = coalesce($4, schema_version), "
            "last_checked_at = $5, updated_at = $5 where tenant_id = $1",
            tenant_id, status, (error or None) and error[:500], version, _now())

    # ---- public operations -----------------------------------------------------------------
    async def info(self, tenant_id: UUID) -> Dict[str, Any]:
        row = await self._control.fetchrow(
            "select host_hint, status, last_checked_at, last_error, schema_version from tenant_databases "
            "where tenant_id = $1", tenant_id)
        latest = provisioner.latest_version()
        if row is None:
            return {"connected": False, "latest_version": latest}
        return {"connected": True, "host_hint": row["host_hint"], "status": row["status"],
                "last_checked_at": row["last_checked_at"].isoformat() if row["last_checked_at"] else None,
                "last_error": row["last_error"], "schema_version": row["schema_version"],
                "latest_version": latest, "up_to_date": row["schema_version"] == latest}

    async def test(self, url: str) -> Dict[str, Any]:
        """Validate a connection string and run the preflight checks, without saving anything."""
        target = provisioner.parse_url(url)
        conn = await self._open(target)
        try:
            check = await provisioner.preflight(conn)
            has_schema = bool(await conn.fetchval("select to_regclass('crm.schema_migrations') is not null"))
            pending = len(await provisioner.preview(conn)) if has_schema else len(provisioner.manifest_names())
        finally:
            await conn.close()
        return {**check.as_dict(), "host_hint": target.hint, "pending_migrations": pending}

    async def save(self, tenant_id: UUID, url: str) -> Dict[str, Any]:
        target = provisioner.parse_url(url)
        conn = await self._open(target)  # proves the credentials work before anything is stored
        try:
            check = await provisioner.preflight(conn)
        finally:
            await conn.close()
        await self._control.execute(
            "insert into tenant_databases (tenant_id, encrypted_url, host_hint, status, last_checked_at) "
            "values ($1, $2, $3, 'connected', $4) "
            "on conflict (tenant_id) do update set encrypted_url = excluded.encrypted_url, "
            "host_hint = excluded.host_hint, status = 'connected', last_error = null, schema_version = null, "
            "last_checked_at = excluded.last_checked_at, updated_at = excluded.last_checked_at",
            tenant_id, self._box.encrypt(url.strip(), context=_ctx(tenant_id)), target.hint, _now())
        await self.evict(tenant_id)
        return {"preflight": check.as_dict(), **await self.info(tenant_id)}

    async def migrate(self, tenant_id: UUID, *, dry_run: bool) -> Dict[str, Any]:
        target = await self._target(tenant_id)
        conn = await self._open(target)
        try:
            check = await provisioner.preflight(conn)
            if not check.ok:
                raise DatabaseSetupError(" ".join(check.problems), code="preflight_failed")
            if dry_run:
                return {"dry_run": True, "pending": await provisioner.preview(conn)}
            try:
                applied = await provisioner.apply(conn)
            except DatabaseSetupError as exc:
                await self._record(tenant_id, status="error", error=exc.message)
                raise
            await self._record(tenant_id, status="migrated", version=provisioner.latest_version())
            await self.evict(tenant_id)
            return {"dry_run": False, "applied": applied, **await self.info(tenant_id)}
        finally:
            await conn.close()

    async def migrations(self, tenant_id: UUID) -> List[Dict[str, str]]:
        conn = await self._open(await self._target(tenant_id))
        try:
            return await provisioner.migration_status(conn)
        finally:
            await conn.close()

    async def check(self, tenant_id: UUID) -> Dict[str, Any]:
        """Re-verify the connection and the schema state (for example after running the manual SQL)."""
        try:
            conn = await self._open(await self._target(tenant_id))
        except DatabaseSetupError as exc:
            await self._record(tenant_id, status="error", error=exc.message)
            raise
        try:
            statuses = await provisioner.migration_status(conn)
        finally:
            await conn.close()
        done = all(s["state"] == "applied" for s in statuses)
        await self._record(tenant_id, status="migrated" if done else "connected",
                           version=provisioner.latest_version() if done else None)
        return {"migrations": statuses, **await self.info(tenant_id)}

    async def disconnect(self, tenant_id: UUID) -> None:
        await self._control.execute("delete from tenant_databases where tenant_id = $1", tenant_id)
        await self.evict(tenant_id)

    # ---- pool cache for the bot and dashboards ---------------------------------------------
    async def pool(self, tenant_id: UUID) -> asyncpg.Pool:
        async with self._pool_lock:
            cached = self._pools.get(tenant_id)
            if cached:
                self._pools[tenant_id] = (cached[0], time.monotonic())
                return cached[0]
            row = await self._control.fetchrow("select status from tenant_databases where tenant_id = $1", tenant_id)
            if row is None:
                raise DatabaseSetupError("No database is connected yet.", code="not_connected")
            if row["status"] != "migrated":
                raise DatabaseSetupError("The database has not been set up yet.", code="not_migrated")
            if len(self._pools) >= MAX_POOLS:
                oldest = min(self._pools, key=lambda k: self._pools[k][1])
                await self._close(oldest)
            target = await self._target(tenant_id)
            address = (await provisioner.resolve_public(target.host, target.port, allow_private=self._allow_private))[0]
            pool = await asyncpg.create_pool(
                host=address, port=target.port, user=target.user, password=target.password,
                database=target.database, ssl=provisioner._ssl_context(target),
                min_size=0, max_size=4, timeout=10, command_timeout=30, statement_cache_size=0,
                max_inactive_connection_lifetime=120, init=_init_connection,
                server_settings={"application_name": "synthra-crm"})
            self._pools[tenant_id] = (pool, time.monotonic())
            return pool

    async def _close(self, tenant_id: UUID) -> None:
        cached = self._pools.pop(tenant_id, None)
        if cached:
            await cached[0].close()

    async def evict(self, tenant_id: UUID) -> None:
        async with self._pool_lock:
            await self._close(tenant_id)

    async def reap_idle(self) -> None:
        async with self._pool_lock:
            cutoff = time.monotonic() - POOL_IDLE_SECONDS
            for tenant_id in [k for k, (_, used) in self._pools.items() if used < cutoff]:
                await self._close(tenant_id)

    async def close_all(self) -> None:
        async with self._pool_lock:
            for tenant_id in list(self._pools):
                await self._close(tenant_id)


async def _init_connection(conn: asyncpg.Connection) -> None:
    from pgvector.asyncpg import register_vector
    await conn.execute("set search_path to public, extensions")
    await register_vector(conn)
