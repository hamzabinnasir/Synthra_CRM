"""Integration tests for the tenant-database data layer (conversations, comments, notifications, analytics, RLS).

Run against a real PostgreSQL with pgvector. Skipped unless CRM_TEST_DATABASE_URL is set (see scripts/dev_postgres.py).
Each test gets its own throw-away database migrated with the real tenant migrations.
"""
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest
import pytest_asyncio

from app import migrator
from app.crm import store
from app.tenantdb import provisioner

from .conftest import BASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def tpool():
    """A brand-new, fully migrated tenant database."""
    if not BASE_URL:
        pytest.skip("CRM_TEST_DATABASE_URL is not set")
    admin = await asyncpg.connect(BASE_URL)
    name = "tt_" + uuid.uuid4().hex[:12]
    await admin.execute(f"create database {name}")
    pool = await asyncpg.create_pool(BASE_URL.rsplit("/", 1)[0] + "/" + name, min_size=1, max_size=6,
                                     statement_cache_size=0)
    try:
        async with pool.acquire() as conn:
            await migrator.migrate(conn, provisioner.TENANT_MIGRATIONS)
        yield pool
    finally:
        await pool.close()
        await admin.execute(f"drop database if exists {name} with (force)")
        await admin.close()


def now():
    return datetime.now(timezone.utc)


async def seed(pool, psid="wa_923001234567", platform="whatsapp", text="hello", mid="m1", sender="customer"):
    await store.upsert_customer(pool, psid, platform, name="Ayesha")
    return await store.insert_message(pool, psid=psid, platform=platform, sender=sender, text=text, mid=mid)


# --- migrations ----------------------------------------------------------------------------
async def test_tenant_migrations_are_idempotent(tpool):
    async with tpool.acquire() as conn:
        await migrator.migrate(conn, provisioner.TENANT_MIGRATIONS)   # second run must be a no-op, not an error
    assert await tpool.fetchval("select count(*) from crm.customers") == 0


async def test_rls_is_enabled_on_every_crm_table(tpool):
    off = await tpool.fetch(
        "select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
        "where n.nspname = 'crm' and c.relkind = 'r' and not c.relrowsecurity")
    assert [r["relname"] for r in off] == []


# --- conversations -------------------------------------------------------------------------
async def test_message_insert_updates_preview_and_unread(tpool):
    await seed(tpool, text="Do you ship to Lahore?")
    conv = (await store.list_conversations(tpool, "whatsapp"))[0]
    assert conv["last_message_text"] == "Do you ship to Lahore?"
    assert conv["unread_count"] == 1
    await store.mark_read(tpool, "wa_923001234567")
    assert (await store.get_customer(tpool, "wa_923001234567"))["unread_count"] == 0


async def test_bot_and_agent_messages_do_not_raise_unread(tpool):
    await seed(tpool)
    await store.mark_read(tpool, "wa_923001234567")
    await store.insert_message(tpool, psid="wa_923001234567", platform="whatsapp", sender="bot", text="Yes!", mid="m2")
    assert (await store.get_customer(tpool, "wa_923001234567"))["unread_count"] == 0


async def test_duplicate_message_id_is_ignored(tpool):
    first = await seed(tpool, mid="dup")
    second = await store.insert_message(tpool, psid="wa_923001234567", platform="whatsapp", sender="customer", text="x", mid="dup")
    assert first is not None and second is None
    assert len(await store.get_messages(tpool, "wa_923001234567")) == 1


async def test_conversation_filters_and_platform_separation(tpool):
    await seed(tpool, psid="wa_1", platform="whatsapp", text="order status")
    await seed(tpool, psid="ig_1", platform="instagram", text="price?", mid="m9")
    assert {c["psid"] for c in await store.list_conversations(tpool, "instagram")} == {"ig_1"}
    assert {c["psid"] for c in await store.list_conversations(tpool, None)} == {"wa_1", "ig_1"}
    assert [c["psid"] for c in await store.list_conversations(tpool, None, q="order")] == ["wa_1"]
    # LIKE wildcards are stripped, so "%" behaves like an empty search (everything), never a pattern
    assert len(await store.list_conversations(tpool, None, q="%")) == 2
    assert await store.list_conversations(tpool, None, q="zzz-no-match") == []


async def test_handoff_pauses_bot_and_resolve_resumes_it(tpool):
    await seed(tpool)
    await store.request_handoff(tpool, "wa_923001234567")
    c = await store.get_customer(tpool, "wa_923001234567")
    assert c["handoff_requested"] and not c["bot_active"]
    assert [x["psid"] for x in await store.list_conversations(tpool, None, handoff_only=True)] == ["wa_923001234567"]
    assert await store.resolve_handoff(tpool, "wa_923001234567", resume_bot=True)
    c = await store.get_customer(tpool, "wa_923001234567")
    assert not c["handoff_requested"] and c["bot_active"]


async def test_real_name_is_not_overwritten_but_numeric_placeholder_is(tpool):
    await store.upsert_customer(tpool, "p1", "facebook", name="12345")
    await store.upsert_customer(tpool, "p1", "facebook", name="Bilal")
    await store.upsert_customer(tpool, "p1", "facebook", name="Someone Else")
    assert (await store.get_customer(tpool, "p1"))["name"] == "Bilal"


async def test_clearing_history_blocks_old_messages_from_coming_back(tpool):
    await seed(tpool, mid="old1")
    out = await store.clear_history(tpool, "wa_923001234567", "owner")
    assert out["deleted_count"] == 1
    assert await store.get_messages(tpool, "wa_923001234567") == []
    stale = await store.insert_message(tpool, psid="wa_923001234567", platform="whatsapp", sender="customer",
                                       text="late redelivery", mid="old2", created_at=now() - timedelta(hours=1))
    assert stale is None                                         # trigger suppresses messages older than the clear
    fresh = await store.insert_message(tpool, psid="wa_923001234567", platform="whatsapp", sender="customer",
                                       text="new", mid="new1", created_at=now() + timedelta(seconds=5))
    assert fresh is not None


