"""End-to-end tests: Session + StubCore + the real mock wallet (in memory, small synthetic seed)."""

from __future__ import annotations

import copy

import httpx
import pytest
from fastapi.testclient import TestClient

from agent.session import Session
from agent.wallet_client import WalletClient
from core.interface import Intent
from core.stub import StubCore
from wallet.api import create_app

PAY = "إي، ادفع"
PIN = "1234"
UID = "u1"

SEED = {
    "users": [
        {"id": "u1", "name": "سارة كريم", "phone": "07801234567", "balance": 500_000,
         "daily_limit": 1_000_000, "pin": PIN, "gender": "f"},
    ],
    "contacts": [
        {"contact_id": "c1", "user_id": "u1", "display_name": "هادي", "phone": "07711112222"},
        {"contact_id": "c2", "user_id": "u1", "display_name": "أحمد", "phone": "07801110001"},
        {"contact_id": "c3", "user_id": "u1", "display_name": "أحمد", "phone": "07502220002"},
        {"contact_id": "c4", "user_id": "u1", "display_name": "زينب", "phone": "07903334444"},
    ],
    "billers": [
        {"id": "generator", "kind": "generator", "name_ar": "مولدة الحجي جاسم", "name_en": "Hajji Jasim Generator"},
        {"id": "electricity", "kind": "electricity", "name_ar": "كهرباء الوطنية", "name_en": "National Electricity"},
    ],
    "bill_accounts": [
        {"id": "a1", "user_id": "u1", "biller_id": "generator", "account_no": "350",
         "holder_name": "سارة كريم", "label_ar": "المولدة"},
        {"id": "a2", "user_id": "u1", "biller_id": "electricity", "account_no": "2024939794",
         "holder_name": "سارة كريم", "label_ar": "كهرباء البيت"},
    ],
    "bills": [
        {"id": "b1", "account_id": "a1", "period": "2026-10", "amount_due": 45_000, "status": "unpaid"},
        {"id": "b2", "account_id": "a2", "period": "2026-09", "amount_due": 50_000, "status": "paid"},
        {"id": "b3", "account_id": "a2", "period": "2026-10", "amount_due": 60_000, "status": "paid"},
    ],
    "transactions": [],
}


class FlakyNetwork:
    """Passes requests to the wallet, but the line to /quotes drops right after the first execute."""

    def __init__(self, client: TestClient):
        self.client = client
        self.down = False

    def request(self, method: str, path: str, **kwargs):
        if self.down and path.startswith("/quotes"):
            raise httpx.ConnectError("network down")
        response = self.client.request(method, path, **kwargs)
        if path.endswith("/execute"):
            self.down = True
        return response


class CutLine:
    """Passes requests to the wallet; after the first execute, nothing answers any more."""

    def __init__(self, client: TestClient):
        self.client = client
        self.down = False

    def request(self, method: str, path: str, **kwargs):
        if self.down:
            raise httpx.ConnectError("network down")
        response = self.client.request(method, path, **kwargs)
        if path.endswith("/execute"):
            self.down = True
        return response


class Blocked:
    """Passes requests to the wallet, except paths that start with one of `prefixes`."""

    def __init__(self, client: TestClient, *prefixes: str):
        self.client = client
        self.prefixes = prefixes

    def request(self, method: str, path: str, **kwargs):
        if path.startswith(self.prefixes):
            raise httpx.ConnectError("network down")
        return self.client.request(method, path, **kwargs)


class SpyCore(StubCore):
    """The stand-in core, recording every message it is shown."""

    def __init__(self):
        self.seen: list[str] = []

    def understand(self, text, context=None):
        self.seen.append(text)
        return super().understand(text, context)


class FixedCore(StubCore):
    """A core that claims words the user never wrote (to test the verbatim check)."""

    def __init__(self, intent: Intent):
        self.intent = intent

    def understand(self, text, context=None):
        return self.intent


@pytest.fixture
def client():
    with TestClient(create_app(":memory:", seed=SEED)) as c:
        yield c


@pytest.fixture
def session(client):
    return Session(wallet=client, core=StubCore())


def balance(client) -> int:
    return client.get(f"/users/{UID}").json()["balance"]


def transactions(client) -> list[dict]:
    return client.get(f"/users/{UID}/transactions").json()


def assert_audit_ok(client) -> None:
    audit = client.get("/admin/audit").json()
    assert audit["ok"], audit["violations"]


