"""Mock wallet: endpoints, quote life cycle, execution rules, seed loading, audit."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from wallet.api import clean_phone, create_app
from wallet.db import Database, verify_pin
from wallet.seed import load_seed

NOOR_PIN = "1234"
ALI_PIN = "4321"

# Small inline fixture: fictional people, numbers and billers.
SEED = {
    "users": [
        {"id": "u_noor", "name": "نور حسين", "phone": "07701234501", "balance": 1_000_000,
         "daily_limit": 500_000, "pin": NOOR_PIN, "gender": "f"},
        {"id": "u_ali", "name": "علي جاسم", "phone": "07801234502", "balance": 50_000,
         "daily_limit": 200_000, "pin": ALI_PIN, "gender": "m"},
    ],
    "contacts": [
        {"contact_id": "c_ali", "user_id": "u_noor", "display_name": "علاوي", "phone": "+964 780 123 4502",
         "alias": None},
        {"contact_id": "c_mustafa", "user_id": "u_noor", "display_name": "مصطفى", "phone": "07501234503",
         "alias": "اخوي"},
        {"contact_id": "c_noor", "user_id": "u_ali", "display_name": "نور", "phone": "07701234501", "alias": None},
    ],
    "billers": [
        {"id": "bl_gen", "kind": "generator", "name_ar": "مولدة أبو سجاد", "name_en": "Abu Sajjad generator"},
        {"id": "bl_elec", "kind": "electricity", "name_ar": "كهرباء حي الجامعة", "name_en": "Jamia electricity"},
        {"id": "bl_topup", "kind": "topup", "name_ar": "رصيد الرافدين التجريبي", "name_en": "Demo top-up"},
    ],
    "bill_accounts": [
        {"id": "a_gen", "user_id": "u_noor", "biller_id": "bl_gen", "account_no": "G-77",
         "holder_name": "نور حسين", "label_ar": "مولدة البيت"},
        {"id": "a_elec", "user_id": "u_noor", "biller_id": "bl_elec", "account_no": "E-1200",
         "holder_name": "حسين كاظم", "label_ar": "كهرباء البيت"},
        {"id": "a_phone", "user_id": "u_noor", "biller_id": "bl_topup", "account_no": "07701234501",
         "holder_name": "نور حسين", "label_ar": "رصيدي"},
    ],
    "bills": [
        {"id": "b_gen_oct", "account_id": "a_gen", "period": "2026-10", "amount_due": 25_000, "status": "unpaid"},
        {"id": "b_elec_sep", "account_id": "a_elec", "period": "2026-09", "amount_due": 40_000, "status": "paid"},
        {"id": "b_elec_oct", "account_id": "a_elec", "period": "2026-10", "amount_due": 45_000, "status": "unpaid"},
    ],
    "transactions": [
        {"id": "t_hist", "quote_id": None, "user_id": "u_noor", "kind": "transfer",
         "target_json": {"contact_id": "c_mustafa"}, "amount": 10_000, "fee": 250, "status": "succeeded",
         "created_at": "2026-10-18T08:30:00+03:00"},
    ],
}

START = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)  # 12:00 in Baghdad


class FakeClock:
    def __init__(self, start: datetime = START):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now += timedelta(**delta)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def app(clock):
    app = create_app(":memory:", seed=SEED, clock=clock)
    yield app
    app.state.wallet.db.close()


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


def quote(client, kind="transfer", target=None, amount=None, user_id="u_noor"):
    body = {"user_id": user_id, "kind": kind, "target": target or {"contact_id": "c_ali"}}
    if amount is not None:
        body["amount"] = amount
    return client.post("/quotes", json=body)


def execute(client, qid, pin=NOOR_PIN, key="same"):
    headers = {"Idempotency-Key": qid if key == "same" else key} if key else {}
    return client.post(f"/quotes/{qid}/execute", json={"pin": pin}, headers=headers)


def balance(client, uid="u_noor") -> int:
    return client.get(f"/users/{uid}").json()["balance"]


def audit_ok(client) -> bool:
    report = client.get("/admin/audit").json()
    assert report["ok"], report["violations"]
    return True


# ------------------------------------------------------------------ reads


def test_profile_hides_pin_and_shows_daily_room(client):
    profile = client.get("/users/u_noor").json()
    assert profile["balance"] == 1_000_000
    assert profile["gender"] == "f"
    assert "pin_hash" not in profile and "pin" not in profile
    assert profile["spent_today"] == 10_000  # seeded transfer earlier today (Baghdad time)
    assert profile["daily_remaining"] == 490_000


def test_unknown_user_is_404(client):
    response = client.get("/users/u_nobody")
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "unknown_user"


def test_lists(client):
    contacts = client.get("/users/u_noor/contacts").json()
    assert {c["contact_id"] for c in contacts} == {"c_ali", "c_mustafa"}
    accounts = client.get("/users/u_noor/bill-accounts").json()
    assert {a["id"]: a["biller_kind"] for a in accounts} == {
        "a_gen": "generator", "a_elec": "electricity", "a_phone": "topup"}
    bills = client.get("/bills", params={"account_id": "a_elec"}).json()
    assert [b["period"] for b in bills] == ["2026-09", "2026-10"]
    assert {b["id"] for b in client.get("/billers").json()} == {"bl_gen", "bl_elec", "bl_topup"}


def test_seed_hashes_pins():
    db = Database(":memory:", seed=SEED)
    with db.connect() as conn:
        stored = conn.execute("SELECT pin_hash FROM users WHERE id = 'u_noor'").fetchone()["pin_hash"]
    db.close()
    assert stored.startswith("pbkdf2_sha256$") and NOOR_PIN not in stored.split("$")
    assert verify_pin(NOOR_PIN, stored)
    assert not verify_pin("0000", stored)


def test_seed_file_db_survives_restart_without_reseeding(tmp_path):
    path = tmp_path / "wallet.db"
    loader = Database(path)
    load_seed(loader, SEED)
    loader.close()
    first_app = create_app(path, seed=SEED)  # users exist, so the seed is not loaded twice
    first = TestClient(first_app)
    assert len(first.get("/users/u_noor/contacts").json()) == 2
    qid = quote(first, amount=5_000).json()["id"]
    assert execute(first, qid).json()["state"] == "SUCCEEDED"
    first_app.state.wallet.db.close()
    second_app = create_app(path)
    second = TestClient(second_app)
    assert second.get(f"/quotes/{qid}").json()["state"] == "SUCCEEDED"
    assert balance(second) == 1_000_000 - 5_250
    audit_ok(second)
    second_app.state.wallet.db.close()


def test_clean_phone():
    assert clean_phone("٠٧٧٠١٢٣٤٥٠١") == "07701234501"
    assert clean_phone("+964 780 123 4502") == "07801234502"


# ------------------------------------------------------------------ quotes


def test_transfer_quote_has_fee_expiry_and_payee(client):
    q = quote(client, amount=50_000).json()
    assert q["state"] == "QUOTED" and q["result"] is None
    assert (q["amount"], q["fee"], q["total"], q["currency"]) == (50_000, 250, 50_250, "IQD")
    assert q["created_at"] == "2026-10-18T09:00:00Z"
    assert q["expires_at"] == "2026-10-18T09:05:00Z"
    assert q["target"]["phone_last4"] == "4502"
    assert q["target"]["payee_user_id"] == "u_ali"  # the contact also has a wallet here


@pytest.mark.parametrize(
    ("body", "status", "error"),
    [
        ({"kind": "transfer", "target": {"contact_id": "c_ali"}, "amount": 0}, 400, "non_positive_amount"),
        ({"kind": "transfer", "target": {"contact_id": "c_ali"}, "amount": -5}, 400, "non_positive_amount"),
        ({"kind": "transfer", "target": {"contact_id": "c_ali"}}, 400, "amount_required"),
        ({"kind": "transfer", "target": {"contact_id": "c_noor"}, "amount": 1000}, 400, "unknown_contact"),
        ({"kind": "bill", "target": {"bill_id": "b_elec_oct"}, "amount": 40_000}, 400, "amount_mismatch"),
        ({"kind": "bill", "target": {"bill_id": "b_elec_sep"}}, 409, "already_paid"),
        ({"kind": "topup", "target": {"biller_id": "bl_gen"}, "amount": 5000}, 400, "unknown_biller"),
    ],
)
def test_quote_rejections(client, body, status, error):
    response = client.post("/quotes", json={"user_id": "u_noor", **body})
    assert response.status_code == status
    assert response.json()["detail"]["error"] == error


def test_bill_quote_by_account_picks_oldest_unpaid_bill(client):
    q = quote(client, "bill", {"account_id": "a_elec"}).json()
    assert q["target"]["bill_id"] == "b_elec_oct"
    assert (q["amount"], q["fee"]) == (45_000, 0)
    assert q["target"]["holder_name"] == "حسين كاظم" and q["target"]["period"] == "2026-10"


def test_topup_quote_defaults_to_own_number(client):
    q = quote(client, "topup", {"biller_id": "bl_topup"}, amount=5_000).json()
    assert q["target"]["phone"] == "07701234501" and q["fee"] == 0
    via_account = quote(client, "topup", {"account_id": "a_phone"}, amount=10_000).json()
    assert via_account["target"]["biller_id"] == "bl_topup"


# ------------------------------------------------------------------ execute


def test_transfer_to_wallet_user_moves_money_once(client):
    qid = quote(client, amount=50_000).json()["id"]
    first = execute(client, qid)
    assert first.status_code == 200
    body = first.json()
    assert body["state"] == "SUCCEEDED"
    assert body["result"]["money_moved"] and body["result"]["balance_after"] == 949_750
    assert balance(client) == 949_750
    assert balance(client, "u_ali") == 100_000
    replay = execute(client, qid)
    assert replay.json() == body  # stored result, no second debit
    assert balance(client) == 949_750
    report = client.get("/admin/audit").json()
    assert report["ok"] and report["totals"]["fees"] == 250


def test_transfer_to_outside_number_leaves_the_wallet(client):
    qid = quote(client, target={"contact_id": "c_mustafa"}, amount=20_000).json()["id"]
    assert execute(client, qid).json()["state"] == "SUCCEEDED"
    report = client.get("/admin/audit").json()
    assert report["ok"] and report["totals"]["external"] == 20_000


def test_missing_idempotency_key_is_accepted_but_wrong_one_is_not(client):
    qid = quote(client, amount=1_000).json()["id"]
    wrong = execute(client, qid, key="q_other")
    assert wrong.status_code == 400
    assert wrong.json()["detail"]["error"] == "idempotency_key_mismatch"
    assert client.get(f"/quotes/{qid}").json()["state"] == "QUOTED"
    assert execute(client, qid, key=None).json()["state"] == "SUCCEEDED"


def test_wrong_pin_changes_nothing_and_quote_stays_open(client):
    qid = quote(client, amount=10_000).json()["id"]
    response = execute(client, qid, pin="9999")
    assert response.status_code == 403
    assert response.json()["detail"] == {"error": "wrong_pin", "state": "QUOTED", "money_moved": False}
    assert balance(client) == 1_000_000
    assert client.get(f"/quotes/{qid}").json()["state"] == "QUOTED"
    assert execute(client, qid).json()["state"] == "SUCCEEDED"


def test_five_wrong_pins_lock_the_pin_for_15_minutes(client, clock):
    qid = quote(client, amount=10_000).json()["id"]
    for _ in range(4):
        assert execute(client, qid, pin="9999").json()["detail"]["error"] == "wrong_pin"
    locked = execute(client, qid, pin="9999")  # the fifth wrong PIN in a row
    assert locked.status_code == 423
    assert locked.json()["detail"] == {"error": "pin_locked", "state": "QUOTED", "money_moved": False}
    assert execute(client, qid).status_code == 423  # even the right PIN waits
    assert client.get(f"/quotes/{qid}").json()["state"] == "QUOTED"
    assert balance(client) == 1_000_000

    clock.advance(minutes=15)
    fresh = quote(client, amount=10_000).json()["id"]
    assert execute(client, fresh).json()["state"] == "SUCCEEDED"
    assert audit_ok(client)


def test_right_pin_resets_the_wrong_pin_count(client):
    first = quote(client, amount=1_000).json()["id"]
    for _ in range(4):
        execute(client, first, pin="9999")
    assert execute(client, first).json()["state"] == "SUCCEEDED"
    second = quote(client, amount=1_000).json()["id"]
    for _ in range(4):
        assert execute(client, second, pin="9999").status_code == 403  # counting from zero again
    assert execute(client, second).json()["state"] == "SUCCEEDED"


def test_pin_typed_in_eastern_digits_is_accepted(client):
    qid = quote(client, amount=1_000).json()["id"]
    assert execute(client, qid, pin="١٢٣٤").json()["state"] == "SUCCEEDED"


def test_older_wallet_file_gets_the_pin_lock_columns(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT NOT NULL, phone TEXT,"
                " balance INTEGER NOT NULL, daily_limit INTEGER, pin_hash TEXT NOT NULL, gender TEXT)")
    old.commit()
    old.close()
    db = Database(path)
    with db.connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    db.close()
    assert {"pin_failures", "pin_locked_until"} <= columns


def test_bill_payment_marks_bill_paid(client):
    qid = quote(client, "bill", {"bill_id": "b_gen_oct"}).json()["id"]
    assert execute(client, qid).json()["state"] == "SUCCEEDED"
    bill = client.get("/bills", params={"account_id": "a_gen"}).json()[0]
    assert bill["status"] == "paid"
    assert balance(client) == 975_000
    again = quote(client, "bill", {"bill_id": "b_gen_oct"})
    assert again.status_code == 409 and again.json()["detail"]["error"] == "already_paid"


def test_bill_paid_by_an_earlier_quote_is_rejected_without_debit(client):
    first = quote(client, "bill", {"bill_id": "b_gen_oct"}).json()["id"]
    second = quote(client, "bill", {"bill_id": "b_gen_oct"}).json()["id"]
    assert execute(client, first).json()["state"] == "SUCCEEDED"
    late = execute(client, second).json()
    assert late["state"] == "FAILED"
    assert late["result"] == {"ok": False, "reason": "already_paid", "money_moved": False, "money_returned": False}
    assert balance(client) == 975_000
    audit_ok(client)


def test_topup_succeeds(client):
    qid = quote(client, "topup", {"biller_id": "bl_topup"}, amount=5_000).json()["id"]
    assert execute(client, qid).json()["state"] == "SUCCEEDED"
    assert balance(client) == 995_000
    assert client.get("/admin/audit").json()["totals"]["billers"] == 5_000


def test_insufficient_balance(client):
    qid = quote(client, target={"contact_id": "c_noor"}, amount=50_000, user_id="u_ali").json()["id"]
    response = execute(client, qid, pin=ALI_PIN).json()  # 50,000 + 250 fee > 50,000
    assert response["state"] == "FAILED" and response["result"]["reason"] == "insufficient_balance"
    assert balance(client, "u_ali") == 50_000
    audit_ok(client)


def test_daily_limit_counts_todays_payments(client):
    over = quote(client, amount=495_000).json()["id"]  # 10,000 already spent today; limit 500,000
    assert execute(client, over).json()["result"]["reason"] == "over_daily_limit"
    exact = quote(client, amount=490_000).json()["id"]
    assert execute(client, exact).json()["state"] == "SUCCEEDED"
    assert client.get("/users/u_noor").json()["daily_remaining"] == 0


def test_daily_limit_resets_at_baghdad_midnight(client, clock):
    first = quote(client, amount=490_000).json()["id"]
    assert execute(client, first).json()["state"] == "SUCCEEDED"
    clock.advance(hours=12)  # 21:00 UTC = 00:00 in Baghdad
    second = quote(client, amount=100_000).json()["id"]
    assert execute(client, second).json()["state"] == "SUCCEEDED"


def test_expired_quote_is_not_executed(client, clock):
    qid = quote(client, amount=10_000).json()["id"]
    clock.advance(minutes=5)
    response = execute(client, qid).json()
    assert response["state"] == "EXPIRED" and response["result"]["reason"] == "expired"
    assert balance(client) == 1_000_000
    assert execute(client, qid).json() == response
    audit_ok(client)


def test_reading_an_old_quote_marks_it_expired(client, clock):
    qid = quote(client, amount=10_000).json()["id"]
    clock.advance(minutes=4, seconds=59)
    assert client.get(f"/quotes/{qid}").json()["state"] == "QUOTED"
    clock.advance(seconds=1)
    assert client.get(f"/quotes/{qid}").json()["state"] == "EXPIRED"


def test_cancel_only_from_quoted(client):
    qid = quote(client, amount=10_000).json()["id"]
    cancelled = client.post(f"/quotes/{qid}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["state"] == "CANCELLED"
    assert client.post(f"/quotes/{qid}/cancel").json()["state"] == "CANCELLED"  # idempotent
    assert execute(client, qid).json()["state"] == "CANCELLED"  # cannot be revived
    assert balance(client) == 1_000_000

    paid = quote(client, amount=10_000).json()["id"]
    execute(client, paid)
    refused = client.post(f"/quotes/{paid}/cancel")
    assert refused.status_code == 409
    assert refused.json()["detail"]["state"] == "SUCCEEDED"


def test_unknown_quote_is_404(client):
    assert execute(client, "q_missing").status_code == 404
    assert client.get("/quotes/q_missing").status_code == 404


def test_transactions_since_minutes(client, clock):
    clock.advance(minutes=1)
    qid = quote(client, amount=7_000).json()["id"]
    execute(client, qid)
    recent = client.get("/users/u_noor/transactions", params={"since_minutes": 10}).json()
    assert [t["quote_id"] for t in recent] == [qid]
    assert recent[0]["target"]["contact_id"] == "c_ali" and recent[0]["status"] == "succeeded"
    everything = client.get("/users/u_noor/transactions").json()
    assert [t["id"] for t in everything][1:] == ["t_hist"]
    assert everything[1]["created_at"] == "2026-10-18T05:30:00Z"  # seed time normalised to UTC


# ------------------------------------------------------------------ faults and audit


def test_fault_endpoint(client):
    bad = client.post("/admin/faults", json={"mode": "meteor_strike"})
    assert bad.status_code == 422
    assert client.post("/admin/faults", json={"mode": "error_500", "times": 1}).json() == {
        "mode": "error_500", "times_left": 1}
    qid = quote(client, amount=1_000).json()["id"]
    assert execute(client, qid).status_code == 500
    assert client.get("/admin/faults").json() == {"mode": "none", "times_left": None}
    assert execute(client, qid).json()["state"] == "SUCCEEDED"


def test_biller_decline_returns_the_money(client):
    client.post("/admin/faults", json={"mode": "biller_decline_after_debit"})
    qid = quote(client, "bill", {"bill_id": "b_gen_oct"}).json()["id"]
    response = execute(client, qid).json()
    assert response["state"] == "FAILED"
    assert response["result"] == {"ok": False, "reason": "biller_declined", "money_moved": False,
                                  "money_returned": True}
    assert balance(client) == 1_000_000
    assert client.get("/bills", params={"account_id": "a_gen"}).json()[0]["status"] == "unpaid"
    history = client.get("/users/u_noor/transactions", params={"since_minutes": 5}).json()
    assert [t["status"] for t in history] == ["reversed"]
    audit_ok(client)


def test_biller_down_fails_before_debit(client):
    client.post("/admin/faults", json={"mode": "biller_down"})
    qid = quote(client, "topup", {"biller_id": "bl_topup"}, amount=5_000).json()["id"]
    assert execute(client, qid).json()["result"]["reason"] == "biller_down"
    assert balance(client) == 1_000_000
    transfer = quote(client, amount=5_000).json()["id"]  # transfers do not touch a biller
    assert execute(client, transfer).json()["state"] == "SUCCEEDED"


def test_audit_catches_tampering(app, client):
    qid = quote(client, amount=10_000).json()["id"]
    execute(client, qid)
    audit_ok(client)
    with app.state.wallet.db.connect() as conn:
        conn.execute("UPDATE users SET balance = balance + 1 WHERE id = 'u_ali'")
    report = client.get("/admin/audit").json()
    assert not report["ok"]
    assert not report["checks"]["money_conserved"]
    assert any("u_ali" in v for v in report["violations"])
