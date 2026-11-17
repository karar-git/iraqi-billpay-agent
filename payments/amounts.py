"""Iraqi amount parser: spoken or typed amounts -> integer IQD.

Iraqi conventions handled here:
- a bare number under 1,000 means thousands of dinars (خمسين -> 50,000, ميتين -> 200,000);
- scales ألف / تالاف / مليون, fractions ربع / نص (ربع مليون, مليون ونص, مليون الا ربع);
- a fraction after a round hundred is a fraction of that hundred (مية ونص -> 150,000);
- after مليون a bare tail is in thousands (مليون وميتين -> 1,200,000);
- Western and Eastern digits with , or ٬ separators (50,000 / ٥٠٬٠٠٠ / 50 الف);
- dollar words and other currencies are never converted: they always lead to a question;
- a hedge before or after the number (حوالي خمسين, ميتين بحدود, اكثر من خمسين, خمسين او اكثر), a range
  (خمسين لستين) and two amounts joined by او / لو (خمسين او ستين) lead to a question;
- a small tail after ألف (خمسين الف وعشرين: 50,020 or 70,000?) leads to a question;
- ال on a number that opens the amount is its value (الربع مليون = 250,000, الخمسين الف = 50,000); after another
  word it may end a saved name («حيدر السبع مليون»), so it leads to a question;
- a word that makes a scale vague or changes its value in a way we do not compute (كم مليون, ثلثين مليون, تلت مليون,
  مليون وشوية, مليون وثلث, or a misspelt count like خمسن الف) leads to a question, never to the bare scale.
  The orchestrator is stricter still: see bare_scale_after_unknown.

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
AMBIGUOUS = "ambiguous"  # خمسين الف وعشرين: 50,020 or 70,000?


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
    "اثنين": 2, "ثنين": 2, "اثنان": 2, "اثنتين": 2, "ثنتين": 2, "تنين": 2, "اثنا": 2, "اثني": 2, "اتنين": 2,
    "ثلاث": 3, "ثلاثة": 3, "تلاث": 3, "تلاثة": 3, "تلاته": 3, "ثلاتة": 3, "تلتة": 3,
    "اربع": 4, "اربعة": 4,
    "خمس": 5, "خمسة": 5,
    "ست": 6, "ستة": 6,
    "سبع": 7, "سبعة": 7,
    "ثمن": 8, "ثمان": 8, "ثماني": 8, "ثمانية": 8, "ثمنية": 8, "تمنية": 8, "تمانية": 8, "تمن": 8,
    "تسع": 9, "تسعة": 9,
})
TEENS = _table({
    "عشر": 10, "عشرة": 10,
    "احدعش": 11, "حدعش": 11, "احدعشر": 11,
    "اثنعش": 12, "ثنعش": 12, "اثنعشر": 12,
    "تلطعش": 13, "ثلطعش": 13, "ثلاثطعش": 13, "تلتعش": 13,
    "اربطعش": 14, "اربعطعش": 14, "اربعتعش": 14, "اربعطعشر": 14,
    "خمسطعش": 15, "خمستعش": 15, "خمطعش": 15, "خمصطعش": 15,
    "ستطعش": 16, "سطعش": 16, "ستعش": 16,
    "سبعطعش": 17, "سبعتعش": 17,
    "ثمنطعش": 18, "ثمانطعش": 18, "تمنطعش": 18, "ثمنتعش": 18,
    "تسعطعش": 19, "تسعتعش": 19,
})
# Teens written as one word: a head for the unit and a tail for ten («خمسطعشر», «تلطعش», «حدعشر»).
TEEN_HEADS = _table({
    "تل": 13, "ثل": 13, "تلت": 13, "تلث": 13, "ثلث": 13, "تلاث": 13, "ثلاث": 13, "تلات": 13, "ثلات": 13,
    "ارب": 14, "اربع": 14,
    "خم": 15, "خمس": 15, "خمص": 15,
    "س": 16, "ست": 16,
    "سبع": 17,
    "ثمن": 18, "ثمان": 18, "تمن": 18, "تمان": 18,
    "تسع": 19,
})
TEEN_TAILS = tuple(fold(t) for t in ("طعشر", "طعش", "تعشر", "تعش"))
SHORT_TEENS = _table({"احد": 11, "حد": 11, "اثن": 12, "ثن": 12, "اتن": 12})  # with «عشر» / «عش»: «حدعشر», «اثنعش»
SPLIT_TEEN_TAILS = {*TEEN_TAILS, fold("عش")}  # «خمس طعش», written apart
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
    "مئتا": 200, "مائتا": 200, "مئتي": 200, "مائتي": 200,  # MSA construct state: «مئتا ألف» = 200,000
})
_HUNDRED_SUFFIXES = [fold(s) for s in ("مية", "مئة", "مائة", "ميت")]
_HUNDRED_PREFIXES = _table({
    "ثلاث": 3, "تلاث": 3, "ثلث": 3, "تلث": 3, "تلت": 3, "تلات": 3, "ثلات": 3, "اربع": 4, "خمس": 5, "ست": 6,
    "سبع": 7, "ثمن": 8, "ثمان": 8, "تمن": 8, "تمان": 8, "تسع": 9,
})
SCALES = _table({
    "الف": THOUSAND, "alf": THOUSAND, "k": THOUSAND, "thousand": THOUSAND,
    "مليون": MILLION, "million": MILLION, "milyon": MILLION, "malyon": MILLION, "malyoon": MILLION, "mil": MILLION,
})
# Past what a wallet can send: asked about, never dropped («خمسين مليار» is not 50,000).
HUGE_SCALES = set(_table({w: 1 for w in (
    "مليار", "مليارات", "ملايير", "بليون", "بلايين", "تريليون", "ترليون", "billion", "billions", "trillion", "trillions")}))
PLURAL_SCALES = _table({
    "آلاف": THOUSAND, "تالاف": THOUSAND, "تلاف": THOUSAND, "الوف": THOUSAND,
    "ملايين": MILLION, "ملاين": MILLION,
})
DUAL_SCALES = _table({"الفين": THOUSAND, "الفان": THOUSAND, "مليونين": MILLION, "مليونان": MILLION})
# MSA forms read by their neighbours (see _tokenize): after a count they are the accusative «خمسون ألفاً»
# (a scale); alone they are the dual construct «ألفا دينار» / oblique «ألفي دينار» (2,000). A lone «ألفا»
# with no tanween and no دينار after it could be either (1,000 or 2,000): we ask.
CONSTRUCT_SCALES = _table({"الفا": THOUSAND, "الفي": THOUSAND, "مليونا": MILLION, "مليوني": MILLION})
TANWEEN_FATH = "\u064b"
FRACTIONS = _table({"نص": Fraction(1, 2), "نصف": Fraction(1, 2), "ربع": Fraction(1, 4)})
QUARTERS = set(_table({"ارباع": 1, "رباع": 1}))  # "ثلاث ارباع مليون" = three quarters of a million
# 3 written so that it reads a third alone («تلت», «ثلث»): before ارباع or a plural scale it can only be 3.
THREE_OR_A_THIRD = set(_table({w: 1 for w in ("ثلث", "تلث", "تلت", "تلات", "ثلات")}))
IQD_WORDS = set(_table({w: 1 for w in (
    "دينار", "دنانير", "دينارات", "عراقي", "iqd", "dinar", "dinars")}))
USD_WORDS = set(_table({w: 1 for w in (
    "دولار", "دولارات", "دولارين", "الدولار", "بالدولار", "دولاري", "بالدولارات", "ورقة", "ورقتين", "ورقات", "اوراق",
    "دفتر", "دفترين", "دفاتر", "$", "usd", "dollar", "dollars", "dolar", "buck", "bucks")}))
# Any other currency next to a number: never read as dinars. Asked about in the same way as dollars. These do not
# start an amount on their own («دز ريال خمسين»: ريال is a name), only a number before them makes them money.
FOREIGN_WORDS = set(_table({w: 1 for w in (
    "يورو", "اليورو", "باليورو", "ريال", "الريال", "بالريال", "ريالات", "درهم", "الدرهم", "بالدرهم", "دراهم",
    "تومان", "بالتومان", "جنيه", "الجنيه", "بالجنيه", "ليرة", "بالليرة", "ليرات", "باوند", "euro", "euros",
    "riyal", "riyals", "pound", "pounds", "dirham", "dirhams", "lira", "toman", "tomans")}))
FOREIGN_DINARS = set(_table({w: 1 for w in ("اردني", "كويتي", "اماراتي", "بحريني", "جزائري", "تونسي", "ليبي")}))
APPROX_WORDS = set(_table({w: 1 for w in (
    "حوالي", "تقريبا", "تقريبن", "بحدود", "حدود", "شي", "فدشي", "يمكن", "قرابة", "نحو", "زهاء", "يقارب",
    "اقل", "اكثر", "اكبر", "اصغر", "فوق", "فوك", "تحت", "وفوق", "وفوك", "وتحت", "واكثر", "واقل", "بالكثير", "بالقليل",
    "الاقل", "الاكثر")}))
# Words that offer a choice between two amounts: "خمسين او ستين", "خمسين لو ستين".
OR_WORDS = set(_table({w: 1 for w in ("او", "أو", "لو", "ولا", "والا", "وإلا")}))
# «فد مليون» is «a million»; «فد خمسين الف» is «fifty or so»: before a number it is a hedge.
A_OR_ABOUT = fold("فد")
THAN = fold("من")  # «اكثر من خمسين»
# After ألف, a tail under this many dinars is not how Iraqis say an amount (see _evaluate).
SMALL_TAIL = 100
# A number right after these words is a month, an account or a phone, not money.
NOT_AMOUNT_AFTER = set(_table({w: 1 for w in (
    "شهر", "الشهر", "لشهر", "رقم", "الرقم", "برقم", "حساب", "الحساب",
    "سنة", "السنة", "ساعة", "الساعة", "يوم", "تاريخ")}))
# "ثمن" also means "price": count it only when a scale or مية follows.
NEEDS_SCALE_AFTER = {fold("ثمن"), fold("تمن")}  # «تمن» is also rice
# A count we cannot read: the scale after it is NOT one of that scale («كم مليون» is not 1,000,000).
# كم = a few, ثلث / ثلثين = a third / two thirds. Tight on purpose: a name may stand right before a scale
# («دز لعلي الحلاق مليون»), so an unknown word alone is no reason to ask.
VAGUE_COUNTS = set(_table({w: 1 for w in (
    "كم", "بكم", "كام", "كمن", "جم", "جمن", "عدة", "بضع", "بضعة", "كذا", "شوية", "شويات",
    "ثلث", "تلث", "تلت", "ثلثين", "تلثين", "ثلثي", "ثلثا", "تلتين", "الثلث", "التلث", "التلت", "الثلثين", "التلثين",
    "ربعين", "نصين", "سدس", "السدس", "اخماس", "اسداس", "اثلاث", "اتلات", "ارباع", "رباع")}))
# «مليون وشوية», «مية الف وكسور», «مليون وثلث»: more than the number said, by an amount we do not compute.
VAGUE_TAILS = set(_table({w: 1 for w in (
    "وكم", "وكام", "وجم", "وشوية", "وشويات", "وكسر", "وكسور", "وشي", "ونيف",
    "وثلث", "وتلث", "وتلت", "وثلثين", "وتلثين", "وتلتين", "وثلثي", "والثلث", "والتلت", "وسدس")}))
# The same words after a separate و («مليون و شوية») or right after the amount («مليون شوية»).
VAGUE_AFTER = set(_table({w: 1 for w in (
    "كم", "كام", "جم", "شوية", "شويات", "كسر", "كسور", "نيف", "ثلث", "تلث", "تلت", "ثلثين", "تلثين", "تلتين", "ثلثي",
    "الثلث", "التلت", "سدس")}))
# «الست» is a title («دز الست مليون» = send the lady a million), «الثمن» is the price: with ال they are not numbers.
NOT_A_NUMBER_WITH_ARTICLE = {fold("ست"), fold("ثمن")}
ARTICLES = (fold("وال"), fold("بال"), fold("ال"))
# Counts long enough that one wrong letter still shows what was meant («خمسن الف» is not 1,000). Shorter ones sit
# one letter from a name (اثنين / ثنين next to بنين, مئتا next to مينا), so they stay out: a name asks nothing.
NEAR_MISS_COUNTS = (*TENS, *(k for k in TEENS if len(k) >= 5), *(k for k in HUNDREDS if len(k) >= 5))
# A word ending like a teen we could not read, right before a scale.
TEENISH_ENDINGS = (*TEEN_TAILS, fold("عشر"))
# After a scale «واحد» only says «one» («مليون واحد»). Next to another number, or after «كل», it is «each» or «unit».
ONE_WORD = fold("واحد")
ONE_WORDS = {ONE_WORD, fold("وحدة")}
EACH_WORDS = set(_table({w: 1 for w in ("كل", "لكل", "بكل")}))
# Words of no meaning between the parts of an amount: in English «half a million» reads «نص ا مليون», «of» reads «اف».
FILLERS = {"ا", fold("اف")}
# Before a bare scale, the only words that say nothing about the count (see bare_scale_after_unknown).
BEFORE_BARE_SCALE_OK = set(_table({w: 1 for w in (
    "ابو", "خلي", "خليها", "خليه", "خلوها", "اريد", "ابي", "ارغب", "اود", "اكو", "هاي",
    "هذا", "هو", "هي", "بس", "فقط", "بالضبط", "يعني", "فد", "اي", "نعم", "لا", "مو", "ما", "من", "على", "الى", "ل", "ب",
    "مبلغ", "المبلغ", "بمبلغ", "قيمة", "بقيمة", "فلوس", "رصيد", "الرصيد", "كارت", "كرت", "زين", "اسيا", "اسياسيل",
    "كورك", "خطي", "لخطي", "رقمي", "لرقمي", "هم", "هسه", "اليوم", "باجر", "بسرعة", "رجاء", "لطفا", "هاتف", "دينار",
    "ان", "أن", "إلى")}))
# A pay verb in any form («دزلها», «حولوا», «تحويل», «اشحنلي»).
PAY_STEMS = tuple(fold(w) for w in (
    "دز", "ادز", "تدز", "حول", "احول", "تحول", "ابعث", "بعث", "ارسل", "ارسال", "ترسل", "انطي", "ادفع", "دفع", "سدد",
    "خلص", "اشحن", "شحن", "عبي"))
# Top-up cards are named by value: "ابو العشرة" = the 10 (thousand) card.
CARD_PREFIX = fold("ابو")
AND = "و"
MINUS = fold("الا")
# MSA «أحد عشر» / «إحدى عشرة» = 11. Alone, أحد is «someone» («ماكو أحد»), never an amount.
ELEVEN_HEADS = {fold("احد"), fold("احدى")}

_NUMERIC = {"unit", "teen", "ten", "hundred", "digits"}
_STARTS = _NUMERIC | {"scale", "plural", "dual", "frac", "usd", "bad_digits", "unclear_scale", "huge"}
_INSIDE = _STARTS | {"and", "iqd", "minus", "foreign"}
_AMOUNT_KINDS = _NUMERIC | {"scale", "plural", "dual", "frac"}

_DIGIT = "[0-9٠-٩۰-۹]"
_SEPARATORS = ".,٫٬"
_TOKEN = re.compile(
    rf"(?<![\w{_SEPARATORS}])[{_SEPARATORS}]{_DIGIT}+"  # «.5»: never read as 5
    rf"|(?![0٠۰]){_DIGIT}{{1,3}}(?: {_DIGIT}{{3}})*(?: [0٠۰]{{3}})(?!{_DIGIT})"  # «1 000 000», «50 000»
    rf"|{_DIGIT}+(?:[.,٫٬]{_DIGIT}+)*"  # digits with separators
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


def _teen_of(word: str) -> int | None:
    """«خمسطعشر» -> 15, «تلطعش» -> 13, «حدعشر» -> 11: any head with any teen tail."""
    for tail in TEEN_TAILS:
        if word.endswith(tail) and word[: -len(tail)] in TEEN_HEADS:
            return TEEN_HEADS[word[: -len(tail)]]
    for tail in (fold("عشر"), fold("عش")):
        if word.endswith(tail) and word[: -len(tail)] in SHORT_TEENS:
            return SHORT_TEENS[word[: -len(tail)]]
    return None


def _classify_word(word: str) -> tuple[str, object] | None:
    """Kind and value of one folded word, or None if it is not an amount word."""
    if word in UNITS:
        return "unit", UNITS[word]
    if word in TEENS:
        return "teen", TEENS[word]
    if (teen := _teen_of(word)) is not None:
        return "teen", teen
    if word in TENS:
        return "ten", TENS[word]
    if word in HUNDREDS:
        return "hundred", HUNDREDS[word]
    for suffix in _HUNDRED_SUFFIXES:
        if word.endswith(suffix) and word[: -len(suffix)] in _HUNDRED_PREFIXES:
            return "hundred", _HUNDRED_PREFIXES[word[: -len(suffix)]] * 100
    if word in HUGE_SCALES:
        return "huge", None
    if word in SCALES:
        return "scale", SCALES[word]
    if word in PLURAL_SCALES:
        return "plural", PLURAL_SCALES[word]
    if word in DUAL_SCALES:
        return "dual", DUAL_SCALES[word]
    if word in CONSTRUCT_SCALES:
        return "construct", CONSTRUCT_SCALES[word]
    if word in FRACTIONS:
        return "frac", FRACTIONS[word]
    if word in IQD_WORDS:
        return "iqd", None
    if word in USD_WORDS:
        return "usd", None
    if word in FOREIGN_WORDS:
        return "foreign", None
    return None


def _classify(word: str) -> tuple[str, object]:
    if word[0] in _SEPARATORS:
        return "bad_digits", None
    if word[0].isdigit():
        word = word.replace(" ", "")  # «1 000 000»
        if re.fullmatch(r"0{2,}", word):
            return "bad_digits", None  # «1000 000»: groups that do not make a number we can read
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


def _one_edit(a: str, b: str) -> bool:
    """True when two different words are one inserted, deleted, replaced or swapped letter apart."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        wrong = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        if len(wrong) == 2 and wrong[1] == wrong[0] + 1:  # «خميسن» for «خمسين»
            return a[wrong[0]] == b[wrong[1]] and a[wrong[1]] == b[wrong[0]]
        return len(wrong) == 1
    short, long = sorted((a, b), key=len)
    return any(long[:i] + long[i + 1:] == short for i in range(len(long)))


