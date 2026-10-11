"""Turn raw Meta webhook payloads (already split into events by channels/events.py) into neutral objects."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .text import postback_to_text


@dataclass(frozen=True)
class MediaRef:
    type: str                      # image | video | audio | document
    media_id: Optional[str] = None  # WhatsApp: resolved through the Graph API
    url: Optional[str] = None       # Messenger / Instagram: direct (temporary) URL
    mime: Optional[str] = None
    filename: Optional[str] = None


@dataclass(frozen=True)
class Inbound:
    platform: str
    external_id: str               # the customer's id on that platform
    text: str
    mid: Optional[str]
    created_at: datetime
    name: Optional[str] = None
    username: Optional[str] = None
    media: Optional[MediaRef] = None
    is_echo: bool = False          # sent BY the business account (a person using the native inbox, or us)


@dataclass(frozen=True)
class ParsedComment:
    platform: str
    comment_id: str
    post_id: Optional[str]
    parent_id: Optional[str]
    from_id: Optional[str]
    from_name: Optional[str]
    from_username: Optional[str]
    message: str
    created_at: datetime


def to_datetime(value: Any) -> datetime:
    try:
        seconds = float(value)
        if seconds > 100_000_000_000:
            seconds /= 1000
        return datetime.fromtimestamp(seconds, timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return datetime.now(timezone.utc)


def parse_whatsapp(data: Dict[str, Any]) -> Optional[Inbound]:
    msg = data.get("message") if isinstance(data.get("message"), dict) else {}
    phone = str(msg.get("from") or "")
    if not re.fullmatch(r"\d{5,20}", phone) or not msg.get("id"):
        return None
    kind = msg.get("type")
    text, media = "", None

    def body(key: str) -> Dict[str, Any]:
        part = msg.get(key)
        return part if isinstance(part, dict) else {}

    if kind == "text":
        text = str(body("text").get("body") or "").strip()
    elif kind == "image":
        part = body("image")
        media, text = MediaRef("image", media_id=part.get("id"), mime=part.get("mime_type")), (part.get("caption") or "").strip() or "[Image]"
    elif kind == "video":
        part = body("video")
        media, text = MediaRef("video", media_id=part.get("id"), mime=part.get("mime_type")), (part.get("caption") or "").strip() or "[Video]"
    elif kind in ("audio", "voice"):
        part = body("audio") or body("voice")
        media, text = MediaRef("audio", media_id=part.get("id"), mime=part.get("mime_type")), "[Audio]"
    elif kind == "document":
        part = body("document")
        media = MediaRef("document", media_id=part.get("id"), mime=part.get("mime_type"), filename=part.get("filename"))
        text = (part.get("caption") or "").strip() or part.get("filename") or "[Document]"
    elif kind == "sticker":
        part = body("sticker")
        media, text = MediaRef("image", media_id=part.get("id"), mime=part.get("mime_type")), "[Sticker]"
    elif kind == "interactive":
        part = body("interactive")
        reply = part.get("button_reply") or part.get("list_reply") or {}
        text = str(reply.get("title") or "").strip()
    elif kind == "button":
        text = str(body("button").get("text") or "").strip()
    elif kind == "location":
        text = "[Location]"
    else:
        return None
    if not text and media is None:
        return None
    name = None
    for contact in data.get("contacts") or []:
        if isinstance(contact, dict) and str(contact.get("wa_id")) == phone:
            name = (contact.get("profile") or {}).get("name")
    return Inbound("whatsapp", phone, text, f"{msg['id']}", to_datetime(msg.get("timestamp")), name=name,
                   username=phone, media=media)


_MEDIA_TYPES = {"image": "image", "video": "video", "audio": "audio", "file": "document"}


def parse_messaging(platform: str, account_id: str, data: Dict[str, Any]) -> Optional[Inbound]:
    item = data.get("messaging") if isinstance(data.get("messaging"), dict) else {}
    sender = str((item.get("sender") or {}).get("id") or "")
    recipient = str((item.get("recipient") or {}).get("id") or "")
    message = item.get("message") if isinstance(item.get("message"), dict) else None
    postback = item.get("postback") if isinstance(item.get("postback"), dict) else None
    if not sender or not (message or postback):
        return None
    echo = bool(message and message.get("is_echo")) or (platform == "instagram" and sender == str(account_id))
    external = recipient if echo else sender
    if not external:
        return None
    text, media, mid = "", None, None
    if message:
        mid = message.get("mid")
        text = str(message.get("text") or "").strip()
        attachments = [a for a in (message.get("attachments") or []) if isinstance(a, dict)]
        if attachments:
            first = attachments[0]
            payload = first.get("payload") if isinstance(first.get("payload"), dict) else {}
            kind = _MEDIA_TYPES.get(str(first.get("type")))
            if kind and payload.get("url"):
                media = MediaRef(kind, url=payload["url"], filename=payload.get("title") if kind == "document" else None)
                text = text or media.filename or f"[{kind.capitalize()}]"
            elif not text:
                text = f"[{str(first.get('type') or 'Attachment').capitalize()}]"
    else:
        text = postback_to_text(postback)
        mid = postback.get("mid") if postback else None
    if not text and media is None:
        return None
    sender_info = item.get("sender") if isinstance(item.get("sender"), dict) else {}
    return Inbound(platform, external, text, mid, to_datetime(item.get("timestamp")),
                   name=None if echo else sender_info.get("name"), username=None if echo else sender_info.get("username"),
                   media=media, is_echo=echo)


def parse_comment(platform: str, data: Dict[str, Any]) -> Optional[ParsedComment]:
    change = data.get("change") if isinstance(data.get("change"), dict) else {}
    value = change.get("value") if isinstance(change.get("value"), dict) else {}
    if platform == "facebook":
        if value.get("item") != "comment" or str(value.get("verb", "add")) != "add" or not value.get("comment_id"):
            return None
        author = value.get("from") if isinstance(value.get("from"), dict) else {}
        return ParsedComment("facebook", str(value["comment_id"]), _s(value.get("post_id")), _s(value.get("parent_id")),
                             _s(author.get("id")), _s(author.get("name")), None, str(value.get("message") or ""),
                             to_datetime(value.get("created_time")))
    if not value.get("id"):
        return None
    author = value.get("from") if isinstance(value.get("from"), dict) else {}
    media = value.get("media") if isinstance(value.get("media"), dict) else {}
    username = _s(author.get("username"))
    return ParsedComment("instagram", str(value["id"]), _s(media.get("id")), _s(value.get("parent_id")),
                         _s(author.get("id")), username, username, str(value.get("text") or ""), datetime.now(timezone.utc))


def comment_id_from_removal(platform: str, data: Dict[str, Any]) -> Optional[str]:
    change = data.get("change") if isinstance(data.get("change"), dict) else {}
    value = change.get("value") if isinstance(change.get("value"), dict) else {}
    found = value.get("comment_id") or value.get("id")
    return str(found) if found else None


def unsent_mid(data: Dict[str, Any]) -> Optional[str]:
    item = data.get("messaging") if isinstance(data.get("messaging"), dict) else {}
    message = item.get("message") if isinstance(item.get("message"), dict) else {}
    return str(message["mid"]) if message.get("mid") else None


def _s(value: Any) -> Optional[str]:
    return str(value) if value not in (None, "") else None
