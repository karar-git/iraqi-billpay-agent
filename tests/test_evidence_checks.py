"""Showing the work: what the code checked on a turn, with the real values and where each came from.

Reply.evidence is a list of Check(what, value, source), recorded by deterministic code in the order the checks
were made: the user's amount words and the rule that read them, how the payee words matched a saved contact, the
balance, the daily limit, the wallet's quote, and after the tap the transaction read back from the wallet. A
refusal names the rule that refused, with its numbers. No check holds a PIN, a whole phone number or anybody
else's balance. Offline: the open StubCore and the in-memory mock wallet.
"""

from __future__ import annotations

import copy
import inspect
import re
import string

import httpx
import pytest

from agent import speech
from agent.session import (EXPLAIN_OPTION, Check, Session, short_reference, wants_explanation)
from agent.wallet_client import WalletClient, WalletUnavailable
from core.interface import ExplainingCore
from core.stub import StubCore
from payments.messages import CATALOGS, render
from payments.messages_interact import INTERACT_MESSAGES
from tests.test_options_tap import PAY, SEED, UID, World

PIN = "8642"  # no amount, balance, time or number ending of these tests holds these digits

OWN_SEED = copy.deepcopy(SEED)
OWN_SEED["users"][0]["pin"] = PIN
OWN_SEED["contacts"].append({"contact_id": "c6", "user_id": "u1", "display_name": "ميثم جبار", "phone": "07809998877",
                             "alias": "اخوي"})
OWN_SEED["bills"].append({"id": "b4", "account_id": "a2", "period": "2026-11", "amount_due": 61_000, "status": "paid"})


@pytest.fixture
def w(worlds):
    return worlds(OWN_SEED)


@pytest.fixture
def worlds():
    made: list[World] = []

    def make(seed: dict | None = None) -> World:
        made.append(World(seed or OWN_SEED))
        return made[-1]

    yield make
    for world in made:
        world.close()


def by_what(reply) -> dict[str, Check]:
    return {check.what: check for check in reply.evidence}


def whats(reply) -> list[str]:
    return [check.what for check in reply.evidence]


def lines(world: World, reply) -> list[str]:
    return world.session.why_lines(UID, reply.evidence)


def pay(world: World, card, pin: str = PIN):
    return world.session.handle_button(UID, card.quote_id, PAY, pin=pin)


# ---------------------------------------------------------------- a transfer


def test_a_transfer_card_says_everything_it_was_built_on_in_order(w):
    card = w.say("دز لهادي خمسين")
    assert card.kind == "card"
    assert whats(card) == ["payee", "amount", "balance_ok", "limit_ok", "flag_first_time_payee", "quote"]
    found = by_what(card)
    assert found["payee"].source == "contacts"
    assert found["payee"].slots["words"] == "هادي" and found["payee"].slots["name"] == "هادي"
    assert found["payee"].slots["last4"] == "2222" and found["payee"].value.endswith("how=how_exact")
    assert found["amount"].source == "user_text"
    assert found["amount"].value == "words=خمسين; amount=50,000; rule=rule_bare"
    assert found["balance_ok"].source == "wallet:profile"
    assert found["balance_ok"].slots == {"balance": 500_000, "needed": 50_250}  # the fee is in what is needed
    assert found["limit_ok"].slots == {"limit": 1_000_000, "spent": 0, "amount": 50_000}
    assert found["flag_first_time_payee"].source == "rules"
    quote = w.quote(card.quote_id)
    assert found["quote"].source == "wallet:quote"
    assert found["quote"].slots["reference"] == card.quote_id == quote["id"]
    assert (found["quote"].slots["amount"], found["quote"].slots["fee"], found["quote"].slots["total"]) == (
        quote["amount"], quote["fee"], quote["total"]) == (50_000, 250, 50_250)
    assert re.fullmatch(r"\d\d:\d\d", found["quote"].slots["time"])  # when the quote runs out, Baghdad time
    assert all(isinstance(check, Check) and check.value is not None for check in card.evidence)


