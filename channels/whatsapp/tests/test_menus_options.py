"""Options to tap on WhatsApp: reply buttons for one to three answers, a list for four to ten, the numbered text
for more (or on a transport with no lists). A tapped id goes to the orchestrator, which looks it up against the
open question; the title that comes back with a tap is never read, and no option can pay. The main menu is a
list, and «شلون تأكدت؟» says what was checked. Offline: the fake Graph API, the open StubCore, a hand-moved clock.
"""

from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from agent.session import EXPLAIN_OPTION, MENU_ROWS, Option, Session
from channels.whatsapp.app import create_app
from channels.whatsapp.channel import button_titles, fit
from channels.whatsapp.graph import (BUTTON_TITLE_MAX, LIST_BUTTON_MAX, LIST_ROW_DESCRIPTION_MAX, LIST_ROW_TITLE_MAX,
                                     Inbound)
from channels.whatsapp.tests import kit
from channels.whatsapp.tests.kit import ALICE, PIN
from channels.whatsapp.tests.test_whatsapp_channel import Harness
from core.stub import StubCore
from payments.messages import render

PAY = "إي، ادفع"
CAROL = "9647700000003"  # no account yet


class Spy(StubCore):
    """The stand-in core, counting what it is asked to read; `during` lets a test write while it reads."""

    def __init__(self):
        super().__init__()
        self.heard: list[str] = []
        self.during: dict[str, object] = {}

    def understand(self, text, context=None):
        self.heard.append(text)
        action = self.during.pop(text, None)
        if action:
            action()
        return super().understand(text, context)


class Seeded(Harness):
    """The usual harness over a seed of the test's own."""

    def __init__(self, tmp_path, seed: dict, graph=None, **overrides):
        self._seed = seed
        self._graph = graph
        super().__init__(tmp_path, **overrides)

    def start(self) -> None:
        if self._graph is not None:
            self.graph = self._graph
        self.app = create_app(self.settings(), graph=self.graph, core=self.core, seed=copy.deepcopy(self._seed),
                              clock=self.clock)
        self._cm = TestClient(self.app)
        self.client = self._cm.__enter__()
        self.channel = self.app.state.channel


def with_bills(count: int) -> dict:
    """ALICE with `count` saved generator accounts, so «ادفع الفاتورة» has that many answers."""
    seed = kit.seed()
    seed["bill_accounts"] = [{"id": f"a{n:02d}", "user_id": "u1", "biller_id": "generator", "account_no": str(350 + n),
                              "holder_name": "سارة كريم", "label_ar": f"مولدة {n}"} for n in range(1, count + 1)]
    seed["bills"] = [{"id": f"b{n:02d}", "account_id": f"a{n:02d}", "period": "2026-10", "amount_due": 40_000 + n * 1000,
                      "status": "unpaid"} for n in range(1, count + 1)]
    return seed


class NoLists(kit.FakeGraph):
    """A transport that cannot send a list."""

    send_list = None


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path, core=Spy())
    harness.say(ALICE, "هلا")
    harness.graph.clear()
    yield harness
    harness.stop()


def tap_id(h: Harness, who: str, option_id: str, title: str = "x") -> None:
    h.send(kit.tap(who, option_id, title, h.clock))


def row_id(h: Harness, who: str, option_id: str, title: str = "x") -> None:
    h.send(kit.list_tap(who, option_id, title, h.clock))


def buttons_of(message: dict) -> list[tuple[str, str]]:
    return [(b["reply"]["id"], b["reply"]["title"]) for b in message["action"]["buttons"]]


def rows_of(menu: dict) -> list[dict]:
    return [row for part in menu["action"]["sections"] for row in part["rows"]]


def pay(h: Harness, who: str = ALICE, pin: str = PIN) -> None:
    h.tap(who, PAY)
    h.say(who, pin)


# ---------------------------------------------------------------- how many options, which message


