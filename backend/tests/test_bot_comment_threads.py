"""Which comments inside a thread does the bot answer? (BotEngine._nested_reply_wanted)

Database-free: the Meta client is replaced by a small fake that serves one comment thread.
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.bot import parsing
from app.bot.engine import MAX_BOT_REPLIES_PER_THREAD, BotEngine, Ctx
from app.channels.registry import CHANNELS
from app.jobs.queue import MAX_ATTEMPTS
from app.platforms.meta import MetaError

PAGE = "100000000000001"
ALICE = "200000000000001"
BOB = "200000000000002"


class FakeClient:
    def __init__(self, *, top_author=ALICE, thread=(), own_name=None, tags_us=False, error=None):
        self.top_author, self.thread, self.own_name, self.tags_us, self.error = top_author, list(thread), own_name, tags_us, error

    async def own_username(self):
        return self.own_name

    async def comment_tags_us(self, comment_id):
        if self.error:
            raise self.error
        return self.tags_us

    async def comment_author(self, comment_id):
        return {"id": self.top_author, "username": None}

    async def thread_replies(self, parent_id):
        return [{"id": cid, "author_id": author, "username": None} for cid, author in self.thread]


def ctx_for(client, channel="facebook_comments", account=PAGE, attempts=1):
    ch = CHANNELS[channel]
    return Ctx(tenant_id=uuid.uuid4(), channel=ch, creds={"values": {ch.account_field: account}, "behaviour": {}},
               pool=None, client=client, attempts=attempts)


def comment(cid, from_id, message="thanks, and the price?", platform="facebook", username=None):
    return parsing.ParsedComment(platform=platform, comment_id=cid, post_id="post1", parent_id="top1", from_id=from_id,
                                 from_name=None, from_username=username, message=message,
                                 created_at=datetime.now(timezone.utc))


async def wanted(client, parsed, **kw):
    return await BotEngine._nested_reply_wanted(None, ctx_for(client, **kw), parsed)


@pytest.mark.asyncio
async def test_answers_customer_replying_to_our_reply():
    client = FakeClient(thread=[("r1", PAGE), ("r2", ALICE)])           # alice wrote top1, we answered, alice answers back
    assert await wanted(client, comment("r2", ALICE)) is True


@pytest.mark.asyncio
async def test_ignores_chatter_between_customers():
    client = FakeClient(thread=[("r1", PAGE), ("r2", ALICE), ("r3", BOB)])   # bob answers alice, not us
    assert await wanted(client, comment("r3", BOB)) is False


@pytest.mark.asyncio
async def test_ignores_newcomer_even_if_our_reply_is_just_before():
    client = FakeClient(thread=[("r1", PAGE), ("r2", BOB)])             # bob was never part of the conversation
    assert await wanted(client, comment("r2", BOB)) is False


@pytest.mark.asyncio
async def test_answers_when_an_earlier_reply_was_from_the_commenter():
    client = FakeClient(top_author=BOB, thread=[("r1", ALICE), ("r2", PAGE), ("r3", ALICE)])
    assert await wanted(client, comment("r3", ALICE)) is True


@pytest.mark.asyncio
async def test_answers_when_top_comment_is_ours():
    client = FakeClient(top_author=PAGE, thread=[("r1", ALICE)])
    assert await wanted(client, comment("r1", ALICE)) is True


@pytest.mark.asyncio
async def test_answers_facebook_tag_of_our_page():
    client = FakeClient(thread=[("r1", BOB)], tags_us=True)
    assert await wanted(client, comment("r1", BOB, "@Shop what is the price?")) is True


@pytest.mark.asyncio
async def test_answers_instagram_mention_in_text():
    client = FakeClient(own_name="my.shop", thread=[("r1", BOB)])
    parsed = comment("r1", BOB, "hey @My.Shop do you ship abroad?", platform="instagram")
    assert await wanted(client, parsed, channel="instagram_comments", account="17841400000000001") is True


@pytest.mark.asyncio
async def test_instagram_mention_must_be_the_whole_handle():
    client = FakeClient(own_name="my.shop", thread=[("r1", BOB)])
    parsed = comment("r1", BOB, "ask @my.shop.fake instead", platform="instagram")
    assert await wanted(client, parsed, channel="instagram_comments", account="17841400000000001") is False


@pytest.mark.asyncio
async def test_stops_after_reply_cap():
    thread = [(f"b{i}", PAGE) for i in range(MAX_BOT_REPLIES_PER_THREAD)] + [("last", ALICE)]
    assert await wanted(FakeClient(top_author=PAGE, thread=thread), comment("last", ALICE)) is False


@pytest.mark.asyncio
async def test_retryable_platform_error_is_raised_for_the_queue():
    client = FakeClient(error=MetaError(0, network=True))
    with pytest.raises(MetaError):
        await wanted(client, comment("r1", ALICE), attempts=1)


@pytest.mark.asyncio
async def test_platform_error_on_last_attempt_means_no_reply():
    client = FakeClient(error=MetaError(0, network=True))
    assert await wanted(client, comment("r1", ALICE), attempts=MAX_ATTEMPTS) is False