@pytest.mark.parametrize("words, value, rule", [
    ("خمسين", 50_000, "rule_bare"),
    ("50 الف", 50_000, "rule_scale"),
    ("50000", 50_000, "rule_exact"),
    ("٥٠", 50_000, "rule_eastern_bare"),
    ("٥٠ الف", 50_000, "rule_eastern_scale"),
    ("٥٠٠٠٠", 50_000, "rule_eastern_exact"),
    ("ربع مليون", 250_000, "rule_scale"),
])
def test_the_amount_check_names_the_rule_that_read_the_words(w, words, value, rule):
    card = w.say(f"دز لهادي {words}")
    amount = by_what(card)["amount"]
    assert amount.slots["words"] == words and amount.slots["amount"] == value
    assert amount.value == f"words={words}; amount={value:,}; rule={rule}"
    assert w.quote(card.quote_id)["amount"] == value  # what the check says is what the wallet quoted
    line = lines(w, card)[whats(card).index("amount")]
    assert f"«{words}»" in line and f"{value:,}" in line and render(f"why_{rule}", "f", "iq") in line


@pytest.mark.parametrize("message, how, name, last4", [
    ("دز لهادي خمسين", "how_exact", "هادي", "2222"),
    ("دز لحسن خمسين", "how_first_name", "حسن علي", "6666"),
    ("دز لاخوي خمسين", "how_alias", "ميثم جبار", "8877"),
])
def test_the_payee_check_says_how_the_words_matched(w, message, how, name, last4):
    card = w.say(message)
    payee = by_what(card)["payee"]
    assert payee.source == "contacts" and payee.slots["name"] == name and payee.slots["last4"] == last4
    assert payee.value.endswith(f"how={how}")
    assert render(f"why_{how}", "f", "iq") in lines(w, card)[0]


def test_a_typed_number_is_checked_by_its_ending_only(w):
    card = w.say("دز 50 الف على 07712345678")
    payee = card.evidence[0]
    assert payee.what == "payee_number" and payee.source == "user_text" and payee.slots == {"last4": "5678"}
    saved = w.say("دز 50 الف على 07711112222")
    assert saved.evidence[0].what == "payee_saved_number" and saved.evidence[0].slots["name"] == "هادي"
    for reply in (card, saved):
        assert "0771" not in " ".join(c.value for c in reply.evidence) + " ".join(lines(w, reply))


def test_a_question_says_why_it_was_asked(w):
    asked = w.say("دز لأحمد خمسين")
    assert asked.kind == "ask" and whats(asked) == ["payees_many"]
    assert asked.evidence[0].slots == {"words": "أحمد", "count": 2}
    card = w.tap(asked.options[1].id)
    assert whats(card)[:3] == ["tapped", "payee_chosen", "amount"]  # the tap, then what it stood for
    assert by_what(card)["tapped"].source == "code" and by_what(card)["payee_chosen"].slots["last4"] == "0002"


def test_words_the_user_never_wrote_are_named_and_not_used(worlds):
    from core.interface import Intent

    class Inventing(StubCore):
        def understand(self, text, context=None):
            return Intent("transfer", payee_mention="هادي", amount_text="مليون")

    world = worlds()
    world.session = Session(wallet=world.client, core=Inventing())
    asked = world.say("دز لهادي")
    assert asked.kind == "ask" and asked.ask_field == "amount"
    unsaid = by_what(asked)["unsaid"]
    assert unsaid.source == "user_text" and unsaid.slots == {"words": "مليون"}
    assert "amount" not in whats(asked)  # nothing was read as an amount


# ---------------------------------------------------------------- a bill


def test_a_bill_card_takes_its_amount_from_the_wallets_bill_record(w):
    card = w.say("ادفع المولدة")
    assert whats(card) == ["bill", "balance_ok", "limit_ok", "flags_none", "quote"]
    bill = by_what(card)["bill"]
    assert bill.source == "wallet:bills"
    assert bill.slots == {"biller": "مولدة الحجي جاسم", "account": "350", "period": "2026-10", "due": 45_000}
    assert by_what(card)["balance_ok"].slots == {"balance": 500_000, "needed": 45_000}  # a bill has no fee
    assert by_what(card)["quote"].slots["fee"] == 0 and by_what(card)["quote"].slots["total"] == 45_000
    said = lines(w, card)[0]
    assert "مولدة الحجي جاسم" in said and "350" in said and "تشرين الأول 2026" in said and "45,000" in said