def test_two_options_are_two_reply_buttons_under_the_question(h):
    h.say(ALICE, "دز لأحمد خمسين")
    [message] = h.graph.to(ALICE)  # one message: the question with its answers
    assert message["type"] == "interactive" and message["interactive"]["type"] == "button"
    body = message["interactive"]["body"]["text"]
    assert "1) أحمد" in body and "2) أحمد" in body and "0001" in body  # the numbered text is still there to type
    buttons = buttons_of(message["interactive"])
    assert [title for _, title in buttons] == ["أحمد (0001)", "أحمد (0002)"]  # one name twice: told apart by the ending
    assert all(Session.is_option(bid) and ":" not in bid for bid, _ in buttons)
    assert all(len(title) <= BUTTON_TITLE_MAX for _, title in buttons)
    tap_id(h, ALICE, buttons[1][0], buttons[1][1])
    card = h.graph.last_card(ALICE)
    assert "0002" in card["body"]["text"] and "50,000 دينار" in card["body"]["text"]
    assert list(kit.card_buttons(card)) == [PAY, "غيّر", "لا، بطّلت"]  # the card's own buttons, as ever
    assert h.transactions(ALICE) == []


@pytest.mark.parametrize("count", [2, 3])
def test_up_to_three_options_are_reply_buttons(tmp_path, count):
    h = Seeded(tmp_path, with_bills(count), core=Spy())
    try:
        h.say(ALICE, "ادفع الفاتورة")
        [message] = h.graph.to(ALICE)
        assert message["interactive"]["type"] == "button"
        buttons = buttons_of(message["interactive"])
        assert [title for _, title in buttons] == [f"مولدة {n}" for n in range(1, count + 1)]
        assert all(Session.is_option(bid) for bid, _ in buttons) and len({bid for bid, _ in buttons}) == count
        tap_id(h, ALICE, buttons[-1][0])
        assert f"{40_000 + count * 1000:,} دينار" in h.graph.last_card(ALICE)["body"]["text"]
    finally:
        h.stop()


@pytest.mark.parametrize("count", [4, 10])
def test_four_to_ten_options_are_one_list_message(tmp_path, count):
    h = Seeded(tmp_path, with_bills(count), core=Spy())
    try:
        h.say(ALICE, "ادفع الفاتورة")
        [message] = h.graph.to(ALICE)
        menu = message["interactive"]
        assert message["type"] == "interactive" and menu["type"] == "list"
        assert menu["action"]["button"] == "اختاري" and len(menu["action"]["button"]) <= LIST_BUTTON_MAX
        assert len(menu["action"]["sections"]) == 1
        rows = rows_of(menu)
        assert [row["title"] for row in rows] == [f"مولدة {n}" for n in range(1, count + 1)]
        assert all(len(row["title"]) <= LIST_ROW_TITLE_MAX for row in rows)
        assert all(0 < len(row["description"]) <= LIST_ROW_DESCRIPTION_MAX for row in rows)
        assert str(350 + count) in rows[-1]["description"]  # the account number tells two bills apart
        assert all(Session.is_option(row["id"]) for row in rows) and len({row["id"] for row in rows}) == count
        assert f"{count}) مولدة {count}" in menu["body"]["text"]
        heard = len(h.core.heard)
        row_id(h, ALICE, rows[-1]["id"], rows[-1]["title"])
        assert f"{40_000 + count * 1000:,} دينار" in h.graph.last_card(ALICE)["body"]["text"]
        assert len(h.core.heard) == heard  # a tapped row is looked up, never read by the core
    finally:
        h.stop()


def test_eleven_options_stay_the_numbered_text(tmp_path):
    h = Seeded(tmp_path, with_bills(11), core=Spy())
    try:
        h.say(ALICE, "ادفع الفاتورة")
        assert h.graph.kinds(ALICE) == ["text"]
        [question] = h.graph.texts(ALICE)
        assert "1) مولدة 1" in question and "11) مولدة 11" in question
        h.say(ALICE, "الثاني")  # answered by typing, as before
        assert "42,000 دينار" in h.graph.last_card(ALICE)["body"]["text"]
    finally:
        h.stop()


