"""Mock wallet HTTP API (SPEC.md "Wallet API"). Deterministic code that touches the money.

Run:  uvicorn wallet.api:app          (file DB from $WALLET_DB_PATH or data/wallet.db,
                                        seeded from data/wallet_seed.json on first use)
Test: TestClient(create_app(":memory:", seed=my_seed_dict))
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Callable, Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from accounts import voiceprint
from wallet.db import BAGHDAD, Database, baghdad_day_start, from_iso, immediate, new_id, to_iso, utc_now, verify_pin

PAYMENT_KINDS = ("transfer", "bill", "topup")
SPENT_STATUSES = ("succeeded", "held")  # money that left the sender today (a held transfer too)
from wallet.faults import MODES, FaultInjector, SimulatedServerError, SimulatedTimeout

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = os.environ.get("WALLET_DB_PATH") or str(ROOT / "data" / "wallet.db")
DEFAULT_SEED_PATH = os.environ.get("WALLET_SEED_PATH") or str(ROOT / "data" / "wallet_seed.json")

QUOTE_TTL = timedelta(minutes=5)
PIN_MAX_FAILURES = 5  # wrong PINs in a row before the PIN locks
PIN_LOCK = timedelta(minutes=15)
PIN_BLOCK_AFTER_LOCKOUTS = 3  # the 3rd lockout within 24 hours blocks the PIN until an admin resets it
# Voice approval: a payee is "known" to a voice once paid before, or saved this long ago (or made by the system).
VOICE_PAYEE_AGE = timedelta(hours=24)
SYSTEM_SOURCES = ("seed", "demo", "admin")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


HOLD_DAYS = _env_int("HOLD_DAYS", 7)  # money for a number with no account waits this long, then comes back
MAX_OPEN_HOLDS = _env_int("MAX_OPEN_HOLDS", 5)  # per sender
HOLD_CAP_IQD = _env_int("HOLD_CAP_IQD", 1_000_000)  # held for one number at a time
PIN_RESET_COOLDOWN_IQD = _env_int("PIN_RESET_COOLDOWN_IQD", 100_000)  # out in the 24 h after a PIN reset
PIN_RESET_COOLDOWN = timedelta(hours=24)
FEES = {"transfer": 250, "bill": 0, "topup": 0}  # synthetic, IQD
BILLER_KINDS = ("bill", "topup")
EASTERN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

# A demo account (SIGNUP_MODE=demo): pretend money and a short past, made in code in one transaction
# (Wallet.open_demo_account). Its opening money comes from the ledger account 'demo_funds', never 'cash', so the
# admin's "cash in" is not inflated by visitors. Every transfer it makes (to its two people or to any number) is paid
# to 'external:<number>' (demo_payee): never a hold, never a real account that happens to own that number.
DEMO_FUNDS = "demo_funds"
DEMO_PLAN: dict[str, Any] = {
    "balance": 425_000,  # what the account shows once its past is in
    "daily_limit": 500_000,
    "contacts": [  # display name, alias, amount sent, days ago, time of day in Baghdad
        {"name": "حسين علي", "name_en": "Hussein Ali", "alias": "أخوي", "sent": 50_000, "days_ago": 3, "at": (18, 30)},
        {"name": "زينب كاظم", "name_en": "Zainab Kadhim", "alias": None, "sent": 25_000, "days_ago": 6,
         "at": (13, 15)},
    ],
    "bills_due": [("electricity", 10), ("generator", 3)],  # (biller, digits of the account number): this month, unpaid
    "bills_paid": [("net_rafidain", 8, 35_000)],  # (biller, digits, amount): last month's, paid
}
DEMO_PREFIXES = ("0770", "0771", "0772", "0780", "0781", "0790", "0750", "0751")

Clock = Callable[[], datetime]  # must return an aware datetime


class QuoteIn(BaseModel):
    user_id: str
    kind: Literal["transfer", "bill", "topup"]
    target: dict[str, Any] = Field(default_factory=dict)
    amount: int | None = None


class ExecuteIn(BaseModel):
    pin: str | int | None = None
    # "voice": no PIN, accepted only on the grant the channel recorded in-process (Wallet.grant_voice is no route)
    factor: Literal["pin", "voice"] = "pin"


class FaultIn(BaseModel):
    mode: str
    times: int | None = None


def api_error(status: int, code: str, **extra: Any) -> HTTPException:
    """Error body: {"detail": {"error": <code>, ...}}."""
    return HTTPException(status_code=status, detail={"error": code, **extra})


def failure(reason: str, *, money_returned: bool = False, **extra: Any) -> dict:
    """Stored result of a quote that ended without moving money."""
    return {"ok": False, "reason": reason, "money_moved": False, "money_returned": money_returned, **extra}


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
        if target.get("hold_id"):
            return f"hold:{target['hold_id']}"
        return f"external:{target['phone']}"
    return f"biller:{target['biller_id']}"


def _demo_only(target: dict) -> dict:
    """A demo account's transfer target with every route to a real account or a hold taken out."""
    target = {k: v for k, v in target.items() if k not in ("hold", "hold_id", "hold_days", "registered_name")}
    target["payee_user_id"] = None
    target["demo_payee"] = True
    return target


def canonical_phone(raw: Any) -> str | None:
    """'9647XXXXXXXXX' for an Iraqi mobile in any common shape, else None."""
    digits = clean_phone(raw)
    if digits.startswith("00964"):
        digits = "0" + digits[5:]
    if len(digits) == 10 and digits.startswith("7"):
        digits = "0" + digits
    if len(digits) == 11 and digits.startswith("07") and digits[2] in "3456789":
        return "964" + digits[1:]
    return None


def _display_phone(phone: str | None) -> str:
    """'9647701234567' or '07701234567' -> '0770 123 4567'."""
    local = clean_phone(phone)
    return f"{local[:4]} {local[4:7]} {local[7:]}" if len(local) == 11 else (phone or "")


def short_name(name: str | None) -> str:
    """«سارة كريم» -> «سارة ك.»: enough to confirm a payee, little to harvest by trying numbers."""
    words = (name or "").split()
    if len(words) < 2:
        return words[0] if words else ""
    return f"{words[0]} {words[1][0]}."


def _mock_amount(biller: dict, account_no: str, period: str) -> int:
    """The mock feed's bill amount (accounts.billing.mock_amount, kept here so the wallet imports no accounts code)."""
    low = int(biller.get("mock_min") or 10_000)
    high = max(low, int(biller.get("mock_max") or low))
    steps = (high - low) // 250 + 1
    digest = hashlib.sha256(f"{biller['id']}|{account_no}|{period}".encode()).digest()
    return low + int.from_bytes(digest[:8], "big") % steps * 250


def _last4(e164: str | None) -> str:
    """The end of an account's number, or "" for an account that has no number (a username account's key)."""
    return e164[-4:] if e164 and e164.isdigit() else ""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


