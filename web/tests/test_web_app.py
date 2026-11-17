"""Chat app end to end: the web API over the real orchestrator, StubCore and an in-memory wallet.

No network, no keys: the default core is the rule-based stand-in and every wallet is in memory.
"""

from __future__ import annotations

import copy
import uuid

import pytest
from fastapi.testclient import TestClient

from web.app import create_app
from web.service import Demo, DemoStore

PIN = "1234"
UID = "u1"

SEED = {
    "users": [
        {"id": "u1", "name": "سارة كريم", "phone": "07701234567", "balance": 500_000,
         "daily_limit": 1_000_000, "pin": PIN, "gender": "f"},
        {"id": "u2", "name": "علي حسن", "phone": "07801112233", "balance": 300_000,
         "daily_limit": 1_000_000, "pin": "4321", "gender": "m"},
    ],
    "contacts": [
        {"contact_id": "c1", "user_id": "u1", "display_name": "هادي", "phone": "07711112222"},
        {"contact_id": "c2", "user_id": "u1", "display_name": "أحمد", "phone": "07801110001"},
        {"contact_id": "c3", "user_id": "u1", "display_name": "أحمد", "phone": "07502220002"},
        {"contact_id": "c4", "user_id": "u2", "display_name": "زينب", "phone": "07903334444"},
    ],
    "billers": [
        {"id": "generator", "kind": "generator", "name_ar": "مولدة الحجي جاسم", "name_en": "Hajji Jasim Generator"},
        {"id": "asiacell", "kind": "topup", "name_ar": "آسياسيل", "name_en": "Asiacell"},
    ],
    "bill_accounts": [
        {"id": "a1", "user_id": "u1", "biller_id": "generator", "account_no": "350",
         "holder_name": "سارة كريم", "label_ar": "المولدة"},
    ],
    "bills": [
        {"id": "b1", "account_id": "a1", "period": "2026-10", "amount_due": 45_000, "status": "unpaid"},
    ],
    "transactions": [],
}


@pytest.fixture
def store():
    s = DemoStore(seed=copy.deepcopy(SEED))
    yield s
    s.close()


@pytest.fixture
def client(store):
    with TestClient(create_app(store)) as c:
        c.headers["X-Demo-Session"] = str(uuid.uuid4())
        c.post("/api/start", json={"user_id": UID}).raise_for_status()
        yield c


def demo(client) -> Demo:
    return client.app.state.demos.get(client.headers["X-Demo-Session"])


def balance(client) -> int:
    return client.get("/api/history").json()["profile"]["balance"]


def audit_ok(client) -> None:
    report = demo(client).wallet.audit()
    assert report["ok"], report["violations"]


def say(client, text: str) -> dict:
    response = client.post("/api/message", json={"text": text})
    assert response.status_code == 200, response.text
    return response.json()


def tap(client, quote_id: str, button: str, pin: str | None = None) -> dict:
    response = client.post("/api/button", json={"quote_id": quote_id, "button": button, "pin": pin})
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------- pages and setup


def test_page_and_config_are_served(client):
    page = client.get("/")
    assert page.status_code == 200
    assert 'dir="rtl"' in page.text and "/static/app.js" in page.text
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    config = client.get("/api/config").json()
    assert config == {"voice": False, "pin_length": 4, "faults": ["none", "drop_after_commit", "biller_down",
                                                                    "network_down"],
                      "speaks": False, "voice_reply_mode": "mirror"}


def test_users_never_expose_pins_and_start_gives_examples(client):
    users = client.get("/api/users").json()
    assert [u["id"] for u in users] == ["u1", "u2"]
    assert all("pin" not in u and "demo_pin" not in u and "pin_hash" not in u for u in users)
    start = client.post("/api/start", json={"user_id": UID}).json()
    assert start["profile"]["first_name"] == "سارة" and start["profile"]["balance"] == 500_000
    texts = [s["text"] for s in start["suggestions"]]
    assert "ادفع المولدة" in texts and "دز لهادي خمسين الف" in texts and "دز لأحمد خمسين" in texts
    assert start["demo_pin"] == PIN  # synthetic, shown as a hint in the demo PIN sheet


def test_bad_session_and_unknown_user_are_refused(client):
    assert client.post("/api/start", json={"user_id": "nobody"}).status_code == 404
    bad = client.post("/api/message", json={"text": "هلا"}, headers={"X-Demo-Session": "x"})
    assert bad.status_code == 400
    assert client.post("/api/message", json={"text": "   "}).status_code == 400


# ---------------------------------------------------------------- the flows


