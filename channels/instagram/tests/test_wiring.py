"""Instagram as a second door to the same agent, end to end and offline.

The app is the WhatsApp channel's (channels/whatsapp/app.py) with Instagram settings and a fake Instagram client that
builds the real payloads (channels/instagram/client.py) and records them. The WhatsApp side is the usual fake Graph
(channels/whatsapp/tests/kit.py). No network, no keys, no model.
"""

from __future__ import annotations

import itertools
import json
import logging
import re
import threading
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from channels.instagram import texts as ig_texts
from channels.instagram.client import NotSupported, buttons_payloads, list_payloads, text_payloads
from channels.instagram.config import Settings as InstagramSettings
from channels.instagram.graph import sign
from channels.instagram.wiring import is_instagram, scoped, unscoped
from channels.whatsapp import texts as wa_texts
from channels.whatsapp.app import create_app
from channels.whatsapp.config import Settings
from channels.whatsapp.graph import Inbound
from channels.whatsapp.tests import kit
from channels.whatsapp.tests.kit import ALICE, BOB, PIN, PNID, SECRET, VERIFY
from channels.whatsapp.tests.test_language_and_voice import Speaking
from payments.messages import render

IG_SECRET = "test-instagram-app-secret"
IG_VERIFY = "test-instagram-verify"
OWN = "17841400000000001"  # our professional account (synthetic)
SARA_IG = "1200000000000042"  # Alice's Instagram-scoped id (synthetic)
ALI_IG = "1200000000000077"  # Bob's
STRANGER = "1200000000000099"
SERVICE = "9647714899059"


class FakeInstagram:
    """The Instagram client's interface. Payloads are built by the real builders, so what Instagram would refuse
    (four buttons, an empty title, a payload too long) is refused here too."""

    voice_replies = False

    def __init__(self):
        self.sent: list[tuple[str, dict]] = []
        self.read: list[tuple[str, str]] = []
        self.remembered: list[str] = []
        self.media_files: dict[str, tuple[bytes, str]] = {}
        self.fetched: list[str] = []
        self.closed = False

    def send_text(self, to, has_phone, text):
        assert has_phone is False
        self.sent += [(to, p) for p in text_payloads(to, text)]

    def send_buttons(self, to, has_phone, body, buttons, header="", footer=""):
        assert has_phone is False
        self.sent += [(to, p) for p in buttons_payloads(to, body, buttons, header, footer)]

    def send_list(self, to, has_phone, body, button, rows, header=None, footer=None):
        self.sent += [(to, p) for p in list_payloads(to, body, rows, header, footer)]

    def upload_media(self, data, mime):
        raise NotSupported("no voice notes on Instagram")

    def send_audio(self, to, has_phone, media_id):
        raise NotSupported("no voice notes on Instagram")

    def mark_read(self, msg_id, to=""):
        self.read.append((msg_id, to))

    def media(self, url):
        self.fetched.append(url)
        if url not in self.media_files:
            raise RuntimeError("no such media")
        return self.media_files[url]

    def remember(self, items):
        self.remembered += [item.sender for item in items]

    def close(self):
        self.closed = True

    # ---- views

    def to(self, who: str) -> list[dict]:
        return [p["message"] for to, p in self.sent if to == who]

    def texts(self, who: str) -> list[str]:
        """Every text shown, a card's and a list's included."""
        out = []
        for m in self.to(who):
            if "text" in m:
                out.append(m["text"])
            elif "attachment" in m:
                out.append(m["attachment"]["payload"]["text"])
        return out

    def templates(self, who: str) -> list[dict]:
        return [m["attachment"]["payload"] for m in self.to(who) if "attachment" in m]

    def everything(self, who: str) -> str:
        return "\n".join(self.texts(who))

    def clear(self):
        self.sent.clear()


def buttons(template: dict) -> dict[str, str]:
    """title -> payload"""
    return {b["title"]: b["payload"] for b in template["buttons"]}


_mids = itertools.count(1)


def ig_body(*events: dict) -> dict:
    return {"object": "instagram", "entry": [{"id": OWN, "time": 1, "messaging": list(events)}]}


def ig_text(sender: str, text: str, clock, mid: str | None = None) -> dict:
    return {"sender": {"id": sender}, "recipient": {"id": OWN}, "timestamp": int(clock().timestamp() * 1000),
            "message": {"mid": mid or f"aWdfZ{next(_mids)}", "text": text}}


def ig_postback(sender: str, payload: str, title: str, clock) -> dict:
    return {"sender": {"id": sender}, "recipient": {"id": OWN}, "timestamp": int(clock().timestamp() * 1000),
            "postback": {"mid": f"aWdfP{next(_mids)}", "title": title, "payload": payload}}


def ig_audio(sender: str, url: str, clock) -> dict:
    return {"sender": {"id": sender}, "recipient": {"id": OWN}, "timestamp": int(clock().timestamp() * 1000),
            "message": {"mid": f"aWdfA{next(_mids)}", "attachments": [{"type": "audio", "payload": {"url": url}}]}}


