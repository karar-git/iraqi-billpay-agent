"""Account services on the shared accounts database: everything about a real account that is not money.

Money (balances, the ledger, holds, admin credits) is moved only by wallet.api.Wallet; this module calls
it for those and never writes a money column itself. Everything else lives here: finding an account by
its WhatsApp number, the language and the 24-hour window, contacts ("the people they know"), saved bill
accounts, the history a user reads, the outbox of notices, PIN-reset requests, the identity check's record
and level (kyc/: never an image, never a whole document number) and the audit log.

Every write that changes what a person owns or sees is one audit_log row (actor 'user:<id>',
'admin:<name>' or 'system'), with no message text and never a PIN.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Callable

from accounts import billing, phones
from kyc import policy as kyc_policy
from payments import names
from wallet.api import Wallet, short_name
from wallet.db import BAGHDAD, Database, from_iso, immediate, new_id, to_iso, utc_now

MAX_CONTACTS = 200
_NOT_DEMO = " AND user_id NOT IN (SELECT id FROM users WHERE source = 'demo')"  # demo payments are pretend
MAX_BILL_ACCOUNTS = 10
WINDOW = timedelta(hours=23, minutes=50)  # WhatsApp's 24 h customer-service window, with a margin
# The identity check: the keys of kyc.privacy.record(), the users columns they are kept in, and the columns of
# kyc_signups (the same record for a number whose sign-up is still open). Nothing else of a document has a column.
KYC_COLUMNS = {"level": "kyc_level", "document_type": "kyc_document_type", "name_on_document": "kyc_name",
               "number_last4": "kyc_number_last4", "number_fingerprint": "kyc_number_fingerprint",
               "birth_year": "kyc_birth_year", "checked_at": "kyc_checked_at", "reason": "kyc_reason"}
KYC_HELD_COLUMNS = {"level": "level", "document_type": "document_type", "name_on_document": "name",
                    "number_last4": "number_last4", "number_fingerprint": "number_fingerprint",
                    "birth_year": "birth_year", "checked_at": "checked_at", "reason": "reason"}
KYC_REVIEW = "pending"  # the level that waits for a person on the admin's requests page
# Reviews a person may only reject: no document was read (nothing to look at; the photo is never kept), the code
# read an age under 18, or the dates came from a card back with no tie to the front (the age rule read a photo that
# may be someone else's). A click never verifies those; another photo that passes the rules does.
KYC_NOT_APPROVABLE = ("too_many_attempts", "under_age", "sides_unconfirmed")
# Reviews whose age reading does not clear an earlier under-18 answer of the same sender: nothing was read, or the
# birth date came from an untied back.
KYC_AGE_UNREAD = ("too_many_attempts", "sides_unconfirmed")
KYC_UNDER_AGE_FREEZE = "kyc_under_age"  # the frozen_reason of an account whose document read under 18
NAME_RE = re.compile(r"^[^\W\d_][^\d_@/:]{0,39}$", re.UNICODE)


def clean_name(text: str) -> str | None:
    """A person's name as typed: 2 to 40 letters (Arabic or Latin, spaces, apostrophe), no digits or links."""
    name = re.sub(r"\s+", " ", (text or "").strip().strip("«»\"'.,،"))
    if len(name) < 2 or len(name) > 40 or "http" in name.lower() or "www." in name.lower():
        return None
    if any(ch.isdigit() for ch in name) or not NAME_RE.match(name):
        return None
    return name


