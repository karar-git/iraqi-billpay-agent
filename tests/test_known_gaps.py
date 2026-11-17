"""The gaps found with new phrasings after the first release, each closed with a question or a warning.

- a bill typed with an amount that is not the amount due -> a question first, then one warning line on the card;
- «خمسين الف وعشرين» (50,020 or 70,000?) -> a question;
- a hedge after the number («ميتين بحدود») -> a question;
- two amounts joined by او / لو -> a question that offers both;
- a named month («مال شهر ستة») -> that month's bill, a clear "no bill for that month", or a question.
Also: the core is chosen by core/loader.py, and voice notes go through Session.handle_voice.
"""

from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from agent.session import Session
from core.loader import CoreLoadError, load_core
from core.stub import StubCore
from payments import amounts
from payments.messages import FAILURE_KEYS, MESSAGES
from payments.periods import months_named
from tests.test_session import PAY, PIN, SEED, UID, balance
from wallet.api import create_app

# The generator account gets an unpaid September bill too, so a named month has a real choice.
SEED_MONTHS = copy.deepcopy(SEED)
SEED_MONTHS["bills"].append(
    {"id": "b4", "account_id": "a1", "period": "2026-09", "amount_due": 40_000, "status": "unpaid"})


@pytest.fixture
def client():
    with TestClient(create_app(":memory:", seed=SEED_MONTHS)) as c:
        yield c


@pytest.fixture
def session(client):
    return Session(wallet=client, core=StubCore())


def quote_of(client, reply) -> dict:
    return client.get(f"/quotes/{reply.quote_id}").json()


# ---------------------------------------------------------------- the amount parser


@pytest.mark.parametrize("text", ["خمسين الف وعشرين", "50 الف و20", "الف وخمسين"])
def test_small_tail_after_alf_is_a_question(text):
    found = amounts.parse(text)
    assert found.needs_question and found.value_iqd is None and found.reason == amounts.AMBIGUOUS


@pytest.mark.parametrize("text, value", [("الف وخمسمية", 1_500), ("خمسين الف وخمسمية", 50_500),
                                         ("الف ونص", 1_500), ("مليون وخمسين", 1_050_000)])
def test_real_tails_still_parse(text, value):
    assert amounts.parse(text).value_iqd == value


@pytest.mark.parametrize("text", ["ميتين بحدود", "خمسين تقريبا", "مية الف شي", "حوالي خمسين"])
def test_hedge_before_or_after_is_a_question(text):
    assert amounts.parse(text).reason == amounts.APPROXIMATE
    assert amounts.hedged_phrases(text)


@pytest.mark.parametrize("text, value", [("خمطعش الف", 15_000), ("خمستعش الف", 15_000)])
def test_iraqi_teens_spellings(text, value):
    assert amounts.parse(text).value_iqd == value


def test_alternatives_with_an_iraqi_teen():
    pair = amounts.alternatives("حول لهادي عشرة او خمطعش الف")
    assert [a.value_iqd for a, _ in pair] == [10_000, 15_000]


def test_alternatives_need_an_or_word_between_them():
    pair = amounts.alternatives("دز لهادي خمسين او ستين")
    assert [(a.value_iqd, words) for a, words in pair] == [(50_000, "خمسين"), (60_000, "ستين")]
    assert amounts.alternatives("خمسين الف لو ستين الف")
    assert amounts.alternatives("دز لهادي خمسين ودز لزينب عشرين") == []  # two requests, not a choice
    assert amounts.alternatives("دز لهادي خمسين") == []


# ---------------------------------------------------------------- months


@pytest.mark.parametrize("text, months", [
    ("سدد الانترنت لشهر ستة", {6}), ("فاتورة حزيران", {6}), ("لشهر ٦", {6}), ("shahr 6", set()),
    ("تشرين الاول", {10}), ("كانون الثاني", {1}), ("مال تشرين", {10, 11}), ("شهر تسعة وشهر عشرة", {9, 10}),
    ("الشهر اللي فات", set()), ("دز لابوي خمسين", set()), ("دز لأحمد ستة الاف", set()), ("شهر 13", set()),
])
def test_months_named(text, months):
    assert months_named(text) == months


# ---------------------------------------------------------------- bills


def test_bill_with_a_different_amount_asks_then_warns_on_the_card(session, client):
    asked = session.handle_text(UID, "ادفع المولدة مال شهر عشرة ٢٥ الف")
    assert asked.kind == "ask" and asked.ask_field == "full_amount" and asked.card is None
    assert "45,000" in asked.text and "25,000" in asked.text and asked.offered_amount == 45_000
    reply = session.handle_text(UID, "إي")
    assert reply.kind == "card"
    assert quote_of(client, reply)["amount"] == 45_000  # the biller's amount, never the typed one
    assert reply.card.flags[0].startswith("انتبهي: المبلغ المطلوب 45,000 دينار، مو 25,000 دينار")
    assert reply.card.flag_codes[0] == "bill_amount_differs"
    assert "مو 25,000" in reply.card.text
    assert balance(client) == 500_000  # a card moves nothing


