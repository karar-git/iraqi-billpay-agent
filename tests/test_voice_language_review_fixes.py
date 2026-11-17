"""Review fixes for voice and language: PINs in any language, speech made for the ear, the open card
shown again in the current language, the web chat's own voice switch and the stand-in core's English."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import pins, speech
from agent.session import Reply, Session
from agent.wallet_client import WalletClient
from core.stub import StubCore
from payments.amounts import find_amount
from payments.quote import amount_in_words
from wallet.api import create_app

ROOT = Path(__file__).resolve().parent.parent


class Spy(StubCore):
    def __init__(self):
        self.seen: list[str] = []

    def understand(self, text, context=None):
        self.seen.append(text)
        return super().understand(text, context)


@pytest.fixture
def spied(tmp_path):
    seed = json.loads((ROOT / "data" / "wallet_seed.json").read_text(encoding="utf-8"))
    app = create_app(str(tmp_path / "w.db"), seed=seed)
    wallet = WalletClient.in_process(app)
    core = Spy()
    yield Session(wallet, core), core
    wallet.http.close()
    app.state.wallet.db.close()


# ---------------------------------------------------------------- PINs


@pytest.mark.parametrize("text", ["1234", "١٢٣٤", "1234.", " 12 34 ", "1234؟"])
def test_a_bare_pin_is_seen_with_the_punctuation_speech_to_text_adds(text):
    assert pins.looks_like_pin(text)


@pytest.mark.parametrize("text", ["my pin is 5678", "PIN: 5678", "passcode 5678", "الرقم السري 5678",
                                  "رمزي ٥٦٧٨", "الرمز 5678"])
def test_a_worded_pin_is_caught_in_every_language(text):
    assert pins.pin_in(text) == ("worded", "5678")


@pytest.mark.parametrize("text", ["دز لهادي 50000", "send 50000 to Hadi", "علي بن احمد 25000"])
def test_an_amount_in_a_request_is_not_a_pin(text):
    assert pins.pin_in(text) is None


def test_a_worded_pin_never_reaches_the_core_with_or_without_a_card(spied):
    session, core = spied
    reply = session.handle_text("u01", "my pin is 5678")
    assert core.seen == [] and reply.kind == "refuse"
    session.handle_text("u01", "send 50000 to Huda")
    core.seen.clear()
    reply = session.handle_text("u01", "PIN: 5678")
    assert core.seen == [] and reply.kind == "card"  # the card stays open


# ---------------------------------------------------------------- speech for the ear


def test_money_in_a_spoken_line_is_said_in_words_in_the_reply_language():
    assert speech.for_the_ear("رصيدج 404,750 دينار.", "iq") == f"رصيدج {amount_in_words(404_750, 'iq')}."
    assert speech.for_the_ear("Your balance is 50,000 dinars.", "en") == "Your balance is fifty thousand dinars."


def test_options_are_said_with_ordinals_and_no_tatweel():
    line = speech.for_the_ear("1) أحمد، 2) علي. دزّيتي لـ هادي", "iq")
    assert line == "الأول: أحمد، الثاني: علي. دزّيتي لهادي"


def test_a_pin_is_never_said_as_words_either():
    reply = Reply(["راح تدزين ألف وميتين واربعة وتلاثين دينار لـ هادي", "تمام"])
    assert speech.spoken_text(reply, never=("1234",)) == "تمام"
    reply = Reply(["You're sending 1,234 dinars to Hadi", "OK"], lang="en")
    assert speech.spoken_text(reply, never=("1234",)) == "OK"


def test_a_round_pin_does_not_mute_every_amount():
    reply = Reply(["رصيدج 2,000 دينار"])
    assert speech.spoken_text(reply, never=("2000",)) == ""  # the digits: dropped, as before
    reply = Reply(["رصيدج 50,000 دينار"])
    assert "خمسين ألف" in speech.spoken_text(reply, never=("2000",))


def test_a_card_with_a_warning_says_the_warning():
    from types import SimpleNamespace

    from payments.quote import QuoteDetails, render_card

    q = QuoteDetails("q1", "transfer", 50_000, fee=250, payee_id="c1", payee_name="هادي", phone="07700000001")
    for lang, generic in (("iq", "انتبهي للتنبيه"), ("en", "Please read the warning")):
        card = render_card(q, SimpleNamespace(gender="f", balance=None), ["first_time_payee"], lang=lang)
        assert card.flags and card.flags[0] in card.spoken and generic not in card.spoken
        spoken = speech.spoken_text(Reply([], card=card, lang=lang))
        assert "50,000" not in spoken and amount_in_words(50_000, lang) in spoken


# ---------------------------------------------------------------- the open card in the current language


def test_the_open_card_is_shown_again_in_the_language_the_user_switched_to(spied):
    session, _ = spied
    first = session.handle_text("u01", "send 50000 to Huda")
    assert first.card.lang == "en"
    again = session.handle_text("u01", "إي ادفع")
    assert again.lang == "iq" and again.card.lang == "iq" and again.quote_id == first.quote_id
    assert "You're sending" not in again.card.spoken
    assert [b.id for b in again.card.buttons] == [b.id for b in first.card.buttons]


# ---------------------------------------------------------------- MSA amounts


@pytest.mark.parametrize("n", [2_000, 2_000_000, 103_000, 110_000, 2_002_000, 404_000, 1_234_000])
def test_msa_and_iraqi_amount_words_read_back(n):
    for lang in ("iq", "msa"):
        found = find_amount(amount_in_words(n, lang))
        assert found is not None and found[0].value_iqd == n, (lang, amount_in_words(n, lang))


@pytest.mark.parametrize("text, value", [("ألفي دينار", 2_000), ("أحوّل ألفي دينار", 2_000),
                                         ("مليونا دينار", 2_000_000), ("خمسون ألفاً", 50_000),
                                         ("أريد ألفاً", 1_000)])
def test_msa_duals_and_accusatives(text, value):
    assert find_amount(text)[0].value_iqd == value


def test_a_lone_alfa_is_asked_about():
    found = find_amount("حوّل لهادي ألفا")
    assert found is not None and found[0].needs_question


# ---------------------------------------------------------------- web chat voice and the stand-in core


def test_the_web_chat_has_its_own_voice_switch():
    from web.service import web_voice_mode

    assert web_voice_mode({"VOICE_REPLY_MODE": "mirror"}) == "mirror"
    assert web_voice_mode({"VOICE_REPLY_MODE": "mirror", "WEB_VOICE_REPLY_MODE": "off"}) == "off"
    assert web_voice_mode({"WEB_VOICE_REPLY_MODE": "always"}) == "always"


def test_the_stand_in_reads_english_months_and_latin_amounts():
    core = StubCore()
    assert core.understand("pay the electricity bill for last month", {}).period == "previous"
    assert core.understand("pay this month's electricity", {}).period == "current"
    intent = core.understand("send 50k to Hadi", {})
    assert intent.action == "transfer" and intent.amount_text == "50k"