def _near_miss_count(word: str) -> bool:
    """«خمسن», «بخمسن»: a misspelt count, read by nobody as a name."""
    bare = word[1:] if word[0] in (AND, "ب") else word
    return any(_one_edit(w, form) for form in NEAR_MISS_COUNTS for w in {word, bare})


def _with_article(word: str) -> tuple[str, object, str] | None:
    """«الربع», «بالخمسين» -> (kind, value, the bare word), when the word after ال is a number or a fraction."""
    for article in ARTICLES:
        if word.startswith(article) and len(word) > len(article) + 1:
            bare = word[len(article):]
            found = _classify_word(bare)
            if found and found[0] in ("unit", "teen", "ten", "hundred", "frac"):
                return found[0], found[1], bare
            return None
    return None


def _says_nothing_of_the_count(word: str) -> bool:
    return word in BEFORE_BARE_SCALE_OK or word.startswith(PAY_STEMS)


def is_number_with_article(word: str) -> bool:
    """«السبع», «العشرة»: a number with ال, as the end of a saved name may be."""
    return _with_article(fold(word)) is not None


def _read_around_scales(tokens: list[_Token]) -> None:
    """The words next to a number that would otherwise be dropped, leaving a wrong amount.

    Read them when the meaning is plain («الربع مليون» = 250,000), else mark them so the phrase asks."""
    for i, tok in enumerate(tokens):
        prev = tokens[i - 1] if i else None
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if tok.kind != "other":
            continue
        joined = prev is not None and prev.kind == "and" and i >= 2 and tokens[i - 2].kind in _AMOUNT_KINDS
        after_amount = prev is not None and prev.kind in _AMOUNT_KINDS
        before_scale = nxt is not None and nxt.kind in ("scale", "plural", "huge")
        if (tok.word in VAGUE_TAILS and after_amount) or (tok.word in VAGUE_AFTER and (after_amount or joined)):
            tok.kind = "unclear_scale"
        elif tok.word in THREE_OR_A_THIRD and nxt is not None and nxt.kind == "plural":
            tok.kind, tok.value = "unit", 3  # «تلت تلاف»: a third of thousands is not a thing people say
        elif tok.word in VAGUE_COUNTS and nxt is not None and (before_scale or nxt.kind in _NUMERIC):
            tok.kind = "unclear_scale"
        elif (article := _with_article(tok.word)) is not None:
            kind, value, bare = article
            before_hundred = kind == "unit" and nxt is not None and nxt.kind == "hundred" and nxt.value == 100
            if after_amount:
                tok.kind = "unclear_scale"  # «مليون والنص»: ask rather than leave the half out
            elif before_scale or before_hundred:
                # A number with ال after another word may end a saved name («لحيدر السبع مليون»); a fraction does not.
                # The orchestrator takes the payee out of the line first, so a name it knows is no reason to ask here.
                # A unit before a scale stays a question: «السبع» is a nickname, and nobody says «the seven million».
                maybe_name = kind != "frac" and prev is not None and prev.kind == "other" \
                    and not _says_nothing_of_the_count(prev.word)
                nickname = kind == "unit" and prev is not None and before_scale and nxt.kind == "scale"
                if bare in NOT_A_NUMBER_WITH_ARTICLE or maybe_name or nickname:
                    tok.kind = "unclear_scale"
                else:
                    tok.kind, tok.value = kind, value
        elif nxt is not None and nxt.kind == "scale" and (_near_miss_count(tok.word)
                                                          or tok.word.endswith(TEENISH_ENDINGS)):
            tok.kind = "unclear_scale"


