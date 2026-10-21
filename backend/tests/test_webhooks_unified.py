"""Unified per-platform webhooks (whatsapp | facebook | instagram). No database: the channel service, queue and
rate limiter are in-memory fakes, so these run in the plain unit-test pass."""
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api import webhooks
from app.channels import signature
from app.channels.registry import (CHANNELS, PLATFORMS, channels_for_platform, resolve_webhook_target,
                                   webhook_fields_for_platform)
from app.channels.service import WebhookConfig, WebhookTarget
from app.errors import install_error_handlers

pytestmark = pytest.mark.asyncio

SECRET = "s" * 32
TOKEN = "verify-token-shared"
PAGE, IG, PHONE = "111111111111111", "17841400000000001", "123456789012345"


class FakeChannels:
    def __init__(self):
        self.rows = {}          # (tenant, channel_id) -> WebhookConfig

    def configure(self, tenant, channel_id, *, secret=SECRET, token=TOKEN, enabled=True, account=None):
        account = account or {"facebook": PAGE, "instagram": IG, "whatsapp": PHONE}[CHANNELS[channel_id].platform]
        self.rows[(tenant, channel_id)] = WebhookConfig(enabled, secret, token, account)

    async def webhook_targets(self, tenant, channels):
        return [WebhookTarget(c, self.rows[(tenant, c.id)]) for c in channels if (tenant, c.id) in self.rows]


class FakeQueue:
    def __init__(self):
        self.events = {}        # (tenant, channel, key) -> Event

    async def enqueue(self, tenant, channel, events):
        new = 0
        for e in events:
            if (tenant, channel, e.key) not in self.events:
                self.events[(tenant, channel, e.key)] = e
                new += 1
        return new

    def by_channel(self):
        out = {}
        for (_, channel, key) in self.events:
            out.setdefault(channel, []).append(key)
        return {k: sorted(v) for k, v in out.items()}


class FakeRepo:
    async def rate_hit(self, key, window, now):
        return 1, 0


@pytest.fixture
def env():
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(webhooks.router)
    channels, queue = FakeChannels(), FakeQueue()
    app.state.settings = SimpleNamespace(webhook_max_body_bytes=1_000_000, webhook_rate_per_minute=1000)
    app.state.channels, app.state.queue = channels, queue
    app.state.auth_service = SimpleNamespace(repo=FakeRepo())
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    return SimpleNamespace(app=app, channels=channels, queue=queue, client=client, tenant=uuid4())


# --- payload builders ------------------------------------------------------------------------
def fb_message(mid, page=PAGE):
    return {"id": page, "messaging": [{"sender": {"id": "u1"}, "recipient": {"id": page}, "timestamp": 1,
                                       "message": {"mid": mid, "text": "hi"}}]}


def fb_comment(cid, page=PAGE):
    return {"id": page, "changes": [{"field": "feed", "value": {"item": "comment", "verb": "add", "comment_id": cid,
                                                                  "message": "nice"}}]}


def ig_message(mid, account=IG):
    return {"id": account, "messaging": [{"sender": {"id": "u1"}, "recipient": {"id": account}, "timestamp": 1,
                                          "message": {"mid": mid, "text": "hi"}}]}


def ig_comment(cid, account=IG):
    return {"id": account, "changes": [{"field": "comments", "value": {"id": cid, "text": "nice"}}]}


def wa_message(wamid, phone=PHONE):
    return {"changes": [{"field": "messages", "value": {"metadata": {"phone_number_id": phone},
                                                         "messages": [{"id": wamid, "type": "text", "text": {"body": "hi"}}]}}]}


async def post(env, target, obj, entries, secret=SECRET):
    body = json.dumps({"object": obj, "entry": entries}).encode()
    return await env.client.post(f"/webhooks/{env.tenant}/{target}", content=body,
                                 headers={"X-Hub-Signature-256": signature.sign(secret, body)})


# --- registry --------------------------------------------------------------------------------
async def test_three_platforms_cover_all_five_channels():
    assert PLATFORMS == ("whatsapp", "facebook", "instagram")
    assert {c.id for p in PLATFORMS for c in channels_for_platform(p)} == set(CHANNELS)
    assert [c.id for c in channels_for_platform("facebook")] == ["facebook_inbox", "facebook_comments"]
    assert [c.id for c in channels_for_platform("instagram")] == ["instagram_inbox", "instagram_comments"]