# ---------------------------------------------------------------- happy path


def test_clear_transfer_shows_card_then_pays_once(session, client):
    reply = session.handle_text(UID, "دز لهادي خمسين الف")
    assert reply.kind == "card" and reply.card is not None
    assert "50,000" in reply.card.text and "هادي" in reply.card.text and "2222" in reply.card.text
    assert [b.label for b in reply.card.buttons] == [PAY, "غيّر", "لا، بطّلت"]
    assert balance(client) == 500_000  # a card moves nothing

    done = session.handle_button(UID, reply.quote_id, PAY, pin=PIN)
    assert done.kind == "done" and done.state == "SUCCEEDED"
    assert balance(client) == 500_000 - 50_000 - 250
    assert client.get(f"/quotes/{reply.quote_id}").json()["state"] == "SUCCEEDED"
    assert len(transactions(client)) == 1
    assert_audit_ok(client)

    again = session.handle_button(UID, reply.quote_id, PAY, pin=PIN)  # a second tap never pays twice
    assert again.kind == "refuse"
    assert balance(client) == 500_000 - 50_250


def test_bill_card_uses_the_billers_amount(session, client):
    reply = session.handle_text(UID, "ادفع المولدة")
    assert reply.kind == "card"
    assert "45,000" in reply.card.text and "مولدة الحجي جاسم" in reply.card.text
    done = session.handle_button(UID, reply.quote_id, PAY, pin=PIN)
    assert done.state == "SUCCEEDED"
    assert balance(client) == 500_000 - 45_000
    assert client.get("/bills", params={"account_id": "a1"}).json()[0]["status"] == "paid"


# ---------------------------------------------------------------- asking instead of guessing


def test_two_ahmeds_ask_which_one(session, client):
    reply = session.handle_text(UID, "دز لأحمد خمسين")
    assert reply.kind == "ask" and reply.ask_field == "payee"
    assert reply.card is None
    assert "0001" in reply.text and "0002" in reply.text  # both shown by their last digits
    assert session.state(UID).card is None

    again = session.handle_text(UID, "اللي رقمه ينتهي 9999")  # fits neither: ask again, same choices
    assert again.kind == "ask" and "0001" in again.text and "0002" in again.text

    picked = session.handle_text(UID, "الثاني")
    assert picked.kind == "card"
    quote = client.get(f"/quotes/{picked.quote_id}").json()
    assert quote["target"]["contact_id"] == "c3" and quote["amount"] == 50_000


def test_dollars_ask_for_dinars_and_never_convert(session, client):
    reply = session.handle_text(UID, "دز لهادي ورقة")
    assert reply.kind == "ask" and reply.ask_field == "amount"
    assert "بالدينار" in reply.text and reply.card is None

    card = session.handle_text(UID, "مية وخمسين الف")
    assert card.kind == "card"
    assert client.get(f"/quotes/{card.quote_id}").json()["amount"] == 150_000


def test_ambiguous_bill_asks_which_one(session):
    reply = session.handle_text(UID, "ادفع الفاتورة")
    assert reply.kind == "ask" and reply.ask_field == "biller"
    assert "المولدة" in reply.text and "كهرباء البيت" in reply.text


def test_core_cannot_invent_an_amount(client):
    """The core says "مية" but the user never wrote an amount: the orchestrator asks."""
    invented = Intent("transfer", payee_mention="لهادي", amount_text="مية")
    session = Session(wallet=client, core=FixedCore(invented))
    reply = session.handle_text(UID, "دز لهادي")
    assert reply.kind == "ask" and reply.ask_field == "amount"
    assert reply.card is None


def test_hedged_amount_asks_even_if_the_core_drops_the_hedge(client):
    lossy = Intent("transfer", payee_mention="لهادي", amount_text="خمسين")
    session = Session(wallet=client, core=FixedCore(lossy))
    reply = session.handle_text(UID, "دز لهادي حوالي خمسين")
    assert reply.kind == "ask" and reply.ask_field == "amount"


def test_no_with_a_new_amount_corrects_the_card(session, client):
    first = session.handle_text(UID, "دز لهادي خمسين الف")
    fixed = session.handle_text(UID, "لا خليها عشرين")
    assert fixed.kind == "card"
    assert client.get(f"/quotes/{fixed.quote_id}").json()["amount"] == 20_000
    assert client.get(f"/quotes/{first.quote_id}").json()["state"] == "CANCELLED"