async def test_deleted_message_cannot_be_resurrected_by_a_webhook_retry(tpool):
    mid_id = await seed(tpool, mid="gone")
    assert await store.delete_message(tpool, mid_id, "owner") == 1
    again = await store.insert_message(tpool, psid="wa_923001234567", platform="whatsapp", sender="customer", text="x", mid="gone")
    assert again is None
    assert await store.delete_message(tpool, mid_id, "owner") == 0   # idempotent


# --- comments ------------------------------------------------------------------------------
async def add_comment(pool, cid="c1", platform="facebook", message="Great product"):
    return await store.insert_comment(pool, platform=platform, comment_id=cid, post_id="p1", parent_id=None,
                                      from_id="u1", from_name="Sara", from_username=None, message=message)


async def test_comment_insert_is_idempotent(tpool):
    assert await add_comment(tpool) is True
    assert await add_comment(tpool) is False
    assert len(await store.list_comments(tpool, "facebook")) == 1


async def test_comment_filters_hide_and_read(tpool):
    await add_comment(tpool, "c1", message="love it")
    await add_comment(tpool, "c2", message="buy followers now")
    await store.save_comment_result(tpool, "facebook", "c2", classification="spam", topic=None, action="hide", hidden=True)
    assert [c["comment_id"] for c in await store.list_comments(tpool, "facebook", hidden=True)] == ["c2"]
    assert [c["comment_id"] for c in await store.list_comments(tpool, "facebook", classification="spam")] == ["c2"]
    assert await store.mark_comments_read(tpool, "facebook", ["c1"]) == 1
    assert [c["comment_id"] for c in await store.list_comments(tpool, "facebook", unread_only=True)] == ["c2"]
    await store.set_comment_hidden(tpool, "facebook", "c2", False)
    assert await store.list_comments(tpool, "facebook", hidden=True) == []


async def test_comments_are_separated_by_platform(tpool):
    await add_comment(tpool, "same-id", platform="facebook")
    await add_comment(tpool, "same-id", platform="instagram")
    assert len(await store.list_comments(tpool, "facebook")) == 1
    assert len(await store.list_comments(tpool, "instagram")) == 1


async def test_deleted_comment_is_tombstoned_and_never_returns(tpool):
    await add_comment(tpool, "c9")
    assert await store.delete_comment_records(tpool, "facebook", "c9", "owner") == 1
    assert await store.get_comment(tpool, "facebook", "c9") is None
    assert await add_comment(tpool, "c9") is False                # late webhook must not resurrect it
    assert await store.delete_comment_records(tpool, "facebook", "c9", "owner") == 0   # idempotent
    audit = await tpool.fetchval("select count(*) from crm.deletion_audit where action_type = 'comment_deleted'")
    assert audit == 1


async def test_delete_comment_rejects_unknown_platform(tpool):
    with pytest.raises(Exception):
        await store.delete_comment_records(tpool, "twitter", "c1", "owner")


# --- notifications and analytics -----------------------------------------------------------
async def test_notifications_unread_flow_and_paging(tpool):
    for i in range(5):
        await store.create_notification(tpool, type="handoff_requested", platform="whatsapp", source_type="message",
                                        target_id=f"t{i}", customer_name="Ayesha", message_preview="x" * 400)
    assert await store.unread_notification_count(tpool) == 5
    page = await store.list_notifications(tpool, limit=2)
    assert len(page) == 2 and page[0]["id"] > page[1]["id"]
    assert len(page[0]["message_preview"]) == 300                  # preview is capped
    older = await store.list_notifications(tpool, limit=10, before_id=page[-1]["id"])
    assert len(older) == 3
    assert await store.mark_notifications_read(tpool, [page[0]["id"]]) == 1
    assert await store.unread_notification_count(tpool) == 4
    assert await store.mark_notifications_read(tpool) == 4         # omitted ids = everything
    assert await store.list_notifications(tpool, unread_only=True) == []


async def test_analytics_counts_messages_response_time_and_handoffs(tpool):
    await seed(tpool, mid="a1")
    await store.insert_message(tpool, psid="wa_923001234567", platform="whatsapp", sender="bot", text="hi", mid="a2")
    await store.record_metric(tpool, "response_time", "whatsapp", 2.0)
    await store.record_metric(tpool, "response_time", "whatsapp", 4.0)
    await store.record_metric(tpool, "handoff", "whatsapp")
    out = await store.analytics(tpool, 7)
    assert len(out["daily"]) == 7
    assert sum(d["customer"] for d in out["daily"]) == 1 and sum(d["bot"] for d in out["daily"]) == 1
    assert out["response_time"]["samples"] == 2 and out["response_time"]["avg_seconds"] == 3.0
    assert out["handoff"]["count"] == 1 and out["handoff"]["conversations"] == 1 and out["handoff"]["rate"] == 1.0


async def test_analytics_on_an_empty_database_has_no_division_errors(tpool):
    out = await store.analytics(tpool, 30)
    assert out["handoff"]["rate"] == 0.0 and out["response_time"]["avg_seconds"] is None
    assert out["comments"] == {"by_classification": [], "by_action": []}


async def test_live_marks_change_when_something_happens(tpool):
    before = await store.activity_marks(tpool)
    await seed(tpool)
    after = await store.activity_marks(tpool)
    assert before != after and after["unread"] == 1
