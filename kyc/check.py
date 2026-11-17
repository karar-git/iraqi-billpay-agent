"""The decision on an identity document, in code. The vision model reads; this file decides.

`check(raw, typed_name=..., today=...)` takes the raw fields a vision model copied from the photo
(`kyc.schema.SCHEMA`) and returns a `Verdict`. The same input always gives the same verdict: no model is
asked whether the person is accepted, and nothing here reads a clock, a file or the network.

Statuses:
- "verified": every rule passed.
- "retry":    the photo could not be used (not a document, not readable, a field missing or malformed).
              The person can send another photo.
- "review":   the document reads well, but a human has to look (the name differs from the typed one, or the
              photo looks like a screen or a copy).
- "refused":  the document itself is not accepted (type, a specimen, age under 18, expired).

A national card prints the birth date and the expiry on its back, so a photo of the front alone may carry no birth
date. Then the reason is "back_needed" (a "retry" that is not a failed try): the channel asks for the back and calls
`check(front, back=...)` with both readings. The front gives the type, the name and the number; the back gives the
dates the front lacks; both must be the same card. The back's dates decide the age, so a back verifies only when it
carries the front's own number: a back with no tie to the front (another person's back, a back whose number could not
be read) goes to a person ("sides_unconfirmed"), and a licence or another type is "sides_differ". A front that prints
the birth date needs no back.

The rules run in a fixed order and the first "retry" or "refused" ends the check. `Verdict.checks` lists what
was checked and how it came out, with no personal data in it, so it can be logged and shown to a reviewer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from kyc.schema import DOCUMENT_TYPES, SCHEMA
from payments import arabizi, names
from payments.text import fold

MIN_AGE = 18
MAX_AGE = 120  # an older birth date is a misread, not a customer

ACCEPTED_TYPES = ("national_card", "passport", "residence_card")

# The number printed on each accepted type, after normalisation (Western digits, no spaces or dashes, capitals).
# - national_card: the Iraqi unified national card number, 12 digits.
# - passport: one Latin letter and 8 digits.
# - residence_card: 0 to 2 Latin letters, then 7 to 12 digits. A working rule for the demo: confirm the real
#   format with the issuing authority before production.
NUMBER_FORMATS = {
    "national_card": re.compile(r"\d{12}"),
    "passport": re.compile(r"[A-Z]\d{8}"),
    "residence_card": re.compile(r"[A-Z]{0,2}\d{7,12}"),
}
# A passport or a residence card always prints its expiry on the photographed page. The national card prints it
# on the back, so a photo of the front has none: there, a missing expiry is not a failure.
EXPIRY_REQUIRED = ("passport", "residence_card")

# Every reason code and the status it gives. Nothing else can come out of check().
REASONS: dict[str, str] = {
    "ok": "verified",
    "no_reading": "retry",          # the reader gave nothing, or something that is not the agreed shape
    "not_a_document": "retry",
    "unreadable": "retry",
    "bad_number": "retry",          # the number is missing or not in the format of that document type
    "name_missing": "retry",
    "bad_birth_date": "retry",      # missing, not a date, in the future, or older than MAX_AGE
    "bad_expiry_date": "retry",     # not a date, or missing on a type that always prints one
    "back_needed": "retry",         # a national card front with no birth date: the back is asked for (no try is lost)
    "sides_differ": "retry",        # the back is of another document than the front
    "type_not_accepted": "refused",
    "specimen": "refused",          # marked SPECIMEN / SAMPLE / نموذج; accepted only where allow_specimen is set
    "under_age": "refused",
    "expired": "refused",
    "name_mismatch": "review",
    "screen_or_copy": "review",
    "sides_unconfirmed": "review",  # a back that reads well but carries nothing that ties it to the front
    # Given by kyc.reader.verify before any model is called; check() itself never returns these two.
    "image_too_large": "retry",
    "image_type": "retry",
}
STATUSES = ("verified", "retry", "review", "refused")

# The user-facing text of each reason: a key of kyc.texts.TEXTS, in the three languages.
TEXT_KEYS: dict[str, str] = {reason: f"kyc_{reason}" for reason in REASONS}


@dataclass(frozen=True)
class Reading:
    """The cleaned fields of one document. A field that did not pass its own check is None."""

    document_type: str
    full_name_ar: str | None
    full_name_latin: str | None
    document_number: str | None  # normalised, and only when it has the format of its document type
    birth_date: date | None
    expiry_date: date | None
    sex: str | None              # "m" | "f" | None: read, never used for a decision
    screen_or_copy: bool
    notes: str = ""

    @property
    def name_on_document(self) -> str | None:
        return self.full_name_ar or self.full_name_latin


@dataclass(frozen=True)
class Verdict:
    status: str                      # "verified" | "retry" | "review" | "refused"
    reason: str                      # a key of REASONS
    reading: Reading | None
    checks: tuple[tuple[str, str], ...] = ()  # (what was checked, outcome): evidence, no personal data

    @property
    def text_key(self) -> str:
        return TEXT_KEYS[self.reason]


def _verdict(reason: str, reading: Reading | None, checks: list[tuple[str, str]]) -> Verdict:
    return Verdict(REASONS[reason], reason, reading, tuple(checks))


# ---- cleaning ----

_EMPTY = {"", "null", "none", "n/a", "-"}
_SEPARATORS = re.compile(r"[\s\-_./\\:]+")
_DATE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")
_BOOLEANS = ("is_identity_document", "readable", "looks_like_photo_of_screen_or_copy", "marked_as_specimen")
_TEXTS = ("full_name_ar", "full_name_latin", "document_number", "birth_date", "expiry_date", "sex", "notes")


def _well_formed(raw) -> bool:
    """The agreed shape, checked again here: the caller may not be the model layer that validates the schema."""
    if not isinstance(raw, dict) or any(key not in raw for key in SCHEMA["required"]):
        return False
    if any(not isinstance(raw[key], bool) for key in _BOOLEANS):
        return False
    if raw["document_type"] not in DOCUMENT_TYPES:
        return False
    return all(raw[key] is None or isinstance(raw[key], str) for key in _TEXTS)


def _text(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    return None if value.lower() in _EMPTY else value


def normalize_number(value: str | None) -> str:
    """A document number as compared and fingerprinted: Eastern digits read as Western, separators out, capitals."""
    return _SEPARATORS.sub("", fold(value or "")).upper()


def parse_date(value: str | None) -> date | None:
    """YYYY-MM-DD (Eastern digits and / accepted) as a real calendar date, else None. Day-first forms are not
    guessed: 03-04-2001 could be two different days."""
    match = _DATE.fullmatch(fold(value or "").strip())
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def age_on(birth: date, today: date) -> int:
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


# ---- names ----

_LATIN = re.compile(r"[A-Za-z]")
_ARTICLES = {"al", "el", "il"}


def _bare(word: str) -> str:
    """Without a leading ال when a name is left: «الموسوي» and «موسوي» are one family name."""
    return word[2:] if word.startswith("ال") and len(word) >= 5 else word


def _word_keys(word: str) -> frozenset[str]:
    """Everything one name word can be compared on: its Arabic spelling, or for a word in Latin letters the
    letters themselves and its Arabic readings (`payments.arabizi`)."""
    if not _LATIN.search(word):
        normal = names.normalize(word)
        return frozenset({_bare(normal)} if normal else ())
    latin = re.sub(r"[^a-z0-9]", "", word.lower())
    readings = (arabizi.name_word_readings(word) or [[]])[0]
    keys = {_bare(names.normalize(reading)) for reading in readings}
    return frozenset(key for key in keys | {latin} if key)


def name_words(name: str | None, *, drop_titles: bool = False) -> list[frozenset[str]]:
    """A name as a list of words, each a set of comparison keys. «عبد الله» is one word, «al saadi» is one word."""
    text = re.sub(r"[-ـ'’`.,،]", " ", name or "")
    text = re.sub(r"عبد\s+ال", "عبدال", text)
    raw = text.split()
    words: list[str] = []
    i = 0
    while i < len(raw):
        if raw[i].lower() in _ARTICLES and i + 1 < len(raw) and _LATIN.search(raw[i + 1]):
            words.append(raw[i + 1])  # the article is dropped on both sides
            i += 2
        else:
            words.append(raw[i])
            i += 1
    if drop_titles:
        words = [w for w in words if names.normalize(w) not in names.TITLES] or words
    return [keys for keys in map(_word_keys, words) if keys]


def names_match(typed_name: str | None, *on_document: str | None) -> bool:
    """The typed name against the name printed on the document (its Arabic line, its Latin line, or both).

    A match needs the first name to be equal and at least one more name part of the typed name to be one of the
    later parts on the document: «زينب هادي», «زينب السعدي» and «zainab kareem» all match «زينب هادي كريم السعدي».
    One typed word alone never matches, and a different first name never matches. A Latin word is compared through
    its Arabic readings, which cannot spell every letter (ع has no Latin letter): what does not match goes to a
    human, never to a refusal."""
    typed = name_words(typed_name, drop_titles=True)
    if len(typed) < 2:
        return False
    for line in on_document:
        printed = name_words(line)
        if len(printed) < 2 or not typed[0] & printed[0]:
            continue
        if any(part & other for part in typed[1:] for other in printed[1:]):
            return True
    return False


# ---- two sides of one card ----

def _sides(front: dict, back) -> tuple[dict | None, str | None, bool]:
    """The reading of a card from its two photos: (merged, None, tied), or (None, reason, False) when the back
    cannot be used.

    The front decides the type, the name and the number. The back must be a readable back of the same type: another
    type (a passport, a driving licence, "other") is "sides_differ", an unreadable back is "unreadable" (its dates are
    not trusted either). A back whose number has the front's format and is another number is "sides_differ".

    `tied` is True only when the back carries the front's own number (the unified card prints the 12-digit national
    number in its machine-readable lines too). A name is not a tie: a relative's card, named after the same
    grandfather, can share the first names and the family name, and the back's birth date is what the age rule reads.
    An untied back can still be refused (expired, specimen, under 18) but never verified: check() makes it a review.

    The back's names are never used for the typed-name rule (a back of someone else's card must not make a typed name
    match). A date the front prints wins over the back's. A mark of a specimen or of a screen or copy on either side
    counts."""
    if not _well_formed(back):
        return None, "no_reading", False
    kind = front["document_type"]
    if not back["is_identity_document"] or back["document_type"] == "none":
        return None, "not_a_document", False
    if back["document_type"] != kind:
        return None, "sides_differ", False
    if not back["readable"]:
        return None, "unreadable", False
    front_number = normalize_number(_text(front["document_number"]))
    back_number = normalize_number(_text(back["document_number"]))
    pattern = NUMBER_FORMATS.get(kind)
    in_format = bool(pattern and back_number and pattern.fullmatch(back_number))
    if in_format and back_number != front_number:
        return None, "sides_differ", False
    tied = in_format and back_number == front_number
    merged = dict(front)
    for key in ("birth_date", "expiry_date"):  # never a name: the name is matched on the front only
        if not _text(merged[key]) and _text(back[key]):
            merged[key] = back[key]
    for key in ("looks_like_photo_of_screen_or_copy", "marked_as_specimen"):
        merged[key] = bool(front[key] or back[key])
    return merged, None, tied


# ---- the decision ----

def check(raw: dict | None, *, typed_name: str, today: date, allow_specimen: bool = False,
          back: dict | None = None) -> Verdict:
    """Decide on one reading. `raw` is what the vision model returned (or None when it returned nothing),
    `typed_name` is the name the person typed at sign-up, `today` is the date the rules are applied on.

    `back` is the reading of the back of the same card, when "back_needed" asked for it: the two are decided as
    one card (see `_sides`); a back with no tie to the front is "sides_unconfirmed" at best. Without it a national card front with no birth date is "back_needed"; with it a
    missing birth date is "bad_birth_date" and a missing expiry is "bad_expiry_date" (the back prints both).

    `allow_specimen` is for demos and tests only: the images in data/kyc/ are all marked SPECIMEN, and a
    document with that mark is refused unless this is set."""
    checks: list[tuple[str, str]] = []
    if not _well_formed(raw):
        return _verdict("no_reading", None, [("reading", "missing_or_malformed")])
    checks.append(("reading", "ok"))
    two_sides, tied = back is not None, True
    if two_sides:
        merged, problem, tied = _sides(raw, back)
        if problem:
            return _verdict(problem, None, checks + [("back", problem)])
        raw = merged
        checks.append(("back", "ok" if tied else "unconfirmed"))

    kind = raw["document_type"]
    if not raw["is_identity_document"] or kind == "none":
        return _verdict("not_a_document", None, checks + [("document", "not_an_identity_document")])
    checks.append(("document", "ok"))

    number = normalize_number(_text(raw["document_number"]))
    number_ok = kind in NUMBER_FORMATS and bool(NUMBER_FORMATS[kind].fullmatch(number))
    birth = parse_date(_text(raw["birth_date"]))
    age = age_on(birth, today) if birth else None
    if birth and not 0 <= age <= MAX_AGE:
        birth = None
    expiry_text = _text(raw["expiry_date"])
    expiry = parse_date(expiry_text)
    sex = (_text(raw["sex"]) or "").lower()
    reading = Reading(
        document_type=kind,
        full_name_ar=_text(raw["full_name_ar"]),
        full_name_latin=_text(raw["full_name_latin"]),
        document_number=number if number_ok else None,
        birth_date=birth,
        expiry_date=expiry,
        sex=sex if sex in ("m", "f") else None,
        screen_or_copy=raw["looks_like_photo_of_screen_or_copy"],
        notes=(_text(raw["notes"]) or "")[:200],
    )

    if not raw["readable"]:
        return _verdict("unreadable", reading, checks + [("readable", "no")])
    checks.append(("readable", "ok"))

    if kind not in ACCEPTED_TYPES:
        return _verdict("type_not_accepted", reading, checks + [("type", kind)])
    checks.append(("type", kind))

    if raw["marked_as_specimen"] and not allow_specimen:
        return _verdict("specimen", reading, checks + [("specimen_mark", "present")])
    checks.append(("specimen_mark", "allowed" if raw["marked_as_specimen"] else "none"))

    if not number_ok:
        return _verdict("bad_number", reading, checks + [("number", "missing" if not number else "wrong_format")])
    checks.append(("number", "ok"))

    if not reading.name_on_document:
        return _verdict("name_missing", reading, checks + [("name_printed", "missing")])
    checks.append(("name_printed", "ok"))

    if birth is None:
        if kind == "national_card" and not two_sides and not _text(raw["birth_date"]):
            return _verdict("back_needed", reading, checks + [("birth_date", "on_the_back")])
        return _verdict("bad_birth_date", reading, checks + [("birth_date", "missing_or_invalid")])
    checks.append(("birth_date", "ok"))
    if age < MIN_AGE:
        return _verdict("under_age", reading, checks + [("age", f"under_{MIN_AGE}")])
    checks.append(("age", "ok"))

    if expiry is None and (expiry_text or kind in EXPIRY_REQUIRED or two_sides):
        return _verdict("bad_expiry_date", reading, checks + [("expiry", "invalid" if expiry_text else "missing")])
    if expiry is not None and expiry < today:
        return _verdict("expired", reading, checks + [("expiry", "past")])
    checks.append(("expiry", "ok" if expiry else "not_printed"))

    name_ok = names_match(typed_name, reading.full_name_ar, reading.full_name_latin)
    checks.append(("name", "ok" if name_ok else "differs_from_typed"))
    checks.append(("original", "screen_or_copy" if reading.screen_or_copy else "ok"))
    if not tied:  # first: the dates came from a photo with no tie to this card, so no click may verify it
        return _verdict("sides_unconfirmed", reading, checks)
    if not name_ok:
        return _verdict("name_mismatch", reading, checks)
    if reading.screen_or_copy:
        return _verdict("screen_or_copy", reading, checks)
    return _verdict("ok", reading, checks)
