"""The channel through YCloud (WHATSAPP_PROVIDER=ycloud), end to end over a fake transport: webhook signature, event
filtering, one account per person on both providers, payload shapes, retries, media only from YCloud, voice fallback.

Offline: every request goes to an httpx.MockTransport. Names and numbers are made up.
"""

from __future__ import annotations

import itertools
import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from channels.whatsapp import graph as meta
from channels.whatsapp import ycloud
from channels.whatsapp.app import create_app
from channels.whatsapp.config import Settings
from channels.whatsapp.graph import GraphClient, SendError
from channels.whatsapp.tests import kit
from channels.whatsapp.tests.kit import ALICE, BOB, PIN, PNID, SECRET, VERIFY
from channels.whatsapp.tests.test_language_and_voice import OGG, Speaking
from channels.whatsapp.tests.test_whatsapp_channel import Harness as MetaHarness
from channels.whatsapp.ycloud import YCloudClient
from core.stub import StubCore

API_KEY = "test-ycloud-api-key"
WH_SECRET = "whsec_test-endpoint-secret"
BUSINESS = "+9647700009999"
CAROL = "9647700000003"  # no account yet
GOOD_PIN = "2583"
SALT = "test-sender-salt"
LINK = "https://api.ycloud.com/v2/whatsapp/media/download/m1?sig=abc&payload=def"
EVIL = "https://evil.example.com/m1"

_ids = itertools.count(1)


def iso(clock) -> str:
    return clock().isoformat().replace("+00:00", "Z")


def event(sender: str | None, clock, *, wamid: str | None = None, event_id: str | None = None, to: str = BUSINESS,
          sent: str | None = None, **fields) -> dict:
    n = next(_ids)
    message = {"id": f"yc{n}", "wamid": wamid or f"wamid.yc{n}", "wabaId": "waba1", "fromUserId": "IQ.77",
               "customerProfile": {"name": "tester"}, "to": to, "sendTime": sent or iso(clock), **fields}
    if sender is not None:
        message["from"] = sender
    return {"id": event_id or f"evt{n}", "type": ycloud.INBOUND_EVENT, "apiVersion": "v2", "createTime": iso(clock),
            "whatsappInboundMessage": message}


def plus(who: str) -> str:
    return f"+{who}"


def text(who: str, body: str, clock, **kw) -> dict:
    return event(plus(who), clock, type="text", text={"body": body}, **kw)


def tap(who: str, button_id: str, title: str, clock, **kw) -> dict:
    return event(plus(who), clock, type="interactive",
                 interactive={"type": "button_reply", "button_reply": {"id": button_id, "title": title}}, **kw)


def voice(who: str, link: str, clock, **kw) -> dict:
    return event(plus(who), clock, type="audio", audio={"link": link, "id": "m1", "sha256": "x",
                                                        "mime_type": "audio/ogg; codecs=opus"}, **kw)


