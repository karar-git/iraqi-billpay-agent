"""What the channel remembers between messages and across restarts (SQLite, DATA_DIR/whatsapp/channel.db).

- seen:    message ids already taken (Meta retries a webhook it thinks we missed).
- inbox:   messages taken but not yet answered, so a restart answers them instead of losing them. A text that
           carries a PIN (or only digits) is kept without its text, as kind REDACTED: after a restart the
           person is asked again, and the PIN never sits on the volume. Deleted rows are overwritten
           (secure_delete), and schema v3 vacuumed what older versions left in free pages.
- senders: the account each WhatsApp sender is (user_id), whether they were greeted (for a demo account: whether
           its intro went out), the card waiting for its PIN, and the Baghdad day of the last hello (the balance is
           mentioned at the first hello of a day at most). Senders are keyed by a keyed hash of their number, never
           the number. Never a PIN.
- onboarding: a sign-up in progress (the step, the language, the name; the first PIN only as a hash).
- hits:    inbound messages per sender, for the hourly rate limit.
- spend:   the conversational core's spend per Baghdad day, for the daily budget.
- counters: messages in and out per Baghdad day (for the admin page; counts only).

The accounts themselves live in one shared database (DATA_DIR/accounts.db, wallet/ and accounts/).
Schema v2 dropped the demo: every demo sender row (a synthetic user per sender) is deleted.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

from agent.pins import pin_in

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (msg_id TEXT PRIMARY KEY, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS inbox (msg_id TEXT PRIMARY KEY, raw TEXT NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS senders (
    key TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    greeted INTEGER NOT NULL DEFAULT 0,
    awaiting_pin TEXT,
    limit_notice REAL,
    rest_notice TEXT,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS hits (key TEXT NOT NULL, ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS hits_key_ts ON hits (key, ts);
CREATE TABLE IF NOT EXISTS spend (day TEXT PRIMARY KEY, usd REAL NOT NULL, notified INTEGER NOT NULL DEFAULT 0);
"""

V2 = """
DELETE FROM senders;
CREATE TABLE IF NOT EXISTS onboarding (
    key          TEXT PRIMARY KEY,
    phone_e164   TEXT NOT NULL,
    step         TEXT NOT NULL,
    lang         TEXT NOT NULL DEFAULT 'iq',
    name         TEXT,
    gender       TEXT,
    pin_hash_tmp TEXT,
    tries        INTEGER NOT NULL DEFAULT 0,
    started_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS counters (day TEXT NOT NULL, name TEXT NOT NULL, n INTEGER NOT NULL DEFAULT 0,
                                     PRIMARY KEY (day, name));
"""
ONBOARDING_FIELDS = {"phone_e164", "step", "lang", "name", "gender", "pin_hash_tmp", "tries", "updated_at"}

SEEN_KEEP_SECONDS = 7 * 24 * 3600
REDACTED = "redacted"  # the kind of an inbox message kept without its text


_FOUR_DIGITS = re.compile(r"(?<!\d)\d{4}(?!\d)")
_EASTERN = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def redact(raw: dict, *, any_four: bool = False) -> dict:
    """The inbox copy of a message: a text carrying a PIN (or only digits) loses its text.

    `any_four` (SIGNUP_MODE=demo): any text with a run of exactly four digits loses it too, because a demo PIN
    can sit inside a sentence with no PIN word («دز لأخوي 5000 والرمز 4719») and only its hash could tell. A lost
    text is answered «send it again» if the process restarts before the turn ends."""
    if raw.get("kind") != "text":
        return raw
    text = str(raw.get("text") or "")
    if pin_in(text) is not None or (any_four and _FOUR_DIGITS.search(text.translate(_EASTERN))):
        return {**raw, "kind": REDACTED, "text": ""}
    return raw


