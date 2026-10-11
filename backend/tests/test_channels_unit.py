"""Stage 3 unit tests: webhook signatures, event extraction, validation, probe, backoff (no database)."""
import asyncio
import json

import httpx
import pytest

from app.channels import events, probe, signature
from app.channels.registry import (CHANNELS, ValidationFailed, validate_behaviour, validate_values)
from app.jobs.queue import MAX_ATTEMPTS, backoff_seconds

SECRET = "a" * 32


# --- signatures ----------------------------------------------------------------------------
def test_valid_signature_roundtrip():
    body = b'{"object":"page"}'
    assert signature.valid_signature(SECRET, body, signature.sign(SECRET, body))


@pytest.mark.parametrize("header", [None, "", "sha256=", "sha256=zz", "md5=" + "0" * 32, "sha256=" + "0" * 64])
def test_bad_signature_headers_rejected(header):
    assert not signature.valid_signature(SECRET, b"{}", header)


def test_signature_covers_exact_bytes():
    body = b'{"a":1}'
    header = signature.sign(SECRET, body)
    assert not signature.valid_signature(SECRET, b'{"a": 1}', header)       # re-serialised body
    assert not signature.valid_signature("b" * 32, body, header)            # other tenant's secret
    assert not signature.valid_signature("", body, header)                  # unconfigured channel


def test_verify_token_and_challenge():
    assert signature.valid_verify_token("tok", "tok")
    assert not signature.valid_verify_token("tok", "tok2")
    assert not signature.valid_verify_token("", "")
    assert signature.safe_challenge("1158201444") == "1158201444"
    assert signature.safe_challenge("<script>alert(1)</script>") is None
    assert signature.safe_challenge(None) is None


# --- event extraction ----------------------------------------------------------------------
def _wa(phone_id="111111", msgs=None, statuses=None):
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "metadata": {"phone_number_id": phone_id}, "contacts": [{"wa_id": "9230000"}],
        "messages": msgs or [], "statuses": statuses or []}}]}]}


def test_whatsapp_messages_and_statuses():
    out = events.extract(CHANNELS["whatsapp"], "111111", _wa(
        msgs=[{"id": "wamid.A", "type": "text"}], statuses=[{"id": "wamid.B", "status": "read"}]))
    assert [(e.key, e.kind) for e in out.events] == [("wamid:wamid.A", "message"), ("status:wamid.B:read", "status")]


def test_whatsapp_other_number_is_skipped():
    out = events.extract(CHANNELS["whatsapp"], "111111", _wa(phone_id="999999", msgs=[{"id": "x"}]))
    assert out.events == [] and out.skipped == 1


def test_wrong_object_is_skipped():
    out = events.extract(CHANNELS["whatsapp"], "1", {"object": "page", "entry": []})
    assert out.events == [] and out.skipped == 1


def test_facebook_inbox_message_postback_and_noise():
    payload = {"object": "page", "entry": [{"id": "55555", "messaging": [
        {"sender": {"id": "u"}, "message": {"mid": "m1", "text": "hi"}},
        {"sender": {"id": "u"}, "message": {"mid": "m2", "is_deleted": True}},
        {"sender": {"id": "u"}, "timestamp": 1, "postback": {"payload": "GET_STARTED"}},
        {"sender": {"id": "u"}, "delivery": {"mids": ["m1"]}},
    ]}]}
    out = events.extract(CHANNELS["facebook_inbox"], "55555", payload)
    assert [e.kind for e in out.events] == ["message", "unsend", "postback"]
    assert out.events[0].key == "mid:m1" and out.events[1].key == "mid:m2:del"
    assert out.skipped == 1


def test_facebook_page_mismatch_skipped():
    out = events.extract(CHANNELS["facebook_inbox"], "55555", {"object": "page", "entry": [{"id": "1", "messaging": [
        {"message": {"mid": "m"}}]}]})
    assert out.events == [] and out.skipped == 1


def test_facebook_comments_add_and_remove_have_distinct_keys():
    def change(verb):
        return {"field": "feed", "value": {"item": "comment", "comment_id": "c1", "verb": verb}}
    payload = {"object": "page", "entry": [{"id": "55555", "changes": [
        change("add"), change("remove"), {"field": "feed", "value": {"item": "reaction"}}]}]}
    out = events.extract(CHANNELS["facebook_comments"], "55555", payload)
    assert [(e.key, e.kind) for e in out.events] == [("comment:c1:add", "comment"), ("comment:c1:remove", "comment_removed")]
    assert out.skipped == 1


def test_instagram_comments_and_inbox():
    comments = {"object": "instagram", "entry": [{"id": "1784", "changes": [
        {"field": "comments", "value": {"id": "ic1", "text": "nice"}}]}]}
    out = events.extract(CHANNELS["instagram_comments"], "1784", comments)
    assert [e.key for e in out.events] == ["comment:ic1"]
    dm = {"object": "instagram", "entry": [{"id": "1784", "messaging": [{"message": {"mid": "im1"}}]}]}
    assert [e.key for e in events.extract(CHANNELS["instagram_inbox"], "1784", dm).events] == ["mid:im1"]