def test_relation_word_is_saved_after_the_user_names_the_person(session, client):
    reply = session.handle_text(UID, "دز لأختي عشرين الف")
    assert reply.kind == "ask" and reply.ask_field == "payee"
    card = session.handle_text(UID, "زينب")
    assert card.kind == "card" and "حفظت زينب" in card.text
    session.handle_button(UID, card.quote_id, "لا، بطّلت")

    again = session.handle_text(UID, "دز لأختي عشرة الاف")  # now known: straight to a card
    assert again.kind == "card"
    assert client.get(f"/quotes/{again.quote_id}").json()["target"]["contact_id"] == "c4"


# ---------------------------------------------------------------- confirmation discipline


def test_typed_yes_never_pays(session, client):
    reply = session.handle_text(UID, "دز لهادي خمسين الف")
    yes = session.handle_text(UID, "إي")
    assert yes.kind == "card" and yes.quote_id == reply.quote_id  # same card, pointing at the button
    assert "زر" in yes.text
    assert balance(client) == 500_000 and transactions(client) == []
    assert client.get(f"/quotes/{reply.quote_id}").json()["state"] == "QUOTED"


def test_new_text_voids_the_open_card(session, client):
    first = session.handle_text(UID, "دز لهادي خمسين الف")
    second = session.handle_text(UID, "ادفع المولدة")
    assert second.kind == "card" and second.quote_id != first.quote_id
    assert client.get(f"/quotes/{first.quote_id}").json()["state"] == "CANCELLED"
    stale = session.handle_button(UID, first.quote_id, PAY, pin=PIN)
    assert stale.kind == "refuse"
    assert balance(client) == 500_000


def test_wrong_pin_keeps_the_card_and_moves_nothing(session, client):
    card = session.handle_text(UID, "دز لهادي خمسين الف")
    wrong = session.handle_button(UID, card.quote_id, PAY, pin="0000")
    assert wrong.kind == "card" and "ما انخصم" in wrong.text
    assert balance(client) == 500_000
    right = session.handle_button(UID, card.quote_id, PAY, pin=PIN)
    assert right.state == "SUCCEEDED"


def test_cancel_button_and_cancel_words(session, client):
    card = session.handle_text(UID, "دز لهادي خمسين الف")
    reply = session.handle_text(UID, "لا بطلت")
    assert reply.kind == "cancelled" and "ما انخصم" in reply.text
    assert client.get(f"/quotes/{card.quote_id}").json()["state"] == "CANCELLED"


def test_two_in_one_offers_the_second_card_after_the_first(session, client):
    first = session.handle_text(UID, "ادفع المولدة ودز لهادي عشرين الف")
    assert first.kind == "card"
    assert client.get(f"/quotes/{first.quote_id}").json()["kind"] == "bill"
    after = session.handle_button(UID, first.quote_id, PAY, pin=PIN)
    assert after.kind == "card" and after.card is not None
    assert client.get(f"/quotes/{after.quote_id}").json()["kind"] == "transfer"
    session.handle_button(UID, after.quote_id, PAY, pin=PIN)
    assert balance(client) == 500_000 - 45_000 - 20_000 - 250


# ---------------------------------------------------------------- honest refusals


def test_already_paid_bill_is_refused_honestly(session, client):
    reply = session.handle_text(UID, "ادفع الكهرباء")
    assert reply.kind == "refuse" and reply.card is None
    assert "مدفوعة" in reply.text and "ما انخصم منج شي" in reply.text
    assert balance(client) == 500_000


def test_over_balance_is_refused_with_numbers(session, client):
    reply = session.handle_text(UID, "دز لهادي مليون")
    assert reply.kind == "refuse"
    assert "500,000" in reply.text and "ما انخصم" in reply.text


# ---------------------------------------------------------------- lost answers (UNKNOWN)


def test_drop_after_commit_is_retried_with_the_same_key(session, client):
    card = session.handle_text(UID, "دز لهادي خمسين الف")
    client.post("/admin/faults", json={"mode": "drop_after_commit", "times": 1})
    done = session.handle_button(UID, card.quote_id, PAY, pin=PIN)
    assert done.state == "SUCCEEDED"  # the retry got the stored result
    assert balance(client) == 500_000 - 50_250  # debited exactly once
    assert [t["quote_id"] for t in transactions(client)] == [card.quote_id]
    assert_audit_ok(client)


