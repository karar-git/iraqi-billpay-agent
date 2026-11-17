"""List menus out, tapped rows and photos in, on both transports (graph.py and ycloud.py) and in the test kit.

Offline: every request goes to an httpx.MockTransport. Names, numbers and files are made up.
"""

from __future__ import annotations

import json
from dataclasses import asdict, replace

import httpx
import pytest

from channels.whatsapp import graph as meta
from channels.whatsapp import ycloud
from channels.whatsapp.graph import GraphClient, Inbound, SendError, list_payload
from channels.whatsapp.store import ChannelStore
from channels.whatsapp.tests import kit
from channels.whatsapp.tests.kit import ALICE, PNID
from channels.whatsapp.ycloud import YCloudClient

API_KEY = "test-ycloud-api-key"
BUSINESS = kit.YC_BUSINESS
EVIL = "https://evil.example.com/m1"
JPEG = b"\xff\xd8\xff\xe0 SPECIMEN"
PDF = b"%PDF-1.4 SPECIMEN"
ROWS = [("bill:a1", "المولدة", "الحجي جاسم، حساب 350"), ("bill:a2", "الكهرباء", ""), ("more:2", "بعد", "الصفحة الثانية")]


def meta_client(handler) -> tuple[GraphClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return GraphClient("tok", "PN1", "v25.0", transport=httpx.MockTransport(record), sleep=lambda s: None), seen


def yc_client(handler) -> tuple[YCloudClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return YCloudClient(API_KEY, BUSINESS, transport=httpx.MockTransport(record), sleep=lambda s: None), seen


def one(body: dict) -> Inbound:
    [item] = meta.parse(body, PNID)
    return item


def yc_one(body: dict) -> Inbound:
    [item] = ycloud.parse(body, BUSINESS)
    return item


# ---------------------------------------------------------------- the list payload


def test_the_documented_limits_are_the_constants():
    assert (meta.LIST_BUTTON_MAX, meta.LIST_ROWS_MAX, meta.LIST_SECTIONS_MAX) == (20, 10, 10)
    assert (meta.LIST_ROW_ID_MAX, meta.LIST_ROW_TITLE_MAX, meta.LIST_ROW_DESCRIPTION_MAX) == (200, 24, 72)
    assert (meta.LIST_SECTION_TITLE_MAX, meta.HEADER_MAX, meta.FOOTER_MAX) == (24, 60, 60)
    # the body: the lower of the two providers' caps, so one payload passes on both
    assert meta.LIST_BODY_MAX == 1024 < meta.LIST_BODY_MAX_CLOUD_API == 4096


def test_list_payload_shape():
    payload = list_payload(ALICE, True, "يا فاتورة تريدين تدفعين؟", "الفواتير", ROWS, header="فواتيرج", footer="اختاري وحدة")
    assert payload == {
        "to": ALICE, "recipient_type": "individual", "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": "فواتيرج"},
            "body": {"text": "يا فاتورة تريدين تدفعين؟"},
            "footer": {"text": "اختاري وحدة"},
            "action": {"button": "الفواتير", "sections": [{"rows": [
                {"id": "bill:a1", "title": "المولدة", "description": "الحجي جاسم، حساب 350"},
                {"id": "bill:a2", "title": "الكهرباء"},  # an empty description is left out, not sent empty
                {"id": "more:2", "title": "بعد", "description": "الصفحة الثانية"},
            ]}]},
        },
    }
    assert "چ" not in json.dumps(payload, ensure_ascii=False)


@pytest.mark.parametrize("header,footer", [(None, None), ("", "")])
def test_no_header_and_no_footer_are_left_out(header, footer):
    interactive = list_payload(ALICE, True, "نص", "افتح", ROWS, header, footer)["interactive"]
    assert set(interactive) == {"type", "body", "action"}
    assert list_payload(ALICE, True, "نص", "افتح", ROWS)["interactive"] == interactive


def test_a_username_only_account_is_addressed_by_its_user_id():
    payload = list_payload("IQ.9", False, "نص", "افتح", ROWS)
    assert payload["recipient"] == "IQ.9" and "to" not in payload


