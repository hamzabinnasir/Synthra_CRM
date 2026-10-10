"""AES-256-GCM encryption for stored credentials.

Ciphertext format:  enc:v1:<key-fingerprint>:<base64url(nonce || ciphertext+tag)>

* `context` is authenticated as AAD (for example "<tenant_id>:whatsapp:access_token"), so a
  ciphertext copied to another tenant or field fails to decrypt.
* The fingerprint identifies which master key encrypted a value, which allows key rotation:
  set the new key as CRM_MASTER_KEY and keep the old one in CRM_MASTER_KEY_PREVIOUS, then
  re-encrypt values where `needs_rotation()` is true.
"""
from __future__ import annotations

import base64
import hashlib
import os
from typing import Iterable, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PREFIX = "enc:v1"


class DecryptionError(Exception):
    """Raised for malformed ciphertext, an unknown key, a wrong context, or tampering."""


def _fingerprint(key: bytes) -> str:
    return hashlib.sha256(b"synthra-crm-key:" + key).hexdigest()[:8]


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SecretBox:
    def __init__(self, key: bytes, previous: Optional[Iterable[bytes]] = None):
        keys = [key, *(previous or [])]
        if any(len(k) != 32 for k in keys):
            raise ValueError("Master keys must be exactly 32 bytes")
        self._active = key
        self._active_fp = _fingerprint(key)
        self._keys = {_fingerprint(k): k for k in keys}

    @classmethod
    def from_b64(cls, key_b64: str, previous_b64: Optional[str] = None) -> "SecretBox":
        previous = [base64.b64decode(previous_b64)] if previous_b64 else []
        return cls(base64.b64decode(key_b64), previous)

    def encrypt(self, plaintext: str, *, context: str) -> str:
        nonce = os.urandom(12)
        sealed = AESGCM(self._active).encrypt(nonce, plaintext.encode("utf-8"), context.encode("utf-8"))
        return f"{PREFIX}:{self._active_fp}:{_b64u(nonce + sealed)}"

    def decrypt(self, token: str, *, context: str) -> str:
        try:
            prefix, version, fingerprint, blob = token.split(":", 3)
            if f"{prefix}:{version}" != PREFIX:
                raise ValueError("unknown format")
            key = self._keys[fingerprint]
            raw = _unb64u(blob)
            if len(raw) < 12 + 16:
                raise ValueError("too short")
            return AESGCM(key).decrypt(raw[:12], raw[12:], context.encode("utf-8")).decode("utf-8")
        except (ValueError, KeyError, InvalidTag, UnicodeDecodeError) as exc:
            # Deliberately vague: do not reveal whether the key, the context or the data was wrong.
            raise DecryptionError("Could not decrypt value") from exc

    def needs_rotation(self, token: str) -> bool:
        parts = token.split(":", 3)
        return len(parts) != 4 or parts[2] != self._active_fp


def mask_secret(value: str, visible: int = 4) -> str:
    """Display form for saved secrets. Short values are fully masked."""
    if not value:
        return ""
    if len(value) <= visible * 2:
        return "\u2022" * 8
    return "\u2022" * 8 + value[-visible:]
