"""The Instagram webhook: the handshake, the signature, and every shape `parse` meets (offline, no network)."""

from __future__ import annotations

import json

import pytest

from channels.instagram.graph import challenge, parse, sign, signature_ok

OWN = "17841400000000001"  # our professional account (synthetic)
USER = "1200000000000042"  # an Instagram-scoped user id (synthetic)
CDN = "https://lookaside.fbsbx.com/ig_messaging_cdn/?asset_id=1&signature=abc"


def body(*events: dict, entry_id: str = OWN) -> dict:
    return {"object": "instagram", "entry": [{"id": entry_id, "time": 1569262486134, "messaging": list(events)}]}


def event(**what) -> dict:
    return {"sender": {"id": USER}, "recipient": {"id": OWN}, "timestamp": 1569262485349, **what}


# ---------------------------------------------------------------- handshake and signature


def test_handshake_answers_the_challenge_only_for_our_token():
    assert challenge("subscribe", "verify-me", "1158201444", "verify-me") == "1158201444"
    assert challenge("subscribe", "other", "1158201444", "verify-me") is None
    assert challenge("unsubscribe", "verify-me", "1158201444", "verify-me") is None
    assert challenge("subscribe", "", "1158201444", "") is None  # no token configured: nothing passes
    assert challenge("subscribe", None, "1", "verify-me") is None
    assert challenge("subscribe", "verify-me", None, "verify-me") is None
    assert challenge("subscribe", "ڤerify", "1", "verify-me") is None  # non-ASCII is a mismatch, not an error


def test_signature_is_hmac_sha256_of_the_raw_body_with_the_app_secret():
    raw = json.dumps(body(event(message={"mid": "m1", "text": "هلا"})), ensure_ascii=False).encode()
    header = sign(raw, "app-secret")
    assert header.startswith("sha256=") and len(header) == 7 + 64
    assert signature_ok(raw, header, "app-secret")
    assert signature_ok(raw, header.upper().replace("SHA256=", "sha256="), "app-secret")  # hex in either case
    assert not signature_ok(raw + b" ", header, "app-secret")
    assert not signature_ok(raw, sign(raw, "another"), "app-secret")
    assert not signature_ok(raw, header, "")
    assert not signature_ok(raw, None, "app-secret")
    assert not signature_ok(raw, header[len("sha256="):], "app-secret")  # the prefix is required
    assert not signature_ok(raw, "sha1=" + header[7:], "app-secret")
    assert not signature_ok(raw, "sha256=ééé", "app-secret")  # a forged non-ASCII header is a mismatch


# ---------------------------------------------------------------- what a person sent


def test_text_message():
    [item] = parse(body(event(message={"mid": "m1", "text": "ادفع المولدة"})), OWN)
    assert (item.msg_id, item.sender, item.has_phone, item.kind, item.text) == ("m1", USER, False, "text",
                                                                               "ادفع المولدة")
    assert item.timestamp == pytest.approx(1569262485.349)  # the samples are milliseconds
    assert item.name is None and item.media_id == "" and item.button_id == ""


def test_timestamp_in_seconds_is_read_as_seconds_and_rubbish_as_none():
    [item] = parse(body(event(message={"mid": "m1", "text": "x"}, timestamp=1569262485)), OWN)
    assert item.timestamp == 1569262485.0
    for bad in ("soon", None, True, -5, float("nan"), [1]):
        [item] = parse(body(event(message={"mid": "m1", "text": "x"}, timestamp=bad)), OWN)
        assert item.timestamp == 0.0


def test_quick_reply_is_a_button_with_its_payload():
    [item] = parse(body(event(message={"mid": "m2", "text": "أحمد علي", "quick_reply": {"payload": "pick:c7"}})), OWN)
    assert (item.kind, item.button_id, item.text, item.msg_id) == ("button", "pick:c7", "أحمد علي", "m2")


def test_typed_title_of_a_button_is_only_text():
    # no payload, no button: typing «إي، ادفع» can never count as the tap
    [item] = parse(body(event(message={"mid": "m3", "text": "إي، ادفع"})), OWN)
    assert item.kind == "text" and item.button_id == ""
    [item] = parse(body(event(message={"mid": "m3", "text": "إي، ادفع", "quick_reply": {"payload": ""}})), OWN)
    assert item.kind == "text"


