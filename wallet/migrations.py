"""Ordered schema migrations of the wallet database (PRAGMA user_version).

v1 is the original wallet schema (wallet/db.py SCHEMA, with the PIN lockout columns). Later versions add
real accounts on top of it:

- v2: accounts. A user is known by the canonical WhatsApp number (phone_e164, unique), has a status
  (invited / active / frozen / closed), a PIN state (unset / set / blocked), a language, and the times the
  channel and the admin need. Contacts, bill accounts and bills get their own phone, source and soft delete.
  Transactions know the receiving user (payee_user_id), so the recipient sees incoming money too.
- v3: holds (money sent to a number that has no account yet), admin_ops (idempotent admin money actions),
  outbox (notices to a user, delivered inside their WhatsApp window) and audit_log (decisions and admin
  actions; never a PIN, never message text).
- v4: the biller catalogue's mock ranges (the catalogue rows themselves are added by
  accounts.billing.ensure_catalogue, never into a seeded demo database).
- v5: the identity check (kyc/). Users get kyc_level (NULL: an account from before this feature, its limits
  untouched; else none / pending / verified) and what kyc.privacy.record() allows to be kept: the document
  type, the printed name, the last 4 characters of the number, a keyed fingerprint of the number (indexed, to
  spot one document on two accounts), the birth year, the time and the reason code. kyc_signups holds the same
  record for a number whose sign-up is still open (it has no users row yet). Never an image, never a whole
  number, never a whole birth date.
- v6: voice approval (accounts/voiceprint.py). voiceprints holds an account's speaker vectors (float32, at most
  three, each from a payment asked for in a voice note and confirmed with the PIN, with the model that made it):
  never audio. voice_grants holds the one-time approval of one quote by a matching voice note (its amount, its
  payee, the score), consumed by execute. quotes.approved_by says how a quote was approved ('pin' or 'voice').

Each migration runs in its own BEGIN IMMEDIATE and bumps user_version in the same transaction. Every
step is idempotent (column-exists checks, IF NOT EXISTS), and a file database that already holds users is
copied with SQLite's online backup API before the first pending migration (DATA_DIR/backups/pre-v<n>-...).
A migration never rewrites a money column.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("wallet.migrations")

LATEST = 6

# (table, column, definition): added when missing.
V2_COLUMNS = [
    ("users", "phone_e164", "TEXT"),
    ("users", "status", "TEXT NOT NULL DEFAULT 'active'"),
    ("users", "pin_state", "TEXT NOT NULL DEFAULT 'set'"),
    ("users", "language", "TEXT NOT NULL DEFAULT 'iq'"),
    ("users", "source", "TEXT NOT NULL DEFAULT 'seed'"),
    ("users", "created_at", "TEXT"),
    ("users", "activated_at", "TEXT"),
    ("users", "last_inbound_at", "TEXT"),
    ("users", "pin_set_at", "TEXT"),
    ("users", "pin_reset_at", "TEXT"),
    ("users", "pin_lockouts", "TEXT NOT NULL DEFAULT '[]'"),
    ("users", "wa_profile_name", "TEXT"),
    ("users", "frozen_reason", "TEXT"),
    ("users", "note", "TEXT"),
    ("contacts", "phone_e164", "TEXT"),
    ("contacts", "source", "TEXT NOT NULL DEFAULT 'seed'"),
    ("contacts", "created_at", "TEXT"),
    ("contacts", "last_paid_at", "TEXT"),
    ("contacts", "deleted_at", "TEXT"),
    ("billers", "account_pattern", "TEXT"),
    ("billers", "mock_min", "INTEGER"),
    ("billers", "mock_max", "INTEGER"),
    ("billers", "active", "INTEGER NOT NULL DEFAULT 1"),
    ("bill_accounts", "created_at", "TEXT"),
    ("bill_accounts", "deleted_at", "TEXT"),
    ("bill_accounts", "source", "TEXT NOT NULL DEFAULT 'seed'"),
    ("bills", "issued_at", "TEXT"),
    ("bills", "source", "TEXT NOT NULL DEFAULT 'seed'"),
    ("bills", "paid_txn_id", "TEXT"),
    ("transactions", "payee_user_id", "TEXT"),
    ("transactions", "counterparty_phone", "TEXT"),
    ("transactions", "hold_id", "TEXT"),
    ("transactions", "reason", "TEXT"),
]

V2_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_users_phone ON users(phone_e164) WHERE phone_e164 IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
CREATE UNIQUE INDEX IF NOT EXISTS ux_contacts_phone ON contacts(user_id, phone_e164)
    WHERE phone_e164 IS NOT NULL AND deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_bills_period ON bills(account_id, period);
CREATE INDEX IF NOT EXISTS idx_transactions_payee ON transactions(payee_user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_transactions_created ON transactions(created_at);
"""

