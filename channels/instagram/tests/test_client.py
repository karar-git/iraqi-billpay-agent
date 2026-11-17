"""The Instagram client over a fake transport: each payload against the documented shape and limits, the token's
path, rate-limit retries and the media host allow-list. Offline."""

from __future__ import annotations

import json

import httpx
import pytest

from channels.instagram.client import (
    BUTTON_TITLE_MAX,
    PAYLOAD_MAX,
    QUICK_REPLIES_MAX,
    QUICK_REPLY_TITLE_MAX,
    TEMPLATE_TEXT_MAX,
    TEXT_MAX_BYTES,
    ApiError,
    InstagramClient,
    NotSupported,
    action_payload,
    api_base,
    api_version,
    buttons_payloads,
    is_cdn,
    list_payloads,
    split_text,
    text_payloads,
    utf8_len,
)
from channels.whatsapp.graph import Inbound, SendError

TOKEN = "IGAA-test-token-not-a-secret"
OWN = "17841400000000001"
USER = "1200000000000042"
CDN = "https://lookaside.fbsbx.com/ig_messaging_cdn/?asset_id=1&signature=abc"
OK = {"recipient_id": USER, "message_id": "mid.1"}


def client(handler=None, **options) -> tuple[InstagramClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return (handler or (lambda r: httpx.Response(200, json=OK)))(request)

    made = InstagramClient(TOKEN, OWN, transport=httpx.MockTransport(record), sleep=lambda s: None, **options)
    return made, seen


def sent(seen: list[httpx.Request]) -> list[dict]:
    return [json.loads(r.content) for r in seen]


# ---------------------------------------------------------------- the documented limits, as constants


def test_limits_are_the_documented_ones():
    assert (TEXT_MAX_BYTES, QUICK_REPLIES_MAX, QUICK_REPLY_TITLE_MAX) == (1000, 13, 20)
    assert (TEMPLATE_TEXT_MAX, BUTTON_TITLE_MAX, PAYLOAD_MAX) == (640, 20, 1000)


# ---------------------------------------------------------------- where it goes, and with what


def test_send_text_shape_endpoint_and_token_in_the_header_only():
    graph, seen = client()
    graph.send_text(USER, False, "هلا بيك")
    [request] = seen
    assert request.method == "POST"
    assert str(request.url) == f"https://graph.instagram.com/v27.0/{OWN}/messages"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["Content-Type"] == "application/json"
    assert TOKEN not in str(request.url) and TOKEN not in request.content.decode()
    assert sent(seen) == [{"recipient": {"id": USER}, "message": {"text": "هلا بيك"}}]


def test_facebook_login_host_and_version_are_configurable():
    graph, seen = client(base="https://graph.facebook.com/", version="v25.0", page_id="100000000000007")
    graph.send_text(USER, False, "hello")
    assert str(seen[0].url) == "https://graph.facebook.com/v25.0/100000000000007/messages"  # from the linked Page
    assert seen[0].headers["Authorization"] == f"Bearer {TOKEN}"
    assert sent(seen) == [{"messaging_type": "RESPONSE", "recipient": {"id": USER}, "message": {"text": "hello"}}]
    graph.mark_read("m1", to=USER)  # a sender action carries nothing else
    assert sent(seen)[1] == {"recipient": {"id": USER}, "sender_action": "mark_seen"}
    graph, seen = client(base="https://graph.facebook.com", page_id="me")  # a Page token's /me is the Page
    graph.send_text(USER, False, "hello")
    assert str(seen[0].url) == "https://graph.facebook.com/v27.0/me/messages"
    graph, seen = client(base="https://graph.facebook.com")  # no Page: from the Instagram account id
    graph.send_text(USER, False, "hello")
    assert str(seen[0].url) == f"https://graph.facebook.com/v27.0/{OWN}/messages"
    with pytest.raises(ValueError):
        client(base="https://graph.facebook.com", page_id="../x")


@pytest.mark.parametrize("bad", ["http://graph.instagram.com", "https://graph.instagram.com.evil.example",
                                 "https://evil.example/graph.instagram.com", "https://user@graph.facebook.com",
                                 "https://graph.facebook.com:8443", "https://graph.facebook.com/v1/x",
                                 "https://api.example.com", "graph.instagram.com", "https://graph.instagram.com?x=1"])
def test_the_token_can_only_be_pointed_at_the_two_graph_hosts(bad):
    with pytest.raises(ValueError):
        api_base(bad)
    with pytest.raises(ValueError):
        InstagramClient(TOKEN, OWN, base=bad, transport=httpx.MockTransport(lambda r: httpx.Response(200)))


def test_base_and_version_defaults_and_shapes():
    assert api_base("") == api_base(None) == "https://graph.instagram.com"
    assert api_base(" https://graph.facebook.com/ ") == "https://graph.facebook.com"
    assert api_version("") == "v27.0" and api_version("v25.0") == "v25.0"
    for bad in ("27", "v27", "v27.0/../x", "latest"):
        with pytest.raises(ValueError):
            api_version(bad)
    with pytest.raises(ValueError):
        InstagramClient(TOKEN, "123/../456", transport=httpx.MockTransport(lambda r: httpx.Response(200)))


def test_nothing_is_sent_without_an_account_id_or_a_token():
    calls: list[httpx.Request] = []
    transport = httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200, json=OK))
    with pytest.raises(SendError):
        InstagramClient(TOKEN, "", transport=transport).send_text(USER, False, "x")
    with pytest.raises(SendError):
        InstagramClient("", OWN, transport=transport).send_text(USER, False, "x")
    assert calls == []


