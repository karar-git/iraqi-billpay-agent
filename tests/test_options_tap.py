"""Options the user can tap: a tap is the typed answer, by another road.

Every question with a closed set of answers carries its options. An option id is opaque, resolved on the
server against the open question, and goes through the same deterministic code as typing that answer (the
tests here compare the two roads on two fresh wallets: same card, same quote). No option can pay, and the
core is never called for a tap. Offline: the open StubCore and the in-memory mock wallet.
"""

from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from agent.session import (EXPLAIN_OPTION, MENU_HELP_OPTION, MENU_ROWS, Option, Session, is_option_id, wants_explanation,
                           wants_menu)
from core.stub import StubCore
from payments.messages import CATALOGS, render
from wallet.api import create_app

PAY = "إي، ادفع"
PIN = "1234"
UID = "u1"

SEED = {
    "users": [
        # 074…: no network owns that prefix here, so a top-up has to ask which one
        {"id": "u1", "name": "سارة كريم", "phone": "07401234567", "balance": 500_000,
         "daily_limit": 1_000_000, "pin": PIN, "gender": "f"},
    ],
    "contacts": [
        {"contact_id": "c1", "user_id": "u1", "display_name": "هادي", "phone": "07711112222"},
        {"contact_id": "c2", "user_id": "u1", "display_name": "أحمد", "phone": "07801110001"},
        {"contact_id": "c3", "user_id": "u1", "display_name": "أحمد", "phone": "07502220002"},
        {"contact_id": "c4", "user_id": "u1", "display_name": "زينب", "phone": "07903334444"},
        {"contact_id": "c5", "user_id": "u1", "display_name": "حسن علي", "phone": "07705556666"},
    ],
    "billers": [
        {"id": "generator", "kind": "generator", "name_ar": "مولدة الحجي جاسم", "name_en": "Hajji Jasim Generator"},
        {"id": "electricity", "kind": "electricity", "name_ar": "كهرباء الوطنية", "name_en": "National Electricity"},
        {"id": "zain", "kind": "topup", "name_ar": "زين", "name_en": "Zain"},
    ],
    "bill_accounts": [
        {"id": "a1", "user_id": "u1", "biller_id": "generator", "account_no": "350",
         "holder_name": "سارة كريم", "label_ar": "المولدة"},
        {"id": "a2", "user_id": "u1", "biller_id": "electricity", "account_no": "2024939794",
         "holder_name": "سارة كريم", "label_ar": "كهرباء البيت"},
    ],
    "bills": [
        {"id": "b1", "account_id": "a1", "period": "2026-10", "amount_due": 45_000, "status": "unpaid"},
        {"id": "b2", "account_id": "a1", "period": "2026-11", "amount_due": 47_000, "status": "unpaid"},
        {"id": "b3", "account_id": "a2", "period": "2026-10", "amount_due": 60_000, "status": "unpaid"},
    ],
    "transactions": [],
}


class SpyCore(StubCore):
    """The stand-in core, counting how often it is asked to read a message."""

    def __init__(self):
        self.seen: list[str] = []

    def understand(self, text, context=None):
        self.seen.append(text)
        return super().understand(text, context)


class World:
    """One fresh wallet and one orchestrator over it."""

    def __init__(self, seed: dict | None = None):
        self._cm = TestClient(create_app(":memory:", seed=copy.deepcopy(seed or SEED)))
        self.client = self._cm.__enter__()
        self.core = SpyCore()
        self.session = Session(wallet=self.client, core=self.core)

    def close(self) -> None:
        self._cm.__exit__(None, None, None)

    def say(self, text: str):
        return self.session.handle_text(UID, text)

    def tap(self, option_id: str):
        return self.session.handle_option(UID, option_id)

    def balance(self) -> int:
        return self.client.get(f"/users/{UID}").json()["balance"]

    def quote(self, qid: str) -> dict:
        return self.client.get(f"/quotes/{qid}").json()

    def transactions(self) -> list[dict]:
        return self.client.get(f"/users/{UID}/transactions").json()


@pytest.fixture
def worlds():
    made: list[World] = []

    def make(seed: dict | None = None) -> World:
        made.append(World(seed))
        return made[-1]

    yield make
    for world in made:
        world.close()