def test_postback_is_a_button_with_its_payload():
    [item] = parse(body(event(postback={"mid": "m4", "title": "إي، ادفع", "payload": "confirm:q_123"})), OWN)
    assert (item.kind, item.button_id, item.text, item.msg_id, item.has_phone) == (
        "button", "confirm:q_123", "إي، ادفع", "m4", False)


def test_postback_without_a_message_id_gets_a_stable_one():
    tap = event(postback={"title": "إي، ادفع", "payload": "confirm:q_123"})
    [first], [again] = parse(body(tap), OWN), parse(body(tap), OWN)
    assert first.msg_id == again.msg_id and first.msg_id.startswith("postback:")  # Meta's retry is taken once
    [other] = parse(body(event(postback={"payload": "cancel:q_123"})), OWN)
    assert other.msg_id != first.msg_id


def test_audio_attachment_carries_its_link():
    [item] = parse(body(event(message={"mid": "m5", "attachments": [{"type": "audio", "payload": {"url": CDN}}]})),
                   OWN)
    assert (item.kind, item.media_id, item.msg_id, item.text) == ("audio", CDN, "m5", "")


def test_image_attachment_carries_its_link_and_the_words_sent_with_it():
    [item] = parse(body(event(message={"mid": "m6", "text": "هويتي",
                                       "attachments": [{"type": "image", "payload": {"url": CDN}}]})), OWN)
    assert (item.kind, item.media_id) == ("image", CDN)
    assert "هويتي" in (item.text, getattr(item, "caption", ""))


def test_several_attachments_each_get_their_own_id():
    items = parse(body(event(message={"mid": "m7", "attachments": [
        {"type": "image", "payload": {"url": CDN + "1"}}, {"type": "video", "payload": {"url": CDN + "v"}},
        {"type": "image", "payload": {"url": CDN + "2"}}]})), OWN)
    assert [(i.kind, i.msg_id, i.media_id) for i in items] == [("image", "m7", CDN + "1"), ("image", "m7#2", CDN + "2")]


@pytest.mark.parametrize("kind, expected", [("video", "video"), ("file", "document"), ("share", "other"),
                                            ("ig_post", "other"), ("ig_reel", "other"), ("reel", "other"),
                                            ("story_mention", "other"), ("ephemeral", "other"), ("", "other")])
def test_other_attachments_are_named_but_never_downloaded(kind, expected):
    [item] = parse(body(event(message={"mid": "m8", "attachments": [{"type": kind, "payload": {"url": CDN}}]})), OWN)
    assert item.kind == expected and item.media_id == ""


def test_words_beside_a_shared_post_or_a_story_reply_are_text():
    [item] = parse(body(event(message={"mid": "m9", "text": "شكد رصيدي",
                                       "attachments": [{"type": "share", "payload": {"url": CDN}}]})), OWN)
    assert (item.kind, item.text) == ("text", "شكد رصيدي")
    [item] = parse(body(event(message={"mid": "m10", "text": "دز لأحمد خمسين",
                                       "reply_to": {"story": {"url": CDN, "id": "s1"}}})), OWN)
    assert (item.kind, item.text) == ("text", "دز لأحمد خمسين")


def test_unsupported_and_empty_messages_are_other():
    [item] = parse(body(event(message={"mid": "m11", "is_unsupported": True})), OWN)
    assert item.kind == "other"
    [item] = parse(body(event(message={"mid": "m12"})), OWN)
    assert item.kind == "other"


# ---------------------------------------------------------------- what is dropped


def test_echoes_and_our_own_messages_are_dropped():
    echo = {"sender": {"id": OWN}, "recipient": {"id": USER}, "timestamp": 1569262485349,
            "message": {"mid": "e1", "text": "تم", "is_echo": True}}
    assert parse(body(echo), OWN) == []
    assert parse(body(echo), "") == []  # the flag alone is enough
    no_flag = {**echo, "message": {"mid": "e2", "text": "تم"}}
    assert parse(body(no_flag), OWN) == []  # from our own id, whatever the flags say
    assert parse(body(event(message={"mid": "e3", "text": "test", "is_self": True})), OWN) == []


