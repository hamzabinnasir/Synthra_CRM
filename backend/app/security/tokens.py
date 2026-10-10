"""Access tokens (short-lived JWT) and opaque tokens (refresh, email verification, password reset)."""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Tuple
from uuid import UUID

import jwt

ISSUER = "synthra-crm"
AUDIENCE = "synthra-crm-web"


class TokenError(Exception):
    """The token is missing, malformed, expired or has a bad signature."""


@dataclass(frozen=True)
class AccessClaims:
    user_id: UUID
    session_id: UUID
    tenant_id: UUID
    expires_at: datetime


def new_opaque_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """Only this hash is stored, so a database leak does not expose usable tokens."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def encode_access_token(secret: str, *, user_id: UUID, session_id: UUID, tenant_id: UUID,
                        now: datetime, ttl: timedelta) -> Tuple[str, datetime]:
    expires = now + ttl
    payload = {
        "iss": ISSUER, "aud": AUDIENCE, "typ": "access",
        "sub": str(user_id), "sid": str(session_id), "tid": str(tenant_id),
        "iat": int(now.timestamp()), "exp": int(expires.timestamp()),
    }
    return jwt.encode(payload, secret, algorithm="HS256"), expires


def decode_access_token(secret: str, token: str, *, now: datetime = None) -> AccessClaims:
    try:
        claims = jwt.decode(
            token, secret, algorithms=["HS256"], audience=AUDIENCE, issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "sid", "tid"]},
            leeway=0,
        )
        if claims.get("typ") != "access":
            raise TokenError("wrong token type")
        if now is not None and claims["exp"] <= int(now.timestamp()):
            raise TokenError("expired")
        return AccessClaims(
            user_id=UUID(claims["sub"]), session_id=UUID(claims["sid"]), tenant_id=UUID(claims["tid"]),
            expires_at=datetime.fromtimestamp(claims["exp"], tz=timezone.utc),
        )
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise TokenError("invalid access token") from exc
