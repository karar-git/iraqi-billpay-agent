"""Iraqi amount parser: spoken or typed amounts -> integer IQD.

Iraqi conventions handled here:
- a bare number under 1,000 means thousands of dinars (خمسين -> 50,000, ميتين -> 200,000);
- scales ألف / تالاف / مليون, fractions ربع / نص (ربع مليون, مليون ونص, مليون الا ربع);
- a fraction after a round hundred is a fraction of that hundred (مية ونص -> 150,000);
- after مليون a bare tail is in thousands (مليون وميتين -> 1,200,000);
- Western and Eastern digits with , or ٬ separators (50,000 / ٥٠٬٠٠٠ / 50 الف);
- dollar words are never converted: they always lead to a question.

Anything unclear returns needs_question=True with a reason code and no value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction

from payments.text import DIACRITICS, fold

THOUSAND = 1_000
MILLION = 1_000_000
MAX_IQD = 1_000_000_000

# Reason codes (Amount.reason when needs_question is True)
NO_AMOUNT = "no_amount"
USD = "usd"
APPROXIMATE = "approximate"
MULTIPLE = "multiple_amounts"
UNCLEAR = "unclear"
FRACTION_ALONE = "fraction_alone"
MISSING_COUNT = "missing_count"
BAD_DIGITS = "bad_digits"
ZERO = "zero"
NOT_WHOLE = "not_whole"
TOO_LARGE = "too_large"


@dataclass(frozen=True)
class Amount:
    value_iqd: int | None
    currency: str | None  # 'IQD' | 'USD' | None
    needs_question: bool
    reason: str = ""


def _ask(reason: str, currency: str | None = None) -> Amount:
    return Amount(None, currency, True, reason)


def _table(pairs: dict) -> dict:
    """Fold the keys so lookups match folded input."""
    return {fold(k): v for k, v in pairs.items()}


UNITS = _table({
    "صفر": 0,
    "واحد": 1, "وحدة": 1,
    "اثنين": 2, "ثنين": 2, "اثنان": 2, "اثنتين": 2, "ثنتين": 2, "تنين": 2, "اثنا": 2, "اثني": 2,
    "ثلاث": 3, "ثلاثة": 3, "تلاث": 3, "تلاثة": 3, "تلاته": 3,
    "اربع": 4, "اربعة": 4,
    "خمس": 5, "خمسة": 5,
    "ست": 6, "ستة": 6,
    "سبع": 7, "سبعة": 7,
    "ثمن": 8, "ثمان": 8, "ثماني": 8, "ثمانية": 8, "ثمنية": 8, "تمنية": 8,
    "تسع": 9, "تسعة": 9,
})
TEENS = _table({
    "عشر": 10, "عشرة": 10,
    "احدعش": 11, "حدعش": 11, "احدعشر": 11,
    "اثنعش": 12, "ثنعش": 12, "اثنعشر": 12,
    "تلطعش": 13, "ثلطعش": 13, "ثلاثطعش": 13, "تلتعش": 13,
    "اربطعش": 14, "اربعطعش": 14, "اربعتعش": 14,
    "خمسطعش": 15, "خمستعش": 15,
    "ستطعش": 16, "سطعش": 16, "ستعش": 16,
    "سبعطعش": 17, "سبعتعش": 17,
    "ثمنطعش": 18, "ثمانطعش": 18, "تمنطعش": 18, "ثمنتعش": 18,
    "تسعطعش": 19, "تسعتعش": 19,
})
TENS = _table({
    "عشرين": 20, "عشرون": 20,
    "ثلاثين": 30, "تلاثين": 30, "ثلاثون": 30,
    "اربعين": 40, "اربعون": 40,
    "خمسين": 50, "خمسون": 50,
    "ستين": 60, "ستون": 60,
    "سبعين": 70, "سبعون": 70,
    "ثمانين": 80, "ثمنين": 80, "تمانين": 80, "ثمانون": 80,
    "تسعين": 90, "تسعون": 90,
})
HUNDREDS = _table({
    "مية": 100, "مئة": 100, "مائة": 100, "ميت": 100,
    "ميتين": 200, "مئتين": 200, "مائتين": 200, "مئتان": 200, "مائتان": 200,
})
_HUNDRED_SUFFIXES = [fold(s) for s in ("مية", "مئة", "مائة", "ميت")]
_HUNDRED_PREFIXES = _table({
    "ثلاث": 3, "تلاث": 3, "ثلث": 3, "تلث": 3, "اربع": 4, "خمس": 5, "ست": 6,
    "سبع": 7, "ثمن": 8, "ثمان": 8, "تمن": 8, "تسع": 9,
})
SCALES = _table({
    "الف": THOUSAND, "alf": THOUSAND, "k": THOUSAND, "thousand": THOUSAND,
    "مليون": MILLION, "million": MILLION, "milyon": MILLION, "malyon": MILLION, "malyoon": MILLION,
})
PLURAL_SCALES = _table({
    "آلاف": THOUSAND, "تالاف": THOUSAND, "تلاف": THOUSAND, "الوف": THOUSAND,
    "ملايين": MILLION, "ملاين": MILLION,
})
DUAL_SCALES = _table({"الفين": THOUSAND, "الفان": THOUSAND, "مليونين": MILLION, "مليونان": MILLION})
FRACTIONS = _table({"نص": Fraction(1, 2), "نصف": Fraction(1, 2), "ربع": Fraction(1, 4)})
QUARTERS = set(_table({"ارباع": 1, "رباع": 1}))  # "ثلاث ارباع مليون" = three quarters of a million
IQD_WORDS = set(_table({w: 1 for w in (
    "دينار", "دنانير", "دينارات", "عراقي", "iqd", "dinar", "dinars")}))
USD_WORDS = set(_table({w: 1 for w in (
    "دولار", "دولارات", "دولارين", "ورقة", "ورقتين", "ورقات", "اوراق",
    "دفتر", "دفترين", "دفاتر", "$", "usd", "dollar", "dollars", "dolar")}))
APPROX_WORDS = set(_table({w: 1 for w in ("حوالي", "تقريبا", "بحدود", "شي", "يمكن")}))
# A number right after these words is a month, an account or a phone, not money.
NOT_AMOUNT_AFTER = set(_table({w: 1 for w in (
    "شهر", "الشهر", "لشهر", "رقم", "الرقم", "برقم", "حساب", "الحساب",
    "سنة", "السنة", "ساعة", "الساعة", "يوم", "تاريخ")}))
# "ثمن" also means "price": count it only when a scale or مية follows.
NEEDS_SCALE_AFTER = {fold("ثمن")}
# Top-up cards are named by value: "ابو العشرة" = the 10 (thousand) card.
CARD_PREFIX = fold("ابو")
AND = "و"
MINUS = fold("الا")

_NUMERIC = {"unit", "teen", "ten", "hundred", "digits"}
_STARTS = _NUMERIC | {"scale", "plural", "dual", "frac", "usd", "bad_digits"}
_INSIDE = _STARTS | {"and", "iqd", "minus"}

_DIGIT = "[0-9٠-٩۰-۹]"
_TOKEN = re.compile(
    rf"{_DIGIT}+(?:[.,٫٬]{_DIGIT}+)*"  # digits with separators
    r"|\$"
    rf"|(?:[^\W\d_]|[{DIACRITICS}])+"  # a word (letters + diacritics)
)


@dataclass
class _Token:
    kind: str
    value: object
    word: str
    start: int
    end: int


def _digits_value(word: str) -> Fraction | None:
    """'50,000' / '1.5' / '1.000.000' -> number; None if the separators make no sense."""
    seps = re.findall(r"[.,٫٬]", word)
    parts = re.split(r"[.,٫٬]", word)
    if not seps:
        return Fraction(int(word))
    if len(set(seps)) == 1 and all(len(p) == 3 for p in parts[1:]) and (
            len(seps) > 1 or seps[0] in ",٬"):
        return Fraction(int("".join(parts)))  # thousands separators
    if len(seps) == 1:
        return Fraction(f"{parts[0]}.{parts[1]}")  # decimal point
    return None


def _classify_word(word: str) -> tuple[str, object] | None:
    """Kind and value of one folded word, or None if it is not an amount word."""
    if word in UNITS:
        return "unit", UNITS[word]
    if word in TEENS:
        return "teen", TEENS[word]
    if word in TENS:
        return "ten", TENS[word]
    if word in HUNDREDS:
        return "hundred", HUNDREDS[word]
    for suffix in _HUNDRED_SUFFIXES:
        if word.endswith(suffix) and word[: -len(suffix)] in _HUNDRED_PREFIXES:
            return "hundred", _HUNDRED_PREFIXES[word[: -len(suffix)]] * 100
    if word in SCALES:
        return "scale", SCALES[word]
    if word in PLURAL_SCALES:
        return "plural", PLURAL_SCALES[word]
    if word in DUAL_SCALES:
        return "dual", DUAL_SCALES[word]
    if word in FRACTIONS:
        return "frac", FRACTIONS[word]
    if word in IQD_WORDS:
        return "iqd", None
    if word in USD_WORDS:
        return "usd", None
    return None


def _classify(word: str) -> tuple[str, object]:
    if word[0].isdigit():
        plain = re.fullmatch(r"\d+", word)
        if plain and ((word.startswith("0") and len(word) > 1) or len(word) >= 10):
            return "other", None  # phone or account number
        value = _digits_value(word)
        return ("digits", value) if value is not None else ("bad_digits", None)
    if word == AND:
        return "and", None
    if word == MINUS:
        return "minus", None
    if word in APPROX_WORDS:
        return "approx", None
    found = _classify_word(word)
    # attached و / ب: "وخمسين", "بخمسين", "بالف"
    if found is None and len(word) > 2 and word[0] in (AND, "ب"):
        found = _classify_word(word[1:])
        if found and found[0] == "iqd":
            found = None
    return found or ("other", None)


def _tokenize(text: str) -> list[_Token]:
    tokens = []
    for m in _TOKEN.finditer(text):
        word = fold(m.group())
        kind, value = _classify(word)
        tokens.append(_Token(kind, value, word, m.start(), m.end()))
    # context rules that need neighbours
    for i, tok in enumerate(tokens):
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if nxt is None:
            continue
        if tok.word == CARD_PREFIX and nxt.kind == "other" and nxt.word.startswith("ال"):
            card = _classify_word(nxt.word[2:])  # "ابو العشرة" -> العشرة -> 10
            if card and card[0] in ("unit", "teen", "ten", "hundred"):
                nxt.kind, nxt.value = card
        elif tok.kind == "other" and tok.word in _HUNDRED_PREFIXES and nxt.kind == "hundred" and nxt.value == 100:
            tok.kind, tok.value = "unit", _HUNDRED_PREFIXES[tok.word]  # "تلث مية" written apart -> 300
        elif tok.kind == "unit" and nxt.word in QUARTERS:
            tok.kind, tok.value = "frac", Fraction(tok.value, 4)  # "ثلاث ارباع" -> 3/4
            nxt.kind = "iqd"  # a filler word inside the amount phrase
    for i, tok in enumerate(tokens):
        prev = tokens[i - 1] if i else None
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if tok.kind in _NUMERIC and prev and prev.word in NOT_AMOUNT_AFTER:
            tok.kind = "other"
        elif tok.word in NEEDS_SCALE_AFTER and not (nxt and nxt.kind in ("scale", "plural", "hundred")):
            tok.kind = "other"
        elif tok.kind == "minus" and not (nxt and nxt.kind == "frac" and nxt.value == Fraction(1, 4)):
            tok.kind = "other"
    return tokens


def _runs(tokens: list[_Token]) -> list[tuple[int, int]]:
    """(start, end) index pairs of maximal amount phrases."""
    runs, i = [], 0
    while i < len(tokens):
        if tokens[i].kind in _STARTS:
            j = i
            while j + 1 < len(tokens) and tokens[j + 1].kind in _INSIDE:
                j += 1
            while tokens[j].kind == "and":
                j -= 1
            runs.append((i, j + 1))
            i = j + 1
        else:
            i += 1
    return runs


def _is_weak(run: list[_Token]) -> bool:
    """A lone small number word (e.g. ست = 'lady', سبع) next to a real amount."""
    return len(run) == 1 and run[0].kind == "unit"


class _Group:
    """One number below a scale word, e.g. 'مية وخمسة وعشرين' -> 125."""

    def __init__(self):
        self.parts: dict[str, Fraction] = {}

    @property
    def empty(self) -> bool:
        return not self.parts

    def value(self) -> Fraction:
        return sum(self.parts.values(), Fraction(0))

    def add(self, kind: str, v) -> bool:
        p = self.parts
        if kind == "digits":
            if p:
                return False
            p["digits"] = v
            return True
        if "digits" in p or "frac" in p:
            return False
        if kind == "hundred":
            if set(p) == {"units"} and p["units"] < 10 and v == 100:  # خمس مية -> 500
                p["hundreds"] = p.pop("units") * 100
                return True
            if p:
                return False
            p["hundreds"] = v
            return True
        if kind == "teen":
            if v == 10 and "units" in p and "tens" not in p and p["units"] < 10:  # خمسة عشر
                p["units"] += 10
                p["tens"] = 0
                return True
            if "units" in p or "tens" in p:
                return False
            p["units"], p["tens"] = v, 0
            return True
        slot = "units" if kind == "unit" else "tens"
        if slot in p:
            return False
        p[slot] = v
        return True

    def add_fraction(self, v: Fraction) -> bool:
        if "frac" in self.parts:
            return False
        if set(self.parts) == {"hundreds"}:
            v = v * 100  # مية ونص = 150, ميتين الا ربع = 175 (a fraction of a hundred)
        self.parts["frac"] = v
        return True


def _evaluate(run: list[_Token]) -> Amount:
    toks = [t for t in run if t.kind not in ("and", "iqd")]
    if any(t.kind == "usd" for t in toks):
        return _ask(USD, "USD")
    if any(t.kind == "bad_digits" for t in toks):
        return _ask(BAD_DIGITS)
    total, group, last_scale = Fraction(0), _Group(), None
    i = 0
    while i < len(toks):
        t = toks[i]
        nxt = toks[i + 1].kind if i + 1 < len(toks) else None
        if t.kind in _NUMERIC:
            if not group.add(t.kind, t.value):
                return _ask(UNCLEAR)
        elif t.kind in ("frac", "minus"):
            if t.kind == "minus":  # "الا ربع"
                i += 1
                frac = -toks[i].value
            else:
                frac = t.value
            if not group.empty or (frac > 0 and nxt == "scale"):  # خمسة ونص / نص مليون
                if not group.add_fraction(frac):
                    return _ask(UNCLEAR)
            elif last_scale:  # مليون ونص / مليون الا ربع
                total += frac * last_scale
            else:
                return _ask(FRACTION_ALONE)
        else:  # scale, plural, dual
            if last_scale is not None and t.value >= last_scale:
                return _ask(UNCLEAR)
            if t.kind == "dual":
                if not group.empty:
                    return _ask(UNCLEAR)
                count = 2
            elif group.empty:
                if t.kind == "plural":
                    return _ask(MISSING_COUNT)
                count = 1
            else:
                count = group.value()
            total += count * t.value
            last_scale, group = t.value, _Group()
        i += 1

    rest = group.value()
    if last_scale is None:
        total = rest * THOUSAND if rest < THOUSAND else rest  # bare number: thousands
    elif last_scale >= MILLION and 0 < rest < THOUSAND:
        total += rest * THOUSAND  # مليون وميتين -> 1,200,000
    else:
        total += rest

    if total.denominator != 1:
        return _ask(NOT_WHOLE)
    if total <= 0:
        return _ask(ZERO)
    if total > MAX_IQD:
        return _ask(TOO_LARGE)
    return Amount(int(total), "IQD", False, "")


def find_amount(text: str) -> tuple[Amount, str] | None:
    """Locate the amount phrase in a sentence.

    Returns (Amount, matched substring of `text`), or None when the sentence has no amount.
    "دز لأحمد خمسين" -> (Amount(50000, 'IQD', False, ''), "خمسين").
    """
    tokens = _tokenize(text or "")
    runs = _runs(tokens)
    if not runs:
        return None
    if len(runs) > 1:
        strong = [r for r in runs if not _is_weak(tokens[r[0]:r[1]])]
        runs = strong if strong else runs
    start, end = runs[0]
    first = start - 1 if start > 0 and tokens[start - 1].word == CARD_PREFIX else start  # keep "ابو"
    matched = text[tokens[first].start:tokens[end - 1].end]
    if len(runs) > 1:
        return _ask(MULTIPLE), matched
    if start > 0 and tokens[start - 1].kind == "approx":
        return _ask(APPROXIMATE), matched
    return _evaluate(tokens[start:end]), matched


def amount_phrases(text: str) -> list[tuple[Amount, str]]:
    """Every amount phrase in a sentence with its value: [(Amount, matched substring of `text`), ...]."""
    tokens = _tokenize(text or "")
    return [(_evaluate(tokens[s:e]), text[tokens[s].start:tokens[e - 1].end]) for s, e in _runs(tokens)]


def parse(text: str) -> Amount:
    """Parse an amount phrase ("خمسين", "مية وخمسين الف", "٥٠٬٠٠٠") into IQD."""
    found = find_amount(text)
    return found[0] if found else _ask(NO_AMOUNT)
