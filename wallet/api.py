"""Mock wallet HTTP API (SPEC.md "Wallet API"). Deterministic code that touches the money.

Run:  uvicorn wallet.api:app          (file DB from $WALLET_DB_PATH or data/wallet.db,
                                        seeded from data/wallet_seed.json on first use)
Test: TestClient(create_app(":memory:", seed=my_seed_dict))
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Callable, Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from wallet.db import Database, baghdad_day_start, from_iso, immediate, new_id, to_iso, utc_now, verify_pin
from wallet.faults import MODES, FaultInjector, SimulatedServerError, SimulatedTimeout

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = os.environ.get("WALLET_DB_PATH") or str(ROOT / "data" / "wallet.db")
DEFAULT_SEED_PATH = os.environ.get("WALLET_SEED_PATH") or str(ROOT / "data" / "wallet_seed.json")

QUOTE_TTL = timedelta(minutes=5)
FEES = {"transfer": 250, "bill": 0, "topup": 0}  # synthetic, IQD
BILLER_KINDS = ("bill", "topup")
EASTERN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

Clock = Callable[[], datetime]  # must return an aware datetime


class QuoteIn(BaseModel):
    user_id: str
    kind: Literal["transfer", "bill", "topup"]
    target: dict[str, Any] = Field(default_factory=dict)
    amount: int | None = None


class ExecuteIn(BaseModel):
    pin: str | int


class FaultIn(BaseModel):
    mode: str
    times: int | None = None


def api_error(status: int, code: str, **extra: Any) -> HTTPException:
    """Error body: {"detail": {"error": <code>, ...}}."""
    return HTTPException(status_code=status, detail={"error": code, **extra})


def failure(reason: str, *, money_returned: bool = False) -> dict:
    """Stored result of a quote that ended without moving money."""
    return {"ok": False, "reason": reason, "money_moved": False, "money_returned": money_returned}


def clean_phone(raw: Any) -> str:
    """Digits only, Eastern digits converted, +964 written as a local 0."""
    digits = "".join(ch for ch in str(raw or "").translate(EASTERN_DIGITS) if ch in "0123456789")
    return "0" + digits[3:] if digits.startswith("964") else digits


def parse_json(text: str | None) -> Any:
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


def payee_account(kind: str, target: dict) -> str:
    """Ledger account that receives the money of a quote."""
    if kind == "transfer":
        if target.get("payee_user_id"):
            return f"user:{target['payee_user_id']}"
        return f"external:{target['phone']}"
    return f"biller:{target['biller_id']}"


class Wallet:
    """Every wallet operation. Each write is a single BEGIN IMMEDIATE transaction."""

    def __init__(self, db: Database, clock: Clock | None = None, faults: FaultInjector | None = None):
        self.db = db
        self.clock = clock or utc_now
        self.faults = faults or FaultInjector()

    # ------------------------------------------------------------ reads

    def user_profile(self, uid: str) -> dict:
        with self.db.connect() as conn:
            user = self._user(conn, uid)
            spent = self._spent_today(conn, uid)
        limit = user["daily_limit"]
        return {
            "id": user["id"],
            "name": user["name"],
            "phone": user["phone"],
            "gender": user["gender"],
            "balance": user["balance"],
            "daily_limit": limit,
            "spent_today": spent,
            "daily_remaining": None if limit is None else max(0, limit - spent),
        }

    def contacts(self, uid: str) -> list[dict]:
        with self.db.connect() as conn:
            self._user(conn, uid)
            rows = conn.execute("SELECT * FROM contacts WHERE user_id = ? ORDER BY display_name", (uid,))
            return [dict(row) for row in rows]

    def bill_accounts(self, uid: str) -> list[dict]:
        with self.db.connect() as conn:
            self._user(conn, uid)
            rows = conn.execute(
                "SELECT a.*, b.kind AS biller_kind, b.name_ar AS biller_name_ar, b.name_en AS biller_name_en"
                " FROM bill_accounts a LEFT JOIN billers b ON b.id = a.biller_id"
                " WHERE a.user_id = ? ORDER BY a.id",
                (uid,),
            )
            return [dict(row) for row in rows]

    def bills(self, account_id: str) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM bills WHERE account_id = ? ORDER BY period", (account_id,))
            return [dict(row) for row in rows]

    def billers(self) -> list[dict]:
        with self.db.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM billers ORDER BY id")]

    def transactions(self, uid: str, since_minutes: int | None = None) -> list[dict]:
        """Newest first. Includes quotes still in flight (status 'pending' or 'unknown')."""
        since = "" if since_minutes is None else to_iso(self.clock() - timedelta(minutes=since_minutes))
        with self.db.connect() as conn:
            self._user(conn, uid)
            done = conn.execute(
                "SELECT * FROM transactions WHERE user_id = ? AND created_at >= ?", (uid, since)
            ).fetchall()
            in_flight = conn.execute(
                "SELECT * FROM quotes WHERE user_id = ? AND state IN ('EXECUTING', 'UNKNOWN') AND created_at >= ?",
                (uid, since),
            ).fetchall()
        items = [
            {
                "id": row["id"],
                "quote_id": row["quote_id"],
                "kind": row["kind"],
                "target": parse_json(row["target_json"]),
                "amount": row["amount"],
                "fee": row["fee"],
                "status": row["status"],
                "created_at": row["created_at"],
            }
            for row in done
        ]
        items += [
            {
                "id": None,
                "quote_id": row["id"],
                "kind": row["kind"],
                "target": parse_json(row["target_json"]),
                "amount": row["amount"],
                "fee": row["fee"],
                "status": "pending" if row["state"] == "EXECUTING" else "unknown",
                "created_at": row["created_at"],
            }
            for row in in_flight
        ]
        return sorted(items, key=lambda item: item["created_at"], reverse=True)

    # ------------------------------------------------------------ quotes

    def create_quote(self, user_id: str, kind: str, target: dict, amount: int | None) -> dict:
        """Server computes fee and a 5-minute expiry; target is validated and enriched."""
        if amount is not None and amount <= 0:
            raise api_error(400, "non_positive_amount")
        now = self.clock()
        with self.db.connect() as conn:
            user = self._user(conn, user_id)
            if kind == "transfer":
                details = self._transfer_target(conn, user, target)
            elif kind == "bill":
                details, amount = self._bill_target(conn, user, target, amount)
            else:
                details = self._topup_target(conn, user, target)
            if amount is None:
                raise api_error(400, "amount_required")
            qid = new_id("q")
            with immediate(conn):
                conn.execute(
                    "INSERT INTO quotes (id, user_id, kind, target_json, amount, fee, created_at, expires_at, state)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'QUOTED')",
                    (qid, user_id, kind, json.dumps(details, ensure_ascii=False), amount, FEES[kind],
                     to_iso(now), to_iso(now + QUOTE_TTL)),
                )
            return self._view(self._quote(conn, qid))

    def get_quote(self, qid: str) -> dict:
        """State + stored result. A QUOTED quote past its expiry is marked EXPIRED here."""
        with self.db.connect() as conn:
            with immediate(conn):
                quote = self._quote(conn, qid)
                if quote["state"] == "QUOTED" and self._expired(quote):
                    self._move(conn, qid, "QUOTED", "EXPIRED", failure("expired"))
                    quote = self._quote(conn, qid)
            return self._view(quote)

    def cancel(self, qid: str) -> dict:
        """Only a QUOTED quote can be cancelled; cancelling twice returns the same answer."""
        with self.db.connect() as conn:
            with immediate(conn):
                quote = self._quote(conn, qid)
                if quote["state"] == "QUOTED":
                    reason = "expired" if self._expired(quote) else "cancelled"
                    self._move(conn, qid, "QUOTED", reason.upper(), failure(reason))
                    quote = self._quote(conn, qid)
        view = self._view(quote)
        if quote["state"] != "CANCELLED":
            raise api_error(409, "not_cancellable", state=quote["state"], quote=view)
        return view

    def execute(self, qid: str, pin: str | int, idempotency_key: str | None = None) -> dict:
        """Run a quote at most once. Any later call returns the stored result, never a second debit."""
        if idempotency_key is not None and idempotency_key != qid:
            raise api_error(400, "idempotency_key_mismatch", quote_id=qid)
        if self.faults.fire("error_500"):
            raise SimulatedServerError(qid)
        with self.db.connect() as conn:
            with immediate(conn):
                quote = self._quote(conn, qid)
                if quote["state"] != "QUOTED":
                    return self._view(quote)  # replay: stored result
                self._move(conn, qid, "QUOTED", "EXECUTING")
                state, result = self._run(conn, quote, str(pin))
                self._move(conn, qid, "EXECUTING", state, result)
                if self.faults.fire("timeout_before_commit"):
                    raise SimulatedTimeout("timeout_before_commit", committed=False)  # rolls back
            view = self._view(self._quote(conn, qid))
        if self.faults.fire("drop_after_commit"):
            raise SimulatedTimeout("drop_after_commit", committed=True)  # committed, answer lost
        return view

    def _run(self, conn: sqlite3.Connection, quote: sqlite3.Row, pin: str) -> tuple[str, dict]:
        """The money part of execute, inside the caller's transaction. Returns (state, result)."""
        if self._expired(quote):
            return "EXPIRED", failure("expired")
        qid, uid, kind = quote["id"], quote["user_id"], quote["kind"]
        user = self._user(conn, uid)
        if not verify_pin(pin, user["pin_hash"]):
            raise api_error(403, "wrong_pin", state="QUOTED", money_moved=False)  # rolls back

        amount, fee = quote["amount"], quote["fee"]
        total = amount + fee
        target = json.loads(quote["target_json"])
        if kind in BILLER_KINDS and self.faults.fire("biller_down"):
            return "FAILED", failure("biller_down")
        if kind == "bill":
            bill = conn.execute("SELECT status FROM bills WHERE id = ?", (target["bill_id"],)).fetchone()
            if bill is None or bill["status"] == "paid":
                return "FAILED", failure("already_paid")
        limit = user["daily_limit"]
        if limit is not None and self._spent_today(conn, uid) + amount > limit:
            return "FAILED", failure("over_daily_limit")

        # Conditional debit: a balance can never go below zero, whatever runs in parallel.
        debit = conn.execute(
            "UPDATE users SET balance = balance - ? WHERE id = ? AND balance >= ?", (total, uid, total)
        )
        if debit.rowcount != 1:
            return "FAILED", failure("insufficient_balance")
        now = to_iso(self.clock())
        txn_id = new_id("t")
        conn.execute(  # quote_id is UNIQUE: a second debit for this quote cannot be recorded
            "INSERT INTO transactions (id, quote_id, user_id, kind, target_json, amount, fee, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'succeeded', ?)",
            (txn_id, qid, uid, kind, quote["target_json"], amount, fee, now),
        )
        payee = payee_account(kind, target)
        legs = [(f"user:{uid}", -total, "debit"), (payee, amount, "credit")]
        if fee:
            legs.append(("fees", fee, "fee"))
        self._post(conn, qid, legs, now)
        if payee.startswith("user:"):
            conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (amount, target["payee_user_id"]))

        if kind in BILLER_KINDS and self.faults.fire("biller_decline_after_debit"):
            # The biller refused after we took the money: give all of it back.
            conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (total, uid))
            self._post(conn, qid, [(account, -delta, "refund") for account, delta, _ in legs], now)
            conn.execute("UPDATE transactions SET status = 'reversed' WHERE id = ?", (txn_id,))
            return "FAILED", failure("biller_declined", money_returned=True)
        if kind == "bill":
            conn.execute("UPDATE bills SET status = 'paid' WHERE id = ? AND status = 'unpaid'", (target["bill_id"],))

        balance_after = conn.execute("SELECT balance FROM users WHERE id = ?", (uid,)).fetchone()["balance"]
        return "SUCCEEDED", {
            "ok": True,
            "reason": None,
            "money_moved": True,
            "money_returned": False,
            "transaction_id": txn_id,
            "amount": amount,
            "fee": fee,
            "total": total,
            "balance_after": balance_after,
            "completed_at": now,
        }

    # ------------------------------------------------------------ audit

    def audit(self) -> dict:
        """Invariants computed from the tables: <=1 debit per quote, no negative balance, money conserved."""
        with self.db.connect() as conn:
            double_debits = conn.execute(
                "SELECT quote_id, COUNT(*) AS n FROM ledger WHERE entry = 'debit' GROUP BY quote_id HAVING n > 1"
            ).fetchall()
            double_txns = conn.execute(
                "SELECT quote_id, COUNT(*) AS n FROM transactions WHERE quote_id IS NOT NULL"
                " GROUP BY quote_id HAVING n > 1"
            ).fetchall()
            negative = conn.execute("SELECT id, balance FROM users WHERE balance < 0").fetchall()
            ledger_sum = conn.execute("SELECT COALESCE(SUM(delta), 0) AS s FROM ledger").fetchone()["s"]
            drift = conn.execute(
                "SELECT u.id, u.balance, COALESCE(SUM(l.delta), 0) AS ledger_balance"
                " FROM users u LEFT JOIN ledger l ON l.account = 'user:' || u.id"
                " GROUP BY u.id HAVING u.balance != ledger_balance"
            ).fetchall()
            quote_money = conn.execute(
                "SELECT q.id, q.state, q.amount + q.fee AS total, COALESCE(SUM(l.delta), 0) AS net,"
                " (SELECT COUNT(*) FROM transactions t WHERE t.quote_id = q.id AND t.status = 'succeeded') AS done"
                " FROM quotes q LEFT JOIN ledger l ON l.quote_id = q.id AND l.account = 'user:' || q.user_id"
                " GROUP BY q.id"
            ).fetchall()
            by_account = conn.execute("SELECT account, SUM(delta) AS total FROM ledger GROUP BY account").fetchall()

        violations = [f"quote {r['quote_id']} debited {r['n']} times" for r in double_debits]
        violations += [f"quote {r['quote_id']} has {r['n']} transactions" for r in double_txns]
        violations += [f"user {r['id']} balance is negative ({r['balance']})" for r in negative]
        if ledger_sum != 0:
            violations.append(f"ledger does not sum to zero ({ledger_sum})")
        violations += [
            f"user {r['id']} balance {r['balance']} != ledger {r['ledger_balance']}" for r in drift
        ]
        bad_quotes = []
        for r in quote_money:
            expected = (-r["total"], 1) if r["state"] == "SUCCEEDED" else (0, 0)
            if (r["net"], r["done"]) != expected:
                bad_quotes.append(r["id"])
                violations.append(f"quote {r['id']} is {r['state']} but moved {-r['net']} IQD")

        totals: dict[str, int] = {}
        for row in by_account:
            prefix = row["account"].split(":")[0]
            totals[prefix] = totals.get(prefix, 0) + row["total"]
        checks = {
            "one_debit_per_quote": not double_debits and not double_txns,
            "no_negative_balance": not negative,
            "money_conserved": ledger_sum == 0 and not drift,
            "quote_states_match_money": not bad_quotes,
        }
        return {
            "ok": all(checks.values()),
            "checks": checks,
            "violations": violations,
            "totals": {  # opening == users + billers + external + fees when money is conserved
                "opening": -totals.get("opening_funds", 0),
                "users": totals.get("user", 0),
                "billers": totals.get("biller", 0),
                "external": totals.get("external", 0),
                "fees": totals.get("fees", 0),
            },
            "quotes": len(quote_money),
        }

    # ------------------------------------------------------------ helpers

    def _user(self, conn: sqlite3.Connection, uid: str) -> sqlite3.Row:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if user is None:
            raise api_error(404, "unknown_user", user_id=uid)
        return user

    def _quote(self, conn: sqlite3.Connection, qid: str) -> sqlite3.Row:
        quote = conn.execute("SELECT * FROM quotes WHERE id = ?", (qid,)).fetchone()
        if quote is None:
            raise api_error(404, "unknown_quote", quote_id=qid)
        return quote

    def _expired(self, quote: sqlite3.Row) -> bool:
        return self.clock() >= from_iso(quote["expires_at"])

    def _move(self, conn: sqlite3.Connection, qid: str, old: str, new: str, result: dict | None = None) -> None:
        """Compare-and-set a quote state; the result is stored together with the final state."""
        stored = None if result is None else json.dumps(result, ensure_ascii=False)
        changed = conn.execute(
            "UPDATE quotes SET state = ?, result_json = COALESCE(?, result_json) WHERE id = ? AND state = ?",
            (new, stored, qid, old),
        ).rowcount
        if changed != 1:
            raise RuntimeError(f"quote {qid} is no longer {old}")

    def _post(self, conn: sqlite3.Connection, qid: str, legs: list[tuple[str, int, str]], now: str) -> None:
        conn.executemany(
            "INSERT INTO ledger (quote_id, account, delta, entry, created_at) VALUES (?, ?, ?, ?, ?)",
            [(qid, account, delta, entry, now) for account, delta, entry in legs],
        )

    def _spent_today(self, conn: sqlite3.Connection, uid: str) -> int:
        """Sum of successful payments since midnight Baghdad time (fees not counted)."""
        start = to_iso(baghdad_day_start(self.clock()))
        row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS spent FROM transactions"
            " WHERE user_id = ? AND status = 'succeeded' AND created_at >= ?",
            (uid, start),
        ).fetchone()
        return row["spent"]

    def _transfer_target(self, conn: sqlite3.Connection, user: sqlite3.Row, target: dict) -> dict:
        contact = conn.execute(
            "SELECT * FROM contacts WHERE contact_id = ? AND user_id = ?", (target.get("contact_id"), user["id"])
        ).fetchone()
        if contact is None:
            raise api_error(400, "unknown_contact")
        phone = clean_phone(contact["phone"])
        payee_user_id = next(
            (row["id"] for row in conn.execute("SELECT id, phone FROM users WHERE id != ?", (user["id"],))
             if clean_phone(row["phone"]) == phone),
            None,
        )
        return {
            "contact_id": contact["contact_id"],
            "display_name": contact["display_name"],
            "phone": phone,
            "phone_last4": phone[-4:],
            "payee_user_id": payee_user_id,  # set when the payee also has a wallet here
        }

    def _bill_target(
        self, conn: sqlite3.Connection, user: sqlite3.Row, target: dict, amount: int | None
    ) -> tuple[dict, int]:
        """Target {bill_id} or {account_id} (oldest unpaid bill). Amount must equal amount_due."""
        bill = None
        account_id = target.get("account_id")
        if target.get("bill_id"):
            bill = conn.execute("SELECT * FROM bills WHERE id = ?", (target["bill_id"],)).fetchone()
            if bill is None:
                raise api_error(400, "unknown_bill")
            account_id = bill["account_id"]
        account = conn.execute(
            "SELECT a.*, b.kind AS biller_kind, b.name_ar AS biller_name_ar, b.name_en AS biller_name_en"
            " FROM bill_accounts a LEFT JOIN billers b ON b.id = a.biller_id WHERE a.id = ? AND a.user_id = ?",
            (account_id, user["id"]),
        ).fetchone()
        if account is None:
            raise api_error(400, "unknown_account")
        if bill is None:
            bill = conn.execute(
                "SELECT * FROM bills WHERE account_id = ? AND status = 'unpaid' ORDER BY period LIMIT 1",
                (account_id,),
            ).fetchone()
            if bill is None:
                raise api_error(409, "already_paid", account_id=account_id)
        if bill["status"] == "paid":
            raise api_error(409, "already_paid", bill_id=bill["id"], period=bill["period"])
        if amount is None:
            amount = bill["amount_due"]
        elif amount != bill["amount_due"]:
            raise api_error(400, "amount_mismatch", amount_due=bill["amount_due"])
        details = {
            "bill_id": bill["id"],
            "account_id": account["id"],
            "biller_id": account["biller_id"],
            "biller_kind": account["biller_kind"],
            "biller_name_ar": account["biller_name_ar"],
            "account_no": account["account_no"],
            "holder_name": account["holder_name"],
            "label_ar": account["label_ar"],
            "period": bill["period"],
            "amount_due": bill["amount_due"],
        }
        return details, amount

    def _topup_target(self, conn: sqlite3.Connection, user: sqlite3.Row, target: dict) -> dict:
        """Target {biller_id, phone?} (phone defaults to the user's own) or {account_id}."""
        if target.get("account_id"):
            account = conn.execute(
                "SELECT * FROM bill_accounts WHERE id = ? AND user_id = ?", (target["account_id"], user["id"])
            ).fetchone()
            if account is None:
                raise api_error(400, "unknown_account")
            biller_id, phone = account["biller_id"], account["account_no"]
        else:
            biller_id, phone = target.get("biller_id"), target.get("phone") or user["phone"]
        biller = conn.execute("SELECT * FROM billers WHERE id = ?", (biller_id,)).fetchone()
        if biller is None or biller["kind"] != "topup":
            raise api_error(400, "unknown_biller")
        phone = clean_phone(phone)
        if len(phone) < 7:
            raise api_error(400, "invalid_phone")
        return {
            "biller_id": biller["id"],
            "biller_name_ar": biller["name_ar"],
            "biller_name_en": biller["name_en"],
            "phone": phone,
            "phone_last4": phone[-4:],
        }

    @staticmethod
    def _view(quote: sqlite3.Row) -> dict:
        return {
            "id": quote["id"],
            "quote_id": quote["id"],
            "user_id": quote["user_id"],
            "kind": quote["kind"],
            "target": parse_json(quote["target_json"]),
            "amount": quote["amount"],
            "fee": quote["fee"],
            "total": quote["amount"] + quote["fee"],
            "currency": "IQD",
            "created_at": quote["created_at"],
            "expires_at": quote["expires_at"],
            "state": quote["state"],
            "result": parse_json(quote["result_json"]),
        }


