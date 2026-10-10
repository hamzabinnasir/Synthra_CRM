"""Authentication business logic: register, verify, login, refresh rotation, logout, password reset.

Security properties:
* Passwords: argon2id. Unknown accounts are verified against a dummy hash (no timing oracle).
* Lockout: failures are counted per email and per IP in a shared fixed window.
* Refresh tokens rotate on every use. Replaying a used token (outside a short grace for concurrent
  tabs) revokes the whole session.
* Registration and reset requests answer the same whether or not the email exists.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from ..config import Settings
from ..errors import BadRequest, Forbidden, TooManyRequests, Unauthorized
from ..mailer import Mailer
from ..security import passwords, tokens
from .models import AuthRepository, EmailTaken

VERIFY, RESET = "verify_email", "reset_password"


@dataclass(frozen=True)
class Issued:
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime
    user_id: UUID
    tenant_id: UUID


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AuthService:
    def __init__(self, repo: AuthRepository, settings: Settings, mailer: Mailer) -> None:
        self.repo, self.s, self.mailer = repo, settings, mailer

    # ---- helpers ---------------------------------------------------------------------------
    def _link(self, path: str, token: str) -> str:
        return f"{self.s.public_url.rstrip('/')}{path}?token={token}"

    async def _issue(self, user_id: UUID, tenant_id: UUID, session_id: UUID, now: datetime) -> Issued:
        refresh = tokens.new_opaque_token()
        refresh_exp = now + timedelta(days=self.s.refresh_ttl_days)
        await self.repo.insert_refresh_token(
            session_id=session_id, token_hash=tokens.hash_token(refresh), expires_at=refresh_exp)
        access, access_exp = tokens.encode_access_token(
            self.s.jwt_secret, user_id=user_id, session_id=session_id, tenant_id=tenant_id,
            now=now, ttl=timedelta(minutes=self.s.access_ttl_minutes))
        return Issued(access, access_exp, refresh, refresh_exp, user_id, tenant_id)

    async def _limit(self, key: str, max_hits: int, window_s: int, now: datetime) -> None:
        count, retry = await self.repo.rate_hit(key, window_s, now)
        if count > max_hits:
            raise TooManyRequests(retry)

    # ---- registration / verification -------------------------------------------------------
    async def register(self, *, email: str, password: str, display_name: str, workspace_name: str,
                       ip: Optional[str], user_agent: Optional[str]) -> None:
        now = _now()
        await self._limit(f"register:ip:{ip}", 10, 3600, now)
        problem = passwords.password_problem(password, email)
        if problem:
            raise BadRequest(problem, code="weak_password")
        pw_hash = await passwords.hash_password(password)
        try:
            user, tenant = await self.repo.create_user_with_tenant(
                email=email, password_hash=pw_hash, display_name=display_name,
                workspace_name=workspace_name, now=now)
        except EmailTaken:
            # Same response as success so the endpoint cannot be used to discover registered emails.
            await self.repo.audit(event="register_duplicate", now=now, ip=ip, user_agent=user_agent)
            return
        await self.repo.audit(event="register", now=now, user_id=user.id, tenant_id=tenant.id,
                              ip=ip, user_agent=user_agent)
        if self.s.require_email_verification:
            await self._send_verification(user.id, email, now)
        else:
            await self.repo.mark_email_verified(user.id, now)

    async def _send_verification(self, user_id: UUID, email: str, now: datetime) -> None:
        token = tokens.new_opaque_token()
        await self.repo.create_auth_token(
            user_id=user_id, purpose=VERIFY, token_hash=tokens.hash_token(token),
            expires_at=now + timedelta(hours=self.s.verify_ttl_hours))
        await self.mailer.send_verification(email, self._link("/verify-email", token))

    async def verify_email(self, token: str, ip: Optional[str]) -> None:
        now = _now()
        await self._limit(f"verify:ip:{ip}", 30, 3600, now)
        user_id = await self.repo.consume_auth_token(token_hash=tokens.hash_token(token), purpose=VERIFY, now=now)
        if user_id is None:
            raise BadRequest("This verification link is invalid or has expired.", code="invalid_token")
        await self.repo.mark_email_verified(user_id, now)
        await self.repo.audit(event="email_verified", now=now, user_id=user_id, ip=ip)

    async def resend_verification(self, email: str, ip: Optional[str]) -> None:
        now = _now()
        await self._limit(f"resend:ip:{ip}", 10, 3600, now)
        await self._limit(f"resend:email:{email}", 3, 3600, now)
        user = await self.repo.get_user_by_email(email)
        if user and user.email_verified_at is None:
            await self._send_verification(user.id, email, now)

    # ---- login / refresh / logout ----------------------------------------------------------
    async def login(self, *, email: str, password: str, ip: Optional[str], user_agent: Optional[str]) -> Issued:
        now = _now()
        window = self.s.login_window_minutes * 60
        ip_key, email_key = f"login:ip:{ip}", f"login:fail:{email}"

        count, retry = await self.repo.rate_count(email_key, window, now)
        if count >= self.s.login_max_failures:
            await self.repo.audit(event="login_locked", now=now, ip=ip, user_agent=user_agent)
            raise TooManyRequests(retry, "Too many failed attempts. Try again later or reset your password.")
        await self._limit(ip_key, self.s.login_ip_max_attempts, window, now)

        user = await self.repo.get_user_by_email(email)
        stored = user.password_hash if user else await passwords.dummy_hash()
        ok = await passwords.verify_password(stored, password)
        if not user or not ok:
            await self.repo.rate_hit(email_key, window, now)
            await self.repo.audit(event="login_failed", now=now, user_id=user.id if user else None,
                                  ip=ip, user_agent=user_agent)
            raise Unauthorized("Incorrect email or password.", code="invalid_credentials")
        if self.s.require_email_verification and user.email_verified_at is None:
            raise Forbidden("Please verify your email address before signing in.", code="email_not_verified")

        if passwords.needs_rehash(user.password_hash):
            await self.repo.set_password(user.id, await passwords.hash_password(password), now)
        await self.repo.rate_reset(email_key)
        tenant = await self.repo.get_primary_tenant(user.id)
        if tenant is None:
            raise Forbidden("This account has no workspace.", code="no_workspace")
        session_id = await self.repo.create_session(user_id=user.id, ip=ip, user_agent=user_agent, now=now)
        await self.repo.audit(event="login", now=now, user_id=user.id, tenant_id=tenant.id,
                              ip=ip, user_agent=user_agent)
        return await self._issue(user.id, tenant.id, session_id, now)

    async def refresh(self, refresh_token: str, ip: Optional[str]) -> Issued:
        now = _now()
        await self._limit(f"refresh:ip:{ip}", 120, 60, now)
        outcome = await self.repo.claim_refresh_token(tokens.hash_token(refresh_token), now)
        if outcome.status == "reused":
            grace = timedelta(seconds=self.s.refresh_reuse_grace_seconds)
            if outcome.used_at and now - outcome.used_at < grace:
                # Two tabs refreshed at once. Refuse this one; the other tab holds the new token.
                raise Unauthorized("Session is refreshing, retry.", code="refresh_race")
            await self.repo.revoke_session(outcome.session_id, "refresh_reuse", now)
            await self.repo.audit(event="refresh_reuse_detected", now=now, user_id=outcome.user_id, ip=ip)
            raise Unauthorized("Session expired. Please sign in again.", code="session_revoked")
        if outcome.status != "ok":
            raise Unauthorized("Session expired. Please sign in again.", code="session_expired")
        tenant = await self.repo.get_primary_tenant(outcome.user_id)
        if tenant is None:
            raise Unauthorized("Session expired. Please sign in again.", code="session_expired")
        await self.repo.touch_session(outcome.session_id, now)
        return await self._issue(outcome.user_id, tenant.id, outcome.session_id, now)

    async def logout(self, refresh_token: Optional[str], session_id: Optional[UUID]) -> None:
        now = _now()
        if refresh_token:
            session_id = await self.repo.session_id_for_refresh_hash(tokens.hash_token(refresh_token)) or session_id
        if session_id:
            await self.repo.revoke_session(session_id, "logout", now)

    async def logout_everywhere(self, user_id: UUID) -> None:
        await self.repo.revoke_all_sessions(user_id, "logout_all", _now())

    # ---- password reset --------------------------------------------------------------------
    async def request_reset(self, email: str, ip: Optional[str]) -> None:
        now = _now()
        await self._limit(f"reset:ip:{ip}", 10, 3600, now)
        await self._limit(f"reset:email:{email}", 3, 3600, now)
        user = await self.repo.get_user_by_email(email)
        if not user:
            return  # identical response either way
        token = tokens.new_opaque_token()
        await self.repo.create_auth_token(
            user_id=user.id, purpose=RESET, token_hash=tokens.hash_token(token),
            expires_at=now + timedelta(minutes=self.s.reset_ttl_minutes))
        await self.mailer.send_password_reset(email, self._link("/reset-password", token))
        await self.repo.audit(event="reset_requested", now=now, user_id=user.id, ip=ip)

    async def reset_password(self, token: str, new_password: str, ip: Optional[str]) -> None:
        now = _now()
        await self._limit(f"resetconfirm:ip:{ip}", 20, 3600, now)
        problem = passwords.password_problem(new_password)
        if problem:
            raise BadRequest(problem, code="weak_password")
        user_id = await self.repo.consume_auth_token(token_hash=tokens.hash_token(token), purpose=RESET, now=now)
        if user_id is None:
            raise BadRequest("This reset link is invalid or has expired.", code="invalid_token")
        await self.repo.set_password(user_id, await passwords.hash_password(new_password), now)
        await self.repo.revoke_all_sessions(user_id, "password_reset", now)
        await self.repo.audit(event="password_reset", now=now, user_id=user_id, ip=ip)

    # ---- request authentication ------------------------------------------------------------
    async def authenticate_access(self, access_token: str) -> tokens.AccessClaims:
        try:
            claims = tokens.decode_access_token(self.s.jwt_secret, access_token)
        except tokens.TokenError as exc:
            raise Unauthorized("Invalid or expired token.", code="invalid_token") from exc
        if not await self.repo.is_session_active(claims.session_id):
            raise Unauthorized("Session has been signed out.", code="session_revoked")
        return claims
