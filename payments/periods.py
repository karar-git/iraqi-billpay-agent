"""Named months in the user's words: «مال شهر ستة», «فاتورة حزيران», «shahr 6».

months_named(text) -> the months (1..12) the text names, as a set:
- a month name: Iraqi / Levantine (حزيران، تموز، تشرين الأول ...), Gulf / Egyptian (يونيو ...) or English;
- a number after شهر: «شهر ستة», «لشهر 6», «شهر ٦»;
- تشرين or كانون alone fits two months, and two months in one message give two: the caller asks.

The words "الشهر اللي فات" or "هالشهر" name no month here; the core reports them as 'previous' or
'current'. A number after شهر is never read as money (payments.amounts skips it too).
"""

from __future__ import annotations

import re

from payments.amounts import TEENS, UNITS
from payments.text import fold


def _fold_keys(pairs: dict[str, int]) -> dict[str, int]:
    return {fold(k): v for k, v in pairs.items()}


# One-word month names. "آب" (August) is also "father", so it counts only after شهر (see _MONTH_AFTER_SHAHR).
MONTH_NAMES = _fold_keys({
    "شباط": 2, "آذار": 3, "اذار": 3, "نيسان": 4, "أيار": 5, "ايار": 5, "حزيران": 6, "تموز": 7, "أيلول": 9,
    "ايلول": 9,
    "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4, "ابريل": 4, "إبريل": 4, "مايو": 5, "يونيو": 6, "يونيه": 6,
    "يوليو": 7, "يوليه": 7, "أغسطس": 8, "اغسطس": 8, "سبتمبر": 9, "أكتوبر": 10, "اكتوبر": 10, "نوفمبر": 11,
    "ديسمبر": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
})
_MONTH_AFTER_SHAHR = _fold_keys({"آب": 8, "اب": 8, "may": 5})
# Two-word names: تشرين / كانون + first / second.
_FIRST = {fold(w) for w in ("الأول", "الاول", "اول", "الأولى", "1")}
_SECOND = {fold(w) for w in ("الثاني", "التاني", "ثاني", "الثانية", "2")}
_PAIRS = {fold("تشرين"): (10, 11), fold("كانون"): (12, 1)}
_SHAHR = {fold(w) for w in ("شهر", "الشهر", "لشهر", "للشهر", "بشهر", "وشهر")}
# Number words a month can be: واحد .. ثنعش (UNITS and TEENS of payments.amounts, already folded).
_NUMBER_WORDS = {w: v for w, v in {**UNITS, **TEENS}.items() if 1 <= v <= 12}
_WORD = re.compile(r"[^\W_]+")


def _strip_particle(word: str) -> list[str]:
    """The word and its readings without one leading ل / ب / و / ال: "لحزيران" -> ["لحزيران", "حزيران"]."""
    out = [word]
    for prefix in ("وال", "بال", "لل", "ال", "ل", "ب", "و"):
        if word.startswith(prefix) and len(word) - len(prefix) >= 2:
            out.append(word[len(prefix):])
    return out


def _month_number(word: str) -> int | None:
    if word.isdigit():
        n = int(word)
        return n if 1 <= n <= 12 else None
    for form in _strip_particle(word):
        if form in _NUMBER_WORDS:
            return _NUMBER_WORDS[form]
    return None


def months_named(text: str) -> set[int]:
    """Every month (1..12) the text names. Empty when it names none."""
    words = _WORD.findall(fold(text or ""))
    found: set[int] = set()
    for i, word in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else ""
        after_shahr = i > 0 and words[i - 1] in _SHAHR
        forms = _strip_particle(word)
        pair = next((_PAIRS[f] for f in forms if f in _PAIRS), None)
        if pair is not None:
            if nxt in _FIRST:
                found.add(pair[0])
            elif nxt in _SECOND:
                found.add(pair[1])
            else:
                found.update(pair)  # تشرين alone: October or November
            continue
        if word in _FIRST or word in _SECOND:
            if i > 0 and any(f in _PAIRS for f in _strip_particle(words[i - 1])):
                continue  # the second word of تشرين الأول
        month = next((MONTH_NAMES[f] for f in forms if f in MONTH_NAMES), None)
        if month is None and after_shahr:
            month = _MONTH_AFTER_SHAHR.get(word) or _month_number(word)
        if month is not None:
            found.add(month)
    return found