async def test_one_subscription_lists_the_fields_of_every_channel_on_the_platform():
    assert webhook_fields_for_platform("whatsapp") == ("messages",)
    assert webhook_fields_for_platform("facebook") == ("messages", "messaging_postbacks", "feed")
    assert webhook_fields_for_platform("instagram") == ("messages", "comments")


async def test_resolve_webhook_target():
    assert resolve_webhook_target("facebook")[0] == "facebook"
    assert [c.id for c in resolve_webhook_target("facebook")[1]] == ["facebook_inbox", "facebook_comments"]
    assert resolve_webhook_target("whatsapp")[0] == "whatsapp"
    platform, legacy = resolve_webhook_target("instagram_comments")              # old per-channel URL still resolves
    assert platform == "instagram" and [c.id for c in legacy] == ["instagram_comments"]
    assert resolve_webhook_target("telegram") == (None, ())


# --- one webhook, both kinds of event --------------------------------------------------------
async def test_facebook_webhook_delivers_inbox_and_comment_events(env):
    for ch in ("facebook_inbox", "facebook_comments"):
        env.channels.configure(env.tenant, ch)
    r = await post(env, "facebook", "page", [fb_message("m1"), fb_comment("p_c1"), fb_message("m2")])
    assert r.status_code == 200 and r.json()["queued"] == 3
    assert env.queue.by_channel() == {"facebook_inbox": ["mid:m1", "mid:m2"], "facebook_comments": ["comment:p_c1:add"]}


async def test_instagram_webhook_delivers_inbox_and_comment_events(env):
    for ch in ("instagram_inbox", "instagram_comments"):
        env.channels.configure(env.tenant, ch)
    r = await post(env, "instagram", "instagram", [ig_message("i1"), ig_comment("c1")])
    assert r.status_code == 200 and r.json() == {"received": 2, "queued": 2, "skipped": 0}
    assert env.queue.by_channel() == {"instagram_inbox": ["mid:i1"], "instagram_comments": ["comment:c1"]}


async def test_whatsapp_webhook_is_unchanged(env):
    env.channels.configure(env.tenant, "whatsapp")
    r = await post(env, "whatsapp", "whatsapp_business_account", [wa_message("wamid.1")])
    assert r.json() == {"received": 1, "queued": 1, "skipped": 0}
    assert (await post(env, "whatsapp", "whatsapp_business_account", [wa_message("wamid.1")])).json()["queued"] == 0   # redelivery


async def test_redelivery_is_idempotent_on_the_shared_webhook(env):
    for ch in ("facebook_inbox", "facebook_comments"):
        env.channels.configure(env.tenant, ch)
    entries = [fb_message("m1"), fb_comment("p_c1")]
    assert (await post(env, "facebook", "page", entries)).json()["queued"] == 2
    assert (await post(env, "facebook", "page", entries)).json()["queued"] == 0


async def test_a_platform_with_only_one_channel_set_up_still_works(env):
    env.channels.configure(env.tenant, "facebook_comments")                       # inbox not configured at all
    r = await post(env, "facebook", "page", [fb_message("m1"), fb_comment("p_c1")])
    assert r.status_code == 200
    assert env.queue.by_channel() == {"facebook_comments": ["comment:p_c1:add"]}


async def test_disabled_channel_receives_nothing_but_its_sibling_still_does(env):
    env.channels.configure(env.tenant, "facebook_inbox", enabled=False)
    env.channels.configure(env.tenant, "facebook_comments", enabled=True)
    await post(env, "facebook", "page", [fb_message("m1"), fb_comment("p_c1")])
    assert env.queue.by_channel() == {"facebook_comments": ["comment:p_c1:add"]}


async def test_all_channels_disabled_acknowledges_and_queues_nothing(env):
    for ch in ("facebook_inbox", "facebook_comments"):
        env.channels.configure(env.tenant, ch, enabled=False)
    r = await post(env, "facebook", "page", [fb_message("m1")])
    assert r.status_code == 200 and r.json()["status"] == "ignored" and not env.queue.events


async def test_events_for_another_page_are_skipped(env):
    for ch in ("facebook_inbox", "facebook_comments"):
        env.channels.configure(env.tenant, ch)
    r = await post(env, "facebook", "page", [fb_message("m1", page="999999999999999"), fb_comment("c", page="999999999999999")])
    assert r.json() == {"received": 0, "queued": 0, "skipped": 2}


