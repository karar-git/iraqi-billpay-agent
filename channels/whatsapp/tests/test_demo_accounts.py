"""SIGNUP_MODE=demo: the instant demo account, its intro and PIN, username-only senders, and the channel's human
touches (the spoken-PIN fix, the delete-your-PIN line, typing dots and pacing, the session's writer for small talk,
the balance at the first hello of a day only, «ما انخصم منك شي» once per message).

Offline: the fake Graph API, the open StubCore (or a small subclass), a hand-moved clock. No network, no model."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time

import pytest

from accounts import phones
from accounts import texts as account_texts
from channels.whatsapp import texts
from channels.whatsapp.channel import once_per_message
from channels.whatsapp.config import Settings
from channels.whatsapp.tests import kit
from channels.whatsapp.tests.kit import ALICE, BOB, PIN
from channels.whatsapp.tests.test_language_and_voice import Speaking
from channels.whatsapp.tests.test_whatsapp_channel import Harness
from core.stub import StubCore
from wallet.api import DEMO_FUNDS, canonical_phone
from wallet.db import verify_pin

CAROL = "9647700000003"  # a number with no account: a new visitor
USER_ID = "IQ.4242"  # a WhatsApp username account: the webhook names no number


def demo(tmp_path, **overrides) -> Harness:
    return Harness(tmp_path, signup_mode="demo", **overrides)


def pin_of(text: str) -> str:
    found = re.findall(r"(?<!\d)\d{4}(?!\d)", text)
    assert len(found) == 1, text
    return found[0]


def username_text(body: str, clock, user_id: str = USER_ID, name: str = "tester") -> dict:
    """A Meta-shaped webhook from a username-only account: from_user_id, no from."""
    message = {"from_user_id": user_id, "id": f"wamid.u{time.monotonic_ns()}",
               "timestamp": str(int(clock().timestamp())), "type": "text", "text": {"body": body}}
    value = {"messaging_product": "whatsapp", "metadata": {"phone_number_id": kit.PNID},
             "contacts": [{"user_id": user_id, "profile": {"name": name}}], "messages": [message]}
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "waba", "changes": [{"field": "messages", "value": value}]}]}


def send_body(h: Harness, body: dict) -> None:
    assert kit.post(h.client, body).status_code == 200
    assert h.channel.join(20)


def recipient_texts(h: Harness, user_id: str = USER_ID) -> list[str]:
    return [s["payload"]["text"]["body"] for s in h.graph.sent
            if s["payload"].get("recipient") == user_id and s["payload"]["type"] == "text"]


# ---------------------------------------------------------------- the switch


def test_a_bad_signup_mode_is_refused_at_start_up():
    with pytest.raises(ValueError):
        Settings(signup_mode="dmeo")
    with pytest.raises(ValueError):
        Settings.from_env({"SIGNUP_MODE": "openn"})
    assert Settings.from_env({"SIGNUP_MODE": "Demo"}).signup_mode == "demo"
    assert Settings.from_env({}).signup_mode == "open"  # the default stays the typed sign-up
    assert Settings().voice_approval is False  # the intro never promises voice approval unless asked to


# ---------------------------------------------------------------- the first message


def test_first_message_opens_a_demo_account_sends_the_intro_then_answers_it(tmp_path):
    h = demo(tmp_path)
    try:
        h.say(CAROL, "شكد رصيدي")
        said = h.graph.texts(CAROL)
        assert len(said) >= 3, said
        intro, pin_bubble, answer = said[0], said[1], said[2:]
        assert "تجريبي" in intro and "425,000" in intro
        assert "حسين علي" in intro and "زينب كاظم" in intro and "50,000" in intro and "25,000" in intro
        assert "قبل 3 أيام" in intro and "قبل 6 أيام" in intro
        assert "الكهرباء" in intro or "كهرباء" in intro
        assert "مولدة" in intro
        assert "صوتية" in intro  # typed or by voice
        assert not re.search(r"(?<!\d)\d{4}(?![\d,])", intro.replace("425,000", "")), "no PIN in bubble one"
        assert "چ" not in intro + pin_bubble
        assert "السلام عليكم" not in intro
        assert intro.startswith("صباح الخير")  # 06:00 UTC is 09:00 in Baghdad
        pin = pin_of(pin_bubble)
        assert "سري" in pin_bubble
        assert "425,000" in "\n".join(answer)  # the question itself was answered, after the intro
        user = h.channel.accounts.by_phone(CAROL)
        assert user["source"] == "demo" and user["balance"] == 425_000 and user["kyc_level"] is None
        assert verify_pin(pin, user["pin_hash"])
        assert h.channel.wallet.audit()["ok"]
    finally:
        h.stop()


def test_a_hello_only_first_message_gets_the_intro_once_and_no_welcome(tmp_path):
    h = demo(tmp_path)
    try:
        h.say(CAROL, "هلا")
        said = h.graph.texts(CAROL)
        assert len(said) == 2, said  # the intro and the PIN, nothing else
        assert "تجريبي" in said[0]
        assert "رصيدك" not in "\n".join(said)
        h.graph.clear()
        h.say(CAROL, "هلا")  # a second hello the same day: a greeting, not the intro again, and no balance
        [again] = h.graph.texts(CAROL)
        assert "تجريبي" not in again and "425,000" not in again
        assert not again.startswith(("صباح الخير", "مساء الخير")), again  # the intro greeted a moment ago
    finally:
        h.stop()


def test_english_and_msa_first_messages_get_the_intro_in_their_language(tmp_path):
    h = demo(tmp_path)
    try:
        h.say(CAROL, "hi")
        intro, pin_bubble = h.graph.texts(CAROL)[:2]
        assert intro.startswith("Good morning") and "demo account" in intro and "425,000 dinars" in intro
        assert "3 days ago" in intro and "6 days ago" in intro
        assert "Keep it to yourself" in pin_bubble
        assert h.channel.accounts.by_phone(CAROL)["language"] == "en"
    finally:
        h.stop()
    h = demo(tmp_path / "msa")
    try:
        h.say(CAROL, "مرحباً، أريد أن أعرف كيف أدفع فاتورة الكهرباء من فضلك")
        intro = h.graph.texts(CAROL)[0]
        assert "الأموال فيه ليست حقيقية" in intro
    finally:
        h.stop()


def test_the_profile_name_is_used_when_clean_and_the_intro_is_gender_neutral(tmp_path):
    h = demo(tmp_path)
    try:
        h.say(CAROL, "هلا")
        intro = h.graph.texts(CAROL)[0]
        assert intro.startswith("صباح الخير tester!")  # kit.webhook's profile name
        for gendered in ("رصيدك", "رصيدج", "اكتبي", "تريدين", "إلك", "إلج"):
            assert gendered not in intro
        assert h.channel.accounts.by_phone(CAROL)["gender"] is None
    finally:
        h.stop()


def test_the_pin_is_only_in_the_bubble_never_in_a_database_log_or_outbox(tmp_path, monkeypatch, caplog):
    from accounts import pins

    monkeypatch.setattr(pins, "new_pin", lambda own=None, length=4: "8642")
    caplog.set_level(logging.DEBUG)
    h = demo(tmp_path)
    try:
        h.say(CAROL, "هلا")
        assert pin_of(h.graph.texts(CAROL)[1]) == "8642"
        accounts_db = sqlite3.connect(str(h.channel.settings.accounts_path))
        stored = []
        for table, column in (("audit_log", "details_json"), ("outbox", "payload_json"), ("users", "pin_hash"),
                              ("transactions", "target_json"), ("users", "note")):
            stored += [str(r[0]) for r in accounts_db.execute(f"SELECT {column} FROM {table}")]
        accounts_db.close()
        store = sqlite3.connect(str(tmp_path / "whatsapp" / "channel.db"))
        for table in ("senders", "inbox", "onboarding"):
            stored += [json.dumps([v for k, v in zip(r.keys(), r) if k != "key"], default=str)
                       for r in _rows(store, table)]
        store.close()
        assert not any("8642" in value for value in stored)
        assert not any("8642" in record.getMessage() for record in caplog.records)
        assert h.channel._secrets == {}  # the turn's copy went with the turn
    finally:
        h.stop()


def _db(h: Harness) -> sqlite3.Connection:
    conn = sqlite3.connect(str(h.channel.settings.accounts_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn, table):
    conn.row_factory = sqlite3.Row
    return conn.execute(f"SELECT * FROM {table}").fetchall()


def test_a_replayed_or_second_first_message_makes_one_account_and_one_intro(tmp_path):
    h = demo(tmp_path)
    try:
        message = kit.text(CAROL, "هلا", h.clock, msg_id="wamid.same")
        h.send(message)
        h.send(message)  # Meta's retry
        h.say(CAROL, "شكد رصيدي")
        said = h.graph.texts(CAROL)
        assert sum("تجريبي" in t for t in said) == 1
        rows = _db(h).execute("SELECT COUNT(*) FROM users WHERE source = 'demo'").fetchone()[0]
        assert rows == 1
        again = h.channel.wallet.open_demo_account(CAROL, "x", language="iq", pin_hash="")
        assert again["created"] is False  # idempotent: an account on the number changes nothing
        assert h.channel.accounts.by_phone(CAROL)["balance"] == 425_000
    finally:
        h.stop()


def test_a_crash_between_the_account_and_its_intro_gives_a_fresh_pin(tmp_path):
    from accounts import pins

    h = demo(tmp_path)
    try:
        old = "5821"
        h.channel.wallet.open_demo_account(CAROL, "كرار", language="iq", pin_hash=pins.hash_pin(old))
        assert h.channel.store.sender(h.channel.sender_key(CAROL)) is None  # the intro never went out
        h.say(CAROL, "شكد رصيدي")
        said = h.graph.texts(CAROL)
        assert "تجريبي" in said[0]
        new = pin_of(said[1])
        user = h.channel.accounts.by_phone(CAROL)
        assert verify_pin(new, user["pin_hash"]) and (new == old or not verify_pin(old, user["pin_hash"]))
        assert h.channel.store.sender(h.channel.sender_key(CAROL))["greeted"] == 1
        h.graph.clear()
        h.say(CAROL, "شكد رصيدي")
        assert not any("تجريبي" in t for t in h.graph.texts(CAROL))  # once greeted, never again
    finally:
        h.stop()


# ---------------------------------------------------------------- the money


def test_seeding_keeps_the_audit_ok_and_books_no_cash(tmp_path):
    h = demo(tmp_path)
    try:
        h.say(CAROL, "هلا")
        audit = h.channel.wallet.audit()
        assert audit["ok"], audit["violations"]
        # the made-up past is booked back to demo_funds: only the shown balance is pretend money outstanding
        assert audit["totals"]["cash"] == 0 and audit["totals"]["demo"] == 425_000
        assert audit["totals"]["fees"] == 0 and audit["totals"]["billers"] == 0 and audit["totals"]["external"] == 0
        conn = _db(h)
        assert conn.execute("SELECT COUNT(*) FROM ledger WHERE entry = 'debit' AND quote_id IS NULL").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM ledger WHERE account = ? AND entry = 'demo_opening'",
                            (DEMO_FUNDS,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM ledger WHERE account = 'cash'").fetchone()[0] == 0
        uid = h.uid(CAROL)
        contacts = conn.execute("SELECT * FROM contacts WHERE user_id = ?", (uid,)).fetchall()
        assert sorted(c["display_name"] for c in contacts) == ["حسين علي", "زينب كاظم"]
        assert {c["alias"] for c in contacts} == {"أخوي", None} and {c["source"] for c in contacts} == {"demo"}
        assert all(canonical_phone(c["phone"]) and c["phone_e164"] != CAROL for c in contacts)
        bills = conn.execute("SELECT b.*, a.biller_id FROM bills b JOIN bill_accounts a ON a.id = b.account_id"
                             " WHERE a.user_id = ?", (uid,)).fetchall()
        due = sorted(b["biller_id"] for b in bills if b["status"] == "unpaid")
        assert due == ["electricity", "generator"] and all(b["period"] == "2026-11" for b in bills if b["status"] == "unpaid")
        [paid] = [b for b in bills if b["status"] == "paid"]
        assert paid["period"] == "2026-10" and paid["amount_due"] == 35_000 and paid["paid_txn_id"]
        assert h.channel.accounts.ensure_bills(uid) == 0  # the seeded month is as the intro told it
        assert conn.execute("SELECT COUNT(*) FROM holds").fetchone()[0] == 0
        assert h.channel.wallet.user_profile(uid)["spent_today"] == 0  # the past is outside today's limit
    finally:
        h.stop()


def test_next_month_the_demo_bill_accounts_get_the_feeds_bills(tmp_path):
    h = demo(tmp_path)
    try:
        h.say(CAROL, "هلا")
        uid = h.uid(CAROL)
        h.clock.advance(days=35)  # December
        assert h.channel.accounts.ensure_bills(uid) == 3  # electricity, generator and the internet line
    finally:
        h.stop()


def test_paying_a_demo_person_goes_to_external_with_no_hold_even_if_a_real_account_owns_the_number(tmp_path):
    h = demo(tmp_path)
    try:
        h.say(CAROL, "هلا")
        pin = pin_of(h.graph.texts(CAROL)[1])
        uid = h.uid(CAROL)
        conn = _db(h)
        alice_before = h.balance(ALICE)
        if True:  # make «حسين» sit on ALICE's number: a real account must still never be credited
            conn.execute("UPDATE contacts SET phone = '07700000001', phone_e164 = ? WHERE user_id = ? AND alias = 'أخوي'",
                         (ALICE, uid))
        h.graph.clear()
        h.say(CAROL, "دز لحسين 10000")
        card = h.graph.last_card(CAROL)
        assert "حسين" in card["body"]["text"] and "7 أيام" not in card["body"]["text"]
        h.tap(CAROL, "إي، ادفع", card)
        h.say(CAROL, pin)
        assert h.balance(CAROL) == 425_000 - 10_250
        assert h.balance(ALICE) == alice_before
        assert conn.execute("SELECT COUNT(*) FROM holds").fetchone()[0] == 0
        legs = conn.execute("SELECT account, delta FROM ledger WHERE quote_id IS NOT NULL").fetchall()
        assert ("external:07700000001", 10_000) in [(r["account"], r["delta"]) for r in legs]
        assert h.channel.wallet.audit()["ok"]
        last = h.graph.texts(CAROL)[-1]
        assert "حذف لدى الجميع" in last  # the delete-your-PIN line, after the receipt
    finally:
        h.stop()


def test_history_shows_the_seeded_entries_with_a_demo_label(tmp_path):
    h = demo(tmp_path)
    try:
        h.say(CAROL, "هلا")
        h.graph.clear()
        h.say(CAROL, "آخر العمليات")
        history = h.graph.texts(CAROL)[-1]
        assert "رصيد تجريبي" in history and "هدية فتح الحساب" not in history
        assert "حسين علي" in history and "زينب كاظم" in history and "50,250" in history
        assert "نت الرافدين" in history and "35,000" in history
    finally:
        h.stop()


# ---------------------------------------------------------------- the PIN of a demo account


def test_the_demo_pin_inside_a_longer_message_never_reaches_the_core(tmp_path):
    class Spy(StubCore):
        def __init__(self):
            super().__init__()
            self.seen = []

        def understand(self, text, context=None):
            self.seen.append(text)
            return super().understand(text, context)

    core = Spy()
    h = demo(tmp_path, core=core)
    try:
        h.say(CAROL, "هلا")
        pin = pin_of(h.graph.texts(CAROL)[1])
        core.seen.clear()
        h.say(CAROL, f"دز لأخوي 5000 والرمز {pin} هسه")
        # the PIN is cut out and the request goes on without it: the core never sees the PIN, nothing is paid
        assert core.seen and all(pin not in seen for seen in core.seen), core.seen
        assert any("الرمز ما نحتاجه هسه" in t for t in h.graph.texts(CAROL))
        assert len(h.graph.cards(CAROL)) == 1  # a card to confirm, never a payment
        assert h.channel.wallet.user_profile(h.uid(CAROL))["balance"] == 425_000
    finally:
        h.stop()


def test_a_guarded_line_follows_the_language_of_this_message_not_the_last_one(tmp_path):
    h = demo(tmp_path)
    try:
        h.say(CAROL, "هلا")
        pin = pin_of(h.graph.texts(CAROL)[1])
        h.say(CAROL, "what can you do")  # an English line: the chat is English for a moment
        h.graph.clear()
        h.say(CAROL, f"دز لزينب 5000 والرمز {pin}")  # Arabic again, the PIN inside: the PIN line is Arabic
        said = h.graph.texts(CAROL)
        assert any("الرمز ما نحتاجه هسه" in t for t in said), said
        assert not any("No PIN is needed" in t for t in said), said
    finally:
        h.stop()
    h = demo(tmp_path / "thanks")
    try:
        h.say(CAROL, "هلا")
        h.say(CAROL, "what can you do")
        h.graph.clear()
        h.say(CAROL, "شكرا")
        [thanks] = h.graph.texts(CAROL)
        assert re.search("[ا-ي]", thanks) and not re.search(r"[A-Za-z]", thanks), thanks  # Arabic, not English
    finally:
        h.stop()


def test_forgot_pin_on_a_demo_account_gives_a_fresh_pin_in_the_chat_and_no_admin_request(tmp_path):
    h = demo(tmp_path)
    try:
        h.say(CAROL, "هلا")
        old = pin_of(h.graph.texts(CAROL)[1])
        h.graph.clear()
        h.say(CAROL, "نسيت الرمز")
        said = h.graph.texts(CAROL)[-1]
        new = pin_of(said)
        user = h.channel.accounts.by_phone(CAROL)
        assert verify_pin(new, user["pin_hash"]) and (new == old or not verify_pin(old, user["pin_hash"]))
        assert h.graph.cards(CAROL) == []  # no «ask the admin?» buttons
        assert not [r for r in h.channel.accounts.open_requests() if r["user_id"] == user["id"]]
    finally:
        h.stop()


def test_forgot_pin_while_a_demo_card_waits_for_its_pin_gives_a_new_one_that_pays(tmp_path):
    h = demo(tmp_path)
    try:
        h.say(CAROL, "هلا")
        h.say(CAROL, "دز لحسين 10000")
        h.tap(CAROL, "إي، ادفع")
        h.say(CAROL, "نسيت الرمز")
        new = pin_of(h.graph.texts(CAROL)[-1])
        h.say(CAROL, new)
        assert h.balance(CAROL) == 425_000 - 10_250
    finally:
        h.stop()


def test_the_new_demo_pin_is_never_spoken(tmp_path):
    core = Speaking(heard="نسيت الرمز")
    h = demo(tmp_path, core=core)
    try:
        h.say(CAROL, "هلا")
        h.graph.media_files["m1"] = (b"OggS-in", "audio/ogg")
        h.send(kit.voice(CAROL, "m1", h.clock))
        new = pin_of(h.graph.texts(CAROL)[-1])
        spoken = " ".join(text for text, _ in core.said)
        assert new not in spoken
    finally:
        h.stop()


# ---------------------------------------------------------------- username-only senders


def test_a_username_only_sender_gets_a_working_demo_account_not_the_dead_end(tmp_path):
    h = demo(tmp_path)
    try:
        send_body(h, username_text("hi", h.clock))
        said = recipient_texts(h)
        assert len(said) == 2 and "demo account" in said[0], said
        assert "An account needs a phone number" not in "\n".join(said)
        key = phones.user_key(USER_ID)
        user = h.channel.accounts.by_phone(key)
        assert user["source"] == "demo" and user["phone"] is None
        assert phones.canonical(key) is None and canonical_phone(key) is None and not re.search(r"\d", key)
        pin = pin_of(said[1])
        # it can pay a bill
        send_body(h, username_text("pay the electricity bill", h.clock))
        card = [s["payload"] for s in h.graph.sent if s["payload"].get("recipient") == USER_ID
                and s["payload"]["type"] == "interactive"][-2]
        buttons = kit.card_buttons(card["interactive"])
        [pay] = [bid for title, bid in buttons.items() if bid.startswith("confirm:")]
        tap = {"from_user_id": USER_ID, "id": "wamid.tapu", "timestamp": str(int(h.clock().timestamp())),
               "type": "interactive", "interactive": {"type": "button_reply", "button_reply": {"id": pay, "title": "Yes"}}}
        body = username_text("x", h.clock)
        body["entry"][0]["changes"][0]["value"]["messages"] = [tap]
        send_body(h, body)
        send_body(h, username_text(pin, h.clock))
        after = h.channel.accounts.get(user["id"])["balance"]
        assert after < 425_000
        # nobody can send to it by number: its key is no number, and the wallet refuses it as one
        alice = h.channel._runtime(h.channel.sender_key(ALICE), h.uid(ALICE)).wallet
        with pytest.raises(Exception):
            alice.create_quote("u1", "transfer", {"phone": key}, 1000)
        assert h.channel.wallet.audit()["ok"]
    finally:
        h.stop()


def test_a_username_only_sender_still_gets_the_old_line_when_not_in_demo_mode(tmp_path):
    h = Harness(tmp_path)
    try:
        send_body(h, username_text("hi", h.clock))
        assert any("phone number" in t for t in recipient_texts(h))
        assert h.channel.accounts.by_phone(phones.user_key(USER_ID)) is None
    finally:
        h.stop()


# ---------------------------------------------------------------- admin


def test_demo_visitors_are_counted_not_announced_and_shown_as_demo_in_admin(tmp_path):
    h = demo(tmp_path, manager=BOB, admin_password="pw")
    try:
        h.say(CAROL, "هلا")
        assert not [t for t in h.graph.texts(BOB) if "حساب جديد" in t]
        day = h.channel._day()
        counters = {c["name"]: c["n"] for c in h.channel.store.counters(day)}
        assert counters.get("demo_signups") == 1 and counters.get("signups") == 1
        auth = ("karar", "pw")
        page = h.client.get("/admin/users?source=demo", auth=auth)
        assert page.status_code == 200 and 'class="pill demo"' in page.text
        real = h.client.get("/admin/users?source=real", auth=auth)
        assert 'class="pill demo"' not in real.text and "سارة كريم" in real.text
        overview = h.client.get("/admin", auth=auth).text
        assert "Demo accounts" in overview
        assert h.channel.wallet.audit()["totals"]["cash"] == 0  # cash in is not inflated by visitors
    finally:
        h.stop()


# ---------------------------------------------------------------- the spoken PIN (every account)


class Hearing(Speaking):
    def __init__(self, heard: str):
        super().__init__(heard=heard)
        self.heard_calls = 0

    def transcribe(self, audio, mime, context):
        self.heard_calls += 1
        return super().transcribe(audio, mime, context)


@pytest.mark.parametrize("heard", [PIN, f"الرمز {PIN}", f"PIN {PIN}"])
def test_a_spoken_pin_pays_nothing_and_its_audio_is_not_heard(tmp_path, heard):
    core = Hearing(heard)
    h = Harness(tmp_path, core=core)
    try:
        h.say(ALICE, "هلا")
        h.say(ALICE, "دز لهادي خمسين الف")
        h.tap(ALICE, "إي، ادفع")
        before = h.balance(ALICE)
        core.heard_calls = 0
        h.graph.clear()
        h.graph.media_files["m1"] = (b"OggS-in", "audio/ogg")
        h.send(kit.voice(ALICE, "m1", h.clock))
        assert h.balance(ALICE) == before, "a spoken PIN paid"
        assert core.heard_calls == 0, "PIN-step audio went to the hearing call"
        [reply] = h.graph.texts(ALICE)
        assert reply == texts.say("pin_by_voice", "f", "iq")
        h.say(ALICE, PIN)  # the card stayed open: the typed PIN pays
        assert h.balance(ALICE) == before - 50_250
    finally:
        h.stop()


def test_the_delete_your_pin_line_comes_once_after_a_typed_pin_payment(tmp_path):
    h = Harness(tmp_path)
    try:
        h.say(ALICE, "هلا")
        h.say(ALICE, "دز لهادي خمسين الف")
        h.tap(ALICE, "إي، ادفع")
        h.graph.clear()
        h.say(ALICE, PIN)
        everything = h.graph.everything(ALICE)
        assert everything.count("حذف لدى الجميع") == 1
        assert "وصل" in everything
        h.graph.clear()
        h.say(ALICE, "شكد رصيدي")
        assert "حذف لدى الجميع" not in h.graph.everything(ALICE)
    finally:
        h.stop()


# ---------------------------------------------------------------- pacing


def test_typing_dots_come_at_once_for_a_voice_note_and_are_refreshed_in_a_long_turn(tmp_path):
    class Slow(Speaking):
        def understand(self, text, context=None):
            time.sleep(0.35)
            return super().understand(text, context)

    h = Harness(tmp_path, core=Slow())
    try:
        h.channel.typing_every = 0.1
        h.say(ALICE, "هلا")
        h.graph.read.clear()
        message = kit.text(ALICE, "دز لهادي خمسين الف", h.clock, msg_id="wamid.slow")
        h.send(message)
        assert h.graph.read.count("wamid.slow") >= 3  # at intake, at the turn, and refreshed while it worked
        h.graph.read.clear()
        h.graph.media_files["m9"] = (b"OggS-in", "audio/ogg")
        note = kit.voice(ALICE, "m9", h.clock)
        h.send(note)
        assert h.graph.read.count(note["id"]) >= 2  # a voice note gets the dots at intake too
    finally:
        h.stop()


def test_human_pace_waits_a_little_before_each_text_and_never_more_than_two_seconds(tmp_path):
    h = Harness(tmp_path, human_pace=True)
    try:
        waits: list[float] = []
        h.channel.sleep = waits.append
        h.say(ALICE, "هلا")
        h.say(ALICE, "مساعدة")
        assert len(waits) == len(h.graph.texts(ALICE)) >= 2
        assert all(0 < w <= 2.0 for w in waits)
        assert waits[1] > waits[0]  # the long help text waits longer than the short hello
    finally:
        h.stop()


# ---------------------------------------------------------------- the session's writer for small talk


def test_hello_thanks_and_stickers_go_through_the_writer_with_the_fixed_line_as_fallback(tmp_path, monkeypatch):
    from agent.session import Session

    calls: list[tuple] = []

    def talk(self, user_id, situation, *, text="", facts=None, required=(), fallback):
        calls.append((situation, text, dict(facts or {}), tuple(required), fallback))
        return f"[{situation}]"

    monkeypatch.setattr(Session, "talk", talk, raising=False)
    h = Harness(tmp_path)
    try:
        h.say(ALICE, "هلا")
        assert h.graph.texts(ALICE)[-1] == "[hello]"
        situation, text, facts, required, fallback = calls[-1]
        assert text == "هلا" and facts["balance"] == 500_000 and required == ("balance",)  # a money fact
        assert "500,000" in fallback and "PIN" not in json.dumps(facts) and PIN not in json.dumps(facts)
        h.say(ALICE, "هلا")  # the second hello of the day: no balance asked for
        assert "balance" not in calls[-1][2] and calls[-1][3] == ()
        assert "500,000" not in calls[-1][4]
        h.say(ALICE, "شكرا")
        assert h.graph.texts(ALICE)[-1] == "[thanks]" and calls[-1][4] == texts.say("thanks", "f", "iq")
        h.send(kit.other(ALICE, "sticker", h.clock))
        assert h.graph.texts(ALICE)[-1] == "[unsupported_media]" and calls[-1][2] == {"media": "sticker"}
    finally:
        h.stop()


def test_a_failing_or_empty_writer_gives_the_fixed_line(tmp_path, monkeypatch):
    from agent.session import Session

    def broken(self, user_id, situation, **kw):
        raise RuntimeError("writer down")

    monkeypatch.setattr(Session, "talk", broken, raising=False)
    h = Harness(tmp_path)
    try:
        h.say(ALICE, "شكرا")
        assert h.graph.texts(ALICE)[-1] == texts.say("thanks", "f", "iq")
    finally:
        h.stop()
    monkeypatch.setattr(Session, "talk", lambda self, *a, fallback, **kw: "  ", raising=False)
    h = Harness(tmp_path / "empty")
    try:
        h.say(ALICE, "شكرا")
        assert h.graph.texts(ALICE)[-1] == texts.say("thanks", "f", "iq")
    finally:
        h.stop()


def test_the_balance_is_said_at_the_first_hello_of_a_day_only(tmp_path):
    h = Harness(tmp_path)
    try:
        h.say(ALICE, "هلا")
        assert "500,000" in h.graph.texts(ALICE)[-1]
        h.say(ALICE, "مرحبا")
        assert "500,000" not in h.graph.texts(ALICE)[-1]
        h.clock.advance(days=1)
        h.say(ALICE, "هلا")
        assert "500,000" in h.graph.texts(ALICE)[-1]
    finally:
        h.stop()


# ---------------------------------------------------------------- «ما انخصم منك شي» once


def test_nothing_was_taken_is_said_once_per_message():
    text = "الرمز السري غلط، ما انخصم منك شي. اكتبه مرة ثانية.\n\nبطلت الطلب. ما انخصم منك شي."
    out = once_per_message(text)
    assert out.count("ما انخصم منك شي") == 1 and out.endswith("ما انخصم منك شي.")
    assert "\n\n" in out and "غلط، اكتبه مرة ثانية." in out
    assert once_per_message("ما انخصم منج شي.\nبطلت. ما انخصم منج شي.") == "بطلت. ما انخصم منج شي."
    assert once_per_message("لم يُخصم منك شيء. ثم لم يُخصم منك شيء.").count("لم يُخصم") == 1
    single = "الرمز غلط، ما انخصم منك شي."
    assert once_per_message(single) == single  # one is always kept: every failure says whether money moved


def test_new_texts_have_no_persian_letter_and_greet_by_time():
    for text in account_texts.all_texts() + texts.all_texts():
        assert "چ" not in text
    for lang in ("iq", "msa", "en"):
        for key in ("demo_open", "demo_pin", "demo_try", "welcome_again", "h_demo_credit"):
            assert "السلام عليكم" not in str(account_texts.BY_LANG[lang][key])


# ---------------------------------------------------------------- integration fixes (money first)


def test_a_demo_account_typing_a_real_number_pays_external_never_the_real_account(tmp_path):
    """Pretend money never reaches a real account or a hold, whatever number is typed."""
    h = demo(tmp_path)
    try:
        h.say(CAROL, "هلا")
        pin = pin_of(h.graph.texts(CAROL)[1])
        before = h.balance(ALICE)
        h.say(CAROL, "دز 10000 لـ 0" + ALICE[3:])
        h.tap(CAROL, "إي، ادفع")
        h.say(CAROL, pin)
        assert h.balance(CAROL) == 425_000 - 10_250  # the demo account paid
        assert h.balance(ALICE) == before  # the real account got nothing
        conn = _db(h)
        assert conn.execute("SELECT COUNT(*) FROM holds").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM outbox WHERE kind = 'transfer_in'").fetchone()[0] == 0
        assert h.channel.wallet.audit()["ok"]
    finally:
        h.stop()


def test_in_demo_mode_a_text_with_four_digits_is_kept_redacted_in_the_inbox():
    from channels.whatsapp.store import REDACTED, redact
    raw = {"kind": "text", "text": "دز لأخوي 5000 والرمز 4719"}
    assert redact(raw)["kind"] == "text"  # open mode: two numbers and a PIN word is not a PIN text
    assert redact(raw, any_four=True) == {"kind": REDACTED, "text": ""}
    assert redact({"kind": "text", "text": "٤٧١٩ تمام"}, any_four=True)["kind"] == REDACTED
    assert redact({"kind": "text", "text": "دز 50000 لأخوي"}, any_four=True)["kind"] == "text"


def test_the_demo_pin_check_looks_at_three_numbers_at_most(tmp_path, monkeypatch):
    import channels.whatsapp.channel as channel_module
    h = demo(tmp_path)
    try:
        h.say(CAROL, "هلا")
        checks = []
        real = channel_module.verify_pin
        monkeypatch.setattr(channel_module, "verify_pin", lambda pin, hashed: checks.append(pin) or real(pin, hashed))
        h.say(CAROL, " ".join(f"{1000 + i}" for i in range(400)))
        assert len(checks) <= 3
    finally:
        h.stop()


def test_forgot_pin_on_a_demo_account_keeps_a_lockout_and_never_unblocks(tmp_path):
    h = demo(tmp_path)
    try:
        h.say(CAROL, "هلا")
        uid = h.uid(CAROL)
        conn = _db(h)
        conn.execute("UPDATE users SET pin_failures = 3, pin_locked_until = '2099-01-01T00:00:00+00:00' WHERE id = ?",
                     (uid,))
        conn.commit()
        h.say(CAROL, "نسيت الرمز")
        row = conn.execute("SELECT pin_failures, pin_locked_until FROM users WHERE id = ?", (uid,)).fetchone()
        assert row[0] == 3 and row[1] is not None  # the new PIN came, the lockout stayed
        conn.execute("UPDATE users SET pin_state = 'blocked' WHERE id = ?", (uid,))
        conn.commit()
        h.graph.clear()
        h.say(CAROL, "نسيت الرمز")
        assert not re.search(r"(?<!\d)\d{4}(?!\d)", " ".join(h.graph.texts(CAROL)))  # no PIN for a blocked one
        assert conn.execute("SELECT pin_state FROM users WHERE id = ?", (uid,)).fetchone()[0] == "blocked"
        assert [r for r in h.channel.accounts.open_requests() if r["user_id"] == uid]  # a person looks
    finally:
        h.stop()


def test_an_intro_whose_pin_bubble_failed_is_sent_again_with_a_fresh_pin(tmp_path):
    h = demo(tmp_path)
    try:
        real = h.channel._post_text
        sent = []

        def flaky(item, text):
            sent.append(text)
            return False if len(sent) == 2 else real(item, text)  # the PIN bubble does not go out

        h.channel._post_text = flaky
        h.say(CAROL, "هلا")
        assert h.channel.store.sender(h.channel.sender_key(CAROL))["greeted"] == 0
        h.channel._post_text = real
        h.graph.clear()
        h.say(CAROL, "هلا")
        said = h.graph.texts(CAROL)
        assert "تجريبي" in said[0]
        assert verify_pin(pin_of(said[1]), h.channel.accounts.by_phone(CAROL)["pin_hash"])
    finally:
        h.stop()


def test_the_intro_joins_the_writer_s_memory(tmp_path):
    h = demo(tmp_path)
    try:
        h.say(CAROL, "شكد رصيدي")
        rt = h.channel._runtime(h.channel.sender_key(CAROL), h.uid(CAROL))
        memory = rt.session.talk_history(rt.user_id)
        assert any("مقدمة الحساب التجريبي" in t["text"] for t in memory if t["role"] == "assistant")
        assert all(not re.search(r"\d", t["text"]) for t in memory)  # no balance, no PIN, no digit
    finally:
        h.stop()


def test_a_hello_only_intro_joins_the_writer_s_memory_and_is_not_greeted_twice(tmp_path, monkeypatch):
    from agent.session import Session

    seen: list[list[dict]] = []
    real_talk = Session.talk

    def talk(self, user_id, situation, **kw):
        seen.append([dict(t) for t in self.talk_history(user_id)])
        return real_talk(self, user_id, situation, **kw)

    monkeypatch.setattr(Session, "talk", talk)
    h = demo(tmp_path)
    try:
        h.say(CAROL, "هلا")  # a hello only: the intro is the whole answer
        h.graph.clear()
        h.say(CAROL, "شلونك")
        assert seen and seen[-1], seen
        assert seen[-1][0]["role"] == "assistant"
        assert seen[-1][0]["text"].startswith("[انرسلت مقدمة الحساب التجريبي]")
        assert all(not re.search(r"\d", t["text"]) for t in seen[-1])  # no balance, no PIN, no digit
        [answer] = h.graph.texts(CAROL)
        assert "الحمد لله" in answer and not answer.startswith(("صباح الخير", "مساء الخير")), answer
    finally:
        h.stop()


def test_with_the_writer_down_a_how_are_you_after_the_intro_is_not_greeted_again(tmp_path, monkeypatch):
    from agent.session import Session

    def broken(self, user_id, situation, **kw):
        raise RuntimeError("writer down")

    monkeypatch.setattr(Session, "talk", broken)
    h = demo(tmp_path)
    try:
        h.say(CAROL, "هلا")
        h.graph.clear()
        h.say(CAROL, "شلونك")
        [answer] = h.graph.texts(CAROL)
        assert "الحمد لله" in answer and not answer.startswith(("صباح الخير", "مساء الخير")), answer
        rt = h.channel._runtime(h.channel.sender_key(CAROL), h.uid(CAROL))
        assert rt.busy == 0  # the hello-only turn held no busy count of its own
        h.clock.advance(hours=2)  # long after: a hello greets again by the time of day
        h.graph.clear()
        h.say(CAROL, "هلا")
        assert h.graph.texts(CAROL)[-1].startswith(("صباح الخير", "مساء الخير"))
    finally:
        h.stop()


def test_salam_is_returned_and_how_are_you_is_answered(tmp_path):
    h = demo(tmp_path)
    try:
        h.say(CAROL, "السلام عليكم")
        assert h.graph.texts(CAROL)[0].startswith("وعليكم السلام، ")
        h.graph.clear()
        h.say(CAROL, "شلونك")  # the second hello of the day: no balance, and the question is answered
        assert "الحمد لله" in h.graph.texts(CAROL)[-1]
    finally:
        h.stop()


def test_english_how_are_you_is_a_hello(tmp_path):
    h = Harness(tmp_path)
    try:
        h.say(ALICE, "how are you")
        out = h.graph.texts(ALICE)[-1]
        assert out.startswith(("Good morning", "Good evening")) and "balance" in out.lower()
    finally:
        h.stop()


def test_the_same_fixed_line_is_never_sent_twice_in_a_row(tmp_path):
    h = Harness(tmp_path)
    try:
        h.say(ALICE, "هلا")
        h.say(ALICE, "شكرا")
        h.say(ALICE, "شكرا")
        thanks = h.graph.texts(ALICE)[-2:]
        assert thanks[0] != thanks[1]
        h.say(ALICE, "هلا")
        h.say(ALICE, "هلا")
        hellos = h.graph.texts(ALICE)[-2:]
        assert hellos[0] != hellos[1]
    finally:
        h.stop()


def test_once_per_message_handles_the_conjunction_forms():
    out = once_per_message("بطلت البطاقة. ما انخصم منج شي.\n" + texts.say("pin_by_voice", "f", "iq"))
    assert out.count("انخصم") == 1 and out.endswith("وما انخصم منج شي.")
    out = once_per_message("ألغيت هذه البطاقة ولم يُخصم منك شيء. ثم كذا.\nلم يُخصم منك شيء.")
    assert out == "ألغيت هذه البطاقة. ثم كذا.\nلم يُخصم منك شيء."
    out = once_per_message("OK, cancelled. Nothing was taken.\nThe card is still open and nothing was taken.")
    assert out == "OK, cancelled.\nThe card is still open and nothing was taken."


def test_the_delete_pin_line_comes_before_a_warm_line_and_the_menu_asks_no_second_question(tmp_path, monkeypatch):
    from agent.session import Session
    monkeypatch.setattr(Session, "_warm_line", lambda self, uid, situation: ["تدللين، أي شي ثاني؟"])
    h = Harness(tmp_path)
    try:
        h.say(ALICE, "دز 5000 لـ 0" + BOB[3:])  # an account on that number: a real transfer, not a hold
        h.tap(ALICE, "إي، ادفع")
        h.graph.clear()
        h.say(ALICE, PIN)
        everything = h.graph.everything(ALICE)
        assert everything.index("حذف لدى الجميع") < everything.index("تدللين")
        assert "تحتاجين شي ثاني؟ اختاري" not in everything and "تحتاج شي ثاني؟ اختار" not in everything
    finally:
        h.stop()


def test_a_warm_line_with_no_question_mark_is_not_followed_by_another_anything_else(tmp_path, monkeypatch):
    from agent.session import Session
    monkeypatch.setattr(Session, "_warm_line", lambda self, uid, situation: ["تدللين، بالخدمة"])
    h = Harness(tmp_path)
    try:
        h.say(ALICE, "دز 5000 لـ 0" + BOB[3:])
        h.tap(ALICE, "إي، ادفع")
        h.graph.clear()
        h.say(ALICE, PIN)
        everything = h.graph.everything(ALICE)
        assert "تدللين، بالخدمة" in everything
        assert texts.say("menu_body", "f", "iq") in everything
        assert texts.say("next_body", "f", "iq") not in everything
    finally:
        h.stop()
    monkeypatch.setattr(Session, "_warm_line", lambda self, uid, situation: [])
    h = Harness(tmp_path / "plain")
    try:  # no warm line at all: the menu still asks «تحتاجين شي ثاني؟» itself
        h.say(ALICE, "دز 5000 لـ 0" + BOB[3:])
        h.tap(ALICE, "إي، ادفع")
        h.graph.clear()
        h.say(ALICE, PIN)
        assert texts.say("next_body", "f", "iq") in h.graph.everything(ALICE)
    finally:
        h.stop()


def test_an_english_intro_names_the_people_in_english(tmp_path):
    h = demo(tmp_path)
    try:
        h.say(CAROL, "hello, what is this?")
        intro = h.graph.texts(CAROL)[0]
        assert "Hussein Ali" in intro and "حسين" not in intro
    finally:
        h.stop()