class Accounts:
    def __init__(self, db: Database, wallet: Wallet, clock: Callable[[], datetime] | None = None):
        self.db = db
        self.wallet = wallet
        self.clock = clock or utc_now

    # ------------------------------------------------------------ helpers

    def _now(self) -> str:
        return to_iso(self.clock())

    def log(self, actor: str, action: str, user_id: str | None = None, ref: str | None = None,
            details: dict | None = None, conn: sqlite3.Connection | None = None) -> None:
        if conn is not None:
            self.wallet.log(conn, actor, action, user_id, ref, details)
            return
        with self.db.connect() as c, immediate(c):
            self.wallet.log(c, actor, action, user_id, ref, details)

    def _one(self, sql: str, args: tuple = ()) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute(sql, args).fetchone()
        return dict(row) if row else None

    def _all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    # ------------------------------------------------------------ accounts

    def by_phone(self, e164: str | None) -> dict | None:
        return self._one("SELECT * FROM users WHERE phone_e164 = ?", (e164,)) if e164 else None

    def get(self, uid: str) -> dict | None:
        return self._one("SELECT * FROM users WHERE id = ?", (uid,))

    def touch_inbound(self, uid: str) -> None:
        """A message from the user: their WhatsApp window is open for the next 24 hours."""
        with self.db.connect() as conn, immediate(conn):
            conn.execute("UPDATE users SET last_inbound_at = ? WHERE id = ?", (self._now(), uid))

    def in_window(self, user: dict) -> bool:
        seen = user.get("last_inbound_at")
        return bool(seen) and self.clock() - from_iso(seen) < WINDOW

    def set_language(self, uid: str, lang: str) -> None:
        if lang in ("iq", "msa", "en"):
            with self.db.connect() as conn, immediate(conn):
                conn.execute("UPDATE users SET language = ? WHERE id = ? AND language != ?", (lang, uid, lang))

    def rename_user(self, uid: str, name: str, actor: str) -> None:
        with self.db.connect() as conn, immediate(conn):
            old = conn.execute("SELECT name FROM users WHERE id = ?", (uid,)).fetchone()
            conn.execute("UPDATE users SET name = ? WHERE id = ?", (name, uid))
            self.wallet.log(conn, actor, "name_changed", uid, None, {"from": old["name"] if old else None, "to": name})

    def kyc_match_name(self, uid: str) -> str:
        """The name an identity document of this account is matched against: the name typed at sign-up, or the one
        an admin set since. A rename by the person themselves («غير اسمي») does not move it, so renaming the account
        to a borrowed card's name first cannot make that card match. The current name when there is no rename."""
        current = (self.get(uid) or {}).get("name") or ""
        match = None  # None: follow the current name
        for row in self._all("SELECT actor, action, details_json FROM audit_log WHERE user_id = ?"
                             " AND action IN ('name_changed', 'profile_changed') ORDER BY id", (uid,)):
            details = json.loads(row["details_json"] or "{}")
            change = details if row["action"] == "name_changed" else details.get("name")
            if not isinstance(change, dict) or "from" not in change:
                continue
            if str(row["actor"]).startswith("user:"):
                match = change["from"] if match is None else match
            else:  # an admin's change is the account's name from then on
                match = None
        return match if match is not None else current

    def update_profile(self, uid: str, *, name: str | None = None, note: str | None = None,
                       gender: str | None = None, language: str | None = None, actor: str) -> None:
        fields = {k: v for k, v in (("name", name), ("note", note), ("gender", gender), ("language", language))
                  if v is not None}
        if not fields:
            return
        with self.db.connect() as conn, immediate(conn):
            old = dict(conn.execute("SELECT name, note, gender, language FROM users WHERE id = ?", (uid,)).fetchone())
            conn.execute(f"UPDATE users SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                         (*fields.values(), uid))
            self.wallet.log(conn, actor, "profile_changed", uid, None,
                            {k: {"from": old.get(k), "to": v} for k, v in fields.items()})

    # ------------------------------------------------------------ contacts

    def contacts(self, uid: str) -> list[dict]:
        rows = self._all("SELECT c.*, (SELECT u.id FROM users u WHERE u.phone_e164 = c.phone_e164"
                         " AND u.status != 'closed') AS account_id FROM contacts c"
                         " WHERE c.user_id = ? AND c.deleted_at IS NULL ORDER BY c.display_name, c.contact_id", (uid,))
        return rows

    def add_contact(self, uid: str, name: str, e164: str, *, source: str = "manual",
                    actor: str | None = None) -> tuple[str, dict | None]:
        """('added' | 'same' | 'other_name' | 'self' | 'limit', contact). 'other_name': the number is
        already saved under another name (the contact is the saved one, unchanged)."""
        user = self.get(uid)
        if user and user.get("phone_e164") == e164:
            return "self", None
        with self.db.connect() as conn, immediate(conn):
            saved = conn.execute("SELECT * FROM contacts WHERE user_id = ? AND phone_e164 = ? AND deleted_at IS NULL",
                                 (uid, e164)).fetchone()
            if saved is not None:
                same = names.normalize(saved["display_name"]) == names.normalize(name)
                return ("same" if same else "other_name"), dict(saved)
            count = conn.execute("SELECT COUNT(*) FROM contacts WHERE user_id = ? AND deleted_at IS NULL",
                                 (uid,)).fetchone()[0]
            if count >= MAX_CONTACTS:
                return "limit", None
            cid = new_id("c")
            conn.execute("INSERT INTO contacts (contact_id, user_id, display_name, phone, phone_e164, source, created_at)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?)", (cid, uid, name, phones.local(e164), e164, source, self._now()))
            self.wallet.log(conn, actor or f"user:{uid}", "contact_added", uid, cid, {"source": source})
            row = conn.execute("SELECT * FROM contacts WHERE contact_id = ?", (cid,)).fetchone()
        return "added", dict(row)

    def rename_contact(self, uid: str, contact_id: str, name: str, actor: str | None = None) -> bool:
        with self.db.connect() as conn, immediate(conn):
            done = conn.execute("UPDATE contacts SET display_name = ? WHERE contact_id = ? AND user_id = ?"
                                " AND deleted_at IS NULL", (name, contact_id, uid)).rowcount
            if done:
                self.wallet.log(conn, actor or f"user:{uid}", "contact_renamed", uid, contact_id)
        return bool(done)

    def delete_contact(self, uid: str, contact_id: str, actor: str | None = None) -> bool:
        with self.db.connect() as conn, immediate(conn):
            done = conn.execute("UPDATE contacts SET deleted_at = ? WHERE contact_id = ? AND user_id = ?"
                                " AND deleted_at IS NULL", (self._now(), contact_id, uid)).rowcount
            if done:
                self.wallet.log(conn, actor or f"user:{uid}", "contact_deleted", uid, contact_id)
        return bool(done)

    def set_alias(self, uid: str, contact_id: str, word: str) -> None:
        """«دز لأمي» + «زينب»: the relation word is kept on the contact, so it survives a restart."""
        with self.db.connect() as conn, immediate(conn):
            conn.execute("UPDATE contacts SET alias = NULL WHERE user_id = ? AND alias = ?", (uid, word))
            conn.execute("UPDATE contacts SET alias = ? WHERE contact_id = ? AND user_id = ?", (word, contact_id, uid))
            self.wallet.log(conn, f"user:{uid}", "alias_saved", uid, contact_id)

    def find_contacts(self, uid: str, text: str) -> list[dict]:
        """Saved contacts a typed name or last-4 digits point at (for rename / delete)."""
        contacts = self.contacts(uid)
        digits = re.sub(r"\D", "", names.normalize(text))
        if len(digits) >= 4:
            full = phones.canonical(digits) if len(digits) >= 10 else None  # a whole number: that number only
            if full:
                return [c for c in contacts if (c.get("phone_e164") or phones.canonical(c["phone"])) == full]
            return [c for c in contacts if (c.get("phone_e164") or c["phone"]).endswith(digits)]
        picked = names.confident(names.resolve(text, contacts))
        return [c for c in contacts if c["contact_id"] in {p.contact_id for p in picked}]

    # ------------------------------------------------------------ bill accounts

    def billers(self, kind: str | None = None, active_only: bool = True) -> list[dict]:
        sql = "SELECT * FROM billers WHERE kind != 'topup'"
        args: tuple = ()
        if kind:
            sql, args = sql + " AND kind = ?", (kind,)
        if active_only:
            sql += " AND active = 1"
        return self._all(sql + " ORDER BY id", args)

    def bill_accounts(self, uid: str) -> list[dict]:
        return self._all("SELECT a.*, b.kind AS biller_kind, b.name_ar AS biller_name_ar, b.name_en AS biller_name_en"
                         " FROM bill_accounts a LEFT JOIN billers b ON b.id = a.biller_id"
                         " WHERE a.user_id = ? AND a.deleted_at IS NULL AND b.kind != 'topup' ORDER BY a.created_at, a.id",
                         (uid,))

    def add_bill_account(self, uid: str, biller_id: str, account_no: str, label: str,
                         actor: str | None = None) -> tuple[str, dict | None, dict | None]:
        """('added' | 'exists' | 'bad_number' | 'limit', account, current bill)."""
        biller = self._one("SELECT * FROM billers WHERE id = ?", (biller_id,))
        user = self.get(uid)
        if biller is None or user is None:
            return "bad_number", None, None
        if biller.get("account_pattern") and not re.fullmatch(biller["account_pattern"], account_no):
            return "bad_number", None, None
        if not re.fullmatch(r"[0-9A-Za-z\-]{1,20}", account_no):
            return "bad_number", None, None
        with self.db.connect() as conn, immediate(conn):
            saved = conn.execute("SELECT * FROM bill_accounts WHERE user_id = ? AND biller_id = ? AND account_no = ?"
                                 " AND deleted_at IS NULL", (uid, biller_id, account_no)).fetchone()
            if saved is not None:
                return "exists", dict(saved), None
            count = conn.execute("SELECT COUNT(*) FROM bill_accounts WHERE user_id = ? AND deleted_at IS NULL",
                                 (uid,)).fetchone()[0]
            if count >= MAX_BILL_ACCOUNTS:
                return "limit", None, None
            aid = new_id("a")
            conn.execute("INSERT INTO bill_accounts (id, user_id, biller_id, account_no, holder_name, label_ar,"
                         " created_at, source) VALUES (?, ?, ?, ?, ?, ?, ?, 'manual')",
                         (aid, uid, biller_id, account_no, user["name"], label, self._now()))
            self.wallet.log(conn, actor or f"user:{uid}", "bill_account_added", uid, aid, {"biller": biller_id})
            bill = billing.issue(conn, aid, biller, account_no, billing.period_of(self.clock()), self.clock())
            account = dict(conn.execute("SELECT * FROM bill_accounts WHERE id = ?", (aid,)).fetchone())
        return "added", account, bill

    def delete_bill_account(self, uid: str, account_id: str) -> bool:
        with self.db.connect() as conn, immediate(conn):
            done = conn.execute("UPDATE bill_accounts SET deleted_at = ? WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
                                (self._now(), account_id, uid)).rowcount
            if done:
                self.wallet.log(conn, f"user:{uid}", "bill_account_deleted", uid, account_id)
        return bool(done)

    def ensure_bills(self, uid: str) -> int:
        """The mock biller feed: this month's bill for every saved account made before this month."""
        return billing.ensure_current(self.db, uid, self.clock())

    def bills(self, account_id: str) -> list[dict]:
        return self._all("SELECT * FROM bills WHERE account_id = ? ORDER BY period", (account_id,))

    # ------------------------------------------------------------ history

    def history(self, uid: str, limit: int = 10, since: str | None = None) -> list[dict]:
        """The user's last entries, in and out, newest first: {at, direction, kind, amount, fee, status, label}."""
        rows = self._all(
            "SELECT t.*, u.name AS sender_name, u.phone_e164 AS sender_phone FROM transactions t"
            " LEFT JOIN users u ON u.id = t.user_id"
            " WHERE (t.user_id = ? OR (t.payee_user_id = ? AND t.kind = 'transfer')) AND t.created_at >= ?"
            " ORDER BY t.created_at DESC, t.id DESC LIMIT ?", (uid, uid, since or "", limit))
        out = []
        for r in rows:
            target = json.loads(r["target_json"] or "{}") if r["target_json"] else {}
            incoming = r["kind"] == "transfer" and r["user_id"] != uid
            out.append({
                "id": r["id"], "at": r["created_at"], "kind": r["kind"], "status": r["status"],
                "direction": "in" if incoming or r["kind"] in ("admin_credit", "signup_credit", "hold_refund") else "out",
                "amount": r["amount"], "fee": r["fee"] if not incoming else 0,
                "name": (short_name(r["sender_name"]) if incoming else target.get("display_name")) or "",
                "last4": ((r["sender_phone"] or "")[-4:] if incoming else
                          (target.get("phone_last4") or phones.last4(r.get("counterparty_phone")))),
                "biller_ar": target.get("biller_name_ar") or target.get("label_ar"),
                "biller_en": target.get("biller_name_en"), "period": target.get("period"),
                "reason": r.get("reason"), "hold_id": r.get("hold_id"),
            })
        return out

    def month_totals(self, uid: str) -> dict[str, int]:
        start = self.clock().astimezone(BAGHDAD).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        rows = self._all("SELECT kind, SUM(amount + fee) AS total FROM transactions WHERE user_id = ? AND created_at >= ?"
                         " AND status IN ('succeeded', 'held') AND kind IN ('transfer', 'bill', 'topup') GROUP BY kind",
                         (uid, to_iso(start)))
        return {r["kind"]: int(r["total"] or 0) for r in rows}

    # ------------------------------------------------------------ outbox

    def pending_notices(self, uid: str) -> list[dict]:
        return self._all("SELECT * FROM outbox WHERE user_id = ? AND delivered_at IS NULL ORDER BY id", (uid,))

    def mark_delivered(self, ids: list[int]) -> None:
        if not ids:
            return
        with self.db.connect() as conn, immediate(conn):
            conn.executemany("UPDATE outbox SET delivered_at = ? WHERE id = ? AND delivered_at IS NULL",
                             [(self._now(), i) for i in ids])

    def users_with_notices(self, limit: int = 20) -> list[dict]:
        cutoff = to_iso(self.clock() - WINDOW)
        return self._all("SELECT DISTINCT u.* FROM outbox o JOIN users u ON u.id = o.user_id"
                         " WHERE o.delivered_at IS NULL AND u.last_inbound_at >= ? AND u.phone_e164 IS NOT NULL"
                         " ORDER BY u.last_inbound_at LIMIT ?", (cutoff, limit))

    # ------------------------------------------------------------ identity check (kyc/)

    @staticmethod
    def _kyc_row(record: dict) -> dict:
        """A kyc.privacy.record() as it is written: its eight keys only, a known level, the time as the database
        writes times. Anything else in the dict (there should be nothing) is dropped here."""
        row = {key: record.get(key) for key in KYC_COLUMNS}
        if row["level"] not in kyc_policy.LEVELS:
            raise ValueError("unknown verification level")
        if row["checked_at"]:
            row["checked_at"] = to_iso(from_iso(str(row["checked_at"])))
        return row

    def kyc(self, uid: str) -> dict | None:
        """What is kept of this account's identity check, as it may be shown: the level and its limits, the
        document type, the printed name, the last 4 characters of the number, the birth year, the time and the
        reason code. None for an account from before the feature (no level: its limits are its own)."""
        user = self.get(uid)
        if not user or user.get("kyc_level") is None:
            return None
        return {"level": user["kyc_level"], "document_type": user.get("kyc_document_type"),
                "name": user.get("kyc_name"), "number_last4": user.get("kyc_number_last4"),
                "birth_year": user.get("kyc_birth_year"), "checked_at": user.get("kyc_checked_at"),
                "reason": user.get("kyc_reason"), **kyc_policy.limits_for(user["kyc_level"])}

    def kyc_limits(self, uid: str) -> dict | None:
        """{"daily_limit", "single_max"} of this account's level (kyc.policy), or None when it has no level."""
        level = (self.get(uid) or {}).get("kyc_level")
        return None if level is None else kyc_policy.limits_for(level)

    def kyc_same_document(self, fingerprint: str | None, *, uid: str | None = None,
                          phone: str | None = None) -> list[dict]:
        """Other accounts (not closed) and other open sign-ups that showed the document with this fingerprint:
        [{"user_id" (None for an open sign-up), "phone"}]. `uid` and `phone` are the asker's own, left out."""
        if not fingerprint:
            return []
        own = phone or ((self.get(uid) or {}).get("phone_e164") if uid else None) or ""
        users = self._all("SELECT id, phone_e164 FROM users WHERE kyc_number_fingerprint = ? AND status != 'closed'"
                          " AND id != ?", (fingerprint, uid or ""))
        waiting = self._all("SELECT phone_e164 FROM kyc_signups WHERE number_fingerprint = ? AND phone_e164 != ?",
                            (fingerprint, own))
        return ([{"user_id": u["id"], "phone": u["phone_e164"]} for u in users if not own or u["phone_e164"] != own]
                + [{"user_id": None, "phone": s["phone_e164"]} for s in waiting])

    def kyc_hold(self, e164: str, record: dict) -> None:
        """Sign-up, before the account exists: keep the record of the check for this number until the PIN is set
        (kyc_apply then moves it onto the account) or the sign-up ends (kyc_drop)."""
        row = self._kyc_row(record)
        with self.db.connect() as conn, immediate(conn):
            conn.execute("INSERT OR REPLACE INTO kyc_signups (phone_e164, level, document_type, name, number_last4,"
                         " number_fingerprint, birth_year, checked_at, reason, created_at)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (e164, *[row[key] for key in KYC_HELD_COLUMNS], self._now()))

    def kyc_held(self, e164: str | None) -> dict | None:
        """The record kyc_hold kept for this number, in the shape of kyc.privacy.record(); None when there is none."""
        row = self._one("SELECT * FROM kyc_signups WHERE phone_e164 = ?", (e164,)) if e164 else None
        if row is None:
            return None
        return {key: row[column] for key, column in KYC_HELD_COLUMNS.items()}

    def kyc_drop(self, e164: str | None) -> None:
        if e164:
            with self.db.connect() as conn, immediate(conn):
                conn.execute("DELETE FROM kyc_signups WHERE phone_e164 = ?", (e164,))

    def kyc_sweep(self, max_age: timedelta = timedelta(hours=24)) -> int:
        """Records of sign-ups that never finished are deleted after a day. Returns how many."""
        cutoff = to_iso(self.clock() - max_age)
        with self.db.connect() as conn, immediate(conn):
            return conn.execute("DELETE FROM kyc_signups WHERE created_at < ?", (cutoff,)).rowcount

    def kyc_apply(self, uid: str, record: dict, *, actor: str = "system", drop_phone: str | None = None) -> dict:
        """Write the record of a check onto the account and set its daily limit from kyc.policy, in one
        transaction with the audit line (levels and codes only: no name, no number). `drop_phone` also deletes
        the record held for that number during sign-up. Returns the limits now in force."""
        row = self._kyc_row(record)
        limits = kyc_policy.limits_for(row["level"])
        with self.db.connect() as conn, immediate(conn):
            old = conn.execute("SELECT kyc_level, daily_limit FROM users WHERE id = ?", (uid,)).fetchone()
            if old is None:
                raise KeyError(uid)
            conn.execute(f"UPDATE users SET {', '.join(f'{column} = ?' for column in KYC_COLUMNS.values())},"
                         " daily_limit = ? WHERE id = ?",
                         (*[row[key] for key in KYC_COLUMNS], limits["daily_limit"], uid))
            if drop_phone:
                conn.execute("DELETE FROM kyc_signups WHERE phone_e164 = ?", (drop_phone,))
            self.wallet.log(conn, actor, "kyc_level_set", uid, None,
                            {"from": old["kyc_level"], "to": row["level"], "reason": row["reason"],
                             "document_type": row["document_type"], "daily_limit_from": old["daily_limit"],
                             "daily_limit": limits["daily_limit"], "single_max": limits["single_max"]})
        return limits

    @staticmethod
    def kyc_approvable(user: dict | None) -> bool:
        """A review a person may approve: one where a document was read (its type and number fingerprint are on
        record) and whose reason is not one of KYC_NOT_APPROVABLE."""
        return bool(user and user.get("kyc_level") == KYC_REVIEW and user.get("kyc_document_type")
                    and user.get("kyc_number_fingerprint") and user.get("kyc_reason") not in KYC_NOT_APPROVABLE)

    def kyc_decide(self, uid: str, approve: bool, *, actor: str, reason: str) -> bool:
        """Admin: approve or reject an account whose check waits for a person. Approving makes it verified with
        the limits of that level; rejecting leaves it unverified with the small ones (the person may send another
        document). Either way the person gets a notice and the audit keeps who and why. False when the account
        is not waiting, or when it is approved but kyc_approvable says no (nothing changes). The condition is
        checked again inside the write, so a second tab cannot approve what the first one changed.

        A rejection also drops what the document showed (its printed name, last 4 and birth year): the fingerprint
        stays, so the same document on another account is still spotted. Rejecting an under-18 review confirms it:
        the reason stays "under_age" with the birth year, and a frozen account stays frozen until an admin
        unfreezes it (its notice then says the account is paused, not that the small limits stay)."""
        level = "verified" if approve else "none"
        limits = kyc_policy.limits_for(level)
        with self.db.connect() as conn, immediate(conn):
            row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
            if row is None or row["kyc_level"] != KYC_REVIEW or (approve and not self.kyc_approvable(dict(row))):
                return False
            under_age = not approve and row["kyc_reason"] == "under_age"
            new_reason = "approved" if approve else "under_age" if under_age else "rejected"
            clear = "" if approve else (", kyc_name = NULL, kyc_number_last4 = NULL"
                                        + ("" if under_age else ", kyc_birth_year = NULL"))
            done = conn.execute(f"UPDATE users SET kyc_level = ?, kyc_reason = ?, daily_limit = ?{clear}"
                                " WHERE id = ? AND kyc_level = ?",
                                (level, new_reason, limits["daily_limit"], uid, KYC_REVIEW)).rowcount
            if not done:
                return False
            kind = "kyc_approved" if approve else "kyc_rejected"
            notice = "frozen" if under_age and row["status"] == "frozen" else kind
            # its own ref: the freeze's notice of the same second has the kind "frozen" too and must not swallow it
            ref = f"{kind}:{self._now()}" if notice == "frozen" else self._now()
            self.wallet._outbox(conn, uid, notice, ref, {"reason": reason, **limits})
            self.wallet.log(conn, actor, kind, uid, None, {"reason": reason, "to": level, "kyc_reason": new_reason,
                                                           "daily_limit": limits["daily_limit"],
                                                           "single_max": limits["single_max"]})
        return True

    def kyc_freeze_under_age(self, uid: str, record: dict) -> dict:
        """An account whose document reads under 18: its payments stop at once (the wallet freezes it; money sent to
        it still arrives) and the check goes on the admin's requests page. The record keeps only what the review
        needs: the level, the reason, the document type, the birth year and the fingerprint, never the printed name
        or the last 4. Only an admin's Unfreeze lets the account pay again. Returns the limits of its level."""
        user = self.get(uid)
        if user is None:
            raise KeyError(uid)
        if user["status"] == "active":
            self.wallet.set_status(uid, "frozen", actor="system", reason=KYC_UNDER_AGE_FREEZE)
        return self.kyc_apply(uid, {**record, "level": KYC_REVIEW, "reason": "under_age", "name_on_document": None,
                                    "number_last4": None})

    @staticmethod
    def kyc_under_age_hold(user: dict | None) -> bool:
        """This account's document read under 18 and the account is still frozen: «verify my account» is not opened,
        so a later document cannot replace that answer. An admin's Unfreeze (an explicit decision) lifts it."""
        return bool(user and user.get("kyc_reason") == "under_age" and user.get("status") == "frozen")

    def kyc_note_check(self, *, uid: str | None, ref: str | None, status: str, reason: str, attempt: int,
                       checks: tuple | list = (), where: str = "signup") -> None:
        """One audit line per photo read: the outcome and what was checked, with no personal data (`ref` is the
        channel's keyed name for the sender, never the number). For a refusal this is all that is stored."""
        self.log("system", "kyc_checked", uid, ref,
                 {"status": status, "reason": reason, "attempt": attempt, "where": where,
                  "checks": {str(name): str(outcome) for name, outcome in checks}})

    def kyc_under_age_seen(self, ref: str | None) -> bool:
        """This sender (the channel's keyed name for it) showed a document that reads under 18, and since then no
        document that passed, or that went to a person with an age of 18 or over read on it. Sign-up then offers no
        «Later», and a review that read no age (three bad photos, an untied back) stops the sign-up instead of
        opening an account: an account needs a document again."""
        if not ref:
            return False
        last = self._one("SELECT MAX(id) AS id FROM audit_log WHERE action = 'kyc_checked' AND ref = ?"
                         " AND json_extract(details_json, '$.reason') = 'under_age'", (ref,))
        if not last or last["id"] is None:
            return False
        unread = ", ".join("?" for _ in KYC_AGE_UNREAD)
        later = self._one("SELECT 1 FROM audit_log WHERE action = 'kyc_checked' AND ref = ? AND id > ?"
                          " AND (json_extract(details_json, '$.status') = 'verified'"
                          " OR (json_extract(details_json, '$.status') = 'review'"
                          " AND json_extract(details_json, '$.checks.age') = 'ok'"
                          f" AND json_extract(details_json, '$.reason') NOT IN ({unread})))",
                          (ref, last["id"], *KYC_AGE_UNREAD))
        return later is None

    def kyc_checks_since(self, since: datetime, *, uid: str | None = None, ref: str | None = None) -> int:
        """How many photos this account or this sender had read since `since`."""
        row = self._one("SELECT COUNT(*) AS n FROM audit_log WHERE action = 'kyc_checked' AND at >= ?"
                        " AND ((? != '' AND user_id = ?) OR (? != '' AND ref = ?))",
                        (to_iso(since), uid or "", uid or "", ref or "", ref or ""))
        return int((row or {"n": 0})["n"])

    # ------------------------------------------------------------ PIN-reset requests

    def request_pin_reset(self, uid: str) -> None:
        self.log(f"user:{uid}", "pin_reset_requested", uid)

    def request_funding(self, uid: str) -> None:
        """«اشحن محفظتي»: the person asks the admin to put money in (money gets in only by an admin credit)."""
        self.log(f"user:{uid}", "funding_requested", uid)

    def open_requests(self) -> list[dict]:
        """PIN-reset requests with no reset after them, funding requests with no admin credit after them,
        blocked PINs, and identity checks that wait for a person (kind 'kyc_review')."""
        rows = self._all(
            "SELECT a.user_id, MAX(a.at) AS at FROM audit_log a WHERE a.action = 'pin_reset_requested'"
            " GROUP BY a.user_id")
        out = []
        for r in rows:
            later = self._one("SELECT 1 FROM audit_log WHERE user_id = ? AND action = 'pin_reset' AND at >= ?",
                              (r["user_id"], r["at"]))
            if later is None:
                user = self.get(r["user_id"]) or {}
                out.append({"user_id": r["user_id"], "at": r["at"], "name": user.get("name"),
                            "phone": user.get("phone_e164"), "kind": "pin_reset_requested"})
        for r in self._all("SELECT a.user_id, MAX(a.at) AS at FROM audit_log a WHERE a.action = 'funding_requested'"
                           " GROUP BY a.user_id"):
            later = self._one("SELECT 1 FROM audit_log WHERE user_id = ? AND action = 'admin_credit' AND at >= ?",
                              (r["user_id"], r["at"]))
            if later is None:
                user = self.get(r["user_id"]) or {}
                out.append({"user_id": r["user_id"], "at": r["at"], "name": user.get("name"),
                            "phone": user.get("phone_e164"), "kind": "funding_requested"})
        for user in self._all("SELECT * FROM users WHERE pin_state = 'blocked'"):
            out.append({"user_id": user["id"], "at": None, "name": user["name"], "phone": user["phone_e164"],
                        "kind": "pin_blocked"})
        for user in self._all("SELECT * FROM users WHERE kyc_level = ? AND status != 'closed'"
                              " ORDER BY kyc_checked_at", (KYC_REVIEW,)):
            out.append({"user_id": user["id"], "at": user["kyc_checked_at"], "name": user["name"],
                        "phone": user["phone_e164"], "kind": "kyc_review", "reason": user["kyc_reason"],
                        "document_type": user["kyc_document_type"], "approvable": self.kyc_approvable(user)})
        return out

    # ------------------------------------------------------------ admin reads

    def search_users(self, q: str = "", status: str = "", limit: int = 50, offset: int = 0,
                     source: str = "") -> list[dict]:
        """`source`: "demo" (SIGNUP_MODE=demo accounts only), "real" (every other account), "" (all)."""
        sql, args = "SELECT * FROM users WHERE source != 'seed'", []
        if status:
            sql, args = sql + " AND status = ?", args + [status]
        if source == "demo":
            sql += " AND source = 'demo'"
        elif source == "real":
            sql += " AND source != 'demo'"
        q = (q or "").strip()
        if q:
            e164 = phones.canonical(q)
            digits = re.sub(r"\D", "", q)
            if e164:
                sql, args = sql + " AND phone_e164 = ?", args + [e164]
            elif len(digits) >= 3:
                sql, args = sql + " AND phone_e164 LIKE ?", args + [f"%{digits}%"]
            else:
                sql, args = sql + " AND (id = ? OR name LIKE ?)", args + [q, f"%{q}%"]
        rows = self._all(sql + " ORDER BY created_at DESC, id LIMIT ? OFFSET ?", (*args, limit, offset))
        if q and not phones.canonical(q) and len(re.sub(r"\D", "", q)) < 3 and not rows:
            wanted = names.normalize(q)
            rows = [u for u in self._all("SELECT * FROM users WHERE source != 'seed'")
                    if wanted and wanted in names.normalize(u["name"])
                    and (not source or (u["source"] == "demo") == (source == "demo"))][:limit]
        return rows

    def overview(self) -> dict[str, Any]:
        day = self.clock().astimezone(BAGHDAD).replace(hour=0, minute=0, second=0, microsecond=0)
        week = day - timedelta(days=7)
        stats = self._one(
            "SELECT"
            " (SELECT COUNT(*) FROM users WHERE source != 'seed' AND status = 'active') AS active,"
            " (SELECT COUNT(*) FROM users WHERE source != 'seed' AND status = 'invited') AS invited,"
            " (SELECT COUNT(*) FROM users WHERE source != 'seed' AND status = 'frozen') AS frozen,"
            " (SELECT COUNT(*) FROM users WHERE source != 'seed' AND created_at >= ?) AS signups_today,"
            " (SELECT COUNT(*) FROM users WHERE source != 'seed' AND created_at >= ?) AS signups_week,"
            " (SELECT COUNT(*) FROM holds WHERE state = 'HELD') AS holds_open,"
            " (SELECT COALESCE(SUM(amount), 0) FROM holds WHERE state = 'HELD') AS holds_iqd,"
            " (SELECT COUNT(*) FROM transactions WHERE created_at >= ? AND kind IN ('transfer','bill','topup')"
            f"{_NOT_DEMO}) AS txns_today,"
            " (SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE created_at >= ?"
            "   AND kind IN ('transfer','bill','topup') AND status IN ('succeeded','held')"
            f"{_NOT_DEMO}) AS volume_today,"
            " (SELECT COALESCE(SUM(fee), 0) FROM transactions WHERE created_at >= ? AND status = 'succeeded'"
            f"{_NOT_DEMO}) AS fees_today,"
            " (SELECT COALESCE(SUM(balance), 0) FROM users WHERE source NOT IN ('seed', 'demo')) AS balances,"
            " (SELECT COUNT(*) FROM users WHERE pin_state = 'blocked') AS pins_blocked,"
            " (SELECT COUNT(*) FROM users WHERE source = 'demo') AS demo_accounts,"
            " (SELECT COALESCE(SUM(balance), 0) FROM users WHERE source = 'demo') AS demo_balances",
            (to_iso(day), to_iso(week), to_iso(day), to_iso(day), to_iso(day))) or {}
        stats["requests"] = len(self.open_requests())
        return stats

    def transactions_feed(self, kind: str = "", status: str = "", q: str = "", since: str = "", until: str = "",
                          limit: int = 100, offset: int = 0) -> list[dict]:
        sql = ("SELECT t.*, u.name AS user_name, u.phone_e164 AS user_phone, p.name AS payee_name"
               " FROM transactions t LEFT JOIN users u ON u.id = t.user_id LEFT JOIN users p ON p.id = t.payee_user_id"
               " WHERE 1 = 1")
        args: list[Any] = []
        for column, value in (("t.kind", kind), ("t.status", status)):
            if value:
                sql, args = sql + f" AND {column} = ?", args + [value]
        if since:
            sql, args = sql + " AND t.created_at >= ?", args + [since]
        if until:
            sql, args = sql + " AND t.created_at < ?", args + [until]
        if q:
            sql += " AND (t.id = ? OR t.quote_id = ? OR t.user_id = ? OR t.payee_user_id = ? OR u.phone_e164 LIKE ?)"
            args += [q, q, q, q, f"%{re.sub(r'[^0-9]', '', q) or q}%"]
        return self._all(sql + " ORDER BY t.created_at DESC, t.id DESC LIMIT ? OFFSET ?", (*args, limit, offset))

    def audit_rows(self, action: str = "", actor: str = "", user_id: str = "", limit: int = 200) -> list[dict]:
        sql, args = "SELECT * FROM audit_log WHERE 1 = 1", []
        for column, value in (("action", action), ("actor", actor), ("user_id", user_id)):
            if value:
                sql, args = sql + f" AND {column} = ?", args + [value]
        return self._all(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))

    def admin_ops(self, limit: int = 200) -> list[dict]:
        return self._all("SELECT * FROM admin_ops ORDER BY created_at DESC LIMIT ?", (limit,))

    def ledger(self, since: str = "", until: str = "") -> list[dict]:
        sql, args = "SELECT * FROM ledger WHERE 1 = 1", []
        if since:
            sql, args = sql + " AND created_at >= ?", args + [since]
        if until:
            sql, args = sql + " AND created_at < ?", args + [until]
        return self._all(sql + " ORDER BY id", tuple(args))

    def set_biller(self, biller_id: str, *, active: bool | None = None, mock_min: int | None = None,
                   mock_max: int | None = None, account_pattern: str | None = None, actor: str) -> None:
        fields = {k: v for k, v in (("active", None if active is None else int(active)), ("mock_min", mock_min),
                                     ("mock_max", mock_max), ("account_pattern", account_pattern)) if v is not None}
        if not fields:
            return
        with self.db.connect() as conn, immediate(conn):
            conn.execute(f"UPDATE billers SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                         (*fields.values(), biller_id))
            self.wallet.log(conn, actor, "biller_changed", None, biller_id, fields)

    def add_biller(self, biller_id: str, kind: str, name_ar: str, name_en: str, mock_min: int, mock_max: int,
                   actor: str) -> bool:
        with self.db.connect() as conn, immediate(conn):
            done = conn.execute("INSERT OR IGNORE INTO billers (id, kind, name_ar, name_en, mock_min, mock_max, active)"
                                " VALUES (?, ?, ?, ?, ?, ?, 1)", (biller_id, kind, name_ar, name_en, mock_min, mock_max)
                                ).rowcount
            if done:
                self.wallet.log(conn, actor, "biller_added", None, biller_id, {"kind": kind})
        return bool(done)

    def set_bill(self, bill_id: str, amount: int, actor: str) -> bool:
        """Admin: change an unpaid bill's amount (a paid bill never changes)."""
        if amount <= 0:
            return False
        with self.db.connect() as conn, immediate(conn):
            done = conn.execute("UPDATE bills SET amount_due = ?, source = 'admin' WHERE id = ? AND status = 'unpaid'",
                                (amount, bill_id)).rowcount
            if done:
                self.wallet.log(conn, actor, "bill_changed", None, bill_id, {"amount": amount})
        return bool(done)

    def issue_bill(self, account_id: str, period: str, amount: int, actor: str) -> bool:
        with self.db.connect() as conn, immediate(conn):
            done = conn.execute("INSERT OR IGNORE INTO bills (id, account_id, period, amount_due, status, issued_at, source)"
                                " VALUES (?, ?, ?, ?, 'unpaid', ?, 'admin')",
                                (new_id("b"), account_id, period, amount, self._now())).rowcount
            if done:
                self.wallet.log(conn, actor, "bill_issued", None, account_id, {"period": period, "amount": amount})
        return bool(done)

    def release_number(self, uid: str, actor: str, reason: str) -> bool:
        """Admin: a closed account lets go of its number (the row stays), so the number can sign up again."""
        with self.db.connect() as conn, immediate(conn):
            done = conn.execute("UPDATE users SET phone_e164 = NULL WHERE id = ? AND status = 'closed'", (uid,)).rowcount
            if done:
                self.wallet.log(conn, actor, "number_released", uid, None, {"reason": reason})
        return bool(done)

    def schema_version(self) -> int:
        with self.db.connect() as conn:
            return int(conn.execute("PRAGMA user_version").fetchone()[0])

    def count_users(self) -> int:
        return int((self._one("SELECT COUNT(*) AS n FROM users WHERE source != 'seed'") or {"n": 0})["n"])

    def short_name(self, user: dict) -> str:
        return short_name(user.get("name"))

    def op_id(self) -> str:
        return new_id("op")
