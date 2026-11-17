"""What each verification level may do. One table, integer IQD, decided in code.

Levels:
- "none":     no accepted document yet. Small amounts only, so a person can try the service.
- "pending":  a document was read and waits for a human (`Verdict.status == "review"`). Same small amounts.
- "verified": the document passed every rule (or a human approved it). The normal limits.

No model output can change a level or a limit: `level_after` reads only the status that `kyc.check.check`
computed, and the numbers below are the only source of the limits.
"""

from __future__ import annotations

from kyc.check import Verdict

LEVELS = ("none", "pending", "verified")

# level -> the most a person may send in one day, and in one transfer or payment (IQD)
POLICY: dict[str, dict[str, int]] = {
    "none":     {"daily_limit": 150_000,   "single_max": 50_000},
    "pending":  {"daily_limit": 150_000,   "single_max": 50_000},
    "verified": {"daily_limit": 1_000_000, "single_max": 1_000_000},
}

# How many photos one sign-up may send before the request goes to a human instead of another automatic try.
MAX_ATTEMPTS = 3

_LEVEL_AFTER = {"verified": "verified", "review": "pending", "retry": "none", "refused": "none"}


def limits_for(level: str) -> dict[str, int]:
    """The limits of a level, as a fresh dict. An unknown level gets the limits of "none"."""
    return dict(POLICY.get(level, POLICY["none"]))


def level_after(verdict: Verdict) -> str:
    """The level a person has after this verdict. Anything unexpected is "none"."""
    return _LEVEL_AFTER.get(verdict.status, "none")


def over(level: str, amount: int, spent_today: int = 0) -> str | None:
    """Which limit of the level this amount would pass: "kyc_single_max", "kyc_daily_limit" (text keys of
    kyc.texts) or None when it fits. `spent_today` is what the person already sent today."""
    limits = limits_for(level)
    if amount > limits["single_max"]:
        return "kyc_single_max"
    if spent_today + amount > limits["daily_limit"]:
        return "kyc_daily_limit"
    return None