@pytest.fixture
def w(worlds):
    return worlds()


def same_quote(one: World, a, other: World, b) -> None:
    """Two replies show the same card over the same quote (ids and times apart)."""
    assert a.kind == b.kind == "card", (a.text, b.text)
    assert a.card.text == b.card.text
    assert [(x.id, x.label) for x in a.card.buttons] == [(x.id, x.label) for x in b.card.buttons]
    qa, qb = one.quote(a.quote_id), other.quote(b.quote_id)
    for field in ("kind", "amount", "fee", "total", "currency", "target", "state", "user_id"):
        assert qa[field] == qb[field], field


# ---------------------------------------------------------------- a tap is the typed answer

# (the messages before the question, the typed answer, the index of the option that means the same, the field)
QUESTIONS = [
    pytest.param(["دز لأحمد خمسين"], "الثاني", 1, "payee", id="which of two saved contacts"),
    pytest.param(["دز لأحمد خمسين"], "0001", 0, "payee", id="a contact, typed by its last digits"),
    pytest.param(["دز لعلي خمسين"], "إي", 0, "payee", id="did you mean: yes"),
    pytest.param(["ادفع الفاتورة"], "الثاني", 1, "biller", id="which bill account"),
    pytest.param(["ادفع الفاتورة"], "المولدة", 0, "biller", id="a bill account, typed by its name"),
    pytest.param(["ادفع المولدة مال تشرين"], "الثاني", 1, "period", id="which month"),
    pytest.param(["ادفع المولدة مال تشرين"], "تشرين الأول", 0, "period", id="a month, typed by its name"),
    pytest.param(["دز لهادي خمسين او ستين"], "الثاني", 1, "amount", id="which of two amounts"),
    pytest.param(["اشحن رصيدي بخمسة"], "زين", 0, "operator", id="which top-up network"),
    pytest.param(["ادفع المولدة 20 الف"], "إي", 0, "full_amount", id="the full bill amount: yes"),
]


@pytest.mark.parametrize("before, typed, index, field", QUESTIONS)
def test_a_tapped_option_gives_the_same_card_as_the_typed_answer(worlds, before, typed, index, field):
    one, other = worlds(), worlds()
    for text in before[:-1]:
        one.say(text), other.say(text)
    asked_a, asked_b = one.say(before[-1]), other.say(before[-1])
    assert asked_a.kind == asked_b.kind == "ask" and asked_a.ask_field == asked_b.ask_field == field
    assert asked_a.options and [o.title for o in asked_a.options] == [o.title for o in asked_b.options]
    assert asked_a.options_role == "answer"

    read_before = len(other.core.seen)
    by_typing = one.say(typed)
    by_tapping = other.tap(asked_b.options[index].id)
    assert len(other.core.seen) == read_before  # a tap is never read by the core
    same_quote(one, by_typing, other, by_tapping)
    assert one.balance() == other.balance() == 500_000  # a card moves nothing, tapped or typed

    # and the card that came of a tap pays like any other: once, with the PIN
    done = other.session.handle_button(UID, by_tapping.quote_id, PAY, pin=PIN)
    assert done.kind == "done" and len(other.transactions()) == 1
    assert other.client.get("/admin/audit").json()["ok"]


@pytest.mark.parametrize("before", [["دز لعلي خمسين"], ["ادفع المولدة 20 الف"]], ids=["did you mean", "the full bill"])
def test_a_tapped_no_is_the_typed_no(worlds, before):
    one, other = worlds(), worlds()
    asked_a, asked_b = one.say(before[0]), other.say(before[0])
    by_typing = one.say("لا")
    by_tapping = other.tap(asked_b.options[1].id)
    assert asked_a.options[1].title == "لا"
    assert by_typing.kind == by_tapping.kind == "cancelled" and by_typing.lines == by_tapping.lines
    assert one.session.state(UID).question is None and other.session.state(UID).question is None
    assert other.session.state(UID).card is None and other.transactions() == []


