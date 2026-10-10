"""Connecting to, checking and provisioning a tenant's own PostgreSQL / Supabase database.

Why a connection string and not the Supabase anon/service keys: those keys talk to the REST API, which
cannot run DDL (create table, extension, function, trigger, policy). Provisioning therefore needs a real
Postgres connection. The URL is used only to provision and to run the bot's queries, and is stored
encrypted (AES-256-GCM); it is never returned to the browser.

Security notes
* The URL is parsed and the connection is rebuilt from validated parts. Query parameters such as
  host=, sslrootcert= or options= are ignored, so a crafted URL cannot redirect the connection or read
  local files.
* The host is resolved and every address must be public (unless CRM_ALLOW_PRIVATE_DB_HOSTS=true).
  The connection is made to the vetted IP, which defeats DNS rebinding.
* TLS is required unless the host is private and sslmode=disable was requested explicitly.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import ssl as ssl_lib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlsplit

import asyncpg

from .. import migrator

TENANT_MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations" / "tenant"
TENANT_TABLE = "crm.schema_migrations"
MIN_PG_VERSION = 13


class DatabaseSetupError(Exception):
    """A problem the user can act on. The message is safe to show in the UI."""

    def __init__(self, message: str, *, code: str = "database_error", hint: str = ""):
        super().__init__(message)
        self.message, self.code, self.hint = message, code, hint


@dataclass(frozen=True)
class Target:
    host: str
    port: int
    user: str
    password: str
    database: str
    sslmode: str  # require | disable

    @property
    def hint(self) -> str:
        return f"{self.host}:{self.port}/{self.database}"


@dataclass
class Preflight:
    server_version: str = ""
    database: str = ""
    user: str = ""
    can_create_schema: bool = False
    vector_installed: bool = False
    vector_available: bool = False
    problems: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def as_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "server_version": self.server_version, "database": self.database,
                "user": self.user, "can_create_schema": self.can_create_schema,
                "vector_installed": self.vector_installed, "vector_available": self.vector_available,
                "problems": self.problems, "warnings": self.warnings}


# ---------------------------------------------------------------------------------------------
# URL parsing and host checks
# ---------------------------------------------------------------------------------------------
def parse_url(url: str) -> Target:
    url = (url or "").strip()
    if len(url) > 2048:
        raise DatabaseSetupError("The connection string is too long.", code="invalid_url")
    try:
        parts = urlsplit(url)
        port = parts.port or 5432
    except ValueError:
        raise DatabaseSetupError("That does not look like a valid connection string.", code="invalid_url")
    if parts.scheme not in ("postgres", "postgresql"):
        raise DatabaseSetupError("The connection string must start with postgresql://", code="invalid_url")
    if not parts.hostname:
        raise DatabaseSetupError("The connection string has no host.", code="invalid_url")
    if not parts.username:
        raise DatabaseSetupError("The connection string has no username.", code="invalid_url")
    query = parse_qs(parts.query)
    sslmode = (query.get("sslmode", ["require"])[0] or "require").lower()
    if sslmode not in ("require", "verify-ca", "verify-full", "prefer", "allow", "disable"):
        raise DatabaseSetupError("Unsupported sslmode in the connection string.", code="invalid_url")
    return Target(
        host=parts.hostname, port=port, user=unquote(parts.username), password=unquote(parts.password or ""),
        database=unquote(parts.path.lstrip("/")) or "postgres",
        sslmode="disable" if sslmode == "disable" else "require")


def _is_public(ip: ipaddress._BaseAddress) -> bool:
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified)


async def resolve_public(host: str, port: int, *, allow_private: bool) -> List[str]:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise DatabaseSetupError(
            "Could not find that host. Check the spelling of the host name.", code="host_not_found")
    addresses: List[str] = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not allow_private and not _is_public(ip):
            raise DatabaseSetupError(
                "That host points to a private or internal network address, which is not allowed here.",
                code="host_not_allowed")
        if str(ip) not in addresses:
            addresses.append(str(ip))
    if not addresses:
        raise DatabaseSetupError("Could not resolve that host.", code="host_not_found")
    return addresses


def _ssl_context(target: Target) -> Optional[ssl_lib.SSLContext]:
    if target.sslmode == "disable":
        return None
    ctx = ssl_lib.create_default_context()
    ctx.check_hostname = False          # encryption without certificate pinning, like libpq's "require"
    ctx.verify_mode = ssl_lib.CERT_NONE
    return ctx


def _friendly(exc: BaseException) -> DatabaseSetupError:
    if isinstance(exc, asyncpg.InvalidPasswordError):
        return DatabaseSetupError("Authentication failed. Check the username and password.", code="auth_failed")
    if isinstance(exc, asyncpg.InvalidCatalogNameError):
        return DatabaseSetupError("The server is reachable but that database name does not exist.", code="no_database")
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return DatabaseSetupError(
            "The connection timed out.", code="timeout",
            hint="Check the host and port. Supabase direct connections are IPv6-only; if your network is "
                 "IPv4 use the Session pooler connection string instead.")
    if isinstance(exc, ssl_lib.SSLError):
        return DatabaseSetupError("The server did not accept an encrypted connection.", code="ssl_failed",
                                  hint="If this is a local test database, add ?sslmode=disable (private hosts only).")
    if isinstance(exc, (OSError, asyncpg.CannotConnectNowError)):
        return DatabaseSetupError("Could not connect to the server.", code="unreachable",
                                  hint="Check the host, port and that the database accepts outside connections.")
    if isinstance(exc, asyncpg.PostgresError):
        return DatabaseSetupError(f"The database refused the request ({type(exc).__name__}).", code="db_error")
    return DatabaseSetupError("Could not connect to the database.", code="connect_failed")


async def connect(target: Target, *, allow_private: bool, timeout: float = 10.0) -> asyncpg.Connection:
    """Open one connection to the vetted address. Raises DatabaseSetupError with a user-safe message."""
    addresses = await resolve_public(target.host, target.port, allow_private=allow_private)
    if target.sslmode == "disable" and not allow_private:
        raise DatabaseSetupError("Unencrypted connections are not allowed. Remove sslmode=disable.",
                                 code="ssl_required")
    last: Optional[BaseException] = None
    for address in addresses:
        try:
            return await asyncpg.connect(
                host=address, port=target.port, user=target.user, password=target.password,
                database=target.database, ssl=_ssl_context(target), timeout=timeout,
                server_hostname=target.host if target.sslmode != "disable" else None,
                statement_cache_size=0, command_timeout=60,
                server_settings={"application_name": "synthra-crm"})
        except (asyncpg.InvalidPasswordError, asyncpg.InvalidCatalogNameError) as exc:
            raise _friendly(exc)
        except Exception as exc:  # try the next resolved address (IPv4 / IPv6)
            last = exc
    raise _friendly(last or RuntimeError("no address"))


async def _session_setup(conn: asyncpg.Connection) -> None:
    # Where the vector type is found differs between plain Postgres (public) and Supabase (extensions).
    await conn.execute("set search_path to public, extensions")


# ---------------------------------------------------------------------------------------------
# Checks, status, preview and apply
# ---------------------------------------------------------------------------------------------
async def preflight(conn: asyncpg.Connection) -> Preflight:
    result = Preflight()
    result.server_version = await conn.fetchval("show server_version")
    row = await conn.fetchrow("select current_user as u, current_database() as d")
    result.user, result.database = row["u"], row["d"]
    result.can_create_schema = bool(await conn.fetchval(
        "select has_database_privilege(current_user, current_database(), 'CREATE')"))
    result.vector_installed = bool(await conn.fetchval("select exists(select 1 from pg_extension where extname = 'vector')"))
    result.vector_available = bool(await conn.fetchval("select exists(select 1 from pg_available_extensions where name = 'vector')"))
    try:
        major = int(result.server_version.split(".")[0].split()[0])
    except (ValueError, IndexError):
        major = 0
    if major and major < MIN_PG_VERSION:
        result.problems.append(f"PostgreSQL {MIN_PG_VERSION} or newer is required (found {result.server_version}).")
    if not result.can_create_schema:
        result.problems.append(
            "This database user is not allowed to create schemas. Use the 'postgres' role or grant CREATE on the database.")
    if not result.vector_installed and not result.vector_available:
        result.problems.append(
            "The pgvector extension is not available on this server. On Supabase it is built in; on other hosts "
            "install pgvector first.")
    elif not result.vector_installed:
        result.warnings.append("The pgvector extension will be enabled during setup.")
    return result


async def migration_status(conn: asyncpg.Connection) -> List[Dict[str, str]]:
    await _session_setup(conn)
    return [{"version": s.version, "name": s.name, "state": s.state}
            for s in await migrator.status(conn, TENANT_MIGRATIONS, TENANT_TABLE)]


async def preview(conn: asyncpg.Connection) -> List[Dict[str, str]]:
    """Dry run: the migrations that would be applied, with their SQL. Changes nothing."""
    await _session_setup(conn)
    return [{"version": m.version, "name": m.name, "sql": m.sql}
            for m in await migrator.pending(conn, TENANT_MIGRATIONS, TENANT_TABLE)]


async def apply(conn: asyncpg.Connection) -> List[str]:
    await _session_setup(conn)
    try:
        return await migrator.migrate(conn, TENANT_MIGRATIONS, TENANT_TABLE)
    except migrator.MigrationError as exc:
        raise DatabaseSetupError(str(exc), code="migration_conflict")
    except asyncpg.InsufficientPrivilegeError:
        raise DatabaseSetupError(
            "The database user lacks permission to create objects.", code="insufficient_privilege",
            hint="Use the 'postgres' role, or run the manual setup SQL in your SQL editor.")
    except asyncpg.PostgresError as exc:
        raise DatabaseSetupError(
            f"A migration failed and was rolled back ({type(exc).__name__}). Nothing was partially applied.",
            code="migration_failed", hint="You can use the manual setup SQL as a fallback.")


def manifest_names() -> List[str]:
    return [m.name for m in migrator.load_migrations(TENANT_MIGRATIONS)]


def latest_version() -> str:
    return migrator.load_migrations(TENANT_MIGRATIONS)[-1].version


def manual_sql() -> str:
    return migrator.bundle_sql(TENANT_MIGRATIONS, TENANT_TABLE)