def test_reads_reactions_edits_deletes_and_referrals_are_dropped():
    assert parse(body(event(read={"mid": "m1"})), OWN) == []
    assert parse(body(event(reaction={"mid": "m1", "action": "react", "reaction": "love", "emoji": "❤️"})), OWN) == []
    assert parse(body(event(reaction={"mid": "m1", "action": "unreact"})), OWN) == []
    assert parse(body(event(message_edit={"mid": "m1", "text": "دز لأحمد مية", "num_edit": 1})), OWN) == []
    assert parse(body(event(message={"mid": "m1", "is_deleted": True})), OWN) == []
    assert parse(body(event(referral={"ref": "ad", "source": "ADS", "type": "OPEN_THREAD"})), OWN) == []
    assert parse(body(event(message={"admin_text": "handed over", "handoff_reason": "human_requested"})), OWN) == []


def test_messages_for_another_account_and_standby_are_dropped():
    other = {"sender": {"id": USER}, "recipient": {"id": "17841400000000999"}, "timestamp": 1,
             "message": {"mid": "x1", "text": "هلا"}}
    assert parse(body(other), OWN) == []
    assert len(parse(body(other), "")) == 1  # no own id configured: nothing to compare with
    standby = {"object": "instagram", "entry": [{"id": OWN, "standby": [event(message={"mid": "s1", "text": "هلا"})]}]}
    assert parse(standby, OWN) == []


def test_other_objects_are_not_ours():
    page = {"object": "page", "entry": [{"id": "1", "messaging": [event(message={"mid": "p1", "text": "hi"})]}]}
    assert parse(page, OWN) == []
    whatsapp = {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {}}]}]}
    assert parse(whatsapp, OWN) == []


def test_the_changes_envelope_is_read_too():
    wrapped = {"object": "instagram", "entry": [{"id": OWN, "time": 1, "changes": [
        {"field": "messages", "value": event(message={"mid": "c1", "text": "شكد رصيدي"})},
        {"field": "messaging_postbacks", "value": event(postback={"mid": "c2", "payload": "cancel:q1"})},
        {"field": "comments", "value": {"from": {"id": USER}, "text": "nice"}},
        {"field": "messaging_seen", "value": event(read={"mid": "c1"})}]}]}
    assert [(i.kind, i.msg_id) for i in parse(wrapped, OWN)] == [("text", "c1"), ("button", "c2")]


def test_a_batch_keeps_its_order_and_one_bad_event_does_not_lose_the_rest():
    batch = body(event(message={"mid": "b1", "text": "واحد"}), "not an event", {"sender": "nobody"},
                 event(message={"mid": "b2", "text": "اثنين"}))
    assert [i.text for i in parse(batch, OWN)] == ["واحد", "اثنين"]


@pytest.mark.parametrize("junk", [
    None, [], "text", 7, {}, {"object": "instagram"}, {"object": "instagram", "entry": None},
    {"object": "instagram", "entry": "x"}, {"object": "instagram", "entry": [None, 3, "x", []]},
    {"object": "instagram", "entry": [{"messaging": "x", "changes": 5}]},
    {"object": "instagram", "entry": [{"messaging": [None, 1, [], {}]}]},
    {"object": "instagram", "entry": [{"messaging": [{"sender": {"id": None}, "message": {"mid": "m", "text": "x"}}]}]},
    {"object": "instagram", "entry": [{"messaging": [{"sender": {"id": {"a": 1}}, "message": {"mid": "m"}}]}]},
    {"object": "instagram", "entry": [{"messaging": [{"sender": {"id": "1"}, "message": "hello"}]}]},
    {"object": "instagram", "entry": [{"messaging": [{"sender": {"id": "1"}, "message": {"mid": None, "text": 5}}]}]},
    {"object": "instagram", "entry": [{"messaging": [{"sender": {"id": "1"}, "postback": []}]}]},
    {"object": "instagram", "entry": [{"changes": [{"field": "messages", "value": None}, None, {"field": []}]}]},
])
def test_unknown_shapes_never_raise(junk):
    assert parse(junk, OWN) == []


def test_odd_fields_inside_a_real_message_never_raise():
    odd = event(message={"mid": "o1", "text": ["not", "text"], "attachments": [None, 5, {"type": 9, "payload": "x"},
                                                                              {"type": "image", "payload": None}],
                         "quick_reply": "nope"})
    [item] = parse(body(odd), OWN)
    assert (item.kind, item.media_id, item.text) == ("image", "", "")
    [item] = parse(body({"sender": {"id": 1200000000000042}, "recipient": {"id": int(OWN)}, "timestamp": "1569262485349",
                         "message": {"mid": "o2", "text": "هلا"}}), OWN)
    assert (item.sender, item.kind) == (USER, "text")  # ids sent as numbers are still ids
