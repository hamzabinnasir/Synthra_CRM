"""Webhook authenticity checks. Pure functions, no I/O."""
from __future__ import annotations

import hashlib
import hmac
import re
from typing import Optional

_SIG = re.compile(r"^sha256=([0-9a-fA-F]{64})$")
_CHALLENGE = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")


def sign(app_secret: str, body: bytes) -> str:
    """The header value Meta sends: 'sha256=' + HMAC-SHA256(app_secret, raw body)."""
    return "sha256=" + hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def valid_signature(app_secret: str, body: bytes, header: Optional[str]) -> bool:
    """Constant-time check of X-Hub-Signature-256 against the exact bytes received."""
    if not app_secret or not header:
        return False
    match = _SIG.match(header.strip())
    if not match:
        return False
    expected = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, match.group(1).lower())


def valid_verify_token(expected: str, supplied: Optional[str]) -> bool:
    if not expected or not supplied:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8"))


def safe_challenge(challenge: Optional[str]) -> Optional[str]:
    """Meta's challenge is a number. Echo only a conservative token so nothing else is ever reflected."""
    return challenge if challenge and _CHALLENGE.match(challenge) else None