def test_a_row_may_be_a_pair_and_a_section_title_is_optional():
    interactive = list_payload(ALICE, True, "نص", "افتح", [("a", "أول"), ("b", "ثاني", None)], section="الأسماء")["interactive"]
    assert interactive["action"]["sections"] == [{"title": "الأسماء", "rows": [{"id": "a", "title": "أول"},
                                                                              {"id": "b", "title": "ثاني"}]}]


def test_every_field_is_clipped_to_its_limit():
    rows = [("r" * 300, "ع" * 40, "و" * 100), ("x", "قصير", "وصف")]
    interactive = list_payload(ALICE, True, "ب" * 5000, "ز" * 30, rows, header="ر" * 80, footer="ذ" * 80,
                               section="ق" * 30)["interactive"]
    first, second = interactive["action"]["sections"][0]["rows"]
    assert len(interactive["body"]["text"]) == 1024 and interactive["body"]["text"].endswith("…")
    assert len(interactive["action"]["button"]) == 20 and interactive["action"]["button"].endswith("…")
    assert len(interactive["header"]["text"]) == 60 and len(interactive["footer"]["text"]) == 60
    assert len(interactive["action"]["sections"][0]["title"]) == 24
    assert first["id"] == "r" * 200  # an id is cut clean: no mark that would come back in the tap
    assert len(first["title"]) == 24 and first["title"].endswith("…")
    assert len(first["description"]) == 72 and first["description"].endswith("…")
    assert second == {"id": "x", "title": "قصير", "description": "وصف"}  # what fits is not touched
    exact = list_payload(ALICE, True, "ب" * 1024, "ز" * 20, [("i" * 200, "ع" * 24, "و" * 72)])["interactive"]
    assert exact["body"]["text"] == "ب" * 1024 and exact["action"]["button"] == "ز" * 20
    assert exact["action"]["sections"][0]["rows"] == [{"id": "i" * 200, "title": "ع" * 24, "description": "و" * 72}]


def test_ten_rows_go_out_and_eleven_or_none_are_refused():
    rows = [(f"row:{n}", f"سطر {n}", "") for n in range(11)]
    assert len(list_payload(ALICE, True, "نص", "افتح", rows[:10])["interactive"]["action"]["sections"][0]["rows"]) == 10
    with pytest.raises(ValueError):
        list_payload(ALICE, True, "نص", "افتح", rows)  # the caller pages
    with pytest.raises(ValueError):
        list_payload(ALICE, True, "نص", "افتح", [])


@pytest.mark.parametrize("body,button,rows", [
    ("", "افتح", ROWS), ("  ", "افتح", ROWS), ("نص", "", ROWS), ("نص", " ", ROWS),
    ("نص", "افتح", [("", "عنوان", "")]), ("نص", "افتح", [("a", "", "")]), ("نص", "افتح", [("a", "  ", "")]),
    ("نص", "افتح", [("a", "أول", ""), ("a", "ثاني", "")]),
    ("نص", "افتح", [("r" * 200 + "1", "أول", ""), ("r" * 200 + "2", "ثاني", "")]),  # one id once they are cut
], ids=["no-body", "blank-body", "no-button", "blank-button", "no-id", "no-title", "blank-title", "same-id",
        "same-id-after-the-cut"])
def test_what_the_api_would_refuse_is_a_value_error_before_anything_is_sent(body, button, rows):
    with pytest.raises(ValueError):
        list_payload(ALICE, True, body, button, rows)
    graph, seen = meta_client(lambda r: httpx.Response(200, json={}))
    with pytest.raises(ValueError):
        graph.send_list(ALICE, True, body, button, rows)
    api, yc_seen = yc_client(lambda r: httpx.Response(200, json={}))
    with pytest.raises(ValueError):
        api.send_list(ALICE, True, body, button, rows)
    assert seen == [] and yc_seen == []


# ---------------------------------------------------------------- sending a list


def test_the_protocol_names_send_list():
    assert callable(getattr(meta.Graph, "send_list"))
    for client_class in (GraphClient, YCloudClient, kit.FakeGraph):
        assert callable(getattr(client_class, "send_list"))


