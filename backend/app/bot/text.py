"""Pure text helpers shared by the bot engine (no I/O, no business-specific wording).

Ported from the original single-business bot's dialog/graph helpers: emoji and markdown stripping,
human-handoff detection, private-reply detection, comment classification parsing.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

COMMENT_TONES = {"friendly", "neutral", "confused", "frustrated", "angry", "sarcastic", "off_topic", "spam", "abusive"}

_EMOJI = re.compile(
    "["
    "\U00010000-\U0010ffff"  # pictographs, emoticons, flags
    "☀-⛿✀-➿⌀-⏿"
    "⭐⭕⌚⌛⌨⏏▪▫◾◽◻◼"
    "‍️"
    "]+", flags=re.UNICODE)


def strip_emojis(text: str) -> str:
    """Remove emojis and normalise typographic punctuation so plain-text channels render cleanly."""
    if not text:
        return ""
    cleaned = _EMOJI.sub("", text)
    cleaned = re.sub(r"[   -​　]", " ", cleaned)
    for dash in ("‐", "‑", "‒", "–", "—", "―", "−"):
        cleaned = cleaned.replace(dash, "-")
    cleaned = cleaned.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n +", "\n", cleaned)
    return cleaned.strip()


def plain_text(text: str) -> str:
    """Remove markdown headings, bold/italic markers and backticks."""
    if not text:
        return ""
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"\*\*(.*?)\*\*|__(.*?)__", lambda m: m.group(1) or m.group(2), text)
    text = re.sub(r"(?m)^\s*[-*]\s+", "- ", text)                 # normalise bullets BEFORE stray * markers are removed
    text = re.sub(r"(?<!\*)\*(?!\*)|(?<!_)_(?!_)", "", text)
    return text.replace("`", "").strip()


def tidy_reply(text: str, limit: int = 1900, *, allow_emoji: bool = False) -> str:
    text = plain_text(text)
    if not allow_emoji:
        text = strip_emojis(text)
    return text[:limit].strip()


_HANDOFF_PHRASES = {
    "/human", "human", "talk to a human", "talk to human", "speak to a human", "speak to human",
    "talk to an agent", "talk to agent", "speak to an agent", "speak to agent", "human agent", "real person",
    "talk to a person", "speak to a person", "talk to representative", "speak to representative", "live agent",
    "human representative", "agent please", "human please", "talk to someone", "speak with someone",
    "can i talk to a human", "i want to talk to a human", "transfer to human", "connect to human",
    "need a human", "need human", "operator", "customer service agent",
}
_HANDOFF_PATTERNS = [re.compile(p) for p in (
    r"\b(talk|speak|chat|connect)\s+(to|with)\s+(a\s+|an\s+)?(human|agent|person|representative|specialist|team|member|someone)\b",
    r"\b(want|need|like)\s+(to\s+)?(talk|speak|chat)\s+(to|with)\s+(a\s+|an\s+)?(human|agent|person|representative|specialist|team|someone)\b",
    r"\b(connect|transfer|hand\s*over)\s+(me\s+)?to\s+(a\s+|an\s+)?(human|agent|person|representative|specialist)\b",
    r"\b(is\s+there\s+a|can\s+i\s+get\s+a)\s+(human|agent|person|real\s+person)\b",
    r"\b(human|live)\s+(agent|support|help|assistance)\b",
    r"\b(need|want)\s+(a\s+|an\s+)?(human|real\s+person|live\s+agent)\b",
)]


def is_human_handoff_request(text: str) -> bool:
    if not text:
        return False
    norm = text.strip().lower()
    clean = " ".join(re.sub(r"[^a-z0-9/\s]", " ", norm).split())
    return clean in _HANDOFF_PHRASES or any(p.search(norm) for p in _HANDOFF_PATTERNS)


_GREETING = re.compile(r"(hi|hello|hey|hiya|good morning|good afternoon|good evening|salam|assalam o alaikum)( there)?")


def small_talk_reply(text: str) -> Optional[str]:
    """Cheap canned answer for plain greetings, so they never cost an LLM call."""
    normalized = " ".join(re.sub(r"[^a-z\s]", " ", (text or "").lower()).split())
    if _GREETING.fullmatch(normalized):
        return "Hello! How can I help you today?"
    if re.search(r"\bhow (are|r) (you|u)\b", normalized) and len(normalized.split()) <= 5:
        return "I'm doing well, thank you! How can I help you today?"
    return None


_PAYMENT = re.compile(r"\bhow\s+(?:can|do|could|should)\s+i\s+pay\b|\bwhere\s+can\s+i\s+pay\b|\b(?:payment|pay)\s+(?:link|details|information)\b", re.I)
_CASE = re.compile(r"\b(my|our)\s+(order|account|payment|subscription|invoice|refund|transaction|case|ticket)\b"
                   r"|\b(order|case|ticket|refund)\s*(number|#|id)\s*[:#-]?\s*[a-z0-9-]{4,}\b", re.I)
_EXPOSED = re.compile(r"\b(password|passcode|ssn|credit card|debit card|card number|cvv|bank account)\b"
                      r"|\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|\b(?:\+?\d[\d ().-]{7,}\d)\b|\bmy address is\b", re.I)


def requires_private_reply(text: str) -> bool:
    """Public comments must not carry order details or personal data; answer those privately."""
    return bool(_PAYMENT.search(text or "") or _CASE.search(text or "") or _EXPOSED.search(text or ""))


_URL = re.compile(r"https?://|www\.", re.I)
_SPAM_WORDS = re.compile(r"\b(free money|click here|earn \$?\d+|crypto giveaway|dm me for|whatsapp me|"
                         r"investment opportunity|guaranteed profit|follow for follow|promo code|buy followers)\b", re.I)


def looks_like_spam(text: str) -> bool:
    text = text or ""
    return len(_URL.findall(text)) >= 2 or bool(_SPAM_WORDS.search(text))


def heuristic_classification(text: str) -> Dict[str, Any]:
    """Used when no LLM is available: conservative, rule-based."""
    return {"classification": "spam" if looks_like_spam(text) else "neutral",
            "private": requires_private_reply(text), "severe": False, "topic": "unclassified"}


def _truthy(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() in {"true", "yes"})


def parse_classification(raw: str, text: str) -> Dict[str, Any]:
    """Parse the model's JSON verdict, falling back to the heuristics on anything unexpected."""
    try:
        result = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
        tone = str(result.get("tone", "neutral")).strip().lower()
        return {"classification": tone if tone in COMMENT_TONES else "neutral",
                "private": _truthy(result.get("private")) or requires_private_reply(text),
                "severe": _truthy(result.get("severe")),
                "topic": str(result.get("topic", "general"))[:80]}
    except (ValueError, AttributeError, TypeError):
        return heuristic_classification(text)


def postback_to_text(postback: Any) -> str:
    """Messenger/Instagram deliver a menu or ice-breaker tap as a postback; treat it as typed text."""
    if not isinstance(postback, dict):
        return ""
    title = str(postback.get("title") or "").strip()
    payload = str(postback.get("payload") or "").strip()
    return title or payload
