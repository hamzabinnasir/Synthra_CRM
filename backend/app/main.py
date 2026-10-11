"""Synthra CRM API entrypoint.  Run:  uvicorn app.main:app --reload"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import migrator
from .api import auth as auth_routes
from .api import database as database_routes
from .api import channels as channel_routes
from .api import webhooks as webhook_routes
from .channels.service import ChannelService
from .jobs.queue import EventQueue
from .jobs.worker import Worker
from .auth.repo_pg import PgAuthRepository
from .auth.service import AuthService
from .config import get_settings
from .db import create_pool
from .errors import install_error_handlers
from .logging_setup import setup_logging
from .mailer import Mailer
from .security.crypto import SecretBox
from .tenantdb.service import TenantDatabaseService
from .web import RequestContextMiddleware

CONTROL_MIGRATIONS = Path(__file__).parent / "migrations" / "control"
log = logging.getLogger("app")


async def _housekeeping(repo: PgAuthRepository, tenant_db: TenantDatabaseService) -> None:
    while True:
        await asyncio.sleep(300)
        try:
            await tenant_db.reap_idle()
            await repo.purge_expired(datetime.now(timezone.utc))
        except Exception:
            log.exception("housekeeping_failed")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging()
    pool = await create_pool(settings.database_url)
    async with pool.acquire() as conn:
        pending = [s for s in await migrator.status(conn, CONTROL_MIGRATIONS) if s.state == "pending"]
        if pending and settings.auto_migrate:
            applied = await migrator.migrate(conn, CONTROL_MIGRATIONS)
            log.info("control_migrations_applied", extra={"count": len(applied)})
        elif pending:
            raise RuntimeError(
                f"{len(pending)} control-plane migration(s) pending. Set CRM_AUTO_MIGRATE=true or run the migrator.")
    repo = PgAuthRepository(pool)
    box = SecretBox.from_b64(settings.master_key, settings.master_key_previous)
    tenant_db = TenantDatabaseService(pool, box, settings)
    app.state.settings = settings
    app.state.pool = pool
    app.state.secret_box = box
    app.state.auth_service = AuthService(repo, settings, Mailer(settings))
    app.state.tenant_db = tenant_db
    app.state.channels = ChannelService(pool, box, settings)
    app.state.queue = EventQueue(pool)
    # Channel handlers are registered here by the bot engine. A channel with no handler is never
    # claimed, so its events wait safely in the queue.
    app.state.handlers = {}
    worker = Worker(app.state.queue, app.state.handlers, concurrency=settings.worker_concurrency)
    stop_worker = asyncio.Event()
    worker_task = asyncio.create_task(worker.run(stop_worker)) if settings.run_worker else None
    task = asyncio.create_task(_housekeeping(repo, tenant_db))
    try:
        yield
    finally:
        task.cancel()
        stop_worker.set()
        if worker_task:
            await worker_task
        await tenant_db.close_all()
        await pool.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Synthra CRM", version="0.2.0", lifespan=lifespan,
                  docs_url=None if settings.is_production else "/docs",
                  redoc_url=None, openapi_url=None if settings.is_production else "/openapi.json")
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.origins, allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Protection", "X-Request-ID"],
        max_age=600)
    app.add_middleware(RequestContextMiddleware, settings=settings)
    install_error_handlers(app)
    app.include_router(auth_routes.router)
    app.include_router(database_routes.router)
    app.include_router(channel_routes.router)
    app.include_router(webhook_routes.router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz():
        return {"status": "ready" if await app.state.auth_service.repo.ping() else "degraded"}

    return app


app = create_app()