def sender_key(sender: str, secret: str = "") -> str:
    """A stable, file-name-safe key for a sender that does not reveal their number.

    Keyed (HMAC-SHA256 with `secret`): Iraqi mobile numbers are a small space, so a plain hash of one
    could be reversed by trying them all. Without a secret (local runs only) it is a plain hash.
    """
    if secret:
        return hmac.new(secret.encode(), ("wa:" + sender).encode(), hashlib.sha256).hexdigest()[:24]
    return hashlib.sha256(("wa:" + sender).encode()).hexdigest()[:24]


class ChannelStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA secure_delete=ON")  # a deleted inbox row is zeroed, not left in a free page
        self.conn.executescript(SCHEMA)
        self.dropped_demo = 0
        if int(self.conn.execute("PRAGMA user_version").fetchone()[0]) < 2:
            self.dropped_demo = int(self.conn.execute("SELECT COUNT(*) FROM senders").fetchone()[0])
            self.conn.executescript("BEGIN IMMEDIATE;" + V2 + "PRAGMA user_version = 2; COMMIT;")
        if int(self.conn.execute("PRAGMA user_version").fetchone()[0]) < 3:
            # v3, once: older versions kept PIN texts in the inbox; rewrite the file without the free pages.
            for row in self.conn.execute("SELECT msg_id, raw FROM inbox").fetchall():
                kept = redact(json.loads(row["raw"]))
                self.conn.execute("UPDATE inbox SET raw = ? WHERE msg_id = ?",
                                  (json.dumps(kept, ensure_ascii=False), row["msg_id"]))
            self.conn.execute("VACUUM")
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.conn.execute("PRAGMA user_version = 3")
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(senders)")}
        if "hello_day" not in columns:  # added in place: an older file keeps its rows
            self.conn.execute("ALTER TABLE senders ADD COLUMN hello_day TEXT")

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def _run(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, args)

    # ------------------------------------------------------------ dedup + inbox

    def first_time(self, msg_id: str, now: float, raw: dict | None = None, *, any_four: bool = False) -> bool:
        """True once per message id; the message is kept in the inbox until it is answered (see redact)."""
        with self._lock:
            cur = self.conn.execute("INSERT OR IGNORE INTO seen (msg_id, ts) VALUES (?, ?)", (msg_id, now))
            if cur.rowcount != 1:
                return False
            if raw is not None:
                self.conn.execute("INSERT OR REPLACE INTO inbox (msg_id, raw, ts) VALUES (?, ?, ?)",
                                  (msg_id, json.dumps(redact(raw, any_four=any_four), ensure_ascii=False), now))
            return True

    def done(self, msg_ids: list[str]) -> None:
        with self._lock:
            self.conn.executemany("DELETE FROM inbox WHERE msg_id = ?", [(m,) for m in msg_ids])

    def pending(self, since: float) -> list[dict]:
        """Inbox messages newer than `since`, oldest first. Older ones are dropped."""
        with self._lock:
            self.conn.execute("DELETE FROM inbox WHERE ts < ?", (since,))
            rows = self.conn.execute("SELECT raw FROM inbox ORDER BY ts").fetchall()
        return [json.loads(r["raw"]) for r in rows]

    def prune(self, now: float) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM seen WHERE ts < ?", (now - SEEN_KEEP_SECONDS,))
            self.conn.execute("DELETE FROM hits WHERE ts < ?", (now - 3600,))

    # ------------------------------------------------------------ senders

    def sender(self, key: str) -> dict | None:
        row = self._run("SELECT * FROM senders WHERE key = ?", (key,)).fetchone()
        return dict(row) if row else None

    def add_sender(self, key: str, user_id: str, now: float) -> dict:
        self._run("INSERT OR IGNORE INTO senders (key, user_id, created) VALUES (?, ?, ?)", (key, user_id, now))
        return self.sender(key) or {}

    def update_sender(self, key: str, **fields: Any) -> None:
        if not fields:
            return
        allowed = {"user_id", "greeted", "awaiting_pin", "limit_notice", "rest_notice", "hello_day"}
        if not set(fields) <= allowed:
            raise ValueError(f"unknown sender fields: {set(fields) - allowed}")
        cols = ", ".join(f"{name} = ?" for name in fields)
        self._run(f"UPDATE senders SET {cols} WHERE key = ?", (*fields.values(), key))

    def senders_count(self) -> int:
        return int(self._run("SELECT COUNT(*) FROM senders").fetchone()[0])

    # ------------------------------------------------------------ sign-up in progress

    def onboarding(self, key: str) -> dict | None:
        row = self._run("SELECT * FROM onboarding WHERE key = ?", (key,)).fetchone()
        return dict(row) if row else None

    def start_onboarding(self, key: str, phone_e164: str, step: str, lang: str, now: float) -> dict:
        self._run("INSERT OR REPLACE INTO onboarding (key, phone_e164, step, lang, started_at, updated_at)"
                  " VALUES (?, ?, ?, ?, ?, ?)", (key, phone_e164, step, lang, now, now))
        return self.onboarding(key) or {}

    def update_onboarding(self, key: str, **fields: Any) -> None:
        if not set(fields) <= ONBOARDING_FIELDS:
            raise ValueError(f"unknown onboarding fields: {set(fields) - ONBOARDING_FIELDS}")
        cols = ", ".join(f"{name} = ?" for name in fields)
        self._run(f"UPDATE onboarding SET {cols} WHERE key = ?", (*fields.values(), key))

    def end_onboarding(self, key: str) -> None:
        self._run("DELETE FROM onboarding WHERE key = ?", (key,))

    def onboarding_rows(self) -> list[dict]:
        return [dict(r) for r in self._run("SELECT phone_e164, step, lang, started_at, updated_at FROM onboarding"
                                           " ORDER BY updated_at DESC").fetchall()]

    # ------------------------------------------------------------ counters

    def count(self, day: str, name: str, n: int = 1) -> None:
        self._run("INSERT INTO counters (day, name, n) VALUES (?, ?, ?)"
                  " ON CONFLICT(day, name) DO UPDATE SET n = n + excluded.n", (day, name, n))

    def counters(self, since_day: str = "") -> list[dict]:
        return [dict(r) for r in self._run("SELECT * FROM counters WHERE day >= ? ORDER BY day", (since_day,)).fetchall()]

    def spend_days(self, since_day: str = "") -> list[dict]:
        return [dict(r) for r in self._run("SELECT day, usd FROM spend WHERE day >= ? ORDER BY day", (since_day,)).fetchall()]

    # ------------------------------------------------------------ rate limit

    def hit(self, key: str, now: float) -> int:
        """Count one inbound message; returns how many this sender sent in the last hour."""
        with self._lock:
            self.conn.execute("INSERT INTO hits (key, ts) VALUES (?, ?)", (key, now))
            return int(self.conn.execute("SELECT COUNT(*) FROM hits WHERE key = ? AND ts > ?",
                                         (key, now - 3600)).fetchone()[0])

    # ------------------------------------------------------------ budget

    def spent(self, day: str) -> float:
        row = self._run("SELECT usd FROM spend WHERE day = ?", (day,)).fetchone()
        return float(row["usd"]) if row else 0.0

    def add_spend(self, day: str, usd: float) -> float:
        with self._lock:
            self.conn.execute("INSERT INTO spend (day, usd) VALUES (?, ?) "
                              "ON CONFLICT(day) DO UPDATE SET usd = usd + excluded.usd", (day, usd))
            return self.spent(day)

    def mark_budget_notified(self, day: str) -> bool:
        """True the first time it is called for a day (the manager hears about the cap once)."""
        with self._lock:
            self.conn.execute("INSERT OR IGNORE INTO spend (day, usd) VALUES (?, 0)", (day,))
            cur = self.conn.execute("UPDATE spend SET notified = 1 WHERE day = ? AND notified = 0", (day,))
            return cur.rowcount == 1