V3_SQL = """
CREATE TABLE IF NOT EXISTS holds (
    id          TEXT PRIMARY KEY,
    quote_id    TEXT NOT NULL UNIQUE,
    sender_id   TEXT NOT NULL,
    phone_e164  TEXT NOT NULL,
    amount      INTEGER NOT NULL CHECK (amount > 0),
    fee         INTEGER NOT NULL DEFAULT 0 CHECK (fee >= 0),
    state       TEXT NOT NULL CHECK (state IN ('HELD', 'CLAIMED', 'REFUNDED')),
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    settled_at  TEXT,
    claimed_by  TEXT,
    settle_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_holds_phone ON holds(phone_e164, state);
CREATE INDEX IF NOT EXISTS idx_holds_sender ON holds(sender_id, state);
CREATE INDEX IF NOT EXISTS idx_holds_expiry ON holds(state, expires_at);
CREATE TABLE IF NOT EXISTS admin_ops (
    op_id      TEXT PRIMARY KEY,
    actor      TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('credit', 'debit', 'opening', 'signup_credit')),
    amount     INTEGER NOT NULL CHECK (amount > 0),
    reason     TEXT NOT NULL,
    txn_id     TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL,
    kind         TEXT NOT NULL,
    ref          TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    delivered_at TEXT,
    UNIQUE (user_id, kind, ref)
);
CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox(user_id, delivered_at);
CREATE TABLE IF NOT EXISTS audit_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    at       TEXT NOT NULL,
    actor    TEXT NOT NULL,
    action   TEXT NOT NULL,
    user_id  TEXT,
    ref      TEXT,
    details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id, at);
CREATE INDEX IF NOT EXISTS idx_audit_at ON audit_log(at);
"""

# v4: mock ranges for the catalogue (only rows that exist; a seeded demo DB keeps its own billers).
V4_SQL = r"""
UPDATE billers SET mock_min = 30000, mock_max = 120000, account_pattern = '^\d{6,12}$'
    WHERE id = 'electricity' AND mock_min IS NULL;
UPDATE billers SET mock_min = 25000, mock_max = 90000, account_pattern = '^\d{1,6}$'
    WHERE id = 'generator' AND mock_min IS NULL;
UPDATE billers SET mock_min = 25000, mock_max = 60000 WHERE kind = 'internet' AND mock_min IS NULL;
UPDATE billers SET mock_min = 5000, mock_max = 15000 WHERE id = 'water' AND mock_min IS NULL;
"""


# v5: the identity check. Only what kyc.privacy.record() returns has a column.
V5_COLUMNS = [
    ("users", "kyc_level", "TEXT"),  # NULL | 'none' | 'pending' | 'verified'
    ("users", "kyc_document_type", "TEXT"),
    ("users", "kyc_name", "TEXT"),
    ("users", "kyc_number_last4", "TEXT"),
    ("users", "kyc_number_fingerprint", "TEXT"),
    ("users", "kyc_birth_year", "INTEGER"),
    ("users", "kyc_checked_at", "TEXT"),
    ("users", "kyc_reason", "TEXT"),
]

V5_SQL = """
CREATE INDEX IF NOT EXISTS idx_users_kyc_fingerprint ON users(kyc_number_fingerprint)
    WHERE kyc_number_fingerprint IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_users_kyc_level ON users(kyc_level) WHERE kyc_level IS NOT NULL;
CREATE TABLE IF NOT EXISTS kyc_signups (
    phone_e164         TEXT PRIMARY KEY,
    level              TEXT NOT NULL CHECK (level IN ('none', 'pending', 'verified')),
    document_type      TEXT,
    name               TEXT,
    number_last4       TEXT,
    number_fingerprint TEXT,
    birth_year         INTEGER,
    checked_at         TEXT,
    reason             TEXT,
    created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kyc_signups_fingerprint ON kyc_signups(number_fingerprint)
    WHERE number_fingerprint IS NOT NULL;
"""