def test_a_bill_with_another_amount_says_why_it_asked_first(w):
    asked = w.say("ادفع المولدة 20 الف")
    assert asked.kind == "ask" and asked.ask_field == "full_amount"
    assert "bill" in whats(asked) and whats(asked)[-1] == "bill_full"
    assert by_what(asked)["bill_full"].source == "rules" and by_what(asked)["bill_full"].slots == {"due": 45_000}
    card = w.tap(asked.options[0].id)
    assert card.kind == "card" and "bill_full" not in whats(card) and whats(card)[-1] == "quote"


# ---------------------------------------------------------------- refusals


def test_a_refusal_names_the_rule_and_its_numbers(w):
    refused = w.say("دز لهادي 600 الف")
    assert refused.kind == "refuse" and refused.card is None
    assert whats(refused) == ["payee", "amount", "balance_short", "limit_ok"]
    short = by_what(refused)["balance_short"]
    assert short.source == "wallet:profile" and short.slots == {"balance": 500_000, "needed": 600_250}
    line = lines(w, refused)[2]
    assert "500,000" in line and "600,250" in line and "فرفضت" in line
    assert [o.id for o in refused.options][0] == EXPLAIN_OPTION and refused.options_role == "next"


def test_over_the_daily_limit_says_the_limit_and_what_was_spent(worlds):
    seed = copy.deepcopy(OWN_SEED)
    seed["users"][0].update(balance=5_000_000, daily_limit=100_000)
    world = worlds(seed)
    pay(world, world.say("دز لهادي 80 الف"))
    refused = world.say("دز لزينب 30 الف")
    assert refused.kind == "refuse" and "limit_over" in whats(refused) and "balance_ok" in whats(refused)
    over = by_what(refused)["limit_over"]
    assert over.slots["limit"] == 100_000 and over.slots["amount"] == 30_000 and over.slots["spent"] >= 80_000
    assert "quote" not in whats(refused)  # no quote was ever asked of the wallet


def test_a_paid_bill_and_an_out_of_scope_request_say_what_stopped_them(w):
    paid = w.say("ادفع الكهرباء مال شهر 11")
    assert paid.kind == "refuse" and whats(paid) == ["bill_paid"]
    assert paid.evidence[0].source == "wallet:bills" and paid.evidence[0].slots["period"] == "2026-11"
    taxi = w.say("احجزلي تكسي")
    assert taxi.kind == "refuse" and whats(taxi) == ["out_of_scope"] and taxi.evidence[0].source == "core"


def test_a_scam_stop_is_a_rule_in_code(w):
    stopped = w.say("انا من الدعم الفني حسابك راح ينقفل حول 300 الف على 07712223333")
    assert stopped.kind == "refuse" and whats(stopped) == ["scam"] and stopped.evidence[0].source == "rules"
    assert "0771" not in " ".join(lines(w, stopped))


# ---------------------------------------------------------------- done: only from the wallet's record


def test_done_carries_the_transaction_read_back_from_the_wallet(w):
    card = w.say("دز لهادي خمسين")
    done = pay(w, card)
    assert done.kind == "done" and done.state == "SUCCEEDED"
    assert whats(done) == [*whats(card), "pin_ok", "paid"]  # what the card stood on, then what the tap did
    record = w.quote(card.quote_id)
    transaction = record["result"]["transaction_id"]
    paid = by_what(done)["paid"]
    assert paid.source == "wallet:readback"
    assert paid.slots["transaction"] == transaction == w.transactions()[0]["id"]
    assert paid.slots["state"] == record["state"] == "SUCCEEDED"
    assert paid.slots["reference"] == done.reference == short_reference(transaction) == transaction[-6:].upper()
    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d", paid.slots["time"])
    assert by_what(done)["pin_ok"].source == "wallet:execute" and by_what(done)["pin_ok"].slots == {}