def test_a_transport_with_no_lists_keeps_buttons_and_falls_back_to_text(tmp_path):
    few = Seeded(tmp_path / "few", with_bills(3), graph=NoLists(), core=Spy())
    try:
        few.say(ALICE, "ادفع الفاتورة")
        assert few.graph.to(ALICE)[0]["interactive"]["type"] == "button"
    finally:
        few.stop()
    many = Seeded(tmp_path / "many", with_bills(4), graph=NoLists(), core=Spy())
    try:
        many.say(ALICE, "ادفع الفاتورة")
        assert many.graph.kinds(ALICE) == ["text"] and "4) مولدة 4" in many.graph.texts(ALICE)[0]
        many.say(ALICE, "القائمة")  # the menu too: its line as text, and requests are typed
        assert many.graph.kinds(ALICE) == ["text", "text"]
        assert many.graph.texts(ALICE)[-1] == render("menu_intro", "f", "iq")
    finally:
        many.stop()


def test_a_send_the_transport_refuses_leaves_the_question_as_text(tmp_path):
    class Refusing(kit.FakeGraph):
        def send_buttons(self, to, has_phone, body, buttons, header="", footer=""):
            if any(bid.startswith("opt_") for bid, _ in buttons):
                raise RuntimeError("refused")
            super().send_buttons(to, has_phone, body, buttons, header, footer)

    h = Seeded(tmp_path, kit.seed(), graph=Refusing(), core=Spy())
    try:
        h.say(ALICE, "دز لأحمد خمسين")
        assert h.graph.kinds(ALICE) == ["text"] and "2) أحمد" in h.graph.texts(ALICE)[0]
        h.say(ALICE, "الثاني")
        assert "0002" in h.graph.last_card(ALICE)["body"]["text"]
    finally:
        h.stop()


def test_a_free_text_question_is_plain_text(h):
    h.say(ALICE, "دز لهادي")
    assert h.graph.kinds(ALICE) == ["text"]  # how much: nothing to tap


def test_yes_or_no_questions_are_two_buttons(h):
    h.say(ALICE, "ادفع المولدة 20 الف")
    [message] = h.graph.to(ALICE)
    buttons = buttons_of(message["interactive"])
    assert [title for _, title in buttons] == ["إي، كاملة", "لا"]
    tap_id(h, ALICE, buttons[0][0])
    assert "45,000 دينار" in h.graph.last_card(ALICE)["body"]["text"]
    h.say(ALICE, "ادفع المولدة 20 الف")
    tap_id(h, ALICE, buttons_of(h.graph.cards(ALICE)[-1])[1][0])  # «لا»
    assert h.graph.texts(ALICE)[-1] == render("cancelled", "f", "iq")
    assert h.transactions(ALICE) == []


# ---------------------------------------------------------------- titles


def test_a_title_is_clipped_without_cutting_its_number_ending():
    assert fit("أحمد", 20) == "أحمد"
    long_name = "أحمد عبد الرحمن عبد الكريم الجبوري"
    assert fit(long_name, 20) == long_name[:19].rstrip() + "…" and len(fit(long_name, 20)) <= 20
    clipped = fit(f"{long_name} 0001", 20)
    assert clipped.endswith("0001") and len(clipped) <= 20 and "…" in clipped
    assert fit(f"{long_name} (4567)", 20).endswith("(4567)")
    assert fit("مبلغ كبير جداً جداً جداً 1,500,000", 20).endswith("1,500,000")
    assert fit("", 20) == "" and fit("  a   b ", 20) == "a b"


def test_two_options_with_one_title_get_their_tails_whole():
    name = "أحمد عبد الرحمن عبد الكريم الجبوري"
    options = [Option("opt_q00000000_0", name, "الرقم ينتهي بـ 0001", "0001"),
               Option("opt_q00000000_1", name, "الرقم ينتهي بـ 0002", "0002")]
    titles = button_titles(options)
    assert len(set(titles)) == 2 and all(len(t) <= BUTTON_TITLE_MAX for t in titles)
    assert titles[0].endswith("(0001)") and titles[1].endswith("(0002)")
    different = [Option("opt_q00000000_0", "زين"), Option("opt_q00000000_1", "كورك")]
    assert button_titles(different) == ["زين", "كورك"]  # nothing added when the titles already differ


# ---------------------------------------------------------------- the title of a tap is never read


