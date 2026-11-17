"""The mock biller feed: bills for real accounts, until the service talks to real billers.

- ensure_catalogue: the eight billers of the pilot (electricity, a neighbourhood generator, two internet
  providers, water, and the three networks for top-ups), with a mock amount range. Only for the real
  accounts database, never a seeded demo one.
- A bill's amount is stable per (biller, account number, month): the same account always shows the same
  bill for the same month, between restarts and on every device. Steps of 250 IQD inside the biller's range.
- issue: the current month's bill when an account is added. ensure_current: the month's bill for every
  account added before this month (on read, once per month; INSERT OR IGNORE on (account, period)).
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime

from wallet.db import BAGHDAD, Database, immediate, new_id, to_iso

CATALOGUE = [
    # id, kind, name_ar, name_en, mock_min, mock_max, account_pattern
    ("electricity", "electricity", "كهرباء الوطنية", "National Electricity", 30_000, 120_000, r"^\d{6,12}$"),
    ("generator", "generator", "مولدة المنطقة", "Neighbourhood Generator", 25_000, 90_000, r"^\d{1,6}$"),
    ("net_rafidain", "internet", "نت الرافدين", "Rafidain Net", 25_000, 60_000, None),
    ("net_sama", "internet", "سما نت", "Sama Net", 25_000, 60_000, None),
    ("water", "water", "ماء المحلة", "Neighbourhood Water", 5_000, 15_000, None),
    ("zain", "topup", "زين", "Zain", None, None, None),
    ("asiacell", "topup", "آسياسيل", "Asiacell", None, None, None),
    ("korek", "topup", "كورك", "Korek", None, None, None),
]
STEP = 250


def ensure_catalogue(db: Database) -> None:
    with db.connect() as conn, immediate(conn):
        for biller_id, kind, name_ar, name_en, low, high, pattern in CATALOGUE:
            conn.execute("INSERT OR IGNORE INTO billers (id, kind, name_ar, name_en, mock_min, mock_max, account_pattern,"
                         " active) VALUES (?, ?, ?, ?, ?, ?, ?, 1)", (biller_id, kind, name_ar, name_en, low, high, pattern))


def period_of(moment: datetime) -> str:
    """'YYYY-MM' of the Baghdad month that contains `moment`."""
    return moment.astimezone(BAGHDAD).strftime("%Y-%m")


def mock_amount(biller: dict, account_no: str, period: str) -> int:
    low = int(biller.get("mock_min") or 10_000)
    high = max(low, int(biller.get("mock_max") or low))
    steps = (high - low) // STEP + 1
    digest = hashlib.sha256(f"{biller['id']}|{account_no}|{period}".encode()).digest()
    return low + int.from_bytes(digest[:8], "big") % steps * STEP


def issue(conn: sqlite3.Connection, account_id: str, biller: dict, account_no: str, period: str,
          now: datetime) -> dict | None:
    """This month's bill for one account (inside the caller's transaction). None for a top-up biller."""
    if biller.get("kind") == "topup":
        return None
    amount = mock_amount(biller, account_no, period)
    conn.execute("INSERT OR IGNORE INTO bills (id, account_id, period, amount_due, status, issued_at, source)"
                 " VALUES (?, ?, ?, ?, 'unpaid', ?, 'mock_feed')", (new_id("b"), account_id, period, amount, to_iso(now)))
    row = conn.execute("SELECT * FROM bills WHERE account_id = ? AND period = ?", (account_id, period)).fetchone()
    return dict(row) if row else None


def month_start(moment: datetime) -> datetime:
    """The first instant of the Baghdad month that contains `moment` (aware)."""
    return moment.astimezone(BAGHDAD).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def ensure_current(db: Database, uid: str, now: datetime) -> int:
    """Issue this month's bill for each of the user's accounts that has none yet. Returns how many.

    A demo account's bill accounts (source 'demo') are seeded with the month they were made in exactly as the
    demo tells it (wallet.api.Wallet.open_demo_account); they get the feed's bills from the next month on."""
    period = period_of(now)
    issued = 0
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT a.id AS account_id, a.account_no, b.id, b.kind, b.mock_min, b.mock_max"
            " FROM bill_accounts a JOIN billers b ON b.id = a.biller_id"
            " WHERE a.user_id = ? AND a.deleted_at IS NULL AND b.kind != 'topup'"
            " AND (a.source = 'manual' OR (a.source = 'demo' AND a.created_at < ?))"
            " AND NOT EXISTS (SELECT 1 FROM bills x WHERE x.account_id = a.id AND x.period = ?)",
            (uid, to_iso(month_start(now)), period)
        ).fetchall()
        if not rows:
            return 0
        with immediate(conn):
            for row in rows:
                if issue(conn, row["account_id"], dict(row), row["account_no"], period, now):
                    issued += 1
    return issued
