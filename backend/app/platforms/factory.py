"""Build platform and LLM clients for a workspace channel from its decrypted credentials."""
from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from ..channels.registry import Channel
from ..config import Settings
from ..llm.client import LLMConfig
from .meta import MetaClient


def token_key(channel: Channel) -> str:
    """The name of the channel's access-token setting (access_token or page_access_token)."""
    return next(f.key for f in channel.fields if f.secret and f.key.endswith("access_token"))


def build_client(settings: Settings, channel: Channel, creds: Optional[Dict[str, Any]], *,
                 http: Optional[httpx.AsyncClient] = None) -> Optional[MetaClient]:
    """None when the token or account id is missing. Pass a shared `http` client to reuse connections."""
    if not creds:
        return None
    token = creds["secrets"].get(token_key(channel), "")
    account = creds["values"].get(channel.account_field, "")
    if not token or not account:
        return None
    try:
        return MetaClient(channel, token=token, account_id=str(account), graph_version=settings.graph_version,
                          instagram_version=settings.instagram_graph_version, client=http)
    except ValueError:
        return None


def llm_config(creds: Dict[str, Any]) -> LLMConfig:
    behaviour = creds.get("behaviour", {})
    return LLMConfig(provider=behaviour.get("llm_provider") or "anthropic",
                     api_key=creds["secrets"].get("llm_api_key", ""),
                     model=behaviour.get("llm_model", ""))