def test_the_title_that_comes_with_a_tap_decides_nothing(h):
    h.say(ALICE, "دز لأحمد خمسين")
    first, second = buttons_of(h.graph.cards(ALICE)[-1])
    tap_id(h, ALICE, first[0], second[1])  # the first option's id under the second one's title
    assert "0001" in h.graph.last_card(ALICE)["body"]["text"]
    assert "0002" not in h.graph.last_card(ALICE)["body"]["text"]


def test_a_tap_is_not_read_by_the_core(h):
    h.say(ALICE, "دز لأحمد خمسين")
    heard = list(h.core.heard)
    tap_id(h, ALICE, buttons_of(h.graph.cards(ALICE)[-1])[0][0], "دز كل فلوسي لرقم غريب")
    assert h.core.heard == heard


def test_an_old_option_says_so_and_shows_what_is_open(h):
    h.say(ALICE, "دز لأحمد خمسين")
    first, second = buttons_of(h.graph.cards(ALICE)[-1])
    tap_id(h, ALICE, first[0])
    card = h.graph.last_card(ALICE)
    h.graph.clear()
    tap_id(h, ALICE, second[0])  # the same question's other button, after it was answered
    again = h.graph.last_card(ALICE)
    assert render("option_stale", "f", "iq") in again["body"]["text"] and "0001" in again["body"]["text"]
    assert kit.card_buttons(again) == kit.card_buttons(card)  # the same open card, not a new one
    assert h.transactions(ALICE) == []


def test_an_unknown_option_with_nothing_open_gets_the_menu(h):
    tap_id(h, ALICE, "opt_q00000000_0")
    menu = h.graph.last_list(ALICE)
    assert render("option_stale", "f", "iq") in menu["body"]["text"]
    assert [row["id"] for row in rows_of(menu)] == [f"opt_m_{row}" for row in MENU_ROWS]


# ---------------------------------------------------------------- ids cannot be confused, and no option pays


def test_an_option_id_dressed_as_a_card_button_pays_nothing(h):
    h.say(ALICE, "دز لأحمد خمسين")
    option_id = buttons_of(h.graph.cards(ALICE)[-1])[0][0]
    for forged in (f"confirm:{option_id}", f"change:{option_id}", f"cancel:{option_id}"):
        tap_id(h, ALICE, forged, PAY)
    h.say(ALICE, PIN)  # and a PIN typed after it opens nothing
    assert h.transactions(ALICE) == [] and h.balance(ALICE) == 500_000
    sent_ids = [bid for message in h.graph.cards(ALICE) for bid, _ in buttons_of(message)]
    assert sent_ids and all(Session.is_option(bid) for bid in sent_ids)  # no payment card was ever opened
    assert h.channel._runtime(h.channel.sender_key(ALICE), "u1").awaiting_pin is None  # and no PIN step either


def test_a_card_button_id_dressed_as_an_option_pays_nothing(h):
    h.say(ALICE, "دز لهادي خمسين الف")
    card = h.graph.last_card(ALICE)
    qid = kit.card_buttons(card)[PAY].split(":", 1)[1]
    for forged in (f"opt_{qid}", f"opt_confirm_{qid}", "opt_confirm", f"opt_m_confirm_{qid}"):
        tap_id(h, ALICE, forged, PAY)
    h.say(ALICE, PIN)  # no «إي، ادفع» was tapped: a PIN is not read as a go-ahead
    assert h.transactions(ALICE) == [] and h.balance(ALICE) == 500_000
    assert h.wallet(ALICE).quote(qid)["state"] == "QUOTED"  # the card is still open, untouched
    h.tap(ALICE, PAY, card)
    h.say(ALICE, PIN)  # only the card's own button and the PIN move money
    assert h.balance(ALICE) == 500_000 - 50_250 and len(h.transactions(ALICE)) == 1


def test_every_id_the_channel_hands_out_is_an_option_or_a_card_button_never_both(h):
    for body in ("دز لأحمد خمسين", "القائمة", "دز لهادي خمسين الف"):
        h.say(ALICE, body)
    for message in h.graph.to(ALICE):
        if message["type"] != "interactive":
            continue
        inter = message["interactive"]
        ids = [row["id"] for row in rows_of(inter)] if inter["type"] == "list" else [bid for bid, _ in buttons_of(inter)]
        shapes = {Session.is_option(i) for i in ids}
        assert len(shapes) == 1  # one message holds options or card buttons, never a mix
        if shapes == {False}:
            assert all(i.split(":")[0] in ("confirm", "change", "cancel") and ":" in i for i in ids)


