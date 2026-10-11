"""One client for the Meta Graph API across WhatsApp, Messenger (Facebook) and Instagram.

Security and reliability rules
* The access token travels in the Authorization header, never in a URL, and is never logged.
* Meta's error *text* is discarded (it can quote the request). Only numeric codes are kept, and each
  code is mapped to a fixed, user-safe sentence by `describe()`.
* Calls that could duplicate a message (sends, replies) are retried ONLY when Meta says the request was
  not processed (rate limit / transient). Idempotent calls (delete, hide, reads) also retry on network
  errors and 5xx.
* Identifiers that come from webhook payloads are validated before they are placed in a URL path.
* Media is downloaded only from Meta's own https hosts, with a hard size limit, and each redirect
  target is re-checked.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import httpx

from ..channels.registry import Channel

TIMEOUT = httpx.Timeout(20.0, connect=6.0)
TRANSIENT_CODES = {4, 17, 32, 613}
_ID = re.compile(r"^[A-Za-z0-9_\-]{1,100}$")
MEDIA_HOST_SUFFIXES = (".fbcdn.net", ".fbsbx.com", ".cdninstagram.com", ".whatsapp.net", ".facebook.com",
                       ".instagram.com", ".fb.com")

_FRIENDLY = {
    190: "Meta rejected the access token (expired or revoked). Generate a new token and save it in the channel settings.",
    102: "Meta rejected the access token (expired or revoked). Generate a new token and save it in the channel settings.",
    10: "The token does not have the permission this action needs. Check the permissions in the setup guide.",
    200: "The token does not have the permission this action needs. Check the permissions in the setup guide.",
    299: "The token does not have the permission this action needs. Check the permissions in the setup guide.",
    4: "Meta is rate limiting this app. Wait a minute and try again.",
    17: "Meta is rate limiting this app. Wait a minute and try again.",
    32: "Meta is rate limiting this app. Wait a minute and try again.",
    613: "Meta is rate limiting this app. Wait a minute and try again.",
    10900: "A private reply was already sent for this comment (Meta allows one per comment).",
    2018278: "This conversation is outside Meta's 24-hour messaging window, so a reply cannot be sent.",
    131047: "More than 24 hours have passed since the customer's last message. WhatsApp now only allows approved templates.",
    131030: "That number is not on your WhatsApp test-recipient list. Add it in Meta's API setup or use a live number.",
    131026: "WhatsApp could not deliver the message (the person may not have WhatsApp or blocked the number).",
    551: "That person is not available to receive messages (they may have blocked the account).",
    100: "Meta could not process the request (an ID may be wrong, expired or already removed).",
    368: "Meta temporarily blocked this action for policy reasons. Try again later.",
}


class MetaError(Exception):
    def __init__(self, status: int, code: Optional[int] = None, subcode: Optional[int] = None,
                 transient: bool = False, network: bool = False):
        self.status, self.code, self.subcode, self.transient, self.network = status, code, subcode, transient, network
        label = "network error" if network else f"HTTP {status}" + (f", code {code}" if code else "")
        super().__init__(f"Meta API error ({label})")

    @property
    def is_auth(self) -> bool:
        return self.code in (190, 102) or self.status == 401

    @property
    def is_rate_limited(self) -> bool:
        return self.code in TRANSIENT_CODES or self.status == 429

    @property
    def is_gone(self) -> bool:
        """The object no longer exists on the platform (already deleted)."""
        return self.status == 404 or (self.code == 100 and self.subcode == 33)

    @property
    def retryable(self) -> bool:
        return self.network or self.transient or self.is_rate_limited or self.status >= 500

    def describe(self) -> str:
        if self.network:
            return "Could not reach Meta. Check the connection and try again."
        for key in (self.subcode, self.code):
            if key in _FRIENDLY:
                return _FRIENDLY[key]
        return f"Meta rejected the request (HTTP {self.status}" + (f", error {self.code}" if self.code else "") + ")."


def safe_id(value: Any) -> str:
    text = str(value or "")
    if not _ID.match(text):
        raise ValueError("Unsafe identifier")
    return text


def safe_media_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and bool(host) and any(host.endswith(s) or host == s[1:] for s in MEDIA_HOST_SUFFIXES)


class MetaClient:
    def __init__(self, channel: Channel, *, token: str, account_id: str, graph_version: str,
                 instagram_version: str, client: Optional[httpx.AsyncClient] = None, backoff: float = 0.5) -> None:
        self.channel, self.platform, self.kind = channel, channel.platform, channel.kind
        self.account_id = safe_id(account_id)
        version = instagram_version if channel.platform == "instagram" else graph_version
        self._base = f"{channel.graph_host}/{version}"
        self._headers = {"Authorization": f"Bearer {token}"}
        self._client, self._owns = client, client is None
        self._backoff = backoff
        self._own_username: Optional[str] = None

    async def aclose(self) -> None:
        if self._owns and self._client is not None:
            await self._client.aclose()

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False)
        return self._client

    # ---- transport ----------------------------------------------------------------------------
    async def _call(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None,
                    json: Optional[Dict[str, Any]] = None, idempotent: bool = False) -> Dict[str, Any]:
        url = f"{self._base}/{path.lstrip('/')}"
        attempts = 4
        for attempt in range(attempts):
            try:
                response = await self._http().request(method, url, params=params, json=json, headers=self._headers)
            except (httpx.TimeoutException, httpx.TransportError):
                error = MetaError(0, network=True)
                if not idempotent or attempt == attempts - 1:
                    raise error
            else:
                body: Dict[str, Any] = {}
                try:
                    parsed = response.json() if response.content else {}
                    body = parsed if isinstance(parsed, dict) else {}
                except ValueError:
                    body = {}
                failure = body.get("error") if isinstance(body.get("error"), dict) else None
                if response.status_code < 400 and failure is None:
                    return body
                failure = failure or {}
                error = MetaError(response.status_code, failure.get("code") if isinstance(failure.get("code"), int) else None,
                                  failure.get("error_subcode") if isinstance(failure.get("error_subcode"), int) else None,
                                  transient=failure.get("is_transient") is True)
                not_processed = error.transient or error.is_rate_limited
                may_retry = not_processed or (idempotent and error.retryable)
                if not may_retry or attempt == attempts - 1:
                    raise error
            await asyncio.sleep(self._backoff * (2 ** attempt))
        raise MetaError(0, network=True)  # unreachable

    # ---- messaging ----------------------------------------------------------------------------
    async def send_text(self, to: str, text: str, *, agent: bool = False) -> Optional[str]:
        to = safe_id(to)
        if self.platform == "whatsapp":
            body = await self._call("POST", f"{self.account_id}/messages", json={
                "messaging_product": "whatsapp", "recipient_type": "individual", "to": to, "type": "text",
                "text": {"body": text[:4096], "preview_url": False}})
            messages = body.get("messages") or []
            return messages[0].get("id") if messages and isinstance(messages[0], dict) else None
        payload: Dict[str, Any] = {"recipient": {"id": to}, "message": {"text": text[:1000 if self.platform == "instagram" else 2000]}}
        if self.platform == "facebook":
            payload["messaging_type"] = "MESSAGE_TAG" if agent else "RESPONSE"
            if agent:
                payload["tag"] = "HUMAN_AGENT"
        body = await self._call("POST", f"{self.account_id}/messages", json=payload)
        return body.get("message_id")

    async def send_image(self, to: str, url: str, caption: Optional[str] = None) -> Optional[str]:
        to = safe_id(to)
        if self.platform == "whatsapp":
            image: Dict[str, Any] = {"link": url}
            if caption:
                image["caption"] = caption[:1024]
            body = await self._call("POST", f"{self.account_id}/messages", json={
                "messaging_product": "whatsapp", "recipient_type": "individual", "to": to, "type": "image", "image": image})
            messages = body.get("messages") or []
            return messages[0].get("id") if messages and isinstance(messages[0], dict) else None
        attachment = {"type": "image", "payload": {"url": url}}
        payload: Dict[str, Any] = {"recipient": {"id": to}, "message": {"attachment": attachment}}
        if self.platform == "facebook":
            payload["messaging_type"] = "RESPONSE"
        body = await self._call("POST", f"{self.account_id}/messages", json=payload)
        return body.get("message_id")

    # ---- comments -----------------------------------------------------------------------------
    async def private_reply(self, comment_id: str, text: str) -> Optional[str]:
        body = await self._call("POST", f"{self.account_id}/messages", json={
            "recipient": {"comment_id": safe_id(comment_id)},
            "message": {"text": text[:1000 if self.platform == "instagram" else 2000]}})
        return body.get("message_id")

    async def public_reply(self, comment_id: str, text: str) -> Optional[str]:
        cid = safe_id(comment_id)
        if self.platform == "instagram":
            body = await self._call("POST", f"{cid}/replies", params={"message": text[:1000]})
        else:
            body = await self._call("POST", f"{cid}/comments", json={"message": text[:7900]})
        return body.get("id")

    async def hide_comment(self, comment_id: str, hide: bool = True) -> None:
        cid = safe_id(comment_id)
        if self.platform == "instagram":
            await self._call("POST", cid, params={"hide": "true" if hide else "false"}, idempotent=True)
        else:
            await self._call("POST", cid, json={"is_hidden": hide}, idempotent=True)

    async def comment_author(self, comment_id: str) -> Dict[str, Optional[str]]:
        cid = safe_id(comment_id)
        fields = "from,username" if self.platform == "instagram" else "from"
        body = await self._call("GET", cid, params={"fields": fields}, idempotent=True)
        author = body.get("from") if isinstance(body.get("from"), dict) else {}
        username = body.get("username") or author.get("username")
        return {"id": str(author["id"]) if author.get("id") else None,
                "username": str(username).lstrip("@").casefold() if username else None}

    async def _own_name(self) -> Optional[str]:
        if self.platform != "instagram":
            return None
        if self._own_username is None:
            try:
                body = await self._call("GET", "me", params={"fields": "username"}, idempotent=True)
                self._own_username = str(body.get("username") or "").lstrip("@").casefold()
            except MetaError:
                self._own_username = ""
        return self._own_username or None

    async def _own_replies(self, comment_id: str) -> List[str]:
        """IDs of replies that this account itself posted under a comment."""
        edge = "replies" if self.platform == "instagram" else "comments"
        fields = "id,username,from" if self.platform == "instagram" else "id,from"
        own_name = await self._own_name()
        found: List[str] = []
        after: Optional[str] = None
        for _ in range(10):                                    # at most 10 pages
            params: Dict[str, Any] = {"fields": fields, "limit": 50}
            if after:
                params["after"] = after
            body = await self._call("GET", f"{comment_id}/{edge}", params=params, idempotent=True)
            for reply in body.get("data") or []:
                author = reply.get("from") if isinstance(reply.get("from"), dict) else {}
                username = str(reply.get("username") or author.get("username") or "").lstrip("@").casefold()
                if str(author.get("id", "")) == self.account_id or (own_name and username == own_name):
                    found.append(str(reply["id"]))
            paging = body.get("paging") or {}
            after = (paging.get("cursors") or {}).get("after")
            if not after or not paging.get("next"):
                break
        return found

    async def delete_comment(self, comment_id: str, *, delete_own_replies: bool = True) -> None:
        """Delete on the platform. Raises MetaError (check `.is_gone`) when Meta refuses."""
        cid = safe_id(comment_id)
        if delete_own_replies:
            try:
                for reply_id in await self._own_replies(cid):
                    try:
                        await self._call("DELETE", safe_id(reply_id), idempotent=True)
                    except MetaError:
                        pass                                    # a stuck reply must not block the main delete
            except MetaError as exc:
                if exc.is_gone:
                    return
        body = await self._call("DELETE", cid, idempotent=True)
        if body.get("success") is False:
            raise MetaError(200, 0)

    # ---- profile, media, subscriptions --------------------------------------------------------
    async def profile(self, user_id: str) -> Dict[str, Optional[str]]:
        uid = safe_id(user_id)
        if self.platform == "whatsapp":
            return {}
        fields = "name,username,profile_pic" if self.platform == "instagram" else "first_name,last_name,name,profile_pic"
        try:
            body = await self._call("GET", uid, params={"fields": fields}, idempotent=True)
        except MetaError:
            return {}
        name = (body.get("name") or " ".join(p for p in (body.get("first_name"), body.get("last_name")) if p)).strip()
        return {"name": name or None, "username": (body.get("username") or "").lstrip("@") or None,
                "profile_pic_url": body.get("profile_pic")}

    async def whatsapp_media(self, media_id: str) -> Dict[str, Any]:
        return await self._call("GET", safe_id(media_id), idempotent=True)

    async def download(self, url: str, max_bytes: int) -> Tuple[bytes, str]:
        """Fetch media from a Meta host. Raises ValueError for unsafe URLs / oversize files, MetaError on HTTP errors."""
        for _ in range(4):
            if not safe_media_url(url):
                raise ValueError("Media URL is not an allowed Meta host")
            try:
                async with self._http().stream("GET", url, headers=self._headers) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        url = response.headers.get("location", "")
                        continue
                    if response.status_code >= 400:
                        raise MetaError(response.status_code)
                    declared = response.headers.get("content-length", "")
                    if declared.isdigit() and int(declared) > max_bytes:
                        raise ValueError("Media file is too large")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > max_bytes:
                            raise ValueError("Media file is too large")
                    return bytes(data), response.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
            except (httpx.TimeoutException, httpx.TransportError):
                raise MetaError(0, network=True)
        raise ValueError("Too many redirects")

    async def subscribe_app(self, fields: Optional[List[str]] = None, *, business_account_id: Optional[str] = None) -> bool:
        """Subscribe the app to this account's webhook fields (replaces the manual subscribed_apps curl)."""
        if self.platform == "whatsapp":
            if not business_account_id:
                raise ValueError("The WhatsApp Business account ID is required to subscribe.")
            body = await self._call("POST", f"{safe_id(business_account_id)}/subscribed_apps", idempotent=True)
        elif self.platform == "instagram":
            body = await self._call("POST", "me/subscribed_apps",
                                    params={"subscribed_fields": ",".join(fields or self.channel.webhook_fields)}, idempotent=True)
        else:
            body = await self._call("POST", f"{self.account_id}/subscribed_apps",
                                    params={"subscribed_fields": ",".join(fields or self.channel.webhook_fields)}, idempotent=True)
        return body.get("success") is True