def test_unknown_outcome_is_reported_honestly_then_resolved(client):
    network = FlakyNetwork(client)
    session = Session(wallet=WalletClient(network), core=StubCore())
    card = session.handle_text(UID, "دز لهادي خمسين الف")
    client.post("/admin/faults", json={"mode": "drop_after_commit", "times": 1})

    unknown = session.handle_button(UID, card.quote_id, PAY, pin=PIN)
    assert unknown.kind == "unknown" and unknown.state == "UNKNOWN"
    assert unknown.text == "لهسه ما نعرف إذا انحوّلت، لا تعيديها. راح نتأكد ونرجعلج خبر."

    network.down = False  # the line is back: the next message reports what really happened
    later = session.handle_text(UID, "شكد رصيدي")
    assert "تمت" in later.text and "هادي" in later.text
    assert session.state(UID).unknown == {}
    assert balance(client) == 500_000 - 50_250  # once, not twice
    assert [t["quote_id"] for t in transactions(client)] == [card.quote_id]
    assert_audit_ok(client)


def test_unknown_that_never_ran_is_closed_and_reported_as_not_paid(client):
    network = FlakyNetwork(client)
    session = Session(wallet=WalletClient(network), core=StubCore())
    card = session.handle_text(UID, "دز لهادي خمسين الف")
    client.post("/admin/faults", json={"mode": "timeout_before_commit", "times": 1})

    unknown = session.handle_button(UID, card.quote_id, PAY, pin=PIN)
    assert unknown.kind == "unknown"

    network.down = False
    later = session.reconcile(UID)
    assert "ما تمت" in later.text and "ما انخصم" in later.text
    assert client.get(f"/quotes/{card.quote_id}").json()["state"] == "CANCELLED"  # can never run later
    assert balance(client) == 500_000 and transactions(client) == []
    assert_audit_ok(client)


def test_settled_unknown_is_reported_even_if_the_wallet_then_stops_answering(client):
    network = FlakyNetwork(client)
    session = Session(wallet=WalletClient(network), core=StubCore())
    card = session.handle_text(UID, "دز لهادي خمسين الف")
    client.post("/admin/faults", json={"mode": "timeout_before_commit", "times": 1})
    assert session.handle_button(UID, card.quote_id, PAY, pin=PIN).kind == "unknown"

    session.wallet = WalletClient(Blocked(client, f"/users/{UID}/contacts"))
    later = session.handle_text(UID, "دز لزينب عشرة الاف")
    assert later.lines[0].startswith("تأكدنا: العملية ما تمت")  # the promised news is not lost
    assert later.lines[-1].startswith("المحفظة ما ترد")
    assert session.state(UID).unknown == {}
    assert balance(client) == 500_000


# ---------------------------------------------------------------- review fixes


def test_two_in_one_keeps_the_paid_result_when_the_wallet_then_drops(client):
    session = Session(wallet=WalletClient(CutLine(client)), core=StubCore())
    first = session.handle_text(UID, "ادفع المولدة ودز لهادي عشرين الف")
    after = session.handle_button(UID, first.quote_id, PAY, pin=PIN)
    assert after.kind == "done" and after.state == "SUCCEEDED"
    assert after.lines[0].startswith("تمت.")  # never replaced by "ما انخصم منج شي"
    assert "الطلب الثاني" in after.lines[-1] and "المحفظة ما ترد" not in after.text
    assert session.state(UID).queue == []
    assert balance(client) == 500_000 - 45_000


def test_five_wrong_pins_lock_and_close_the_card(session, client):
    card = session.handle_text(UID, "دز لهادي خمسين الف")
    for _ in range(4):
        assert session.handle_button(UID, card.quote_id, PAY, pin="0000").kind == "card"
    locked = session.handle_button(UID, card.quote_id, PAY, pin="0000")
    assert locked.kind == "failed" and locked.card is None
    assert "انقفل" in locked.text and "ما انخصم" in locked.text
    assert client.get(f"/quotes/{card.quote_id}").json()["state"] == "CANCELLED"

    again = session.handle_text(UID, "دز لهادي خمسين الف")
    assert "انقفل" in session.handle_button(UID, again.quote_id, PAY, pin=PIN).text
    assert balance(client) == 500_000