def test_garbage_payloads_never_raise():
    for junk in (None, [], "x", 5, {"object": "page", "entry": "nope"}, {"object": "page", "entry": [None, 3, {"id": "1", "messaging": "x"}]}):
        events.extract(CHANNELS["facebook_inbox"], "1", junk)


def test_event_cap_per_request():
    msgs = [{"id": f"m{i}"} for i in range(events.MAX_EVENTS_PER_REQUEST + 50)]
    assert len(events.extract(CHANNELS["whatsapp"], "111111", _wa(msgs=msgs)).events) == events.MAX_EVENTS_PER_REQUEST


# --- registry validation -------------------------------------------------------------------
def test_every_channel_has_required_secrets_and_a_guide():
    for channel in CHANNELS.values():
        assert channel.guide.endswith(".md")
        assert {"app_secret", "llm_api_key"} <= set(channel.secret_keys)
        assert channel.account_field in {f.key for f in channel.fields}


def test_validate_values_accepts_good_and_ignores_blank():
    wa = CHANNELS["whatsapp"]
    clean = validate_values(wa, {"phone_number_id": " 123456789012345 ", "access_token": "", "app_secret": None})
    assert clean == {"phone_number_id": "123456789012345"}


@pytest.mark.parametrize("key,value", [
    ("phone_number_id", "12 34"), ("phone_number_id", "abc"), ("access_token", "has space token 1234567890"),
    ("app_secret", "short"), ("unknown", "x"), ("access_token", 12345),
])
def test_validate_values_rejects_bad(key, value):
    with pytest.raises(ValidationFailed) as exc:
        validate_values(CHANNELS["whatsapp"], {key: value})
    assert key in exc.value.errors


def test_validate_behaviour():
    ok = validate_behaviour({"tone": "formal", "spam_action": "hide", "llm_provider": "openai",
                             "business_phone": "+92 300 1234567", "system_prompt": "Be kind."})
    assert ok["tone"] == "formal"
    for bad in ({"tone": "angry"}, {"spam_action": "nuke"}, {"llm_provider": "x"}, {"business_phone": "call me"},
                {"system_prompt": "x" * 9000}, {"nope": "x"}, {"llm_model": "bad model!"}):
        with pytest.raises(ValidationFailed):
            validate_behaviour(bad)


# --- probe (Meta is mocked; no network) ----------------------------------------------------
def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def run(coro):
    return asyncio.run(coro)


def test_probe_success_sends_token_in_header_not_url():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"], seen["auth"] = str(request.url), request.headers.get("authorization")
        return httpx.Response(200, json={"display_phone_number": "+1 555 0100", "verified_name": "Shop"})

    result = run(probe.run(CHANNELS["whatsapp"], token="SECRETTOKEN1234567", account_id="111111", version="v21.0",
                           client=_client(handler)))
    assert result.ok and "+1 555 0100" in result.message
    assert "SECRETTOKEN1234567" not in seen["url"] and seen["auth"] == "Bearer SECRETTOKEN1234567"
    assert seen["url"].startswith("https://graph.facebook.com/v21.0/111111")


def test_probe_maps_meta_error_codes_and_never_leaks_meta_text():
    def handler(request):
        return httpx.Response(400, json={"error": {"code": 190, "message": "Invalid OAuth access token SECRETTOKEN1234567"}})

    result = run(probe.run(CHANNELS["facebook_inbox"], token="SECRETTOKEN1234567", account_id="5555555", version="v21.0",
                           client=_client(handler)))
    assert not result.ok and "expired" in result.message and "SECRETTOKEN" not in result.message


def test_probe_timeout_is_friendly():
    def handler(request):
        raise httpx.ConnectTimeout("slow")

    result = run(probe.run(CHANNELS["facebook_comments"], token="t" * 20, account_id="5555555", version="v21.0",
                           client=_client(handler)))
    assert not result.ok and "did not respond" in result.message


def test_probe_instagram_account_mismatch_warns():
    def handler(request):
        assert request.url.host == "graph.instagram.com" and request.url.path.endswith("/me")
        return httpx.Response(200, json={"user_id": "999999", "username": "shop"})

    result = run(probe.run(CHANNELS["instagram_inbox"], token="t" * 20, account_id="123456", version="v25.0",
                           client=_client(handler)))
    assert result.ok and result.warnings and "@shop" in result.message


# --- retry policy --------------------------------------------------------------------------
def test_backoff_grows_and_is_capped():
    delays = [backoff_seconds(n) for n in range(1, MAX_ATTEMPTS + 1)]
    assert delays == sorted(delays) and delays[0] == 20
    assert backoff_seconds(50) == 3600