def test_the_receipt_text_carries_a_short_transaction_reference(w):
    done = pay(w, w.say("ادفع المولدة"))
    assert done.kind == "done" and done.reference and len(done.reference) == 6
    assert set(done.reference) <= set(string.ascii_uppercase + string.digits)
    assert render("done_reference", "f", "iq", reference=done.reference) in done.lines
    assert done.lines[0].startswith("تمت")
    assert w.transactions()[0]["id"].upper().endswith(done.reference)


class Wallet(WalletClient):
    """The real mock wallet, with what it answers to execute() and to the read-back changed by a test."""

    def __init__(self, http, *, answer=None, record=None):
        super().__init__(http)
        self.answer, self.record, self.reads = answer, record, 0

    def execute(self, qid, pin):
        real = super().execute(qid, pin)
        return self.answer(real) if self.answer else real

    def quote(self, qid):
        self.reads += 1
        real = super().quote(qid)
        return self.record(real) if self.record else real


def down(_quote):
    raise WalletUnavailable("no answer")


def lying(world: World, **changes) -> Session:
    world.session = Session(wallet=Wallet(world.client, **changes), core=world.core)
    return world.session


def test_done_is_read_back_before_it_is_said(w):
    session = lying(w)
    card = w.say("دز لهادي خمسين")
    before = session.wallet.reads
    assert pay(w, card).kind == "done" and session.wallet.reads == before + 1


@pytest.mark.parametrize("record, kind", [
    (lambda q: {**q, "state": "FAILED", "result": {"reason": "biller_down"}}, "failed"),
    (lambda q: {**q, "state": "CANCELLED", "result": None}, "failed"),
    (lambda q: {**q, "state": "EXECUTING", "result": None}, "unknown"),
    (lambda q: {**q, "id": "q_someone_elses"}, "unknown"),
    (lambda q: {**q, "result": {**q["result"], "transaction_id": ""}}, "unknown"),
    (lambda q: {**q, "user_id": "u9"}, "unknown"),
], ids=["the record says failed", "the record says cancelled", "the record is still in flight",
        "the record of another quote", "a record with no transaction", "a record of another user"])
def test_an_answer_that_says_paid_is_never_done_when_the_record_does_not(w, record, kind):
    lying(w, record=record)
    card = w.say("دز لهادي خمسين")
    reply = pay(w, card)
    assert reply.kind == kind and reply.kind != "done" and reply.reference is None
    assert not any(line.startswith("تمت") for line in reply.lines)
    assert "paid" not in whats(reply) and "paid_unread" not in whats(reply)


@pytest.mark.parametrize("answer", [
    lambda q: {**q, "result": {}},
    lambda q: {**q, "result": None},
    lambda q: {**q, "id": "q_other"},
    lambda q: {"state": "SUCCEEDED"},
], ids=["no transaction id", "no result", "another quote's id", "a bare SUCCEEDED"])
def test_an_answer_that_proves_nothing_is_unknown_when_the_wallet_cannot_be_read(w, answer):
    lying(w, answer=answer, record=down)
    reply = pay(w, w.say("دز لهادي خمسين"))
    assert reply.kind == "unknown" and reply.state == "UNKNOWN" and reply.reference is None
    assert whats(reply)[-1] == "unknown"


def test_the_wallets_own_answer_stands_when_it_cannot_be_read_a_second_time(w):
    lying(w, record=down)
    card = w.say("دز لهادي خمسين")
    done = pay(w, card)
    assert done.kind == "done" and whats(done)[-1] == "paid_unread"  # and the check says it was not read back
    assert by_what(done)["paid_unread"].source == "wallet:execute"
    assert "ما گدرت أقرا سجلها مرة ثانية" in lines(w, done)[-1]


