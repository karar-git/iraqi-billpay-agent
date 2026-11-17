"""What may be kept about an identity document, and what never is.

Never stored and never logged, anywhere in this package: the image bytes, the full document number, the full
birth date, the raw answer of the vision model. `record()` builds the only dict that may be written to a
database: the level, the document type, the name printed on it, the last 4 characters of the number, a keyed
fingerprint of the number, the birth year, the time of the check and the reason code.

The fingerprint is an HMAC-SHA256 of the normalised number under a secret pepper. Two accounts that show the
same document get the same fingerprint, so a duplicate can be spotted without keeping the number. The pepper
comes from the environment of the service (never from a file in the repository); without it the fingerprint
of a 12-digit number could be found by trying every number.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone

from kyc.check import Verdict, normalize_number
from kyc.policy import level_after

RECORD_KEYS = ("level", "document_type", "name_on_document", "number_last4", "number_fingerprint", "birth_year",
               "checked_at", "reason")


def last4(number: str | None) -> str | None:
    """The last 4 characters of the normalised number, or None for a number shorter than 8 (its last 4 would be
    half of it or more)."""
    normal = normalize_number(number)
    return normal[-4:] if len(normal) >= 8 else None


def masked(number: str | None) -> str:
    """The number as it may be shown: last 4 only, «••••1234». Nothing of a number that is too short."""
    return "••••" + (last4(number) or "")


def _require_pepper(pepper) -> None:
    if not isinstance(pepper, (bytes, bytearray)) or len(pepper) < 16:
        raise ValueError("the fingerprint pepper must be at least 16 bytes")


def fingerprint(number: str, pepper: bytes) -> str:
    """HMAC-SHA256 (hex) of the normalised number under `pepper`. The same document gives the same value however
    it was typed or read (Eastern digits, spaces, small letters)."""
    _require_pepper(pepper)
    normal = normalize_number(number)
    if not normal:
        raise ValueError("no number to fingerprint")
    return hmac.new(bytes(pepper), normal.encode("utf-8"), hashlib.sha256).hexdigest()


def record(verdict: Verdict, *, pepper: bytes, checked_at: datetime | None = None) -> dict:
    """The only dict that may be stored for one check. No image, no full number, no full birth date.

    `checked_at` is written in UTC (ISO 8601, seconds); it defaults to now. A verdict with no usable number has
    no last 4 and no fingerprint."""
    _require_pepper(pepper)  # also when this verdict has no number: a missing pepper must never go unnoticed
    reading = verdict.reading
    number = reading.document_number if reading else None
    moment = checked_at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return {
        "level": level_after(verdict),
        "document_type": reading.document_type if reading else None,
        "name_on_document": reading.name_on_document if reading else None,
        "number_last4": last4(number),
        "number_fingerprint": fingerprint(number, pepper) if number else None,
        "birth_year": reading.birth_date.year if reading and reading.birth_date else None,
        "checked_at": moment.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "reason": verdict.reason,
    }
