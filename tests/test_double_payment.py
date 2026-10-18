"""Property-style check that no fault and no client behaviour can double a payment.

Every fault mode x every scenario x {transfer, bill}. After each run the money invariants must
hold, both as reported by /admin/audit and as recomputed here straight from the tables:
at most one debit per quote, no negative balance, money conserved.
"""

import sqlite3
import threading
from contextlib import ExitStack, closing

import pytest
from fastapi.testclient import TestClient

from wallet.api import create_app
from wallet.faults import MODES

PIN = "2468"
OPENING = {"u_zahraa": 100_000, "u_haidar": 20_000}

# Zahraa can afford exactly one of two 60,000 payments.
SEED = {
    "users": [
        {"id": "u_zahraa", "name": "زهراء كريم", "phone": "07701110001", "balance": OPENING["u_zahraa"],
         "daily_limit": 1_000_000, "pin": PIN, "gender": "f"},
        {"id": "u_haidar", "name": "حيدر سلمان", "phone": "07801110002", "balance": OPENING["u_haidar"],
         "daily_limit": 1_000_000, "pin": "1357", "gender": "m"},
    ],
    "contacts": [
        {"contact_id": "c_haidar", "user_id": "u_zahraa", "display_name": "حيدر", "phone": "07801110002"},
    ],
    "billers": [{"id": "bl_gen", "kind": "generator", "name_ar": "مولدة الحي", "name_en": "Area generator"}],
    "bill_accounts": [
        {"id": "a_gen", "user_id": "u_zahraa", "biller_id": "bl_gen", "account_no": "G-1001",
         "holder_name": "زهراء كريم", "label_ar": "مولدة البيت"},
    ],
    "bills": [
        {"id": "b_sep", "account_id": "a_gen", "period": "2026-09", "amount_due": 60_000, "status": "unpaid"},
        {"id": "b_oct", "account_id": "a_gen", "period": "2026-10", "amount_due": 60_000, "status": "unpaid"},
    ],
}

SCENARIOS = ("single_call", "same_key_retry", "double_tap", "restart", "two_quotes_race")
KINDS = ("transfer", "bill")
BILLER_FAULTS = ("biller_down", "biller_decline_after_debit")


@pytest.fixture
def start():
    """start(db_path, mode, times) -> (app, client): a fresh app instance on the DB file
    (what a process restart looks like), with a fault set. Everything is closed afterwards."""
    with ExitStack() as stack:

        def _start(db_path, mode="none", times=None):
            app = create_app(db_path, seed=SEED)
            app.state.faults.set(mode, times)
            stack.callback(app.state.wallet.db.close)
            return app, stack.enter_context(TestClient(app))

        yield _start


def new_quote(client, kind, bill_id="b_sep") -> str:
    target = {"contact_id": "c_haidar"} if kind == "transfer" else {"bill_id": bill_id}
    body = {"user_id": "u_zahraa", "kind": kind, "target": target}
    if kind == "transfer":
        body["amount"] = 60_000
    response = client.post("/quotes", json=body)
    assert response.status_code == 200, response.text
    return response.json()["id"]


def execute(client, qid):
    return client.post(f"/quotes/{qid}/execute", json={"pin": PIN}, headers={"Idempotency-Key": qid})