# ---------------------------------------------------------------- text and its 1000 bytes


def test_a_long_arabic_text_is_split_under_1000_bytes_and_nothing_is_lost():
    lines = [f"السطر {n}: دفعت فاتورة المولدة والمبلغ خمسين ألف دينار" for n in range(60)]
    text = "\n".join(lines)
    assert utf8_len(text) > 3 * TEXT_MAX_BYTES
    graph, seen = client()
    graph.send_text(USER, False, text)
    parts = [p["message"]["text"] for p in sent(seen)]
    assert len(parts) >= 4 and all(utf8_len(p) <= TEXT_MAX_BYTES for p in parts)
    assert "\n".join(parts) == text  # cut only at line ends, in order
    assert all(set(p) == {"recipient", "message"} and p["recipient"] == {"id": USER} for p in sent(seen))


def test_split_prefers_lines_then_spaces_then_letters_and_never_breaks_a_letter():
    assert split_text("") == [] and split_text("   \n ") == []
    assert split_text("قصير") == ["قصير"]
    words = " ".join(["كلمة"] * 400)  # no line ends: cut at spaces
    parts = split_text(words)
    assert all(utf8_len(p) <= 1000 for p in parts) and " ".join(parts) == words
    solid = "ب" * 1700  # no space at all: cut between letters, each still whole
    parts = split_text(solid)
    assert "".join(parts) == solid and [utf8_len(p) for p in parts] == [1000, 1000, 1000, 400]
    emoji = "😀" * 600  # four bytes each
    parts = split_text(emoji)
    assert "".join(parts) == emoji and all(utf8_len(p) <= 1000 and utf8_len(p) % 4 == 0 for p in parts)
    marked = "ا" + "بَ" * 400  # the 1000th byte falls between a letter and its vowel mark: they stay together
    parts = split_text(marked)
    assert "".join(parts) == marked and all(not p.startswith("َ") and utf8_len(p) <= 1000 for p in parts)
    assert split_text("x" * 1000) == ["x" * 1000] and len(split_text("x" * 1001)) == 2
    with pytest.raises(ValueError):
        split_text("x", limit=2)