def test_bill_with_the_right_amount_has_no_warning(session):
    reply = session.handle_text(UID, "ادفع المولدة مال شهر عشرة 45 الف")
    assert reply.kind == "card" and "bill_amount_differs" not in reply.card.flag_codes


def test_bill_with_an_unclear_amount_says_the_amount_is_fixed(session):
    asked = session.handle_text(UID, "ادفع المولدة مال شهر عشرة حوالي خمسين")
    assert asked.kind == "ask" and asked.ask_field == "full_amount" and asked.card is None
    reply = session.handle_text(UID, "إي")
    assert reply.kind == "card"
    assert any("مبلغ الفاتورة ثابت، المطلوب 45,000 دينار" in line for line in reply.card.flags)


def test_typed_amount_that_matches_another_unpaid_bill_picks_it(session, client):
    # Two unpaid bills (September 40,000 and October 45,000), no month said: the typed 45 means October.
    reply = session.handle_text(UID, "ادفع المولدة ٤٥ الف")
    assert reply.kind == "card" and "bill_amount_differs" not in reply.card.flag_codes
    assert quote_of(client, reply)["target"]["period"] == "2026-10"
    fresh = Session(wallet=client, core=StubCore())
    asked = fresh.handle_text(UID, "سددلي المولدة بعشرين الف")
    assert asked.kind == "ask" and asked.offered_amount == 40_000  # no match: the oldest unpaid one
    other = fresh.handle_text(UID, "إي")
    assert other.kind == "card" and other.card.flag_codes[0] == "bill_amount_differs"
    assert quote_of(client, other)["target"]["period"] == "2026-09"


@pytest.mark.parametrize("text", ["ادفعلي فاتورة الكهرباء مال جاري", "ادفع مولدة جيراننا", "سدد نت صديقي"])
def test_someone_elses_bill_is_refused(session, client, text):
    reply = session.handle_text(UID, text)
    assert reply.kind == "refuse" and "شخص ثاني" in reply.text and "ما انخصم" in reply.text
    assert balance(client) == 500_000


def test_this_month_is_not_my_neighbour(session):
    reply = session.handle_text(UID, "ادفع المولدة مال الشهر الجاري")
    assert reply.kind == "card"


def test_named_month_picks_that_months_bill(session, client):
    october = session.handle_text(UID, "ادفع المولدة مال شهر عشرة")
    assert october.kind == "card" and quote_of(client, october)["target"]["period"] == "2026-10"
    september = session.handle_text(UID, "ادفع المولدة مال شهر تسعة")
    assert september.kind == "card" and quote_of(client, september)["target"]["period"] == "2026-09"
    done = session.handle_button(UID, september.quote_id, PAY, pin=PIN)
    assert done.state == "SUCCEEDED" and balance(client) == 500_000 - 40_000


def test_named_month_by_name_and_already_paid(session):
    reply = session.handle_text(UID, "ادفع الكهرباء مال أيلول")
    assert reply.kind == "refuse" and "مدفوعة من قبل" in reply.text and "أيلول 2026" in reply.text


def test_named_month_without_a_bill_says_so(session):
    reply = session.handle_text(UID, "ادفع المولدة مال شهر ستة")
    assert reply.kind == "refuse" and reply.card is None
    assert "ما لگيت فاتورة" in reply.text and "حزيران" in reply.text and "ما انخصم منج شي" in reply.text


def test_two_months_ask_which_one(session, client):
    reply = session.handle_text(UID, "ادفع المولدة مال تشرين")
    assert reply.kind == "ask" and reply.ask_field == "period" and reply.card is None
    assert "تشرين الأول 2026" in reply.text and "تشرين الثاني" in reply.text
    again = session.handle_text(UID, "مدري")  # no month in the answer: ask again
    assert again.kind == "ask" and again.ask_field == "period"
    picked = session.handle_text(UID, "الاول")
    assert picked.kind == "card" and quote_of(client, picked)["target"]["period"] == "2026-10"


def test_core_month_is_overridden_by_the_users_words(client):
    class WrongMonth(StubCore):
        def understand(self, text, context=None):
            intent = super().understand(text, context)
            intent.period = "current"  # the user said September
            return intent

    reply = Session(client, WrongMonth()).handle_text(UID, "ادفع المولدة مال شهر تسعة")
    assert reply.kind == "card" and quote_of(client, reply)["target"]["period"] == "2026-09"


# ---------------------------------------------------------------- transfers


def test_ambiguous_tail_after_alf_asks(session):
    reply = session.handle_text(UID, "دز لهادي خمسين الف وعشرين")
    assert reply.kind == "ask" and reply.ask_field == "amount" and reply.card is None


def test_hedge_after_the_number_asks(session):
    reply = session.handle_text(UID, "دز لهادي ميتين بحدود")
    assert reply.kind == "ask" and reply.ask_field == "amount" and "بالضبط" in reply.text


