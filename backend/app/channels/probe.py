"""'Test connection': a single read-only Graph API call per channel, with friendly error messages.

The token is sent in the Authorization header (never in the URL) and is never logged or echoed back.
Meta's own error text is not shown to the user, because it can quote request details; a fixed message
is chosen from Meta's numeric error code instead.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import httpx

from .registry import Channel

TIMEOUT = httpx.Timeout(10.0, connect=5.0)

_BY_CODE = {
    190: "The access token is invalid or has expired. Generate a new permanent token and paste it again.",
    102: "The access token is invalid or has expired. Generate a new permanent token and paste it again.",
    10: "The token does not have the permissions this channel needs. Re-generate it with the permissions listed in the setup guide.",
    200: "The token does not have the permissions this channel needs. Re-generate it with the permissions listed in the setup guide.",
    299: "The token does not have the permissions this channel needs. Re-generate it with the permissions listed in the setup guide.",
    100: "Meta could not find that ID, or this token cannot access it. Check the ID and that the token belongs to the same account.",
    803: "Meta could not find that ID. Check it for typos.",
    4: "Meta is rate limiting this app right now. Wait a minute and test again.",
    17: "Meta is rate limiting this app right now. Wait a minute and test again.",
    32: "Meta is rate limiting this app right now. Wait a minute and test again.",
    613: "Meta is rate limiting this app right now. Wait a minute and test again.",
}


@dataclass
class ProbeResult:
    ok: bool
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    warnings: list = field(default_factory=list)


def _request(channel: Channel, account_id: str, version: str) -> tuple[str, Dict[str, str]]:
    base = f"{channel.graph_host}/{version}"
    if channel.platform == "whatsapp":
        return f"{base}/{account_id}", {"fields": "display_phone_number,verified_name"}
    if channel.platform == "facebook":
        return f"{base}/{account_id}", {"fields": "id,name"}
    return f"{base}/me", {"fields": "user_id,username"}


async def run(channel: Channel, *, token: str, account_id: str, version: str,
              client: Optional[httpx.AsyncClient] = None) -> ProbeResult:
    url, params = _request(channel, account_id, version)
    owns = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False)
    try:
        response = await client.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
    except (httpx.TimeoutException, httpx.TransportError):
        return ProbeResult(False, "Meta did not respond in time. Try again in a moment.")
    finally:
        if owns:
            await client.aclose()

    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code != 200 or "error" in body:
        error = body.get("error") if isinstance(body.get("error"), dict) else {}
        code = error.get("code")
        message = _BY_CODE.get(code) if isinstance(code, int) else None
        if message is None:
            message = f"Meta rejected the request (HTTP {response.status_code}" + (f", error {code}" if code else "") + ")."
        return ProbeResult(False, message, {"http_status": response.status_code, "meta_error_code": code})

    details: Dict[str, Any] = {}
    warnings: list = []
    if channel.platform == "whatsapp":
        details = {"phone_number": body.get("display_phone_number"), "name": body.get("verified_name")}
        label = details["phone_number"] or "your number"
    elif channel.platform == "facebook":
        details = {"page_name": body.get("name"), "page_id": body.get("id")}
        label = details["page_name"] or "your Page"
    else:
        returned = str(body.get("user_id") or body.get("id") or "")
        details = {"username": body.get("username"), "account_id": returned}
        label = f"@{details['username']}" if details["username"] else "your account"
        if returned and returned != account_id:
            warnings.append("This token belongs to a different Instagram account than the ID you entered.")
    return ProbeResult(True, f"Connected to {label}.", details, warnings)
