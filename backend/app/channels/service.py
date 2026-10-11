"""Per-workspace channel configuration: encrypted credentials, masked display, verify tokens, status.

Storage layout (control database, table channel_configs):
  encrypted_secrets  one AES-256-GCM blob holding {"app_secret": ..., "access_token": ..., "verify_token": ...}
  settings           {"values": {non-secret ids}, "behaviour": {prompt, tone, ...}}

Secrets are never returned to the browser. The only credential the dashboard may display in full is the
webhook verify token, because the user has to paste it into Meta; it can be rotated at any time.
"""
from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

import asyncpg

from ..config import Settings
from ..security.crypto import DecryptionError, SecretBox, mask_secret
from . import probe
from .registry import (CHANNEL_IDS, Channel, ValidationFailed, get_channel, validate_behaviour, validate_values)

log = logging.getLogger("app.channels")

DEFAULT_BEHAVIOUR: Dict[str, str] = {
    "llm_provider": "anthropic", "llm_model": "", "system_prompt": "", "tone": "friendly",
    "spam_action": "none", "abuse_action": "none", "business_phone": "",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: Any) -> Dict[str, Any]:
    if isinstance(value, (str, bytes)):
        return json.loads(value)
    return dict(value or {})


@dataclass(frozen=True)
class WebhookConfig:
    enabled: bool
    app_secret: str
    verify_token: str
    account_id: str