def test_bill_pay_card_confirm_pin_executed(client):
    shown = say(client, "ادفع المولدة")
    card = shown["card"]
    assert shown["reply"]["kind"] == "card" and card is not None
    assert card["kind"] == "bill" and card["payee"] == "مولدة الحجي جاسم"
    assert card["amount"] == 45_000 and card["fee"] == 0 and card["total"] == 45_000
    assert card["amount_words"] == "خمسة واربعين ألف دينار"
    assert card["balance_after"] == 455_000
    assert 0 < card["expires_in"] <= 300
    assert [b["label"] for b in card["buttons"]] == ["إي، ادفع", "غيّر", "لا، بطّلت"]
    assert {"label": "عن شهر", "value": "تشرين الأول 2026"} in card["rows"]
    assert balance(client) == 500_000  # a card moves nothing

    asked = tap(client, card["quote_id"], "confirm")  # tap without a PIN: the agent asks for it
    assert asked["reply"]["kind"] == "card" and asked["receipt"] is None
    assert balance(client) == 500_000

    done = tap(client, card["quote_id"], "confirm", PIN)
    assert done["reply"]["kind"] == "done" and done["reply"]["state"] == "SUCCEEDED"
    assert done["receipt"]["status"] == "succeeded" and done["receipt"]["amount"] == 45_000
    assert done["receipt"]["reference"] and done["receipt"]["balance_after"] == 455_000
    assert done["profile"]["balance"] == 455_000
    assert any("تمت" in line for line in done["reply"]["lines"])
    history = client.get("/api/history").json()["items"]
    assert history[0]["quote_id"] == card["quote_id"] and history[0]["status"] == "succeeded"
    audit_ok(client)


def test_wrong_pin_keeps_the_card_and_moves_nothing(client):
    card = say(client, "دز لهادي خمسين الف")["card"]
    wrong = tap(client, card["quote_id"], "confirm", "0000")
    assert wrong["reply"]["kind"] == "card" and wrong["reply"]["quote_id"] == card["quote_id"]
    assert wrong["receipt"] is None and wrong["reply"]["lines"]
    assert balance(client) == 500_000
    done = tap(client, card["quote_id"], "confirm", "١٢٣٤")  # Eastern digits are read
    assert done["receipt"]["status"] == "succeeded"
    assert balance(client) == 500_000 - 50_000 - 250


def test_transfer_to_two_ahmeds_asks_which_one(client):
    asked = say(client, "دز لأحمد خمسين")
    assert asked["reply"]["kind"] == "ask" and asked["reply"]["ask_field"] == "payee"
    assert asked["card"] is None
    text = " ".join(asked["reply"]["lines"])
    assert "0001" in text and "0002" in text
    picked = say(client, "الثاني")
    assert picked["card"]["kind"] == "transfer" and picked["card"]["amount"] == 50_000
    assert {"label": "رقم الهاتف", "value": "ينتهي بـ 0002"} in picked["card"]["rows"]
    assert picked["card"]["fee"] == 250 and picked["card"]["total"] == 50_250


def test_refusals_say_why_and_move_nothing(client):
    out_of_scope = say(client, "احجزلي تكسي")
    assert out_of_scope["reply"]["kind"] == "refuse" and out_of_scope["card"] is None
    too_much = say(client, "دز لهادي عشر ملايين")
    assert too_much["reply"]["kind"] == "refuse" and too_much["card"] is None
    assert "ما انخصم" in " ".join(too_much["reply"]["lines"])
    assert balance(client) == 500_000


def test_double_confirm_does_not_double_pay(client):
    card = say(client, "دز لهادي خمسين الف")["card"]
    first = tap(client, card["quote_id"], "confirm", PIN)
    second = tap(client, card["quote_id"], "confirm", PIN)
    assert first["receipt"]["status"] == "succeeded"
    assert second["reply"]["kind"] == "refuse" and second["receipt"] is None
    assert balance(client) == 500_000 - 50_250
    items = [i for i in client.get("/api/history").json()["items"] if i["quote_id"] == card["quote_id"]]
    assert len(items) == 1
    audit_ok(client)


def test_cancel_and_change_buttons_close_the_card(client):
    card = say(client, "دز لهادي خمسين الف")["card"]
    cancelled = tap(client, card["quote_id"], "cancel")
    assert cancelled["reply"]["kind"] == "cancelled" and cancelled["card"] is None
    assert tap(client, card["quote_id"], "confirm", PIN)["reply"]["kind"] == "refuse"  # a closed card never pays
    card = say(client, "دز لهادي خمسين الف")["card"]
    change = tap(client, card["quote_id"], "change")
    assert change["reply"]["kind"] == "ask" and change["reply"]["ask_field"] == "change"
    assert balance(client) == 500_000


def test_new_text_voids_the_open_card(client):
    first = say(client, "دز لهادي خمسين الف")["card"]
    say(client, "ادفع المولدة")
    assert tap(client, first["quote_id"], "confirm", PIN)["reply"]["kind"] == "refuse"
    assert balance(client) == 500_000


# ---------------------------------------------------------------- the honest UNKNOWN


