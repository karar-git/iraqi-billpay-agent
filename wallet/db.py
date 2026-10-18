"""SQLite storage for the mock wallet: schema, connections, PIN hashing and time helpers."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    phone       TEXT,
    balance     INTEGER NOT NULL CHECK (balance >= 0),
    daily_limit INTEGER,
    pin_hash    TEXT NOT NULL,
    gender      TEXT,
    pin_failures     INTEGER NOT NULL DEFAULT 0,  -- wrong PINs in a row
    pin_locked_until TEXT                         -- no PIN is checked before this time
);
CREATE TABLE IF NOT EXISTS contacts (
    contact_id   TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    display_name TEXT NOT NULL,
    phone        TEXT NOT NULL,
    alias        TEXT
);
CREATE TABLE IF NOT EXISTS billers (
    id      TEXT PRIMARY KEY,
    kind    TEXT NOT NULL,
    name_ar TEXT NOT NULL,
    name_en TEXT
);
CREATE TABLE IF NOT EXISTS bill_accounts (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    biller_id   TEXT NOT NULL,
    account_no  TEXT NOT NULL,
    holder_name TEXT,
    label_ar    TEXT
);
CREATE TABLE IF NOT EXISTS bills (
    id         TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    period     TEXT NOT NULL,
    amount_due INTEGER NOT NULL CHECK (amount_due > 0),
    status     TEXT NOT NULL DEFAULT 'unpaid' CHECK (status IN ('unpaid', 'paid'))
);
CREATE TABLE IF NOT EXISTS quotes (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('transfer', 'bill', 'topup')),
    target_json TEXT NOT NULL,
    amount      INTEGER NOT NULL CHECK (amount > 0),
    fee         INTEGER NOT NULL CHECK (fee >= 0),
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    state       TEXT NOT NULL CHECK (state IN ('QUOTED', 'EXECUTING', 'SUCCEEDED', 'FAILED',
                                               'UNKNOWN', 'EXPIRED', 'CANCELLED')),
    result_json TEXT
);
CREATE TABLE IF NOT EXISTS transactions (
    id          TEXT PRIMARY KEY,
    quote_id    TEXT UNIQUE,
    user_id     TEXT NOT NULL,
    kind        TEXT NOT NULL,
    target_json TEXT NOT NULL,
    amount      INTEGER NOT NULL,
    fee         INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
-- Double-entry record of every money movement; the audit checks balances against it.
-- Accounts: 'user:<id>', 'biller:<id>', 'external:<phone>', 'fees', 'opening_funds'.
CREATE TABLE IF NOT EXISTS ledger (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id   TEXT,
    account    TEXT NOT NULL,
    delta      INTEGER NOT NULL,
    entry      TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contacts_user ON contacts(user_id);
CREATE INDEX IF NOT EXISTS idx_accounts_user ON bill_accounts(user_id);
CREATE INDEX IF NOT EXISTS idx_bills_account ON bills(account_id);
CREATE INDEX IF NOT EXISTS idx_quotes_user ON quotes(user_id);
CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ledger_quote ON ledger(quote_id);
CREATE INDEX IF NOT EXISTS idx_ledger_account ON ledger(account);
"""

MEMORY = ":memory:"
# Columns added after the first release, so an older wallet file gets them on open.
ADDED_COLUMNS = {
    "users": {
        "pin_failures": "INTEGER NOT NULL DEFAULT 0",
        "pin_locked_until": "TEXT",
    },
}

# ---------------------------------------------------------------- PINs

PIN_ITERATIONS = 20_000  # synthetic data; enough to show the pattern without slowing tests


def hash_pin(pin: str | int, *, salt: bytes | None = None, iterations: int = PIN_ITERATIONS) -> str:
    """Return 'pbkdf2_sha256$<iterations>$<salt hex>$<digest hex>' for a PIN."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", str(pin).encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_pin(pin: str | int, pin_hash: str | None) -> bool:
    """Constant-time check of a PIN against a stored hash."""
    try:
        algorithm, iterations, salt_hex, digest_hex = (pin_hash or "").split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac("sha256", str(pin).encode(), bytes.fromhex(salt_hex), int(iterations))
    except ValueError:
        return False
    return hmac.compare_digest(candidate.hex(), digest_hex)


# ---------------------------------------------------------------- time

BAGHDAD = timezone(timedelta(hours=3))  # Iraq has no daylight saving time


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(moment: datetime) -> str:
    """Store times as sortable UTC strings: 2026-10-18T09:30:00Z."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_iso(text: str) -> datetime:
    moment = datetime.fromisoformat(text)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def baghdad_day_start(moment: datetime) -> datetime:
    """Midnight of the Baghdad calendar day that contains `moment` (used for the daily limit)."""
    local = moment.astimezone(BAGHDAD)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


# ---------------------------------------------------------------- connections


def _open(path: str) -> sqlite3.Connection:
    # isolation_level=None: we issue BEGIN IMMEDIATE / COMMIT ourselves.
    conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if path != MEMORY:
        # WAL: readers never block the writer. NORMAL sync survives a process crash (a power cut
        # may drop the last commits but never leaves a half-done one), plenty for a mock wallet.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def immediate(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One write transaction that takes the database write lock up front."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


class Database:
    """A wallet database: a file path, or ':memory:' for a private throw-away DB.

    Nothing touches the disk until the first connection. The schema is created then, and
    `seed` (a dict or a JSON path) is loaded if the users table is still empty.
    A file DB hands each `connect()` its own connection (reused from a small pool), so threads
    really run in parallel and SQLite's write lock serialises them. A ':memory:' DB shares one
    connection behind a lock.
    """

    def __init__(self, path: str | Path = MEMORY, seed: dict | str | Path | None = None):
        self.path = str(path)
        self.seed = seed
        self._lock = threading.RLock()
        self._shared: sqlite3.Connection | None = None
        self._idle: list[sqlite3.Connection] = []
        self._ready = False

    @property
    def in_memory(self) -> bool:
        return self.path == MEMORY

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self._prepare()
        if self.in_memory:
            with self._lock:
                yield self._shared
            return
        with self._lock:
            conn = self._idle.pop() if self._idle else None
        conn = conn or _open(self.path)
        try:
            yield conn
        finally:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            with self._lock:
                self._idle.append(conn)

    def close(self) -> None:
        """Close all connections. A ':memory:' DB is discarded; a file DB can be reopened."""
        with self._lock:
            conns = self._idle + ([self._shared] if self._shared else [])
            self._idle, self._shared, self._ready = [], None, False
        for conn in conns:
            conn.close()

    def _prepare(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            if self.in_memory:
                self._shared = conn = _open(MEMORY)
            else:
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
                conn = _open(self.path)
                self._idle.append(conn)
            conn.executescript(SCHEMA)
            _add_missing_columns(conn)
            if self.seed is not None:
                from wallet.seed import seed_if_empty  # local import: seed.py imports this module

                seed_if_empty(conn, self.seed)
            self._ready = True


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """Bring a wallet file made by an older version up to the current schema."""
    for table, columns in ADDED_COLUMNS.items():
        have = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, kind in columns.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")


def open_db(path: str | Path = MEMORY, seed: dict | str | Path | None = None) -> Database:
    """Factory: open_db() gives a fresh in-memory DB, open_db('data/wallet.db') a file DB."""
    return Database(path, seed=seed)