def test_an_empty_text_sends_nothing_and_a_runaway_text_is_capped():
    graph, seen = client()
    graph.send_text(USER, False, "  ")
    assert seen == []
    payloads = text_payloads(USER, "ا" * 50_000)
    assert sum(len(p["message"]["text"]) for p in payloads) == 4096 and payloads[-1]["message"]["text"].endswith("…")


# ---------------------------------------------------------------- buttons: a button template


def test_send_buttons_is_a_button_template_with_postback_buttons():
    graph, seen = client()
    graph.send_buttons(USER, False, "تدز 50,000 دينار إلى أحمد علي", [("confirm:q1", "إي، ادفع"), ("change:q1", "غيّر"),
                                                                    ("cancel:q1", "لا، بطّلت")],
                       header="تأكيد تحويل", footer="تنتهي 14:05 بتوقيت بغداد")
    assert sent(seen) == [{"recipient": {"id": USER}, "message": {"attachment": {"type": "template", "payload": {
        "template_type": "button",
        "text": "تأكيد تحويل\n\nتدز 50,000 دينار إلى أحمد علي\n\nتنتهي 14:05 بتوقيت بغداد",
        "buttons": [{"type": "postback", "title": "إي، ادفع", "payload": "confirm:q1"},
                    {"type": "postback", "title": "غيّر", "payload": "change:q1"},
                    {"type": "postback", "title": "لا، بطّلت", "payload": "cancel:q1"}]}}}}]


def test_button_limits_count_titles_and_payloads():
    for count in (0, 4):
        with pytest.raises(ValueError):
            buttons_payloads(USER, "x", [("a", "b")] * count)
    [payload] = buttons_payloads(USER, "x", [("id:1", "ع" * 30)])
    title = payload["message"]["attachment"]["payload"]["buttons"][0]["title"]
    assert len(title) == 20 and title.endswith("…")
    [payload] = buttons_payloads(USER, "x", [("p" * 1000, "ok")])
    assert payload["message"]["attachment"]["payload"]["buttons"][0]["payload"] == "p" * 1000
    for bad in ("p" * 1001, ""):  # an id is never cut: half an id names nothing
        with pytest.raises(ValueError):
            buttons_payloads(USER, "x", [(bad, "ok")])
    with pytest.raises(ValueError):
        buttons_payloads(USER, "  ", [("a", "b")])


def test_a_card_over_640_characters_keeps_its_buttons_under_the_last_part():
    card = "\n".join(f"سطر {n} من البطاقة وبيه تفاصيل الدفعة كاملة" for n in range(40))
    assert len(card) > 2 * TEMPLATE_TEXT_MAX
    payloads = buttons_payloads(USER, card, [("confirm:q9", "إي، ادفع")], header="تأكيد", footer="تنتهي 14:05")
    *lead, last = payloads
    assert lead and all(set(p["message"]) == {"text"} and utf8_len(p["message"]["text"]) <= 1000 for p in lead)
    template = last["message"]["attachment"]["payload"]
    assert len(template["text"]) <= TEMPLATE_TEXT_MAX and template["text"].endswith("تنتهي 14:05")
    assert template["buttons"] == [{"type": "postback", "title": "إي، ادفع", "payload": "confirm:q9"}]
    whole = "\n".join([p["message"]["text"] for p in lead] + [template["text"]])
    assert whole.replace("\n", "") == ("تأكيد\n\n" + card + "\n\nتنتهي 14:05").replace("\n", "")  # nothing lost
    exactly = buttons_payloads(USER, "ا" * 640, [("a", "b")])
    assert len(exactly) == 1 and len(exactly[0]["message"]["attachment"]["payload"]["text"]) == 640


# ---------------------------------------------------------------- lists: quick replies


