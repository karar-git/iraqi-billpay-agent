"""PIN policy for real accounts: 4 to 6 digits, nothing easy to guess, stored only as a peppered hash.

Refused: one digit repeated (0000), straight runs up or down (1234, 9876, 123456), a two-digit pattern
repeated (1212, 121212), the end of the user's own number, and a short list of common PINs. Eastern
digits are read as Western. The hash is wallet.db.hash_account_pin (PBKDF2 over HMAC with the pepper).
"""

from __future__ import annotations

import re
import secrets

from payments.text import fold
from wallet.db import hash_account_pin, verify_pin

MIN_LEN, MAX_LEN = 4, 6
COMMON = frozenset({
    "1122", "1313", "2000", "2001", "2020", "2580", "0007", "1990", "1991", "1995", "1999", "2468", "1357",
    "1004", "4444", "6969", "1010", "0101", "7777", "1478", "3690", "112233", "123123", "000007", "159753",
    "147258", "258369", "111222", "121314", "654321", "102030", "1212", "5683", "0852", "1590",
})
_EDGE = re.compile(r"^[\s.,;:!?؟،\-]+|[\s.,;:!?؟،\-]+$")

OK, NOT_DIGITS, WEAK = "ok", "not_digits", "weak"


def digits_of(text: str) -> str | None:
    """The PIN in a message that is only 4 to 6 digits («1234», «١٢٣٤», «12 34», «1234.»), else None."""
    bare = re.sub(r"\s", "", _EDGE.sub("", fold(text or "")))
    return bare if re.fullmatch(rf"\d{{{MIN_LEN},{MAX_LEN}}}", bare) else None


def is_weak(pin: str, own_phone: str | None = None) -> bool:
    if len(set(pin)) == 1:
        return True
    steps = {(int(b) - int(a)) % 10 for a, b in zip(pin, pin[1:])}
    if steps in ({1}, {9}):
        return True
    if len(pin) % 2 == 0 and pin == pin[:2] * (len(pin) // 2):
        return True
    phone = re.sub(r"\D", "", own_phone or "")
    if phone and phone.endswith(pin):
        return True
    return pin in COMMON


def check(text: str, own_phone: str | None = None) -> tuple[str, str | None]:
    """(OK, pin) | (NOT_DIGITS, None) | (WEAK, None)."""
    pin = digits_of(text)
    if pin is None:
        return NOT_DIGITS, None
    if is_weak(pin, own_phone):
        return WEAK, None
    return OK, pin


def hash_pin(pin: str) -> str:
    return hash_account_pin(pin)


def matches(pin: str, pin_hash: str | None) -> bool:
    return verify_pin(pin, pin_hash)


def new_pin(own_phone: str | None = None, length: int = MIN_LEN) -> str:
    """A fresh random PIN that passes check() (a demo account's: made here, shown once, kept only as a hash)."""
    while True:
        pin = f"{secrets.randbelow(10 ** length):0{length}d}"
        if check(pin, own_phone)[0] == OK:
            return pin