def test_tapping_everything_on_offer_never_moves_money(h):
    seen: list[str] = [EXPLAIN_OPTION, *[f"opt_m_{row}" for row in MENU_ROWS]]
    for body in ("دز لأحمد خمسين", "ادفع المولدة 20 الف", "دز لهادي خمسين او ستين", "دز لهادي خمسين الف"):
        h.say(ALICE, body)
        for message in h.graph.to(ALICE):
            if message["type"] == "interactive":
                inter = message["interactive"]
                ids = [r["id"] for r in rows_of(inter)] if inter["type"] == "list" else [b for b, _ in buttons_of(inter)]
                seen += [i for i in ids if Session.is_option(i) and i not in seen]
        for option_id in list(seen):
            tap_id(h, ALICE, option_id)
    assert h.transactions(ALICE) == [] and h.balance(ALICE) == 500_000


# ---------------------------------------------------------------- the menu


def test_the_menu_is_one_list_with_its_eight_rows(h):
    h.say(ALICE, "القائمة")
    [message] = h.graph.to(ALICE)
    menu = message["interactive"]
    assert menu["type"] == "list" and menu["action"]["button"] == "القائمة"
    assert menu["body"]["text"] == render("menu_intro", "f", "iq")
    rows = rows_of(menu)
    assert [row["id"] for row in rows] == [f"opt_m_{row}" for row in MENU_ROWS]
    assert [row["title"] for row in rows] == ["تحويل فلوس", "دفع فاتورة", "شحن رصيد", "رصيدي", "آخر العمليات",
                                              "أسمائي", "حسابي", "مساعدة"]
    assert all(row["description"] for row in rows)
    assert h.core.heard == []  # the greeting and the menu were answered in code


@pytest.mark.parametrize("words, first_row, button", [("menu", "Send money", "Menu"),
                                                      ("ما هي القائمة الرئيسية؟ أريد أن أرى الخيارات", None, None),
                                                      ("القائمة الرئيسية", "تحويل فلوس", "القائمة")])
def test_the_menu_words_and_its_language(h, words, first_row, button):
    h.say(ALICE, words)
    if first_row is None:  # a sentence about a menu is not the menu word: it is read like any message
        assert h.core.heard == [words]
        return
    menu = h.graph.last_list(ALICE)
    assert rows_of(menu)[0]["title"] == first_row and menu["action"]["button"] == button


def test_menu_rows_start_their_flows_with_no_model_call(h):
    h.say(ALICE, "القائمة")
    rows = kit.list_rows(h.graph.last_list(ALICE))
    row_id(h, ALICE, rows["رصيدي"], "رصيدي")
    assert h.graph.texts(ALICE)[-1] == "رصيدج 500,000 دينار."
    row_id(h, ALICE, rows["آخر العمليات"])
    assert h.graph.texts(ALICE)[-1]  # the account service's own history
    row_id(h, ALICE, rows["أسمائي"])
    assert "هادي" in h.graph.texts(ALICE)[-1] and "07711112222" not in h.graph.texts(ALICE)[-1].replace(" ", "")
    row_id(h, ALICE, rows["حسابي"])
    assert "سارة كريم" in h.graph.texts(ALICE)[-1]
    row_id(h, ALICE, rows["مساعدة"])
    assert "«إي، ادفع»" in h.graph.texts(ALICE)[-1]  # this channel's own help, with its commands
    row_id(h, ALICE, rows["دفع فاتورة"])
    assert "مولدة الحجي جاسم" in h.graph.last_card(ALICE)["body"]["text"]  # one saved bill: its card
    row_id(h, ALICE, rows["تحويل فلوس"])
    assert h.graph.texts(ALICE)[-1].endswith(render("ask_payee", "f", "iq"))
    h.say(ALICE, "هادي")
    h.say(ALICE, "خمسين الف")
    assert "50,000 دينار" in h.graph.last_card(ALICE)["body"]["text"]
    assert h.core.heard == ["هادي", "خمسين الف"]  # only what was typed was ever read
    assert h.transactions(ALICE) == []


