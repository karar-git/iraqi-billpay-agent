"""Payee name resolver: match what the user said against their saved contacts.

Scores are in [0, 1]. Anything at or above THRESHOLD counts as a real match; fuzzy
matches are always below it, so a typo can only ever produce a "did you mean?" question.
So is a match on a later word of the saved name only (in Iraqi names that is the father's
name or the surname): "علي" never picks "حسن علي" without asking.
Relation words (ابني, امي, ...) resolve only through a saved alias.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from payments.text import fold

THRESHOLD = 0.6

SCORE_ALIAS = 1.0
SCORE_EXACT = 1.0
SCORE_PHONE = 0.95
SCORE_FIRST_NAME = 0.9  # mention is the start of the saved name: "احمد" -> "احمد علي"
SCORE_NAME_PART = 0.5  # below THRESHOLD: "علي" -> "حسن علي" (Hassan son of Ali) needs a question
SCORE_NICKNAME = 0.7  # one-to-many: "حمودي" -> محمد / احمد / محمود
FUZZY_MAX = 0.55  # always below THRESHOLD
FUZZY_MIN_SIMILARITY = 0.6


@dataclass(frozen=True)
class Candidate:
    contact_id: str
    display_name: str
    phone: str
    score: float
    how: str  # alias | exact | phone | first_name | name_part | nickname | fuzzy


def normalize(s: str) -> str:
    """Normalize an Arabic name for matching.

    أ/إ/آ->ا, ة->ه, ى->ي, گ/ك/ق->ك, چ->ج, no tatweel/diacritics/punctuation,
    "عبد الله" -> "عبدالله", single spaces.
    """
    s = fold(s or "").replace("ق", "ك")
    s = re.sub(r"[^\w\s]|_", " ", s)
    s = re.sub(r"\bعبد\s+ال", "عبدال", s)
    return re.sub(r"\s+", " ", s).strip()


def _norm_set(words: Iterable[str]) -> set[str]:
    return {normalize(w) for w in words}


RELATION_WORDS = _norm_set((
    "ابني", "بنتي", "اخوي", "اخويه", "اختي", "امي", "يمه", "ابوي", "بويه", "زوجي", "رجلي",
    "مرتي", "زوجتي", "خالي", "خالتي", "عمي", "عمتي", "جدي", "جدتي",
))
TITLES = _norm_set((
    "حجي", "الحجي", "حاج", "الحاج", "حجية", "الحجية", "ست", "الست", "استاذ", "الاستاذ",
    "استاذة", "الاستاذة", "دكتور", "الدكتور", "دكتورة", "الدكتورة", "اخ", "الاخ", "اخت", "الاخت",
))
NICKNAMES = {normalize(k): _norm_set(v) for k, v in {
    "حمودي": ("محمد", "أحمد", "محمود"),
    "حمود": ("محمد", "أحمد", "محمود"),
    "حمادي": ("محمد", "أحمد", "حماد"),
    "علاوي": ("علي",),
    "علوش": ("علي",),
    "حسوني": ("حسين", "حسن"),
    "حسون": ("حسين", "حسن"),
    "عبوسي": ("عباس",),
    "جسومي": ("جاسم",),
    "كظومي": ("كاظم",),
    "سجودي": ("سجاد",),
    "بسومي": ("باسم",),
    "رزوقي": ("عبد الرزاق", "رزاق"),
    "عبودي": ("عبد الله", "عبد الرحمن"),
    "عبود": ("عبد الله", "عبد الرحمن"),
    "سلومي": ("سلمان", "سليم", "سالم"),
    "مصطو": ("مصطفى",),
    "فطومة": ("فاطمة",),
    "فطوم": ("فاطمة",),
    "زوزو": ("زينب", "زهراء"),
    "مريومة": ("مريم",),
}.items()}


def _get(obj, name: str, default=None):
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _digits(s: str) -> str:
    return re.sub(r"\D", "", fold(s or ""))


def _levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def similarity(a: str, b: str) -> float:
    """1.0 for equal strings, falling with edit distance."""
    if not a or not b:
        return 0.0
    return 1 - _levenshtein(a, b) / max(len(a), len(b))


def _strip_dative(word: str) -> str:
    """'لأحمد' -> 'احمد', 'للحجي' -> 'الحجي'."""
    if word.startswith("لل"):
        return "ال" + word[2:]
    if word.startswith("ل") and len(word) > 2:
        return word[1:]
    return word


def _mention_variants(mention: str, strip_dative: bool) -> list[tuple[str, ...]]:
    """The mention as typed (and optionally without a leading ل), minus titles and relation words."""
    words = normalize(mention).split()
    if not words:
        return []
    raw = [words]
    if strip_dative:
        raw.append([_strip_dative(words[0])] + words[1:])
    variants = []
    for ws in raw:
        no_titles = [w for w in ws if w not in TITLES] or ws
        variants.append(tuple(no_titles))
        names_only = [w for w in no_titles if w not in RELATION_WORDS]
        if names_only and len(names_only) < len(no_titles):
            variants.append(tuple(names_only))  # "اخوي علي" -> "علي"
    return list(dict.fromkeys(variants))


def _nickname_ok(said: str, saved: str) -> bool:
    return saved in NICKNAMES.get(said, ()) or said in NICKNAMES.get(saved, ())


def _score(words: tuple[str, ...], name_words: list[str], phone: str) -> tuple[float, str]:
    said = " ".join(words)
    if not name_words:
        return 0.0, ""
    if said in RELATION_WORDS:
        return 0.0, ""  # only an alias can resolve a relation word
    digits = _digits(said)
    if len(digits) >= 4 and digits == said.replace(" ", ""):
        return (SCORE_PHONE, "phone") if _digits(phone).endswith(digits) else (0.0, "")
    if list(words) == name_words:
        return SCORE_EXACT, "exact"
    if list(words) == name_words[:len(words)]:
        return SCORE_FIRST_NAME, "first_name"
    if all(w in name_words for w in words):
        return SCORE_NAME_PART, "name_part"
    if _nickname_ok(words[0], name_words[0]) and all(w in name_words[1:] for w in words[1:]):
        return SCORE_NICKNAME, "nickname"
    sim = max(similarity(said, " ".join(name_words)), similarity(words[0], name_words[0]))
    if sim >= FUZZY_MIN_SIMILARITY:
        return round(FUZZY_MAX * sim, 3), "fuzzy"
    return 0.0, ""


def _rank(contacts: list, variants: list[tuple[str, ...]], alias_map: dict[str, set[str]]) -> list[Candidate]:
    found = []
    for c in contacts:
        cid = _get(c, "contact_id")
        name = _get(c, "display_name", "") or ""
        phone = _get(c, "phone", "") or ""
        name_words = [w for w in normalize(name).split() if w not in TITLES] or normalize(name).split()
        best, how = 0.0, ""
        for words in variants:
            if cid in alias_map.get(" ".join(words), ()):
                best, how = SCORE_ALIAS, "alias"
                break
            score, kind = _score(words, name_words, phone)
            if score > best:
                best, how = score, kind
        if best > 0:
            found.append(Candidate(cid, name, phone, best, how))
    return sorted(found, key=lambda c: (-c.score, c.display_name, c.contact_id))


def resolve(mention: str, contacts: Iterable, aliases: Mapping[str, str] | None = None) -> list[Candidate]:
    """Rank the user's contacts against a spoken/typed payee mention.

    contacts: dicts or objects with contact_id, display_name, phone and optional alias.
    aliases: extra saved aliases {alias word: contact_id}, e.g. {"امي": "c7"}.
    Returns candidates with score > 0, best first.
    """
    contacts = list(contacts)
    alias_map: dict[str, set[str]] = {}
    for c in contacts:
        if _get(c, "alias"):
            alias_map.setdefault(normalize(_get(c, "alias")), set()).add(_get(c, "contact_id"))
    for word, cid in (aliases or {}).items():
        alias_map.setdefault(normalize(word), set()).add(cid)

    # Read the mention as typed first, so a name like "لمى" is never cut to "مى".
    as_typed = _rank(contacts, _mention_variants(mention, strip_dative=False), alias_map)
    if confident(as_typed):
        return as_typed
    # "لأحمد" (to Ahmed): try again without the leading ل.
    return _rank(contacts, _mention_variants(mention, strip_dative=True), alias_map)


def is_relation_word(mention: str) -> bool:
    """True for "ابني", "لامي", "اختي" ... (they need a saved alias)."""
    return any(" ".join(v) in RELATION_WORDS for v in _mention_variants(mention, strip_dative=True))


def confident(candidates: list[Candidate], threshold: float = THRESHOLD) -> list[Candidate]:
    return [c for c in candidates if c.score >= threshold]


def needs_question(candidates: list[Candidate], threshold: float = THRESHOLD) -> bool:
    """True when zero or more than one candidate is a real match."""
    return len(confident(candidates, threshold)) != 1


def pick(candidates: list[Candidate], threshold: float = THRESHOLD) -> Candidate | None:
    """The single confident candidate, or None when we must ask."""
    sure = confident(candidates, threshold)
    return sure[0] if len(sure) == 1 else None