def test_an_unknown_outcome_is_said_only_once_the_record_is_read(w):
    class LostAnswer:
        """The wallet runs the payment, but its answer never arrives, and then nothing about quotes does."""

        def __init__(self, client):
            self.client, self.down = client, False

        def request(self, method, path, **kwargs):
            if self.down and path.startswith("/quotes"):
                raise httpx.ConnectError("network down")
            response = self.client.request(method, path, **kwargs)
            if path.endswith("/execute"):
                self.down = True
                raise httpx.ConnectError("the answer was lost")
            return response

    line = LostAnswer(w.client)
    w.session = Session(wallet=line, core=w.core)
    card = w.say("دز لهادي خمسين")
    unknown = pay(w, card)
    assert unknown.kind == "unknown" and whats(unknown)[-1] == "unknown" and unknown.reference is None
    assert unknown.evidence[-1].source == "wallet:execute"
    line.down = False
    later = w.session.reconcile(UID)
    assert any(line_.startswith("تمت") for line_ in later.lines)
    paid = by_what(later)["paid"]
    assert paid.source == "wallet:readback" and paid.slots["transaction"] == w.transactions()[0]["id"]
    assert render("done_reference", "f", "iq", reference=paid.slots["reference"]) in later.lines


def test_a_failed_payment_says_what_the_record_says(w):
    card = w.say("ادفع المولدة")
    w.client.post("/admin/faults", json={"mode": "biller_down", "times": 1})
    failed = pay(w, card)
    assert failed.kind == "failed" and whats(failed)[-1] == "not_paid"
    assert failed.evidence[-1].slots["reason"] == "biller_down" and failed.evidence[-1].slots["state"] == "FAILED"
    assert [o.id for o in failed.options] == [EXPLAIN_OPTION]
    assert w.balance() == 500_000


def test_a_wrong_pin_is_a_check_of_the_wallet_and_never_the_pin_itself(w):
    card = w.say("دز لهادي خمسين")
    wrong = pay(w, card, pin="1357")
    assert wrong.kind == "card" and whats(wrong)[-1] == "pin_wrong" and wrong.evidence[-1].source == "wallet:execute"
    everything = " ".join(c.value for c in wrong.evidence) + " ".join(lines(w, wrong))
    assert "1357" not in everything and PIN not in everything


def test_only_one_place_in_the_orchestrator_says_done():
    """The audit, kept as a test: «done» is built in one function, under the wallet's state SUCCEEDED, and every
    road to that function first proves the record (Session._is_paid)."""
    source = inspect.getsource(Session)
    assert source.count('kind="done"') == 1 and "kind='done'" not in source
    outcome = inspect.getsource(Session._outcome)
    assert outcome.index('if state == "SUCCEEDED":') < outcome.index('kind="done"') < outcome.index("reason = result")
    assert "_confirm_paid" in inspect.getsource(Session._execute)
    assert "_is_paid" in inspect.getsource(Session._reconcile) and "_is_paid" in inspect.getsource(Session._confirm_paid)


def test_two_requests_in_one_message_keep_their_checks_apart(w):
    first = w.say("دز لهادي خمسين وادفع المولدة")
    assert first.kind == "card"
    second = pay(w, first)
    assert second.kind == "card" and second.quote_id != first.quote_id
    names_ = whats(second)
    assert names_.index("paid") < names_.index("bill") < len(names_) - 1 and names_[-1] == "quote"
    quotes = [c.slots["reference"] for c in second.evidence if c.what == "quote"]
    assert quotes == [first.quote_id, second.quote_id]


# ---------------------------------------------------------------- explain()


def test_explain_says_the_last_checks_in_three_languages(w):
    card = w.say("دز لهادي خمسين")
    iraqi = w.session.explain(UID)
    assert iraqi.lang == "iq" and iraqi.lines[0] == render("why_title", "f", "iq")
    assert len(iraqi.lines) == 1 + len(card.evidence) and all(line.startswith("• ") for line in iraqi.lines[1:])
    assert "كتبتي «خمسين» وقريتها 50,000 دينار" in iraqi.lines[2]
    assert "رقم تحت الألف بلا كلمة «ألف» يعني آلاف" in iraqi.lines[2]
    assert "الرصيد: عندج 500,000 دينار والمطلوب ويا العمولة 50,250 دينار، يكفي." in iraqi.lines[3]

    english = w.say("why")
    assert english.lang == "en" and english.lines[0] == "Here is what I checked on your last request:"
    assert "you wrote “خمسين” and I read 50,000 dinars" in english.lines[2]
    assert "a number under 1,000 with no “thousand” means thousands" in english.lines[2]
    assert "Balance: you have 500,000 dinars and 50,250 are needed with the fee. Enough." in english.lines[3]
    assert f"Wallet quote: id {card.quote_id}" in english.lines[-1]

    fusha = w.say("لماذا")
    assert fusha.lang == "msa" and fusha.lines[0] == "هذا ما تحققت منه في آخر طلب:"
    assert "ورد في كلامك «خمسين» وقرأته 50,000 دينار" in fusha.lines[2]
    assert "الرصيد: لديك 500,000 دينار والمطلوب مع العمولة 50,250 دينار، وهو كافٍ." in fusha.lines[3]
    for reply in (iraqi, english, fusha):  # the card stays open through all of it, and comes back with the reply
        assert reply.kind == "card" and reply.quote_id == card.quote_id and reply.card is not None
    assert w.quote(card.quote_id)["state"] == "QUOTED"
    assert pay(w, card).kind == "done"


