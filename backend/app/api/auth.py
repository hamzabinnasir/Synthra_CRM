"""/api/auth routes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from ..auth.service import AuthService
from ..config import Settings
from ..errors import Unauthorized
from ..security.tokens import AccessClaims
from ..web import REFRESH_COOKIE, clear_refresh_cookie, csrf_guard, set_refresh_cookie
from .deps import current_claims, get_auth_service, get_ip, get_settings_dep

router = APIRouter(prefix="/api/auth", tags=["auth"])


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _Email(_Strict):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()


class RegisterIn(_Email):
    password: str = Field(min_length=1, max_length=128)
    display_name: str = Field("", max_length=100)
    workspace_name: str = Field(min_length=1, max_length=100)


class LoginIn(_Email):
    password: str = Field(min_length=1, max_length=128)


class TokenIn(_Strict):
    token: str = Field(min_length=10, max_length=200)


class ResetIn(TokenIn):
    new_password: str = Field(min_length=1, max_length=128)


class SessionOut(BaseModel):
    access_token: str
    expires_at: str
    user_id: str
    tenant_id: str


def _session_response(response: Response, settings: Settings, issued) -> SessionOut:
    ttl = int((issued.refresh_expires_at - issued.access_expires_at).total_seconds()) + settings.access_ttl_minutes * 60
    set_refresh_cookie(response, settings, issued.refresh_token, ttl)
    return SessionOut(access_token=issued.access_token, expires_at=issued.access_expires_at.isoformat(),
                      user_id=str(issued.user_id), tenant_id=str(issued.tenant_id))


@router.post("/register", status_code=202)
async def register(body: RegisterIn, request: Request, service: AuthService = Depends(get_auth_service),
                   ip: Optional[str] = Depends(get_ip)):
    await service.register(email=body.email, password=body.password, display_name=body.display_name,
                           workspace_name=body.workspace_name, ip=ip, user_agent=request.headers.get("user-agent"))
    return {"message": "If the address can be used, a verification email is on its way."}


@router.post("/verify-email")
async def verify_email(body: TokenIn, service: AuthService = Depends(get_auth_service),
                       ip: Optional[str] = Depends(get_ip)):
    await service.verify_email(body.token, ip)
    return {"message": "Email verified. You can sign in."}


@router.post("/resend-verification", status_code=202)
async def resend(body: _Email, service: AuthService = Depends(get_auth_service),
                 ip: Optional[str] = Depends(get_ip)):
    await service.resend_verification(body.email, ip)
    return {"message": "If the account needs verification, an email is on its way."}


@router.post("/login", response_model=SessionOut)
async def login(body: LoginIn, request: Request, response: Response,
                service: AuthService = Depends(get_auth_service), settings: Settings = Depends(get_settings_dep),
                ip: Optional[str] = Depends(get_ip)):
    issued = await service.login(email=body.email, password=body.password, ip=ip,
                                 user_agent=request.headers.get("user-agent"))
    return _session_response(response, settings, issued)


@router.post("/refresh", response_model=SessionOut)
async def refresh(request: Request, response: Response, service: AuthService = Depends(get_auth_service),
                  settings: Settings = Depends(get_settings_dep), ip: Optional[str] = Depends(get_ip)):
    csrf_guard(request, settings)
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise Unauthorized("Not signed in.", code="not_authenticated")
    try:
        issued = await service.refresh(token, ip)
    except Unauthorized as exc:
        if exc.code != "refresh_race":
            clear_refresh_cookie(response, settings)
            exc.headers = {**exc.headers, **{k: v for k, v in response.headers.items() if k.lower() == "set-cookie"}}
        raise
    return _session_response(response, settings, issued)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, service: AuthService = Depends(get_auth_service),
                 settings: Settings = Depends(get_settings_dep)):
    csrf_guard(request, settings)
    await service.logout(request.cookies.get(REFRESH_COOKIE), None)
    clear_refresh_cookie(response, settings)
    response.status_code = 204
    return response


@router.post("/logout-all", status_code=204)
async def logout_all(response: Response, claims: AccessClaims = Depends(current_claims),
                     service: AuthService = Depends(get_auth_service),
                     settings: Settings = Depends(get_settings_dep)):
    await service.logout_everywhere(claims.user_id)
    clear_refresh_cookie(response, settings)
    response.status_code = 204
    return response


@router.post("/forgot-password", status_code=202)
async def forgot(body: _Email, service: AuthService = Depends(get_auth_service),
                 ip: Optional[str] = Depends(get_ip)):
    await service.request_reset(body.email, ip)
    return {"message": "If the account exists, a reset email is on its way."}


@router.post("/reset-password")
async def reset(body: ResetIn, service: AuthService = Depends(get_auth_service),
                ip: Optional[str] = Depends(get_ip)):
    await service.reset_password(body.token, body.new_password, ip)
    return {"message": "Password updated. Please sign in."}


@router.get("/me")
async def me(claims: AccessClaims = Depends(current_claims), service: AuthService = Depends(get_auth_service)):
    user = await service.repo.get_user(claims.user_id)
    tenant = await service.repo.get_primary_tenant(claims.user_id)
    if not user or not tenant:
        raise Unauthorized("Account not found.", code="not_authenticated")
    return {"user": {"id": str(user.id), "email": user.email, "display_name": user.display_name,
                     "email_verified": user.email_verified_at is not None},
            "tenant": {"id": str(tenant.id), "name": tenant.name}}
