"""The web chat's options and «what I checked»: the API the page draws its chips and its expander from.

A reply carries `options` (id, title, description) and `evidence` (what, value, source, from, line). A chip
posts only its id to /api/option; the server looks it up against the open question, so a chip answers or opens
a menu row and never pays. Offline: the real orchestrator, StubCore and an in-memory wallet.
"""

from __future__ import annotations

import copy
import json
import re
import uuid

import pytest
from fastapi.testclient import TestClient

from web.app import create_app
from web.service import DemoStore
from web.tests.test_web_app import PIN, SEED, UID, audit_ok, balance, demo, say, tap

MENU = ["transfer", "bill", "topup", "balance", "history", "contacts", "account", "help"]
WHY = "opt_why"


@pytest.fixture
def store():
    s = DemoStore(seed=copy.deepcopy(SEED))
    yield s
    s.close()


def new_client(store) -> TestClient:
    client = TestClient(create_app(store))
    client.__enter__()
    client.headers["X-Demo-Session"] = str(uuid.uuid4())
    client.post("/api/start", json={"user_id": UID}).raise_for_status()
    return client


@pytest.fixture
def clients(store):
    made: list[TestClient] = []

    def make() -> TestClient:
        made.append(new_client(store))
        return made[-1]

    yield make
    for client in made:
        client.__exit__(None, None, None)


@pytest.fixture
def client(clients):
    return clients()


def option(client, option_id: str) -> dict:
    response = client.post("/api/option", json={"option_id": option_id})
    assert response.status_code == 200, response.text
    return response.json()


def same_card(a: dict, b: dict) -> None:
    """Two cards over two wallets show the same payment (quote ids and the countdown apart)."""
    skip = ("quote_id", "expires_in", "expires_at")
    assert {k: v for k, v in a.items() if k not in skip} == {k: v for k, v in b.items() if k not in skip}


# ---------------------------------------------------------------- the shape of a reply


def test_every_reply_has_the_options_and_evidence_fields(client):
    for payload in (say(client, "هلا"), say(client, "دز لهادي خمسين الف"), say(client, "دز لأحمد خمسين")):
        reply = payload["reply"]
        assert isinstance(reply["options"], list) and isinstance(reply["evidence"], list)
        assert reply["options_role"] in ("answer", "next")
        assert set(reply) >= {"lines", "kind", "state", "quote_id", "ask_field", "lang", "options", "options_title",
                              "options_role", "evidence", "why_label", "reference"}
        for chip in reply["options"]:
            assert set(chip) == {"id", "title", "description"} and re.fullmatch(r"opt_[a-z0-9_]+", chip["id"])
        for check in reply["evidence"]:
            assert set(check) == {"what", "value", "source", "from", "line"} and check["line"] and check["from"]


def test_start_and_reset_hand_the_page_the_menu(client):
    start = client.post("/api/start", json={"user_id": UID}).json()
    for payload in (start, client.post("/api/reset").json()):
        menu = payload["menu"]
        assert menu["kind"] == "ask" and menu["options_role"] == "next" and menu["options_title"] == "القائمة"
        assert [chip["id"] for chip in menu["options"]] == [f"opt_m_{row}" for row in MENU]
        assert menu["options"][0]["title"] == "تحويل فلوس" and menu["options"][3]["title"] == "رصيدي"
        assert menu["lines"] and all(chip["description"] for chip in menu["options"])


# ---------------------------------------------------------------- chips answer questions


def test_a_question_comes_with_its_chips_and_a_tap_is_the_typed_answer(clients):
    typing, tapping = clients(), clients()
    asked = say(tapping, "دز لأحمد خمسين")
    say(typing, "دز لأحمد خمسين")
    chips = asked["reply"]["options"]
    assert asked["reply"]["kind"] == "ask" and asked["reply"]["options_role"] == "answer"
    assert [chip["title"] for chip in chips] == ["أحمد", "أحمد"]
    assert [chip["description"][-4:] for chip in chips] == ["0001", "0002"]  # the ending tells the two apart
    assert all("0780" not in chip["description"] and "0750" not in chip["description"] for chip in chips)
    assert asked["reply"]["options_title"] == "اختاري"
    by_tap = option(tapping, chips[1]["id"])
    by_text = say(typing, "الثاني")
    assert by_tap["reply"]["kind"] == by_text["reply"]["kind"] == "card"
    same_card(by_tap["card"], by_text["card"])
    assert by_tap["card"]["total"] == 50_250 and balance(tapping) == 500_000  # a card moves nothing
    done = tap(tapping, by_tap["card"]["quote_id"], "confirm", PIN)  # and it pays like any card: button and PIN
    assert done["receipt"]["status"] == "succeeded" and balance(tapping) == 500_000 - 50_250
    audit_ok(tapping)