def test_a_yes_or_no_question_has_exactly_those_two_options(w):
    asked = w.say("ادفع المولدة 20 الف")
    assert [o.title for o in asked.options] == ["إي، كاملة", "لا"]
    assert "45,000" in asked.options[0].description and asked.offered_amount == 45_000
    asked = w.say("دز لعلي خمسين")
    assert [o.title for o in asked.options] == ["إي", "لا"]
    assert "حسن علي" in asked.options[0].description and "6666" in asked.options[0].description
    assert "07705556666" not in asked.options[0].description  # the ending of a number, never the number


def test_which_contact_options_carry_the_saved_name_and_the_number_ending(w):
    asked = w.say("دز لأحمد خمسين")
    assert [(o.title, o.tail) for o in asked.options] == [("أحمد", "0001"), ("أحمد", "0002")]
    assert all(o.description.endswith(o.tail) for o in asked.options)
    assert all("0780" not in o.description and "0750" not in o.description for o in asked.options)
    assert all(is_option_id(o.id) for o in asked.options) and len({o.id for o in asked.options}) == 2
    assert asked.options_title == "اختاري"
    assert "1) أحمد" in asked.text and "2) أحمد" in asked.text  # the numbered text is still there for typing


def test_free_text_questions_carry_no_options(w):
    assert w.say("دز لهادي").options == []  # how much?
    assert w.say("دز لهادي ورقة").options == []  # how much in dinars?
    w.say("بطلت")
    assert w.say("دز خمسين").options == []  # to whom? nobody was paid yet, so nobody to offer


def test_to_whom_offers_the_people_paid_last_and_a_tap_is_the_typed_name(worlds):
    one, other = worlds(), worlds()
    for world in (one, other):
        for text in ("دز لهادي عشرة الاف", "دز لزينب عشرة الاف"):
            card = world.say(text)
            assert world.session.handle_button(UID, card.quote_id, PAY, pin=PIN).kind == "done"
    asked_a, asked_b = one.say("دز عشرين الف"), other.say("دز عشرين الف")
    assert asked_b.ask_field == "payee" and [o.title for o in asked_b.options] == ["زينب", "هادي"]  # newest first
    assert [o.tail for o in asked_b.options] == ["4444", "2222"]
    same_quote(one, one.say("هادي"), other, other.tap(asked_b.options[1].id))
    assert asked_a.lines == asked_b.lines == [render("ask_payee", "f", "iq")]


def test_a_typed_yes_never_picks_a_recent_payee(w):
    card = w.say("دز لهادي عشرة الاف")
    w.session.handle_button(UID, card.quote_id, PAY, pin=PIN)
    asked = w.say("دز عشرين الف")
    assert [o.title for o in asked.options] == ["هادي"]
    reply = w.say("إي")  # the options are there to tap: the words «to whom?» got no name
    assert reply.kind != "card" and w.session.state(UID).card is None


def test_typed_answers_still_work_next_to_the_options(w):
    for answer, ending in (("الاول", "0001"), ("2", "0002"), ("0002", "0002")):
        w.say("دز لأحمد خمسين")
        card = w.say(answer)
        assert card.kind == "card" and ending in card.card.text
        w.session.handle_button(UID, card.quote_id, "لا، بطّلت")


# ---------------------------------------------------------------- old and unknown ids


def test_an_option_of_an_answered_question_is_old(w):
    asked = w.say("دز لأحمد خمسين")
    card = w.tap(asked.options[0].id)
    again = w.tap(asked.options[1].id)  # the same list, tapped a second time: the card is open, nothing changes
    assert again.kind == "card" and again.quote_id == card.quote_id
    assert again.lines == [render("option_stale", "f", "iq")]
    assert w.quote(card.quote_id)["state"] == "QUOTED" and "0001" in again.card.text


def test_an_old_option_repeats_the_question_that_is_open_now(w):
    first = w.say("دز لأحمد خمسين")
    w.say("بطلت")
    second = w.say("ادفع الفاتورة")
    reply = w.tap(first.options[0].id)
    assert reply.kind == "ask" and reply.ask_field == "biller"
    assert reply.lines == [render("option_stale", "f", "iq"), *second.lines]
    assert [o.id for o in reply.options] == [o.id for o in second.options]  # the open question's own options
    card = w.tap(reply.options[0].id)
    assert card.kind == "card" and "مولدة الحجي جاسم" in card.card.text