# v6: voice approval. No audio anywhere: a vector, the seconds of speech it came from, the model's name.
V6_COLUMNS = [("quotes", "approved_by", "TEXT")]  # NULL (before v6, or never run) | 'pin' | 'voice'
V6_SQL = """
CREATE TABLE IF NOT EXISTS voiceprints (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    quote_id   TEXT NOT NULL UNIQUE,
    model      TEXT NOT NULL,
    dims       INTEGER NOT NULL CHECK (dims > 0),
    vector     BLOB NOT NULL,
    seconds    REAL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_voiceprints_user ON voiceprints(user_id);
CREATE TABLE IF NOT EXISTS voice_grants (
    quote_id    TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    amount      INTEGER NOT NULL CHECK (amount > 0),
    payee       TEXT NOT NULL,
    model       TEXT NOT NULL,
    score       REAL NOT NULL,
    created_at  TEXT NOT NULL,
    consumed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_voice_grants_user ON voice_grants(user_id, consumed_at);
"""


def version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _v2(conn: sqlite3.Connection) -> None:
    for table, column, kind in V2_COLUMNS:
        if column not in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    # canonical numbers for what is already there (seed users and contacts: 07XXXXXXXXX)
    conn.execute("UPDATE users SET phone_e164 = '964' || substr(phone, 2)"
                 " WHERE phone_e164 IS NULL AND phone LIKE '07%' AND length(phone) = 11"
                 " AND NOT EXISTS (SELECT 1 FROM users u2 WHERE u2.phone_e164 = '964' || substr(users.phone, 2))")
    conn.execute("UPDATE contacts SET phone_e164 = '964' || substr(phone, 2)"
                 " WHERE phone_e164 IS NULL AND phone LIKE '07%' AND length(phone) = 11")
    for statement in _statements(V2_SQL):
        try:
            conn.execute(statement)
        except sqlite3.IntegrityError:  # an old file with two contacts of one number: keep it readable
            log.warning("v2: an index could not be made unique on old data (%s)", statement.split()[5])


def _v3(conn: sqlite3.Connection) -> None:
    for statement in _statements(V3_SQL):
        conn.execute(statement)


def _v4(conn: sqlite3.Connection) -> None:
    for statement in _statements(V4_SQL):
        conn.execute(statement)


def _v5(conn: sqlite3.Connection) -> None:
    for table, column, kind in V5_COLUMNS:
        if column not in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    for statement in _statements(V5_SQL):
        conn.execute(statement)


def _v6(conn: sqlite3.Connection) -> None:
    for table, column, kind in V6_COLUMNS:
        if column not in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    for statement in _statements(V6_SQL):
        conn.execute(statement)


MIGRATIONS = [(2, _v2), (3, _v3), (4, _v4), (5, _v5), (6, _v6)]


def _statements(sql: str) -> list[str]:
    return [s.strip() for s in sql.split(";") if s.strip()]


def snapshot(conn: sqlite3.Connection, path: str, label: str) -> Path | None:
    """Copy a file database with the online backup API to <dir>/backups/<stem>-<label>-<time>.db."""
    source = Path(path)
    folder = source.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = folder / f"{source.stem}-{label}-{stamp}.db"
    dest = sqlite3.connect(str(target))
    try:
        conn.backup(dest)
    finally:
        dest.close()
    return target


def migrate(conn: sqlite3.Connection, path: str | None = None) -> int:
    """Bring the database to LATEST. `path` (a file DB) enables the pre-migration snapshot.

    Returns the version reached. A database at v0 is the original schema (v1)."""
    current = version(conn)
    if current == 0:
        conn.execute("PRAGMA user_version = 1")
        current = 1
    pending = [(n, step) for n, step in MIGRATIONS if n > current]
    if not pending:
        return current
    if path and conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        try:
            snapshot(conn, path, f"pre-v{pending[0][0]}")
        except (OSError, sqlite3.Error) as exc:  # never block the start on a backup; say so
            log.warning("pre-migration snapshot failed: %s", type(exc).__name__)
    for number, step in pending:
        conn.execute("BEGIN IMMEDIATE")
        try:
            step(conn)
            conn.execute(f"PRAGMA user_version = {number}")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
        log.info("wallet schema migrated to v%d", number)
    return version(conn)