def test_typing_still_answers_a_question_that_has_chips(client):
    asked = say(client, "دز لأحمد خمسين")
    assert asked["reply"]["options"]
    picked = say(client, "0002")
    assert picked["card"]["kind"] == "transfer" and {"label": "رقم الهاتف", "value": "ينتهي بـ 0002"} in picked["card"]["rows"]


def test_a_yes_or_no_question_has_two_chips(client):
    asked = say(client, "ادفع المولدة 20 الف")
    assert [chip["title"] for chip in asked["reply"]["options"]] == ["إي، كاملة", "لا"]
    card = option(client, asked["reply"]["options"][0]["id"])
    assert card["card"]["kind"] == "bill" and card["card"]["amount"] == 45_000
    asked = say(client, "ادفع المولدة 20 الف")
    cancelled = option(client, asked["reply"]["options"][1]["id"])
    assert cancelled["reply"]["kind"] == "cancelled" and cancelled["card"] is None


def test_a_free_text_question_has_no_chips(client):
    asked = say(client, "دز لهادي")
    assert asked["reply"]["kind"] == "ask" and asked["reply"]["options"] == []


# ---------------------------------------------------------------- old ids, and no chip pays


@pytest.mark.parametrize("option_id", ["opt_q00000000_0", "opt_nope", "confirm", "1", "أحمد"])
def test_an_old_or_unknown_chip_is_answered_politely(client, option_id):
    asked = say(client, "دز لأحمد خمسين")
    reply = option(client, option_id)["reply"]
    assert reply["kind"] == "ask" and "قديمة" in reply["lines"][0]
    assert [chip["id"] for chip in reply["options"]] == [chip["id"] for chip in asked["reply"]["options"]]
    assert balance(client) == 500_000


def test_a_chip_of_an_answered_question_shows_the_open_card_again(client):
    asked = say(client, "دز لأحمد خمسين")
    card = option(client, asked["reply"]["options"][0]["id"])
    again = option(client, asked["reply"]["options"][1]["id"])
    assert again["reply"]["kind"] == "card" and again["card"]["quote_id"] == card["card"]["quote_id"]
    assert "قديمة" in again["reply"]["lines"][0]


def test_no_chip_can_pay_and_no_chip_id_is_a_card(client):
    card = say(client, "دز لهادي خمسين الف")["card"]
    qid = card["quote_id"]
    for forged in (f"confirm:{qid}", qid, f"opt_{qid}", f"opt_confirm_{qid}", f"confirm:{qid}:{PIN}"):
        reply = option(client, forged)
        assert reply["receipt"] is None and reply["reply"]["kind"] == "card"  # the card is still open, unpaid
    assert balance(client) == 500_000
    asked = say(client, "دز لأحمد خمسين")  # and an option id sent as a card button is no open card
    for chip in asked["reply"]["options"]:
        refused = tap(client, chip["id"], "confirm", PIN)
        assert refused["reply"]["kind"] == "refuse" and refused["receipt"] is None
    assert balance(client) == 500_000 and client.get("/api/history").json()["items"] == []
    audit_ok(client)


def test_the_option_endpoint_takes_an_id_and_nothing_else(client):
    assert client.post("/api/option", json={}).status_code == 422
    assert client.post("/api/option", json={"option_id": ""}).status_code == 422
    assert client.post("/api/option", json={"option_id": "x" * 65}).status_code == 422
    asked = say(client, "دز لأحمد خمسين")
    first = asked["reply"]["options"][0]["id"]  # a title sent along is not read: the id decides
    response = client.post("/api/option", json={"option_id": first, "title": "أحمد 0002", "pin": PIN})
    assert response.status_code == 200
    assert {"label": "رقم الهاتف", "value": "ينتهي بـ 0001"} in response.json()["card"]["rows"]
    assert response.json()["receipt"] is None and balance(client) == 500_000
    assert client.post("/api/option", json={"option_id": first}, headers={"X-Demo-Session": "x"}).status_code == 400