def test_the_same_question_asked_again_has_new_ids(w):
    first = w.say("دز لأحمد خمسين")
    second = w.say("دز لأحمد خمسين")
    assert {o.id for o in first.options}.isdisjoint({o.id for o in second.options})
    assert w.tap(first.options[0].id).kind == "ask"  # old: the question again, never a card
    assert w.tap(second.options[0].id).kind == "card"


@pytest.mark.parametrize("option_id", ["", "opt_", "opt_zzz", "opt_q00000000_0", "opt_m_pay", "opt_m_confirm",
                                      "confirm", "1", "أحمد", "opt_q" + "f" * 8 + "_99", "x" * 500])
def test_an_unknown_id_is_answered_politely_and_changes_nothing(w, option_id):
    asked = w.say("دز لأحمد خمسين")
    reply = w.tap(option_id)
    assert reply.kind == "ask" and reply.lines[0] == render("option_stale", "f", "iq")
    assert reply.lines[1:] == asked.lines and [o.id for o in reply.options] == [o.id for o in asked.options]
    assert w.balance() == 500_000 and w.transactions() == []
    assert w.core.seen == ["دز لأحمد خمسين"]  # nothing was read


def test_an_unknown_id_with_nothing_open_offers_the_menu(w):
    reply = w.tap("opt_q00000000_3")
    assert reply.lines == [render("option_stale", "f", "iq"), render("menu_intro", "f", "iq")]
    assert [o.id for o in reply.options] == [f"opt_m_{row}" for row in MENU_ROWS]


def test_an_index_past_the_end_of_the_open_list_is_old(w):
    asked = w.say("دز لأحمد خمسين")
    past = asked.options[0].id.rsplit("_", 1)[0] + "_2"
    assert w.tap(past).kind == "ask" and w.session.state(UID).card is None


def test_the_title_a_client_sends_is_never_what_decides(w):
    """handle_option takes an id and nothing else: there is no way to hand it a title."""
    asked = w.say("دز لأحمد خمسين")
    card = w.tap(asked.options[1].id)
    assert "0002" in card.card.text
    with pytest.raises(TypeError):
        w.session.handle_option(UID, asked.options[0].id, "أحمد")  # type: ignore[call-arg]


# ---------------------------------------------------------------- no option moves money


def test_an_option_id_sent_as_a_card_button_does_nothing(w):
    asked = w.say("دز لأحمد خمسين")
    for button in (PAY, "confirm", "/pay"):
        reply = w.session.handle_button(UID, asked.options[0].id, button, pin=PIN)
        assert reply.kind == "refuse" and reply.card is None
    assert w.balance() == 500_000 and w.transactions() == []
    assert w.session.state(UID).question is not None  # the question is still open, and still answerable
    assert w.tap(asked.options[0].id).kind == "card"


def test_a_card_button_id_sent_as_an_option_does_nothing(w):
    card = w.say("دز لهادي خمسين الف")
    qid = card.quote_id
    for forged in (f"confirm:{qid}", qid, f"opt_{qid}", f"opt_confirm_{qid}", "confirm", f"confirm:{qid}:{PIN}"):
        reply = w.tap(forged)
        assert reply.kind == "card" and reply.quote_id == qid, forged  # the card is still open, and unpaid
        assert reply.lines == [render("option_stale", "f", "iq")]
        assert not is_option_id(f"confirm:{qid}")
    assert w.quote(qid)["state"] == "QUOTED" and w.balance() == 500_000 and w.transactions() == []
    done = w.session.handle_button(UID, qid, PAY, pin=PIN)  # only this pays
    assert done.kind == "done" and w.balance() == 500_000 - 50_250


def test_no_option_ever_executes_whatever_is_tapped(w):
    """Every id a conversation hands out, tapped at every point of it: the wallet shows no payment."""
    seen: list[str] = [EXPLAIN_OPTION, *[f"opt_m_{row}" for row in MENU_ROWS]]
    for text in ("دز لأحمد خمسين", "ادفع الفاتورة", "ادفع المولدة 20 الف", "دز لهادي خمسين او ستين", "دز لهادي خمسين الف"):
        reply = w.say(text)
        seen += [o.id for o in reply.options]
        for option_id in list(seen):
            after = w.tap(option_id)
            seen += [o.id for o in after.options if o.id not in seen]
    assert w.transactions() == [] and w.balance() == 500_000
    assert w.client.get("/admin/audit").json()["ok"]