class Harness:
    def __init__(self, tmp_path, *, instagram: bool = True, core=None, ig_settings=None, **overrides):
        self.clock = kit.Clock()
        self.graph = kit.FakeGraph()
        self.ig = FakeInstagram()
        settings = Settings(**{**dict(token="test-token", app_secret=SECRET, verify_token=VERIFY,
                                      phone_number_id=PNID, data_dir=tmp_path, debounce_seconds=0.0,
                                      daily_budget_usd=0.0, service_number=SERVICE), **overrides})
        if ig_settings is None and instagram:
            ig_settings = InstagramSettings(token="ig-token", app_secret=IG_SECRET, verify_token=IG_VERIFY,
                                            account_id=OWN)
        self.app = create_app(settings, graph=self.graph, core=core, seed=kit.seed(), clock=self.clock,
                              instagram=ig_settings, instagram_client=self.ig if instagram else None)
        self._cm = TestClient(self.app)
        self.client = self._cm.__enter__()
        self.channel = self.app.state.channel

    def stop(self):
        self._cm.__exit__(None, None, None)

    # ---- WhatsApp
    def say(self, who: str, body: str) -> None:
        assert kit.post(self.client, kit.webhook(kit.text(who, body, self.clock))).status_code == 200
        assert self.channel.join(20)

    def tap(self, who: str, button_id: str, title: str = "x") -> None:
        assert kit.post(self.client, kit.webhook(kit.tap(who, button_id, title, self.clock))).status_code == 200
        assert self.channel.join(20)

    # ---- Instagram
    def ig_post(self, body: dict, signature: str | None = None):
        raw = json.dumps(body, ensure_ascii=False).encode()
        sig = sign(raw, IG_SECRET) if signature is None else signature
        response = self.client.post("/instagram/webhook", content=raw,
                                    headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})
        assert self.channel.join(20)
        return response

    def ig_say(self, who: str, text: str) -> None:
        assert self.ig_post(ig_body(ig_text(who, text, self.clock))).status_code == 200

    def ig_tap(self, who: str, payload: str, title: str = "x") -> None:
        assert self.ig_post(ig_body(ig_postback(who, payload, title, self.clock))).status_code == 200

    def code_for(self, who: str) -> str:
        """Ask for a link code in the WhatsApp chat and read it from the reply."""
        self.say(who, "اربط انستغرام")
        found = re.search(r"\b(\d{6})\b", self.graph.texts(who)[-1])
        assert found, self.graph.texts(who)
        return found.group(1)

    def link(self, who: str, ig_sender: str) -> None:
        self.ig_say(ig_sender, self.code_for(who))
        assert self.channel.instagram.linked(scoped(ig_sender)) == self.uid(who)
        self.graph.clear()
        self.ig.clear()

    def uid(self, who: str) -> str:
        return self.channel.accounts.by_phone(who)["id"]

    def balance(self, who: str) -> int:
        return int(self.channel.accounts.get(self.uid(who))["balance"])


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path)
    yield harness
    harness.stop()


# ---------------------------------------------------------------- the webhook


def test_the_handshake_answers_only_our_verify_token(h):
    ok = h.client.get("/instagram/webhook", params={"hub.mode": "subscribe", "hub.verify_token": IG_VERIFY,
                                                    "hub.challenge": "8812"})
    assert ok.status_code == 200 and ok.text == "8812"
    for token in ("nope", VERIFY):  # the WhatsApp verify token does not open this door
        bad = h.client.get("/instagram/webhook", params={"hub.mode": "subscribe", "hub.verify_token": token,
                                                         "hub.challenge": "8812"})
        assert bad.status_code == 403


def test_a_bad_or_missing_signature_is_403_and_nothing_is_answered(h):
    body = ig_body(ig_text(STRANGER, "هلا", h.clock))
    raw = json.dumps(body, ensure_ascii=False).encode()
    for sig in ("sha256=" + "0" * 64, sign(raw, SECRET), ""):  # the WhatsApp app secret is not this one
        assert h.ig_post(body, signature=sig).status_code == 403
    assert h.client.post("/instagram/webhook", content=raw).status_code == 403  # no header at all
    assert h.ig.sent == [] and h.graph.sent == []


def test_a_retried_message_is_answered_once(h):
    event = ig_text(STRANGER, "هلا", h.clock, mid="aWdfZretry1")
    for _ in range(3):
        assert h.ig_post(ig_body(event)).status_code == 200
    assert len(h.ig.texts(STRANGER)) == 1
    assert set(h.ig.read) == {("aWdfZretry1", STRANGER)}  # «seen» names the conversation, not only the message


def test_an_odd_body_is_still_200(h):
    raw = b"not json"
    r = h.client.post("/instagram/webhook", content=raw, headers={"X-Hub-Signature-256": sign(raw, IG_SECRET)})
    assert r.status_code == 200
    assert h.ig_post({"object": "page", "entry": []}).status_code == 200
    assert h.ig.sent == []


