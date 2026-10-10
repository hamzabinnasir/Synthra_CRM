"""In-memory AuthRepository. Used by unit tests and as the executable specification of the contract
(the same contract tests run against this and against PostgreSQL)."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

from .models import ClaimOutcome, EmailTaken, Tenant, User
from .windows import window_bounds


@dataclass
class _Session:
    id: UUID
    user_id: UUID
    revoked_at: Optional[datetime] = None


@dataclass
class _Refresh:
    session_id: UUID
    expires_at: datetime
    used_at: Optional[datetime] = None


@dataclass
class _AuthToken:
    user_id: UUID
    purpose: str
    expires_at: datetime
    used_at: Optional[datetime] = None


class MemoryAuthRepository:
    def __init__(self) -> None:
        self.users: Dict[UUID, User] = {}
        self.tenants: Dict[UUID, Tenant] = {}
        self.members: Dict[UUID, List[UUID]] = {}
        self.sessions: Dict[UUID, _Session] = {}
        self.refresh: Dict[str, _Refresh] = {}
        self.auth_tokens: Dict[str, _AuthToken] = {}
        self.rates: Dict[Tuple[str, datetime], int] = {}
        self.audit_log: List[Dict[str, Any]] = []

    async def create_user_with_tenant(self, *, email, password_hash, display_name, workspace_name, now):
        if any(u.email == email for u in self.users.values()):
            raise EmailTaken(email)
        user = User(uuid4(), email, None, password_hash, display_name, now)
        tenant = Tenant(uuid4(), workspace_name, user.id)
        self.users[user.id] = user
        self.tenants[tenant.id] = tenant
        self.members.setdefault(user.id, []).append(tenant.id)
        return user, tenant

    async def get_user_by_email(self, email):
        return next((u for u in self.users.values() if u.email == email), None)

    async def get_user(self, user_id):
        return self.users.get(user_id)

    async def get_primary_tenant(self, user_id):
        ids = self.members.get(user_id) or []
        return self.tenants.get(ids[0]) if ids else None

    async def mark_email_verified(self, user_id, now):
        user = self.users[user_id]
        if user.email_verified_at is None:
            self.users[user_id] = replace(user, email_verified_at=now)

    async def set_password(self, user_id, password_hash, now):
        self.users[user_id] = replace(self.users[user_id], password_hash=password_hash)

    async def create_session(self, *, user_id, ip, user_agent, now):
        session = _Session(uuid4(), user_id)
        self.sessions[session.id] = session
        return session.id

    async def insert_refresh_token(self, *, session_id, token_hash, expires_at):
        self.refresh[token_hash] = _Refresh(session_id, expires_at)

    async def claim_refresh_token(self, token_hash, now):
        record = self.refresh.get(token_hash)
        if record is None:
            return ClaimOutcome("unknown")
        session = self.sessions[record.session_id]
        if session.revoked_at is not None:
            return ClaimOutcome("revoked", record.session_id, session.user_id)
        if record.used_at is not None:
            return ClaimOutcome("reused", record.session_id, session.user_id, record.used_at)
        if record.expires_at <= now:
            return ClaimOutcome("expired", record.session_id, session.user_id)
        record.used_at = now
        return ClaimOutcome("ok", record.session_id, session.user_id)

    async def session_id_for_refresh_hash(self, token_hash):
        record = self.refresh.get(token_hash)
        return record.session_id if record else None

    async def touch_session(self, session_id, now):
        return None

    async def revoke_session(self, session_id, reason, now):
        session = self.sessions.get(session_id)
        if session and session.revoked_at is None:
            session.revoked_at = now

    async def revoke_all_sessions(self, user_id, reason, now):
        for session in self.sessions.values():
            if session.user_id == user_id and session.revoked_at is None:
                session.revoked_at = now

    async def is_session_active(self, session_id):
        session = self.sessions.get(session_id)
        return bool(session and session.revoked_at is None)

    async def create_auth_token(self, *, user_id, purpose, token_hash, expires_at):
        # A newer token replaces older unused ones of the same purpose.
        for key in [k for k, t in self.auth_tokens.items()
                    if t.user_id == user_id and t.purpose == purpose and t.used_at is None]:
            del self.auth_tokens[key]
        self.auth_tokens[token_hash] = _AuthToken(user_id, purpose, expires_at)

    async def consume_auth_token(self, *, token_hash, purpose, now):
        token = self.auth_tokens.get(token_hash)
        if token is None or token.purpose != purpose or token.used_at is not None or token.expires_at <= now:
            return None
        token.used_at = now
        return token.user_id

    async def rate_hit(self, key, window_seconds, now):
        start, retry = window_bounds(now, window_seconds)
        self.rates[(key, start)] = self.rates.get((key, start), 0) + 1
        return self.rates[(key, start)], retry

    async def rate_count(self, key, window_seconds, now):
        start, retry = window_bounds(now, window_seconds)
        return self.rates.get((key, start), 0), retry

    async def rate_reset(self, key):
        for item in [k for k in self.rates if k[0] == key]:
            del self.rates[item]

    async def audit(self, *, event, now, user_id=None, tenant_id=None, ip=None, user_agent=None, detail=None):
        self.audit_log.append({"event": event, "user_id": user_id, "tenant_id": tenant_id,
                               "ip": ip, "detail": detail or {}, "at": now})

    async def purge_expired(self, now):
        self.rates = {k: v for k, v in self.rates.items() if k[1] > now - timedelta(days=1)}

    async def ping(self):
        return True
