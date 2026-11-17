"""Regression tests for the WhatsApp channel review: the PIN never reaches the core, whatever comes
before or around it; the card survives small talk during the PIN step; the webhook's 403 contract
holds for non-ASCII input; and the smaller UX fixes (receipt on a second tap, thanks)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from channels.whatsapp import texts
from channels.whatsapp.store import sender_key
from channels.whatsapp.tests import kit
from channels.whatsapp.tests.kit import ALICE, PIN
from channels.whatsapp.tests.test_whatsapp_channel import Harness
from core.stub import StubCore

PAID = 500_000 - 50_250  # «دز لهادي خمسين الف» with its fee


class SpyCore(StubCore):
    def __init__(self):
        super().__init__()
        self.heard: list[str] = []

    def understand(self, text, context=None):
        self.heard.append(text)
        return super().understand(text, context)


@pytest.fixture
def spy(tmp_path):
    core = SpyCore()
    harness = Harness(tmp_path, core=core)
    yield harness, core
    harness.stop()


def _card_for_hadi(h):
    h.say(ALICE, "هلا")
    h.say(ALICE, "دز لهادي خمسين الف")
    return h.graph.last_card(ALICE)


def _no_pin_heard(core, pin=PIN):
    assert not any(pin in s for s in core.heard), core.heard


# ---------------------------------------------------------------- webhook: 403, never 500


def test_non_ascii_signature_header_is_403_not_500(tmp_path):
    h = Harness(tmp_path)
    try:
        with TestClient(h.app, raise_server_exceptions=False) as client:
            raw = json.dumps(kit.webhook()).encode()
            r = client.post("/webhook", content=raw,
                            headers={"Content-Type": "application/json",
                                     "X-Hub-Signature-256": "sha256=é".encode("latin-1")})
            assert r.status_code == 403, r.status_code
    finally:
        h.stop()


def test_non_ascii_verify_token_is_403_not_500(tmp_path):
    h = Harness(tmp_path)
    try:
        with TestClient(h.app, raise_server_exceptions=False) as client:
            r = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "هلا",
                                               "hub.challenge": "1"})
            assert r.status_code == 403, r.status_code
    finally:
        h.stop()


# ---------------------------------------------------------------- the PIN never reaches the core


def test_pin_after_a_short_text_never_reaches_the_core(spy):
    h, core = spy
    _card_for_hadi(h)
    h.tap(ALICE, "إي، ادفع")
    h.say(ALICE, "لحظة")
    assert "البطاقة بعدها مفتوحة" in h.graph.texts(ALICE)[-1]
    h.say(ALICE, PIN)
    _no_pin_heard(core)
    assert "لحظة" not in core.heard
    assert h.balance(ALICE) == PAID  # the card stayed open and the PIN paid it


def test_wait_and_pin_in_one_burst_pays_once_and_never_reaches_the_core(tmp_path):
    core = SpyCore()
    h = Harness(tmp_path, core=core, debounce_seconds=0.6)
    try:
        _card_for_hadi(h)
        h.tap(ALICE, "إي، ادفع")
        kit.post(h.client, kit.webhook(kit.text(ALICE, "تمام", h.clock), kit.text(ALICE, PIN, h.clock)))
        assert h.channel.join(20)
        _no_pin_heard(core)
        assert h.balance(ALICE) == PAID and len(h.transactions(ALICE)) == 1
    finally:
        h.stop()


def test_pin_typed_before_tap_never_reaches_core_or_changes_card(spy):
    h, core = spy
    card = _card_for_hadi(h)
    h.graph.clear()
    core.heard.clear()
    h.say(ALICE, PIN)  # the greeting gives the demo PIN out; people type it without tapping first
    _no_pin_heard(core)
    assert "دوسي «إي، ادفع» بالبطاقة أول" in h.graph.texts(ALICE)[-1]
    new = h.graph.last_card(ALICE)  # the same card again, under the hint
    assert "1,234" not in new["body"]["text"]
    assert new["action"]["buttons"][0]["reply"]["id"] == card["action"]["buttons"][0]["reply"]["id"]
    h.tap(ALICE, "إي، ادفع")
    h.say(ALICE, PIN)
    assert h.balance(ALICE) == PAID


def test_demo_pin_inside_a_sentence_is_kept_from_the_core(spy):
    h, core = spy
    _card_for_hadi(h)
    h.say(ALICE, f"الرمز {PIN}")  # card open, not tapped
    _no_pin_heard(core)
    h.tap(ALICE, "إي، ادفع")
    h.say(ALICE, f"الرمز {PIN}")  # tapped: read as the PIN
    _no_pin_heard(core)
    assert h.balance(ALICE) == PAID


def test_pin_after_a_new_request_voided_the_tapped_card_is_refused(spy):
    h, core = spy
    _card_for_hadi(h)
    h.tap(ALICE, "إي، ادفع")
    h.say(ALICE, "شكد رصيدي")  # a real new request: the orchestrator voids the card
    h.say(ALICE, PIN)
    _no_pin_heard(core)
    assert "ما تشتغل بعد" in h.graph.texts(ALICE)[-1]
    assert h.balance(ALICE) == 500_000


def test_a_bare_pin_with_no_card_and_no_question_is_kept_from_the_core(spy):
    h, core = spy
    h.say(ALICE, "هلا")
    h.say(ALICE, PIN)
    _no_pin_heard(core)
    assert "ماكو بطاقة دفع مفتوحة" in h.graph.texts(ALICE)[-1]


def test_digits_answering_a_question_still_reach_the_orchestrator(spy):
    h, _ = spy
    h.say(ALICE, "هلا")
    h.say(ALICE, "دز لهادي")
    h.say(ALICE, "50000")  # «شكد؟» -> an amount, not a PIN
    assert "50,000" in h.graph.last_card(ALICE)["body"]["text"]


def test_forgot_pin_keeps_card_open(spy):
    h, core = spy
    _card_for_hadi(h)
    h.tap(ALICE, "إي، ادفع")
    h.say(ALICE, "0000")
    h.say(ALICE, "نسيت الرمز")
    reminder = h.graph.texts(ALICE)[-1]
    assert PIN not in reminder and "ما نگدر نشوفه" in reminder and "البطاقة بعدها مفتوحة" in reminder
    h.say(ALICE, PIN)
    assert h.balance(ALICE) == PAID
    _no_pin_heard(core, "0000")


# ---------------------------------------------------------------- smaller UX fixes


def test_second_tap_on_paid_card_gives_receipt_not_a_missing_log(spy):
    h, _ = spy
    card = _card_for_hadi(h)
    h.tap(ALICE, "إي، ادفع")
    h.say(ALICE, PIN)
    h.graph.clear()
    h.tap(ALICE, "إي، ادفع", card)
    said = h.graph.everything(ALICE)
    assert "سجل العمليات" not in said
    assert "اندفعت قبل، مرة وحدة بس" in said and "رقم العملية" in said
    assert h.balance(ALICE) == PAID


def test_thanks_after_a_receipt_gets_a_short_answer_and_skips_the_core(spy):
    h, core = spy
    _card_for_hadi(h)
    h.tap(ALICE, "إي، ادفع")
    h.say(ALICE, PIN)
    core.heard.clear()
    answers = {texts.say(key, "f") for key in ("thanks", "thanks_2", "thanks_3")}  # said in turn, never twice
    said = []
    for word in ("شكرا", "تسلمين", "عاشت ايدك", "👍"):
        h.say(ALICE, word)
        said.append(h.graph.texts(ALICE)[-1])
        assert said[-1] in answers
    assert said[0] == texts.say("thanks", "f") and all(a != b for a, b in zip(said, said[1:]))
    assert core.heard == []


def test_greeting_with_small_talk_is_only_greeted_once(spy):
    h, core = spy
    h.say(ALICE, "مرحبا شلونج")
    assert len(h.graph.texts(ALICE)) == 1  # the greeting, not the greeting plus «هلا بيج»
    assert core.heard == []


def test_min_jadid_never_wipes_the_wallet(spy):
    h, _ = spy
    _card_for_hadi(h)
    h.tap(ALICE, "إي، ادفع")
    h.say(ALICE, PIN)
    h.say(ALICE, "من جديد")
    assert h.balance(ALICE) == PAID and len(h.transactions(ALICE)) == 1


def test_sender_key_is_keyed_and_never_the_plain_hash(tmp_path):
    plain = sender_key(ALICE)
    assert sender_key(ALICE, "salt-a") != plain
    assert sender_key(ALICE, "salt-a") != sender_key(ALICE, "salt-b")
    h = Harness(tmp_path, sender_salt="pepper")
    try:
        h.say(ALICE, "هلا")
        assert h.channel.store.sender(sender_key(ALICE, "pepper"))["user_id"] == "u1"
        assert h.channel.store.sender(plain) is None
        assert not (tmp_path / "whatsapp" / "wallets").exists()  # no per-sender wallet files any more
    finally:
        h.stop()


def test_channel_texts_fit_and_have_no_persian_letter():
    for text in texts.all_texts():
        assert "چ" not in text and "انتي داخلة" not in text


# ---------------------------------------------------------------- «what can you do?» with a card open


@pytest.mark.parametrize("asked", ["شنو تگدر تسوي؟", "what can you do?"])
def test_what_can_you_do_with_a_card_open_shows_the_help_and_keeps_the_card(tmp_path, asked):
    from accounts import texts as account_texts
    h = Harness(tmp_path)
    try:
        h.channel.menu_on_hello = True
        card = _card_for_hadi(h)
        h.graph.clear()
        h.say(ALICE, asked)
        everything = h.graph.everything(ALICE)
        lang = "en" if asked.startswith("what") else "iq"
        assert account_texts.say("help", "f", lang).splitlines()[0] in everything
        assert h.graph.lists(ALICE), "the menu to tap comes after the help"
        assert "لغيت البطاقة" not in everything and "cancelled" not in everything.lower()
        st = h.channel._runtime(h.channel.sender_key(ALICE), h.uid(ALICE)).session.state(h.uid(ALICE))
        assert st.card is not None and st.card.quote.get("id")
        h.tap(ALICE, "إي، ادفع", card)  # the same card still pays, with the PIN
        h.say(ALICE, PIN)
        assert h.balance(ALICE) == PAID and len(h.transactions(ALICE)) == 1
    finally:
        h.stop()


def test_a_menu_row_tapped_with_a_card_open_still_voids_the_card(tmp_path):
    h = Harness(tmp_path)
    try:
        h.channel.menu_on_hello = True
        _card_for_hadi(h)
        h.say(ALICE, "شنو تگدر تسوي؟")
        rows = {row["title"]: row["id"] for part in h.graph.last_list(ALICE)["action"]["sections"]
                for row in part["rows"]}
        h.graph.clear()
        balance_row = next(rid for title, rid in rows.items() if "رصيد" in title)
        h.send(kit.list_tap(ALICE, balance_row, "x", h.clock))
        assert "لغيت البطاقة" in h.graph.everything(ALICE)
        st = h.channel._runtime(h.channel.sender_key(ALICE), h.uid(ALICE)).session.state(h.uid(ALICE))
        assert st.card is None and h.balance(ALICE) == 500_000
    finally:
        h.stop()


# ---------------------------------------------------------------- «لـ أحمد»: the dative word standing apart


def test_a_detached_dative_is_not_part_of_the_name_shown():
    from agent.session import _display
    assert _display("لـ أحمد") == "أحمد" and _display("ل أحمد") == "أحمد"
    assert _display("لـأحمد") == "أحمد"  # glued with a tatweel: no «ـأحمد» left behind
    assert _display("ليث") == "ليث" and _display("لمى") == "لمى"  # short names keep their ل
    assert _display("to أحمد") == "أحمد"


class Scripted(StubCore):
    """The stand-in core, but its reading of a message is the one given."""

    def __init__(self, intent):
        super().__init__()
        self.intent = intent

    def understand(self, text, context=None):
        return self.intent


def test_a_detached_dative_still_finds_the_saved_person(tmp_path):
    from core.interface import Intent
    h = Harness(tmp_path, core=Scripted(Intent("transfer", payee_mention="لـ هادي", amount_text="5000")))
    try:
        h.say(ALICE, "send لـ هادي 5000")
        card = h.graph.last_card(ALICE)
        assert "هادي" in card["body"]["text"] and "لـ هادي" not in h.graph.everything(ALICE)
    finally:
        h.stop()


def test_a_demo_voice_note_to_someone_unsaved_names_him_without_the_dative(tmp_path):
    from core.interface import Intent

    from channels.whatsapp.tests.test_language_and_voice import Speaking

    class Heard(Speaking):
        def understand(self, text, context=None):
            return Intent("transfer", payee_mention="لـ أحمد", amount_text="fifty thousand")

    heard = "send لـ أحمد fifty thousand please"
    core = Heard(heard=heard)
    who = "9647700000003"  # a new visitor: a demo account, with no أحمد saved
    h = Harness(tmp_path, core=core, signup_mode="demo")
    try:
        h.say(who, "هلا")
        h.graph.clear()
        h.graph.media_files["m1"] = (b"OggS-in", "audio/ogg")
        h.send(kit.voice(who, "m1", h.clock))
        # what we wrote and said back (the echo of the user's own words aside)
        ours = h.graph.everything(who).replace(heard, "") + " " + " ".join(text for text, _ in core.said)
        assert "أحمد" in ours and "لـ أحمد" not in ours, ours
    finally:
        h.stop()