def test_to_whom_offers_the_people_paid_last_as_buttons(h):
    h.say(ALICE, "دز لهادي عشرة الاف")
    pay(h)
    h.graph.clear()
    h.say(ALICE, "دز عشرين الف")
    [message] = h.graph.to(ALICE)
    buttons = buttons_of(message["interactive"])
    assert [title for _, title in buttons] == ["هادي"]
    tap_id(h, ALICE, buttons[0][0])
    assert "20,000 دينار" in h.graph.last_card(ALICE)["body"]["text"] and "هادي" in h.graph.last_card(ALICE)["body"]["text"]


def test_a_hello_is_one_message_unless_the_menu_is_switched_on(h):
    h.say(ALICE, "هلا")
    assert h.graph.kinds(ALICE) == ["text"]
    h.graph.clear()
    h.channel.menu_on_hello = True
    h.say(ALICE, "هلا")
    assert h.graph.kinds(ALICE) == ["text", "interactive"]
    menu = h.graph.last_list(ALICE)
    assert len(rows_of(menu)) == len(MENU_ROWS) and menu["body"]["text"].startswith("اختاري من القائمة")
    h.graph.clear()
    h.say(ALICE, "مساعدة")
    assert h.graph.kinds(ALICE) == ["text", "interactive"]


def test_the_menu_follows_sign_up_when_switched_on(tmp_path):
    h = Harness(tmp_path, core=Spy())
    try:
        h.channel.menu_on_hello = True
        h.say(CAROL, "هلا")
        for button in ("o:open", "o:name_yes", "o:f"):
            tap_id(h, CAROL, button)
        h.say(CAROL, "2583")
        h.graph.clear()
        h.say(CAROL, "2583")
        assert h.channel.accounts.by_phone(CAROL)["status"] == "active"
        menu = h.graph.last_list(CAROL)
        assert [row["id"] for row in rows_of(menu)] == [f"opt_m_{row}" for row in MENU_ROWS]
        assert h.graph.kinds(CAROL)[-1] == "interactive"  # after the words that the account is open
    finally:
        h.stop()


# ---------------------------------------------------------------- after a payment or a refusal, and «ليش»


def test_a_receipt_is_followed_by_how_did_you_check_and_the_menu(h):
    h.say(ALICE, "دز لهادي خمسين الف")
    card = h.graph.last_card(ALICE)
    h.graph.clear()
    h.tap(ALICE, PAY, card)
    h.say(ALICE, PIN)
    assert h.graph.kinds(ALICE) == ["text", "text", "interactive"]  # the PIN question, the receipt, what next
    receipt = h.graph.texts(ALICE)[-1]
    assert "وصل" in receipt and "50,000" in receipt and receipt.count("رقم العملية") == 1  # the reference, once
    menu = h.graph.last_list(ALICE)
    rows = rows_of(menu)
    assert rows[0]["id"] == EXPLAIN_OPTION and rows[0]["title"] == "شلون تأكدت؟"
    assert [row["id"] for row in rows[1:]] == [f"opt_m_{row}" for row in MENU_ROWS] and len(rows) <= 10
    h.graph.clear()
    row_id(h, ALICE, EXPLAIN_OPTION, "شلون تأكدت؟")
    [why] = h.graph.texts(ALICE)
    assert why.startswith(render("why_title", "f", "iq"))
    assert "قريت سجل المحفظة بعد الدفع" in why and "حالتها ناجحة" in why
    assert "«خمسين الف»" in why and "هادي" in why and "2222" in why
    assert PIN not in why.replace("2222", "") and "07711112222" not in why
    assert len(h.transactions(ALICE)) == 1  # asking how changes nothing


def test_a_refusal_is_followed_by_how_did_you_check(h):
    h.say(ALICE, "دز لهادي عشر ملايين")
    assert h.graph.kinds(ALICE) == ["text", "interactive"]
    assert "رصيدج ما يكفي" in h.graph.texts(ALICE)[0]
    assert rows_of(h.graph.last_list(ALICE))[0]["id"] == EXPLAIN_OPTION
    h.say(ALICE, "ليش")
    why = h.graph.texts(ALICE)[-1]
    assert "500,000" in why and "10,000,250" in why and "ما يكفي، فرفضت" in why


