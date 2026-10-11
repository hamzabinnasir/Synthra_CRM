"""Stage 3 integration tests against a real PostgreSQL (skipped unless CRM_TEST_DATABASE_URL is set)."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app import migrator
from app.api import webhooks
from app.auth.repo_pg import PgAuthRepository
from app.channels import signature
from app.channels.events import Event
from app.channels.registry import CHANNELS, ValidationFailed
from app.channels.service import ChannelService
from app.errors import install_error_handlers
from app.jobs.queue import MAX_ATTEMPTS, RETENTION_DAYS, EventQueue
from app.jobs.worker import PermanentError, Worker

from .conftest import CONTROL_MIGRATIONS, make_settings, make_tenant

pytestmark = pytest.mark.asyncio


def now():
    return datetime.now(timezone.utc)


def ev(key, kind="message"):
    return Event(key, kind, {"text": "hello " + key})


# --- migrations ----------------------------------------------------------------------------
async def test_control_migrations_all_applied(control_pool):
    async with control_pool.acquire() as conn:
        states = {s.version: s.state for s in await migrator.status(conn, CONTROL_MIGRATIONS)}
        assert states and set(states.values()) == {"applied"} and "0003" in states
        assert await migrator.migrate(conn, CONTROL_MIGRATIONS) == []          # idempotent


# --- queue ---------------------------------------------------------------------------------
async def test_enqueue_is_idempotent(control_pool):
    q, t = EventQueue(control_pool), await make_tenant(control_pool)
    assert await q.enqueue(t, "whatsapp", [ev("a"), ev("b")]) == 2
    assert await q.enqueue(t, "whatsapp", [ev("a"), ev("c")]) == 1             # Meta redelivery of "a" is a no-op
    assert await q.enqueue(t, "facebook_inbox", [ev("a")]) == 1                # same key, other channel is distinct
    assert (await q.stats(t))["pending"] == 4


async def test_claim_marks_processing_and_filters_channels(control_pool):
    q, t = EventQueue(control_pool), await make_tenant(control_pool)
    await q.enqueue(t, "whatsapp", [ev("a")])
    await q.enqueue(t, "facebook_inbox", [ev("b")])
    got = await q.claim("w1", ["whatsapp"], now())
    assert [(e.channel, e.key, e.attempts, e.data["text"]) for e in got] == [("whatsapp", "wamid", 1, "hello a")][:0] or \
        [(e.channel, e.key, e.attempts) for e in got] == [("whatsapp", "a", 1)]
    assert await q.claim("w1", ["whatsapp"], now()) == []                      # already taken
    assert await q.claim("w1", [], now()) == []                                # no handlers, no claims
    assert (await q.stats(t))["processing"] == 1 and (await q.stats(t))["pending"] == 1


async def test_concurrent_workers_never_claim_the_same_event(control_pool):
    q, t = EventQueue(control_pool), await make_tenant(control_pool)
    await q.enqueue(t, "whatsapp", [ev(f"e{i}") for i in range(40)])
    batches = await asyncio.gather(*[q.claim(f"w{i}", ["whatsapp"], now(), limit=10, per_tenant=10) for i in range(8)])
    ids = [e.id for batch in batches for e in batch]
    assert len(ids) == len(set(ids)) == 40


async def test_one_busy_tenant_cannot_starve_another(control_pool):
    q = EventQueue(control_pool)
    busy, quiet = await make_tenant(control_pool, "busy"), await make_tenant(control_pool, "quiet")
    await q.enqueue(busy, "whatsapp", [ev(f"b{i}") for i in range(30)])
    await q.enqueue(quiet, "whatsapp", [ev("q1"), ev("q2")])
    got = await q.claim("w", ["whatsapp"], now(), limit=6, per_tenant=3)
    by_tenant = {}
    for e in got:
        by_tenant[e.tenant_id] = by_tenant.get(e.tenant_id, 0) + 1
    assert by_tenant[quiet] == 2 and by_tenant[busy] == 3


async def test_complete_erases_payload_but_keeps_idempotency_key(control_pool):
    q, t = EventQueue(control_pool), await make_tenant(control_pool)
    await q.enqueue(t, "whatsapp", [ev("secret-msg")])
    (e,) = await q.claim("w", ["whatsapp"], now())
    await q.complete(e.id, now())
    row = await control_pool.fetchrow("select status, payload::text as p from webhook_events where id = $1", e.id)
    assert row["status"] == "done" and "hello" not in row["p"]
    assert await q.enqueue(t, "whatsapp", [ev("secret-msg")]) == 0             # late redelivery still deduplicated


async def test_failures_retry_with_backoff_then_die(control_pool):
    q, t = EventQueue(control_pool), await make_tenant(control_pool)
    await q.enqueue(t, "whatsapp", [ev("a")])
    clock = now()
    for attempt in range(1, MAX_ATTEMPTS + 1):
        (e,) = await q.claim("w", ["whatsapp"], clock)
        assert e.attempts == attempt
        status = await q.fail(e.id, e.attempts, "Boom", clock)
        if attempt < MAX_ATTEMPTS:
            assert status == "pending" and await q.claim("w", ["whatsapp"], clock) == []   # not before the delay
            clock += timedelta(hours=2)
    assert status == "dead"
    assert (await q.stats(t))["dead"] == 1 and await q.claim("w", ["whatsapp"], clock) == []


async def test_permanent_failure_is_buried_immediately(control_pool):
    q, t = EventQueue(control_pool), await make_tenant(control_pool)
    await q.enqueue(t, "whatsapp", [ev("a")])
    (e,) = await q.claim("w", ["whatsapp"], now())
    assert await q.fail(e.id, e.attempts, "Gone", now(), permanent=True) == "dead"


async def test_stale_processing_events_are_reclaimed_and_old_ones_purged(control_pool):
    q, t = EventQueue(control_pool), await make_tenant(control_pool)
    await q.enqueue(t, "whatsapp", [ev("a"), ev("b")])
    claimed = await q.claim("crashed-worker", ["whatsapp"], now(), limit=1)
    assert len(claimed) == 1
    # Simulate a worker that died an hour ago while holding the event.
    await control_pool.execute("update webhook_events set locked_at = $2 where id = $1",
                               claimed[0].id, now() - timedelta(hours=1))
    assert await q.reclaim_stale(now()) == 1
    assert (await q.stats(t))["pending"] == 2
    (e,) = await q.claim("w", ["whatsapp"], now(), limit=1)
    await q.complete(e.id, now() - timedelta(days=RETENTION_DAYS + 1))
    assert await q.purge(now()) == 1


async def test_worker_runs_handlers_retries_and_buries(control_pool):
    q, t = EventQueue(control_pool), await make_tenant(control_pool)
    await q.enqueue(t, "whatsapp", [ev("ok"), ev("flaky"), ev("gone")])
    seen = []

    async def handler(event):
        seen.append(event.key)
        if event.key == "flaky":
            raise RuntimeError("token=SECRET123 leaked in message")
        if event.key == "gone":
            raise PermanentError("account deleted")

    worker = Worker(q, {"whatsapp": handler}, concurrency=4)
    assert await worker.run_once() == 3
    await worker.drain()
    rows = {r["event_key"]: r for r in await control_pool.fetch("select event_key, status, last_error from webhook_events")}
    assert rows["ok"]["status"] == "done"
    assert rows["flaky"]["status"] == "pending" and rows["flaky"]["last_error"] == "RuntimeError"   # class name only
    assert "SECRET123" not in (rows["flaky"]["last_error"] or "")
    assert rows["gone"]["status"] == "dead"


# --- channel service -----------------------------------------------------------------------
def service(pool, box):
    return ChannelService(pool, box, make_settings())


WA = {"phone_number_id": "123456789012345", "access_token": "EAAGsupersecrettoken123456", "app_secret": "s" * 32}


async def test_secrets_are_encrypted_at_rest_and_masked_in_the_view(control_pool, box):
    svc, t = service(control_pool, box), await make_tenant(control_pool)
    view = await svc.save(t, "whatsapp", values={**WA, "llm_api_key": "sk-ant-verysecretkey"}, behaviour={}, enabled=None, bot_enabled=None)
    raw = await control_pool.fetchval("select to_jsonb(c)::text from channel_configs c where tenant_id = $1", t)
    for secret in ("supersecrettoken", "sk-ant-verysecret", WA["app_secret"]):
        assert secret not in raw and secret not in json.dumps(view)
    assert view["values"]["access_token"] == {"saved": True, "masked": "\u2022" * 8 + "3456"}
    assert view["values"]["phone_number_id"]["value"] == WA["phone_number_id"]         # ids are not secret
    assert view["status"] == "configured" and view["missing"] == []


async def test_blank_secret_keeps_existing_and_partial_save_reports_missing(control_pool, box):
    svc, t = service(control_pool, box), await make_tenant(control_pool)
    v = await svc.save(t, "whatsapp", values={"phone_number_id": WA["phone_number_id"]}, behaviour={}, enabled=None, bot_enabled=None)
    assert set(v["missing"]) == {"access_token", "app_secret"} and v["status"] == "not_configured"
    await svc.save(t, "whatsapp", values={"access_token": WA["access_token"], "app_secret": WA["app_secret"]}, behaviour={}, enabled=None, bot_enabled=None)
    v = await svc.save(t, "whatsapp", values={"access_token": "", "app_secret": None}, behaviour={"tone": "formal"}, enabled=None, bot_enabled=None)
    assert v["missing"] == [] and v["behaviour"]["tone"] == "formal"
    assert (await svc.credentials(t, "whatsapp"))["secrets"]["access_token"] == WA["access_token"]


async def test_cannot_enable_an_incomplete_channel(control_pool, box):
    svc, t = service(control_pool, box), await make_tenant(control_pool)
    with pytest.raises(ValidationFailed):
        await svc.save(t, "whatsapp", values={}, behaviour={}, enabled=True, bot_enabled=None)
    v = await svc.save(t, "whatsapp", values=WA, behaviour={}, enabled=True, bot_enabled=False)
    assert v["enabled"] is True and v["bot_enabled"] is False


async def test_changing_a_credential_invalidates_the_last_test(control_pool, box):
    svc, t = service(control_pool, box), await make_tenant(control_pool)
    await svc.save(t, "whatsapp", values=WA, behaviour={}, enabled=None, bot_enabled=None)
    await control_pool.execute("update channel_configs set status = 'connected', last_tested_at = now() where tenant_id = $1", t)
    v = await svc.save(t, "whatsapp", values={"access_token": "EAAGanotherbrandnewtoken999"}, behaviour={}, enabled=None, bot_enabled=None)
    assert v["status"] == "configured" and v["last_tested_at"] is None
    v = await svc.save(t, "whatsapp", values={}, behaviour={"tone": "playful"}, enabled=None, bot_enabled=None)
    assert v["status"] == "configured"


async def test_verify_token_is_stable_unique_and_rotatable(control_pool, box):
    svc, t1, t2 = service(control_pool, box), await make_tenant(control_pool), await make_tenant(control_pool)
    a = (await svc.get(t1, "whatsapp"))["webhook"]
    assert a == (await svc.get(t1, "whatsapp"))["webhook"] and len(a["verify_token"]) >= 40
    assert a["url"] == f"https://api.example.com/webhooks/{t1}/whatsapp"
    assert (await svc.get(t2, "whatsapp"))["webhook"]["verify_token"] != a["verify_token"]
    assert (await svc.rotate_verify_token(t1, "whatsapp"))["webhook"]["verify_token"] != a["verify_token"]


async def test_tenants_are_isolated_and_ciphertext_cannot_be_moved_between_them(control_pool, box):
    svc, t1, t2 = service(control_pool, box), await make_tenant(control_pool), await make_tenant(control_pool)
    await svc.save(t1, "whatsapp", values=WA, behaviour={}, enabled=None, bot_enabled=None)
    assert (await svc.get(t2, "whatsapp"))["values"]["access_token"]["saved"] is False
    await svc.get(t2, "whatsapp")
    stolen = await control_pool.fetchval("select encrypted_secrets from channel_configs where tenant_id = $1", t1)
    await control_pool.execute("update channel_configs set encrypted_secrets = $2 where tenant_id = $1", t2, stolen)
    assert await svc.webhook_config(t2, "whatsapp") is None                    # AAD mismatch -> unreadable, not leaked


async def test_unknown_channel_is_a_404(control_pool, box):
    from app.errors import NotFound
    with pytest.raises(NotFound):
        await service(control_pool, box).get(await make_tenant(control_pool), "telegram")


# --- webhook endpoints, end to end ---------------------------------------------------------
async def webhook_app(pool, box, **overrides):
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(webhooks.router)
    settings = make_settings(**overrides)
    app.state.settings = settings
    app.state.channels = ChannelService(pool, box, settings)
    app.state.queue = EventQueue(pool)
    app.state.auth_service = SimpleNamespace(repo=PgAuthRepository(pool))
    return app


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def wa_payload(*ids, phone="123456789012345"):
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "metadata": {"phone_number_id": phone}, "messages": [{"id": i, "type": "text", "text": {"body": "hi"}} for i in ids]}}]}]}


async def post(c, t, payload, secret=WA["app_secret"], channel="whatsapp", header=None):
    body = json.dumps(payload).encode()
    return await c.post(f"/webhooks/{t}/{channel}", content=body, headers={
        "X-Hub-Signature-256": header or signature.sign(secret, body), "Content-Type": "application/json"})


async def test_webhook_handshake(control_pool, box):
    app, t = await webhook_app(control_pool, box), await make_tenant(control_pool)
    token = (await app.state.channels.get(t, "whatsapp"))["webhook"]["verify_token"]
    async with client(app) as c:
        ok = await c.get(f"/webhooks/{t}/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": "1158201444"})
        assert ok.status_code == 200 and ok.text == "1158201444"
        for params in ({"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"},
                       {"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": "<b>x</b>"},
                       {"hub.mode": "unsubscribe", "hub.verify_token": token, "hub.challenge": "1"}):
            assert (await c.get(f"/webhooks/{t}/whatsapp", params=params)).status_code == 403


async def test_webhook_flow_signature_dedupe_and_worker(control_pool, box):
    app, t = await webhook_app(control_pool, box), await make_tenant(control_pool)
    await app.state.channels.save(t, "whatsapp", values=WA, behaviour={}, enabled=True, bot_enabled=None)
    async with client(app) as c:
        r = await post(c, t, wa_payload("wamid.1", "wamid.2"))
        assert r.status_code == 200 and r.json() == {"received": 2, "queued": 2, "skipped": 0}
        assert (await post(c, t, wa_payload("wamid.2", "wamid.3"))).json()["queued"] == 1        # redelivery deduplicated
        assert (await post(c, t, wa_payload("wamid.9", phone="999999999"))).json() == {"received": 0, "queued": 0, "skipped": 1}

        bad = await post(c, t, wa_payload("wamid.evil"), secret="x" * 32)
        unknown = await post(c, "00000000-0000-0000-0000-000000000000", wa_payload("wamid.evil"))
        not_uuid = await c.post("/webhooks/not-a-uuid/whatsapp", content=b"{}")
        assert bad.status_code == unknown.status_code == not_uuid.status_code == 403
        assert bad.text == unknown.text == not_uuid.text                      # indistinguishable: no tenant enumeration
        assert (await post(c, t, wa_payload("w"), channel="nonexistent")).status_code == 403
        assert (await control_pool.fetchval("select count(*) from webhook_events where event_key like '%evil%'")) == 0

    handled = []

    async def handler(event):
        handled.append((event.key, event.data["message"]["text"]["body"]))

    worker = Worker(app.state.queue, {"whatsapp": handler})
    await worker.run_once()
    await worker.drain()
    assert sorted(handled) == [("wamid:wamid.1", "hi"), ("wamid:wamid.2", "hi"), ("wamid:wamid.3", "hi")]
    assert (await app.state.queue.stats(t))["done"] == 3


async def test_disabled_channel_acknowledges_but_queues_nothing(control_pool, box):
    app, t = await webhook_app(control_pool, box), await make_tenant(control_pool)
    await app.state.channels.save(t, "whatsapp", values=WA, behaviour={}, enabled=False, bot_enabled=None)
    async with client(app) as c:
        r = await post(c, t, wa_payload("wamid.1"))
    assert r.status_code == 200 and r.json()["status"] == "ignored"
    assert await control_pool.fetchval("select count(*) from webhook_events") == 0


async def test_oversized_body_and_rate_limit(control_pool, box):
    app = await webhook_app(control_pool, box, CRM_WEBHOOK_MAX_BODY_BYTES=10_000, CRM_WEBHOOK_RATE_PER_MINUTE=10)
    t = await make_tenant(control_pool)
    await app.state.channels.save(t, "whatsapp", values=WA, behaviour={}, enabled=True, bot_enabled=None)
    async with client(app) as c:
        assert (await c.post(f"/webhooks/{t}/whatsapp", content=b"x" * 20_000)).status_code == 413
        codes = [(await post(c, t, wa_payload(f"m{i}"))).status_code for i in range(13)]
    assert codes[:10] == [200] * 10 and codes[10:] == [429] * 3
