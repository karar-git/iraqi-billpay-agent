"""Load synthetic seed data into a wallet database.

Seed format (dict or JSON file): {users, contacts, billers, bill_accounts, bills, transactions},
each a list of rows whose keys are the column names in wallet/db.py. Users carry a plain synthetic
"pin" that is hashed on load (or a ready "pin_hash"). Unknown keys are ignored.

Command line:  python -m wallet.seed --db data/wallet.db --seed data/wallet_seed.json --reset
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from wallet.db import Database, from_iso, hash_pin, immediate, new_id, to_iso, utc_now

COLUMNS = {
    "users": ("id", "name", "phone", "balance", "daily_limit", "pin_hash", "gender"),
    "contacts": ("contact_id", "user_id", "display_name", "phone", "alias"),
    "billers": ("id", "kind", "name_ar", "name_en"),
    "bill_accounts": ("id", "user_id", "biller_id", "account_no", "holder_name", "label_ar"),
    "bills": ("id", "account_id", "period", "amount_due", "status"),
    "transactions": ("id", "quote_id", "user_id", "kind", "target_json", "amount", "fee", "status", "created_at"),
}


def read_seed(source: dict | str | Path) -> dict | None:
    """Return the seed as a dict; None when `source` is a path that does not exist."""
    if isinstance(source, dict):
        return source
    path = Path(source)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_seed(target: Database | sqlite3.Connection, source: dict | str | Path) -> dict[str, int]:
    """Insert every seed row in one transaction. Returns row counts per table."""
    data = read_seed(source)
    if data is None:
        raise FileNotFoundError(source)
    if isinstance(target, Database):
        with target.connect() as conn, immediate(conn):
            return _insert_all(conn, data)
    with immediate(target):
        return _insert_all(target, data)


def seed_if_empty(conn: sqlite3.Connection, source: dict | str | Path) -> bool:
    """Load the seed only into a DB with no users yet. Returns True when it loaded."""
    data = read_seed(source)
    if data is None:
        return False
    with immediate(conn):
        if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            return False
        _insert_all(conn, data)
    return True


def _insert_all(conn: sqlite3.Connection, data: dict) -> dict[str, int]:
    counts = {}
    for table, columns in COLUMNS.items():
        rows = [_clean(table, raw) for raw in data.get(table) or []]
        for row in rows:
            names = [c for c in columns if c in row]
            conn.execute(
                f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
                [row[c] for c in names],
            )
        counts[table] = len(rows)
    # Opening balances enter the double-entry ledger, so the audit can prove conservation later.
    now = to_iso(utc_now())
    for user in data.get("users") or []:
        balance = int(user["balance"])
        conn.execute(
            "INSERT INTO ledger (quote_id, account, delta, entry, created_at) VALUES (NULL, ?, ?, 'opening', ?)",
            (f"user:{user['id']}", balance, now),
        )
        conn.execute(
            "INSERT INTO ledger (quote_id, account, delta, entry, created_at)"
            " VALUES (NULL, 'opening_funds', ?, 'opening', ?)",
            (-balance, now),
        )
    return counts


def _clean(table: str, raw: dict) -> dict:
    """Fill defaults and convert seed values to what the schema stores."""
    row = dict(raw)
    if table == "users" and "pin_hash" not in row:
        row["pin_hash"] = hash_pin(str(row["pin"]))
    if table == "bills":
        row.setdefault("status", "unpaid")
    if table == "transactions":
        target = row.get("target_json", row.get("target", {}))
        row["target_json"] = target if isinstance(target, str) else json.dumps(target, ensure_ascii=False)
        row.setdefault("id", new_id("t"))
        row.setdefault("fee", 0)
        row.setdefault("status", "succeeded")
        row["created_at"] = _normalise_time(row.get("created_at"))
    return row


def _normalise_time(value: str | None) -> str:
    if not value:
        return to_iso(utc_now())
    try:
        return to_iso(from_iso(value))
    except ValueError:
        return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a wallet SQLite file from a seed JSON file.")
    parser.add_argument("--db", default="data/wallet.db")
    parser.add_argument("--seed", default="data/wallet_seed.json")
    parser.add_argument("--reset", action="store_true", help="delete the DB file first")
    args = parser.parse_args()
    if args.reset:
        Path(args.db).unlink(missing_ok=True)
    counts = load_seed(Database(args.db), args.seed)
    print(f"seeded {args.db}: " + ", ".join(f"{k}={v}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
