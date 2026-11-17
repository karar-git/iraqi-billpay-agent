"""Iraqi mobile numbers: find them in a message, write them one canonical way, and mask them.

- canonical: '9647XXXXXXXXX' (the digits WhatsApp gives as the sender id). Accepted shapes: 07701234567,
  0770 123 4567, 0770-123-4567, +964 770 123 4567, 00964770..., 964770..., 7701234567, with Western or
  Eastern digits. A mobile is 7[3-9] + 8 digits after the country code.
- display: '0770 123 4567'; last4: '4567'.
- mask: every number in a text becomes a placeholder word (هاتف_ا, هاتف_ب, ...) made only of Arabic letters,
  so the conversational core never sees a third party's number, and a phone number can never be read as an
  amount. The same number gets the same placeholder. unmask maps a placeholder back.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from payments.text import fold

OPERATOR_PREFIXES = {"zain": ("078", "079"), "asiacell": ("077",), "korek": ("075",)}
TOKEN_LETTERS = "ابتثجحخدذرزسشصضطظعغفقكلمنو"  # never ي (هاتفي = my phone)
TOKEN_PREFIX = "هاتف_"
# Digits with the separators people put inside a number; the regex is run on the folded text.
_CANDIDATE = re.compile(r"(?<![\d+])(?:\+|00)?\d(?:[\d \-.()]{6,20}\d)(?!\d)")
_MOBILE = re.compile(r"^(?:00964|964|0)?(7[3-9]\d{8})$")
TOKEN_RE = re.compile(rf"{TOKEN_PREFIX}[{TOKEN_LETTERS}]{{1,2}}")
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")  # one character for one


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    raw: str
    e164: str | None  # None: digits that look like a phone number but are not an Iraqi mobile


def canonical(raw: str | None) -> str | None:
    """'9647XXXXXXXXX', or None when `raw` is not an Iraqi mobile number."""
    digits = re.sub(r"\D", "", fold(str(raw or "")))
    match = _MOBILE.match(digits)
    return "964" + match.group(1) if match else None


# A WhatsApp account that hides its number behind a username (the webhook names only its user id) gets an account
# key in place of a number: «wa-user:» + a hash of that user id, written in letters only (no digit at all). So
# canonical() (and the wallet's canonical_phone) can never produce it, nor read a number out of it: nobody can send
# to it by number, and it never receives a hold or a notice meant for a real number.
USER_KEY_PREFIX = "wa-user:"
_HEX_AS_LETTERS = str.maketrans("0123456789", "ghijklmnop")


def user_key(user_id: str) -> str:
    """The account key of a username-only WhatsApp sender (its user id, hashed: stable, not dialable)."""
    digest = hashlib.sha256(("wa-user:" + str(user_id or "")).encode()).hexdigest()[:24]
    return USER_KEY_PREFIX + digest.translate(_HEX_AS_LETTERS)


def is_user_key(value: str | None) -> bool:
    """True for a username account's key («wa-user:…»), which is never a phone number."""
    return bool(value) and str(value).startswith(USER_KEY_PREFIX)


def dialable(e164: str | None) -> bool:
    """A number a message can be sent to (an account key of a username account is not)."""
    return bool(e164) and re.fullmatch(r"\d{7,15}", str(e164)) is not None


def display(e164: str | None) -> str:
    """'9647701234567' -> '0770 123 4567' (anything else is returned as it is)."""
    if not e164 or not re.fullmatch(r"9647\d{9}", e164):
        return e164 or ""
    local = "0" + e164[3:]
    return f"{local[:4]} {local[4:7]} {local[7:]}"


def last4(e164: str | None) -> str:
    if is_user_key(e164):
        return ""  # an account key's hex digits are no number's end
    return re.sub(r"\D", "", e164 or "")[-4:]


def local(e164: str | None) -> str:
    """'9647701234567' -> '07701234567' (the seed's and the wallet's stored form)."""
    return "0" + e164[3:] if e164 and e164.startswith("964") else (e164 or "")


def operator_of(e164_or_local: str | None) -> str | None:
    """zain (078/079), asiacell (077), korek (075), else None."""
    digits = re.sub(r"\D", "", str(e164_or_local or ""))
    if digits.startswith("964"):
        digits = "0" + digits[3:]
    return next((op for op, prefixes in OPERATOR_PREFIXES.items() if digits.startswith(prefixes)), None)


def find(text: str) -> list[Span]:
    """Every phone-shaped number in `text` (10+ digits once the separators are gone), in order.

    The positions are in `text` itself: only the digits are folded, one character for one. A run like
    «50000 07701234567» (an amount next to a number) is split into its groups first."""
    folded = (text or "").translate(_DIGITS)
    spans = []
    for m in _CANDIDATE.finditer(folded):
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) < 10:
            continue
        e164 = canonical(digits)
        if e164:
            spans.append(Span(m.start(), m.end(), text[m.start():m.end()], e164))
            continue
        inner = _split(folded, m.start(), m.end())
        if inner:
            spans += [Span(a, b, text[a:b], canonical(folded[a:b])) for a, b in inner]
        else:
            spans.append(Span(m.start(), m.end(), text[m.start():m.end()], None))
    return spans


