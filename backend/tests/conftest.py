"""Shared fixtures. Integration tests need a real PostgreSQL: set CRM_TEST_DATABASE_URL (see scripts/dev_postgres.py).
Without it they are skipped, so the plain unit-test run needs no database."""
import base64
import os
import uuid
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from app import migrator
from app.config import Settings
from app.security.crypto import SecretBox

BASE_URL = os.environ.get("CRM_TEST_DATABASE_URL")
CONTROL_MIGRATIONS = Path(__file__).resolve().parent.parent / "app" / "migrations" / "control"


def make_settings(**overrides) -> Settings:
    env = {"CRM_DATABASE_URL": BASE_URL or "postgresql://x:y@localhost/z",
           "CRM_MASTER_KEY": base64.b64encode(os.urandom(32)).decode(), "CRM_JWT_SECRET": "j" * 40,
           "CRM_API_PUBLIC_URL": "https://api.example.com", **overrides}
    return Settings(_env_file=None, **env)


@pytest_asyncio.fixture
async def control_pool():
    """A brand-new, fully migrated control-plane database for each test."""
    if not BASE_URL:
        pytest.skip("CRM_TEST_DATABASE_URL is not set")
    admin = await asyncpg.connect(BASE_URL)
    name = "t_" + uuid.uuid4().hex[:12]
    await admin.execute(f"create database {name}")
    pool = await asyncpg.create_pool(BASE_URL.rsplit("/", 1)[0] + "/" + name, min_size=1, max_size=8,
                                     statement_cache_size=0)
    try:
        async with pool.acquire() as conn:
            await migrator.migrate(conn, CONTROL_MIGRATIONS)
        yield pool
    finally:
        await pool.close()
        await admin.execute(f"drop database if exists {name} with (force)")
        await admin.close()


async def make_tenant(pool, label: str = "t") -> uuid.UUID:
    user_id = await pool.fetchval(
        "insert into users (email, password_hash) values ($1, 'x') returning id", f"{label}-{uuid.uuid4().hex[:8]}@example.com")
    return await pool.fetchval("insert into tenants (name, owner_user_id) values ($1, $2) returning id", label, user_id)


@pytest.fixture
def box():
    return SecretBox(os.urandom(32))