def test_explain_speaks_to_a_man_as_a_man(worlds):
    seed = copy.deepcopy(OWN_SEED)
    seed["users"][0]["gender"] = "m"
    world = worlds(seed)
    world.say("دز لهادي خمسين")
    said = world.session.explain(UID).lines
    assert "كتبت «خمسين»" in said[2] and "عندك 500,000" in said[3] and "عندج" not in " ".join(said)


def test_the_why_words_are_read_in_code_and_change_nothing(w):
    card = w.say("دز لهادي خمسين")
    read = list(w.core.seen)
    for words in ("ليش", "ليش؟", "شلون تأكدت؟", "التفاصيل", "details", "Why?"):
        assert wants_explanation(words)
        reply = w.say(words)
        assert reply.kind == "card" and reply.quote_id == card.quote_id and len(reply.lines) == 7
    assert w.core.seen == read  # never sent to the core
    tapped = w.tap(EXPLAIN_OPTION)
    assert tapped.lines == w.session.explain(UID).lines and tapped.kind == "card"
    assert w.session.state(UID).card is not None and w.transactions() == []


def test_explain_keeps_an_open_question_open(w):
    asked = w.say("دز لأحمد خمسين")
    why = w.say("ليش")
    assert why.kind == "info" and "لگيت 2 أسماء تطابق «أحمد»" in why.lines[1]
    assert w.session.state(UID).question is not None
    assert w.tap(asked.options[0].id).kind == "card"  # the same options still answer it


def test_explain_with_nothing_checked_says_so(w):
    w.say("هلا")  # small talk checks nothing
    assert w.session.explain(UID).lines == [render("why_nothing", "f", "iq")]
    for words, lang in (("ليش", "iq"), ("why", "en"), ("لماذا", "msa")):
        assert w.say(words).lines == [render("why_nothing", "f", lang)]


def test_explain_is_about_the_last_turn_that_checked_something(w):
    w.say("دز لهادي 600 الف")  # refused
    w.say("هلا")  # checks nothing: the refusal is still what «ليش» is about
    assert "ما يكفي، فرفضت" in " ".join(w.session.explain(UID).lines)
    balance = w.say("شكد رصيدي")
    assert whats(balance) == ["balance_read"] and balance.evidence[0].source == "wallet:profile"
    assert w.session.explain(UID).lines[1:] == ["• الرصيد مقري من المحفظة هسه: 500,000 دينار."]


def test_the_evidence_of_a_turn_is_only_that_turns(w):
    w.say("دز لهادي خمسين")
    bill = w.say("ادفع المولدة")
    assert "payee" not in whats(bill) and "amount" not in whats(bill)
    assert w.say("شكرا").evidence == []


def test_a_reply_offers_how_did_you_check_after_a_card_a_receipt_and_a_refusal(w):
    card = w.say("دز لهادي خمسين")
    assert [(o.id, o.title) for o in card.options] == [(EXPLAIN_OPTION, "شلون تأكدت؟")]
    done = pay(w, card)
    assert done.options[0].id == EXPLAIN_OPTION and len(done.options) == 9
    refused = w.say("دز لهادي عشر ملايين")
    assert refused.options[0].id == EXPLAIN_OPTION
    assert w.say("شكد رصيدي").options == []  # nothing to explain about a question that checked one number
    english = w.say("send 50000 to Hadi")
    assert english.lang == "en" and english.options[0].title == "How did you check?"


