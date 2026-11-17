"""Linking an Instagram sender to a wallet account that already exists.

The root of an account stays its WhatsApp number. An Instagram user id proves nothing about a phone number, so the
link is made from the trusted side:

1. In the WhatsApp chat (where the number is the account) the person asks for a link code: `issue(user_id)`.
   Six digits, valid 10 minutes, one live code per account, at most 3 codes an hour.
2. The person types the six digits in the Instagram chat: `redeem(ig_sender, code)` returns the account's user id
   and remembers the link. A code works once. 5 wrong codes from one sender inside an hour lock that sender for an
   hour. A flood of wrong codes from many senders (many throwaway accounts, one or two guesses each) turns on a
   brake: while it is on, a sender that already typed a wrong code this hour may not try again. A sender with no
   wrong code of its own still links, so the brake never shuts out the person holding a live code.
3. From then on `linked(ig_sender)` names the account. `unlink(user_id)` cuts it (from WhatsApp or the admin).

A link only says which account an Instagram chat talks about. It is not a second factor: every payment still needs
the tap on the card and the PIN.

The table holds only a salted, keyed hash of a code (HMAC-SHA256 with `secret` as the key and a random salt per
code). A code is never logged and never stored in the clear. Six digits cannot be protected by hashing alone, so
`secret` should be set: a copy of the table is then useless without it.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterator

log = logging.getLogger("instagram.link")

CODE_DIGITS = 6
CODE_TTL_SECONDS = 600
MAX_CODES_PER_HOUR = 3
MAX_WRONG_PER_HOUR = 5
LOCK_SECONDS = 3600
GLOBAL_WRONG_PER_HOUR = 100  # wrong codes from all senders together before the brake is on (0: no such brake)
HOUR = 3600

_DIGITS = str.maketrans({**{chr(0x0660 + d): str(d) for d in range(10)},
                         **{chr(0x06F0 + d): str(d) for d in range(10)}})
_SPACERS = " \t ‎‏-.٫٬"

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS ig_link_codes (
        user_id TEXT PRIMARY KEY, salt BLOB NOT NULL, code_hash BLOB NOT NULL,
        created_at REAL NOT NULL, expires_at REAL NOT NULL)""",
    "CREATE TABLE IF NOT EXISTS ig_link_issues (user_id TEXT NOT NULL, at REAL NOT NULL)",
    "CREATE INDEX IF NOT EXISTS ig_link_issues_user ON ig_link_issues (user_id, at)",
    "CREATE TABLE IF NOT EXISTS ig_link_tries (ig_sender TEXT NOT NULL, at REAL NOT NULL)",
    "CREATE INDEX IF NOT EXISTS ig_link_tries_sender ON ig_link_tries (ig_sender, at)",
    "CREATE TABLE IF NOT EXISTS ig_link_locks (ig_sender TEXT PRIMARY KEY, until REAL NOT NULL)",
    """CREATE TABLE IF NOT EXISTS ig_links (
        ig_sender TEXT PRIMARY KEY, user_id TEXT NOT NULL UNIQUE, linked_at REAL NOT NULL)""",
)


class LinkError(Exception):
    pass


class TooManyCodes(LinkError):
    """This account asked for too many codes. `retry_after` is in seconds."""

    def __init__(self, retry_after: float):
        self.retry_after = max(0.0, float(retry_after))
        super().__init__("too many link codes this hour")


def looks_like_code(text: object) -> str | None:
    """The six digits when a message is a link code and nothing else (Eastern digits and spaces accepted)."""
    if not isinstance(text, str) or len(text) > 40:
        return None
    plain = text.translate(_DIGITS).strip().strip("«»\"'")
    for ch in _SPACERS:
        plain = plain.replace(ch, "")
    return plain if len(plain) == CODE_DIGITS and plain.isascii() and plain.isdigit() else None