def _read_one_words(tokens: list[_Token]) -> None:
    """«واحد» / «وحدة» count as 1 only alone, before a scale, or joined by و («واحد وعشرين»)."""
    for i, tok in enumerate(tokens):
        if tok.kind != "unit" or tok.word not in ONE_WORDS:
            continue
        prev = tokens[i - 1] if i else None
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if prev is not None and prev.word in EACH_WORDS:
            tok.kind = "unclear_scale"  # «كل واحد خمسين»: each, so no amount for the whole
        elif nxt is not None and nxt.kind == "teen" and nxt.value == 10:
            continue  # «واحد عشر», and MSA «مئة وأحد عشر», which folds to the same letters
        elif prev is not None and prev.kind in _NUMERIC:
            tok.kind = "unclear_scale"  # «خمسين وحدة»: fifty units
        elif prev is not None and prev.kind in ("scale", "plural", "dual"):
            tok.kind = "iqd" if tok.word == ONE_WORD else "unclear_scale"  # «مليون واحد» is just «one million»
        elif nxt is not None and not nxt.word.startswith(AND) and (
                nxt.kind in ("ten", "digits") or (nxt.kind == "teen" and nxt.value != 10)):
            tok.kind = "unclear_scale"  # «واحد خمسين»


def _tokenize(text: str) -> list[_Token]:
    tokens = []
    for m in _TOKEN.finditer(text):
        word = fold(m.group())
        if word in FILLERS:
            continue
        kind, value = _classify(word)
        tokens.append(_Token(kind, value, word, m.start(), m.end()))
    for i, tok in enumerate(tokens):  # «خمسون ألفاً» (a scale) or «ألفا دينار» / «ألفي دينار» (two thousand)
        if tok.kind != "construct":
            continue
        prev = tokens[i - 1] if i else None
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if prev is not None and prev.kind in _NUMERIC:
            tok.kind = "scale"
        elif tok.word.endswith("ي") or (nxt is not None and nxt.kind == "iqd"):
            tok.kind = "dual"
        elif TANWEEN_FATH in text[tok.start:tok.end]:
            tok.kind = "scale"  # «ألفاً»: one thousand
        else:
            tok.kind = "unclear_scale"
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
        elif tok.kind == "other" and tok.word in TEEN_HEADS and (
                nxt.word in SPLIT_TEEN_TAILS or (nxt.kind == "teen" and nxt.value == 10 and len(tok.word) >= 3)):
            tok.kind, tok.value = "teen", TEEN_HEADS[tok.word]  # «تلت طعش», «تلت عشر» written apart
            nxt.kind = "iqd"
        elif tok.kind == "unit" and tok.value < 10 and nxt.word in SPLIT_TEEN_TAILS:
            tok.kind, tok.value = "teen", tok.value + 10  # «خمس طعش» written apart
            nxt.kind = "iqd"
        elif tok.kind == "other" and tok.word in THREE_OR_A_THIRD and nxt.word in QUARTERS:
            tok.kind, tok.value = "frac", Fraction(3, 4)  # «تلت ارباع»
            nxt.kind = "iqd"
        elif tok.kind == "other" and tok.word in ELEVEN_HEADS and nxt.kind == "teen" and nxt.value == 10:
            tok.kind, tok.value = "unit", 1  # «أحد عشر» -> 1 + 10
        elif tok.kind == "unit" and nxt.word in QUARTERS:
            tok.kind, tok.value = "frac", Fraction(tok.value, 4)  # "ثلاث ارباع" -> 3/4
            nxt.kind = "iqd"  # a filler word inside the amount phrase
    for i, tok in enumerate(tokens):  # «مية دينار كويتي»
        if tok.kind == "other" and tok.word in FOREIGN_DINARS and i and tokens[i - 1].kind == "iqd":
            tok.kind = "foreign"
    _read_one_words(tokens)
    _read_around_scales(tokens)
    for i, tok in enumerate(tokens):
        prev = tokens[i - 1] if i else None
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if tok.kind in _NUMERIC and prev and prev.word in NOT_AMOUNT_AFTER:
            tok.kind = "other"
        elif tok.word in NEEDS_SCALE_AFTER and not (nxt and nxt.kind in ("scale", "plural", "hundred")):
            tok.kind = "other"
        elif tok.kind == "minus":
            article = _with_article(nxt.word) if nxt is not None and nxt.kind == "other" else None
            if article and article[0] == "frac" and article[1] == Fraction(1, 4):
                nxt.kind, nxt.value = "frac", article[1]  # «مليون الا الربع»
            elif not (nxt and nxt.kind == "frac" and nxt.value == Fraction(1, 4)):
                # «مليون الا ثلث», «مليون الا شوية»: a minus we cannot compute must not be lost
                tok.kind = "unclear_scale" if prev is not None and prev.kind in _AMOUNT_KINDS else "other"
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