# ---------------------------------------------------------------- what is never shown


def test_no_check_ever_holds_a_pin_a_whole_number_or_someone_elses_balance(worlds):
    seed = copy.deepcopy(OWN_SEED)
    seed["users"].append({"id": "u2", "name": "هادي جواد", "phone": "07711112222", "balance": 777_123,
                          "daily_limit": 1_000_000, "pin": "9753", "gender": "m"})
    world = worlds(seed)
    numbers = [c["phone"] for c in seed["contacts"]] + [u["phone"] for u in seed["users"]] + ["07712345678"]
    said: list[str] = []

    def keep(reply) -> None:
        said.extend(c.value for c in reply.evidence)
        said.extend(str(v) for c in reply.evidence for v in c.slots.values())
        said.extend(world.session.why_lines(UID, reply.evidence))
        said.extend(world.session.explain(UID).lines)
        for lang in ("en", "msa", "iq"):
            world.session.state(UID).lang = lang
            said.extend(world.session.explain(UID).lines)

    card = world.say("دز لهادي خمسين")
    keep(card)
    keep(world.say(PIN))  # a PIN typed in the chat while a card is open
    keep(world.say(f"الرمز {PIN}"))
    card = world.say("دز 50 الف على 07712345678")
    keep(card)
    keep(pay(world, card, pin="1357"))
    keep(pay(world, card))
    card = world.say("دز لهادي خمسين")  # Hadi has an account of his own here, with a balance of his own
    keep(card)
    keep(pay(world, card))
    keep(world.say("دز لأحمد 07801110001 خمسين"))
    keep(world.tap("opt_m_account"))
    keep(world.tap("opt_m_contacts"))
    text = "\n".join(said)
    assert PIN not in text and "1357" not in text and "9753" not in text
    for number in numbers:
        assert number not in text and number[1:] not in text and number.replace("0", "964", 1) not in text
    assert "777,123" not in text and "777123" not in text  # the payee's balance is nobody else's business
    assert "5678" in text and "2222" in text  # endings are what is shown


def test_a_number_inside_quoted_words_is_cut_to_its_ending():
    from agent.session import _safe_words

    assert _safe_words("حساب 07712345678") == "حساب …5678"
    assert _safe_words("٠٧٧١٢٣٤٥٦٧٨") == "…5678"
    assert _safe_words("0771 234 5678 خمسين") == "…5678 خمسين"
    assert _safe_words("خمسين الف") == "خمسين الف" and len(_safe_words("ك" * 200)) == 60


# ---------------------------------------------------------------- a core that explains itself


class Explaining(StubCore):
    def __init__(self, facts):
        self.facts = facts

    def evidence(self, user_id):
        if isinstance(self.facts, Exception):
            raise self.facts
        return self.facts


def test_a_core_that_explains_itself_is_part_of_the_evidence(worlds):
    world = worlds()
    core = Explaining({"decision": "transfer", "confidence": 0.97, "generative": "transfer", "verdict": "agree",
                       "route": "understand: local:small-model", "fallback": False})
    assert isinstance(core, ExplainingCore) and not isinstance(StubCore(), ExplainingCore)
    world.session = Session(wallet=world.client, core=core)
    card = world.say("دز لهادي خمسين")
    assert whats(card)[:3] == ["core_agree", "core_route", "payee"]  # what read the request, then what code checked
    agree = by_what(card)["core_agree"]
    assert agree.source == "core" and agree.slots["confidence"] == 97
    said = world.session.why_lines(UID, card.evidence)
    assert said[0] == "قراءة الطلب: نموذج القرار ونموذج الفهم اتفقوا على «تحويل» (ثقة 97٪)."
    assert said[1] == "اللي قرا الطلب: understand: local:small-model."
    assert world.session.source_label(UID, "core") == "نموذج الفهم"