def _split(folded: str, start: int, end: int) -> list[tuple[int, int]]:
    """Mobile numbers inside a longer run of digit groups, left to right, longest first."""
    groups = [(start + g.start(), start + g.end()) for g in re.finditer(r"\+?\d+", folded[start:end])]
    found, i = [], 0
    while i < len(groups):
        for j in range(len(groups) - 1, i - 1, -1):
            a, b = groups[i][0], groups[j][1]
            if canonical(folded[a:b]):
                found.append((a, b))
                i = j
                break
        i += 1
    return found


def token(index: int) -> str:
    """The placeholder for the index-th number: هاتف_ا, هاتف_ب, ... then two letters."""
    n = len(TOKEN_LETTERS)
    if index < n:
        return TOKEN_PREFIX + TOKEN_LETTERS[index]
    return TOKEN_PREFIX + TOKEN_LETTERS[index // n - 1] + TOKEN_LETTERS[index % n]


def mask(text: str) -> tuple[str, dict[str, str]]:
    """(text with each Iraqi mobile replaced by a placeholder, {placeholder: e164}).

    A phone-shaped number that is not an Iraqi mobile is left as it is (the agent asks about it)."""
    out, mapping, by_number = [], {}, {}
    pos = 0
    for span in find(text):
        if span.e164 is None:
            continue
        name = by_number.get(span.e164)
        if name is None:
            name = by_number[span.e164] = token(len(by_number))
            mapping[name] = span.e164
        lead = "" if span.start == 0 or text[span.start - 1].isspace() else " "
        out += [text[pos:span.start], lead, name]
        pos = span.end
    out.append(text[pos:])
    return "".join(out), mapping


def tokens_in(text: str) -> list[str]:
    return TOKEN_RE.findall(text or "")


def foreign_numbers(text: str) -> list[str]:
    """Phone-shaped numbers in `text` that are not Iraqi mobiles (to ask about, never to pay)."""
    return [span.raw for span in find(text) if span.e164 is None]


_TOKEN_LOOSE = re.compile(rf"هاتف[\s_]+([{TOKEN_LETTERS}]{{1,2}})(?![{TOKEN_LETTERS}])")


def token_of(mention: str | None) -> str | None:
    """The placeholder a payee mention points at («لـ هاتف_ا», «هاتف ا»), else None."""
    m = _TOKEN_LOOSE.search(fold(mention or "").replace("ـ", ""))
    return TOKEN_PREFIX + m.group(1) if m else None


_SPOKEN = {
    "صفر": "0", "zero": "0",
    "واحد": "1", "one": "1",
    "اثنين": "2", "اثنان": "2", "ثنين": "2", "two": "2",
    "ثلاثه": "3", "تلاثه": "3", "ثلاث": "3", "تلاث": "3", "three": "3",
    "اربعه": "4", "اربع": "4", "four": "4",
    "خمسه": "5", "خمس": "5", "five": "5",
    "سته": "6", "ست": "6", "six": "6",
    "سبعه": "7", "سبع": "7", "seven": "7",
    "ثمانيه": "8", "ثمنيه": "8", "ثمان": "8", "ثمن": "8", "eight": "8",
    "تسعه": "9", "تسع": "9", "nine": "9",
}


def _spoken_digit(word: str) -> tuple[str, str] | None:
    """(prefix, digit) for one spoken digit («سبعة», «لصفر» -> ("ل", "0"), «seven»), or a written digit run."""
    bare = fold(word).strip(".,،؛:!?؟").lower().replace("أ", "ا").replace("إ", "ا").replace("ة", "ه")
    if bare.isdigit():
        return "", bare
    if bare in _SPOKEN:
        return "", _SPOKEN[bare]
    if bare[:1] in ("ل", "و", "ب") and bare[1:] in _SPOKEN:
        return bare[0], _SPOKEN[bare[1:]]
    return None


def spoken_digits(text: str) -> str:
    """A voice transcript with a phone number said digit by digit («لصفر سبعة ثمانية واحد…») written as digits
    («ل07815556666»), so it is read as a number, not a name. Only a run of 10 to 13 digits changes, so an
    amount («خمسة وعشرين ألف») is left alone."""
    words = (text or "").split()
    out: list[str] = []
    i = 0
    while i < len(words):
        j, digits, prefix = i, "", ""
        while j < len(words):
            found = _spoken_digit(words[j])
            if found is None or (j > i and found[0]):
                break
            if j == i:
                prefix = found[0]
            digits += found[1]
            j += 1
        if 10 <= len(digits) <= 13 and j - i >= 3:
            out.append(prefix + digits)
            i = j
        else:
            out.append(words[i])
            i += 1
    return " ".join(out)