def test_cloud_api_send_list():
    graph, seen = meta_client(lambda r: httpx.Response(200, json={"messages": [{"id": "wamid.1"}]}))
    graph.send_list(ALICE, True, "نص", "افتح", ROWS, header="رأس", footer="ذيل")
    graph.send_list("IQ.9", False, "نص", "افتح", ROWS[:1])
    first, second = (json.loads(r.content) for r in seen)
    assert str(seen[0].url) == "https://graph.facebook.com/v25.0/PN1/messages"
    assert first == {"messaging_product": "whatsapp", **list_payload(ALICE, True, "نص", "افتح", ROWS, "رأس", "ذيل")}
    assert second["recipient"] == "IQ.9" and "to" not in second
    assert second["interactive"]["action"] == {"button": "افتح", "sections": [{"rows": [
        {"id": "bill:a1", "title": "المولدة", "description": "الحجي جاسم، حساب 350"}]}]}


def test_second_provider_send_list():
    api, seen = yc_client(lambda r: httpx.Response(200, json={"id": "o", "status": "accepted"}))
    api.send_list(ALICE, True, "نص", "افتح", ROWS, header="رأس", footer="ذيل")
    api.send_list("IQ.9", False, "نص", "افتح", ROWS[:1], section="قسم")
    first, second = (json.loads(r.content) for r in seen)
    assert str(seen[0].url) == "https://api.ycloud.com/v2/whatsapp/messages/sendDirectly"
    assert seen[0].headers["X-API-Key"] == API_KEY
    assert first == {"from": BUSINESS, "to": f"+{ALICE}", "type": "interactive",
                     "interactive": list_payload(ALICE, True, "نص", "افتح", ROWS, "رأس", "ذيل")["interactive"]}
    assert second["recipient"] == "IQ.9" and "to" not in second and second["from"] == BUSINESS
    assert second["interactive"]["action"]["sections"][0]["title"] == "قسم"
    assert len(first["interactive"]["body"]["text"]) <= 1024  # that provider's cap for an interactive body


@pytest.mark.parametrize("build", [meta_client, yc_client], ids=["cloud-api", "second-provider"])
def test_a_list_is_retried_on_a_rate_limit_and_never_on_a_5xx(build):
    replies = iter([httpx.Response(429, text="slow down"), httpx.Response(200, json={"status": "accepted"})])
    client, seen = build(lambda r: next(replies))
    client.send_list(ALICE, True, "نص", "افتح", ROWS)
    assert len(seen) == 2
    client, seen = build(lambda r: httpx.Response(500, text="boom"))
    with pytest.raises(SendError):
        client.send_list(ALICE, True, "نص", "افتح", ROWS)
    assert len(seen) == 1  # it may have been delivered: never the same menu twice


# ---------------------------------------------------------------- a tapped row


def test_a_tapped_row_is_a_button_with_the_row_id():
    clock = kit.Clock()
    item = one(kit.webhook(kit.list_tap(ALICE, "bill:a1", "المولدة", clock, "الحجي جاسم", msg_id="wamid.row")))
    assert (item.kind, item.button_id, item.text, item.msg_id) == ("button", "bill:a1", "المولدة", "wamid.row")
    assert item.sender == ALICE and item.has_phone and item.media_id == "" and item.caption == ""
    tapped = one(kit.webhook(kit.tap(ALICE, "bill:a1", "المولدة", clock, msg_id="wamid.row")))
    assert item == tapped  # exactly like a reply button


def test_a_tapped_row_parses_the_same_on_both_providers():
    clock = kit.Clock()
    expected = one(kit.webhook(kit.list_tap(ALICE, "c:c1", "هادي", clock, "ينتهي بـ 2222", msg_id="wamid.a")))
    assert ycloud.parse(kit.yc_list_tap(ALICE, "c:c1", "هادي", clock, "ينتهي بـ 2222", wamid="wamid.a"), BUSINESS) == [expected]
    assert kit.YC_INBOUND_EVENT == ycloud.INBOUND_EVENT


@pytest.mark.parametrize("interactive", [
    {"type": "list_reply"},
    {"type": "list_reply", "list_reply": {}},
    {"type": "list_reply", "list_reply": {"title": "عنوان"}},
    {"type": "list_reply", "button_reply": {"id": "confirm:q1", "title": "إي، ادفع"}},  # the type decides, not a stray key
])
def test_a_row_without_an_id_is_a_tap_on_nothing(interactive):
    clock = kit.Clock()
    message = kit.list_tap(ALICE, "x", "x", clock)
    message["interactive"] = interactive
    item = one(kit.webhook(message))
    assert item.kind == "button" and item.button_id == ""