# ---------------------------------------------------------------- the menu


def test_menu_chips_start_their_flows(client):
    balance_reply = option(client, "opt_m_balance")["reply"]
    assert balance_reply["kind"] == "info" and balance_reply["lines"] == ["رصيدج 500,000 دينار."]
    contacts = option(client, "opt_m_contacts")["reply"]
    assert any("هادي" in line and "2222" in line for line in contacts["lines"])
    assert "07711112222" not in json.dumps(contacts, ensure_ascii=False)
    account = option(client, "opt_m_account")["reply"]
    assert "سارة كريم" in " ".join(account["lines"]) and "07701234567" not in " ".join(account["lines"])
    assert option(client, "opt_m_history")["reply"]["kind"] == "info"
    assert "«ليش»" in " ".join(option(client, "opt_m_help")["reply"]["lines"])
    transfer = option(client, "opt_m_transfer")["reply"]
    assert transfer["kind"] == "ask" and transfer["ask_field"] == "payee"
    bill = option(client, "opt_m_bill")
    assert bill["card"]["kind"] == "bill"  # one saved bill: straight to its card
    assert balance(client) == 500_000


def test_the_menu_is_offered_on_hello_on_its_word_and_after_a_payment(client):
    hello = say(client, "هلا")["reply"]
    assert [chip["id"] for chip in hello["options"]] == [f"opt_m_{row}" for row in MENU] and hello["options_role"] == "next"
    for words in ("القائمة", "menu"):
        assert len(say(client, words)["reply"]["options"]) == len(MENU)
    card = say(client, "دز لهادي خمسين الف")["card"]
    done = tap(client, card["quote_id"], "confirm", PIN)["reply"]
    assert done["kind"] == "done" and done["options_role"] == "next"
    assert [chip["id"] for chip in done["options"]] == [WHY, *[f"opt_m_{row}" for row in MENU]]


def test_the_menu_speaks_to_a_man_as_a_man(client):
    start = client.post("/api/start", json={"user_id": "u2"}).json()
    assert start["menu"]["lines"] == ["شتحتاج؟ اختار من القائمة، أو اكتبلي طلبك بكلامك."]
    assert start["menu"]["options"][4]["description"] == "اللي دفعته واللي وصلك"


def test_the_menu_speaks_the_language_of_the_conversation(client):
    say(client, "what is my balance")
    menu = say(client, "menu")["reply"]
    assert menu["lang"] == "en" and [chip["title"] for chip in menu["options"]][:2] == ["Send money", "Pay a bill"]
    assert client.post("/api/start", json={"user_id": UID}).json()["menu"]["options"][0]["title"] == "تحويل فلوس"


# ---------------------------------------------------------------- what I checked


def test_a_card_shows_what_it_was_built_on(client):
    shown = say(client, "دز لهادي خمسين")
    reply = shown["reply"]
    assert reply["why_label"] == "شلون تأكدت؟"
    assert [check["what"] for check in reply["evidence"]] == ["payee", "amount", "balance_ok", "limit_ok",
                                                              "flag_first_time_payee", "quote"]
    by_what = {check["what"]: check for check in reply["evidence"]}
    assert by_what["amount"]["value"] == "words=خمسين; amount=50,000; rule=rule_bare"
    assert by_what["amount"]["source"] == "user_text" and by_what["amount"]["from"] == "كلامج"
    assert "«خمسين»" in by_what["amount"]["line"] and "50,000" in by_what["amount"]["line"]
    assert by_what["payee"]["from"] == "الأسماء المحفوظة" and "2222" in by_what["payee"]["line"]
    assert by_what["balance_ok"]["from"] == "المحفظة" and "500,000" in by_what["balance_ok"]["line"]
    assert by_what["quote"]["source"] == "wallet:quote" and shown["card"]["quote_id"] in by_what["quote"]["line"]
    assert [chip["id"] for chip in reply["options"]] == [WHY]


