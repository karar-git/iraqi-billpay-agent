"""Real accounts in the wallet and the account services: migrations on an existing database, phone numbers,
the PIN policy and its peppered hash, transfers to accounts and to numbers with no account (holds), admin money,
account states, the PIN lockout and its escalation, the post-reset cap, the mock biller feed and backups."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from accounts import backup, billing, phones, pins
from accounts.service import Accounts, clean_name
from wallet.db import Database, hash_account_pin, hash_pin, verify_pin
from wallet.api import Wallet
from wallet.migrations import LATEST

A, B, C = "9647700000001", "9647700000002", "9647700000003"


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 2, 9, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


@pytest.fixture
def env():
    clock = Clock()
    db = Database(":memory:")
    wallet = Wallet(db, clock=clock)
    billing.ensure_catalogue(db)
    accounts = Accounts(db, wallet, clock=clock)
    a = wallet.open_account(A, "سارة كريم", gender="f", language="iq", pin_hash=hash_account_pin("2583"))["user"]["id"]
    b = wallet.open_account(B, "علي حسن", gender="m", language="iq", pin_hash=hash_account_pin("8642"))["user"]["id"]
    wallet.admin_credit(a, 500_000, "opening for tests", op_id="op-a", actor="admin:test")
    return clock, db, wallet, accounts, a, b


def transfer(wallet, uid, target, amount, pin="2583"):
    quote = wallet.create_quote(uid, "transfer", target, amount)
    return quote, wallet.execute(quote["id"], pin)


# ---------------------------------------------------------------- migrations


OLD_SCHEMA = """
CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT NOT NULL, phone TEXT, balance INTEGER NOT NULL CHECK (balance >= 0),
    daily_limit INTEGER, pin_hash TEXT NOT NULL, gender TEXT);
CREATE TABLE contacts (contact_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, display_name TEXT NOT NULL,
    phone TEXT NOT NULL, alias TEXT);
CREATE TABLE ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, quote_id TEXT, account TEXT NOT NULL, delta INTEGER NOT NULL,
    entry TEXT NOT NULL, created_at TEXT NOT NULL);
