"""Password hashing (argon2id) and password policy."""
from __future__ import annotations

import asyncio
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# argon2-cffi defaults follow RFC 9106 (argon2id, 64 MiB, t=3, p=4).
_hasher = PasswordHasher()

# Each hash uses ~64 MiB, so cap concurrency to keep a login flood from exhausting memory.
_MAX_CONCURRENT = 4
_semaphore: Optional[asyncio.Semaphore] = None

MIN_LENGTH, MAX_LENGTH = 10, 128
_COMMON = {
    "password", "password1", "password123", "1234567890", "qwertyuiop", "letmein123",
    "iloveyou12", "admin12345", "welcome123", "changeme123", "0123456789", "1q2w3e4r5t",
}


def _sem() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(_MAX_CONCURRENT)
    return _semaphore


async def hash_password(password: str) -> str:
    async with _sem():
        return await asyncio.to_thread(_hasher.hash, password)


async def verify_password(password_hash: str, password: str) -> bool:
    async with _sem():
        return await asyncio.to_thread(_verify_sync, password_hash, password)


def _verify_sync(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


_dummy_hash: Optional[str] = None


async def dummy_hash() -> str:
    """A valid hash to verify against when the account does not exist, so response time doesn't reveal it."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = await hash_password("not-a-real-password-for-timing-only")
    return _dummy_hash


def password_problem(password: str, email: str = "") -> Optional[str]:
    """Return a human-readable reason the password is unacceptable, or None if it is fine."""
    if len(password) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters."
    if len(password) > MAX_LENGTH:
        return f"Use at most {MAX_LENGTH} characters."
    lowered = password.lower()
    if lowered in _COMMON:
        return "That password is too common."
    if email and lowered == email.lower():
        return "The password must not be your email address."
    if len(set(password)) < 5:
        return "Use a more varied password."
    return None