def test_why_typed_on_a_tapped_card_keeps_the_card_and_its_pin_step(h):
    h.say(ALICE, "دز لهادي خمسين الف")
    h.tap(ALICE, PAY)
    h.graph.clear()
    h.say(ALICE, "ليش؟")
    assert h.graph.kinds(ALICE) == ["text"] and "عرض المحفظة" in h.graph.texts(ALICE)[0]
    assert h.core.heard == ["دز لهادي خمسين الف"]  # «ليش» is answered from what was recorded
    h.say(ALICE, PIN)  # the PIN step is still open
    assert h.balance(ALICE) == 500_000 - 50_250


def test_why_in_english_is_answered_in_english(h):
    h.say(ALICE, "send 50000 to Hadi")
    h.say(ALICE, "why")
    assert h.graph.texts(ALICE)[-1].startswith(render("why_title", "f", "en"))


def test_why_with_nothing_checked_yet_says_so(h):
    h.say(ALICE, "ليش")
    assert h.graph.texts(ALICE) == [render("why_nothing", "f", "iq")]


# ---------------------------------------------------------------- bursts and limits


def test_a_question_with_options_overtaken_by_its_answer_is_never_sent(tmp_path):
    core = Spy()
    h = Harness(tmp_path, core=core)
    try:
        h.say(ALICE, "هلا")
        h.graph.clear()
        core.during["دز لأحمد خمسين"] = lambda: kit.post(h.client, kit.webhook(kit.text(ALICE, "الثاني", h.clock)))
        h.say(ALICE, "دز لأحمد خمسين")
        assert h.channel.join(20)
        assert len(h.graph.to(ALICE)) == 1  # one answer for the burst: the card, never the buttons before it
        assert "0002" in h.graph.last_card(ALICE)["body"]["text"]
    finally:
        h.stop()


def test_a_question_and_its_buttons_are_one_message_after_a_burst(tmp_path):
    h = Harness(tmp_path, core=Spy(), debounce_seconds=0.6)
    try:
        h.say(ALICE, "هلا")
        h.graph.clear()
        kit.post(h.client, kit.webhook(kit.text(ALICE, "دز", h.clock), kit.text(ALICE, "لأحمد خمسين", h.clock)))
        assert h.channel.join(20)
        assert len(h.graph.to(ALICE)) == 1 and len(buttons_of(h.graph.cards(ALICE)[0])) == 2
    finally:
        h.stop()


def test_taps_count_towards_the_hourly_limit(tmp_path):
    h = Harness(tmp_path, core=Spy(), max_msgs_per_user_hour=3)
    try:
        h.say(ALICE, "هلا")
        h.say(ALICE, "دز لأحمد خمسين")
        first = buttons_of(h.graph.cards(ALICE)[-1])[0][0]
        tap_id(h, ALICE, "opt_q00000000_0")  # the third message of the hour
        h.graph.clear()
        tap_id(h, ALICE, first)  # the fourth: over the limit, so it is not looked up
        assert h.graph.cards(ALICE) == [] and len(h.graph.texts(ALICE)) == 1
        assert h.channel._runtime(h.channel.sender_key(ALICE), "u1").session.state("u1").card is None
    finally:
        h.stop()


def test_a_tapped_row_from_either_message_shape_is_a_button_event():
    """A reply button and a list row arrive the same way: kind "button" with the id (transport contract)."""
    from channels.whatsapp.graph import parse

    clock = kit.Clock()
    items = parse(kit.webhook(kit.tap(ALICE, "opt_m_balance", "رصيدي", clock),
                              kit.list_tap(ALICE, "opt_m_history", "آخر العمليات", clock)), kit.PNID)
    assert [(i.kind, i.button_id) for i in items] == [("button", "opt_m_balance"), ("button", "opt_m_history")]
    assert all(isinstance(i, Inbound) and Session.is_option(i.button_id) for i in items)
