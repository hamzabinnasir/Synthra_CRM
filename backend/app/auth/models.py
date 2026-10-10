"""Domain models and the repository contract for authentication."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Protocol, Tuple
from uuid import UUID


@dataclass(frozen=True)
class User:
    id: UUID
    email: str
    email_verified_at: Optional[datetime]
    password_hash: str
    display_name: str
    created_at: datetime


@dataclass(frozen=True)
class Tenant:
    id: UUID
    name: str
    owner_user_id: UUID


@dataclass(frozen=True)
class ClaimOutcome:
    """Result of trying to consume a refresh token.

    status: ok | unknown | expired | revoked | reused
    """
    status: str
    session_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    used_at: Optional[datetime] = None


class EmailTaken(Exception):
    pass


class AuthRepository(Protocol):
    # users / tenants
    async def create_user_with_tenant(self, *, email: str, password_hash: str, display_name: str,
                                      workspace_name: str, now: datetime) -> Tuple[User, Tenant]: ...
    async def get_user_by_email(self, email: str) -> Optional[User]: ...
    async def get_user(self, user_id: UUID) -> Optional[User]: ...
    async def get_primary_tenant(self, user_id: UUID) -> Optional[Tenant]: ...
    async def mark_email_verified(self, user_id: UUID, now: datetime) -> None: ...
    async def set_password(self, user_id: UUID, password_hash: str, now: datetime) -> None: ...

    # sessions and refresh tokens
    async def create_session(self, *, user_id: UUID, ip: Optional[str], user_agent: Optional[str],
                             now: datetime) -> UUID: ...
    async def insert_refresh_token(self, *, session_id: UUID, token_hash: str, expires_at: datetime) -> None: ...
    async def claim_refresh_token(self, token_hash: str, now: datetime) -> ClaimOutcome: ...
    async def session_id_for_refresh_hash(self, token_hash: str) -> Optional[UUID]: ...
    async def touch_session(self, session_id: UUID, now: datetime) -> None: ...
    async def revoke_session(self, session_id: UUID, reason: str, now: datetime) -> None: ...
    async def revoke_all_sessions(self, user_id: UUID, reason: str, now: datetime) -> None: ...
    async def is_session_active(self, session_id: UUID) -> bool: ...

    # one-time email tokens
    async def create_auth_token(self, *, user_id: UUID, purpose: str, token_hash: str,
                                expires_at: datetime) -> None: ...
    async def consume_auth_token(self, *, token_hash: str, purpose: str, now: datetime) -> Optional[UUID]: ...

    # fixed-window rate limiting shared across instances
    async def rate_hit(self, key: str, window_seconds: int, now: datetime) -> Tuple[int, int]: ...
    async def rate_count(self, key: str, window_seconds: int, now: datetime) -> Tuple[int, int]: ...
    async def rate_reset(self, key: str) -> None: ...

    # audit + housekeeping
    async def audit(self, *, event: str, now: datetime, user_id: Optional[UUID] = None,
                    tenant_id: Optional[UUID] = None, ip: Optional[str] = None,
                    user_agent: Optional[str] = None, detail: Optional[Dict[str, Any]] = None) -> None: ...
    async def purge_expired(self, now: datetime) -> None: ...
    async def ping(self) -> bool: ...
