"""/api/database routes: connect your own Postgres/Supabase, preview and apply the schema."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from ..errors import BadRequest, TooManyRequests
from ..security.tokens import AccessClaims
from ..tenantdb import provisioner
from ..tenantdb.provisioner import DatabaseSetupError
from ..tenantdb.service import TenantDatabaseService
from .deps import current_claims

router = APIRouter(prefix="/api/database", tags=["database"])


class UrlIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=10, max_length=2048)


class MigrateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dry_run: bool = False


def svc(request: Request) -> TenantDatabaseService:
    return request.app.state.tenant_db


@contextmanager
def _user_errors():
    try:
        yield
    except DatabaseSetupError as exc:
        raise BadRequest(exc.message, code=exc.code, details={"hint": exc.hint} if exc.hint else None)


async def _throttle(request: Request, claims: AccessClaims, name: str, limit: int = 30) -> None:
    repo = request.app.state.auth_service.repo
    count, retry = await repo.rate_hit(f"db:{name}:{claims.tenant_id}", 3600, datetime.now(timezone.utc))
    if count > limit:
        raise TooManyRequests(retry)


@router.get("")
async def info(claims: AccessClaims = Depends(current_claims), s: TenantDatabaseService = Depends(svc)):
    return await s.info(claims.tenant_id)


@router.post("/test")
async def test_connection(body: UrlIn, request: Request, claims: AccessClaims = Depends(current_claims),
                          s: TenantDatabaseService = Depends(svc)):
    await _throttle(request, claims, "test")
    with _user_errors():
        return await s.test(body.url)


@router.put("")
async def save_connection(body: UrlIn, request: Request, claims: AccessClaims = Depends(current_claims),
                          s: TenantDatabaseService = Depends(svc)):
    await _throttle(request, claims, "save")
    with _user_errors():
        result = await s.save(claims.tenant_id, body.url)
    await request.app.state.auth_service.repo.audit(
        event="database_connected", now=datetime.now(timezone.utc), user_id=claims.user_id,
        tenant_id=claims.tenant_id, detail={"host": result.get("host_hint")})
    return result


@router.post("/migrate")
async def migrate(body: MigrateIn, request: Request, claims: AccessClaims = Depends(current_claims),
                  s: TenantDatabaseService = Depends(svc)):
    await _throttle(request, claims, "migrate")
    with _user_errors():
        result = await s.migrate(claims.tenant_id, dry_run=body.dry_run)
    if not body.dry_run:
        await request.app.state.auth_service.repo.audit(
            event="database_migrated", now=datetime.now(timezone.utc), user_id=claims.user_id,
            tenant_id=claims.tenant_id, detail={"applied": result.get("applied", [])})
    return result


@router.get("/migrations")
async def migrations(request: Request, claims: AccessClaims = Depends(current_claims),
                     s: TenantDatabaseService = Depends(svc)):
    await _throttle(request, claims, "status", 120)
    with _user_errors():
        return {"migrations": await s.migrations(claims.tenant_id)}


@router.post("/check")
async def recheck(request: Request, claims: AccessClaims = Depends(current_claims),
                  s: TenantDatabaseService = Depends(svc)):
    await _throttle(request, claims, "check", 60)
    with _user_errors():
        return await s.check(claims.tenant_id)


@router.get("/setup.sql", response_class=PlainTextResponse)
async def manual_sql(claims: AccessClaims = Depends(current_claims)):
    """Fallback: paste this into your SQL editor if automatic setup fails. Contains no secrets."""
    return PlainTextResponse(provisioner.manual_sql(), headers={
        "Content-Disposition": 'attachment; filename="synthra-crm-setup.sql"'})


@router.delete("", status_code=204)
async def disconnect(request: Request, claims: AccessClaims = Depends(current_claims),
                     s: TenantDatabaseService = Depends(svc)):
    """Forgets the stored connection. Data in your database is left untouched."""
    await s.disconnect(claims.tenant_id)
    await request.app.state.auth_service.repo.audit(
        event="database_disconnected", now=datetime.now(timezone.utc), user_id=claims.user_id,
        tenant_id=claims.tenant_id)