async def test_wrong_object_for_the_platform_is_skipped(env):
    env.channels.configure(env.tenant, "instagram_inbox")
    r = await post(env, "instagram", "page", [fb_message("m1")])                  # a Page payload sent to the Instagram URL
    assert r.status_code == 200 and r.json()["queued"] == 0 and not env.queue.events


# --- signatures ------------------------------------------------------------------------------
async def test_each_channel_only_receives_events_its_own_app_secret_signed(env):
    env.channels.configure(env.tenant, "facebook_inbox", secret="a" * 32)
    env.channels.configure(env.tenant, "facebook_comments", secret="b" * 32)
    await post(env, "facebook", "page", [fb_message("m1"), fb_comment("p_c1")], secret="b" * 32)
    assert env.queue.by_channel() == {"facebook_comments": ["comment:p_c1:add"]}   # inbox's secret did not validate


async def test_bad_signature_unknown_tenant_and_unknown_platform_are_indistinguishable(env):
    env.channels.configure(env.tenant, "facebook_inbox")
    bad = await post(env, "facebook", "page", [fb_message("evil")], secret="x" * 32)
    unknown_tenant = await env.client.post(f"/webhooks/{uuid4()}/facebook", content=b"{}")
    not_uuid = await env.client.post("/webhooks/not-a-uuid/facebook", content=b"{}")
    unknown_platform = await post(env, "telegram", "page", [fb_message("evil")])
    unconfigured = await env.client.post(f"/webhooks/{env.tenant}/instagram", content=b"{}")
    responses = [bad, unknown_tenant, not_uuid, unknown_platform, unconfigured]
    assert {r.status_code for r in responses} == {403} and len({r.text for r in responses}) == 1
    assert not env.queue.events


async def test_missing_signature_header_is_rejected(env):
    env.channels.configure(env.tenant, "facebook_inbox")
    r = await env.client.post(f"/webhooks/{env.tenant}/facebook", content=json.dumps({"object": "page", "entry": []}).encode())
    assert r.status_code == 403


async def test_oversized_body_is_rejected(env):
    env.app.state.settings.webhook_max_body_bytes = 1000
    env.channels.configure(env.tenant, "facebook_inbox")
    assert (await env.client.post(f"/webhooks/{env.tenant}/facebook", content=b"x" * 5000)).status_code == 413


# --- handshake -------------------------------------------------------------------------------
async def test_handshake_accepts_the_shared_token_on_the_platform_url(env):
    for ch in ("facebook_inbox", "facebook_comments"):
        env.channels.configure(env.tenant, ch)
    ok = await env.client.get(f"/webhooks/{env.tenant}/facebook", params={
        "hub.mode": "subscribe", "hub.verify_token": TOKEN, "hub.challenge": "1158201444"})
    assert ok.status_code == 200 and ok.text == "1158201444"


async def test_handshake_accepts_either_channels_token_for_old_setups(env):
    env.channels.configure(env.tenant, "instagram_inbox", token="token-of-inbox")
    env.channels.configure(env.tenant, "instagram_comments", token="token-of-comments")
    for token in ("token-of-inbox", "token-of-comments"):
        r = await env.client.get(f"/webhooks/{env.tenant}/instagram", params={
            "hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": "42"})
        assert r.status_code == 200


async def test_handshake_rejects_wrong_token_bad_challenge_and_wrong_mode(env):
    env.channels.configure(env.tenant, "facebook_inbox")
    url = f"/webhooks/{env.tenant}/facebook"
    for params in ({"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"},
                   {"hub.mode": "subscribe", "hub.verify_token": TOKEN, "hub.challenge": "<b>x</b>"},
                   {"hub.mode": "unsubscribe", "hub.verify_token": TOKEN, "hub.challenge": "1"},
                   {"hub.mode": "subscribe", "hub.challenge": "1"}):
        assert (await env.client.get(url, params=params)).status_code == 403


# --- legacy per-channel URLs -----------------------------------------------------------------
async def test_legacy_channel_url_serves_only_that_channel(env):
    for ch in ("facebook_inbox", "facebook_comments"):
        env.channels.configure(env.tenant, ch)
    r = await post(env, "facebook_comments", "page", [fb_message("m1"), fb_comment("p_c1")])
    assert r.status_code == 200
    assert env.queue.by_channel() == {"facebook_comments": ["comment:p_c1:add"]}