def test_without_complete_settings_the_door_is_404_and_said_once(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    harness = Harness(tmp_path, ig_settings=InstagramSettings(token="ig-token", account_id=OWN))
    try:
        assert harness.client.get("/instagram/webhook", params={"hub.mode": "subscribe"}).status_code == 404
        assert harness.client.post("/instagram/webhook", content=b"{}").status_code == 404
        assert harness.channel.instagram is None
        lines = [r.getMessage() for r in caplog.records if "Instagram is off" in r.getMessage()]
        assert lines == ["Instagram is off: INSTAGRAM_APP_SECRET, INSTAGRAM_VERIFY_TOKEN missing "
                         "(/instagram/webhook is 404)"]
        assert "ig-token" not in caplog.text
        harness.say(ALICE, "اربط انستغرام")  # with the door closed, the words are not a command
        assert not any(re.search(r"\b\d{6}\b", t) for t in harness.graph.texts(ALICE))
    finally:
        harness.stop()


def test_a_test_app_with_a_fake_graph_never_reads_instagram_from_the_environment(tmp_path, monkeypatch):
    for name, value in {"INSTAGRAM_TOKEN": "t", "INSTAGRAM_APP_SECRET": "s", "INSTAGRAM_VERIFY_TOKEN": "v",
                        "INSTAGRAM_ACCOUNT_ID": "1"}.items():
        monkeypatch.setenv(name, value)
    harness = Harness(tmp_path, instagram=False)
    try:
        assert harness.channel.instagram is None
    finally:
        harness.stop()


def test_the_sender_is_scoped_and_never_a_phone():
    assert scoped("123") == "ig:123" and is_instagram("ig:123") and unscoped("ig:123") == "123"
    assert not is_instagram(ALICE) and unscoped(ALICE) == ALICE


# ---------------------------------------------------------------- linking


def test_an_unlinked_sender_is_told_how_to_link_even_when_its_id_looks_like_a_number(h):
    h.ig_say(ALICE, "شكد رصيدي")  # an Instagram id made of Alice's digits is still nobody
    [reply] = h.ig.texts(ALICE)
    assert reply == ig_texts.say("not_linked", "m", "iq", wa_link=ig_texts.wa_link(SERVICE, "iq"),
                                 phrase="اربط انستغرام", minutes=10)
    assert "https://wa.me/9647714899059?text=" in reply
    assert "500,000" not in reply and h.graph.sent == []
    h.ig_say(STRANGER, "what's my balance")
    assert h.ig.texts(STRANGER)[-1].startswith("Hello! To use your wallet from Instagram")


def test_link_code_on_whatsapp_then_linked_on_instagram_with_a_notice_on_whatsapp(h):
    code = h.code_for(ALICE)
    ask = h.graph.texts(ALICE)[-1]
    assert ask == ig_texts.say("link_code", "f", "iq", code=code, minutes=10)
    h.ig_say(SARA_IG, code)
    assert h.ig.texts(SARA_IG) == [ig_texts.say("link_ok", "f", "iq")]
    assert h.graph.texts(ALICE)[-1] == ig_texts.say("linked_notice", "f", "iq", phrase="افصل انستغرام")
    assert h.channel.instagram.linked(scoped(SARA_IG)) == h.uid(ALICE)
    h.ig_say(SARA_IG, code)  # a code works once: now it is just six digits from a linked account
    assert h.channel.instagram.linked(scoped(SARA_IG)) == h.uid(ALICE)
    h.ig.clear()
    h.ig_say(SARA_IG, "شكد رصيدي")
    assert "500,000" in h.ig.everything(SARA_IG)


def test_the_link_request_is_answered_in_english_too(h):
    h.say(ALICE, "link instagram")
    assert h.graph.texts(ALICE)[-1].startswith("Instagram link code: ")
    h.say(ALICE, "unlink instagram")
    assert h.graph.texts(ALICE)[-1] == ig_texts.say("not_linked_here", "f", "en")


def test_a_wrong_code_says_the_tries_left_and_five_lock_the_sender(h):
    code = h.code_for(ALICE)
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"
    h.ig_say(STRANGER, wrong)
    assert h.ig.texts(STRANGER)[-1] == ig_texts.say("wrong_code", "m", "iq", left=4)
    for _ in range(4):
        h.ig_say(STRANGER, wrong)
    assert h.ig.texts(STRANGER)[-1] == ig_texts.say("locked", "m", "iq", minutes=60)
    h.ig_say(STRANGER, code)  # locked: even the right code is refused
    assert h.ig.texts(STRANGER)[-1].startswith("انكتبت رموز غلط هواية")
    assert h.channel.instagram.linked(scoped(STRANGER)) is None
    h.ig_say(SARA_IG, code)  # another sender is not locked, and the code is still live
    assert h.channel.instagram.linked(scoped(SARA_IG)) == h.uid(ALICE)


def test_too_many_codes_in_an_hour(h):
    for _ in range(3):
        h.code_for(ALICE)
    h.say(ALICE, "اربط انستغرام")
    assert h.graph.texts(ALICE)[-1] == ig_texts.say("too_many_codes", "f", "iq", minutes=60)


def test_unlink_from_whatsapp_or_from_instagram(h):
    h.link(ALICE, SARA_IG)
    h.say(ALICE, "افصل انستغرام")
    assert h.graph.texts(ALICE)[-1] == ig_texts.say("unlinked", "f", "iq", phrase="اربط انستغرام")
    h.ig_say(SARA_IG, "شكد رصيدي")
    assert h.ig.texts(SARA_IG)[-1].startswith("هلا بيك! حتى تستخدم محفظتك من انستغرام")
    h.say(ALICE, "افصل انستغرام")
    assert h.graph.texts(ALICE)[-1] == ig_texts.say("not_linked_here", "f", "iq")
    h.link(ALICE, SARA_IG)
    h.ig_say(SARA_IG, "افصل انستغرام")
    assert h.ig.texts(SARA_IG)[-1] == ig_texts.say("unlinked", "f", "iq", phrase="اربط انستغرام")
    assert h.channel.instagram.linked(scoped(SARA_IG)) is None


def test_a_linked_instagram_chat_asking_to_link_is_told_it_is_linked(h):
    h.link(ALICE, SARA_IG)
    h.ig_say(SARA_IG, "اربط انستغرام")
    assert h.ig.texts(SARA_IG) == [ig_texts.say("already_linked", "f", "iq")]


def test_sign_up_and_the_identity_document_stay_on_whatsapp(h):
    h.link(ALICE, SARA_IG)
    h.ig_tap(SARA_IG, "m:verify:start", "وثّق حسابي")  # the button under «حسابي»
    assert h.ig.texts(SARA_IG) == [ig_texts.say("whatsapp_only", "f", "iq", wa_link="https://wa.me/" + SERVICE)]
    h.ig_say(STRANGER, "اريد افتح حساب")
    assert "سجل من واتساب أول" in h.ig.texts(STRANGER)[-1]


# ---------------------------------------------------------------- the same account, two chats


def test_a_linked_instagram_user_pays_a_bill_end_to_end(h):
    h.link(ALICE, SARA_IG)
    h.ig_say(SARA_IG, "ادفع المولدة")
    card, why = h.ig.templates(SARA_IG)[-2:]
    assert "مولدة الحجي جاسم" in card["text"] and "45,000" in card["text"]
    taps = buttons(card)
    assert list(taps) == ["إي، ادفع", "غيّر", "لا، بطّلت"]
    assert all(payload.split(":")[0] in ("confirm", "change", "cancel") for payload in taps.values())
    assert buttons(why) == {"شلون تأكدت؟": "opt_why:" + taps["إي، ادفع"].split(":", 1)[1]}  # tied to this card
    h.ig_say(SARA_IG, PIN)  # a PIN before the tap is never read
    assert h.balance(ALICE) == 500_000
    h.ig_tap(SARA_IG, taps["إي، ادفع"], "إي، ادفع")
    assert h.ig.texts(SARA_IG)[-1] == wa_texts.say("ask_pin", "f", "iq")
    assert h.balance(ALICE) == 500_000  # the tap alone moves nothing
    h.ig_say(SARA_IG, "0000")
    assert h.ig.texts(SARA_IG)[-1].endswith(wa_texts.say("wrong_pin", "f", "iq"))
    h.ig_say(SARA_IG, PIN)
    receipt = h.ig.everything(SARA_IG)
    assert "فاتورة: مولدة الحجي جاسم، عن شهر تشرين الأول 2026" in receipt
    assert "الرصيد بعد الدفع: 455,000 دينار" in receipt
    assert h.balance(ALICE) == 455_000
    h.ig_tap(SARA_IG, taps["إي، ادفع"], "إي، ادفع")  # the same card again: paid once
    assert h.balance(ALICE) == 455_000
    assert h.graph.sent == []  # nothing went to the WhatsApp chat
    h.say(ALICE, "شكد رصيدي")  # the same account on WhatsApp
    assert "455,000" in h.graph.everything(ALICE)
    assert not any(is_instagram(to) or to == SARA_IG for to in (s["to"] for s in h.graph.sent))


def test_the_two_chats_keep_their_own_card_and_pin_step(h):
    h.link(ALICE, SARA_IG)
    h.say(ALICE, "دز لهادي خمسين الف")
    wa_card = h.graph.last_card(ALICE)
    h.tap(ALICE, kit.card_buttons(wa_card)["إي، ادفع"])  # the WhatsApp card waits for its PIN
    h.ig_say(SARA_IG, PIN)  # typed in the other chat: never the WhatsApp card's PIN
    assert h.balance(ALICE) == 500_000
    assert h.ig.texts(SARA_IG)[-1] == wa_texts.say("pin_no_card", "f", "iq")
    h.say(ALICE, PIN)
    assert h.balance(ALICE) == 449_750  # 50,000 and its fee, once
    assert "وصل" in h.graph.everything(ALICE)


def test_waiting_notices_arrive_in_the_instagram_chat(h):
    h.link(BOB, ALI_IG)
    h.clock.advance(hours=25)  # Bob's WhatsApp window (opened by asking for the code) is closed
    h.say(ALICE, "دز 10 الاف لـ 07700000002")
    h.tap(ALICE, kit.card_buttons(h.graph.last_card(ALICE))["إي، ادفع"])
    h.say(ALICE, PIN)
    assert h.graph.texts(BOB) == []  # Bob never wrote on WhatsApp: no window there
    h.ig_say(ALI_IG, "شكد رصيدي")
    first = h.ig.texts(ALI_IG)[0]
    assert "وصلك 10,000 دينار من سارة ك. (…0001)." in first
    h.ig_say(ALI_IG, "شكد رصيدي")
    assert sum("وصلك 10,000" in t for t in h.ig.texts(ALI_IG)) == 1  # delivered once
    assert h.graph.texts(BOB) == []


def test_a_voice_note_on_instagram_is_answered_in_writing_and_nothing_is_spoken(tmp_path):
    core = Speaking()
    harness = Harness(tmp_path, core=core)
    try:
        harness.link(ALICE, SARA_IG)
        url = "https://lookaside.fbsbx.com/ig_messaging_cdn/?asset_id=7&signature=x"
        harness.ig.media_files[url] = (b"\x00\x00\x00 ftypM4A ", "audio/mp4")
        for _ in range(2):
            assert harness.ig_post(ig_body(ig_audio(SARA_IG, url, harness.clock))).status_code == 200
        everything = harness.ig.everything(SARA_IG)
        assert core.said == []  # no speech bought for a chat that cannot play it
        assert everything.count(ig_texts.say("voice_not_here", "f", "iq")) == 1  # said once
        assert harness.ig.templates(SARA_IG)  # the card, as text with its buttons
    finally:
        harness.stop()


# ---------------------------------------------------------------- «شلون تأكدت؟» under the card


def test_the_explain_button_sits_under_the_whatsapp_card_and_leaves_it_open(h):
    h.say(ALICE, "ادفع المولدة")
    card = h.graph.last_card(ALICE)
    assert list(kit.card_buttons(card)) == ["إي، ادفع", "غيّر", "لا، بطّلت"]  # the card keeps its three
    [why] = h.graph.whys(ALICE)
    assert why["body"]["text"] == wa_texts.say("card_why_body", "f", "iq")
    why_id = kit.card_buttons(why)["شلون تأكدت؟"]
    assert why_id == "opt_why:" + kit.card_buttons(card)["إي، ادفع"].split(":", 1)[1]  # the quote id of this card
    assert h.graph.all_kinds(ALICE)[-2:] == ["interactive", "why"]  # right under the card
    h.tap(ALICE, why_id, "شلون تأكدت؟")
    explained = h.graph.texts(ALICE)[-1]
    assert explained.startswith(wa_texts.say("card_why_title", "f", "iq"))
    assert "•" in explained
    assert len(h.graph.cards(ALICE)) == 1  # nothing new to tap: the card above is still the one
    h.tap(ALICE, kit.card_buttons(card)["إي، ادفع"])
    h.tap(ALICE, why_id, "شلون تأكدت؟")  # during the PIN step too: the step stays open
    h.say(ALICE, PIN)
    assert h.balance(ALICE) == 455_000


def test_the_explain_button_in_english_and_its_title_fits(h):
    h.say(ALICE, "send 50000 to Hadi")
    [why] = h.graph.whys(ALICE)
    assert why["body"]["text"] == "Want to see what I checked before this card?"
    [why_id] = kit.card_buttons(why).values()
    assert list(kit.card_buttons(why)) == ["How did you check?"] and why_id.startswith("opt_why:")
    for lang in ("iq", "msa", "en"):
        assert len(render("why_button", "f", lang)) <= 20
    h.tap(ALICE, why_id, "How did you check?")
    assert h.graph.texts(ALICE)[-1].startswith("Here is what I checked before this card:")
    h.say(ALICE, "why")  # typed, it still explains the last request
    assert h.graph.texts(ALICE)[-1].startswith("Here is what I checked on your last request:")


def test_a_card_sent_again_after_tap_first_brings_no_second_explain_button(h):
    h.say(ALICE, "ادفع المولدة")
    h.say(ALICE, PIN)  # «tap first», and the card again
    assert len(h.graph.cards(ALICE)) == 2 and len(h.graph.whys(ALICE)) == 1


def test_the_explain_button_can_be_switched_off(tmp_path, monkeypatch):
    monkeypatch.setenv("CARD_WHY_BUTTON", "0")
    harness = Harness(tmp_path)
    try:
        harness.say(ALICE, "ادفع المولدة")
        assert harness.graph.cards(ALICE) and harness.graph.whys(ALICE) == []
        harness.say(ALICE, "ليش")  # typing it still works
        assert harness.graph.texts(ALICE)[-1].startswith(render("why_title", "f", "iq"))
    finally:
        harness.stop()


def test_the_explain_button_on_instagram(h):
    h.link(ALICE, SARA_IG)
    h.ig_say(SARA_IG, "ادفع المولدة")
    card, why = h.ig.templates(SARA_IG)[-2:]
    assert why["text"] == wa_texts.say("card_why_body", "f", "iq")
    h.ig_tap(SARA_IG, buttons(why)["شلون تأكدت؟"], "شلون تأكدت؟")
    assert h.ig.texts(SARA_IG)[-1].startswith(wa_texts.say("card_why_title", "f", "iq"))
    h.ig_tap(SARA_IG, buttons(card)["إي، ادفع"], "إي، ادفع")  # the card was left open
    h.ig_say(SARA_IG, PIN)
    assert h.balance(ALICE) == 455_000


# ---------------------------------------------------------------- the door is the route, never the sender's text


def test_a_whatsapp_door_message_cannot_pose_as_an_instagram_chat(tmp_path):
    harness = Harness(tmp_path, app_secret="", allow_unsigned=True)  # even with the WhatsApp door unsigned
    try:
        harness.link(ALICE, SARA_IG)
        harness.say(scoped(SARA_IG), "شكد رصيدي")  # a forged WhatsApp message «from» the linked Instagram chat
        harness.say(scoped(SARA_IG), "ادفع المولدة")
        assert harness.ig.sent == [] and harness.graph.sent == []  # silence: no turn ran on Alice's account
        assert harness.balance(ALICE) == 500_000
        forged = Inbound("wamid.forged", scoped(SARA_IG), True, "text", text="هلا")
        assert harness.channel.accept([forged]) == 0  # the door defaults to WhatsApp
        plain = Inbound("aWdfZplain", SARA_IG, False, "text", text="هلا")
        assert harness.channel.accept([plain], door="instagram") == 0  # and an Instagram item is always scoped
    finally:
        harness.stop()


# ---------------------------------------------------------------- the allow-list on the Instagram door


def test_with_an_allow_list_an_unlinked_instagram_stranger_gets_silence_but_a_code_still_links(tmp_path):
    harness = Harness(tmp_path, allowed_numbers=frozenset({ALICE}))
    try:
        harness.say("9647700009999", "هلا")
        assert harness.graph.texts("9647700009999") == []  # the WhatsApp stranger: silence
        harness.ig_say(STRANGER, "هلا")
        harness.ig_say(STRANGER, "شكد رصيدي")
        assert harness.ig.texts(STRANGER) == []  # the Instagram stranger: the same silence
        code = harness.code_for(ALICE)
        harness.ig_say(STRANGER, wrong_code(code))  # a mistyped code is still answered, so a real user knows
        assert harness.ig.texts(STRANGER) == [ig_texts.say("wrong_code", "m", "iq", left=4)]
        harness.ig_say(SARA_IG, code)
        assert harness.channel.instagram.linked(scoped(SARA_IG)) == harness.uid(ALICE)
        harness.ig_say(SARA_IG, "شكد رصيدي")
        assert "500,000" in harness.ig.everything(SARA_IG)
    finally:
        harness.stop()


def test_a_linked_chat_of_an_account_off_the_allow_list_gets_silence(tmp_path):
    harness = Harness(tmp_path)
    try:
        harness.link(BOB, ALI_IG)
        harness.channel.settings = replace(harness.channel.settings, allowed_numbers=frozenset({ALICE}))
        harness.ig_say(ALI_IG, "شكد رصيدي")
        assert harness.ig.sent == []
    finally:
        harness.stop()


def test_an_unlinked_strangers_voice_note_is_never_downloaded(tmp_path):
    harness = Harness(tmp_path, core=Speaking())
    try:
        url = "https://lookaside.fbsbx.com/ig_messaging_cdn/?asset_id=9&signature=x"
        harness.ig.media_files[url] = (b"\x00\x00\x00 ftypM4A ", "audio/mp4")
        assert harness.ig_post(ig_body(ig_audio(STRANGER, url, harness.clock))).status_code == 200
        assert harness.ig.fetched == []  # nothing fetched for a sender with no account behind it
        assert harness.ig.texts(STRANGER)[-1].startswith("هلا بيك! حتى تستخدم محفظتك من انستغرام")
        harness.link(ALICE, SARA_IG)
        assert harness.ig_post(ig_body(ig_audio(SARA_IG, url, harness.clock))).status_code == 200
        assert harness.ig.fetched == [url]  # a linked chat's voice note is heard as before
    finally:
        harness.stop()


# ---------------------------------------------------------------- the flood brake


def test_twenty_throwaway_accounts_cannot_switch_linking_off_and_the_admin_is_told_once(tmp_path):
    manager = "9647700000099"
    harness = Harness(tmp_path, manager=manager)
    try:
        code = harness.code_for(ALICE)
        for n in range(20):  # 20 senders x 5 wrong codes: the old brake then refused everyone for an hour
            for _ in range(5):
                harness.ig_say(f"13000000000{n:05d}", wrong_code(code))
        assert harness.client.get("/healthz").json()["instagram_linking_paused"] is True
        flood = [t for t in harness.graph.texts(manager) if t.startswith("انستغرام:")]
        assert flood == [ig_texts.say("manager_link_flood", "m", "iq", count=100, minutes=60)]  # once, not 100 times
        assert code not in harness.graph.everything(manager)
        harness.ig_say(SARA_IG, code)  # the real user, with a live code, links all the same
        assert harness.channel.instagram.linked(scoped(SARA_IG)) == harness.uid(ALICE)
        assert harness.ig.texts(SARA_IG) == [ig_texts.say("link_ok", "f", "iq")]
    finally:
        harness.stop()


def test_the_flood_threshold_comes_from_the_settings(tmp_path):
    settings = InstagramSettings(token="ig-token", app_secret=IG_SECRET, verify_token=IG_VERIFY, account_id=OWN,
                                 link_flood_per_hour=3)
    harness = Harness(tmp_path, ig_settings=settings)
    try:
        assert harness.channel.instagram.links.global_wrong_per_hour == 3
        assert harness.client.get("/healthz").json()["instagram_linking_paused"] is False
    finally:
        harness.stop()
    assert InstagramSettings.from_env({"INSTAGRAM_LINK_FLOOD_PER_HOUR": "0"}).link_flood_per_hour == 0
    with pytest.raises(ValueError):
        InstagramSettings.from_env({"INSTAGRAM_LINK_FLOOD_PER_HOUR": "lots"})


# ---------------------------------------------------------------- the PIN stays on WhatsApp


def test_changing_or_resetting_the_pin_from_instagram_is_sent_to_whatsapp(h):
    h.link(ALICE, SARA_IG)
    only = ig_texts.say("whatsapp_only", "f", "iq", wa_link="https://wa.me/" + SERVICE)
    h.ig_say(SARA_IG, "غير الرمز")
    h.ig_say(SARA_IG, PIN)  # what would have been the current PIN is not read as one
    h.ig_say(SARA_IG, "8642")
    h.ig_say(SARA_IG, "نسيت الرمز")
    h.ig_tap(SARA_IG, "m:forgot:yes", "إي، اطلب")  # an old button, or a forged postback
    texts = h.ig.texts(SARA_IG)
    assert texts[0] == only and texts[-2:] == [only, only]
    assert not any("تغيّر الرمز" in t or "انطلب" in t for t in texts)
    assert h.channel.wallet.check_pin(h.uid(ALICE), PIN) == "ok"  # the PIN is what it was
    assert not [r for r in h.channel.accounts.open_requests() if r.get("kind") == "pin_reset_requested"
                or "pin_reset" in json.dumps(r, default=str)]
    h.say(ALICE, "غير الرمز")  # on WhatsApp the step is there as before
    assert h.graph.texts(ALICE)[-1] != only and h.graph.texts(ALICE)


def test_change_pin_said_in_english_on_instagram_is_answered_in_english(h):
    h.link(ALICE, SARA_IG)
    h.ig_say(SARA_IG, "change my pin")
    assert h.ig.texts(SARA_IG) == [ig_texts.say("whatsapp_only", "f", "en", wa_link="https://wa.me/" + SERVICE)]


# ---------------------------------------------------------------- the link is checked under the account's lock


def _slow_whatsapp(channel, seconds: float = 0.3):
    original = channel._handle

    def slowed(rt, batch):
        if not rt.via_instagram:
            time.sleep(seconds)
        return original(rt, batch)

    channel._handle = slowed


def _ig_post_nowait(h, sender: str, text: str) -> None:
    raw = json.dumps(ig_body(ig_text(sender, text, h.clock)), ensure_ascii=False).encode()
    assert h.client.post("/instagram/webhook", content=raw, headers={
        "Content-Type": "application/json", "X-Hub-Signature-256": sign(raw, IG_SECRET)}).status_code == 200


def test_an_instagram_pin_queued_behind_an_unlink_moves_no_money(h):
    h.link(ALICE, SARA_IG)
    h.ig_say(SARA_IG, "ادفع المولدة")
    h.ig_tap(SARA_IG, buttons(h.ig.templates(SARA_IG)[-2])["إي، ادفع"], "إي، ادفع")
    _slow_whatsapp(h.channel)
    assert kit.post(h.client, kit.webhook(kit.text(ALICE, "افصل انستغرام", h.clock))).status_code == 200
    time.sleep(0.1)  # the unlink holds Alice's turn now
    _ig_post_nowait(h, SARA_IG, PIN)  # the PIN waits for that turn, its link read before the wait
    assert h.channel.join(20)
    assert h.graph.texts(ALICE)[-1] == ig_texts.say("unlinked", "f", "iq", phrase="اربط انستغرام")
    assert h.channel.instagram.linked(scoped(SARA_IG)) is None
    assert h.balance(ALICE) == 500_000  # cut at once: the queued PIN paid nothing
    assert h.ig.texts(SARA_IG)[-1].startswith("هلا بيك! حتى تستخدم محفظتك من انستغرام")
    h.link(ALICE, SARA_IG)  # linked again: the old card and its PIN step are gone, not waiting
    h.ig_say(SARA_IG, PIN)
    assert h.balance(ALICE) == 500_000
    assert h.ig.texts(SARA_IG)[-1] == wa_texts.say("pin_no_card", "f", "iq")


def test_a_balance_read_queued_behind_an_unlink_is_not_answered(h):
    h.link(ALICE, SARA_IG)
    _slow_whatsapp(h.channel)
    assert kit.post(h.client, kit.webhook(kit.text(ALICE, "افصل انستغرام", h.clock))).status_code == 200
    time.sleep(0.1)
    _ig_post_nowait(h, SARA_IG, "شكد رصيدي")
    assert h.channel.join(20)
    assert "500,000" not in h.ig.everything(SARA_IG)


def test_unlinking_from_instagram_ends_the_burst_there(h):
    h.link(ALICE, SARA_IG)
    h.ig_post(ig_body(ig_text(SARA_IG, "افصل انستغرام", h.clock), ig_text(SARA_IG, "شكد رصيدي", h.clock)))
    assert h.channel.instagram.linked(scoped(SARA_IG)) is None
    assert ig_texts.say("unlinked", "f", "iq", phrase="اربط انستغرام") in h.ig.everything(SARA_IG)
    assert "500,000" not in h.ig.everything(SARA_IG)


def test_a_newer_link_closes_the_old_instagram_chat(h):
    h.link(ALICE, SARA_IG)
    h.ig_say(SARA_IG, "ادفع المولدة")
    h.ig_tap(SARA_IG, buttons(h.ig.templates(SARA_IG)[-2])["إي، ادفع"], "إي، ادفع")
    h.link(ALICE, ALI_IG)  # the same wallet from another Instagram account: the newest link wins
    h.ig_say(SARA_IG, PIN)
    assert h.balance(ALICE) == 500_000
    assert h.ig.texts(SARA_IG)[-1].startswith("هلا بيك! حتى تستخدم محفظتك من انستغرام")


def test_one_turn_of_one_account_at_a_time_across_both_chats(h):
    h.link(ALICE, SARA_IG)
    original = h.channel._handle
    active: dict[str, int] = {}
    most = [0]
    guard = threading.Lock()

    def counted(rt, batch):
        with guard:
            active[rt.user_id] = active.get(rt.user_id, 0) + 1
            most[0] = max(most[0], active[rt.user_id])
        try:
            time.sleep(0.2)
            return original(rt, batch)
        finally:
            with guard:
                active[rt.user_id] -= 1

    h.channel._handle = counted
    assert kit.post(h.client, kit.webhook(kit.text(ALICE, "شكد رصيدي", h.clock))).status_code == 200
    _ig_post_nowait(h, SARA_IG, "شكد رصيدي")
    assert h.channel.join(20)
    assert most[0] == 1  # the two chats of one account never ran a turn together
    assert "500,000" in h.graph.everything(ALICE) and "500,000" in h.ig.everything(SARA_IG)


# ---------------------------------------------------------------- «شلون تأكدت؟» explains its own card


def test_the_explain_button_explains_its_card_and_never_a_later_request(h):
    h.say(ALICE, "ادفع المولدة")
    [old_why] = h.graph.whys(ALICE)
    old_id = kit.card_buttons(old_why)["شلون تأكدت؟"]
    h.tap(ALICE, old_id, "شلون تأكدت؟")
    card_lines = h.graph.texts(ALICE)[-1]
    assert card_lines.startswith(wa_texts.say("card_why_title", "f", "iq")) and "•" in card_lines
    h.say(ALICE, "شكد رصيدي")  # a new request: the card is voided
    h.tap(ALICE, old_id, "شلون تأكدت؟")  # the old card's button, scrolled back to
    assert h.graph.texts(ALICE)[-1] == wa_texts.say("card_why_closed", "f", "iq")
    h.say(ALICE, "دز لهادي خمسين الف")
    new_id = kit.card_buttons(h.graph.whys(ALICE)[-1])["شلون تأكدت؟"]
    assert new_id != old_id
    h.tap(ALICE, old_id, "شلون تأكدت؟")  # still the old one: never the new card's checks
    assert h.graph.texts(ALICE)[-1] == wa_texts.say("card_why_closed", "f", "iq")
    h.tap(ALICE, new_id, "شلون تأكدت؟")
    assert h.graph.texts(ALICE)[-1].startswith(wa_texts.say("card_why_title", "f", "iq"))
    h.tap(ALICE, kit.card_buttons(h.graph.last_card(ALICE))["إي، ادفع"])  # the new card is still open
    h.say(ALICE, PIN)
    assert h.balance(ALICE) == 449_750


def test_the_explain_texts_exist_in_every_language():
    for lang in ("iq", "msa", "en"):
        for gender in ("f", "m"):
            for key in ("card_why_title", "card_why_closed"):
                said = wa_texts.say(key, gender, lang)
                assert said and chr(0x0686) not in said and chr(0x2014) not in said  # ج, never the Persian letter


def wrong_code(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"