def test_option_ids_and_card_buttons_cannot_be_confused():
    assert is_option_id("opt_why") and is_option_id("opt_m_balance") and is_option_id("opt_q0123abcd_7")
    for other in ("confirm:q_1", "change:q_1", "cancel:q_1", "m:save:", "o:open", "opt_:x", "opt_a:b", "OPT_why",
                  "q_0123abcd", "", None, 7, "opt_" + "a" * 41):
        assert not is_option_id(other), other
    assert Session.is_option is is_option_id


# ---------------------------------------------------------------- the menu


def test_the_menu_rows_fit_a_button_and_a_list_row_in_three_languages():
    for lang, table in CATALOGS.items():
        for row in MENU_ROWS:
            for gender in ("f", "m"):
                assert 0 < len(render(f"menu_{row}", gender, lang)) <= 20, (lang, row)
                assert 0 < len(render(f"menu_{row}_hint", gender, lang)) <= 72, (lang, row)
        for key in ("menu_button", "option_yes", "option_no", "option_yes_full", "options_button", "why_button"):
            assert all(len(render(key, g, lang)) <= 20 for g in ("f", "m")), (lang, key)
        assert f"menu_{MENU_ROWS[0]}" in table


def test_the_menu_is_offered_on_a_greeting_and_on_its_words(w):
    hello = w.say("هلا")
    assert hello.lines == [render("help", "f", "iq")] and hello.kind == "ask"
    assert [o.id for o in hello.options] == [f"opt_m_{row}" for row in MENU_ROWS] and hello.options_role == "next"
    assert hello.options_title == "القائمة"
    read = len(w.core.seen)
    for text, lang in (("القائمة", "iq"), ("menu", "en"), ("Help", "en"), ("مساعدة", "iq"), ("القائمة؟", "iq")):
        menu = w.say(text)
        assert menu.kind == "ask" and menu.lines == [render("menu_intro", "f", lang)], text
        assert [o.title for o in menu.options] == [render(f"menu_{row}", "f", lang) for row in MENU_ROWS]
    assert len(w.core.seen) == read  # answered in code
    assert wants_menu("Main menu") and not wants_menu("دز لهادي") and not wants_menu("شنو القائمة مال المطعم")


def test_menu_rows_start_their_flow_in_code_with_no_model_call(w):
    w.say("هلا")
    read = len(w.core.seen)
    transfer = w.tap("opt_m_transfer")
    assert transfer.kind == "ask" and transfer.ask_field == "payee" and transfer.lines == [render("ask_payee", "f", "iq")]
    bill = w.tap("opt_m_bill")
    assert bill.kind == "ask" and bill.ask_field == "biller" and [o.title for o in bill.options] == ["المولدة", "كهرباء البيت"]
    topup = w.tap("opt_m_topup")
    assert topup.kind == "ask" and topup.ask_field == "operator" and len(topup.options) == 3
    balance = w.tap("opt_m_balance")
    assert balance.kind == "info" and balance.lines == ["رصيدج 500,000 دينار."]
    history = w.tap("opt_m_history")
    assert history.kind == "info" and history.lines == [render("plain_history_empty", "f", "iq")]
    contacts = w.tap("opt_m_contacts")
    assert contacts.lines[0] == "أسمائج المحفوظة:" and len(contacts.lines) == 6
    assert "• هادي، الرقم ينتهي بـ 2222" in contacts.lines and "07711112222" not in contacts.text
    account = w.tap("opt_m_account")
    assert "سارة كريم" in account.text and "4567" in account.text and "07401234567" not in account.text
    assert "500,000" in account.text and "1,000,000" in account.text
    help_ = w.tap(MENU_HELP_OPTION)
    assert help_.lines == [render("menu_help_text", "f", "iq")] and "«ليش»" in help_.text
    assert len(w.core.seen) == read and w.transactions() == []