"""


def test_an_existing_v0_database_migrates_to_the_latest_with_the_same_money_and_a_snapshot(tmp_path):
    path = tmp_path / "accounts.db"
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.execute("INSERT INTO users VALUES ('u1', 'سارة', '07700000001', 120000, 1000000, ?, 'f')", (hash_pin("1357"),))
    conn.execute("INSERT INTO contacts VALUES ('c1', 'u1', 'علي', '07700000002', NULL)")
    conn.execute("INSERT INTO ledger (quote_id, account, delta, entry, created_at) VALUES"
                 " (NULL, 'user:u1', 120000, 'opening', 'x'), (NULL, 'opening_funds', -120000, 'opening', 'x')")
    conn.commit()
    conn.close()
    db = Database(path)
    wallet = Wallet(db)
    with db.connect() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == LATEST
        user = dict(c.execute("SELECT * FROM users WHERE id = 'u1'").fetchone())
        contact = dict(c.execute("SELECT * FROM contacts").fetchone())
    assert user["balance"] == 120_000 and user["phone_e164"] == A and user["status"] == "active"
    assert user["pin_state"] == "set" and verify_pin("1357", user["pin_hash"])  # the old hash still works
    assert contact["phone_e164"] == B
    assert wallet.audit()["ok"]
    assert list((tmp_path / "backups").glob("accounts-pre-v2-*.db"))
    db.close()
    again = Database(path)  # a second open changes nothing
    with again.connect() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == LATEST
        assert c.execute("SELECT balance FROM users").fetchone()[0] == 120_000
    again.close()


def test_a_fresh_database_is_at_the_latest_and_the_seed_still_loads():
    from wallet.api import DEFAULT_SEED_PATH

    db = Database(":memory:", seed=DEFAULT_SEED_PATH)
    with db.connect() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == LATEST
        assert c.execute("SELECT COUNT(*) FROM users WHERE phone_e164 IS NOT NULL").fetchone()[0] == 30
    assert Wallet(db).audit()["ok"]


# ---------------------------------------------------------------- phones and PINs


@pytest.mark.parametrize("raw", ["07701234567", "0770 123 4567", "0770-123-4567", "+964 770 123 4567",
                                 "00964 770 123 4567", "9647701234567", "7701234567", "٠٧٧٠١٢٣٤٥٦٧"])
def test_every_common_shape_of_an_iraqi_mobile_is_one_canonical_number(raw):
    assert phones.canonical(raw) == "9647701234567"


@pytest.mark.parametrize("raw", ["0712345678", "07201234567", "0044123456789", "123", "", None, "07701234567890"])
def test_other_numbers_are_not_iraqi_mobiles(raw):
    assert phones.canonical(raw) is None


def test_mask_leaves_no_digit_of_a_number_and_reuses_the_placeholder():
    masked, mapping = phones.mask("دز 50000 07701234567 و 0770 123 4567 ولـ 07801112233")
    assert "07701234567" not in masked and "0780" not in masked and "50000" in masked
    assert masked.count("هاتف_ا") == 2 and "هاتف_ب" in masked
    assert mapping == {"هاتف_ا": "9647701234567", "هاتف_ب": "9647801112233"}
    assert not any(ch.isdigit() for ch in masked.replace("50000", ""))
    assert phones.display("9647701234567") == "0770 123 4567" and phones.operator_of("9647801112233") == "zain"
    assert phones.token_of("لـ هاتف_ب") == "هاتف_ب" and phones.token_of("هاتفي") is None


@pytest.mark.parametrize("pin, ok", [("2583", True), ("٢٥٨٣", True), ("83917", True), ("1234", False), ("0000", False),
                                     ("9876", False), ("1212", False), ("4567", False), ("123456", False),
                                     ("2580", False), ("12a4", None), ("123", None), ("1234567", None)])
def test_the_pin_policy(pin, ok):
    status, value = pins.check(pin, "9647701234567")
    if ok is None:
        assert status == pins.NOT_DIGITS
    else:
        assert (status == pins.OK) is ok


def test_the_peppered_hash_needs_the_pepper():
    stored = hash_account_pin("2583", pepper=b"right")
    assert verify_pin("2583", stored, pepper=b"right") and verify_pin("٢٥٨٣", stored, pepper=b"right")
    assert not verify_pin("2583", stored, pepper=b"wrong") and not verify_pin("2584", stored, pepper=b"right")
    assert not verify_pin("2583", "")  # an account without a PIN never matches


def test_names_are_letters_only():
    assert clean_name(" زينب علي ") == "زينب علي" and clean_name("Zainab O'Neil") == "Zainab O'Neil"
    assert clean_name("زينب 2") is None and clean_name("x") is None and clean_name("http://x.com") is None


# ---------------------------------------------------------------- transfers


def test_a_transfer_to_an_account_credits_it_in_the_same_transaction_with_one_notice(env):
    clock, db, wallet, accounts, a, b = env
    quote, done = transfer(wallet, a, {"phone": "0770 000 0002"}, 25_000)
    assert quote["target"]["payee_user_id"] == b and quote["target"]["display_name"] == "علي ح."
    assert done["state"] == "SUCCEEDED"
    assert wallet.user_profile(b)["balance"] == 25_000
    wallet.execute(quote["id"], "2583")  # a replay: the stored result, nothing again
    assert wallet.user_profile(b)["balance"] == 25_000
    notices = accounts.pending_notices(b)
    assert [n["kind"] for n in notices] == ["transfer_in"]
    [incoming] = [h for h in accounts.history(b) if h["kind"] == "transfer"]
    assert incoming["direction"] == "in" and incoming["amount"] == 25_000 and incoming["name"] == "سارة ك."
    assert wallet.audit()["ok"]


def test_a_transfer_to_a_number_with_no_account_is_held_and_claimed_at_sign_up(env):
    clock, db, wallet, accounts, a, b = env
    quote, done = transfer(wallet, a, {"phone": C}, 10_000)
    assert quote["target"]["hold"] is True and done["result"]["held"] is True
    assert wallet.user_profile(a)["balance"] == 500_000 - 10_250 and wallet.user_profile(a)["open_holds"] == 1
    assert wallet.audit()["ok"] and wallet.audit()["totals"]["holds"] == 10_000
    opened = wallet.open_account(C, "زينب علي", gender="f", language="iq", pin_hash=hash_account_pin("9753"))
    assert opened["user"]["balance"] == 10_000 and len(opened["claimed"]) == 1
    assert wallet.holds()[0]["state"] == "CLAIMED"
    assert [n["kind"] for n in accounts.pending_notices(a)] == ["admin_credit", "hold_claimed"]
    assert wallet.audit()["ok"] and wallet.audit()["totals"]["holds"] == 0


def test_an_expired_hold_is_refunded_with_the_fee_exactly_once(env):
    clock, db, wallet, accounts, a, b = env
    transfer(wallet, a, {"phone": C}, 10_000)
    clock.now += timedelta(days=7, minutes=1)
    assert wallet.expire_holds() == 1 and wallet.expire_holds() == 0
    assert wallet.user_profile(a)["balance"] == 500_000
    [hold] = wallet.holds()
    assert hold["state"] == "REFUNDED" and hold["settle_reason"] == "expired"
    assert wallet.audit()["ok"]


def test_a_claim_racing_an_expiry_has_exactly_one_winner(tmp_path):
    clock = Clock()
    db = Database(tmp_path / "race.db")
    wallet = Wallet(db, clock=clock)
    a = wallet.open_account(A, "سارة كريم", gender="f", language="iq", pin_hash=hash_account_pin("2583"))["user"]["id"]
    wallet.admin_credit(a, 100_000, "race credit", op_id="op-r", actor="admin:test")
    transfer(wallet, a, {"phone": C}, 10_000)
    clock.now += timedelta(days=8)
    errors = []

    def claim():
        try:
            wallet.open_account(C, "زينب", gender="f", language="iq", pin_hash=hash_account_pin("9753"))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=claim), threading.Thread(target=wallet.expire_holds)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    [hold] = wallet.holds()
    claimed = wallet.user_by_phone(C)["balance"]
    refunded = wallet.user_profile(a)["balance"] == 100_000
    assert (hold["state"], claimed, refunded) in (("CLAIMED", 10_000, False), ("REFUNDED", 0, True))
    assert wallet.audit()["ok"]
    db.close()


def test_hold_limits_self_transfers_and_closed_recipients(env, monkeypatch):
    clock, db, wallet, accounts, a, b = env
    with pytest.raises(HTTPException) as exc:
        wallet.create_quote(a, "transfer", {"phone": A}, 1000)
    assert exc.value.detail["error"] == "transfer_to_self"
    with pytest.raises(HTTPException) as exc:
        wallet.create_quote(a, "transfer", {"phone": C}, 1_000_001)
    assert exc.value.detail["error"] == "hold_cap"
    for n in range(5):
        transfer(wallet, a, {"phone": f"964770100000{n}"}, 1000)
    with pytest.raises(HTTPException) as exc:
        wallet.create_quote(a, "transfer", {"phone": C}, 1000)
    assert exc.value.detail["error"] == "hold_limit"
    wallet.set_status(b, "closed", actor="admin:test", reason="test close")
    with pytest.raises(HTTPException) as exc:
        wallet.create_quote(a, "transfer", {"phone": B}, 1000)
    assert exc.value.detail["error"] == "recipient_closed"


def test_a_frozen_account_cannot_pay_but_still_receives(env):
    clock, db, wallet, accounts, a, b = env
    quote = wallet.create_quote(a, "transfer", {"phone": B}, 5000)
    wallet.set_status(a, "frozen", actor="admin:test", reason="checking")
    with pytest.raises(HTTPException) as exc:
        wallet.execute(quote["id"], "2583")  # a card made before the freeze is refused too
    assert exc.value.status_code == 423 and exc.value.detail["error"] == "account_frozen"
    with pytest.raises(HTTPException):
        wallet.create_quote(a, "transfer", {"phone": B}, 5000)
    wallet.admin_credit(b, 50_000, "for the return", op_id="op-b", actor="admin:test")
    transfer(wallet, b, {"phone": A}, 5000, pin="8642")
    assert wallet.user_profile(a)["balance"] == 505_000
    assert wallet.audit()["ok"]


# ---------------------------------------------------------------- admin money


def test_admin_credit_and_debit_are_idempotent_and_never_go_below_zero(env):
    clock, db, wallet, accounts, a, b = env
    first = wallet.admin_credit(b, 70_000, "cash at the shop", op_id="op-x", actor="admin:test")
    again = wallet.admin_credit(b, 70_000, "cash at the shop", op_id="op-x", actor="admin:test")
    assert again["replayed"] and again["txn_id"] == first["txn_id"]
    assert wallet.user_profile(b)["balance"] == 70_000
    with pytest.raises(HTTPException):
        wallet.admin_debit(b, 70_001, "too much", op_id="op-y", actor="admin:test")
    wallet.admin_debit(b, 20_000, "correction", op_id="op-z", actor="admin:test")
    assert wallet.user_profile(b)["balance"] == 50_000
    with pytest.raises(HTTPException):
        wallet.admin_credit(b, 1000, "no", op_id="op-w", actor="admin:test")  # a reason of 3+ characters
    report = wallet.audit()
    assert report["ok"] and report["totals"]["cash"] == 550_000
    with db.connect() as conn:
        actions = [r[0] for r in conn.execute("SELECT action FROM audit_log WHERE actor = 'admin:test'")]
    assert actions.count("admin_credit") == 2 and actions.count("admin_debit") == 1  # a's opening, b's credit


def test_the_audit_catches_an_injected_drift(env):
    clock, db, wallet, accounts, a, b = env
    with db.connect() as conn:
        conn.execute("UPDATE users SET balance = balance + 1 WHERE id = ?", (a,))
    assert not wallet.audit()["ok"]


# ---------------------------------------------------------------- the PIN in the wallet


def test_lockouts_escalate_to_a_block_and_a_reset_starts_the_cap(env):
    clock, db, wallet, accounts, a, b = env
    for round_ in range(3):
        quote = wallet.create_quote(a, "transfer", {"phone": B}, 1000)
        for attempt in range(5):
            with pytest.raises(HTTPException) as exc:
                wallet.execute(quote["id"], "1111")
        assert exc.value.detail["error"] == ("pin_blocked" if round_ == 2 else "pin_locked")
        clock.now += timedelta(minutes=16)
    assert wallet.user_profile(a)["pin_state"] == "blocked"
    assert wallet.check_pin(a, "2583") == "blocked"
    wallet.reset_pin(a, actor="admin:test", reason="phone call")
    assert wallet.user_profile(a)["pin_state"] == "unset"
    with pytest.raises(HTTPException) as exc:
        wallet.create_quote(a, "transfer", {"phone": B}, 1000)
    assert exc.value.detail["error"] == "pin_not_set"
    wallet.set_pin(a, hash_account_pin("9753"))
    quote, done = transfer(wallet, a, {"phone": B}, 150_000, pin="9753")
    assert done["state"] == "FAILED" and done["result"]["reason"] == "pin_reset_cooldown"
    quote, done = transfer(wallet, a, {"phone": B}, 90_000, pin="9753")
    assert done["state"] == "SUCCEEDED"
    clock.now += timedelta(hours=25)
    quote, done = transfer(wallet, a, {"phone": B}, 150_000, pin="9753")
    assert done["state"] == "SUCCEEDED"
    assert wallet.audit()["ok"]


def test_check_pin_counts_towards_the_lockout(env):
    clock, db, wallet, accounts, a, b = env
    assert wallet.check_pin(a, "2583") == "ok"
    results = [wallet.check_pin(a, "1111") for _ in range(5)]
    assert results == ["wrong"] * 4 + ["locked"]
    assert wallet.check_pin(a, "2583") == "locked"


# ---------------------------------------------------------------- contacts, bills, backups


def test_contacts_add_rename_delete_and_aliases(env):
    clock, db, wallet, accounts, a, b = env
    status, contact = accounts.add_contact(a, "علي", B)
    assert status == "added" and accounts.contacts(a)[0]["account_id"] == b
    assert accounts.add_contact(a, "علي", B)[0] == "same"
    assert accounts.add_contact(a, "حمودي", B)[0] == "other_name"
    assert accounts.add_contact(a, "أنا", A)[0] == "self"
    accounts.set_alias(a, contact["contact_id"], "اخوي")
    assert accounts.contacts(a)[0]["alias"] == "اخوي"
    assert accounts.rename_contact(a, contact["contact_id"], "حمودي")
    assert accounts.delete_contact(a, contact["contact_id"]) and accounts.contacts(a) == []
    assert wallet.contacts(a) == []  # soft-deleted contacts are never offered


def test_the_mock_bill_is_stable_per_account_and_month_and_issued_once():
    biller = {"id": "generator", "kind": "generator", "mock_min": 25_000, "mock_max": 90_000}
    first = billing.mock_amount(biller, "350", "2026-11")
    assert first == billing.mock_amount(biller, "350", "2026-11") and 25_000 <= first <= 90_000 and first % 250 == 0
    assert billing.mock_amount(biller, "350", "2026-12") != first or billing.mock_amount(biller, "351", "2026-11") != first


def test_a_saved_bill_account_gets_this_month_and_next_month_lazily(env):
    clock, db, wallet, accounts, a, b = env
    status, account, bill = accounts.add_bill_account(a, "generator", "350", "المولدة")
    assert status == "added" and bill["period"] == "2026-11" and bill["status"] == "unpaid"
    assert accounts.add_bill_account(a, "generator", "350", "المولدة")[0] == "exists"
    assert accounts.add_bill_account(a, "electricity", "12", "كهرباء")[0] == "bad_number"
    assert accounts.ensure_bills(a) == 0
    clock.now += timedelta(days=30)
    assert accounts.ensure_bills(a) == 1 and accounts.ensure_bills(a) == 0
    periods = [x["period"] for x in accounts.bills(account["id"])]
    assert periods == ["2026-11", "2026-12"]
    quote = wallet.create_quote(a, "bill", {"bill_id": bill["id"]}, None)
    wallet.execute(quote["id"], "2583")
    assert not accounts.set_bill(bill["id"], 1000, "admin:test")  # a paid bill never changes


def test_backups_are_written_checked_listed_and_pruned(tmp_path):
    clock = Clock()
    db = Database(tmp_path / "accounts.db")
    Wallet(db, clock=clock).open_account(A, "سارة", gender="f", language="iq", pin_hash=hash_account_pin("2583"))
    early = clock.now.replace(hour=0)  # 03:00 Baghdad: before the backup hour
    assert backup.daily(tmp_path / "accounts.db", early) is None
    written = backup.daily(tmp_path / "accounts.db", clock.now)
    assert written is not None and written.name == "accounts-2026-11-02.db"
    assert backup.daily(tmp_path / "accounts.db", clock.now) is None  # once a day
    assert backup.now(tmp_path / "accounts.db", clock.now) is not None
    assert len(backup.listing(tmp_path / "accounts.db")) == 2
    assert backup.prune(tmp_path / "accounts.db", clock.now + timedelta(days=20)) == 2
    db.close()