class Net:
    """The fake YCloud: answers what the client asks and remembers every request."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.sends: list[dict] = []
        self.upload_status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/whatsapp/messages/sendDirectly"):
            self.sends.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "out1", "wabaId": "waba1", "from": BUSINESS, "status": "accepted"})
        if path.endswith("/typing"):
            return httpx.Response(200)  # no body
        if path.endswith("/upload"):
            return httpx.Response(self.upload_status, json={"id": "media-1"})
        if "/media/download/" in path:
            return httpx.Response(200, content=b"OggS-in", headers={"content-type": "application/octet-stream"})
        return httpx.Response(404, json={"error": {"status": 404, "code": "NOT_FOUND"}})

    def to(self, who: str) -> list[dict]:
        """What went to `who`, without the «شلون تأكدت؟» message under a card (kit.is_why); `sends` has it."""
        return [s for s in self.sends if s.get("to") == plus(who)
                and not (s.get("type") == "interactive" and kit.is_why(s["interactive"]))]

    def texts(self, who: str) -> list[str]:
        return [s["text"]["body"] for s in self.to(who) if s["type"] == "text"]

    def cards(self, who: str) -> list[dict]:
        return [s["interactive"] for s in self.to(who) if s["type"] == "interactive"]

    def audios(self, who: str) -> list[dict]:
        return [s["audio"] for s in self.to(who) if s["type"] == "audio"]

    def uploads(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith("/upload")]

    def hosts(self) -> set[str]:
        return {r.url.host for r in self.requests}


class Harness:
    def __init__(self, tmp_path, *, core=None, clock=None, client_class=YCloudClient, **overrides):
        self.net = Net()
        self.clock = clock or kit.Clock()
        base = dict(provider="ycloud", ycloud_api_key=API_KEY, ycloud_webhook_secret=WH_SECRET, ycloud_from=BUSINESS,
                    sender_salt=SALT, data_dir=tmp_path, debounce_seconds=0.0, daily_budget_usd=0.0)
        self.settings = Settings(**{**base, **overrides})
        self.graph = client_class(API_KEY, BUSINESS, transport=httpx.MockTransport(self.net), sleep=lambda s: None)
        self.app = create_app(self.settings, graph=self.graph, core=core or StubCore(), seed=kit.seed(), clock=self.clock)
        self._cm = TestClient(self.app)
        self.client = self._cm.__enter__()
        self.channel = self.app.state.channel

    def stop(self) -> None:
        self._cm.__exit__(None, None, None)
        self.graph.close()

    def post(self, body: dict, *, secret: str = WH_SECRET, stamp: int | None = None, headers: dict | None = None):
        raw = json.dumps(body, ensure_ascii=False).encode()
        if headers is None:
            when = int(self.clock().timestamp()) if stamp is None else stamp
            headers = {"YCloud-Signature": ycloud.sign(raw, secret, when)}
        return self.client.post("/webhook", content=raw, headers={"Content-Type": "application/json", **headers})

    def send(self, body: dict):
        response = self.post(body)
        assert response.status_code == 200
        assert self.channel.join(20)
        return response

    def say(self, who: str, body: str) -> None:
        self.send(text(who, body, self.clock))

    def tap(self, who: str, title: str) -> None:
        card = self.net.cards(who)[-1]
        self.send(tap(who, kit.card_buttons(card)[title], title, self.clock))

    def tap_id(self, who: str, button_id: str) -> None:
        self.send(tap(who, button_id, "x", self.clock))

    def uid(self, who: str) -> str:
        return self.channel.accounts.by_phone(who)["id"]

    def wallet(self, who: str):
        return self.channel._runtime(self.channel.sender_key(who), self.uid(who)).wallet

    def balance(self, who: str) -> int:
        return self.wallet(who).user(self.uid(who))["balance"]

    def transactions(self, who: str) -> list[dict]:
        return self.wallet(who).transactions(self.uid(who))


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path)
    yield harness
    harness.stop()


def client(handler) -> tuple[YCloudClient, list[httpx.Request], list[float]]:
    seen: list[httpx.Request] = []
    slept: list[float] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return YCloudClient(API_KEY, BUSINESS, transport=httpx.MockTransport(record), sleep=slept.append), seen, slept


# ---------------------------------------------------------------- the whole conversation


def test_full_payment_through_the_signed_webhook_is_recorded_once(h):
    h.say(ALICE, "ادفع المولدة")
    card = h.net.cards(ALICE)[-1]
    assert "45,000" in card["body"]["text"]
    assert list(kit.card_buttons(card)) == ["إي، ادفع", "غيّر", "لا، بطّلت"]
    h.tap(ALICE, "إي، ادفع")
    assert "رمز" in h.net.texts(ALICE)[-1]
    pin = text(ALICE, PIN, h.clock)
    h.send(pin)
    h.send(pin)  # YCloud's retry of the PIN message
    before = h.balance(ALICE)
    assert before == 455_000
    assert [t["status"] for t in h.transactions(ALICE)] == ["succeeded"]
    assert sum("45,000" in t for t in h.net.texts(ALICE)) == 1  # one receipt
    assert all("چ" not in t for t in h.net.texts(ALICE))

    # every send went from our number, to the person's, with the key, to YCloud alone
    assert h.net.hosts() == {"api.ycloud.com"}
    assert all(r.headers["X-API-Key"] == API_KEY and "Authorization" not in r.headers for r in h.net.requests)
    for body in h.net.sends:
        assert body["from"] == BUSINESS and body["to"] == plus(ALICE)
        assert "messaging_product" not in body and "recipient_type" not in body


def test_sign_up_through_ycloud_lands_on_the_number_in_digits(h):
    h.say(CAROL, "هلا")
    h.tap_id(CAROL, "o:open")
    h.tap_id(CAROL, "o:name_yes")
    h.tap_id(CAROL, "o:f")
    h.say(CAROL, GOOD_PIN)
    h.say(CAROL, GOOD_PIN)
    user = h.channel.accounts.by_phone(CAROL)
    assert user["phone_e164"] == CAROL and user["name"] == "tester"
    assert h.channel.accounts.by_phone(CAROL)["id"] == user["id"]
    assert h.net.texts(CAROL)[-1].startswith("تم فتح حسابج")
    assert h.channel.store.onboarding(h.channel.sender_key(CAROL)) is None


def test_a_notice_to_another_account_goes_out_with_a_plus(h):
    h.say(BOB, "هلا")  # inside his window
    before = len(h.net.to(BOB))
    h.say(ALICE, "دز 25 الف لـ 0770 000 0002")
    h.tap(ALICE, "إي، ادفع")
    h.say(ALICE, PIN)
    assert h.balance(BOB) == 325_000
    assert len(h.net.to(BOB)) == before + 1 and "25,000" in h.net.texts(BOB)[-1]


# ---------------------------------------------------------------- one person, one account


def test_the_same_message_parses_the_same_on_both_providers(h):
    clock = h.clock
    cases = [
        (kit.text(ALICE, "هلا", clock, msg_id="wamid.a"), text(ALICE, "هلا", clock, wamid="wamid.a")),
        (kit.tap(ALICE, "confirm:q1", "إي، ادفع", clock, msg_id="wamid.b"),
         tap(ALICE, "confirm:q1", "إي، ادفع", clock, wamid="wamid.b")),
    ]
    for theirs, ours in cases:
        [expected] = meta.parse(kit.webhook(theirs), PNID)
        expected.name = "tester"
        assert ycloud.parse(ours, BUSINESS) == [expected]


@pytest.mark.parametrize("written", [ALICE, plus(ALICE), "+964 770 000 0001"])
def test_a_sender_in_any_form_is_digits(h, written):
    [item] = ycloud.parse(event(written, h.clock, type="text", text={"body": "هلا"}), BUSINESS)
    assert item.sender == ALICE and item.has_phone
    assert h.channel.sender_key(item.sender) == h.channel.sender_key(ALICE)


def test_sender_key_is_the_same_on_both_providers():
    for salt, secret in ((SALT, SECRET), ("", SECRET)):
        on_meta = Settings(sender_salt=salt, app_secret=secret)
        on_ycloud = Settings(sender_salt=salt, app_secret=secret, provider="ycloud", ycloud_webhook_secret=WH_SECRET)
        assert on_meta.sender_secret == on_ycloud.sender_secret == (salt or secret)
    assert Settings(provider="ycloud", ycloud_webhook_secret=WH_SECRET).sender_secret == WH_SECRET  # never a plain hash
    assert Settings(provider="ycloud").service_number == Settings().service_number == "9647714899059"
    env = {"WHATSAPP_PROVIDER": "ycloud", "SERVICE_NUMBER": "+964 770 000 1111"}
    assert Settings.from_env(env).service_number == "9647700001111"


def test_a_sign_up_begun_on_meta_finishes_on_ycloud_and_a_seen_wamid_is_not_answered_again(tmp_path):
    before = MetaHarness(tmp_path, sender_salt=SALT)
    try:
        before.say(CAROL, "هلا")
        for button in ("o:open", "o:name_yes", "o:f"):
            before.send(kit.tap(CAROL, button, "x", before.clock))
        before.send(kit.text(ALICE, "هلا", before.clock, msg_id="wamid.flip"))
        clock = before.clock
    finally:
        before.stop()
    after = Harness(tmp_path, clock=clock)
    try:
        after.send(text(ALICE, "هلا", clock, wamid="wamid.flip"))
        assert after.net.to(ALICE) == []  # already answered through Meta
        after.say(CAROL, GOOD_PIN)
        after.say(CAROL, GOOD_PIN)
        assert after.channel.accounts.by_phone(CAROL)["phone_e164"] == CAROL
        assert after.net.texts(CAROL)[-1].startswith("تم فتح حسابج")
    finally:
        after.stop()


def test_a_username_only_account_is_addressed_by_its_user_id(h):
    [item] = ycloud.parse(event(None, h.clock, type="text", text={"body": "هلا"}), BUSINESS)
    assert item.sender == "IQ.77" and not item.has_phone and item.name == "tester"
    h.send(event(None, h.clock, type="text", text={"body": "هلا"}))
    [body] = h.net.sends
    assert body["recipient"] == "IQ.77" and "to" not in body and body["from"] == BUSINESS
    assert h.channel.accounts.by_phone("IQ.77") is None


# ---------------------------------------------------------------- what is not a user's message


@pytest.mark.parametrize("sender", [BUSINESS, "9647700009999", "+964 771 489 9059"])
def test_the_bots_own_number_is_never_a_sender(h, sender):
    h.send(event(sender, h.clock, type="text", text={"body": "هلا"}))
    assert h.net.sends == [] and h.channel.store.conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0] == 0


def test_a_message_to_another_number_of_the_account_is_ignored(h):
    h.send(text(ALICE, "هلا", h.clock, to="+9647700008888"))
    assert h.net.sends == []


OWN = "9647700009999"
SERVICE = "9647714899059"
ARABIC = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


@pytest.mark.parametrize("sender", [
    "00" + OWN, "0" + OWN[3:], OWN[3:], "+" + OWN.translate(ARABIC),
    "00" + SERVICE, "0" + SERVICE[3:], SERVICE[3:], "+" + SERVICE.translate(ARABIC),
])
def test_the_bots_own_number_in_any_written_form_is_never_a_sender(h, sender):
    body = event(sender, h.clock, type="text", text={"body": "هلا"})
    assert ycloud.parse(body, BUSINESS, SERVICE) == []
    h.send(body)
    assert h.net.sends == []


@pytest.mark.parametrize("own", [BUSINESS, OWN, "0" + OWN[3:], "+" + SERVICE])
def test_the_bots_own_number_as_the_user_id_is_never_a_sender(h, own):
    body = event(None, h.clock, type="text", text={"body": "هلا"})
    body["whatsappInboundMessage"]["fromUserId"] = own
    h.send(body)
    assert h.net.sends == []


@pytest.mark.parametrize("written", ["00" + ALICE, "0" + ALICE[3:], ALICE[3:], "+" + ALICE.translate(ARABIC)])
def test_a_customers_number_in_any_written_form_is_one_sender_answered_at_one_number(h, written):
    [item] = ycloud.parse(event(written, h.clock, type="text", text={"body": "هلا"}), BUSINESS)
    assert item.sender == ALICE and item.has_phone
    h.send(event(written, h.clock, type="text", text={"body": "هلا"}))
    assert [s["to"] for s in h.net.sends] == [plus(ALICE)]


@pytest.mark.parametrize("written", ["IQ." + ALICE, "US.13491208655302741918", "IQ.77", "13491208655302741918",
                                     ALICE + "@s.whatsapp.net", ALICE + ":12@s.whatsapp.net", "0044770000000", "12345", ""])
def test_a_from_that_is_not_a_phone_number_falls_back_on_the_user_id(h, written):
    body = event(written, h.clock, type="text", text={"body": "هلا"})
    body["whatsappInboundMessage"]["fromUserId"] = "IQ.555"
    [item] = ycloud.parse(body, BUSINESS)
    assert item.sender == "IQ.555" and not item.has_phone
    h.send(body)
    [sent] = h.net.sends
    assert sent["recipient"] == "IQ.555" and "to" not in sent
    assert h.channel.sender_key(item.sender) != h.channel.sender_key(ALICE)
    body["whatsappInboundMessage"].pop("fromUserId")  # nothing to answer at: not taken at all
    assert ycloud.parse(body, BUSINESS) == []


def _without_to(body: dict) -> dict:
    body["whatsappInboundMessage"].pop("to")
    return body


@pytest.mark.parametrize("build", [
    lambda c: _without_to(text(ALICE, "هلا", c)),
    lambda c: text(ALICE, "هلا", c, to="garbage"),
    lambda c: text(ALICE, "هلا", c, to="+9647700008888"),
], ids=["no-to", "to-not-a-number", "another-number"])
def test_a_message_not_addressed_to_this_number_is_dropped_with_a_log_line(h, build, caplog):
    with caplog.at_level(logging.WARNING, logger="whatsapp.ycloud"):
        h.send(build(h.clock))
    assert h.net.requests == [] and h.channel.store.conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0] == 0
    assert "YCLOUD_FROM" in caplog.text and ALICE not in caplog.text


def test_with_no_business_number_nothing_is_taken(tmp_path):
    h = Harness(tmp_path, ycloud_from="")
    try:
        h.send(text(CAROL, "هلا", h.clock, to="+9647700008888"))
        h.send(text(CAROL, "هلا", h.clock, to=BUSINESS))
        assert h.net.requests == [] and h.channel.store.onboarding(h.channel.sender_key(CAROL)) is None
    finally:
        h.stop()


def test_a_message_sent_in_a_group_is_not_read_as_a_private_one(h):
    h.send(text(ALICE, "ادفع المولدة", h.clock, groupId="120363345678901234@g.us"))
    assert h.net.requests == []
    assert h.channel.store.conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0] == 0


@pytest.mark.parametrize("kind", ["user_changed_number", "user_changed_user_id", "user_identity_changed"])
def test_a_system_notice_gets_no_answer(h, kind):
    h.send(event(plus(ALICE), h.clock, type="system", system={"type": kind, "wa_id": CAROL, "body": "x"}))
    assert h.net.requests == [] and h.channel.accounts.by_phone(CAROL) is None
    assert h.channel.store.conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0] == 0


@pytest.mark.parametrize("field,value", [("wamid", {"a": 1}), ("wamid", ["x"]), ("wamid", 5), ("id", {"a": 1})])
def test_an_id_that_is_not_text_is_not_used_and_never_a_500(h, field, value):
    body = text(ALICE, "هلا", h.clock, event_id="evt.fallback")
    body["whatsappInboundMessage"][field] = value
    if field == "id":
        body["whatsappInboundMessage"].pop("wamid")
    h.send(body)
    assert len(h.net.sends) == 1
    assert isinstance(ycloud.parse(body, BUSINESS)[0].msg_id, str)


def test_no_text_id_at_all_is_dropped(h):
    body = text(ALICE, "هلا", h.clock)
    message = body["whatsappInboundMessage"]
    message["wamid"], message["id"], body["id"] = {"a": 1}, ["x"], 5
    h.send(body)
    assert h.net.requests == []


@pytest.mark.parametrize("value", [{"a": 1}, ["x"], 5])
def test_a_user_id_or_name_that_is_not_text_is_not_used(h, value):
    body = event(None, h.clock, type="text", text={"body": "هلا"})
    body["whatsappInboundMessage"]["fromUserId"] = value
    assert ycloud.parse(body, BUSINESS) == []
    named = text(ALICE, "هلا", h.clock)
    named["whatsappInboundMessage"]["customerProfile"] = {"name": value}
    assert ycloud.parse(named, BUSINESS)[0].name is None


def _owner_message(clock) -> dict:
    return {"id": "yc-out", "wamid": "wamid.owner", "wabaId": "waba1", "from": BUSINESS, "to": plus(ALICE),
            "type": "text", "text": {"body": "هلا"}, "status": "sent", "createTime": iso(clock), "sendTime": iso(clock)}


@pytest.mark.parametrize("build", [
    lambda c: {"id": "e", "type": "whatsapp.smb.message.echoes", "createTime": iso(c), "whatsappMessage": _owner_message(c)},
    lambda c: {"id": "e", "type": "whatsapp.smb.message.created", "createTime": iso(c), "whatsappMessage": _owner_message(c)},
    lambda c: {"id": "e", "type": "whatsapp.smb.history", "createTime": iso(c), "phase": 0, "progress": 10,
               "whatsappMessage": _owner_message(c)},
    # a customer's old message, under the same key as a live one: only the event type tells them apart
    lambda c: {**text(ALICE, "هلا", c), "type": "whatsapp.smb.history", "phase": 0, "progress": 10},
    lambda c: {"id": "e", "type": "whatsapp.smb.history", "createTime": iso(c), "phase": 2, "progress": 100},
    lambda c: {"id": "e", "type": "whatsapp.message.updated", "createTime": iso(c),
               "whatsappMessage": {**_owner_message(c), "status": "delivered"}},
    lambda c: {"id": "e", "createTime": iso(c), "whatsappInboundMessage": text(ALICE, "هلا", c)["whatsappInboundMessage"]},
], ids=["echoes", "created", "history-out", "history-in", "history-progress", "status", "no-type"])
def test_other_events_are_answered_200_and_ignored(h, build):
    h.send(build(h.clock))
    assert h.net.requests == []
    assert h.channel.store.conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0] == 0


def test_old_messages_are_never_answered(h):
    h.send(text(ALICE, "هلا", h.clock, sent="2026-11-02T05:44:00.000Z"))  # 16 minutes
    h.send(text(ALICE, "هلا", h.clock, sent="2026-11-02T09:44:00+04:00"))  # the same moment, another offset
    assert h.net.sends == []
    h.send(text(ALICE, "هلا", h.clock, sent="2026-11-02T05:50:00.000Z"))  # 10 minutes
    assert len(h.net.sends) == 1


def test_a_message_without_any_usable_time_is_dropped(h):
    body = text(ALICE, "هلا", h.clock, sent="not a time")
    body["createTime"] = "also not"
    h.send(body)
    assert h.net.sends == []
    fallback = text(ALICE, "هلا", h.clock, sent="not a time")  # the event's own time still tells its age
    h.send(fallback)
    assert len(h.net.sends) == 1


@pytest.mark.parametrize("body", [
    {"type": ycloud.INBOUND_EVENT},
    {"type": ycloud.INBOUND_EVENT, "whatsappInboundMessage": "x"},
    {"type": ycloud.INBOUND_EVENT, "whatsappInboundMessage": {"from": "+9647700000001", "to": BUSINESS,
                                                              "sendTime": "2026-11-02T06:00:00Z", "id": "m",
                                                              "type": "text", "text": "not an object"}},
    [], "text", 5,
])
def test_a_malformed_body_is_answered_200_and_ignored(h, body):
    assert h.post(body).status_code == 200
    assert h.channel.join(20) and h.net.sends == []


# ---------------------------------------------------------------- the signature


def test_bad_missing_and_stale_signatures_are_403_and_nothing_happens(h):
    body = text(ALICE, "هلا", h.clock)
    now = int(h.clock().timestamp())
    raw = json.dumps(body, ensure_ascii=False).encode()
    good = ycloud.sign(raw, WH_SECRET, now)
    assert h.post(body, secret="whsec_wrong").status_code == 403
    assert h.post(body, headers={}).status_code == 403
    assert h.post(body, stamp=now - 301).status_code == 403
    assert h.post(body, stamp=now + 301).status_code == 403
    for header in ("", "t=", "s=abc", f"t={now}", good.split(",")[1], "garbage", f"t=abc,{good.split(',')[1]}",
                   f"t={now},t={now - 1},{good.split(',')[1]}", f"t={now},s=zz", f"t={now},s=é"):
        assert h.post(body, headers={"YCloud-Signature": header.encode("latin-1")}).status_code == 403, header
    assert h.channel.join(20) and h.net.requests == []
    assert h.post(body, stamp=now - 299).status_code == 200  # inside the window


def test_any_of_several_s_values_and_upper_case_hex_pass_as_in_the_docs_verifier(h):
    body = text(ALICE, "هلا", h.clock)
    raw = json.dumps(body, ensure_ascii=False).encode()
    now = int(h.clock().timestamp())
    stamp, mac = ycloud.sign(raw, WH_SECRET, now).split(",s=")
    wrong = "0" * 64
    for header in (f"{stamp},s={wrong},s={mac}", f"{stamp},s={mac},s={wrong}", f"{stamp},s={mac.upper()}"):
        assert h.post(body, headers={"YCloud-Signature": header}).status_code == 200, header
    for header in (f"{stamp},s={wrong},s={wrong}", f"{stamp},s=,s=", f"{stamp}", f"{stamp},s={wrong},s=é"):
        assert h.post(body, headers={"YCloud-Signature": header.encode("latin-1")}).status_code == 403, header
    assert h.channel.join(20) and len(h.net.sends) == 1


def test_signature_covers_the_exact_raw_body(h):
    raw = json.dumps(text(ALICE, "هلا", h.clock)).encode()  # escaped Arabic: another serialisation of the same JSON
    now = int(h.clock().timestamp())
    head = {"Content-Type": "application/json"}
    assert h.client.post("/webhook", content=raw, headers={**head, "YCloud-Signature": ycloud.sign(raw, WH_SECRET, now)}).status_code == 200
    other = json.dumps(text(ALICE, "هلا", h.clock), ensure_ascii=False).encode()
    assert h.client.post("/webhook", content=other, headers={**head, "YCloud-Signature": ycloud.sign(raw, WH_SECRET, now)}).status_code == 403


def test_a_replayed_signature_is_answered_once_and_dies_with_its_window(h):
    body = text(ALICE, "هلا", h.clock)
    raw = json.dumps(body, ensure_ascii=False).encode()
    head = {"Content-Type": "application/json",
            "YCloud-Signature": ycloud.sign(raw, WH_SECRET, int(h.clock().timestamp()))}
    assert h.client.post("/webhook", content=raw, headers=head).status_code == 200
    assert h.client.post("/webhook", content=raw, headers=head).status_code == 200
    assert h.channel.join(20)
    assert len(h.net.sends) == 1
    other = json.dumps(text(ALICE, "شكد رصيدي", h.clock), ensure_ascii=False).encode()
    assert h.client.post("/webhook", content=other, headers=head).status_code == 403
    h.clock.advance(minutes=6)
    assert h.client.post("/webhook", content=raw, headers=head).status_code == 403


def test_retries_of_the_same_event_or_wamid_are_answered_once(h):
    first = text(ALICE, "هلا", h.clock, wamid="wamid.same", event_id="evt.same")
    h.send(first)
    h.send(first)  # the same event
    h.send(text(ALICE, "هلا", h.clock, wamid="wamid.same"))  # another event id, the same message
    assert len(h.net.sends) == 1
    no_wamid = text(BOB, "هلا", h.clock, event_id="evt.other")
    no_wamid["whatsappInboundMessage"].pop("wamid")
    h.send(no_wamid)
    h.send(no_wamid)
    assert len(h.net.to(BOB)) == 1
    bare = text(CAROL, "هلا", h.clock, event_id="evt.bare")
    bare["whatsappInboundMessage"].pop("wamid")
    bare["whatsappInboundMessage"].pop("id")
    h.send(bare)
    h.send(bare)  # nothing but the event id to go by
    assert len(h.net.to(CAROL)) == 1


def test_each_provider_takes_only_its_own_signature(tmp_path):
    h = Harness(tmp_path / "y", app_secret=SECRET)  # a Meta secret left in place changes nothing
    try:
        body = text(ALICE, "هلا", h.clock)
        raw = json.dumps(body, ensure_ascii=False).encode()
        assert h.post(body, headers={"X-Hub-Signature-256": meta.sign(raw, SECRET)}).status_code == 403
        assert h.post(body, headers={"X-Hub-Signature-256": meta.sign(raw, WH_SECRET)}).status_code == 403
    finally:
        h.stop()
    m = MetaHarness(tmp_path / "m", ycloud_webhook_secret=WH_SECRET, ycloud_api_key=API_KEY)
    try:
        body = kit.webhook(kit.text(ALICE, "هلا", m.clock))
        raw = json.dumps(body, ensure_ascii=False).encode()
        now = int(m.clock().timestamp())
        for secret in (WH_SECRET, SECRET):
            r = m.client.post("/webhook", content=raw, headers={"YCloud-Signature": ycloud.sign(raw, secret, now)})
            assert r.status_code == 403
        assert m.channel.join(20) and m.graph.sent == []
    finally:
        m.stop()


def test_no_webhook_secret_refuses_every_post_unless_unsigned_is_allowed(tmp_path):
    h = Harness(tmp_path / "a", ycloud_webhook_secret="")
    try:
        assert h.post(text(ALICE, "هلا", h.clock), secret="").status_code == 403
        assert h.post(text(ALICE, "هلا", h.clock), headers={}).status_code == 403
    finally:
        h.stop()
    h = Harness(tmp_path / "b", ycloud_webhook_secret="", allow_unsigned=True)
    try:
        assert h.post(text(ALICE, "هلا", h.clock), headers={}).status_code == 200
        assert h.channel.join(20) and len(h.net.sends) == 1
    finally:
        h.stop()
    h = Harness(tmp_path / "c", allow_unsigned=True)  # a secret is set: it is checked, as on Meta
    try:
        assert h.post(text(ALICE, "هلا", h.clock), headers={}).status_code == 403
    finally:
        h.stop()
    # Any webhook secret set means this is not a local test: nobody opens an account on someone else's number.
    h = Harness(tmp_path / "d", ycloud_webhook_secret="", allow_unsigned=True, app_secret=SECRET)
    try:
        assert h.post(text(CAROL, "هلا", h.clock), headers={}).status_code == 403
        assert h.channel.join(20) and h.net.requests == []
        assert h.channel.store.onboarding(h.channel.sender_key(CAROL)) is None
    finally:
        h.stop()
    # Meta mode goes by its own secret alone, as it always has
    m = MetaHarness(tmp_path / "e", app_secret="", allow_unsigned=True, ycloud_webhook_secret=WH_SECRET)
    try:
        assert m.client.post("/webhook", json=kit.webhook(kit.text(ALICE, "هلا", m.clock))).status_code == 200
    finally:
        m.stop()


# ---------------------------------------------------------------- the client


def test_payload_shapes():
    api, seen, _ = client(lambda r: httpx.Response(200, json={"id": "o", "status": "accepted"}))
    api.send_text(ALICE, True, "هلا")
    api.send_buttons("IQ.9", False, "بطاقة", [("confirm:q1", "إي، ادفع")], header="تأكيد", footer="تنتهي")
    api.send_audio(ALICE, True, "media-1")
    first, second, third = (json.loads(r.content) for r in seen)
    assert str(seen[0].url) == "https://api.ycloud.com/v2/whatsapp/messages/sendDirectly"
    assert seen[0].headers["X-API-Key"] == API_KEY
    assert first == {"from": BUSINESS, "to": plus(ALICE), "type": "text",
                     "text": {"preview_url": False, "body": "هلا"}}
    assert second["recipient"] == "IQ.9" and "to" not in second and second["from"] == BUSINESS
    assert second["interactive"]["action"]["buttons"][0]["reply"] == {"id": "confirm:q1", "title": "إي، ادفع"}
    assert second["interactive"]["header"]["text"] == "تأكيد" and second["interactive"]["footer"]["text"] == "تنتهي"
    assert third == {"from": BUSINESS, "to": plus(ALICE), "type": "audio", "audio": {"id": "media-1", "voice": True}}


def test_no_from_number_sends_nothing():
    seen = []
    api = YCloudClient(API_KEY, "", transport=httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200)))
    with pytest.raises(SendError):
        api.send_text(ALICE, True, "x")
    with pytest.raises(SendError):
        api.upload_media(OGG, "audio/ogg")
    assert seen == []


def test_rate_limits_are_retried():
    replies = iter([httpx.Response(429, text="slow down"),
                    httpx.Response(400, json={"error": {"whatsappApiError": {"code": "130429", "message": "x"}}}),
                    httpx.Response(200, json={"status": "accepted"})])
    api, seen, slept = client(lambda r: next(replies))
    api.send_text(ALICE, True, "x")
    assert len(seen) == 3 and slept == [1.5, 3.0]
    api, seen, _ = client(lambda r: httpx.Response(429))
    with pytest.raises(SendError):
        api.send_text(ALICE, True, "x")
    assert len(seen) == 3


@pytest.mark.parametrize("status", [500, 502, 503])
def test_a_5xx_is_never_retried_even_when_it_names_a_rate_limit(status):
    api, seen, slept = client(lambda r: httpx.Response(status, text="error 130429"))
    with pytest.raises(SendError):
        api.send_buttons(ALICE, True, "بطاقة", [("a", "b")])
    assert len(seen) == 1 and slept == []  # it may have been delivered: never the same card twice


@pytest.mark.parametrize("status", [400, 404, 422])
def test_other_errors_are_not_retried(status):
    api, seen, _ = client(lambda r: httpx.Response(status, json={"error": {"code": "X"}}))
    with pytest.raises(SendError):
        api.send_text(ALICE, True, "x")
    assert len(seen) == 1


def test_an_answer_saying_failed_is_an_error_and_a_rate_limited_one_is_retried():
    api, seen, _ = client(lambda r: httpx.Response(200, json={"status": "failed", "errorCode": "131026"}))
    with pytest.raises(SendError):
        api.send_text(ALICE, True, "x")
    assert len(seen) == 1
    replies = iter([httpx.Response(200, json={"status": "failed", "errorCode": "131056"}),
                    httpx.Response(200, json={"status": "accepted"})])
    api, seen, _ = client(lambda r: next(replies))
    api.send_text(ALICE, True, "x")
    assert len(seen) == 2


def test_a_success_with_an_odd_body_counts_as_sent():
    api, seen, _ = client(lambda r: httpx.Response(200, text="ok"))
    api.send_text(ALICE, True, "x")
    assert len(seen) == 1


def test_mark_read_shows_the_typing_dots_and_never_raises():
    api, seen, slept = client(lambda r: httpx.Response(200))
    api.mark_read("wamid.HBg/x=")
    assert seen[0].method == "POST" and seen[0].content == b""
    assert seen[0].url.raw_path == b"/v2/whatsapp/inboundMessages/wamid.HBg%2Fx%3D/typing"
    api, seen, slept = client(lambda r: httpx.Response(429))
    api.mark_read("wamid.1")
    assert len(seen) == 1 and slept == []

    def down(request):
        raise httpx.ConnectError("no route")

    api, _, _ = client(down)
    api.mark_read("wamid.1")


@pytest.mark.parametrize("msg_id", ["..", ".", "...", ""])
def test_mark_read_never_leaves_its_path_on_an_id_of_dots(msg_id):
    api, seen, _ = client(lambda r: httpx.Response(200))
    api.mark_read(msg_id)
    assert seen == []


def test_the_api_key_goes_out_on_every_call_and_shows_in_no_repr():
    api, seen, _ = client(lambda r: httpx.Response(200, json={"id": "x"}))
    assert API_KEY not in repr(api.client.headers) and API_KEY not in str(api.client.headers)
    assert API_KEY not in repr(api) and API_KEY not in repr(api.client)
    api.send_text(ALICE, True, "x")
    api.upload_media(OGG, "audio/ogg")
    api.mark_read("wamid.1")
    api.media(LINK)
    assert len(seen) == 4 and all(r.headers["X-API-Key"] == API_KEY for r in seen)


def test_media_comes_only_from_api_ycloud_com_with_the_key():
    api, seen, _ = client(lambda r: httpx.Response(200, content=OGG, headers={"content-type": "audio/ogg"}))
    assert api.media(LINK) == (OGG, "audio/ogg")
    assert seen[0].headers["X-API-Key"] == API_KEY and str(seen[0].url) == LINK
    seen.clear()
    path = "/v2/whatsapp/media/download/m1"
    for forged in (EVIL, f"http://api.ycloud.com{path}", f"https://api.ycloud.com.evil.example.com{path}",
                   f"https://evil.example.com/api.ycloud.com{path}", f"https://api.ycloud.com@evil.example.com{path}",
                   f"https://x:y@api.ycloud.com{path}", f"https://api.ycloud.com:8443{path}",
                   f"https://xapi.ycloud.com{path}", "m1", "", "https://[bad/m1",
                   # on YCloud, but not a media file: the key must not read the rest of the account
                   "https://api.ycloud.com/v2/webhookEndpoints", "https://api.ycloud.com/v2/balance",
                   "https://api.ycloud.com/", "https://api.ycloud.com", "https://api.ycloud.com/v2/whatsapp/media/download",
                   "https://api.ycloud.com/x/v2/whatsapp/media/download/m1",
                   "https://api.ycloud.com/v2/whatsapp/media/download/../../../webhookEndpoints",
                   "https://api.ycloud.com/v2/whatsapp/media/download/%2e%2e/%2e%2e/%2e%2e/webhookEndpoints",
                   "https://api.ycloud.com/v2/whatsapp/media/download/m1/..%2f..%2f..%2f..%2fwebhookEndpoints"):
        assert not ycloud.is_ycloud(forged), forged
        with pytest.raises(SendError):
            api.media(forged)
    assert seen == []  # nothing was fetched, and the key went nowhere


def test_a_redirect_from_the_media_link_is_not_followed():
    api, seen, _ = client(lambda r: httpx.Response(302, headers={"location": EVIL}))
    with pytest.raises(SendError):
        api.media(LINK)
    assert [r.url.host for r in seen] == ["api.ycloud.com"]


def test_media_size_cap_stays(monkeypatch):
    monkeypatch.setattr(ycloud, "MAX_INBOUND_MEDIA", 8)
    api, _, _ = client(lambda r: httpx.Response(200, content=b"x" * 9))
    with pytest.raises(SendError):
        api.media(LINK)
    assert ycloud.MAX_INBOUND_MEDIA == 8
    monkeypatch.undo()
    assert ycloud.MAX_INBOUND_MEDIA == meta.MAX_INBOUND_MEDIA


def test_upload_shape_and_errors():
    api, seen, _ = client(lambda r: httpx.Response(200, json={"id": "media-1"}))
    assert api.upload_media(OGG, "audio/ogg; codecs=opus") == "media-1"
    request = seen[0]
    assert request.url.host == "api.ycloud.com" and request.url.path == f"/v2/whatsapp/media/{BUSINESS}/upload"
    assert request.headers["content-type"].startswith("multipart/form-data") and request.headers["X-API-Key"] == API_KEY
    assert b'name="file"; filename="reply.ogg"' in request.content and b"Content-Type: audio/ogg" in request.content
    assert OGG in request.content
    for answer in (httpx.Response(400, json={"error": {}}), httpx.Response(200, json={}), httpx.Response(200, text="x")):
        api, seen, _ = client(lambda r: answer)
        with pytest.raises(SendError):
            api.upload_media(OGG, "audio/ogg")
        assert len(seen) == 1
    api, seen, _ = client(lambda r: httpx.Response(200, json={"id": "x"}))
    with pytest.raises(SendError):
        api.upload_media(b"", "audio/ogg")
    assert seen == []


# ---------------------------------------------------------------- voice


def test_a_voice_note_is_fetched_heard_and_answered_with_a_voice_note_then_the_card(tmp_path):
    core = Speaking()
    h = Harness(tmp_path, core=core)
    try:
        assert h.channel.speaks
        assert ycloud.parse(voice(ALICE, LINK, h.clock), BUSINESS)[0].mime == "audio/ogg; codecs=opus"
        h.send(voice(ALICE, LINK, h.clock))
        assert any(str(r.url) == LINK for r in h.net.requests)
        assert h.net.audios(ALICE) == [{"id": "media-1", "voice": True}]
        assert len(h.net.uploads()) == 1 and len(h.net.cards(ALICE)) == 1
    finally:
        h.stop()


def test_a_forged_media_link_is_not_fetched_and_the_person_is_asked_again(tmp_path):
    h = Harness(tmp_path, core=Speaking())
    try:
        h.send(voice(ALICE, EVIL, h.clock))
        assert h.net.hosts() == {"api.ycloud.com"}
        assert not any("media/download" in r.url.path for r in h.net.requests)
        assert h.net.cards(ALICE) == [] and len(h.net.texts(ALICE)) == 1
    finally:
        h.stop()


def test_a_client_without_upload_never_speaks_and_answers_in_text(tmp_path):
    class NoUpload(YCloudClient):
        upload_media = None

    h = Harness(tmp_path, core=Speaking(), client_class=NoUpload)
    try:
        assert not h.channel.speaks
        h.send(voice(ALICE, LINK, h.clock))
        assert h.net.audios(ALICE) == [] and h.net.uploads() == []
        assert len(h.net.cards(ALICE)) == 1
    finally:
        h.stop()


@pytest.mark.parametrize("status", [400, 500])
def test_a_failed_upload_falls_back_to_text(tmp_path, status):
    h = Harness(tmp_path, core=Speaking())
    try:
        h.net.upload_status = status
        h.send(voice(ALICE, LINK, h.clock))
        assert len(h.net.uploads()) == 1 and h.net.audios(ALICE) == []
        assert len(h.net.cards(ALICE)) == 1
    finally:
        h.stop()


# ---------------------------------------------------------------- settings and health


def test_settings_from_env_never_show_secrets():
    s = Settings.from_env({"WHATSAPP_PROVIDER": " YCloud ", "YCLOUD_API_KEY": "yc-key-secret",
                           "YCLOUD_WEBHOOK_SECRET": "whsec_secret", "YCLOUD_FROM": "+964 770 000 9999"})
    assert s.provider == "ycloud" and s.ycloud_from == "9647700009999"
    assert "yc-key-secret" not in repr(s) and "whsec_secret" not in repr(s)
    assert s.missing == []


@pytest.mark.parametrize("written", ["+964 771 489 9059", "964-771-489-9059", "009647714899059", "0771 489 9059",
                                     "07714899059", "7714899059", "+٩٦٤٧٧١٤٨٩٩٠٥٩"])
def test_ycloud_from_in_any_written_form_answers_and_sends_as_the_same_number(written):
    s = Settings.from_env({"WHATSAPP_PROVIDER": "ycloud", "YCLOUD_API_KEY": "k", "YCLOUD_WEBHOOK_SECRET": "w",
                           "YCLOUD_FROM": written})
    assert s.ycloud_from == "9647714899059" and s.missing == []
    assert YCloudClient("k", s.ycloud_from).from_number == "+9647714899059"
    assert YCloudClient("k", written).from_number == "+9647714899059"
    clock = kit.Clock()
    assert len(ycloud.parse(text(ALICE, "هلا", clock, to="+9647714899059"), s.ycloud_from)) == 1


@pytest.mark.parametrize("written", ["0044 7700 900000", "not a number", "12345", "US.13491208655302741918"])
def test_a_ycloud_from_that_cannot_be_sent_from_is_reported_missing(written):
    s = Settings.from_env({"WHATSAPP_PROVIDER": "ycloud", "YCLOUD_API_KEY": "k", "YCLOUD_WEBHOOK_SECRET": "w",
                           "YCLOUD_FROM": written})
    assert s.missing == ["YCLOUD_FROM"]
    assert Settings.from_env({"WHATSAPP_PROVIDER": "ycloud", "YCLOUD_FROM": "+1 631 555 1111"}).ycloud_from == "16315551111"


def test_settings_missing_names_follow_the_provider():
    assert Settings.from_env({"WHATSAPP_PROVIDER": "ycloud"}).missing == ["YCLOUD_API_KEY", "YCLOUD_WEBHOOK_SECRET",
                                                                          "YCLOUD_FROM"]
    meta_names = ["WHATSAPP_TOKEN", "WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN", "WHATSAPP_PHONE_NUMBER_ID"]
    assert Settings.from_env({}).provider == "meta" and Settings.from_env({}).missing == meta_names
    assert Settings.from_env({"WHATSAPP_PROVIDER": "", "YCLOUD_API_KEY": "x"}).missing == meta_names
    with pytest.raises(ValueError) as refused:
        Settings.from_env({"WHATSAPP_PROVIDER": "ycloudd"})
    assert "ycloudd" not in str(refused.value)


def test_healthz_names_what_ycloud_needs_and_never_the_meta_names(tmp_path, monkeypatch):
    h = Harness(tmp_path / "a")
    try:
        assert h.client.get("/healthz").json() == {"ok": True, "missing": [], "voice": False}
        monkeypatch.setattr("channels.whatsapp.app.pin_pepper", lambda: "")
        body = h.client.get("/healthz")  # the pepper counts here too, with no WHATSAPP_TOKEN set
        assert body.status_code == 503 and body.json()["missing"] == ["ACCOUNTS_PIN_PEPPER"]
    finally:
        h.stop()
    monkeypatch.undo()
    h = Harness(tmp_path / "b", ycloud_api_key="", ycloud_from="")
    try:
        body = h.client.get("/healthz")
        assert body.status_code == 503 and body.json()["missing"] == ["YCLOUD_API_KEY", "YCLOUD_FROM"]
    finally:
        h.stop()


def test_the_provider_chooses_the_client(tmp_path):
    base = dict(debounce_seconds=0.0, daily_budget_usd=0.0)
    app = create_app(Settings(provider="ycloud", ycloud_api_key=API_KEY, ycloud_from=BUSINESS, data_dir=tmp_path / "y",
                              **base), core=StubCore(), seed=kit.seed())
    with TestClient(app):
        graph = app.state.channel.graph
        assert isinstance(graph, YCloudClient) and graph.from_number == BUSINESS
    app = create_app(Settings(token="t", phone_number_id=PNID, data_dir=tmp_path / "m", **base), core=StubCore(),
                     seed=kit.seed())
    with TestClient(app):
        assert isinstance(app.state.channel.graph, GraphClient)


def test_get_webhook_stays_for_meta(tmp_path):
    h = Harness(tmp_path, verify_token=VERIFY)
    try:
        ok = h.client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "7"})
        assert ok.status_code == 200 and ok.text == "7"
    finally:
        h.stop()


def test_no_secret_reaches_the_logs(h, caplog):
    with caplog.at_level(logging.DEBUG):
        h.say(ALICE, "ادفع المولدة")
        h.tap(ALICE, "إي، ادفع")
        h.say(ALICE, PIN)
        h.post(text(ALICE, "هلا", h.clock), secret="whsec_wrong")
        assert h.channel.join(20)
    assert API_KEY not in caplog.text and WH_SECRET not in caplog.text and SALT not in caplog.text


# ---------------------------------------------------------------- Meta mode


def test_meta_mode_is_unchanged(tmp_path):
    h = MetaHarness(tmp_path)
    try:
        assert h.settings().provider == "meta" and h.settings().sender_secret == SECRET
        assert h.client.get("/healthz").json() == {"ok": True, "missing": [], "voice": False}
        ok = h.client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "7"})
        assert ok.status_code == 200 and ok.text == "7"
        h.say(ALICE, "ادفع المولدة")
        h.tap(ALICE, "إي، ادفع")
        h.say(ALICE, PIN)
        assert h.balance(ALICE) == 455_000
        assert all(s["to"] == ALICE for s in h.graph.sent)  # digits, as Meta takes them
        # a YCloud-shaped body with Meta's signature: signed, but it is not the shape this mode reads
        h.graph.clear()
        assert kit.post(h.client, text(ALICE, "هلا", h.clock)).status_code == 200
        assert h.channel.join(20) and h.graph.sent == []
    finally:
        h.stop()


def test_meta_client_and_parser_do_not_know_ycloud(tmp_path):
    seen = []
    graph = GraphClient("tok", PNID, "v25.0", transport=httpx.MockTransport(
        lambda r: seen.append(r) or httpx.Response(200, json={})), sleep=lambda s: None)
    graph.send_text(ALICE, True, "x")
    body = json.loads(seen[0].content)
    assert seen[0].url.host == "graph.facebook.com" and body["to"] == ALICE
    assert body["messaging_product"] == "whatsapp" and body["recipient_type"] == "individual"
    assert "from" not in body and "X-API-Key" not in seen[0].headers
    assert meta.parse(text(ALICE, "هلا", kit.Clock())) == []
