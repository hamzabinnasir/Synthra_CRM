"""Provider-neutral LLM client: Anthropic, OpenAI, Gemini and Groq over plain HTTPS.

Every workspace brings its own key. The key is sent in a header (never in the URL), is never logged and
never appears in an exception: provider error text is NOT passed on, because it can quote the request.
Callers get an LLMError whose message is one of a few fixed, user-safe sentences.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import httpx

DEFAULT_MODELS: Dict[str, str] = {
    "anthropic": "claude-haiku-5-5",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.0-flash",
    "groq": "llama-3.3-70b-versatile",
}
MAX_OUTPUT_TOKENS = 600


class LLMError(Exception):
    """`retryable` tells the caller whether trying again can help; `reason` is a short stable code."""

    def __init__(self, message: str, *, reason: str, retryable: bool = False):
        super().__init__(message)
        self.message, self.reason, self.retryable = message, reason, retryable


class LLMConfigError(LLMError):
    """No key / unknown provider: only the workspace owner can fix this."""

    def __init__(self, message: str, reason: str = "not_configured"):
        super().__init__(message, reason=reason, retryable=False)


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    api_key: str
    model: str = ""

    @property
    def resolved_model(self) -> str:
        return self.model or DEFAULT_MODELS.get(self.provider, "")


def _request(cfg: LLMConfig, system: str, prompt: str) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    model = cfg.resolved_model
    if cfg.provider == "anthropic":
        return ("https://api.anthropic.com/v1/messages",
                {"x-api-key": cfg.api_key, "anthropic-version": "2023-06-01"},
                {"model": model, "max_tokens": MAX_OUTPUT_TOKENS, "temperature": 0.2, "system": system,
                 "messages": [{"role": "user", "content": prompt}]})
    if cfg.provider in ("openai", "groq"):
        url = ("https://api.openai.com/v1/chat/completions" if cfg.provider == "openai"
               else "https://api.groq.com/openai/v1/chat/completions")
        return (url, {"Authorization": f"Bearer {cfg.api_key}"},
                {"model": model, "temperature": 0.2, "max_tokens": MAX_OUTPUT_TOKENS,
                 "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]})
    if cfg.provider == "gemini":
        return (f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                {"x-goog-api-key": cfg.api_key},
                {"system_instruction": {"parts": [{"text": system}]},
                 "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                 "generationConfig": {"temperature": 0.2, "maxOutputTokens": MAX_OUTPUT_TOKENS}})
    raise LLMConfigError("Choose a supported LLM provider in the channel settings.", "unknown_provider")


def _extract(provider: str, body: Dict[str, Any]) -> str:
    try:
        if provider == "anthropic":
            return "".join(b.get("text", "") for b in body["content"] if b.get("type") == "text").strip()
        if provider in ("openai", "groq"):
            return (body["choices"][0]["message"]["content"] or "").strip()
        return "".join(p.get("text", "") for p in body["candidates"][0]["content"]["parts"]).strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""


def _classify_status(status: int) -> LLMError:
    if status in (401, 403):
        return LLMError("The AI provider rejected the API key. Check the key and its permissions.",
                        reason="auth", retryable=False)
    if status == 404:
        return LLMError("The AI provider does not know that model name. Check the model in the channel settings.",
                        reason="model", retryable=False)
    if status == 429:
        return LLMError("The AI provider is rate limiting this key or the quota is used up.",
                        reason="rate_limit", retryable=True)
    if status >= 500:
        return LLMError("The AI provider is having problems right now.", reason="provider_down", retryable=True)
    return LLMError("The AI provider rejected the request.", reason="bad_request", retryable=False)


async def complete(cfg: LLMConfig, system: str, prompt: str, *, timeout: float = 25.0, retries: int = 2,
                   client: Optional[httpx.AsyncClient] = None) -> str:
    """One chat completion. Retries transient failures with exponential backoff and jitter."""
    if not cfg.api_key:
        raise LLMConfigError("No AI API key is saved for this channel. Add one in the channel settings.")
    url, headers, body = _request(cfg, system, prompt)
    owns = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=8.0), follow_redirects=False)
    try:
        last: LLMError = LLMError("The AI provider did not answer.", reason="unknown", retryable=True)
        for attempt in range(retries + 1):
            try:
                response = await client.post(url, headers=headers, json=body)
            except (httpx.TimeoutException, httpx.TransportError):
                last = LLMError("The AI provider did not answer in time.", reason="timeout", retryable=True)
            else:
                if response.status_code == 200:
                    try:
                        text = _extract(cfg.provider, response.json())
                    except ValueError:
                        text = ""
                    if text:
                        return text
                    last = LLMError("The AI provider returned an empty answer.", reason="empty", retryable=False)
                    raise last
                last = _classify_status(response.status_code)
            if not last.retryable or attempt >= retries:
                raise last
            await asyncio.sleep(min(6.0, 0.6 * (2 ** attempt)) * (0.5 + random.random()))
        raise last
    finally:
        if owns:
            await client.aclose()