def test_two_amounts_ask_which_then_pick(session, client):
    reply = session.handle_text(UID, "دز لهادي خمسين او ستين")
    assert reply.kind == "ask" and reply.ask_field == "amount" and reply.card is None
    assert "50,000" in reply.text and "60,000" in reply.text
    card = session.handle_text(UID, "الثاني")
    assert card.kind == "card" and quote_of(client, card)["amount"] == 60_000


def test_two_amounts_answered_with_a_number(session, client):
    session.handle_text(UID, "دز لهادي خمسين لو ستين")
    card = session.handle_text(UID, "خمسين")
    assert card.kind == "card" and quote_of(client, card)["amount"] == 50_000


def test_a_core_pointing_at_one_of_two_amounts_still_asks(client):
    class PicksOne(StubCore):
        def understand(self, text, context=None):
            intent = super().understand(text, context)
            if intent.action == "transfer":
                intent.amount_text = "ستين"
            return intent

    reply = Session(client, PicksOne()).handle_text(UID, "دز لهادي خمسين او ستين")
    assert reply.kind == "ask" and reply.ask_field == "amount"


# ---------------------------------------------------------------- empty messages


@pytest.mark.parametrize("text", ["", "   ", "؟؟", "..."])
def test_empty_message_is_not_a_crash(session, text):
    assert StubCore().understand(text, {}).action == "chat"
    reply = session.handle_text(UID, text)
    assert reply.kind == "ask" and reply.card is None


# ---------------------------------------------------------------- messages


def test_unused_pressure_message_is_gone():
    assert "pressure" not in MESSAGES and "pressure" not in FAILURE_KEYS


# ---------------------------------------------------------------- choosing the core


def test_loader_defaults_to_the_stub(monkeypatch):
    monkeypatch.delenv("BILLPAY_CORE", raising=False)
    assert isinstance(load_core(), StubCore)


def test_loader_reads_the_environment(monkeypatch):
    monkeypatch.setenv("BILLPAY_CORE", "core.stub:StubCore")
    assert isinstance(load_core(), StubCore)


@pytest.mark.parametrize("spec", ["no_such_module_xyz:Core", "core.stub:Nothing", "core.stub", "payments.text:fold"])
def test_loader_refuses_a_bad_core(spec):
    with pytest.raises(CoreLoadError):
        load_core(spec)


def test_session_uses_the_loader(monkeypatch, client):
    monkeypatch.setenv("BILLPAY_CORE", "core.stub:StubCore")
    assert isinstance(Session(client).core, StubCore)


# ---------------------------------------------------------------- voice notes


class Hears(StubCore):
    def __init__(self, transcript="", fail=False):
        self.transcript, self.fail, self.calls = transcript, fail, []

    def transcribe(self, audio, mime, context):
        self.calls.append((audio, mime, context["user_id"]))
        if self.fail:
            raise RuntimeError("upstream down")
        return self.transcript


def test_voice_note_is_handled_like_text(client):
    core = Hears("دز لهادي خمسين الف")
    reply = Session(client, core).handle_voice(UID, b"OggS...", "audio/ogg")
    assert reply.kind == "card" and reply.heard == "دز لهادي خمسين الف"
    assert quote_of(client, reply)["amount"] == 50_000
    assert core.calls == [(b"OggS...", "audio/ogg", UID)]


def test_voice_note_without_a_voice_core(client):
    reply = Session(client, StubCore()).handle_voice(UID, b"x")
    assert reply.kind == "failed" and "الرسائل الصوتية" in reply.text and "ما انخصم" in reply.text


@pytest.mark.parametrize("core", [Hears(""), Hears(fail=True)])
def test_voice_note_not_heard_keeps_the_card(client, core):
    session = Session(client, core)
    card = session.handle_text(UID, "دز لهادي خمسين الف")
    reply = session.handle_voice(UID, b"x")
    assert "ما سمعت" in reply.text and reply.card is not None and reply.quote_id == card.quote_id
    assert client.get(f"/quotes/{card.quote_id}").json()["state"] == "QUOTED"


def test_second_tap_after_payment_says_it_was_paid_once(session, client):
    card = session.handle_text(UID, "ادفع المولدة مال شهر عشرة")
    done = session.handle_button(UID, card.quote_id, "confirm", PIN)
    assert done.kind == "done"
    after = balance(client)
    again = session.handle_button(UID, card.quote_id, "confirm", PIN)
    assert again.kind == "refuse" and "اندفعت قبل" in again.text and "ما انخصم" not in again.text
    assert balance(client) == after  # paid once


def test_tap_on_a_cancelled_card_is_still_a_plain_stale_card(session):
    card = session.handle_text(UID, "ادفع المولدة مال شهر عشرة")
    session.handle_button(UID, card.quote_id, "cancel")
    again = session.handle_button(UID, card.quote_id, "confirm", PIN)
    assert again.kind == "refuse" and "ما انخصم" in again.text