def test_send_list_is_a_text_with_quick_replies_and_the_descriptions_in_the_text():
    graph, seen = client()
    rows = [("pick:c1", "أحمد علي", "الرقم ينتهي بـ 1234"), ("pick:c2", "أحمد حسين", "الرقم ينتهي بـ 9876"),
            ("pick:none", "ولا واحد", "")]
    graph.send_list(USER, False, "ياهو منهم؟", "اختار", rows, header="أكثر من أحمد", footer="اضغط على الاسم")
    assert sent(seen) == [{"recipient": {"id": USER}, "message": {
        "text": "أكثر من أحمد\n\nياهو منهم؟\n\nأحمد علي: الرقم ينتهي بـ 1234\nأحمد حسين: الرقم ينتهي بـ 9876\n\n"
                "اضغط على الاسم",
        "quick_replies": [{"content_type": "text", "title": "أحمد علي", "payload": "pick:c1"},
                          {"content_type": "text", "title": "أحمد حسين", "payload": "pick:c2"},
                          {"content_type": "text", "title": "ولا واحد", "payload": "pick:none"}]}}]
    assert "اختار" not in seen[0].content.decode()  # WhatsApp's "open the list" label has no place here


def test_list_limits_count_titles_and_payloads():
    full = [(f"id:{n}", f"خيار {n}", "") for n in range(13)]
    [payload] = list_payloads(USER, "اختار", full)
    assert len(payload["message"]["quick_replies"]) == 13
    for rows in ([], full + [("id:13", "خيار 13", "")]):  # never a silently dropped choice
        with pytest.raises(ValueError):
            list_payloads(USER, "اختار", rows)
    long_title = "فاتورة كهرباء بيت الوالد بالكرادة"
    [payload] = list_payloads(USER, "ياهي؟", [("bill:1", long_title, ""), ("bill:2", "المولدة", "")])
    reply = payload["message"]["quick_replies"][0]
    assert len(reply["title"]) == 20 and reply["title"].endswith("…") and reply["payload"] == "bill:1"
    assert long_title in payload["message"]["text"]  # the chip is cut, the text has it whole
    assert "المولدة" not in payload["message"]["text"]  # a short title needs no second copy
    with pytest.raises(ValueError):
        list_payloads(USER, "x", [("r" * 1001, "t", "")])
    with pytest.raises(ValueError):
        list_payloads(USER, "", [("", "t", "")])


def test_a_list_over_1000_bytes_keeps_its_quick_replies_on_the_last_part():
    rows = [(f"id:{n}", f"حساب {n}", "وصف طويل لهذا الحساب حتى يطول النص ويعبر الحد المسموح " * 2) for n in range(10)]
    *lead, last = list_payloads(USER, "اختار حساب", rows)
    assert lead and all("quick_replies" not in p["message"] for p in lead)
    assert len(last["message"]["quick_replies"]) == 10
    assert all(utf8_len(p["message"]["text"]) <= 1000 for p in [*lead, last])


# ---------------------------------------------------------------- voice: not on Instagram


def test_voice_replies_are_refused_clearly_and_nothing_is_sent():
    graph, seen = client()
    assert graph.voice_replies is False
    with pytest.raises(NotSupported) as error:
        graph.upload_media(b"OggS....", "audio/ogg; codecs=opus")
    assert isinstance(error.value, SendError) and "voice" in str(error.value)
    with pytest.raises(NotSupported):
        graph.send_audio(USER, False, "media-id")
    assert seen == []


# ---------------------------------------------------------------- seen and typing


def test_mark_read_marks_the_conversation_seen_then_types():
    graph, seen = client()
    graph.remember([Inbound(msg_id="m1", sender=USER, has_phone=False, kind="text", text="هلا")])
    graph.mark_read("m1")
    assert sent(seen) == [{"recipient": {"id": USER}, "sender_action": "mark_seen"},
                          {"recipient": {"id": USER}, "sender_action": "typing_on"}]
    graph.mark_read("unknown-message")  # nobody to mark: nothing is sent, nothing is raised
    assert len(seen) == 2
    graph.typing(USER, on=False)
    assert sent(seen)[2] == action_payload(USER, "typing_off")
    with pytest.raises(ValueError):
        action_payload(USER, "react")