def _missing_and(run: list[_Token]) -> bool:
    """Two numbers side by side with no و: «عشرين خمسة», «مية خمسين». Arabic joins the parts of a number with و, so
    this may be a self-correction. «خمس مية» and «خمسة عشر» are one number."""
    for a, b in zip(run, run[1:]):
        if b.word.startswith(AND) or b.kind not in ("unit", "teen", "ten", "hundred"):
            continue
        if a.kind in ("teen", "ten", "hundred") or (a.kind == "unit" and b.kind == "ten"):
            return True
    return False


def _evaluate(run: list[_Token]) -> Amount:
    toks = [t for t in run if t.kind not in ("and", "iqd")]
    if any(t.kind in ("usd", "foreign") for t in toks):  # any money that is not dinars: asked in dinars
        return _ask(USD, "USD")
    if any(t.kind == "bad_digits" for t in toks):
        return _ask(BAD_DIGITS)
    if any(t.kind == "huge" for t in toks):
        return _ask(TOO_LARGE)
    if any(t.kind == "unclear_scale" for t in toks) or _missing_and(run):
        return _ask(UNCLEAR)
    total, group, last_scale, seen_fraction = Fraction(0), _Group(), None, False
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
            if seen_fraction:  # «نص مليون ونص», «مليون وربع ونص»
                return _ask(UNCLEAR)
            seen_fraction = True
            if not group.empty or (frac > 0 and nxt == "scale"):  # خمسة ونص / نص مليون
                if last_scale is not None and group.empty:  # «مليون ونص الف»
                    return _ask(UNCLEAR)
                if not group.add_fraction(frac):
                    return _ask(UNCLEAR)
            elif last_scale:  # مليون ونص / مليون الا ربع
                if last_scale == THOUSAND and total >= 100 * THOUSAND:  # «مية الف ونص»: 100,500 or 150,000?
                    return _ask(UNCLEAR)
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
                if count >= THOUSAND:  # «50,000 الف»: a written-out amount with a scale after it
                    return _ask(UNCLEAR)
            total += count * t.value
            last_scale, group = t.value, _Group()
        i += 1

    rest = group.value()
    if last_scale is None:
        total = rest * THOUSAND if rest < THOUSAND else rest  # bare number: thousands
    elif last_scale >= MILLION and 0 < rest < THOUSAND:
        total += rest * THOUSAND  # مليون وميتين -> 1,200,000
    elif last_scale == THOUSAND and 0 < rest < SMALL_TAIL:
        # "خمسين الف وعشرين": 50,020 dinars, or fifty and twenty thousand? Nobody pays 20 dinars,
        # so ask. A tail of hundreds is real money: الف وخمسمية -> 1,500, خمسين الف وخمسمية -> 50,500.
        return _ask(AMBIGUOUS)
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
    if _hedged(tokens, start, end):
        return _ask(APPROXIMATE), matched
    return _evaluate(tokens[start:end]), matched