def test_pin_typed_in_the_chat_never_reaches_the_core(client):
    core = SpyCore()
    session = Session(wallet=client, core=core)
    card = session.handle_text(UID, "دز لهادي خمسين الف")
    ask = session.handle_button(UID, card.quote_id, PAY)  # tapped without a PIN
    assert ask.card is not None and "خانة الرمز" in ask.text
    for typed in ("1234", "١٢٣٤"):
        reply = session.handle_text(UID, typed)
        assert reply.kind == "card" and reply.quote_id == card.quote_id and "بالدردشة" in reply.text
    assert core.seen == ["دز لهادي خمسين الف"]
    assert client.get(f"/quotes/{card.quote_id}").json()["state"] == "QUOTED"
    assert session.handle_button(UID, card.quote_id, PAY, pin=PIN).state == "SUCCEEDED"


def test_pin_in_eastern_digits_pays(session, client):
    card = session.handle_text(UID, "دز لهادي خمسين الف")
    assert session.handle_button(UID, card.quote_id, PAY, pin="١٢٣٤").state == "SUCCEEDED"
    assert balance(client) == 500_000 - 50_250


@pytest.mark.parametrize("text, fragment, says", [
    ("دز لهادي مية وخمسين", "مية", "ما فهمت المبلغ"),
    ("دز لهادي خمسة وعشرين", "خمسة", "ما فهمت المبلغ"),
    ("دز لهادي خمسين وورقة", "خمسين", "بالدينار"),
    ("دز لهادي مليون ونص", "مليون", "ما فهمت المبلغ"),
])
def test_core_pointing_at_part_of_the_amount_asks(client, text, fragment, says):
    partial = Intent("transfer", payee_mention="لهادي", amount_text=fragment)
    session = Session(wallet=client, core=FixedCore(partial))
    reply = session.handle_text(UID, text)
    assert reply.kind == "ask" and reply.ask_field == "amount" and reply.card is None
    assert says in reply.text


@pytest.mark.parametrize("answer", ["مو الأول", "مو الاول، الثاني", "الأول لا"])
def test_a_negated_or_double_pick_asks_again(session, answer):
    session.handle_text(UID, "دز لأحمد خمسين")
    reply = session.handle_text(UID, answer)
    assert reply.kind == "ask" and reply.ask_field == "payee" and reply.card is None


def test_digits_in_an_answer_never_pick_a_contact_we_did_not_list():
    seed = copy.deepcopy(SEED)
    seed["contacts"].append({"contact_id": "c5", "user_id": UID, "display_name": "حسين جبار",
                             "phone": "07701112250"})
    with TestClient(create_app(":memory:", seed=seed)) as client:
        session = Session(wallet=client, core=StubCore())
        assert session.handle_text(UID, "دز لاختي عشرين الف").ask_field == "payee"
        reply = session.handle_text(UID, "زينب 2250")
        assert "حسين" not in reply.text
        if reply.card is not None:
            assert client.get(f"/quotes/{reply.quote_id}").json()["target"]["contact_id"] == "c4"
        assert session.state(UID).aliases.get("اختي") != "c5"


def test_relation_question_names_the_word_without_a_gender():
    with TestClient(create_app(":memory:", seed=SEED)) as client:
        reply = Session(wallet=client, core=StubCore()).handle_text(UID, "دز لامي خمسين")
    assert reply.ask_field == "payee" and "«امي»" in reply.text and "إله" not in reply.text


def test_wallet_down_reply_uses_the_users_gender():
    seed = copy.deepcopy(SEED)
    seed["users"][0]["gender"] = "m"
    with TestClient(create_app(":memory:", seed=seed)) as client:
        session = Session(wallet=client, core=StubCore())
        session.handle_text(UID, "شكد رصيدي")
        session.wallet = WalletClient(Blocked(client, "/"))
        reply = session.handle_text(UID, "شكد رصيدي")
    assert reply.text == "المحفظة ما ترد هسه. ما انخصم منك شي. جرب بعد شوية."


def test_change_on_a_bill_card_says_the_biller_sets_the_amount(session, client):
    card = session.handle_text(UID, "ادفع المولدة")
    reply = session.handle_button(UID, card.quote_id, "غيّر")
    assert reply.kind == "ask" and reply.ask_field == "change"
    assert "مولدة الحجي جاسم" in reply.text and "ما نگدر نغيّره" in reply.text
    assert "الاسم" not in reply.text
    assert client.get(f"/quotes/{card.quote_id}").json()["state"] == "CANCELLED"
