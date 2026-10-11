"""The five supported channels and the settings each one needs.

This is the single source of truth: the API validates against it and the dashboard renders its
settings pages from it, so adding a field here adds it everywhere.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

GUIDE_BASE = "/docs/channels"

LLM_PROVIDERS: Tuple[str, ...] = ("anthropic", "openai", "gemini", "groq")
TONES: Tuple[str, ...] = ("friendly", "professional", "concise", "playful", "formal")
MODERATION_ACTIONS: Tuple[str, ...] = ("none", "hide", "delete")


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    help: str
    secret: bool = False
    required: bool = False
    placeholder: str = ""
    pattern: Optional[str] = None   # full-match regex for the value
    max_length: int = 1024

    def public(self) -> Dict[str, object]:
        return {"key": self.key, "label": self.label, "help": self.help, "secret": self.secret,
                "required": self.required, "placeholder": self.placeholder}


_TOKEN = r"[A-Za-z0-9._\-|~+/=]{16,1024}"      # access tokens: no whitespace, no quotes
_ID = r"\d{5,30}"
_APP_SECRET = r"[A-Za-z0-9]{16,128}"


@dataclass(frozen=True)
class Channel:
    id: str
    title: str
    platform: str                 # whatsapp | facebook | instagram
    kind: str                     # inbox | comments
    meta_object: str              # value of "object" in the webhook payload
    webhook_fields: Tuple[str, ...]
    graph_host: str
    account_field: str            # setting that holds the account / page / phone-number id
    fields: Tuple[Field, ...]
    summary: str

    @property
    def guide(self) -> str:
        return f"{GUIDE_BASE}/{self.id.replace('_', '-')}.md"

    def field(self, key: str) -> Optional[Field]:
        return next((f for f in self.fields if f.key == key), None)

    @property
    def secret_keys(self) -> Tuple[str, ...]:
        return tuple(f.key for f in self.fields if f.secret)

    def public(self) -> Dict[str, object]:
        return {"id": self.id, "title": self.title, "platform": self.platform, "kind": self.kind,
                "summary": self.summary, "webhook_fields": list(self.webhook_fields), "guide": self.guide,
                "fields": [f.public() for f in self.fields]}


def _common(app_secret_help: str) -> Tuple[Field, ...]:
    return (
        Field("app_secret", "App secret", app_secret_help, secret=True, required=True,
              placeholder="32-character secret from your Meta app", pattern=_APP_SECRET, max_length=128),
        Field("llm_api_key", "LLM API key",
              "The key for the AI provider chosen below. Leave empty if the bot is switched off.",
              secret=True, placeholder="sk-...", pattern=r"\S{8,512}", max_length=512),
    )


_META_APP_SECRET = "Meta for Developers > your app > App settings > Basic > App secret. Used to verify that webhooks really come from Meta."
_IG_APP_SECRET = ("Meta for Developers > your app > Instagram > API setup with Instagram login > Instagram app secret. "
                  "This is different from the Facebook app secret.")

CHANNELS: Dict[str, Channel] = {c.id: c for c in (
    Channel(
        id="whatsapp", title="WhatsApp inbox", platform="whatsapp", kind="inbox",
        meta_object="whatsapp_business_account", webhook_fields=("messages",),
        graph_host="https://graph.facebook.com", account_field="phone_number_id",
        summary="Answer WhatsApp Business messages with your bot and hand over to a person when needed.",
        fields=(
            Field("phone_number_id", "Phone number ID",
                  "WhatsApp > API setup > the numeric ID under the phone number (not the phone number itself).",
                  required=True, placeholder="123456789012345", pattern=_ID, max_length=30),
            Field("business_account_id", "WhatsApp Business account ID",
                  "Optional. Needed only to list your message templates.", placeholder="123456789012345",
                  pattern=_ID, max_length=30),
            Field("access_token", "Access token",
                  "Use a permanent token from a System User (Business settings > System users). "
                  "Temporary tokens expire after 24 hours.", secret=True, required=True,
                  placeholder="EAAG...", pattern=_TOKEN),
            *_common(_META_APP_SECRET))),
    Channel(
        id="facebook_inbox", title="Facebook inbox", platform="facebook", kind="inbox",
        meta_object="page", webhook_fields=("messages", "messaging_postbacks"),
        graph_host="https://graph.facebook.com", account_field="page_id",
        summary="Reply to Messenger conversations on your Facebook Page.",
        fields=(
            Field("page_id", "Page ID", "Your Facebook Page > About > Page transparency > Page ID.",
                  required=True, placeholder="123456789012345", pattern=_ID, max_length=30),
            Field("page_access_token", "Page access token",
                  "A long-lived Page token with pages_messaging and pages_manage_metadata.",
                  secret=True, required=True, placeholder="EAAG...", pattern=_TOKEN),
            *_common(_META_APP_SECRET))),
    Channel(
        id="facebook_comments", title="Facebook comments", platform="facebook", kind="comments",
        meta_object="page", webhook_fields=("feed",),
        graph_host="https://graph.facebook.com", account_field="page_id",
        summary="Moderate and answer comments on your Page's posts, publicly or by private reply.",
        fields=(
            Field("page_id", "Page ID", "Your Facebook Page > About > Page transparency > Page ID.",
                  required=True, placeholder="123456789012345", pattern=_ID, max_length=30),
            Field("page_access_token", "Page access token",
                  "A long-lived Page token with pages_read_engagement, pages_manage_engagement and "
                  "pages_manage_posts.", secret=True, required=True, placeholder="EAAG...", pattern=_TOKEN),
            *_common(_META_APP_SECRET))),
    Channel(
        id="instagram_inbox", title="Instagram inbox", platform="instagram", kind="inbox",
        meta_object="instagram", webhook_fields=("messages",),
        graph_host="https://graph.instagram.com", account_field="instagram_account_id",
        summary="Reply to Instagram direct messages sent to your professional account.",
        fields=(
            Field("instagram_account_id", "Instagram account ID",
                  "Instagram > API setup with Instagram login > the numeric account ID.",
                  required=True, placeholder="17841400000000000", pattern=_ID, max_length=30),
            Field("access_token", "Instagram access token",
                  "An Instagram User token with instagram_business_basic and "
                  "instagram_business_manage_messages.", secret=True, required=True,
                  placeholder="IGAA...", pattern=_TOKEN),
            *_common(_IG_APP_SECRET))),
    Channel(
        id="instagram_comments", title="Instagram comments", platform="instagram", kind="comments",
        meta_object="instagram", webhook_fields=("comments",),
        graph_host="https://graph.instagram.com", account_field="instagram_account_id",
        summary="Moderate and answer comments on your Instagram posts and reels.",
        fields=(
            Field("instagram_account_id", "Instagram account ID",
                  "Instagram > API setup with Instagram login > the numeric account ID.",
                  required=True, placeholder="17841400000000000", pattern=_ID, max_length=30),
            Field("access_token", "Instagram access token",
                  "An Instagram User token with instagram_business_basic and "
                  "instagram_business_manage_comments.", secret=True, required=True,
                  placeholder="IGAA...", pattern=_TOKEN),
            *_common(_IG_APP_SECRET))),
)}

CHANNEL_IDS: Tuple[str, ...] = tuple(CHANNELS)

# Non-secret bot behaviour, the same on every channel.
BEHAVIOUR_FIELDS = ("llm_provider", "llm_model", "system_prompt", "tone", "spam_action", "abuse_action",
                    "business_phone", "pause_on_human", "handoff_message", "fallback_message")
CANNED_MAX = 500
SYSTEM_PROMPT_MAX = 8000
_PHONE = re.compile(r"^\+?[0-9 ()\-]{6,24}$")


class ValidationFailed(ValueError):
    """A user-correctable problem. `errors` maps field name to a message that is safe to show."""

    def __init__(self, errors: Dict[str, str]):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def get_channel(channel_id: str) -> Optional[Channel]:
    return CHANNELS.get(channel_id)


def validate_values(channel: Channel, values: Dict[str, object]) -> Dict[str, str]:
    """Validate submitted credential / id fields. Returns the cleaned non-empty values."""
    errors: Dict[str, str] = {}
    clean: Dict[str, str] = {}
    for key, raw in values.items():
        spec = channel.field(key)
        if spec is None:
            errors[key] = "Unknown setting for this channel."
            continue
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue                                  # empty means "leave as is"
        if not isinstance(raw, str):
            errors[key] = "Must be text."
            continue
        value = raw.strip()
        if len(value) > spec.max_length:
            errors[key] = f"Too long (maximum {spec.max_length} characters)."
        elif spec.pattern and not re.fullmatch(spec.pattern, value):
            errors[key] = "That does not look right. Check it for spaces or missing characters."
        else:
            clean[key] = value
    if errors:
        raise ValidationFailed(errors)
    return clean


def validate_behaviour(raw: Dict[str, object]) -> Dict[str, str]:
    errors: Dict[str, str] = {}
    clean: Dict[str, str] = {}
    for key, value in raw.items():
        if key not in BEHAVIOUR_FIELDS:
            errors[key] = "Unknown setting."
            continue
        if value is None:
            continue
        if not isinstance(value, str):
            errors[key] = "Must be text."
            continue
        value = value.strip()
        if key == "llm_provider" and value not in LLM_PROVIDERS:
            errors[key] = f"Choose one of: {', '.join(LLM_PROVIDERS)}."
        elif key == "tone" and value not in TONES:
            errors[key] = f"Choose one of: {', '.join(TONES)}."
        elif key in ("spam_action", "abuse_action") and value not in MODERATION_ACTIONS:
            errors[key] = f"Choose one of: {', '.join(MODERATION_ACTIONS)}."
        elif key == "system_prompt" and len(value) > SYSTEM_PROMPT_MAX:
            errors[key] = f"Too long (maximum {SYSTEM_PROMPT_MAX} characters)."
        elif key == "llm_model" and not re.fullmatch(r"[A-Za-z0-9._:\-/]{1,100}", value or "x"):
            errors[key] = "Model names use letters, digits and . _ : - / only."
        elif key == "pause_on_human" and value not in ("true", "false"):
            errors[key] = "Choose true or false."
        elif key in ("handoff_message", "fallback_message") and len(value) > CANNED_MAX:
            errors[key] = f"Too long (maximum {CANNED_MAX} characters)."
        elif key == "business_phone" and value and not _PHONE.match(value):
            errors[key] = "Enter a phone number such as +92 300 1234567."
        else:
            clean[key] = value
    if errors:
        raise ValidationFailed(errors)
    return clean