def test_mark_read_never_raises_and_is_not_retried():
    graph, seen = client(lambda r: httpx.Response(429, json={"error": {"code": 613}}))
    graph.mark_read("m1", to=USER)
    assert len(seen) == 1

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    graph, _ = client(boom)
    graph.mark_read("m1", to=USER)
    graph.typing(USER)


def test_remember_is_bounded():
    graph, _ = client()
    graph.remember(Inbound(msg_id=f"m{n}", sender=USER, has_phone=False, kind="text") for n in range(5200))
    assert len(graph._senders) == 5000 and "m0" not in graph._senders and "m5199" in graph._senders


# ---------------------------------------------------------------- errors and retries


def test_rate_limits_are_retried_other_errors_are_not():
    replies = iter([httpx.Response(429, text="slow down"),
                    httpx.Response(400, json={"error": {"message": "rate", "code": 613}}),
                    httpx.Response(200, json=OK)])
    graph, seen = client(lambda r: next(replies))
    graph.send_text(USER, False, "x")
    assert len(seen) == 3
    graph, seen = client(lambda r: httpx.Response(429, json={"error": {"code": 613}}))
    with pytest.raises(ApiError):
        graph.send_text(USER, False, "x")
    assert len(seen) == 3  # gave up
    graph, seen = client(lambda r: httpx.Response(500, text="boom"))
    with pytest.raises(SendError):
        graph.send_text(USER, False, "x")
    assert len(seen) == 1  # a 5xx may have been delivered: never sent twice


def test_an_api_error_carries_numbers_and_never_the_token():
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {
            "message": f"(#10) outside the allowed window, token {TOKEN} " + "x" * 500,
            "type": "OAuthException", "code": 10, "error_subcode": 2534022, "fbtrace_id": "A1"}})

    graph, _ = client(refuse)
    with pytest.raises(ApiError) as error:
        graph.send_text(USER, False, "x")
    assert (error.value.status, error.value.code, error.value.subcode) == (400, 10, 2534022)
    assert TOKEN not in str(error.value) and TOKEN not in repr(error.value) and len(str(error.value)) < 260
    assert isinstance(error.value, SendError)
    graph, _ = client(lambda r: httpx.Response(401, text="<html>not json</html>"))
    with pytest.raises(ApiError) as error:
        graph.send_text(USER, False, "x")
    assert (error.value.status, error.value.code) == (401, None)


def test_a_network_failure_is_a_send_error_without_the_request():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    graph, seen = client(boom)
    with pytest.raises(SendError) as error:
        graph.send_text(USER, False, "x")
    assert len(seen) == 1 and str(error.value) == "network: ReadTimeout"
    assert error.value.__cause__ is None and error.value.__suppress_context__  # no header rides along
    assert TOKEN not in repr(graph) and TOKEN not in repr(graph.client.headers) and TOKEN not in repr(vars(graph))


def test_a_2xx_with_an_odd_body_is_still_sent():
    graph, seen = client(lambda r: httpx.Response(200, text="ok"))
    graph.send_text(USER, False, "x")
    assert len(seen) == 1


def test_a_redirect_is_never_followed_with_the_token():
    graph, seen = client(lambda r: httpx.Response(302, headers={"location": "https://evil.example/collect"}))
    with pytest.raises(ApiError) as error:  # a 3xx is not "sent", and nothing follows it
        graph.send_text(USER, False, "x")
    assert error.value.status == 302 and [r.url.host for r in seen] == ["graph.instagram.com"]


# ---------------------------------------------------------------- media: only Meta's CDN, never with the token


def test_media_is_downloaded_from_the_cdn_without_the_token():
    graph, seen = client(lambda r: httpx.Response(200, content=b"\x00\x00\x00\x18ftypM4A ",
                                                  headers={"content-type": "audio/mp4; codecs=mp4a"}))
    assert graph.media(CDN) == (b"\x00\x00\x00\x18ftypM4A ", "audio/mp4")
    [request] = seen
    assert request.method == "GET" and str(request.url) == CDN and "authorization" not in request.headers
    graph, _ = client(lambda r: httpx.Response(200, content=b"\xff\xd8\xff"))
    assert graph.media(CDN) == (b"\xff\xd8\xff", "")  # no type named: the channel goes by what it knows


