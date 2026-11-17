"""Wallet.open_demo_account (SIGNUP_MODE=demo): one transaction, idempotent, ledger-true, and demo people that are
never a hold and never a real account."""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from accounts import billing, phones, pins
from wallet.api import DEMO_FUNDS, DEMO_PLAN, create_app
from wallet.db import Database, verify_pin

REAL = "9647700000001"
VISITOR = "9647811112222"


@pytest.fixture
def wallet(tmp_path):
    db = Database(str(tmp_path / "accounts.db"))
    billing.ensure_catalogue(db)
    app = create_app(db)
    w = app.state.wallet
    w.open_account(REAL, "سارة كريم", gender="f", language="iq", pin_hash=pins.hash_pin("8080"))
    yield w
    db.close()


def _demo(w, key=VISITOR, pin="4719"):
    return w.open_demo_account(key, "كرار", language="iq", pin_hash=pins.hash_pin(pin))


def test_one_transaction_makes_the_whole_story_and_the_audit_stays_ok(wallet):
    result = _demo(wallet)
    assert result["created"] and result["user"]["balance"] == DEMO_PLAN["balance"]
    uid = result["user"]["id"]
    story = wallet.demo_story(uid)
    assert [t["name"] for t in story["transfers"]] == ["حسين علي", "زينب كاظم"]
    assert [t["amount"] for t in story["transfers"]] == [50_000, 25_000]
    assert sorted(b["biller_id"] for b in story["bills"]) == ["electricity", "generator"]
    audit = wallet.audit()
    assert audit["ok"], audit["violations"]
    assert audit["totals"]["cash"] == 0 and audit["totals"]["demo"] == DEMO_PLAN["balance"]
    # the made-up past is booked back to demo_funds: no real fees, billers or external money from a visitor
    assert audit["totals"]["fees"] == 0 and audit["totals"]["billers"] == 0 and audit["totals"]["external"] == 0
    with wallet.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM ledger WHERE entry = 'debit'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM admin_ops").fetchone()[0] == 0  # no admin money, no cash
        details = [json.loads(r[0]) for r in conn.execute("SELECT details_json FROM audit_log WHERE user_id = ?",
                                                          (uid,))]
        assert {"source": "demo"} in details
        user = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        assert user["source"] == "demo" and user["kyc_level"] is None and user["daily_limit"] == 500_000
    assert verify_pin("4719", user["pin_hash"])


def test_a_second_call_changes_nothing(wallet):
    first = _demo(wallet)
    again = _demo(wallet, pin="9031")
    assert again["created"] is False and again["user"]["id"] == first["user"]["id"]
    with wallet.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users WHERE source = 'demo'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM transactions WHERE user_id = ?",
                            (first["user"]["id"],)).fetchone()[0] == 4
    assert verify_pin("4719", wallet.user_by_phone(VISITOR)["pin_hash"])  # the PIN was not replaced


def test_a_demo_person_is_paid_to_external_never_a_hold_nor_the_real_account_on_its_number(wallet):
    uid = _demo(wallet)["user"]["id"]
    real = wallet.user_by_phone(REAL)
    with wallet.db.connect() as conn:
        conn.execute("UPDATE contacts SET phone = '07700000001', phone_e164 = ? WHERE user_id = ? AND alias = 'أخوي'",
                     (REAL, uid))
        conn.commit()
        cid = conn.execute("SELECT contact_id FROM contacts WHERE user_id = ? AND alias = 'أخوي'", (uid,)).fetchone()[0]
    quote = wallet.create_quote(uid, "transfer", {"contact_id": cid}, 20_000)
    assert quote["target"]["demo_payee"] is True and quote["target"]["payee_user_id"] is None
    assert "hold" not in quote["target"]
    done = wallet.execute(quote["id"], "4719")
    assert done["state"] == "SUCCEEDED" and not done["result"]["held"]
    assert wallet.user_by_phone(REAL)["balance"] == real["balance"]
    by_number = wallet.create_quote(uid, "transfer", {"phone": "07700000001"}, 1_000)  # typed: the same demo person
    assert by_number["target"]["demo_payee"] is True
    with wallet.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM holds").fetchone()[0] == 0
    assert wallet.audit()["ok"]