def test_a_receipt_shows_the_transaction_read_back_and_its_reference(client):
    card = say(client, "ادفع المولدة")["card"]
    done = tap(client, card["quote_id"], "confirm", PIN)
    reply = done["reply"]
    assert reply["kind"] == "done" and reply["reference"] and len(reply["reference"]) == 6
    assert done["receipt"]["reference"].upper().endswith(reply["reference"])
    assert any(reply["reference"] in line for line in reply["lines"])
    paid = reply["evidence"][-1]
    assert paid["what"] == "paid" and paid["source"] == "wallet:readback" and paid["from"] == "المحفظة"
    assert reply["reference"] in paid["line"] and "state=SUCCEEDED" in paid["value"]
    assert [check["what"] for check in reply["evidence"]][0] == "bill"


def test_a_refusal_shows_the_rule_and_its_numbers(client):
    refused = say(client, "دز لهادي عشر ملايين")["reply"]
    assert refused["kind"] == "refuse" and refused["why_label"]
    short = next(check for check in refused["evidence"] if check["what"] == "balance_short")
    assert "500,000" in short["line"] and "10,000,250" in short["line"] and short["from"] == "المحفظة"
    assert refused["options"][0]["id"] == WHY


def test_explain_says_the_last_checks_and_keeps_the_card_open(client):
    shown = say(client, "دز لهادي خمسين")
    explained = client.post("/api/explain").json()
    assert explained["reply"]["kind"] == "card" and explained["card"]["quote_id"] == shown["card"]["quote_id"]
    assert len(explained["reply"]["lines"]) == 1 + len(shown["reply"]["evidence"])
    assert explained["reply"]["lines"][1:] == [f"• {check['line']}" for check in shown["reply"]["evidence"]]
    typed = say(client, "ليش")
    assert typed["reply"]["lines"] == explained["reply"]["lines"]
    assert option(client, WHY)["reply"]["lines"] == explained["reply"]["lines"]
    done = tap(client, shown["card"]["quote_id"], "confirm", PIN)  # still the same open card
    assert done["receipt"]["status"] == "succeeded"


def test_what_i_checked_follows_the_language(client):
    shown = say(client, "send 50000 to Hadi")["reply"]
    assert shown["lang"] == "en" and shown["why_label"] == "How did you check?"
    assert shown["evidence"][0]["from"] == "saved contacts"
    assert any(check["line"].startswith("Balance: you have 500,000 dinars") for check in shown["evidence"])
    assert client.post("/api/explain").json()["reply"]["lines"][0] == "Here is what I checked on your last request:"


def test_nothing_the_page_gets_holds_a_pin_or_a_whole_number(client):
    seen = []
    card = say(client, "دز 50 الف على 07712345678")
    seen.append(card)
    seen.append(tap(client, card["card"]["quote_id"], "confirm", "0000"))
    seen.append(tap(client, card["card"]["quote_id"], "confirm", PIN))
    seen.append(client.post("/api/explain").json())
    seen.append(option(client, WHY))
    text = json.dumps([[p["reply"]["evidence"], p["reply"]["options"], p["reply"]["lines"]] for p in seen],
                      ensure_ascii=False)
    assert "07712345678" not in text and "7712345678" not in text and "5678" in text
    evidence = json.dumps([p["reply"]["evidence"] for p in seen], ensure_ascii=False)
    assert PIN not in evidence and "0000" not in evidence


# ---------------------------------------------------------------- the page itself


def test_the_page_draws_chips_and_the_expander_and_sends_only_ids(client):
    script = client.get("/static/app.js").text
    assert '"/api/option"' in script and "dataset.option" in script and "[data-option]" in script
    assert "function renderOptions" in script and "function renderChecks" in script
    assert 'createElement("details")' in script  # the expander is the browser's own: keyboard and screen reader work
    send = script[script.index("async function tapOption"):script.index("async function tapButton")]
    call = send[send.index("api("):send.index("typing.remove()")]
    assert "body: { option_id: id }" in call and "title" not in call  # only the id leaves the page
    assert 'addEventListener("submit"' in script or "sendText" in script  # typing a request still works
    styles = client.get("/static/app.css").text
    assert ".option" in styles and ".checks" in styles and "min-height: 44px" in styles
    assert demo(client).session.menu(UID).options  # and the server has a menu for it
