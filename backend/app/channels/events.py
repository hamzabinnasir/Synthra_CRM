"""Split a Meta webhook payload into individual, idempotent events for one channel.

Every event gets a stable `key`. Meta redelivers webhooks, so the queue's unique
(tenant, channel, key) constraint turns a duplicate delivery into a no-op.

Events that do not belong to the configured account (another phone number, another Page) or that the
bot does not act on (delivery and read receipts) are counted as skipped, not queued.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List

from .registry import Channel

MAX_EVENTS_PER_REQUEST = 200


@dataclass(frozen=True)
class Event:
    key: str
    kind: str            # message | status | postback | unsend | comment | comment_removed
    data: Dict[str, Any]


@dataclass
class Extracted:
    events: List[Event] = field(default_factory=list)
    skipped: int = 0


def _digest(*parts: Any) -> str:
    blob = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:40]


def _dicts(value: Any) -> List[Dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def extract(channel: Channel, account_id: str, payload: Any) -> Extracted:
    out = Extracted()
    if not isinstance(payload, dict) or payload.get("object") != channel.meta_object:
        out.skipped += 1
        return out
    for entry in _dicts(payload.get("entry")):
        if channel.platform == "whatsapp":
            _whatsapp(entry, account_id, out)
        elif channel.kind == "inbox":
            _messaging(entry, str(account_id), out, channel.platform)
        else:
            _comments(channel, entry, str(account_id), out)
        if len(out.events) >= MAX_EVENTS_PER_REQUEST:
            out.events = out.events[:MAX_EVENTS_PER_REQUEST]
            break
    return out


def _whatsapp(entry: Dict[str, Any], phone_number_id: str, out: Extracted) -> None:
    for change in _dicts(entry.get("changes")):
        value = change.get("value") if isinstance(change.get("value"), dict) else {}
        if change.get("field") != "messages":
            out.skipped += 1
            continue
        if str((value.get("metadata") or {}).get("phone_number_id", "")) != phone_number_id:
            out.skipped += 1          # a different number on the same Meta app
            continue
        contacts = _dicts(value.get("contacts"))
        for msg in _dicts(value.get("messages")):
            if not msg.get("id"):
                out.skipped += 1
                continue
            out.events.append(Event(f"wamid:{msg['id']}", "message", {"message": msg, "contacts": contacts}))
        for status in _dicts(value.get("statuses")):
            if not status.get("id"):
                out.skipped += 1
                continue
            out.events.append(Event(f"status:{status['id']}:{status.get('status', '')}", "status",
                                    {"status": status}))


def _messaging(entry: Dict[str, Any], account_id: str, out: Extracted, platform: str) -> None:
    if str(entry.get("id", "")) != account_id:
        out.skipped += 1
        return
    for item in _dicts(entry.get("messaging")):
        message = item.get("message") if isinstance(item.get("message"), dict) else None
        postback = item.get("postback") if isinstance(item.get("postback"), dict) else None
        if message and message.get("mid"):
            kind = "unsend" if message.get("is_deleted") else "message"
            out.events.append(Event(f"mid:{message['mid']}" + (":del" if kind == "unsend" else ""), kind, {"messaging": item}))
        elif postback:
            out.events.append(Event(f"pb:{_digest(item.get('sender'), item.get('timestamp'), postback)}",
                                    "postback", {"messaging": item}))
        else:
            out.skipped += 1          # delivery / read receipts, reactions and similar noise


def _comments(channel: Channel, entry: Dict[str, Any], account_id: str, out: Extracted) -> None:
    if str(entry.get("id", "")) != account_id:
        out.skipped += 1
        return
    for change in _dicts(entry.get("changes")):
        value = change.get("value") if isinstance(change.get("value"), dict) else {}
        field_name = change.get("field")
        if channel.platform == "facebook":
            if field_name != "feed" or value.get("item") != "comment" or not value.get("comment_id"):
                out.skipped += 1
                continue
            verb = str(value.get("verb", "add"))
            kind = "comment_removed" if verb == "remove" else "comment"
            out.events.append(Event(f"comment:{value['comment_id']}:{verb}", kind, {"change": change}))
        else:
            if field_name not in ("comments", "live_comments") or not value.get("id"):
                out.skipped += 1
                continue
            out.events.append(Event(f"comment:{value['id']}", "comment", {"change": change}))