class ChannelService:
    def __init__(self, pool: asyncpg.Pool, box: SecretBox, settings: Settings) -> None:
        self._pool, self._box, self._settings = pool, box, settings

    # ---- helpers ---------------------------------------------------------------------------
    @staticmethod
    def _ctx(tenant_id: UUID, channel_id: str) -> str:
        return f"{tenant_id}:{channel_id}:secrets"

    def webhook_url(self, tenant_id: UUID, channel_id: str) -> str:
        return f"{self._settings.api_public_url.rstrip('/')}/webhooks/{tenant_id}/{channel_id}"

    def _open(self, tenant_id: UUID, channel_id: str, row: asyncpg.Record) -> Dict[str, str]:
        if not row["encrypted_secrets"]:
            return {}
        return json.loads(self._box.decrypt(row["encrypted_secrets"], context=self._ctx(tenant_id, channel_id)))

    def _seal(self, tenant_id: UUID, channel_id: str, secrets_map: Dict[str, str]) -> str:
        return self._box.encrypt(json.dumps(secrets_map, separators=(",", ":")), context=self._ctx(tenant_id, channel_id))

    @staticmethod
    def _channel(channel_id: str) -> Channel:
        channel = get_channel(channel_id)
        if channel is None:
            from ..errors import NotFound
            raise NotFound("Unknown channel.", code="unknown_channel")
        return channel

    async def _ensure(self, conn: asyncpg.Connection, tenant_id: UUID, channel: Channel) -> asyncpg.Record:
        """Fetch the row, creating it (with a fresh verify token) the first time a channel is opened."""
        row = await conn.fetchrow("select * from channel_configs where tenant_id = $1 and channel = $2 for update",
                                  tenant_id, channel.id)
        if row is None:
            sealed = self._seal(tenant_id, channel.id, {"verify_token": secrets.token_urlsafe(32)})
            await conn.execute(
                "insert into channel_configs (tenant_id, channel, encrypted_secrets, settings) values ($1, $2, $3, $4::jsonb) "
                "on conflict (tenant_id, channel) do nothing",
                tenant_id, channel.id, sealed, json.dumps({"values": {}, "behaviour": {}}))
            row = await conn.fetchrow("select * from channel_configs where tenant_id = $1 and channel = $2 for update",
                                      tenant_id, channel.id)
        return row

    def _view(self, tenant_id: UUID, channel: Channel, row: asyncpg.Record) -> Dict[str, Any]:
        secret_map = self._open(tenant_id, channel.id, row)
        settings = _json(row["settings"])
        stored = settings.get("values", {})
        values: Dict[str, Dict[str, Any]] = {}
        for spec in channel.fields:
            if spec.secret:
                raw = secret_map.get(spec.key, "")
                values[spec.key] = {"saved": bool(raw), "masked": mask_secret(raw) if raw else ""}
            else:
                raw = stored.get(spec.key, "")
                values[spec.key] = {"saved": bool(raw), "value": raw}
        missing = [f.key for f in channel.fields if f.required and not values[f.key]["saved"]]
        return {
            **channel.public(),
            "enabled": row["enabled"], "bot_enabled": row["bot_enabled"], "status": row["status"],
            "last_tested_at": row["last_tested_at"].isoformat() if row["last_tested_at"] else None,
            "last_error": row["last_error"],
            "values": values, "missing": missing,
            "behaviour": {**DEFAULT_BEHAVIOUR, **settings.get("behaviour", {})},
            "webhook": {"url": self.webhook_url(tenant_id, channel.id), "verify_token": secret_map.get("verify_token", ""),
                        "subscribe_to": list(channel.webhook_fields)},
        }

    # ---- dashboard operations --------------------------------------------------------------
    async def list(self, tenant_id: UUID) -> List[Dict[str, Any]]:
        out = []
        for channel_id in CHANNEL_IDS:
            out.append(await self.get(tenant_id, channel_id))
        return out

    async def get(self, tenant_id: UUID, channel_id: str) -> Dict[str, Any]:
        channel = self._channel(channel_id)
        async with self._pool.acquire() as conn, conn.transaction():
            row = await self._ensure(conn, tenant_id, channel)
        return self._view(tenant_id, channel, row)

    async def save(self, tenant_id: UUID, channel_id: str, *, values: Dict[str, Any], behaviour: Dict[str, Any],
                   enabled: Optional[bool], bot_enabled: Optional[bool]) -> Dict[str, Any]:
        channel = self._channel(channel_id)
        clean_values = validate_values(channel, values)
        clean_behaviour = validate_behaviour(behaviour)
        async with self._pool.acquire() as conn, conn.transaction():
            row = await self._ensure(conn, tenant_id, channel)
            secret_map = self._open(tenant_id, channel.id, row)
            settings = _json(row["settings"])
            stored = dict(settings.get("values", {}))
            changed = False
            for key, value in clean_values.items():
                if channel.field(key).secret:
                    changed |= secret_map.get(key) != value
                    secret_map[key] = value
                else:
                    changed |= stored.get(key) != value
                    stored[key] = value
            merged_behaviour = {**settings.get("behaviour", {}), **clean_behaviour}
            have = {f.key for f in channel.fields if f.required and (
                (f.secret and secret_map.get(f.key)) or (not f.secret and stored.get(f.key)))}
            complete = all(f.key in have for f in channel.fields if f.required)
            new_enabled = row["enabled"] if enabled is None else enabled
            if new_enabled and not complete:
                raise ValidationFailed({"enabled": "Fill in every required setting before turning this channel on."})
            status = row["status"]
            if not complete:
                status = "not_configured"
            elif changed or status == "not_configured":
                status = "configured"       # credentials changed, so the last test result no longer applies
            await conn.execute(
                "update channel_configs set encrypted_secrets = $3, secret_keys = $4, settings = $5::jsonb, "
                "enabled = $6, bot_enabled = $7, status = $8, "
                "last_error = case when $9 then null else last_error end, "
                "last_tested_at = case when $9 then null else last_tested_at end, updated_at = $10 "
                "where tenant_id = $1 and channel = $2",
                tenant_id, channel.id, self._seal(tenant_id, channel.id, secret_map),
                sorted(k for k, v in secret_map.items() if v and k != "verify_token"),
                json.dumps({"values": stored, "behaviour": merged_behaviour}),
                new_enabled, row["bot_enabled"] if bot_enabled is None else bot_enabled, status, changed, _now())
            row = await conn.fetchrow("select * from channel_configs where tenant_id = $1 and channel = $2",
                                      tenant_id, channel.id)
        return self._view(tenant_id, channel, row)

    async def rotate_verify_token(self, tenant_id: UUID, channel_id: str) -> Dict[str, Any]:
        channel = self._channel(channel_id)
        async with self._pool.acquire() as conn, conn.transaction():
            row = await self._ensure(conn, tenant_id, channel)
            secret_map = self._open(tenant_id, channel.id, row)
            secret_map["verify_token"] = secrets.token_urlsafe(32)
            await conn.execute("update channel_configs set encrypted_secrets = $3, updated_at = $4 "
                               "where tenant_id = $1 and channel = $2",
                               tenant_id, channel.id, self._seal(tenant_id, channel.id, secret_map), _now())
            row = await conn.fetchrow("select * from channel_configs where tenant_id = $1 and channel = $2",
                                      tenant_id, channel.id)
        return self._view(tenant_id, channel, row)

    async def clear(self, tenant_id: UUID, channel_id: str) -> None:
        """Forget every credential and turn the channel off. Webhook URL stays but accepts nothing."""
        channel = self._channel(channel_id)
        await self._pool.execute("delete from channel_configs where tenant_id = $1 and channel = $2", tenant_id, channel.id)

    async def test(self, tenant_id: UUID, channel_id: str) -> Dict[str, Any]:
        channel = self._channel(channel_id)
        row = await self._pool.fetchrow("select * from channel_configs where tenant_id = $1 and channel = $2",
                                        tenant_id, channel.id)
        if row is None:
            return {"ok": False, "message": "Save your settings first.", "details": {}, "warnings": []}
        secret_map = self._open(tenant_id, channel.id, row)
        stored = _json(row["settings"]).get("values", {})
        token_key = next((f.key for f in channel.fields if f.secret and f.key.endswith("access_token")), None)
        token, account = secret_map.get(token_key or "", ""), stored.get(channel.account_field, "")
        if not token or not account:
            return {"ok": False, "message": "Enter the account ID and access token, save, then test again.",
                    "details": {}, "warnings": []}
        version = self._settings.instagram_graph_version if channel.platform == "instagram" else self._settings.graph_version
        result = await probe.run(channel, token=token, account_id=account, version=version)
        await self._pool.execute(
            "update channel_configs set status = $3, last_tested_at = $4, last_error = $5 "
            "where tenant_id = $1 and channel = $2",
            tenant_id, channel.id, "connected" if result.ok else "error", _now(), None if result.ok else result.message[:480])
        return {"ok": result.ok, "message": result.message, "details": result.details, "warnings": result.warnings}

    # ---- used by the webhook handler and the bot engine -------------------------------------
    async def webhook_config(self, tenant_id: UUID, channel_id: str) -> Optional[WebhookConfig]:
        if get_channel(channel_id) is None:
            return None
        row = await self._pool.fetchrow(
            "select enabled, encrypted_secrets, settings from channel_configs where tenant_id = $1 and channel = $2",
            tenant_id, channel_id)
        if row is None:
            return None
        try:
            secret_map = self._open(tenant_id, channel_id, row)
        except DecryptionError:
            log.error("channel_secret_unreadable", extra={"channel": channel_id})
            return None
        channel = get_channel(channel_id)
        account = _json(row["settings"]).get("values", {}).get(channel.account_field, "")
        return WebhookConfig(bool(row["enabled"]), secret_map.get("app_secret", ""), secret_map.get("verify_token", ""), account)

    async def credentials(self, tenant_id: UUID, channel_id: str) -> Optional[Dict[str, Any]]:
        """Full decrypted configuration for the bot engine. Never expose this over HTTP."""
        channel = self._channel(channel_id)
        row = await self._pool.fetchrow("select * from channel_configs where tenant_id = $1 and channel = $2",
                                        tenant_id, channel.id)
        if row is None:
            return None
        settings = _json(row["settings"])
        return {"enabled": row["enabled"], "bot_enabled": row["bot_enabled"],
                "secrets": self._open(tenant_id, channel.id, row), "values": settings.get("values", {}),
                "behaviour": {**DEFAULT_BEHAVIOUR, **settings.get("behaviour", {})}}