def test_a_username_account_key_is_never_a_number(wallet):
    key = phones.user_key("IQ.77")
    result = _demo(wallet, key=key)
    uid = result["user"]["id"]
    assert result["user"]["phone"] is None and result["user"]["phone_e164"] == key
    sara = wallet.user_by_phone(REAL)
    with pytest.raises(HTTPException) as refused:  # nobody can send to it by number
        wallet.create_quote(sara["id"], "transfer", {"phone": key}, 1_000)
    assert refused.value.detail["error"] == "invalid_phone"
    # it can send to a real number, but its pretend money goes to external: (no real credit, no notice)
    quote = wallet.create_quote(uid, "transfer", {"phone": "07700000001"}, 5_000)
    assert quote["target"]["demo_payee"] is True
    wallet.execute(quote["id"], "4719")
    assert wallet.user_by_phone(REAL)["balance"] == sara["balance"]
    with wallet.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM outbox WHERE kind = 'transfer_in'").fetchone()[0] == 0
    assert wallet.audit()["ok"]


def test_holds_waiting_for_the_number_are_claimed(wallet):
    sara = wallet.user_by_phone(REAL)
    wallet.admin_credit(sara["id"], 50_000, "test money", op_id="op_test", actor="admin")
    quote = wallet.create_quote(sara["id"], "transfer", {"phone": "0" + VISITOR[3:]}, 7_000)
    wallet.execute(quote["id"], "8080")
    result = _demo(wallet)
    assert [c["amount"] for c in result["claimed"]] == [7_000]
    assert wallet.user_profile(result["user"]["id"])["balance"] == DEMO_PLAN["balance"] + 7_000
    assert wallet.audit()["ok"]


def test_the_seeded_past_is_outside_todays_limit(wallet):
    uid = _demo(wallet)["user"]["id"]
    assert wallet.user_profile(uid)["spent_today"] == 0
    with wallet.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM ledger WHERE account = ? AND entry = 'demo_opening'",
                            (DEMO_FUNDS,)).fetchone()[0] == 1


def test_a_demo_account_never_pays_a_real_account_nor_makes_a_hold(wallet):
    """Pretend money must never become real: a typed number of a real account, or of a number with no account,
    is paid to external: (no credit, no notice, no hold), and execute re-checks a quote made before the fix."""
    uid = _demo(wallet)["user"]["id"]
    real = wallet.user_by_phone(REAL)
    to_real = wallet.create_quote(uid, "transfer", {"phone": "07700000001"}, 100_000)
    assert to_real["target"]["demo_payee"] is True and to_real["target"]["payee_user_id"] is None
    assert wallet.execute(to_real["id"], "4719")["state"] == "SUCCEEDED"
    assert wallet.user_by_phone(REAL)["balance"] == real["balance"]
    unknown = wallet.create_quote(uid, "transfer", {"phone": "07901234567"}, 10_000)
    assert "hold" not in unknown["target"]
    assert not wallet.execute(unknown["id"], "4719")["result"]["held"]
    # a quote whose stored target still points at the real account (made by older code) is re-routed at execute
    stale = wallet.create_quote(uid, "transfer", {"phone": "07700000001"}, 1_000)
    with wallet.db.connect() as conn:
        target = json.loads(conn.execute("SELECT target_json FROM quotes WHERE id = ?", (stale["id"],)).fetchone()[0])
        target.update({"payee_user_id": real["id"], "demo_payee": False})
        conn.execute("UPDATE quotes SET target_json = ? WHERE id = ?", (json.dumps(target), stale["id"]))
        conn.commit()
    wallet.execute(stale["id"], "4719")
    assert wallet.user_by_phone(REAL)["balance"] == real["balance"]
    with wallet.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM holds").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM outbox WHERE kind = 'transfer_in'").fetchone()[0] == 0
    assert wallet.audit()["ok"]
