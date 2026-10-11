"""Webhook payload parsing: WhatsApp, Messenger/Instagram messages and comments become neutral objects."""
from datetime import datetime, timezone

from app.bot import parsing


def _wa(**message):
    base = {"from": "923001234567", "id": "wamid.1", "type": "text", "timestamp": "1700000000", "text": {"body": " hi "}}
    base.update(message)
    return {"message": base, "contacts": [{"wa_id": "923001234567", "profile": {"name": "Ali"}}]}


def test_to_datetime_handles_seconds_milliseconds_and_junk():
    assert parsing.to_datetime(1700000000).year == 2023
    assert parsing.to_datetime(1700000000000) == parsing.to_datetime(1700000000)
    junk = parsing.to_datetime("not a number")
    assert junk.tzinfo is not None and abs((datetime.now(timezone.utc) - junk).total_seconds()) < 5


def test_whatsapp_text_message():
    inbound = parsing.parse_whatsapp(_wa())
    assert (inbound.platform, inbound.external_id, inbound.text, inbound.mid) == ("whatsapp", "923001234567", "hi", "wamid.1")
    assert inbound.name == "Ali" and inbound.username == "923001234567" and inbound.media is None


def test_whatsapp_image_uses_caption_or_placeholder():
    with_caption = parsing.parse_whatsapp(_wa(type="image", image={"id": "m1", "mime_type": "image/jpeg", "caption": "Look"}))
    assert with_caption.text == "Look" and with_caption.media.type == "image" and with_caption.media.media_id == "m1"
    without = parsing.parse_whatsapp(_wa(type="image", image={"id": "m2", "mime_type": "image/jpeg"}))
    assert without.text == "[Image]"


def test_whatsapp_document_interactive_and_location():
    doc = parsing.parse_whatsapp(_wa(type="document", document={"id": "d1", "filename": "invoice.pdf", "mime_type": "application/pdf"}))
    assert doc.media.type == "document" and doc.text == "invoice.pdf"
    button = parsing.parse_whatsapp(_wa(type="interactive", interactive={"button_reply": {"title": "Yes"}}))
    assert button.text == "Yes"
    assert parsing.parse_whatsapp(_wa(type="location")).text == "[Location]"


def test_whatsapp_rejects_bad_or_unsupported_messages():
    assert parsing.parse_whatsapp(_wa(**{"from": "abc"})) is None
    assert parsing.parse_whatsapp(_wa(id="")) is None
    assert parsing.parse_whatsapp(_wa(type="reaction")) is None
    assert parsing.parse_whatsapp(_wa(text={"body": ""})) is None
    assert parsing.parse_whatsapp({}) is None


def _messaging(**item):
    base = {"sender": {"id": "111"}, "recipient": {"id": "999"}, "timestamp": 1700000000000,
            "message": {"mid": "m1", "text": "hello"}}
    base.update(item)
    return {"messaging": base}


def test_messenger_customer_message():
    inbound = parsing.parse_messaging("facebook", "999", _messaging())
    assert (inbound.external_id, inbound.text, inbound.mid, inbound.is_echo) == ("111", "hello", "m1", False)


def test_messenger_echo_belongs_to_the_recipient_conversation():
    echo = parsing.parse_messaging("facebook", "999", _messaging(
        sender={"id": "999"}, recipient={"id": "111"}, message={"mid": "m2", "text": "Agent reply", "is_echo": True}))
    assert echo.is_echo and echo.external_id == "111" and echo.name is None


def test_instagram_echo_detected_when_sender_is_the_business_account():
    echo = parsing.parse_messaging("instagram", "999", _messaging(sender={"id": "999"}, recipient={"id": "111"}))
    assert echo.is_echo and echo.external_id == "111"


def test_messenger_attachment_and_postback():
    image = parsing.parse_messaging("facebook", "999", _messaging(
        message={"mid": "m3", "attachments": [{"type": "image", "payload": {"url": "https://cdn.example/a.jpg"}}]}))
    assert image.media.type == "image" and image.media.url == "https://cdn.example/a.jpg" and image.text == "[Image]"
    postback = parsing.parse_messaging("facebook", "999", {"messaging": {
        "sender": {"id": "111"}, "recipient": {"id": "999"}, "postback": {"title": "Start", "payload": "S", "mid": "pb1"}}})
    assert postback.text == "Start" and postback.mid == "pb1"


def test_messenger_ignores_empty_or_senderless_events():
    assert parsing.parse_messaging("facebook", "999", _messaging(message={"mid": "m4"})) is None
    assert parsing.parse_messaging("facebook", "999", {"messaging": {"message": {"mid": "m5", "text": "x"}}}) is None
    assert parsing.parse_messaging("facebook", "999", {}) is None


def test_facebook_comment():
    change = {"change": {"value": {"item": "comment", "verb": "add", "comment_id": "c1", "post_id": "p1", "parent_id": "p1",
                                   "from": {"id": "5", "name": "Sara"}, "message": "Nice", "created_time": 1700000000}}}
    comment = parsing.parse_comment("facebook", change)
    assert (comment.comment_id, comment.post_id, comment.from_name, comment.message) == ("c1", "p1", "Sara", "Nice")


def test_facebook_comment_ignores_removals_and_other_items():
    removed = {"change": {"value": {"item": "comment", "verb": "remove", "comment_id": "c1"}}}
    reaction = {"change": {"value": {"item": "reaction", "comment_id": "c1"}}}
    assert parsing.parse_comment("facebook", removed) is None
    assert parsing.parse_comment("facebook", reaction) is None


def test_instagram_comment():
    change = {"change": {"value": {"id": "ig1", "text": "hi", "from": {"id": "7", "username": "bob"}, "media": {"id": "m9"}}}}
    comment = parsing.parse_comment("instagram", change)
    assert (comment.comment_id, comment.post_id, comment.from_username, comment.message) == ("ig1", "m9", "bob", "hi")
    assert parsing.parse_comment("instagram", {"change": {"value": {}}}) is None


def test_removal_and_unsend_helpers():
    assert parsing.comment_id_from_removal("facebook", {"change": {"value": {"comment_id": "c1"}}}) == "c1"
    assert parsing.comment_id_from_removal("instagram", {"change": {"value": {"id": "ig1"}}}) == "ig1"
    assert parsing.comment_id_from_removal("facebook", {}) is None
    assert parsing.unsent_mid({"messaging": {"message": {"mid": "m1"}}}) == "m1"
    assert parsing.unsent_mid({}) is None