def test_other_interactive_replies_stay_what_they_were():
    clock = kit.Clock()
    message = kit.list_tap(ALICE, "x", "x", clock)
    message["interactive"] = {"type": "nfm_reply", "nfm_reply": {"name": "flow", "response_json": "{}"}}
    item = one(kit.webhook(message))
    assert (item.kind, item.text, item.button_id) == ("text", "", "")


# ---------------------------------------------------------------- a photo


def test_a_photo_is_an_image_with_its_media_id_mime_and_caption():
    clock = kit.Clock()
    item = one(kit.webhook(kit.image(ALICE, "img1", clock, caption="هويتي", msg_id="wamid.img")))
    assert (item.kind, item.media_id, item.mime, item.caption) == ("image", "img1", "image/jpeg", "هويتي")
    assert item.text == "" and item.button_id == ""  # a caption is never a typed message
    bare = one(kit.webhook(kit.image(ALICE, "img2", clock, mime="image/png")))
    assert (bare.kind, bare.media_id, bare.mime, bare.caption) == ("image", "img2", "image/png", "")


@pytest.mark.parametrize("mime,kind", [
    ("application/pdf", "image"), ("image/png", "image"), ("IMAGE/JPEG", "image"), ("image/heic; x=1", "image"),
    ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "document"),
    ("text/plain", "document"), ("application/pdfx", "document"), ("", "document"),
])
def test_a_file_that_is_a_picture_or_a_pdf_is_an_image_and_any_other_file_is_not(mime, kind):
    clock = kit.Clock()
    item = one(kit.webhook(kit.document(ALICE, "doc1", clock, mime=mime, caption="فاتورة")))
    assert item.kind == kind
    if kind == "image":
        assert (item.media_id, item.mime, item.caption) == ("doc1", mime, "فاتورة")
    else:
        assert (item.media_id, item.mime, item.caption) == ("", "", "")


def test_a_malformed_or_bare_image_is_still_an_image_with_nothing_to_fetch():
    clock = kit.Clock()
    for image in ("not an object", None, {}, {"id": None}):
        message = kit.image(ALICE, "x", clock)
        message["image"] = image
        item = one(kit.webhook(message))
        assert (item.kind, item.media_id, item.mime, item.caption) == ("image", "", "", "")
    assert one(kit.webhook(kit.other(ALICE, "image", clock))).media_id == "i1"  # the old helper still works
    assert [one(kit.webhook(kit.other(ALICE, k, clock))).kind for k in ("sticker", "location", "video")] == [
        "sticker", "location", "video"]


def test_a_long_caption_is_cut():
    item = one(kit.webhook(kit.image(ALICE, "img1", kit.Clock(), caption="ك" * 5000)))
    assert len(item.caption) == meta.CAPTION_MAX


def test_is_image_mime():
    assert all(meta.is_image_mime(m) for m in ("image/jpeg", "image/webp", "application/pdf", " Application/PDF ; q=1"))
    assert not any(meta.is_image_mime(m) for m in ("", "audio/ogg", "video/mp4", "application/zip", "imagejpeg", None))


def test_an_image_survives_the_inbox_and_an_old_row_without_a_caption_still_loads(tmp_path):
    clock = kit.Clock()
    item = one(kit.webhook(kit.image(ALICE, "img1", clock, caption="هويتي")))
    store = ChannelStore(tmp_path / "channel.db")
    assert store.first_time(item.msg_id, item.timestamp, asdict(item))
    [raw] = store.pending(0)
    assert Inbound(**raw) == item
    old = {k: v for k, v in asdict(item).items() if k != "caption"}  # a row written before this field existed
    assert Inbound(**old) == replace(item, caption="")


