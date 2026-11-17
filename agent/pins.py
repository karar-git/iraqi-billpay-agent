"""Spotting a PIN in a message, typed or said, in any reply language.

A PIN must never reach the core, never become an amount, and never be said back. The orchestrator
and the WhatsApp channel both use these checks:

- looks_like_pin: a message that is only 4 to 8 digits, also with the full stop speech-to-text adds
  («1234.») or spaces («12 34»), Western or Eastern digits;
- mentions_pin: the word for PIN in Iraqi, MSA or English («الرمز», «الرقم السري», «pin», «passcode»),
  or «نسيت» / «forgot»;
- pin_in: the PIN a message carries, and how ("bare", "demo" or "worded"), else None. An amount
  inside a request («دز لهادي 50000») is not a PIN.
"""

from __future__ import annotations

import re

from payments import names
from payments.text import fold

PIN_RE = re.compile(r"\d{4,8}")
_EDGE_MARKS = re.compile(r"^[\s.,;:!?؟،!\-]+|[\s.,;:!?؟،!\-]+$")
# Whole words that name a PIN (normalised). «رمز» is matched inside a word too («رمزي», «الرمز»).
PIN_WORDS = frozenset(names.normalize(w) for w in (
    "pin", "pins", "pin:", "passcode", "passcodes", "password", "passwd", "code", "otp",
    "السري", "سري", "السرّي", "السر", "سر", "كلمة السر", "باسورد", "الباسورد"))


def _words(text: str) -> list[str]:
    return names.normalize(text or "").split()


def looks_like_pin(text: str) -> bool:
    """Only 4 to 8 digits («1234», «١٢٣٤», «1234.», «12 34»)."""
    bare = _EDGE_MARKS.sub("", fold(text or ""))
    return PIN_RE.fullmatch(re.sub(r"\s", "", bare)) is not None


def mentions_pin(text: str) -> bool:
    """«نسيت الرمز», «شنو رمزي», «الرقم السري 5678», «my pin is 5678», «I forgot».

    Each word is read without the punctuation around it, so «PIN:» counts too."""
    words = [w.strip(".,;:!?؟،\"'«»()") for w in _words(text)]
    return any("رمز" in w or w.startswith("نسيت") or w in PIN_WORDS or w in ("forgot", "forget") for w in words)


def pin_in(text: str, demo_pin: str = "") -> tuple[str, str] | None:
    """The PIN a message carries, and how: ("bare", digits) for a message of 4 to 8 digits only,
    ("demo", pin) when the demo PIN is one of its numbers, ("worded", digits) for «الرمز 5678» or
    «my pin is 5678». None for anything else."""
    folded = fold(text or "")
    if looks_like_pin(folded):
        return "bare", re.sub(r"\s", "", _EDGE_MARKS.sub("", folded))
    tokens = re.findall(r"\d+", folded)
    if demo_pin and demo_pin in tokens:
        return "demo", demo_pin
    candidates = [t for t in tokens if 4 <= len(t) <= 8]
    if len(candidates) == 1 and mentions_pin(folded):
        return "worded", candidates[0]
    return None
