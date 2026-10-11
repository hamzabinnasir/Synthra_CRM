"""Pure bot text helpers: reply cleanup, handoff and privacy detection, spam heuristics, classification parsing."""
import pytest

from app.bot import text


def test_strip_emojis_and_typographic_punctuation():
    assert text.strip_emojis("Hello \U0001F600 world") == "Hello world"
    assert text.strip_emojis("\u201cHi\u201d \u2014 it\u2019s fine") == "\"Hi\" - it's fine"
    assert text.strip_emojis("") == ""


def test_plain_text_removes_markdown():
    assert text.plain_text("## Title\n**bold** and __under__ and `code`") == "Title\nbold and under and code"
    assert text.plain_text("- item\n- other") == "- item\n- other"


def test_tidy_reply_limits_length_and_respects_emoji_flag():
    assert text.tidy_reply("a" * 50, limit=10) == "a" * 10
    assert text.tidy_reply("Hi \U0001F600") == "Hi"
    assert text.tidy_reply("Hi \U0001F600", allow_emoji=True) == "Hi \U0001F600"


@pytest.mark.parametrize("message", [
    "human", "/human", "Talk to a human", "I want to talk to a human please", "Can I speak with someone?",
    "connect me to an agent", "human support",
])
def test_handoff_requests_detected(message):
    assert text.is_human_handoff_request(message)


@pytest.mark.parametrize("message", ["", "What are your opening hours?", "human resources department", "I love this"])
def test_normal_messages_are_not_handoffs(message):
    assert not text.is_human_handoff_request(message)


def test_small_talk_only_answers_plain_greetings():
    assert text.small_talk_reply("Hi!") == "Hello! How can I help you today?"
    assert text.small_talk_reply("hello there") == "Hello! How can I help you today?"
    assert text.small_talk_reply("salam") == "Hello! How can I help you today?"
    assert text.small_talk_reply("How are you?").startswith("I'm doing well")
    assert text.small_talk_reply("Hi, what is the price?") is None
    assert text.small_talk_reply("") is None


@pytest.mark.parametrize("message", [
    "How can I pay?", "my order is late", "email me at someone@example.com", "Call 0300 1234567",
    "my address is 12 Main Street",
])
def test_private_reply_required_for_personal_or_order_details(message):
    assert text.requires_private_reply(message)


@pytest.mark.parametrize("message", ["Great product!", "Nice post", "", "Where are you located?"])
def test_public_reply_is_fine_for_ordinary_comments(message):
    assert not text.requires_private_reply(message)


def test_spam_heuristics():
    assert text.looks_like_spam("visit http://a.example and http://b.example")
    assert text.looks_like_spam("Click here to win")
    assert text.looks_like_spam("buy followers now")
    assert not text.looks_like_spam("see http://a.example for details")
    assert not text.looks_like_spam("Love this!")


def test_parse_classification_reads_model_json():
    verdict = text.parse_classification('{"tone": "Angry", "private": "yes", "severe": true, "topic": "billing"}', "hello")
    assert verdict == {"classification": "angry", "private": True, "severe": True, "topic": "billing"}


def test_parse_classification_tolerates_prose_around_json():
    assert text.parse_classification('Sure! {"tone": "spam"} done', "hello")["classification"] == "spam"


def test_parse_classification_unknown_tone_becomes_neutral():
    assert text.parse_classification('{"tone": "weird"}', "hello")["classification"] == "neutral"


def test_parse_classification_privacy_rules_override_the_model():
    assert text.parse_classification('{"private": false}', "my order is late")["private"] is True


def test_parse_classification_falls_back_to_heuristics_on_garbage():
    verdict = text.parse_classification("not json at all", "hello")
    assert verdict == {"classification": "neutral", "private": False, "severe": False, "topic": "unclassified"}
    spam = text.parse_classification("", "click here http://a.example http://b.example")
    assert spam["classification"] == "spam"


def test_postback_to_text():
    assert text.postback_to_text({"title": "Menu", "payload": "X"}) == "Menu"
    assert text.postback_to_text({"payload": "GET_STARTED"}) == "GET_STARTED"
    assert text.postback_to_text(None) == ""