def test_network_down_gives_unknown_then_the_real_outcome(client):
    card = say(client, "دز لهادي خمسين الف")["card"]
    assert client.post("/api/demo-fault", json={"mode": "network_down"}).json() == {"mode": "network_down"}
    unknown = tap(client, card["quote_id"], "confirm", PIN)
    assert unknown["reply"]["kind"] == "unknown"
    assert unknown["receipt"]["status"] == "unknown" and unknown["receipt"]["quote_id"] == card["quote_id"]
    assert "لهسه ما نعرف" in " ".join(unknown["reply"]["lines"])

    later = client.post("/api/check").json()  # the line is back: the agent reports what really happened
    assert [r["quote_id"] for r in later["resolved"]] == [card["quote_id"]]
    assert later["resolved"][0]["status"] == "succeeded"
    assert any("تمت" in line for line in later["reply"]["lines"])
    assert balance(client) == 500_000 - 50_250  # once
    audit_ok(client)


def test_lost_answer_is_retried_with_the_same_key_and_paid_once(client):
    card = say(client, "دز لهادي خمسين الف")["card"]
    client.post("/api/demo-fault", json={"mode": "drop_after_commit"})
    done = tap(client, card["quote_id"], "confirm", PIN)
    assert done["receipt"]["status"] == "succeeded"
    assert balance(client) == 500_000 - 50_250
    audit_ok(client)


def test_biller_down_fails_honestly(client):
    card = say(client, "ادفع المولدة")["card"]
    client.post("/api/demo-fault", json={"mode": "biller_down"})
    failed = tap(client, card["quote_id"], "confirm", PIN)
    assert failed["reply"]["kind"] == "failed" and failed["receipt"]["status"] == "failed"
    assert failed["receipt"]["money_returned"] is False
    assert balance(client) == 500_000


# ---------------------------------------------------------------- reset, users, voice


def test_reset_gives_a_fresh_wallet(client):
    card = say(client, "دز لهادي خمسين الف")["card"]
    tap(client, card["quote_id"], "confirm", PIN)
    assert balance(client) == 500_000 - 50_250
    fresh = client.post("/api/reset").json()
    assert fresh["profile"]["balance"] == 500_000 and fresh["history"] == []


def test_each_browser_has_its_own_wallet(client):
    card = say(client, "دز لهادي خمسين الف")["card"]
    tap(client, card["quote_id"], "confirm", PIN)
    other = {"X-Demo-Session": str(uuid.uuid4())}
    client.post("/api/start", json={"user_id": UID}, headers=other)
    assert client.get("/api/history", headers=other).json()["profile"]["balance"] == 500_000


def test_switching_user_talks_as_that_user(client):
    start = client.post("/api/start", json={"user_id": "u2"}).json()
    assert start["profile"]["id"] == "u2" and start["profile"]["gender"] == "m"
    assert start["demo_pin"] == "4321"


def test_voice_without_a_voice_core_returns_a_friendly_note(client):
    response = client.post("/api/voice", content=b"\x1a\x45\xdf\xa3fake-webm",
                           headers={"Content-Type": "audio/webm"})
    body = response.json()
    assert response.status_code == 200 and body["voice"] is False
    assert "النسخة الكاملة" in body["note"] and "چ" not in body["note"]
    assert balance(client) == 500_000


class VoiceCore:
    """A test core with a voice path: it 'hears' a fixed sentence, then reads it like StubCore."""

    def __init__(self):
        from core.stub import StubCore

        self.inner = StubCore()
        self.heard: list[tuple[int, str]] = []

    def understand(self, text, context):
        return self.inner.understand(text, context)

    def phrase(self, key, slots):
        return self.inner.phrase(key, slots)

    def transcribe(self, audio: bytes, mime: str, context: dict) -> str:
        self.heard.append((len(audio), mime))
        return "دز لهادي خمسين الف"


def test_voice_with_a_voice_core_goes_through_the_same_card(monkeypatch):
    monkeypatch.setenv("BILLPAY_CORE", f"{__name__}:VoiceCore")
    store = DemoStore(seed=copy.deepcopy(SEED))
    try:
        with TestClient(create_app(store)) as c:
            c.headers["X-Demo-Session"] = str(uuid.uuid4())
            assert c.get("/api/config").json()["voice"] is True
            c.post("/api/start", json={"user_id": UID})
            body = c.post("/api/voice", content=b"audio-bytes", headers={"Content-Type": "audio/ogg"}).json()
            assert body["transcript"] == "دز لهادي خمسين الف"
            assert body["card"]["amount"] == 50_000 and body["reply"]["kind"] == "card"
            core = c.app.state.demos.get(c.headers["X-Demo-Session"]).core
            assert core.heard == [(len(b"audio-bytes"), "audio/ogg")]
    finally:
        store.close()


def test_public_files_follow_the_house_rules():
    from pathlib import Path

    static = Path(__file__).resolve().parent.parent / "static"
    banned = ("چ", "zaincash", "zain cash", "شركة", "llc")
    for path in static.iterdir():
        text = path.read_text(encoding="utf-8").lower()
        for word in banned:
            assert word not in text, f"{word!r} in {path.name}"
        assert "\r\n" not in path.read_text(encoding="utf-8"), f"CRLF in {path.name}"