def test_a_photo_parses_the_same_on_both_providers_but_for_its_handle():
    clock = kit.Clock()
    cases = [
        (kit.image(ALICE, "m1", clock, caption="هويتي", msg_id="wamid.a"),
         kit.yc_image(ALICE, "m1", clock, caption="هويتي", wamid="wamid.a")),
        (kit.image(ALICE, "m1", clock, mime="image/png", msg_id="wamid.b"),
         kit.yc_image(ALICE, "m1", clock, mime="image/png", wamid="wamid.b")),
        (kit.document(ALICE, "m1", clock, caption="فاتورة", msg_id="wamid.c"),
         kit.yc_document(ALICE, "m1", clock, caption="فاتورة", wamid="wamid.c")),
    ]
    for theirs, ours in cases:
        expected = one(kit.webhook(theirs))
        assert expected.kind == "image" and expected.media_id == "m1"
        # on the second provider the handle is the download link, as for a voice note
        assert yc_one(ours) == replace(expected, media_id=kit.yc_media_link("m1"))
    other = yc_one(kit.yc_document(ALICE, "m1", clock, mime="text/plain"))
    assert (other.kind, other.media_id) == ("document", "")


@pytest.mark.parametrize("link", [{"a": 1}, ["x"], 5, None, ""])
def test_a_link_that_is_not_text_is_nothing_to_fetch(link):
    body = kit.yc_image(ALICE, "m1", kit.Clock())
    body["whatsappInboundMessage"]["image"]["link"] = link
    item = yc_one(body)
    assert item.kind == "image" and item.media_id == ""


@pytest.mark.parametrize("image", ["not an object", ["x"], 5, None])
def test_a_malformed_image_on_the_second_provider_is_never_an_error(image):
    body = kit.yc_image(ALICE, "m1", kit.Clock())
    body["whatsappInboundMessage"]["image"] = image
    item = yc_one(body)
    assert (item.kind, item.media_id, item.mime, item.caption) == ("image", "", "", "")


def test_a_voice_note_on_the_second_provider_is_read_as_before():
    body = kit.yc_event(ALICE, kit.Clock(), type="audio", audio={
        "link": kit.yc_media_link("v1"), "id": "v1", "sha256": "x", "mime_type": "audio/ogg; codecs=opus"})
    item = yc_one(body)
    assert (item.kind, item.media_id, item.mime) == ("audio", kit.yc_media_link("v1"), "audio/ogg; codecs=opus")


# ---------------------------------------------------------------- downloading a photo


def test_cloud_api_downloads_a_photo_from_meta_with_its_mime():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "graph.facebook.com" and request.url.path.endswith("/img1"):
            return httpx.Response(200, json={"url": "https://lookaside.fbsbx.com/whatsapp/img1",
                                             "mime_type": "image/jpeg", "file_size": len(JPEG)})
        if request.url.host == "graph.facebook.com" and request.url.path.endswith("/img2"):
            return httpx.Response(200, json={"url": "https://lookaside.fbsbx.com/whatsapp/img2"})
        if request.url.host == "graph.facebook.com":
            return httpx.Response(200, json={"url": EVIL, "mime_type": "image/jpeg"})
        return httpx.Response(200, content=JPEG)

    graph, seen = meta_client(handler)
    assert graph.media("img1") == (JPEG, "image/jpeg")
    assert graph.media("img2") == (JPEG, "")  # no mime in the lookup: the caller goes by the webhook's, never a guess
    with pytest.raises(SendError):
        graph.media("img3")
    assert not any(r.url.host == "evil.example.com" for r in seen)


@pytest.mark.parametrize("content,sent,given", [
    (JPEG, "image/jpeg", "image/jpeg"), (PDF, "application/pdf", "application/pdf"),
    (JPEG, "application/octet-stream", ""), (JPEG, "text/html", ""),
])
def test_second_provider_downloads_a_photo_from_its_link_with_the_key(content, sent, given):
    api, seen = yc_client(lambda r: httpx.Response(200, content=content, headers={"content-type": sent}))
    item = yc_one(kit.yc_image(ALICE, "m1", kit.Clock()))
    assert api.media(item.media_id) == (content, given)
    assert str(seen[0].url) == kit.yc_media_link("m1") and seen[0].headers["X-API-Key"] == API_KEY


def test_a_forged_photo_link_is_never_fetched():
    api, seen = yc_client(lambda r: httpx.Response(200, content=JPEG))
    for forged in (EVIL, "https://api.ycloud.com/v2/webhookEndpoints", "m1"):
        item = yc_one(kit.yc_image(ALICE, "m1", kit.Clock(), link=forged))
        assert item.kind == "image" and item.media_id == forged
        with pytest.raises(SendError):
            api.media(item.media_id)
    assert seen == []  # the key went nowhere