@pytest.mark.parametrize("url, ok", [
    (CDN, True),
    ("https://scontent.xx.fbcdn.net/v/t1/photo.jpg?sig=1", True),
    ("https://scontent-fra3-1.cdninstagram.com/v/photo.jpg", True),
    ("https://fbsbx.com/x", True),
    ("http://lookaside.fbsbx.com/ig_messaging_cdn/?asset_id=1", False),  # not https
    ("https://lookaside.fbsbx.com:8443/x", False),
    ("https://user:pw@lookaside.fbsbx.com/x", False),
    ("https://lookaside.fbsbx.com.evil.example/x", False),
    ("https://evilfbsbx.com/x", False),
    ("https://evil.example/lookaside.fbsbx.com/x", False),
    ("https://graph.instagram.com/v27.0/me", False),  # the API host is not a media host
    ("https://graph.facebook.com/123", False),
    ("https://127.0.0.1/x", False),
    ("https://localhost/x", False),
    ("file:///etc/passwd", False),
    ("ftp://lookaside.fbsbx.com/x", False),
    ("//lookaside.fbsbx.com/x", False),
    ("media-id-123", False),
    ("", False),
])
def test_media_host_allow_list(url, ok):
    assert is_cdn(url) is ok
    graph, seen = client(lambda r: httpx.Response(200, content=b"data"))
    if ok:
        assert graph.media(url)[0] == b"data"
    else:
        with pytest.raises(SendError) as error:
            graph.media(url)
        assert seen == [] and "evil" not in str(error.value)  # nothing was asked, and the link is not echoed


def test_media_redirects_are_followed_only_inside_the_allow_list():
    def hop(request: httpx.Request) -> httpx.Response:
        if request.url.host == "lookaside.fbsbx.com":
            return httpx.Response(302, headers={"location": "https://scontent.xx.fbcdn.net/v/audio.m4a"})
        return httpx.Response(200, content=b"audio", headers={"content-type": "audio/mp4"})

    graph, seen = client(hop)
    assert graph.media(CDN) == (b"audio", "audio/mp4")
    assert [r.url.host for r in seen] == ["lookaside.fbsbx.com", "scontent.xx.fbcdn.net"]
    assert all("authorization" not in r.headers for r in seen)

    graph, seen = client(lambda r: httpx.Response(302, headers={"location": "https://evil.example/steal"}))
    with pytest.raises(SendError):
        graph.media(CDN)
    assert [r.url.host for r in seen] == ["lookaside.fbsbx.com"]  # the hop outside was never made

    graph, seen = client(lambda r: httpx.Response(302, headers={"location": "http://169.254.169.254/latest"}))
    with pytest.raises(SendError):
        graph.media(CDN)
    assert len(seen) == 1

    graph, seen = client(lambda r: httpx.Response(302, headers={"location": CDN}))  # a loop ends
    with pytest.raises(SendError):
        graph.media(CDN)
    assert len(seen) == 4


def test_media_is_capped_in_size_and_errors_never_show_the_link():
    graph, _ = client(lambda r: httpx.Response(200, content=b"x" * 2001), max_media=2000)
    with pytest.raises(SendError) as error:
        graph.media(CDN)
    assert "cap" in str(error.value) and "signature" not in str(error.value)
    graph, _ = client(lambda r: httpx.Response(200, content=b"x" * 2000), max_media=2000)
    assert len(graph.media(CDN)[0]) == 2000
    graph, _ = client(lambda r: httpx.Response(404, text="gone"))
    with pytest.raises(SendError) as error:
        graph.media(CDN)
    assert str(error.value) == "media download 404"

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    graph, _ = client(boom)
    with pytest.raises(SendError) as error:
        graph.media(CDN)
    assert "signature" not in str(error.value) and error.value.__cause__ is None