def _is_range_end(tok: _Token) -> bool:
    """«خمسين لستين»: ل on a number after an amount. A unit is left out: «لست نادية» is a person."""
    if not tok.word.startswith("ل") or len(tok.word) < 3:
        return False
    found = _classify_word(tok.word[1:])
    return bool(found) and found[0] in ("teen", "ten", "hundred")


def _hedged(tokens: list[_Token], start: int, end: int) -> bool:
    """A hedge next to the phrase: "حوالي خمسين", "ميتين بحدود", "اكثر من خمسين", "خمسين او اكثر", "خمسين لستين"."""
    if start > 0:
        before = tokens[start - 1]
        if before.kind == "approx" or (before.word == A_OR_ABOUT and tokens[start].kind in _NUMERIC):
            return True
        if before.word == THAN and start > 1 and tokens[start - 2].kind == "approx":
            return True
    if end < len(tokens):
        after = tokens[end]
        if after.kind == "approx" or _is_range_end(after):
            return True
        if after.word in OR_WORDS and end + 1 < len(tokens) and tokens[end + 1].kind == "approx":
            return True
    return False


def hedged_phrases(text: str) -> list[str]:
    """The amount phrases (substrings of `text`) that carry a hedge before or after them."""
    tokens = _tokenize(text or "")
    return [text[tokens[s].start:tokens[e - 1].end] for s, e in _runs(tokens) if _hedged(tokens, s, e)]