def in_parallel(app, qids):
    """Fire one execute per quote id from separate threads, released together."""
    barrier = threading.Barrier(len(qids))
    responses = [None] * len(qids)

    def tap(i, qid):
        client = TestClient(app)
        barrier.wait()
        try:
            responses[i] = execute(client, qid)
        except Exception as exc:  # surfaced by the assertion below
            responses[i] = exc

    threads = [threading.Thread(target=tap, args=(i, qid)) for i, qid in enumerate(qids)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert all(hasattr(r, "status_code") for r in responses), responses
    return responses


def blocks_bill(mode, kind) -> bool:
    return kind == "bill" and mode in BILLER_FAULTS


def expected_single_call(mode, kind) -> tuple[int, str]:
    """(HTTP status the client sees, quote state on the server) after one call."""
    if mode == "timeout_before_commit":
        return 504, "QUOTED"
    if mode == "drop_after_commit":
        return 504, "SUCCEEDED"
    if mode == "error_500":
        return 500, "QUOTED"
    return 200, "FAILED" if blocks_bill(mode, kind) else "SUCCEEDED"


def check_invariants(client, db_path, qids) -> dict:
    """Audit endpoint plus independent recomputation from the raw tables."""
    report = client.get("/admin/audit").json()
    assert report["ok"], report["violations"]

    quotes = {qid: client.get(f"/quotes/{qid}").json() for qid in qids}
    with closing(sqlite3.connect(db_path)) as conn:
        for qid in qids:
            txns = conn.execute("SELECT COUNT(*) FROM transactions WHERE quote_id = ?", (qid,)).fetchone()[0]
            debits = conn.execute(
                "SELECT COUNT(*) FROM ledger WHERE quote_id = ? AND entry = 'debit'", (qid,)
            ).fetchone()[0]
            assert txns <= 1 and debits <= 1, f"{qid} debited {debits} times"
        balances = dict(conn.execute("SELECT id, balance FROM users").fetchall())
        bill_status = dict(conn.execute("SELECT id, status FROM bills").fetchall())

    assert all(value >= 0 for value in balances.values()), balances
    succeeded = [q for q in quotes.values() if q["state"] == "SUCCEEDED"]
    assert balances["u_zahraa"] == OPENING["u_zahraa"] - sum(q["total"] for q in succeeded)
    to_haidar = sum(q["amount"] for q in succeeded if q["target"].get("payee_user_id") == "u_haidar")
    assert balances["u_haidar"] == OPENING["u_haidar"] + to_haidar
    left_wallet = sum(q["fee"] + (0 if q["target"].get("payee_user_id") else q["amount"]) for q in succeeded)
    assert sum(balances.values()) == sum(OPENING.values()) - left_wallet  # money conserved
    for q in quotes.values():
        if q["kind"] == "bill":
            assert (bill_status[q["target"]["bill_id"]] == "paid") == (q["state"] == "SUCCEEDED")
        if q["state"] == "SUCCEEDED":
            assert q["result"]["transaction_id"]
    return quotes


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("mode", MODES)
def test_no_double_payment(tmp_path, start, mode, scenario, kind):
    db_path = str(tmp_path / "wallet.db")
    final_state = "FAILED" if blocks_bill(mode, kind) else "SUCCEEDED"

    if scenario == "two_quotes_race":
        app, client = start(db_path)
        qids = [new_quote(client, kind, "b_sep"), new_quote(client, kind, "b_oct")]
        app.state.faults.set(mode)
        in_parallel(app, qids)
        _, settled = start(db_path)  # restart without faults, then same-key retries settle both
        assert all(execute(settled, qid).status_code == 200 for qid in qids)
        quotes = check_invariants(settled, db_path, qids)
        wins = [q for q in quotes.values() if q["state"] == "SUCCEEDED"]
        assert len(wins) == (0 if blocks_bill(mode, kind) else 1)
        losers = [q for q in quotes.values() if q["state"] != "SUCCEEDED"]
        if not blocks_bill(mode, kind):
            assert losers[0]["result"]["reason"] == "insufficient_balance"
        return

    app, client = start(db_path)
    qid = new_quote(client, kind)

    if scenario == "single_call":
        app.state.faults.set(mode)
        status, state = expected_single_call(mode, kind)
        assert execute(client, qid).status_code == status
        quotes = check_invariants(client, db_path, [qid])
        assert quotes[qid]["state"] == state
        return

    if scenario == "same_key_retry":
        app.state.faults.set(mode, times=1)
        first = execute(client, qid)
        second = execute(client, qid)
        assert first.status_code == expected_single_call(mode, kind)[0]
        assert second.status_code == 200
        if first.status_code == 200:
            assert first.json() == second.json()

    elif scenario == "double_tap":
        app.state.faults.set(mode)
        taps = in_parallel(app, [qid, qid])
        answered = [r.json() for r in taps if r.status_code == 200]
        assert all(body == answered[0] for body in answered)  # both taps see the same single outcome
        second = execute(start(db_path)[1], qid)  # restart and retry with the same key to settle
        assert second.status_code == 200

    elif scenario == "restart":
        app.state.faults.set(mode)
        first = execute(client, qid)
        assert first.status_code == expected_single_call(mode, kind)[0]
        _, client = start(db_path)  # new app instance on the same DB file, faults cleared
        second = execute(client, qid)
        assert second.status_code == 200

    assert second.json()["state"] == final_state
    quotes = check_invariants(client, db_path, [qid])
    assert quotes[qid]["state"] == final_state


def test_retry_after_drop_after_commit_returns_succeeded_without_second_debit(tmp_path, start):
    db_path = str(tmp_path / "wallet.db")
    app, client = start(db_path)
    qid = new_quote(client, "transfer")
    app.state.faults.set("drop_after_commit")  # stays on: replays must still get the stored answer

    lost = execute(client, qid)
    assert lost.status_code == 504  # the client cannot tell whether money moved
    retry = execute(client, qid)
    assert retry.status_code == 200
    body = retry.json()
    assert body["state"] == "SUCCEEDED" and body["result"]["money_moved"]
    assert client.get(f"/quotes/{qid}").json() == body
    assert client.get("/users/u_zahraa").json()["balance"] == 100_000 - 60_250
    history = client.get("/users/u_zahraa/transactions", params={"since_minutes": 5}).json()
    assert [t["quote_id"] for t in history] == [qid]
    check_invariants(client, db_path, [qid])


def test_timeout_before_commit_moves_nothing_and_retry_pays_once(tmp_path, start):
    db_path = str(tmp_path / "wallet.db")
    app, client = start(db_path)
    qid = new_quote(client, "bill")
    app.state.faults.set("timeout_before_commit", times=1)
    assert execute(client, qid).status_code == 504
    assert client.get(f"/quotes/{qid}").json()["state"] == "QUOTED"
    assert client.get("/users/u_zahraa").json()["balance"] == 100_000
    assert execute(client, qid).json()["state"] == "SUCCEEDED"
    assert execute(client, qid).json()["state"] == "SUCCEEDED"
    assert client.get("/users/u_zahraa").json()["balance"] == 40_000
    check_invariants(client, db_path, [qid])
