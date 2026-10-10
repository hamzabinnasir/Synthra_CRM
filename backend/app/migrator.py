"""Versioned, checksummed SQL migrations. Used for the control-plane database and for each tenant's own
database (different directory and tracking table).

* Files are named NNNN_description.sql and applied in order, each inside one transaction.
* A PostgreSQL advisory lock stops two processes migrating at once.
* The SHA-256 of every applied file is stored; editing an applied migration is refused.
* The tracking table may be schema-qualified ("crm.schema_migrations"); the schema is created if needed.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import asyncpg

_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


class MigrationError(Exception):
    pass


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    sql: str
    checksum: str


@dataclass(frozen=True)
class MigrationStatus:
    version: str
    name: str
    state: str  # applied | pending


def load_migrations(directory: Path) -> List[Migration]:
    found: List[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = _NAME.match(path.name)
        if not match:
            raise MigrationError(f"Bad migration file name: {path.name} (expected NNNN_name.sql)")
        # Normalise line endings so the checksum is identical on Windows (CRLF) and Linux (LF).
        sql = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        found.append(Migration(match.group(1), path.stem, sql, hashlib.sha256(sql.encode("utf-8")).hexdigest()))
    versions = [m.version for m in found]
    if len(versions) != len(set(versions)):
        raise MigrationError("Duplicate migration version numbers")
    return found


def _parts(table: str) -> List[str]:
    parts = table.split(".")
    if not 1 <= len(parts) <= 2 or not all(_IDENT.match(p) for p in parts):
        raise MigrationError(f"Invalid migration table name: {table!r}")
    return parts


def _qualified(table: str) -> str:
    return ".".join(f'"{p}"' for p in _parts(table))


def _lock_id(table: str) -> int:
    return int.from_bytes(hashlib.sha256(table.encode()).digest()[:7], "big")


async def _ensure_table(conn: asyncpg.Connection, table: str) -> None:
    parts = _parts(table)
    if len(parts) == 2:
        await conn.execute(f'create schema if not exists "{parts[0]}"')
    await conn.execute(
        f"create table if not exists {_qualified(table)} ("
        "version text primary key, name text not null, checksum text not null, "
        "applied_at timestamptz not null default now())"
    )


async def _applied(conn: asyncpg.Connection, table: str) -> dict:
    return {r["version"]: r["checksum"] for r in await conn.fetch(f"select version, checksum from {_qualified(table)}")}


async def status(conn: asyncpg.Connection, directory: Path, table: str = "schema_migrations") -> List[MigrationStatus]:
    await _ensure_table(conn, table)
    applied = await _applied(conn, table)
    result = []
    for m in load_migrations(directory):
        if m.version in applied and applied[m.version] != m.checksum:
            raise MigrationError(f"Migration {m.name} was modified after it was applied")
        result.append(MigrationStatus(m.version, m.name, "applied" if m.version in applied else "pending"))
    return result


async def pending(conn: asyncpg.Connection, directory: Path, table: str = "schema_migrations") -> List[Migration]:
    """Pending migrations with their SQL (used for the dry-run / preview)."""
    done = {s.version for s in await status(conn, directory, table) if s.state == "applied"}
    return [m for m in load_migrations(directory) if m.version not in done]


async def migrate(conn: asyncpg.Connection, directory: Path, table: str = "schema_migrations",
                  *, dry_run: bool = False, target: Optional[str] = None) -> List[str]:
    """Apply pending migrations. Returns the names applied (or that would be applied, for dry_run)."""
    await _ensure_table(conn, table)
    await conn.execute("select pg_advisory_lock($1)", _lock_id(table))
    try:
        applied = await _applied(conn, table)
        done: List[str] = []
        for m in load_migrations(directory):
            if m.version in applied:
                if applied[m.version] != m.checksum:
                    raise MigrationError(f"Migration {m.name} was modified after it was applied")
                continue
            if target and m.version > target:
                break
            done.append(m.name)
            if dry_run:
                continue
            async with conn.transaction():
                await conn.execute(m.sql)
                await conn.execute(
                    f"insert into {_qualified(table)} (version, name, checksum) values ($1, $2, $3)",
                    m.version, m.name, m.checksum)
        return done
    finally:
        await conn.execute("select pg_advisory_unlock($1)", _lock_id(table))


def bundle_sql(directory: Path, table: str = "schema_migrations") -> str:
    """One self-contained, idempotent script for pasting into a SQL editor when automatic setup fails.
    Records each migration exactly like migrate() does, so the app later sees the schema as up to date."""
    parts = _parts(table)
    out = ["-- Synthra CRM database setup (manual fallback). Safe to run more than once.", "begin;"]
    if len(parts) == 2:
        out.append(f'create schema if not exists "{parts[0]}";')
    out.append(f"create table if not exists {_qualified(table)} (version text primary key, name text not null, "
               "checksum text not null, applied_at timestamptz not null default now());")
    for m in load_migrations(directory):
        out.append(f"\n-- ===== {m.name} =====")
        out.append(f"do $mig$ begin if not exists (select 1 from {_qualified(table)} where version = '{m.version}') then")
        out.append(f"  execute $sql${m.sql}$sql$;")
        out.append(f"  insert into {_qualified(table)} (version, name, checksum) "
                   f"values ('{m.version}', '{m.name}', '{m.checksum}');")
        out.append("end if; end $mig$;")
    out.append("commit;")
    return "\n".join(out) + "\n"
