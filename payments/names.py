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
    "سيد", "السيد", "سيدة", "السيدة",
))
# «ام سجاد», «ابو علي»: alone these two words say nothing about who, they are never a first name.
KUNYA_HEADS = _norm_set(("ام", "ابو"))
# A saved name that starts with one of these is matched as a whole in a voice note, never on its first word.
KIN_HEADS = KUNYA_HEADS | _norm_set(("اخو", "اخت", "ابن", "بنت", "مرت", "زوج"))
# Somebody's relative, in any person: «ابوك», «اخوج». No alias makes these a contact, only a question does.
RELATIVES = RELATION_WORDS | _norm_set((
    "ابوك", "ابوج", "امك", "امج", "اخوك", "اخوج", "اختك", "اختج", "ابنك", "ابنج", "بنتك", "بنتج", "زوجك", "زوجج",
    "مرتك", "خالك", "خالج", "عمك", "عمج", "جدك", "جدج", "ابوه", "ابوها", "امه", "امها", "اخوه", "اخوها", "اخته", "اختها",
))
# Names that end in the sound of ا and are written both ways: «هدى» / «هدا», «سلمى» / «سلما». Short on purpose: a final
# ى is also a common spelling of a final ي, so «على», «رانى», «رامى» are not read as «علا», «رانا», «راما».
ALIF_NAMES = frozenset((
    "هدى", "سلمى", "ليلى", "ندى", "منى", "لمى", "سهى", "نهى", "رؤى", "ضحى", "لبنى", "سلوى", "نجوى", "بشرى", "فدوى",
    "موسى", "عيسى", "مصطفى", "مرتضى", "مجتبى", "رضى",
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


# A pay verb, with or without a pronoun on it («دزلها», «حوله», «ابعثلي»). It is never somebody's name.
_VERB_STEMS = _norm_set(("دز", "ادز", "تدز", "ندز", "دزي", "دزو", "دزيت", "حول", "احول", "تحول", "نحول", "حولي", "حولو", "حولت",
                         "ابعث", "بعث", "ابعت", "تبعث", "نبعث", "ارسل", "ترسل", "نرسل", "ارسلت", "انطي", "نطي", "ادفع", "تدفع"))
_VERB_ENDS = ("", "ل", "لي", "له", "لها", "لك", "لج", "لهم", "لنا", "ه", "ها", "هم", "يله", "يلها", "يلي", "يلهم")
VERB_FORMS = frozenset({stem + end for stem in _VERB_STEMS for end in _VERB_ENDS} | {"send", "transfer", "pay", "wire"})


def is_verb_form(word: str) -> bool:
    """One normalized word that is a pay verb form, also with a leading و or ف: «دزلها», «فدزلها»."""
    word = (word or "").lower()
    return word in VERB_FORMS or (word[:1] in ("و", "ف") and word[1:] in VERB_FORMS)


def holds_verb_form(mention: str) -> bool:
    """A payee mention with a pay verb in it: the core pointed at the verb, not at a person."""
    return any(is_verb_form(w) for w in normalize(mention).split())


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


def _alif_reading(s: str) -> str:
    """The same words with the final ى of a name in ALIF_NAMES written ا: "هدى" -> "هدا" (one name, two spellings).

    Kept apart from normalize(), which reads ى as ي: a final ا and a final ي are different people (علا, علي)."""
    def reading(word: str) -> str:
        known = word in ALIF_NAMES or (word.startswith("ل") and word[1:] in ALIF_NAMES)
        return word[:-1] + "ا" if known else word

    return " ".join(reading(word) for word in s.split())


def _strip_dative(word: str) -> str:
    """'لأحمد' -> 'احمد', 'للحجي' -> 'الحجي'."""
    if word.startswith("لل"):
        return "ال" + word[2:]
    if word.startswith("ل") and len(word) > 2:
        return word[1:]
    return word


def _mention_variants(mention: str, strip_dative: bool) -> list[tuple[str, ...]]:
    """The mention as typed (and optionally without a leading ل), minus titles and relation words."""
    raw = []
    for reading in dict.fromkeys((mention, _alif_reading(mention))):
        words = normalize(reading).split()
        if words:
            raw.append(words)
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
        if len(words) == 1 and words[0] in KIN_HEADS:
            return SCORE_NAME_PART, "name_part"  # «دز لام خمسين»: whose mother? ask
        return SCORE_FIRST_NAME, "first_name"
    if all(w in name_words for w in words):
        return SCORE_NAME_PART, "name_part"
    if _nickname_ok(words[0], name_words[0]) and all(w in name_words[1:] for w in words[1:]):
        return SCORE_NICKNAME, "nickname"
    sim = max(similarity(said, " ".join(name_words)), similarity(words[0], name_words[0]))
    if sim >= FUZZY_MIN_SIMILARITY:
        return round(FUZZY_MAX * sim, 3), "fuzzy"
    return 0.0, ""


def _saved_readings(name: str) -> list[list[str]]:
    """A saved name as matching sees it, without titles: as normalized, and with a final ى read as ا."""
    readings = []
    for reading in dict.fromkeys((name, _alif_reading(name))):
        words = normalize(reading).split()
        readings.append([w for w in words if w not in TITLES] or words)
    return readings


def _rank(contacts: list, variants: list[tuple[str, ...]], alias_map: dict[str, set[str]]) -> list[Candidate]:
    found = []
    for c in contacts:
        cid = _get(c, "contact_id")
        name = _get(c, "display_name", "") or ""
        phone = _get(c, "phone", "") or ""
        readings = _saved_readings(name)
        best, how = 0.0, ""
        for words in variants:
            if cid in alias_map.get(" ".join(words), ()):
                best, how = SCORE_ALIAS, "alias"
                break
            for name_words in readings:
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


def spelled_by(word_readings: list[list[str]], contacts: Iterable) -> list[Candidate]:
    """The saved names a Latin mention can spell, each of its words with its possible readings, word by word. A saved
    name may go on after the mention, as a first name may. Every one comes back below THRESHOLD: this only says who a
    question has to name, it never makes anybody a match."""
    if not word_readings:
        return []
    options = [{normalize(r) for r in readings} for readings in word_readings]
    options[0] |= {_strip_dative(r) for r in options[0]}  # «lzaid» is to Zaid

    def spelled(name_words: list[str]) -> bool:
        return len(name_words) >= len(options) and all(w in readings for w, readings in zip(name_words, options))

    def spells(name: str) -> bool:  # with a saved title too: «sayed ali» spells سيد علي
        return any(spelled(words) for words in (*_saved_readings(name), normalize(name).split()))

    return [Candidate(_get(c, "contact_id"), _get(c, "display_name", ""), _get(c, "phone", "") or "", FUZZY_MAX, "fuzzy")
            for c in contacts if spells(_get(c, "display_name", "") or "")]


def is_title(word: str) -> bool:
    """A title, on its own or after a leading ل: «سيد», «لسيد»."""
    word = normalize(word)
    return word in TITLES or _strip_dative(word) in TITLES


def title_not_saved(reading: str, saved: str) -> bool:
    """A reading with a word resolve() drops as a title that the saved name does not have in that place, before or after
    the same name words. In Arabic script the user wrote the title; from Latin letters it is only how a name read: «ali
    sayed» is not «علي حسن», nor «السيد علي حسن», whose title stands elsewhere, and «ukht ali» is not «علي»."""
    def places(words: list[str]) -> set[tuple[str, int]]:
        return {(w.removeprefix("ال"), sum(x not in TITLES for x in words[:i])) for i, w in enumerate(words) if w in TITLES}

    said = normalize(reading).split()
    if said and is_title(said[0]):  # «lsayed ali»
        said[0] = said[0] if said[0] in TITLES else _strip_dative(said[0])
    return not places(said) <= places(normalize(saved).split())


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