class Wallet:
    """Every wallet operation. Each write is a single BEGIN IMMEDIATE transaction."""

    def __init__(self, db: Database, clock: Clock | None = None, faults: FaultInjector | None = None):
        self.db = db
        self.clock = clock or utc_now
        self.faults = faults or FaultInjector()
        # Voice approval (grant_voice, execute(factor="voice")): off until the channel that holds the speaker model
        # switches it on (configure_voice). Off, no grant is made and no voice factor is accepted.
        self.voice_enabled = False
        self.voice_threshold = voiceprint.DEFAULT_THRESHOLD
        self.voice_max_iqd = voiceprint.DEFAULT_MAX_IQD

    # ------------------------------------------------------------ reads

    def user_profile(self, uid: str) -> dict:
        with self.db.connect() as conn:
            user = self._user(conn, uid)
            spent = self._spent_today(conn, uid)
            holds = conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total FROM holds"
                                 " WHERE sender_id = ? AND state = 'HELD'", (uid,)).fetchone()
        limit = user["daily_limit"]
        return {
            "id": user["id"],
            "name": user["name"],
            "phone": user["phone"],
            "phone_e164": user["phone_e164"],
            "gender": user["gender"],
            "balance": user["balance"],
            "daily_limit": limit,
            "spent_today": spent,
            "daily_remaining": None if limit is None else max(0, limit - spent),
            "status": user["status"],
            "pin_state": user["pin_state"],
            "language": user["language"],
            "source": user["source"],
            "pin_reset_at": user["pin_reset_at"],
            "created_at": user["created_at"],
            "open_holds": holds["n"],
            "held_iqd": holds["total"],
        }

    def contacts(self, uid: str) -> list[dict]:
        with self.db.connect() as conn:
            self._user(conn, uid)
            rows = conn.execute("SELECT * FROM contacts WHERE user_id = ? AND deleted_at IS NULL"
                                " ORDER BY display_name, contact_id", (uid,))
            return [dict(row) for row in rows]

    def bill_accounts(self, uid: str) -> list[dict]:
        with self.db.connect() as conn:
            self._user(conn, uid)
            rows = conn.execute(
                "SELECT a.*, b.kind AS biller_kind, b.name_ar AS biller_name_ar, b.name_en AS biller_name_en"
                " FROM bill_accounts a LEFT JOIN billers b ON b.id = a.biller_id"
                " WHERE a.user_id = ? AND a.deleted_at IS NULL ORDER BY a.id",
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
            done = conn.execute(  # the user's own payments (incoming money and admin entries are history only)
                "SELECT * FROM transactions WHERE user_id = ? AND created_at >= ? AND kind IN (?, ?, ?)",
                (uid, since, *PAYMENT_KINDS),
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
            self._may_pay(user)
            if kind == "transfer":
                details = self._transfer_target(conn, user, target, amount)
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

    def execute(self, qid: str, pin: str | int | None = None, idempotency_key: str | None = None, *,
                factor: str = "pin") -> dict:
        """Run a quote at most once. Any later call returns the stored result, never a second debit.

        A wrong PIN changes nothing (403 wrong_pin) but is counted; after PIN_MAX_FAILURES in a row
        the PIN locks for PIN_LOCK (423 pin_locked) and a right PIN resets the count. PINs typed
        with Eastern Arabic digits are read as Western digits.

        factor="voice" runs it with no PIN only on the one-time grant grant_voice() recorded for this very quote, and
        only while every rule of that grant still holds (_voice_ok); anything else is 403 voice_not_approved, nothing
        moved, nothing counted, and the PIN can still be used on the same quote. No other factor exists.
        """
        if idempotency_key is not None and idempotency_key != qid:
            raise api_error(400, "idempotency_key_mismatch", quote_id=qid)
        if factor not in ("pin", "voice"):
            raise api_error(400, "bad_factor")
        if factor == "pin" and (pin is None or str(pin).strip() == ""):
            raise api_error(400, "pin_required", state="QUOTED", money_moved=False)
        if self.faults.fire("error_500"):
            raise SimulatedServerError(qid)
        with self.db.connect() as conn:
            try:
                with immediate(conn):
                    quote = self._quote(conn, qid)
                    if quote["state"] != "QUOTED":
                        return self._view(quote)  # replay: stored result
                    self._move(conn, qid, "QUOTED", "EXECUTING")
                    state, result = self._run(conn, quote, "" if pin is None else str(pin), factor)
                    self._move(conn, qid, "EXECUTING", state, result)
                    if self.faults.fire("timeout_before_commit"):
                        raise SimulatedTimeout("timeout_before_commit", committed=False)  # rolls back
            except HTTPException as exc:
                if isinstance(exc.detail, dict) and exc.detail.get("error") == "wrong_pin":
                    if self._count_wrong_pin(conn, quote["user_id"]):  # after the rollback, on its own
                        blocked = self._user(conn, quote["user_id"])["pin_state"] == "blocked"
                        raise api_error(423, "pin_blocked" if blocked else "pin_locked", state="QUOTED",
                                        money_moved=False) from exc
                raise
            view = self._view(self._quote(conn, qid))
        if self.faults.fire("drop_after_commit"):
            raise SimulatedTimeout("drop_after_commit", committed=True)  # committed, answer lost
        return view

    def _run(self, conn: sqlite3.Connection, quote: sqlite3.Row, pin: str, factor: str = "pin") -> tuple[str, dict]:
        """The money part of execute, inside the caller's transaction. Returns (state, result)."""
        if self._expired(quote):
            return "EXPIRED", failure("expired")
        qid, uid, kind = quote["id"], quote["user_id"], quote["kind"]
        user = self._user(conn, uid)
        self._may_pay(user, state="QUOTED")  # frozen / closed / no PIN: refused, rolls back, nothing moved
        voice_score = None
        if factor == "voice":
            voice_score = self._voice_ok(conn, quote, user)  # 403 voice_not_approved otherwise: rolls back
        else:
            if user["pin_locked_until"] and self.clock() < from_iso(user["pin_locked_until"]):
                raise api_error(423, "pin_locked", state="QUOTED", money_moved=False)  # rolls back
            if not verify_pin(pin.translate(EASTERN_DIGITS).strip(), user["pin_hash"]):
                raise api_error(403, "wrong_pin", state="QUOTED", money_moved=False)  # rolls back
            if user["pin_failures"] or user["pin_locked_until"]:
                conn.execute("UPDATE users SET pin_failures = 0, pin_locked_until = NULL WHERE id = ?", (uid,))
        conn.execute("UPDATE quotes SET approved_by = ? WHERE id = ?", (factor, qid))
        if voice_score is not None:
            self.log(conn, f"user:{uid}", "voice_approved", uid, qid,
                     {"kind": kind, "amount": quote["amount"], "score": round(voice_score, 3)})

        amount, fee = quote["amount"], quote["fee"]
        total = amount + fee
        target = json.loads(quote["target_json"])
        if kind in BILLER_KINDS and self.faults.fire("biller_down"):
            return "FAILED", failure("biller_down")
        if kind == "bill":
            bill = conn.execute("SELECT status, amount_due FROM bills WHERE id = ?", (target["bill_id"],)).fetchone()
            if bill is None or bill["status"] == "paid":
                return "FAILED", failure("already_paid")
            if int(bill["amount_due"]) != amount:  # the bill changed after the card was made: never pay the old one
                return "FAILED", failure("bill_changed")
        limit = user["daily_limit"]
        if limit is not None and self._spent_today(conn, uid) + amount > limit:
            return "FAILED", failure("over_daily_limit")
        if kind in ("transfer", "topup") and self._cooldown_left(conn, user) < amount:
            return "FAILED", failure("pin_reset_cooldown")
        settled = False
        if kind == "transfer" and user["source"] == "demo" and target != _demo_only(target):
            target, settled = _demo_only(target), True  # again at execute: a demo payer only pays external:
        if (kind == "transfer" and target.get("phone_e164") and not target.get("payee_user_id")
                and not target.get("demo_payee")):
            target, settled = self._settle_payee(conn, uid, target), True
        if kind == "transfer" and target.get("payee_user_id"):  # closed since the card was made: nothing moves
            payee_row = conn.execute("SELECT status FROM users WHERE id = ?", (target["payee_user_id"],)).fetchone()
            if payee_row is None or payee_row["status"] == "closed":
                return "FAILED", failure("recipient_closed")
        if kind == "transfer" and target.get("hold_id"):
            refused = self._hold_refused(conn, uid, target["phone_e164"], amount)  # again, inside the write lock
            if refused is not None:
                return "FAILED", refused

        # Conditional debit: a balance can never go below zero, whatever runs in parallel.
        debit = conn.execute(
            "UPDATE users SET balance = balance - ? WHERE id = ? AND balance >= ?", (total, uid, total)
        )
        if debit.rowcount != 1:
            return "FAILED", failure("insufficient_balance")
        now = to_iso(self.clock())
        txn_id = new_id("t")
        hold_id = target.get("hold_id") if kind == "transfer" else None
        conn.execute(  # quote_id is UNIQUE: a second debit for this quote cannot be recorded
            "INSERT INTO transactions (id, quote_id, user_id, kind, target_json, amount, fee, status, created_at,"
            " payee_user_id, counterparty_phone, hold_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (txn_id, qid, uid, kind, _json(target), amount, fee, "held" if hold_id else "succeeded", now,
             target.get("payee_user_id") if kind == "transfer" else None,
             target.get("phone_e164") or canonical_phone(target.get("phone")), hold_id),
        )
        payee = payee_account(kind, target)
        legs = [(f"user:{uid}", -total, "debit"), (payee, amount, "credit")]
        if fee:
            legs.append(("fees", fee, "fee"))
        self._post(conn, qid, legs, now)
        if payee.startswith("user:"):
            conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (amount, target["payee_user_id"]))
            if user["source"] != "seed":
                self._outbox(conn, target["payee_user_id"], "transfer_in", txn_id, {
                    "amount": amount, "sender_name": short_name(user["name"]), "sender_last4": _last4(user["phone_e164"])})
        if hold_id:
            conn.execute(
                "INSERT INTO holds (id, quote_id, sender_id, phone_e164, amount, fee, state, created_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'HELD', ?, ?)",
                (hold_id, qid, uid, target["phone_e164"], amount, fee, now,
                 to_iso(self.clock() + timedelta(days=HOLD_DAYS))))
        if kind == "transfer" and target.get("contact_id"):
            conn.execute("UPDATE contacts SET last_paid_at = ? WHERE contact_id = ?", (now, target["contact_id"]))
        if settled:  # the payee was settled at execute time: the quote says who really got it
            conn.execute("UPDATE quotes SET target_json = ? WHERE id = ?", (_json(target), qid))
        if user["source"] != "seed":
            self.log(conn, f"user:{uid}", "executed", uid, qid,
                     {"kind": kind, "amount": amount, "held": bool(hold_id), "factor": factor})

        if kind in BILLER_KINDS and self.faults.fire("biller_decline_after_debit"):
            # The biller refused after we took the money: give all of it back.
            conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (total, uid))
            self._post(conn, qid, [(account, -delta, "refund") for account, delta, _ in legs], now)
            conn.execute("UPDATE transactions SET status = 'reversed' WHERE id = ?", (txn_id,))
            return "FAILED", failure("biller_declined", money_returned=True)
        if kind == "bill":
            marked = conn.execute(
                "UPDATE bills SET status = 'paid', paid_txn_id = ? WHERE id = ? AND status = 'unpaid' AND amount_due = ?",
                (txn_id, target["bill_id"], amount)).rowcount
            if marked != 1:  # cannot happen inside this transaction (checked above); never pay a bill left open
                raise RuntimeError("bill changed during execute")

        balance_after = conn.execute("SELECT balance FROM users WHERE id = ?", (uid,)).fetchone()["balance"]
        return "SUCCEEDED", {
            "ok": True,
            "held": bool(hold_id),
            "hold_id": hold_id,
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

    def _count_wrong_pin(self, conn: sqlite3.Connection, uid: str) -> bool:
        """Record one more wrong PIN in its own transaction. True when this one locks the PIN.

        Each lockout of a real account is remembered; the PIN_BLOCK_AFTER_LOCKOUTS-th within 24 hours
        blocks the PIN (pin_state 'blocked') until an admin resets it."""
        now = self.clock()
        locked_until = to_iso(now + PIN_LOCK)
        with immediate(conn):
            conn.execute(
                "UPDATE users SET pin_failures = pin_failures + 1,"
                " pin_locked_until = CASE WHEN pin_failures + 1 >= ? THEN ? END WHERE id = ?",
                (PIN_MAX_FAILURES, locked_until, uid),
            )
            row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
            if row["source"] != "seed":
                self.log(conn, f"user:{uid}", "pin_wrong", uid, None, {})
            if row["pin_locked_until"] is None:
                return False
            if row["source"] == "seed":
                return True
            recent = [t for t in json.loads(row["pin_lockouts"] or "[]") if now - from_iso(t) < timedelta(hours=24)]
            recent.append(to_iso(now))
            blocked = len(recent) >= PIN_BLOCK_AFTER_LOCKOUTS
            conn.execute("UPDATE users SET pin_lockouts = ?, pin_failures = 0,"
                         " pin_state = CASE WHEN ? THEN 'blocked' ELSE pin_state END WHERE id = ?",
                         (_json(recent), 1 if blocked else 0, uid))
            self.log(conn, "system", "pin_blocked" if blocked else "pin_locked", uid, None, {"lockouts": len(recent)})
        return True

    # ------------------------------------------------------------ voice approval

    def configure_voice(self, *, enabled: bool, threshold: float = voiceprint.DEFAULT_THRESHOLD,
                        max_iqd: int = voiceprint.DEFAULT_MAX_IQD) -> None:
        """Switched on by the channel that holds the speaker model (VOICE_APPROVAL with a working embedder)."""
        self.voice_enabled = bool(enabled)
        self.voice_threshold = float(threshold)
        self.voice_max_iqd = int(max_iqd)

    def voiceprint_samples(self, uid: str) -> int:
        """How many voice samples the account has (0: no voice kept)."""
        with self.db.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM voiceprints WHERE user_id = ?", (uid,)).fetchone()[0])

    def voice_users(self, uids: list[str]) -> set[str]:
        """Which of these accounts have a voice kept (the admin's badge)."""
        if not uids:
            return set()
        with self.db.connect() as conn:
            marks = ",".join("?" * len(uids))
            rows = conn.execute(f"SELECT DISTINCT user_id FROM voiceprints WHERE user_id IN ({marks})", list(uids))
            return {row["user_id"] for row in rows}

    @staticmethod
    def _voice_samples(conn: sqlite3.Connection, uid: str, model: str) -> list[tuple[float, ...]]:
        rows = conn.execute("SELECT vector FROM voiceprints WHERE user_id = ? AND model = ? ORDER BY created_at",
                            (uid, model)).fetchall()
        return [vector for vector in (voiceprint.unpack(row["vector"]) for row in rows) if vector]

    @staticmethod
    def _voice_payee(kind: str, target: dict) -> str:
        """What a grant is bound to besides the quote: the number, the bill account or the topped-up number."""
        if kind == "transfer":
            return "phone:" + str(target.get("phone_e164") or canonical_phone(target.get("phone")) or "")
        if kind == "bill":
            return "account:" + str(target.get("account_id") or "")
        return "topup:" + clean_phone(target.get("phone"))

    def _voice_payee_known(self, conn: sqlite3.Connection, user: sqlite3.Row, kind: str, target: dict) -> bool:
        """A payee this account already knows, so a voice alone may pay it:
        - a transfer: a number this account has paid before (a transfer that succeeded or is held), or a saved contact
          made by the system (seed, demo, admin) or saved at least VOICE_PAYEE_AGE ago. A contact saved or pointed
          to a new number a moment ago is not: someone holding an unlocked phone could save their own number under
          a trusted name, then play back an old voice note of the owner's;
        - a bill: an account that has a paid bill, or was made by the system, or was saved VOICE_PAYEE_AGE ago;
        - a top-up: the account's own number, or a number topped up before."""
        uid = user["id"]
        old = to_iso(self.clock() - VOICE_PAYEE_AGE)
        if kind == "transfer":
            e164 = target.get("phone_e164") or canonical_phone(target.get("phone"))
            if not e164:
                return False
            if conn.execute("SELECT 1 FROM transactions WHERE user_id = ? AND kind = 'transfer' AND status IN (?, ?)"
                            " AND counterparty_phone = ? LIMIT 1", (uid, *SPENT_STATUSES, e164)).fetchone():
                return True
            contact = conn.execute("SELECT source, created_at FROM contacts WHERE user_id = ? AND phone_e164 = ?"
                                   " AND deleted_at IS NULL ORDER BY created_at LIMIT 1", (uid, e164)).fetchone()
            return contact is not None and (contact["source"] in SYSTEM_SOURCES
                                            or bool(contact["created_at"] and contact["created_at"] <= old))
        if kind == "bill":
            account = conn.execute("SELECT source, created_at FROM bill_accounts WHERE id = ? AND user_id = ?"
                                   " AND deleted_at IS NULL", (target.get("account_id"), uid)).fetchone()
            if account is None:
                return False
            if conn.execute("SELECT 1 FROM bills WHERE account_id = ? AND status = 'paid' AND id != ? LIMIT 1",
                            (target.get("account_id"), target.get("bill_id") or "")).fetchone():
                return True
            return account["source"] in SYSTEM_SOURCES or bool(account["created_at"] and account["created_at"] <= old)
        phone = clean_phone(target.get("phone"))
        if phone and phone == clean_phone(user["phone"]):
            return True
        e164 = canonical_phone(phone)
        return bool(e164 and conn.execute("SELECT 1 FROM transactions WHERE user_id = ? AND kind = 'topup' AND"
                                          " status = 'succeeded' AND counterparty_phone = ? LIMIT 1",
                                          (uid, e164)).fetchone())

    def _voice_refusal(self, conn: sqlite3.Connection, quote: sqlite3.Row, user: sqlite3.Row) -> str | None:
        """Why a voice may not approve this quote (None: it may, if it matches). The same rules at grant and at
        execute: the feature on, the quote open, the account able to pay with no PIN lock, the amount within the
        cap, a known payee."""
        if not self.voice_enabled:
            return "off"
        if quote["state"] not in ("QUOTED", "EXECUTING") or self._expired(quote):
            return "not_open"
        if user["status"] != "active" or user["pin_state"] != "set":
            return "locked"
        if user["pin_locked_until"] and self.clock() < from_iso(user["pin_locked_until"]):
            return "locked"
        if int(quote["amount"]) > self.voice_max_iqd:
            return "over_cap"
        if not self._voice_payee_known(conn, user, quote["kind"], json.loads(quote["target_json"])):
            return "unknown_payee"
        return None

    def grant_voice(self, qid: str, uid: str, print_: voiceprint.Voiceprint | None) -> dict:
        """A voice note asked for this quote: compare its speaker vector with the account's kept voice, here, and
        record a one-time grant bound to this quote (its amount and payee) when it matches and every rule holds.
        Returns {"granted": bool, "reason": ..., "score": float | None}. Never an HTTP route: only the channel that
        heard the note (and checked it was the person's own voice note, not forwarded) calls it."""
        if print_ is None or not print_.vector:
            return {"granted": False, "reason": "no_voiceprint", "score": None}
        with self.db.connect() as conn, immediate(conn):
            quote = conn.execute("SELECT * FROM quotes WHERE id = ?", (qid,)).fetchone()
            if quote is None or quote["user_id"] != uid:
                return {"granted": False, "reason": "not_open", "score": None}
            user = self._user(conn, uid)
            samples = self._voice_samples(conn, uid, print_.model)
            refused = "not_enrolled" if not samples else self._voice_refusal(conn, quote, user)
            if refused is not None:
                return {"granted": False, "reason": refused, "score": None}
            found = voiceprint.score(samples, print_.vector)
            matched = found >= self.voice_threshold
            self.log(conn, f"user:{uid}", "voice_checked", uid, qid,
                     {"score": round(found, 3), "matched": matched, "threshold": self.voice_threshold})
            if not matched:
                return {"granted": False, "reason": "no_match", "score": found}
            target = json.loads(quote["target_json"])
            conn.execute("INSERT OR REPLACE INTO voice_grants (quote_id, user_id, amount, payee, model, score,"
                         " created_at, consumed_at) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                         (qid, uid, int(quote["amount"]), self._voice_payee(quote["kind"], target), print_.model,
                          found, to_iso(self.clock())))
        return {"granted": True, "reason": None, "score": found}

    def _voice_ok(self, conn: sqlite3.Connection, quote: sqlite3.Row, user: sqlite3.Row) -> float:
        """Inside execute: the grant of this quote, unconsumed, for this amount and payee, with the voice still kept
        and every rule still holding. Consumed here (one use, whatever the outcome). Returns its score."""
        qid, uid = quote["id"], quote["user_id"]
        grant = conn.execute("SELECT * FROM voice_grants WHERE quote_id = ?", (qid,)).fetchone()
        target = json.loads(quote["target_json"])
        refused = self._voice_refusal(conn, quote, user)
        if grant is None or grant["user_id"] != uid or grant["consumed_at"] is not None:
            refused = refused or "no_grant"
        elif int(grant["amount"]) != int(quote["amount"]) or grant["payee"] != self._voice_payee(quote["kind"], target):
            refused = refused or "grant_mismatch"
        elif not self._voice_samples(conn, uid, grant["model"]):
            refused = refused or "not_enrolled"  # the voice was deleted after the card was made
        if refused is not None:
            raise api_error(403, "voice_not_approved", reason=refused, state="QUOTED", money_moved=False)
        used = conn.execute("UPDATE voice_grants SET consumed_at = ? WHERE quote_id = ? AND consumed_at IS NULL",
                            (to_iso(self.clock()), qid)).rowcount
        if used != 1:
            raise api_error(403, "voice_not_approved", reason="no_grant", state="QUOTED", money_moved=False)
        return float(grant["score"])

    def enroll_voice(self, uid: str, qid: str, print_: voiceprint.Voiceprint | None) -> dict:
        """Keep the voice of a voice note whose card was then paid with the right PIN: the quote must be this
        account's, SUCCEEDED, and approved by the PIN (that is what proves the speaker is the owner). The first
        voiceprint.MAX_SAMPLES are kept (a later one is not: the kept voice does not drift); the voice is their mean.
        Returns {"added": bool, "first": bool, "samples": int, "reason": ...}."""
        if print_ is None or not print_.vector:
            return {"added": False, "first": False, "samples": 0, "reason": "no_voiceprint"}
        if not self.voice_enabled:
            return {"added": False, "first": False, "samples": 0, "reason": "off"}
        with self.db.connect() as conn, immediate(conn):
            quote = conn.execute("SELECT * FROM quotes WHERE id = ?", (qid,)).fetchone()
            if (quote is None or quote["user_id"] != uid or quote["state"] != "SUCCEEDED"
                    or quote["approved_by"] != "pin"):
                return {"added": False, "first": False, "samples": 0, "reason": "not_pin_paid"}
            conn.execute("DELETE FROM voiceprints WHERE user_id = ? AND model != ?", (uid, print_.model))
            samples = self._voice_samples(conn, uid, print_.model)
            if conn.execute("SELECT 1 FROM voiceprints WHERE quote_id = ?", (qid,)).fetchone():
                return {"added": False, "first": False, "samples": len(samples), "reason": "already"}
            if len(samples) >= voiceprint.MAX_SAMPLES:
                return {"added": False, "first": False, "samples": len(samples), "reason": "full"}
            conn.execute("INSERT INTO voiceprints (id, user_id, quote_id, model, dims, vector, seconds, created_at)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (new_id("vp"), uid, qid, print_.model, len(print_.vector), voiceprint.pack(print_.vector),
                          round(float(print_.seconds), 2), to_iso(self.clock())))
            self.log(conn, f"user:{uid}", "voice_enrolled", uid, qid, {"samples": len(samples) + 1})
        return {"added": True, "first": not samples, "samples": len(samples) + 1, "reason": None}

    def delete_voice(self, uid: str, *, actor: str | None = None, reason: str | None = None) -> int:
        """Delete the account's kept voice (the person's «امسح صوتي», or the admin) and void its open grants.
        Returns how many samples were deleted."""
        with self.db.connect() as conn, immediate(conn):
            self._user(conn, uid)
            gone = conn.execute("DELETE FROM voiceprints WHERE user_id = ?", (uid,)).rowcount
            conn.execute("UPDATE voice_grants SET consumed_at = ? WHERE user_id = ? AND consumed_at IS NULL",
                         (to_iso(self.clock()), uid))
            if gone:
                details = {"samples": gone, **({"reason": reason} if reason else {})}
                self.log(conn, actor or f"user:{uid}", "voice_deleted", uid, None, details)
        return gone

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
                " (SELECT COUNT(*) FROM transactions t WHERE t.quote_id = q.id"
                " AND (t.status IN ('succeeded', 'held') OR t.hold_id IS NOT NULL)) AS done"
                " FROM quotes q LEFT JOIN ledger l ON l.quote_id = q.id AND l.account = 'user:' || q.user_id"
                " GROUP BY q.id"
            ).fetchall()
            by_account = conn.execute("SELECT account, SUM(delta) AS total FROM ledger GROUP BY account").fetchall()
            bad_holds = conn.execute(
                "SELECT h.id, h.state, h.amount, COALESCE(SUM(l.delta), 0) AS held FROM holds h"
                " LEFT JOIN ledger l ON l.account = 'hold:' || h.id GROUP BY h.id"
                " HAVING held != CASE WHEN h.state = 'HELD' THEN h.amount ELSE 0 END").fetchall()
            orphan_ops = conn.execute(
                "SELECT o.op_id FROM admin_ops o LEFT JOIN transactions t ON t.id = o.txn_id WHERE t.id IS NULL"
            ).fetchall()

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

        violations += [f"hold {r['id']} is {r['state']} but holds {r['held']} IQD" for r in bad_holds]
        violations += [f"admin op {r['op_id']} has no transaction" for r in orphan_ops]
        totals: dict[str, int] = {}
        for row in by_account:
            prefix = row["account"].split(":")[0]
            totals[prefix] = totals.get(prefix, 0) + row["total"]
        checks = {
            "one_debit_per_quote": not double_debits and not double_txns,
            "no_negative_balance": not negative,
            "money_conserved": ledger_sum == 0 and not drift,
            "quote_states_match_money": not bad_quotes,
            "holds_match_ledger": not bad_holds and not orphan_ops,
        }
        return {
            "ok": all(checks.values()),
            "checks": checks,
            "violations": violations,
            "totals": {  # opening + cash + demo == users + billers + external + fees + holds when money is conserved
                "opening": -totals.get("opening_funds", 0),
                "users": totals.get("user", 0),
                "billers": totals.get("biller", 0),
                "external": totals.get("external", 0),
                "fees": totals.get("fees", 0),
                "holds": totals.get("hold", 0),
                "cash": -totals.get("cash", 0),
                "demo": -totals.get(DEMO_FUNDS, 0),  # pretend money given to demo accounts (never cash)
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
            " WHERE user_id = ? AND status IN (?, ?) AND kind IN (?, ?, ?) AND created_at >= ?",
            (uid, *SPENT_STATUSES, *PAYMENT_KINDS, start),
        ).fetchone()
        return row["spent"]

    def _cooldown_left(self, conn: sqlite3.Connection, user: sqlite3.Row) -> int:
        """How much more may go out in the 24 hours after a PIN reset (no reset: no limit)."""
        if not user["pin_reset_at"]:
            return 10**15
        since = from_iso(user["pin_reset_at"])
        if self.clock() - since >= PIN_RESET_COOLDOWN:
            return 10**15
        row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS s FROM transactions WHERE user_id = ?"
            " AND kind IN ('transfer', 'topup') AND status IN (?, ?) AND created_at >= ?",
            (user["id"], *SPENT_STATUSES, to_iso(since))).fetchone()
        return max(0, PIN_RESET_COOLDOWN_IQD - row["s"])

    @staticmethod
    def _may_pay(user: sqlite3.Row, state: str | None = None) -> None:
        """Refuse a payment from an account that is not active or has no usable PIN (423, nothing moved)."""
        extra = {"state": state, "money_moved": False} if state else {}
        if user["status"] != "active":
            raise api_error(423, "account_frozen" if user["status"] == "frozen" else "account_inactive",
                            account_status=user["status"], **extra)
        if user["pin_state"] != "set":
            raise api_error(423, "pin_blocked" if user["pin_state"] == "blocked" else "pin_not_set", **extra)

    def _transfer_target(self, conn: sqlite3.Connection, user: sqlite3.Row, target: dict,
                         amount: int | None = None) -> dict:
        """Target {contact_id} (a saved contact) or {phone} (any Iraqi mobile number).

        The payee is the account on that number when there is one. A real account sending to a number
        with no account yet gets a hold (the money waits HOLD_DAYS for that number to sign up)."""
        contact = None
        if target.get("contact_id"):
            contact = conn.execute(
                "SELECT * FROM contacts WHERE contact_id = ? AND user_id = ? AND deleted_at IS NULL",
                (target.get("contact_id"), user["id"])).fetchone()
            if contact is None:
                raise api_error(400, "unknown_contact")
            e164 = contact["phone_e164"] or canonical_phone(contact["phone"])
            phone = clean_phone(contact["phone"])
        elif target.get("phone"):
            e164 = canonical_phone(target["phone"])
            if e164 is None:
                raise api_error(400, "invalid_phone")
            phone = "0" + e164[3:]
            contact = conn.execute(
                "SELECT * FROM contacts WHERE user_id = ? AND phone_e164 = ? AND deleted_at IS NULL"
                " ORDER BY contact_id LIMIT 1", (user["id"], e164)).fetchone()
        else:
            raise api_error(400, "unknown_contact")
        if e164 and e164 == user["phone_e164"]:
            raise api_error(400, "transfer_to_self")
        if (contact is not None and contact["source"] == "demo") or user["source"] == "demo":
            # A demo account's pretend money never reaches a real account or a hold: every transfer it makes (to
            # its made-up people or to any other number) is paid to external:<number>. The flag rides on the
            # quote, and execute re-checks the payer's source (_demo_only), so _settle_payee is skipped too.
            return {"contact_id": contact["contact_id"] if contact else None,
                    "display_name": contact["display_name"] if contact else _display_phone(e164 or phone),
                    "phone": phone, "phone_e164": e164, "phone_last4": phone[-4:], "payee_user_id": None,
                    "demo_payee": True}
        payee = self._account_on(conn, e164, phone, exclude=user["id"])
        if payee is not None and payee["status"] == "closed":
            raise api_error(409, "recipient_closed")
        details = {
            "contact_id": contact["contact_id"] if contact else None,
            "display_name": contact["display_name"] if contact else (short_name(payee["name"]) if payee else
                                                                     _display_phone(e164 or phone)),
            "phone": phone,
            "phone_e164": e164,
            "phone_last4": phone[-4:],
            "payee_user_id": payee["id"] if payee else None,  # set when the payee also has a wallet here
        }
        if payee is not None and user["source"] != "seed":
            details["registered_name"] = short_name(payee["name"])
        if payee is None and user["source"] != "seed":
            if e164 is None:
                raise api_error(400, "invalid_phone")
            refused = self._hold_refused(conn, user["id"], e164, amount or 0)
            if refused is not None:
                raise api_error(409, refused["reason"], **{k: refused[k] for k in ("limit", "cap") if k in refused})
            details["hold"] = True
            details["hold_days"] = HOLD_DAYS
        return details

    @staticmethod
    def _hold_refused(conn: sqlite3.Connection, sender_id: str, e164: str, amount: int) -> dict | None:
        """The hold limits (open holds per sender, money held per number). Checked when the card is made and
        again at execute, inside the write transaction, so two cards open together cannot pass both."""
        open_holds = conn.execute("SELECT COUNT(*) FROM holds WHERE sender_id = ? AND state = 'HELD'",
                                  (sender_id,)).fetchone()[0]
        if open_holds >= MAX_OPEN_HOLDS:
            return failure("hold_limit", limit=MAX_OPEN_HOLDS)
        held = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM holds WHERE phone_e164 = ? AND state = 'HELD'",
                            (e164,)).fetchone()[0]
        if held + amount > HOLD_CAP_IQD:
            return failure("hold_cap", cap=HOLD_CAP_IQD)
        return None

    @staticmethod
    def _account_on(conn: sqlite3.Connection, e164: str | None, phone: str, exclude: str) -> sqlite3.Row | None:
        """The account on a number (canonical first; the seed's old phone format as a fallback). A closed
        account whose number the admin released no longer owns it."""
        if e164:
            row = conn.execute("SELECT * FROM users WHERE phone_e164 = ? AND id != ?", (e164, exclude)).fetchone()
            if row is not None:
                return row
        return next((row for row in conn.execute("SELECT * FROM users WHERE id != ? AND phone_e164 IS NULL"
                                                 " AND source = 'seed'", (exclude,))
                     if clean_phone(row["phone"]) == phone), None)

    def _settle_payee(self, conn: sqlite3.Connection, uid: str, target: dict) -> dict:
        """At execute: the number may have opened an account since the card was made (credit it now),
        else the transfer becomes a hold (a new hold id)."""
        target = dict(target)
        payee = self._account_on(conn, target.get("phone_e164"), target.get("phone") or "", exclude=uid)
        if payee is not None and payee["status"] != "closed":
            target.pop("hold", None)
            target["payee_user_id"] = payee["id"]
            target["registered_name"] = short_name(payee["name"])
        elif target.get("hold"):
            target["hold_id"] = new_id("h")
        return target

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
            "biller_name_en": account["biller_name_en"],
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

    # ------------------------------------------------------------ outbox and audit log

    def _outbox(self, conn: sqlite3.Connection, user_id: str, kind: str, ref: str, payload: dict) -> None:
        """A notice for a user, delivered by the channel inside their WhatsApp window (never a PIN)."""
        conn.execute("INSERT OR IGNORE INTO outbox (user_id, kind, ref, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                     (user_id, kind, ref, _json(payload), to_iso(self.clock())))

    def log(self, conn: sqlite3.Connection, actor: str, action: str, user_id: str | None = None,
            ref: str | None = None, details: dict | None = None) -> None:
        """One audit_log row (decisions and admin actions). Never a PIN and never message text."""
        conn.execute("INSERT INTO audit_log (at, actor, action, user_id, ref, details_json) VALUES (?, ?, ?, ?, ?, ?)",
                     (to_iso(self.clock()), actor, action, user_id, ref, _json(details or {})))

    # ------------------------------------------------------------ accounts (real users)

    def user_by_phone(self, e164: str) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE phone_e164 = ?", (e164,)).fetchone()
        return dict(row) if row else None

    def open_account(self, phone_e164: str, name: str, *, gender: str | None, language: str, pin_hash: str,
                     wa_name: str | None = None, signup_credit: int = 0,
                     daily_limit: int = 1_000_000) -> dict:
        """Open (or, for an invited number, activate) the account on a WhatsApp number, in one transaction.

        Sets the PIN, adds the optional sign-up grant, claims every hold waiting for this number and writes
        the notices and the audit. A second call for the same number changes nothing (one account per number).
        Returns {"user": profile, "claimed": [holds], "created": bool}."""
        now = to_iso(self.clock())
        with self.db.connect() as conn:
            with immediate(conn):
                row = conn.execute("SELECT * FROM users WHERE phone_e164 = ?", (phone_e164,)).fetchone()
                if row is not None and row["status"] != "invited" and row["pin_state"] == "set":
                    return {"user": dict(row), "claimed": [], "created": False}
                if row is None:
                    uid = new_id("w")
                    conn.execute(
                        "INSERT INTO users (id, name, phone, balance, daily_limit, pin_hash, gender, phone_e164, status,"
                        " pin_state, language, source, created_at, activated_at, pin_set_at, wa_profile_name)"
                        " VALUES (?, ?, ?, 0, ?, ?, ?, ?, 'active', 'set', ?, 'whatsapp', ?, ?, ?, ?)",
                        (uid, name, "0" + phone_e164[3:], daily_limit, pin_hash, gender, phone_e164, language,
                         now, now, now, wa_name))
                    self.log(conn, f"user:{uid}", "signup_done", uid, None, {"source": "whatsapp"})
                    created = True
                else:
                    uid = row["id"]
                    conn.execute(
                        "UPDATE users SET pin_hash = ?, pin_state = 'set', pin_set_at = ?, pin_failures = 0,"
                        " pin_locked_until = NULL, status = CASE WHEN status = 'invited' THEN 'active' ELSE status END,"
                        " activated_at = COALESCE(activated_at, ?), gender = COALESCE(?, gender), language = ?,"
                        " wa_profile_name = COALESCE(?, wa_profile_name) WHERE id = ?",
                        (pin_hash, now, now, gender, language, wa_name, uid))
                    self.log(conn, f"user:{uid}", "pin_set" if row["status"] != "invited" else "signup_done", uid)
                    created = False
                if created and signup_credit > 0:
                    self._cash(conn, uid, signup_credit, "signup_credit", "sign-up grant", "system", new_id("op"))
                claimed = self._claim_holds(conn, uid, phone_e164)
        return {"user": self.user_profile(uid), "claimed": claimed, "created": created}

    def open_demo_account(self, account_key: str, name: str, *, language: str, pin_hash: str,
                          gender: str | None = None, wa_name: str | None = None,
                          plan: dict[str, Any] | None = None) -> dict:
        """A demo account (SIGNUP_MODE=demo) with pretend money and a short past, in one transaction.

        `account_key` is the WhatsApp number ('9647…') or, for a sender that shows no number, its account key
        ('wa-user:…', accounts.phones.user_key: never dialable, so nobody can send to it by number). What is made:
        the account (source 'demo', no identity level, its own daily limit); one opening credit from 'demo_funds',
        dated before everything else; two people (source 'demo', one with an alias) and a transfer to each, a few
        days back (booked back to demo_funds, as is the paid bill: a visitor's made-up past never reaches the real
        'fees', 'biller:' or 'external:' totals); this month's unpaid bills; last month's paid bill. Every
        amount comes from `plan` (DEMO_PLAN), never from a model. Each past entry has ledger legs with no quote and
        no 'debit' entry, so audit() stays ok. Idempotent: an account already on the key changes nothing
        (created False). Holds already waiting for a real number are claimed, as at sign-up.
        Returns {"user": profile, "created": bool, "claimed": [holds]}."""
        plan = plan or DEMO_PLAN
        now = self.clock()
        stamp = to_iso(now)
        local = now.astimezone(BAGHDAD)
        dialable = account_key.isdigit()
        with self.db.connect() as conn:
            with immediate(conn):
                row = conn.execute("SELECT * FROM users WHERE phone_e164 = ?", (account_key,)).fetchone()
                if row is not None:
                    return {"user": dict(row), "created": False, "claimed": []}
                uid = new_id("w")
                pick = secrets.SystemRandom()
                own = "0" + account_key[3:] if dialable else None
                people, used = [], {own}
                for person in plan["contacts"]:
                    phone = own
                    while phone in used:
                        phone = pick.choice(DEMO_PREFIXES) + f"{pick.randrange(10 ** 7):07d}"
                    used.add(phone)
                    day = (local - timedelta(days=int(person["days_ago"]))).replace(
                        hour=person["at"][0], minute=person["at"][1], second=0, microsecond=0)
                    people.append((person, phone, day))
                billers = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM billers")}
                first_of_month = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                paid_at = (first_of_month - timedelta(days=6)).replace(hour=11)
                paid = [(b, d, a) for b, d, a in plan["bills_paid"] if b in billers]
                times = [day for _, _, day in people] + ([paid_at] if paid else [])
                opened_at = min(times + [local]) - timedelta(days=2)
                spent = sum(int(p["sent"]) + FEES["transfer"] for p, _, _ in people) + sum(a for _, _, a in paid)
                opening = int(plan["balance"]) + spent
                conn.execute(
                    "INSERT INTO users (id, name, phone, balance, daily_limit, pin_hash, gender, phone_e164, status,"
                    " pin_state, language, source, created_at, activated_at, pin_set_at, wa_profile_name)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 'set', ?, 'demo', ?, ?, ?, ?)",
                    (uid, name, own, int(plan["balance"]), int(plan["daily_limit"]), pin_hash, gender, account_key,
                     language, stamp, stamp, stamp, wa_name))
                self._demo_entry(conn, uid, "signup_credit", {}, opening, 0, opened_at,
                                 [(DEMO_FUNDS, -opening, "demo_opening"), (f"user:{uid}", opening, "demo_opening")],
                                 reason="demo_opening")
                for person, phone, day in people:
                    cid = new_id("c")
                    e164 = "964" + phone[1:]
                    conn.execute("INSERT INTO contacts (contact_id, user_id, display_name, phone, alias, phone_e164,"
                                 " source, created_at, last_paid_at) VALUES (?, ?, ?, ?, ?, ?, 'demo', ?, ?)",
                                 (cid, uid, person["name"], phone, person["alias"], e164, to_iso(opened_at),
                                  to_iso(day)))
                    amount, fee = int(person["sent"]), FEES["transfer"]
                    target = {"contact_id": cid, "display_name": person["name"], "phone": phone, "phone_e164": e164,
                              "phone_last4": phone[-4:], "payee_user_id": None, "demo_payee": True}
                    self._demo_entry(conn, uid, "transfer", target, amount, fee, day,
                                     [(f"user:{uid}", -(amount + fee), "demo_history"),
                                      (DEMO_FUNDS, amount + fee, "demo_history")],  # made-up past: never real fees
                                     counterparty=e164)
                period = local.strftime("%Y-%m")
                for biller_id, digits in plan["bills_due"]:
                    biller = billers.get(biller_id)
                    if biller is None:
                        continue
                    account_id, number = self._demo_bill_account(conn, uid, biller_id, digits, pick, stamp, name)
                    conn.execute("INSERT INTO bills (id, account_id, period, amount_due, status, issued_at, source)"
                                 " VALUES (?, ?, ?, ?, 'unpaid', ?, 'demo')",
                                 (new_id("b"), account_id, period, _mock_amount(biller, number, period),
                                  to_iso(first_of_month)))
                for biller_id, digits, amount in paid:
                    biller = billers[biller_id]
                    account_id, number = self._demo_bill_account(conn, uid, biller_id, digits, pick, stamp, name)
                    bill_id, last = new_id("b"), (first_of_month - timedelta(days=1)).strftime("%Y-%m")
                    target = {"bill_id": bill_id, "account_id": account_id, "biller_id": biller_id,
                              "biller_kind": biller["kind"], "biller_name_ar": biller["name_ar"],
                              "biller_name_en": biller["name_en"], "account_no": number, "holder_name": name,
                              "label_ar": None, "period": last, "amount_due": amount}
                    txn = self._demo_entry(conn, uid, "bill", target, amount, 0, paid_at,
                                           [(f"user:{uid}", -amount, "demo_history"),
                                            (DEMO_FUNDS, amount, "demo_history")])  # nor a real biller's money
                    conn.execute("INSERT INTO bills (id, account_id, period, amount_due, status, issued_at, source,"
                                 " paid_txn_id) VALUES (?, ?, ?, ?, 'paid', ?, 'demo', ?)",
                                 (bill_id, account_id, last, amount, to_iso(paid_at - timedelta(days=5)), txn))
                self.log(conn, f"user:{uid}", "signup_done", uid, None, {"source": "demo"})
                claimed = self._claim_holds(conn, uid, account_key) if dialable else []
        return {"user": self.user_profile(uid), "created": True, "claimed": claimed}

    def _demo_entry(self, conn: sqlite3.Connection, uid: str, kind: str, target: dict, amount: int, fee: int,
                    at: datetime, legs: list[tuple[str, int, str]], *, reason: str | None = None,
                    counterparty: str | None = None) -> str:
        """One past entry of a demo account: its transaction (no quote) and its ledger legs, at `at`."""
        txn_id, when = new_id("t"), to_iso(at)
        conn.execute(
            "INSERT INTO transactions (id, quote_id, user_id, kind, target_json, amount, fee, status, created_at,"
            " counterparty_phone, reason) VALUES (?, NULL, ?, ?, ?, ?, ?, 'succeeded', ?, ?, ?)",
            (txn_id, uid, kind, _json(target), amount, fee, when, counterparty, reason))
        self._post(conn, None, legs, when)
        return txn_id

    @staticmethod
    def _demo_bill_account(conn: sqlite3.Connection, uid: str, biller_id: str, digits: int, pick: Any,
                           stamp: str, holder: str | None = None) -> tuple[str, str]:
        account_id = new_id("a")
        number = str(pick.randrange(10 ** (digits - 1), 10 ** digits))
        conn.execute("INSERT INTO bill_accounts (id, user_id, biller_id, account_no, holder_name, label_ar, created_at,"
                     " source) VALUES (?, ?, ?, ?, ?, NULL, ?, 'demo')", (account_id, uid, biller_id, number, holder or None, stamp))
        return account_id, number

    def demo_story(self, uid: str) -> dict:
        """What a demo account's intro tells, read back from the tables: the balance, the past transfers to its
        people (name, amount, when) and the unpaid bills (biller names, amount). Code facts only."""
        with self.db.connect() as conn:
            user = self._user(conn, uid)
            sent = conn.execute("SELECT target_json, amount, created_at FROM transactions WHERE user_id = ?"
                                " AND kind = 'transfer' AND quote_id IS NULL ORDER BY created_at DESC", (uid,)).fetchall()
            due = conn.execute(
                "SELECT b.amount_due, b.period, r.id AS biller_id, r.name_ar, r.name_en FROM bills b"
                " JOIN bill_accounts a ON a.id = b.account_id JOIN billers r ON r.id = a.biller_id"
                " WHERE a.user_id = ? AND a.deleted_at IS NULL AND b.status = 'unpaid' ORDER BY a.created_at, a.id",
                (uid,)).fetchall()
        transfers = []
        for row in sent:
            target = parse_json(row["target_json"]) or {}
            if isinstance(target, dict) and target.get("demo_payee"):
                transfers.append({"name": target.get("display_name") or "", "amount": row["amount"],
                                  "at": row["created_at"]})
        return {"balance": user["balance"], "transfers": transfers,
                "bills": [{"biller_id": r["biller_id"], "name_ar": r["name_ar"], "name_en": r["name_en"],
                           "amount": r["amount_due"], "period": r["period"]} for r in due]}

    def invite(self, phone_e164: str, name: str, *, language: str = "iq", gender: str | None = None,
               opening: int = 0, daily_limit: int = 1_000_000, note: str | None = None,
               op_id: str | None = None, actor: str = "admin") -> dict:
        """An admin adds a person by number: an invited account, the opening balance, no PIN yet."""
        now = to_iso(self.clock())
        with self.db.connect() as conn:
            with immediate(conn):
                row = conn.execute("SELECT id FROM users WHERE phone_e164 = ?", (phone_e164,)).fetchone()
                if row is not None:
                    return {"user_id": row["id"], "created": False}
                uid = new_id("w")
                conn.execute(
                    "INSERT INTO users (id, name, phone, balance, daily_limit, pin_hash, gender, phone_e164, status,"
                    " pin_state, language, source, created_at, note) VALUES (?, ?, ?, 0, ?, '', ?, ?, 'invited',"
                    " 'unset', ?, 'admin', ?, ?)",
                    (uid, name, "0" + phone_e164[3:], daily_limit, gender, phone_e164, language, now, note))
                self.log(conn, actor, "user_invited", uid, None, {"opening": opening})
                if opening > 0:
                    self._cash(conn, uid, opening, "opening", "opening balance", actor, op_id or new_id("op"))
        return {"user_id": uid, "created": True}

    def set_pin(self, uid: str, pin_hash: str, *, actor: str | None = None, action: str = "pin_set",
                keep_lock: bool = False) -> None:
        """`keep_lock` (a demo account's «نسيت الرمز»): the new PIN replaces the old one, but a running lockout
        and the failure count stay, and a blocked PIN is never unblocked (refused with 423, nothing changed)."""
        now = to_iso(self.clock())
        with self.db.connect() as conn, immediate(conn):
            row = self._user(conn, uid)
            if keep_lock:
                if row["pin_state"] == "blocked" or row["status"] != "active":
                    raise api_error(423, "pin_blocked" if row["pin_state"] == "blocked" else "account_inactive")
                conn.execute("UPDATE users SET pin_hash = ?, pin_set_at = ? WHERE id = ?", (pin_hash, now, uid))
                self.log(conn, actor or f"user:{uid}", action, uid)
                return
            conn.execute("UPDATE users SET pin_hash = ?, pin_state = 'set', pin_set_at = ?, pin_failures = 0,"
                         " pin_locked_until = NULL, status = CASE WHEN status = 'invited' THEN 'active' ELSE status END,"
                         " activated_at = COALESCE(activated_at, ?) WHERE id = ?", (pin_hash, now, now, uid))
            self.log(conn, actor or f"user:{uid}", action, uid)
            if row["status"] == "invited" and row["phone_e164"]:
                self._claim_holds(conn, uid, row["phone_e164"])

    def check_pin(self, uid: str, pin: str) -> str:
        """'ok' | 'wrong' | 'locked' | 'blocked': a PIN typed to change it, counted like a payment PIN."""
        with self.db.connect() as conn:
            user = self._user(conn, uid)
            if user["pin_state"] == "blocked":
                return "blocked"
            if user["pin_locked_until"] and self.clock() < from_iso(user["pin_locked_until"]):
                return "locked"
            if verify_pin(str(pin).translate(EASTERN_DIGITS).strip(), user["pin_hash"]):
                if user["pin_failures"]:
                    with immediate(conn):
                        conn.execute("UPDATE users SET pin_failures = 0 WHERE id = ?", (uid,))
                return "ok"
            if self._count_wrong_pin(conn, uid):
                return "blocked" if self._user(conn, uid)["pin_state"] == "blocked" else "locked"
            return "wrong"

    def reset_pin(self, uid: str, *, actor: str, reason: str) -> None:
        """Admin: clear the PIN (the user sets a new one at the next message); starts the cooldown."""
        now = to_iso(self.clock())
        with self.db.connect() as conn, immediate(conn):
            self._user(conn, uid)
            conn.execute("UPDATE users SET pin_hash = '', pin_state = 'unset', pin_failures = 0,"
                         " pin_locked_until = NULL, pin_lockouts = '[]', pin_reset_at = ? WHERE id = ?", (now, uid))
            self._outbox(conn, uid, "pin_reset", now, {})
            self.log(conn, actor, "pin_reset", uid, None, {"reason": reason})

    def unblock_pin(self, uid: str, *, actor: str, reason: str) -> None:
        with self.db.connect() as conn, immediate(conn):
            self._user(conn, uid)
            conn.execute("UPDATE users SET pin_state = CASE WHEN pin_hash != '' THEN 'set' ELSE 'unset' END,"
                         " pin_failures = 0, pin_locked_until = NULL, pin_lockouts = '[]' WHERE id = ?", (uid,))
            self.log(conn, actor, "pin_unblocked", uid, None, {"reason": reason})

    def set_status(self, uid: str, status: str, *, actor: str, reason: str) -> None:
        """Admin: freeze (payments stop, incoming money still arrives), unfreeze, or close (balance 0 only)."""
        if status not in ("active", "frozen", "closed"):
            raise api_error(400, "bad_status")
        with self.db.connect() as conn, immediate(conn):
            user = self._user(conn, uid)
            if status == "closed":
                holds = conn.execute("SELECT COUNT(*) FROM holds WHERE sender_id = ? AND state = 'HELD'",
                                     (uid,)).fetchone()[0]
                if user["balance"] != 0 or holds:
                    raise api_error(409, "not_empty", balance=user["balance"], holds=holds)
            if user["status"] == "invited" and status == "active":
                raise api_error(409, "still_invited")
            conn.execute("UPDATE users SET status = ?, frozen_reason = ? WHERE id = ?",
                         (status, reason if status == "frozen" else None, uid))
            kind = {"frozen": "frozen", "active": "unfrozen", "closed": "closed"}[status]
            if kind == "closed":  # a closed account keeps no voice
                conn.execute("DELETE FROM voiceprints WHERE user_id = ?", (uid,))
            if kind != "closed":
                self._outbox(conn, uid, kind, to_iso(self.clock()), {"reason": reason})
            self.log(conn, actor, kind, uid, None, {"reason": reason})

    def set_limit(self, uid: str, limit: int, *, actor: str) -> None:
        if not 0 <= limit <= 10_000_000:
            raise api_error(400, "bad_limit")
        with self.db.connect() as conn, immediate(conn):
            old = self._user(conn, uid)["daily_limit"]
            conn.execute("UPDATE users SET daily_limit = ? WHERE id = ?", (limit, uid))
            self.log(conn, actor, "limit_changed", uid, None, {"from": old, "to": limit})

    # ------------------------------------------------------------ admin money (idempotent by op_id)

    def admin_credit(self, uid: str, amount: int, reason: str, *, op_id: str, actor: str,
                     notify: bool = True) -> dict:
        return self._admin_money(uid, amount, reason, op_id, actor, "credit", notify)

    def admin_debit(self, uid: str, amount: int, reason: str, *, op_id: str, actor: str,
                    notify: bool = True) -> dict:
        return self._admin_money(uid, amount, reason, op_id, actor, "debit", notify)

    def _admin_money(self, uid: str, amount: int, reason: str, op_id: str, actor: str, kind: str,
                     notify: bool) -> dict:
        if not isinstance(amount, int) or amount <= 0:
            raise api_error(400, "non_positive_amount")
        reason = (reason or "").strip()
        if not 3 <= len(reason) <= 200:
            raise api_error(400, "reason_required")
        if not op_id:
            raise api_error(400, "op_id_required")
        with self.db.connect() as conn:
            with immediate(conn):
                done = conn.execute("SELECT * FROM admin_ops WHERE op_id = ?", (op_id,)).fetchone()
                if done is not None:  # a resubmitted form: the same answer, nothing moves again
                    return {"op_id": op_id, "txn_id": done["txn_id"], "replayed": True,
                            "balance": self._user(conn, done["user_id"])["balance"]}
                self._user(conn, uid)
                txn_id = self._cash(conn, uid, amount, kind, reason, actor, op_id)
                if notify:
                    self._outbox(conn, uid, f"admin_{kind}", txn_id, {"amount": amount, "reason": reason})
            return {"op_id": op_id, "txn_id": txn_id, "replayed": False, "balance": self._user(conn, uid)["balance"]}

    def _cash(self, conn: sqlite3.Connection, uid: str, amount: int, kind: str, reason: str, actor: str,
              op_id: str) -> str:
        """Money in or out of the service's cash account (inside the caller's transaction)."""
        now = to_iso(self.clock())
        txn_id = new_id("t")
        if kind == "debit":
            moved = conn.execute("UPDATE users SET balance = balance - ? WHERE id = ? AND balance >= ?",
                                 (amount, uid, amount)).rowcount
            if moved != 1:
                raise api_error(409, "insufficient_balance")
            legs = [(f"user:{uid}", -amount, "admin_debit"), ("cash", amount, "admin_debit")]
        else:
            conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (amount, uid))
            legs = [("cash", -amount, kind), (f"user:{uid}", amount, kind)]
        txn_kind = {"credit": "admin_credit", "debit": "admin_debit", "opening": "admin_credit",
                    "signup_credit": "signup_credit"}[kind]
        conn.execute(
            "INSERT INTO transactions (id, quote_id, user_id, kind, target_json, amount, fee, status, created_at, reason)"
            " VALUES (?, NULL, ?, ?, '{}', ?, 0, 'succeeded', ?, ?)", (txn_id, uid, txn_kind, amount, now, reason))
        self._post(conn, None, legs, now)
        conn.execute("INSERT INTO admin_ops (op_id, actor, user_id, kind, amount, reason, txn_id, created_at)"
                     " VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (op_id, actor, uid, kind, amount, reason, txn_id, now))
        self.log(conn, actor, "admin_debit" if kind == "debit" else "admin_credit", uid, txn_id,
                 {"amount": amount, "kind": kind, "reason": reason})
        return txn_id

    # ------------------------------------------------------------ holds

    def _claim_holds(self, conn: sqlite3.Connection, uid: str, phone_e164: str) -> list[dict]:
        """Inside the caller's transaction: every HELD hold for this number goes to the new account."""
        now = to_iso(self.clock())
        claimed = []
        for hold in conn.execute("SELECT * FROM holds WHERE phone_e164 = ? AND state = 'HELD' ORDER BY created_at",
                                 (phone_e164,)).fetchall():
            if conn.execute("UPDATE holds SET state = 'CLAIMED', settled_at = ?, claimed_by = ?, settle_reason = 'signup'"
                            " WHERE id = ? AND state = 'HELD'", (now, uid, hold["id"])).rowcount != 1:
                continue
            conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (hold["amount"], uid))
            self._post(conn, None, [(f"hold:{hold['id']}", -hold["amount"], "hold_claim"),
                                    (f"user:{uid}", hold["amount"], "hold_claim")], now)
            conn.execute("UPDATE transactions SET status = 'succeeded', payee_user_id = ? WHERE hold_id = ?",
                         (uid, hold["id"]))
            sender = conn.execute("SELECT name, phone_e164 FROM users WHERE id = ?", (hold["sender_id"],)).fetchone()
            receiver = conn.execute("SELECT name FROM users WHERE id = ?", (uid,)).fetchone()
            self._outbox(conn, hold["sender_id"], "hold_claimed", hold["id"],
                         {"amount": hold["amount"], "name": short_name(receiver["name"]),
                          "last4": phone_e164[-4:]})
            self.log(conn, "system", "hold_claimed", uid, hold["id"], {"amount": hold["amount"]})
            claimed.append({"id": hold["id"], "amount": hold["amount"], "sender_name": short_name(sender["name"]) if sender else "",
                            "sender_last4": _last4(sender["phone_e164"] if sender else "")})
        return claimed

    def holds(self, *, state: str | None = None, user_id: str | None = None, limit: int = 200) -> list[dict]:
        sql, args = "SELECT * FROM holds WHERE 1 = 1", []
        if state:
            sql, args = sql + " AND state = ?", args + [state]
        if user_id:
            sql, args = sql + " AND sender_id = ?", args + [user_id]
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute(sql + " ORDER BY created_at DESC LIMIT ?", (*args, limit))]

    def refund_hold(self, hold_id: str, *, reason: str, actor: str = "system") -> dict | None:
        """Give a held transfer back to the sender, fee included. Exactly once (CAS on state 'HELD')."""
        now = to_iso(self.clock())
        with self.db.connect() as conn, immediate(conn):
            hold = conn.execute("SELECT * FROM holds WHERE id = ?", (hold_id,)).fetchone()
            if hold is None:
                raise api_error(404, "unknown_hold")
            if conn.execute("UPDATE holds SET state = 'REFUNDED', settled_at = ?, settle_reason = ?"
                            " WHERE id = ? AND state = 'HELD'", (now, reason, hold_id)).rowcount != 1:
                return None
            total = hold["amount"] + hold["fee"]
            conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (total, hold["sender_id"]))
            legs = [(f"hold:{hold_id}", -hold["amount"], "hold_refund"), (f"user:{hold['sender_id']}", total, "hold_refund")]
            if hold["fee"]:
                legs.append(("fees", -hold["fee"], "hold_refund"))
            self._post(conn, None, legs, now)
            txn_id = new_id("t")
            conn.execute(
                "INSERT INTO transactions (id, quote_id, user_id, kind, target_json, amount, fee, status, created_at,"
                " counterparty_phone, hold_id, reason) VALUES (?, NULL, ?, 'hold_refund', '{}', ?, 0, 'succeeded', ?, ?, ?, ?)",
                (txn_id, hold["sender_id"], total, now, hold["phone_e164"], hold_id, reason))
            conn.execute("UPDATE transactions SET status = 'reversed' WHERE hold_id = ? AND kind = 'transfer'", (hold_id,))
            self._outbox(conn, hold["sender_id"], "hold_refunded", hold_id,
                         {"amount": total, "last4": hold["phone_e164"][-4:], "reason": reason})
            self.log(conn, actor, "hold_refunded", hold["sender_id"], hold_id, {"amount": total, "reason": reason})
        return {"hold_id": hold_id, "refunded": total}

    def expire_holds(self) -> int:
        """Refund every HELD hold past its expiry. Returns how many were refunded."""
        now = to_iso(self.clock())
        with self.db.connect() as conn:
            due = [r["id"] for r in conn.execute("SELECT id FROM holds WHERE state = 'HELD' AND expires_at <= ?", (now,))]
        return sum(1 for hold_id in due if self.refund_hold(hold_id, reason="expired"))

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
        return wallet.execute(qid, body.pin, idempotency_key, factor=body.factor)

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