def test_a_photo_over_the_size_cap_is_refused(monkeypatch):
    monkeypatch.setattr(ycloud, "MAX_INBOUND_MEDIA", 8)
    monkeypatch.setattr(meta, "MAX_INBOUND_MEDIA", 8)
    api, _ = yc_client(lambda r: httpx.Response(200, content=JPEG))
    with pytest.raises(SendError):
        api.media(kit.yc_media_link("m1"))
    graph, seen = meta_client(lambda r: httpx.Response(200, json={
        "url": "https://lookaside.fbsbx.com/whatsapp/img1", "mime_type": "image/jpeg", "file_size": 9}))
    with pytest.raises(SendError):
        graph.media("img1")
    assert len(seen) == 1  # told by the lookup: the file itself was not asked for


# ---------------------------------------------------------------- the test kit


def test_the_fake_graph_records_lists_next_to_cards():
    fake = kit.FakeGraph()
    fake.send_buttons(ALICE, True, "بطاقة", [("confirm:q1", "إي، ادفع")])
    fake.send_list(ALICE, True, "يا فاتورة؟", "الفواتير", ROWS, header="فواتيرج", footer="اختاري")
    fake.send_text(ALICE, True, "هلا")
    assert fake.kinds(ALICE) == ["interactive", "interactive", "text"]
    assert fake.to(ALICE)[1] == list_payload(ALICE, True, "يا فاتورة؟", "الفواتير", ROWS, "فواتيرج", "اختاري")
    assert len(fake.cards(ALICE)) == 1 and kit.card_buttons(fake.last_card(ALICE)) == {"إي، ادفع": "confirm:q1"}
    assert fake.lists(ALICE) == [fake.last_list(ALICE)]
    assert kit.list_rows(fake.last_list(ALICE)) == {"المولدة": "bill:a1", "الكهرباء": "bill:a2", "بعد": "more:2"}
    assert fake.everything(ALICE) == "بطاقة\nيا فاتورة؟\nهلا"
    with pytest.raises(ValueError):  # the same refusals as the real clients, and nothing recorded
        fake.send_list(ALICE, True, "نص", "افتح", [(f"r{n}", "سطر", "") for n in range(11)])
    assert len(fake.to(ALICE)) == 3
    with pytest.raises(AssertionError):
        kit.FakeGraph().last_list(ALICE)


def test_the_fake_graph_serves_a_photo_by_its_media_id():
    fake = kit.FakeGraph()
    fake.media_files["img1"] = (JPEG, "image/jpeg")
    item = one(kit.webhook(kit.image(ALICE, "img1", kit.Clock())))
    assert fake.media(item.media_id) == (JPEG, item.mime)


def test_kit_messages_have_their_own_ids_and_the_old_helpers_are_unchanged():
    clock = kit.Clock()
    made = [kit.list_tap(ALICE, "a", "b", clock), kit.image(ALICE, "i", clock), kit.document(ALICE, "d", clock)]
    assert len({m["id"] for m in made}) == 3 and all(m["from"] == ALICE for m in made)
    events = [kit.yc_list_tap(ALICE, "a", "b", clock), kit.yc_image(ALICE, "i", clock), kit.yc_document(ALICE, "d", clock)]
    assert len({e["whatsappInboundMessage"]["wamid"] for e in events}) == 3
    assert all(e["whatsappInboundMessage"]["from"] == f"+{ALICE}" and e["whatsappInboundMessage"]["to"] == BUSINESS
               for e in events)
    nameless = kit.yc_event(None, clock, type="text", text={"body": "هلا"})
    assert yc_one(nameless).sender == "IQ.77" and not yc_one(nameless).has_phone
    assert kit.tap(ALICE, "confirm:q1", "إي، ادفع", clock)["interactive"]["type"] == "button_reply"
    assert kit.voice(ALICE, "m1", clock)["audio"] == {"id": "m1", "mime_type": "audio/ogg; codecs=opus", "voice": True}
    assert kit.other(ALICE, "image", clock)["image"] == {"id": "i1"}