def create_app(
    db: str | Path | Database = DEFAULT_DB_PATH,
    *,
    seed: dict | str | Path | None = None,
    clock: Clock | None = None,
    faults: FaultInjector | None = None,
) -> FastAPI:
    """Build the wallet app. `db` is a path, ':memory:' (fresh private DB) or a Database."""
    if isinstance(db, Database):
        if seed is not None:
            raise ValueError("give the seed to Database(...) when passing a Database")
        database = db
    else:
        database = Database(db, seed=seed)
    wallet = Wallet(database, clock=clock, faults=faults)

    api = FastAPI(title="Mock wallet", version="1.0", description="Synthetic wallet for the bill-pay agent.")
    api.state.wallet = wallet
    api.state.faults = wallet.faults

    @api.exception_handler(SimulatedTimeout)
    async def _on_timeout(request: Request, exc: SimulatedTimeout) -> JSONResponse:
        return JSONResponse(status_code=504, content={"detail": {"error": "gateway_timeout"}})

    @api.exception_handler(SimulatedServerError)
    async def _on_server_error(request: Request, exc: SimulatedServerError) -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": {"error": "internal_error"}})

    @api.get("/users/{uid}")
    def get_user(uid: str) -> dict:
        return wallet.user_profile(uid)

    @api.get("/users/{uid}/contacts")
    def get_contacts(uid: str) -> list[dict]:
        return wallet.contacts(uid)

    @api.get("/users/{uid}/bill-accounts")
    def get_bill_accounts(uid: str) -> list[dict]:
        return wallet.bill_accounts(uid)

    @api.get("/users/{uid}/transactions")
    def get_transactions(uid: str, since_minutes: Annotated[int | None, Query(ge=0)] = None) -> list[dict]:
        return wallet.transactions(uid, since_minutes)

    @api.get("/bills")
    def get_bills(account_id: str) -> list[dict]:
        return wallet.bills(account_id)

    @api.get("/billers")
    def get_billers() -> list[dict]:
        return wallet.billers()

    @api.post("/quotes")
    def post_quote(body: QuoteIn) -> dict:
        return wallet.create_quote(body.user_id, body.kind, body.target, body.amount)

    @api.get("/quotes/{qid}")
    def get_quote(qid: str) -> dict:
        return wallet.get_quote(qid)

    @api.post("/quotes/{qid}/execute")
    def post_execute(
        qid: str, body: ExecuteIn, idempotency_key: Annotated[str | None, Header()] = None
    ) -> dict:
        return wallet.execute(qid, body.pin, idempotency_key)

    @api.post("/quotes/{qid}/cancel")
    def post_cancel(qid: str) -> dict:
        return wallet.cancel(qid)

    @api.get("/admin/faults")
    def get_faults() -> dict:
        return wallet.faults.current()

    @api.post("/admin/faults")
    def post_faults(body: FaultIn) -> dict:
        try:
            return wallet.faults.set(body.mode, body.times)
        except ValueError as exc:
            raise api_error(422, "bad_fault", message=str(exc), modes=list(MODES)) from exc

    @api.get("/admin/audit")
    def get_audit() -> dict:
        return wallet.audit()

    return api


app = create_app(DEFAULT_DB_PATH, seed=DEFAULT_SEED_PATH)
