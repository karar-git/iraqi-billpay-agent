"""Fault injection for the mock wallet, so tests and demos can break payments on purpose.

Modes (all act on POST /quotes/{qid}/execute only):
- none                        normal behaviour
- timeout_before_commit       all work is done, then rolled back; client gets 504; nothing moved
- drop_after_commit           the payment COMMITS, then the response is lost; client gets 504
                              although money moved (a same-key retry returns the stored result)
- error_500                   the server fails before touching anything; client gets 500
- biller_down                 bill/top-up execution fails before the debit (quote FAILED)
- biller_decline_after_debit  bill/top-up: debit happens, biller declines, the debit is reversed
                              automatically (quote FAILED, result says the money was returned)

A fault stays on until set back to "none", or fires `times` times when `times` is given.
timeout/drop faults fire only when a call commits a new outcome, never on a replay of a
finished quote.
"""

from __future__ import annotations

import threading

MODES = (
    "none",
    "timeout_before_commit",
    "drop_after_commit",
    "error_500",
    "biller_down",
    "biller_decline_after_debit",
)


class SimulatedTimeout(Exception):
    """The client must see a timeout (HTTP 504). `committed` says whether the work was saved."""

    def __init__(self, mode: str, committed: bool):
        super().__init__(mode)
        self.mode = mode
        self.committed = committed


class SimulatedServerError(Exception):
    """The client must see HTTP 500. Nothing was changed."""


class FaultInjector:
    """Holds the active fault mode for one app instance (thread-safe)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.mode = "none"
        self.times_left: int | None = None

    def set(self, mode: str = "none", times: int | None = None) -> dict:
        if mode not in MODES:
            raise ValueError(f"unknown fault mode: {mode}")
        if times is not None and times < 1:
            raise ValueError("times must be at least 1")
        with self._lock:
            self.mode = mode
            self.times_left = None if mode == "none" else times
        return self.current()

    def current(self) -> dict:
        return {"mode": self.mode, "times_left": self.times_left}

    def fire(self, mode: str) -> bool:
        """True when `mode` is active; uses up one of its remaining `times`."""
        with self._lock:
            if self.mode != mode:
                return False
            if self.times_left is not None:
                self.times_left -= 1
                if self.times_left == 0:
                    self.mode, self.times_left = "none", None
            return True