def alternatives(text: str) -> list[tuple[Amount, str]]:
    """Two amounts offered as a choice ("خمسين او ستين", "50 لو 60"): [(Amount, phrase), (Amount, phrase)].

    Empty when the text has no such pair. Two amounts in two separate requests
    ("دز لهادي خمسين ودز لزينب عشرين") are not a choice: only or-words may stand between them.
    """
    tokens = _tokenize(text or "")
    runs = _runs(tokens)
    for (s1, e1), (s2, e2) in zip(runs, runs[1:]):
        between = tokens[e1:s2]
        if between and all(t.word in OR_WORDS for t in between):
            return [(_evaluate(tokens[s1:e1]), text[tokens[s1].start:tokens[e1 - 1].end]),
                    (_evaluate(tokens[s2:e2]), text[tokens[s2].start:tokens[e2 - 1].end])]
    return []


def amount_phrases(text: str) -> list[tuple[Amount, str]]:
    """Every amount phrase in a sentence with its value: [(Amount, matched substring of `text`), ...]."""
    tokens = _tokenize(text or "")
    return [(_evaluate(tokens[s:e]), text[tokens[s].start:tokens[e - 1].end]) for s, e in _runs(tokens)]


def bare_scale_after_unknown(text: str) -> bool:
    """A scale or «مية» with no count, right after a word we do not know («شوية مليون», «خمص مية»).

    parse() cannot ask about these: it does not know the user's contacts, and a name may stand there («دز لعلي مليون»).
    The orchestrator knows who the payee is: it takes the payee's words out of the line and asks this. The word may
    be anything, so this is what makes a word missing from our lists a question, not a smaller amount."""
    tokens = _tokenize(text or "")
    for start, _ in _runs(tokens):
        first = tokens[start]
        bare = first.kind == "scale" or (first.kind == "hundred" and first.value == 100)
        if bare and start > 0:
            before = tokens[start - 1]
            if before.kind == "other" and not _says_nothing_of_the_count(before.word) \
                    and not before.word.startswith("ل") and not before.word[0].isdigit():
                return True
    return False


def parse(text: str) -> Amount:
    """Parse an amount phrase ("خمسين", "مية وخمسين الف", "٥٠٬٠٠٠") into IQD."""
    found = find_amount(text)
    return found[0] if found else _ask(NO_AMOUNT)