def test_a_menu_row_then_typed_answers_ends_in_the_usual_card(worlds):
    one, other = worlds(), worlds()
    typed = [one.say(text) for text in ("دز لهادي", "خمسين الف")][-1]
    other.tap("opt_m_transfer")
    other.say("هادي")
    tapped = other.say("خمسين الف")
    same_quote(one, typed, other, tapped)


def test_history_row_lists_the_wallets_own_transactions(w):
    card = w.say("دز لهادي خمسين الف")
    w.session.handle_button(UID, card.quote_id, PAY, pin=PIN)
    history = w.tap("opt_m_history")
    assert history.lines[0] == "آخر عملياتج:" and len(history.lines) == 2
    assert "50,250" in history.lines[1] and "هادي" in history.lines[1]


def test_a_menu_row_voids_an_open_card_like_any_new_request(w):
    card = w.say("دز لهادي خمسين الف")
    balance = w.tap("opt_m_balance")
    assert balance.lines == [render("card_voided", "f", "iq"), "رصيدج 500,000 دينار."]
    assert w.quote(card.quote_id)["state"] == "CANCELLED"
    assert w.session.handle_button(UID, card.quote_id, PAY, pin=PIN).kind == "refuse" and w.transactions() == []


def test_asking_for_the_menu_object_changes_nothing(w):
    card = w.say("دز لهادي خمسين الف")
    menu = w.session.menu(UID)
    assert len(menu.options) == len(MENU_ROWS) and all(isinstance(o, Option) for o in menu.options)
    assert w.session.state(UID).card is not None and w.quote(card.quote_id)["state"] == "QUOTED"


def test_the_menu_follows_the_language_of_the_conversation(w):
    w.say("what is my balance")
    menu = w.say("menu")
    assert menu.lang == "en" and [o.title for o in menu.options][:3] == ["Send money", "Pay a bill", "Top up credit"]
    asked = w.tap("opt_m_bill")
    assert asked.lang == "en" and asked.options_title == "Choose"
    w.say("أريد أن أحوّل إلى أحمد خمسين ألف دينار")
    assert [o.title for o in w.session.menu(UID).options][0] == "تحويل أموال"


def test_a_finished_payment_offers_the_menu_and_how_it_was_checked(w):
    card = w.say("دز لهادي خمسين الف")
    assert [o.id for o in card.options] == [EXPLAIN_OPTION] and card.options_role == "next"
    done = w.session.handle_button(UID, card.quote_id, PAY, pin=PIN)
    assert done.kind == "done" and done.options_role == "next" and done.options_title == "القائمة"
    assert [o.id for o in done.options] == [EXPLAIN_OPTION, *[f"opt_m_{row}" for row in MENU_ROWS]]
    assert done.options[0].title == "شلون تأكدت؟"


def test_the_explain_words_are_whole_messages_only():
    for text in ("ليش", "ليش؟", "شلون تأكدت؟", "التفاصيل", "why", "Why?", "details", "لماذا"):
        assert wants_explanation(text), text
    for text in ("ليش ما اندفعت", "why not", "دز لهادي", "", "تفاصيل الفاتورة", "شلون ادفع"):
        assert not wants_explanation(text), text


def test_an_old_option_after_the_change_button_says_the_change_question_again(w):
    asked = w.say("دز لأحمد خمسين")
    card = w.tap(asked.options[0].id)
    change = w.session.handle_button(UID, card.quote_id, "غيّر")
    assert change.kind == "ask" and change.ask_field == "change"
    reply = w.tap(asked.options[1].id)
    assert reply.kind == "ask" and reply.ask_field == "change"
    assert reply.lines == [render("option_stale", "f", "iq"), *change.lines] and reply.options == []
    assert w.say("ستين الف").kind == "card"  # and the typed change still lands on the same request


def test_a_menu_or_why_word_in_latin_letters_is_answered_in_english(w):
    from agent.session import command_language

    assert command_language("menu", "iq") == "en" and command_language("Details", "msa") == "en"
    assert command_language("القائمة", "iq") == "iq" and command_language("لماذا", "iq") == "msa"
    assert command_language("دز لهادي", "iq") == "iq" and command_language("menu", "en") == "en"
    assert w.say("options").lang == "en" and w.say("خيارات").lang == "iq"
    assert Session.command_language is command_language