class LinkCodes:
    """Link codes and links over a sqlite3 connection (its tables are created if missing).

    `secret` keys the code hash (set it: see the module's notes). `clock` gives seconds, `randbelow` draws the code;
    both can be replaced in tests. Safe to share between threads when the connection is."""

    def __init__(self, conn: sqlite3.Connection, *, secret: str | bytes = b"",
                 clock: Callable[[], float] = time.time, randbelow: Callable[[int], int] = secrets.randbelow,
                 ttl_seconds: int = CODE_TTL_SECONDS, max_codes_per_hour: int = MAX_CODES_PER_HOUR,
                 max_wrong_per_hour: int = MAX_WRONG_PER_HOUR, lock_seconds: int = LOCK_SECONDS,
                 global_wrong_per_hour: int = GLOBAL_WRONG_PER_HOUR):
        self.conn = conn
        self.clock = clock
        self.randbelow = randbelow
        self.ttl_seconds = ttl_seconds
        self.max_codes_per_hour = max_codes_per_hour
        self.max_wrong_per_hour = max_wrong_per_hour
        self.lock_seconds = lock_seconds
        self.global_wrong_per_hour = global_wrong_per_hour
        key = secret.encode() if isinstance(secret, str) else bytes(secret)
        self._key = hashlib.sha256(b"instagram-link-code\x00" + key).digest()
        self._lock = threading.RLock()
        with self._tx() as c:
            for statement in _SCHEMA:
                c.execute(statement)

    # ------------------------------------------------------------ plumbing

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """One transaction of our own, whatever mode the connection is in. Inside a transaction the caller opened,
        nothing is committed here: the caller's commit decides."""
        with self._lock:
            pep249 = getattr(self.conn, "autocommit", -1) is False  # always in a transaction: commit() ends ours
            own = pep249 or not self.conn.in_transaction
            if own and not pep249:
                self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                if own and pep249:
                    self.conn.rollback()
                elif own:
                    self.conn.execute("ROLLBACK")
                raise
            if own and pep249:
                self.conn.commit()
            elif own:
                self.conn.execute("COMMIT")

    def _hash(self, salt: bytes, code: str) -> bytes:
        return hmac.new(self._key, bytes(salt) + code.encode("ascii"), hashlib.sha256).digest()

    def _purge(self, c: sqlite3.Connection, now: float) -> None:
        c.execute("DELETE FROM ig_link_codes WHERE expires_at <= ?", (now,))
        c.execute("DELETE FROM ig_link_issues WHERE at <= ?", (now - HOUR,))
        c.execute("DELETE FROM ig_link_tries WHERE at <= ?", (now - HOUR,))
        c.execute("DELETE FROM ig_link_locks WHERE until <= ?", (now,))

    def _owner(self, c: sqlite3.Connection, code: str, now: float, but: str = "") -> str | None:
        """Whose live code this is. Every live code is compared, in constant time each, with no early exit."""
        owner: str | None = None
        rows = c.execute("SELECT user_id, salt, code_hash FROM ig_link_codes WHERE expires_at > ? AND user_id != ?",
                         (now, but)).fetchall()
        for user_id, salt, stored in rows:
            if hmac.compare_digest(self._hash(salt, code), bytes(stored)) and owner is None:
                owner = user_id
        return owner

    def _flood(self, c: sqlite3.Connection, now: float) -> float | None:
        """When the brake on a flood of wrong codes comes off, or None when it is not on."""
        if self.global_wrong_per_hour <= 0:
            return None
        wrong = c.execute("SELECT at FROM ig_link_tries WHERE at > ? ORDER BY at DESC LIMIT 1 OFFSET ?",
                          (now - HOUR, self.global_wrong_per_hour - 1)).fetchone()
        return float(wrong[0]) + HOUR if wrong else None  # it ends when its oldest counted try is an hour old

    def _locked(self, c: sqlite3.Connection, ig_sender: str, now: float) -> float | None:
        """When this sender may try again, or None. The brake holds only a sender with a wrong code of its own this
        hour: twenty throwaway accounts must not be able to shut every real user out of linking."""
        row = c.execute("SELECT until FROM ig_link_locks WHERE ig_sender = ? AND until > ?", (ig_sender, now)).fetchone()
        until = float(row[0]) if row else None
        flood = self._flood(c, now)
        if flood is not None and c.execute("SELECT 1 FROM ig_link_tries WHERE ig_sender = ? AND at > ? LIMIT 1",
                                           (ig_sender, now - HOUR)).fetchone():
            until = max(until or 0.0, flood)
        return until

    # ------------------------------------------------------------ the WhatsApp side

    def issue(self, user_id: str) -> str:
        """A fresh code for this account, replacing any earlier one. Raises `TooManyCodes` after 3 in an hour."""
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("user_id is empty")
        now = self.clock()
        with self._tx() as c:
            self._purge(c, now)
            times = [float(row[0]) for row in c.execute(
                "SELECT at FROM ig_link_issues WHERE user_id = ? AND at > ? ORDER BY at", (user_id, now - HOUR))]
            if len(times) >= self.max_codes_per_hour:
                raise TooManyCodes(times[len(times) - self.max_codes_per_hour] + HOUR - now)
            for _ in range(50):
                code = f"{self.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"
                if self._owner(c, code, now, but=user_id) is None:  # two accounts never hold the same live code
                    break
            else:
                raise LinkError("no free code")
            salt = secrets.token_bytes(16)
            c.execute("INSERT OR REPLACE INTO ig_link_codes (user_id, salt, code_hash, created_at, expires_at) "
                      "VALUES (?, ?, ?, ?, ?)", (user_id, salt, self._hash(salt, code), now, now + self.ttl_seconds))
            c.execute("INSERT INTO ig_link_issues (user_id, at) VALUES (?, ?)", (user_id, now))
        log.info("instagram link code issued")
        return code

    def unlink(self, user_id: str) -> bool:
        """Cut this account's Instagram link and void its live code. True when there was a link."""
        with self._tx() as c:
            c.execute("DELETE FROM ig_link_codes WHERE user_id = ?", (user_id,))
            gone = c.execute("DELETE FROM ig_links WHERE user_id = ?", (user_id,)).rowcount
        return gone > 0

    def sender_of(self, user_id: str) -> str | None:
        """The Instagram sender linked to this account, if any."""
        with self._lock:
            row = self.conn.execute("SELECT ig_sender FROM ig_links WHERE user_id = ?", (user_id,)).fetchone()
        return str(row[0]) if row else None

    # ------------------------------------------------------------ the Instagram side

    def linked(self, ig_sender: str) -> str | None:
        """The account this Instagram sender is linked to, if any."""
        with self._lock:
            row = self.conn.execute("SELECT user_id FROM ig_links WHERE ig_sender = ?", (ig_sender,)).fetchone()
        return str(row[0]) if row else None

    def redeem(self, ig_sender: str, code: str) -> str | None:
        """The account's user id when `code` is a live code, and the link is made (replacing an older link of that
        account or of that sender). None otherwise: a wrong, used or expired code, or a locked sender. Something
        that is not six digits is None as well, and is not counted as a try."""
        digits = looks_like_code(code)
        if not isinstance(ig_sender, str) or not ig_sender or digits is None:
            return None
        now = self.clock()
        made = False
        with self._tx() as c:
            self._purge(c, now)
            if self._locked(c, ig_sender, now) is not None:
                return None  # not even a right code passes, and nothing is counted
            owner = self._owner(c, digits, now)
            if owner is not None:
                # compare-and-delete: of two redeems of one code, exactly one finds the row
                made = c.execute("DELETE FROM ig_link_codes WHERE user_id = ? AND expires_at > ?",
                                 (owner, now)).rowcount == 1
            if made:
                c.execute("DELETE FROM ig_links WHERE ig_sender = ? OR user_id = ?", (ig_sender, owner))
                c.execute("INSERT INTO ig_links (ig_sender, user_id, linked_at) VALUES (?, ?, ?)",
                          (ig_sender, owner, now))
                c.execute("DELETE FROM ig_link_tries WHERE ig_sender = ?", (ig_sender,))
            else:
                c.execute("INSERT INTO ig_link_tries (ig_sender, at) VALUES (?, ?)", (ig_sender, now))
                wrong = c.execute("SELECT COUNT(*) FROM ig_link_tries WHERE ig_sender = ? AND at > ?",
                                  (ig_sender, now - HOUR)).fetchone()[0]
                if wrong >= self.max_wrong_per_hour:
                    c.execute("INSERT OR REPLACE INTO ig_link_locks (ig_sender, until) VALUES (?, ?)",
                              (ig_sender, now + self.lock_seconds))
        if made:
            log.info("instagram link made")
            return owner
        return None

    def locked_until(self, ig_sender: str) -> float | None:
        """When this sender may try a code again (the clock's seconds), or None when it may now."""
        now = self.clock()
        with self._lock:
            return self._locked(self.conn, ig_sender, now)

    def paused_until(self) -> float | None:
        """When the brake on a flood of wrong codes comes off (the clock's seconds), or None when it is not on. For
        the health check and the admin's alert: while it is on, only senders with no wrong code of their own link."""
        now = self.clock()
        with self._lock:
            return self._flood(self.conn, now)

    def tries_left(self, ig_sender: str) -> int:
        """Wrong codes this sender may still type this hour before it is locked."""
        now = self.clock()
        with self._lock:
            if self._locked(self.conn, ig_sender, now) is not None:
                return 0
            wrong = self.conn.execute("SELECT COUNT(*) FROM ig_link_tries WHERE ig_sender = ? AND at > ?",
                                      (ig_sender, now - HOUR)).fetchone()[0]
        return max(0, self.max_wrong_per_hour - int(wrong))