@pytest.mark.parametrize("facts, expected", [
    ({"decision": "balance", "confidence": 0.99, "verdict": "direct"}, ["core_direct"]),
    ({"decision": "chat", "confidence": 0.91, "generative": "transfer", "verdict": "ask"}, ["core_ask"]),
    ({"decision": "transfer", "confidence": 0.4, "verdict": "abstain", "fallback": True},
     ["core_abstain", "core_fallback"]),
    ({"route": "understand: backup"}, ["core_route"]),
    ({"decision": "transfer", "verdict": "something else"}, []),
    ({}, []), (None, []), ("transfer", []), (RuntimeError("no"), []),
])
def test_what_a_core_says_it_checked_is_read_with_care(worlds, facts, expected):
    world = worlds()
    world.session = Session(wallet=world.client, core=Explaining(facts))
    card = world.say("دز لهادي خمسين")
    assert card.kind == "card"  # whatever the core says about itself, the turn is the same
    assert [name for name in whats(card) if name.startswith("core_")] == expected
    for lang in ("iq", "msa", "en"):
        world.session.state(UID).lang = lang
        assert all(not line.startswith("core_") for line in world.session.why_lines(UID, card.evidence))


def test_a_cores_own_words_cannot_carry_a_number_or_run_long(worlds):
    world = worlds()
    world.session = Session(wallet=world.client, core=Explaining(
        {"route": "understand: 07712345678 " + "x" * 300, "decision": "07712345678", "verdict": "agree"}))
    card = world.say("دز لهادي خمسين")
    text = " ".join(world.session.why_lines(UID, card.evidence))
    assert "07712345678" not in text and "x" * 90 not in text


# ---------------------------------------------------------------- the words


def _fields(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def test_every_why_line_exists_in_three_languages_with_the_same_slots():
    keys = set(INTERACT_MESSAGES["iq"])
    assert keys == set(INTERACT_MESSAGES["msa"]) == set(INTERACT_MESSAGES["en"])
    for key in sorted(keys):
        shapes = []
        for lang in ("iq", "msa", "en"):
            entry = CATALOGS[lang][key]
            forms = list(entry.values()) if isinstance(entry, dict) else [entry]
            assert all(forms), (lang, key)
            shapes.append({frozenset(_fields(form)) for form in forms})
        assert shapes[0] == shapes[1] == shapes[2] and len(shapes[0]) == 1, key


def test_the_iraqi_words_follow_the_house_rules():
    for lang, table in INTERACT_MESSAGES.items():
        for key, entry in table.items():
            for form in (entry.values() if isinstance(entry, dict) else [entry]):
                assert not set(form) & {chr(0x0686), chr(0x2014), chr(0x2013)}, (lang, key)  # che, em and en dash
    iraqi = " ".join(str(v) for v in INTERACT_MESSAGES["iq"].values())
    assert "عليج" in iraqi and "كلامج" in iraqi  # ج where others would write the Persian letter


def test_every_check_the_orchestrator_can_note_has_a_line():
    source = inspect.getsource(Session)
    noted = set(re.findall(r'_note\(\s*uid,\s*"([a-z_]+)"', source))
    noted |= {"balance_short", "balance_ok", "limit_over", "limit_ok", "cooldown_over", "cooldown_ok", "paid",
              "paid_unread", "core_agree", "core_ask", "core_direct", "core_abstain"}
    assert len(noted) > 25
    for name in noted:
        for lang in ("iq", "msa", "en"):
            assert f"why_{name}" in CATALOGS[lang], (name, lang)


def test_the_source_of_a_check_has_a_name_in_each_language(w):
    for lang, wallet, words in (("iq", "المحفظة", "كلامج"), ("msa", "المحفظة", "كلامك"), ("en", "the wallet", "your words")):
        w.session.state(UID).lang = lang
        assert w.session.source_label(UID, "wallet:readback") == wallet
        assert w.session.source_label(UID, "user_text") == words
        assert w.session.source_label(UID, "something new") == w.session.source_label(UID, "code")


# ---------------------------------------------------------------- the voice


def test_a_voice_reply_never_reads_the_reference_or_the_options(w):
    done = pay(w, w.say("دز لهادي خمسين"))
    spoken = speech.spoken_text(done)
    assert done.reference not in spoken and "رقم العملية" not in spoken
    assert "شلون تأكدت" not in spoken and "تحويل فلوس" not in spoken
    assert "تمت" in spoken
