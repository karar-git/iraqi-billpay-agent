"""Text preparation and character n-grams for the decision model. Standard library only.

Training (decision/train.py) hands `prepare` to scikit-learn as the preprocessor and lets scikit-learn cut the
n-grams itself; inference (decision/intent.py) uses `ngrams` below. A test proves both give the same numbers.

`prepare` rests on payments/text.py (fold) and payments/arabizi.py (the Arabic reading of Latin words). A change
in either changes what the model reads: retrain, or tests/test_decision_intent.py fails on the stored reference.
"""

from __future__ import annotations

import re

from payments import arabizi
from payments.text import fold

_DIGIT = re.compile(r"[0-9]")
_LATIN = re.compile(r"[a-z]")
_NUMBER_WITH_UNIT = re.compile(r"[0-9][0-9.,]*[a-z]{1,3}[.,!?]*")  # «50k», «25,000iqd»: a number, not Arabizi
# A masked phone number as the orchestrator writes it (accounts/phones.py): «هاتف_ا», «هاتف_اب».
_PHONE_TOKEN = re.compile(r"هاتف_[ء-ي]{1,2}")
_PHONE_SHAPE = "0" * 11  # what an Iraqi mobile number (07XXXXXXXXX) looks like once every digit is 0


def _zero_digits(text: str) -> str:
    """Every digit written 0, except inside a Latin-letter word, where a digit is an Arabizi letter («sh7anet»)."""
    return " ".join(word if _LATIN.search(word) and not _NUMBER_WITH_UNIT.fullmatch(word) else _DIGIT.sub("0", word)
                    for word in text.split())


def prepare(text: str, reading: bool = True) -> str:
    """The text the model reads: folded letters (payments.text.fold), single spaces, every digit written 0.

    The model picks an action, it never reads an amount, so the digits themselves carry nothing: «50000» and
    «75000» become the same «00000». A masked phone number becomes the shape of a real one, so the model reads
    a message the same way before and after masking.

    With `reading`, a message that has Latin letters is followed by its reading in Arabic script
    (payments.arabizi.to_arabic), so Arabizi «sh7anet» shares its n-grams with «شحنت». English words get a
    meaningless but stable spelling there, which does no harm."""
    shaped = _zero_digits(_PHONE_TOKEN.sub(_PHONE_SHAPE, fold(text or "")))
    if reading and _LATIN.search(shaped):
        try:
            arabic = arabizi.to_arabic(shaped)
        except Exception:  # a reading is a bonus; never let it break a prediction
            arabic = ""
        if arabic:
            shaped = f"{shaped} {_zero_digits(fold(arabic))}"
    return shaped


def ngrams(prepared: str, low: int, high: int) -> list[str]:
    """Character n-grams inside word boundaries, each word padded with one space on both sides.

    The same cut as scikit-learn's `char_wb` analyzer: for a word shorter than n, the padded word is counted
    once and longer n are skipped."""
    out: list[str] = []
    for word in prepared.split():
        padded = f" {word} "
        size = len(padded)
        for n in range(low, high + 1):
            if n >= size:
                out.append(padded)
                break
            out.extend(padded[i:i + n] for i in range(size - n + 1))
    return out
