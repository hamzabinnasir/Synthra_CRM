"""Shared FastAPI dependencies."""
from __future__ import annotations

from fastapi import Depends, Request

from ..auth.service import AuthService
from ..config import Settings
from ..errors import Unauthorized
from ..security.tokens import AccessClaims
from ..web import client_ip


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


def get_ip(request: Request) -> str | None:
    return client_ip(request, request.app.state.settings.trusted_proxy_hops)


async def current_claims(request: Request, service: AuthService = Depends(get_auth_service)) -> AccessClaims:
    """Authenticated caller. Every data query downstream must filter by claims.tenant_id."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Unauthorized("Authentication required.", code="not_authenticated")
    return await service.authenticate_access(token.strip())
