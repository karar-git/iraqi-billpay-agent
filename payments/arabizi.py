"""Arabizi: Iraqi Arabic typed in Latin letters and digits ("dizla 50 alf l ahmad").

- to_arabic_words(text) -> [(original, arabic)], word by word. The particles l / b / w / il join
  the next word, as they do in Arabic script ("l ahmad" -> one word "لاحمد").
- to_arabic(text) -> the sentence in Arabic script (first reading of every word).
- variants(word) -> possible Arabic spellings of one Latin word. Arabizi drops short vowels and
  writes h for both ه and ح, so a name has several readings; the caller keeps the one that matches.

Common words come from a small lexicon; anything else is spelled letter by letter.
"""

from __future__ import annotations

import re
from itertools import product

from payments.text import fold

MAX_VARIANTS = 256

LEXICON = {
    # transfer / pay / top-up verbs
    **dict.fromkeys(("dez", "diz", "dezz", "dizz", "dz"), "دز"),
    **dict.fromkeys(("dezla", "dizla", "dezlah", "dizlah"), "دزله"),
    **dict.fromkeys(("dezli", "dizli"), "دزلي"),
    **dict.fromkeys(("dezlha", "dizlha", "dizilha"), "دزلها"),
    **dict.fromkeys(("7awil", "7awel", "7awwil", "7awwel", "hawil", "hawel", "7wl"), "حول"),
    **dict.fromkeys(("7awli", "7awili", "hawli"), "حولي"),
    **dict.fromkeys(("ib3ath", "eb3ath", "ib3ad", "eb3at"), "ابعث"),
    **dict.fromkeys(("irsil", "ersil", "ersel"), "ارسل"),
    **dict.fromkeys(("idfa3", "edfa3", "idfa", "edfa"), "ادفع"),
    **dict.fromkeys(("idfa3li", "edfa3li", "idfa3ly"), "ادفعلي"),
    **dict.fromkeys(("sadid", "saddid", "sadded", "sadd"), "سدد"),
    **dict.fromkeys(("saddidli", "sadidli"), "سددلي"),
    **dict.fromkeys(("khallisli", "khalisli", "5alisli", "5allisli"), "خلصلي"),
    **dict.fromkeys(("ish7an", "esh7an", "ishhan", "ish7en"), "اشحن"),
    **dict.fromkeys(("ish7anli", "esh7anli", "ish7anly", "ishhanli"), "اشحنلي"),
    **dict.fromkeys(("3abbi", "3abi", "abbi"), "عبي"),
    **dict.fromkeys(("3abbili", "3abili", "abbili"), "عبيلي"),
    # cancel / yes / no
    **dict.fromkeys(("batalit", "battalit", "batalt", "bitalit", "batelt"), "بطلت"),
    **dict.fromkeys(("ilghi", "elghi", "ilghiha", "elghiha", "cancel"), "الغي"),
    **dict.fromkeys(("khalliha", "5alliha", "khaliha"), "خليها"),
    **dict.fromkeys(("la", "la2", "laa", "no"), "لا"),
    **dict.fromkeys(("ee", "eee", "yes", "ah"), "اي"),
    **dict.fromkeys(("mo", "mu", "mou"), "مو"),
    **dict.fromkeys(("areed", "ared", "aread", "arid"), "اريد"),
    **dict.fromkeys(("shgad", "shkad", "shqad", "shgd", "shgadd"), "شكد"),
    **dict.fromkeys(("3indi", "3ndi", "endi"), "عندي"),
    **dict.fromkeys(("raseed", "rased", "rasid"), "رصيد"),
    **dict.fromkeys(("raseedi", "rasidi", "rasedi"), "رصيدي"),
    **dict.fromkeys(("flous", "floos", "flus", "fulus"), "فلوس"),
    **dict.fromkeys(("fatoora", "fatora", "fatura", "fatoura", "fatwra"), "فاتورة"),
    **dict.fromkeys(("ishtirak", "eshtirak", "ishtiraak"), "اشتراك"),
    **dict.fromkeys(("kart", "kaart", "card"), "كارت"),
    **dict.fromkeys(("khatti", "5atti", "khati"), "خطي"),
    **dict.fromkeys(("bsur3a", "bsuraa", "bsor3a"), "بسرعة"),
    **dict.fromkeys(("hassa", "hessa", "hisa", "hasa"), "هسه"),
    **dict.fromkeys(("3eeni", "3ini", "eeni"), "عيني"),
    # billers and operators
    **dict.fromkeys(("mawlada", "mawlida", "mowalda", "muwalda", "mawlda", "mawlade", "moalda"), "مولدة"),
    **dict.fromkeys(("kahraba", "kahraba2", "kahrabaa", "kahrba", "kahruba"), "كهرباء"),
    **dict.fromkeys(("wataniya", "wataniyya", "wataneya"), "وطنية"),
    **dict.fromkeys(("net", "nt"), "نت"),
    **dict.fromkeys(("internet", "intarnet", "internit", "interneet"), "انترنت"),
    **dict.fromkeys(("may", "mai", "maay", "mayy", "maa2", "mayi"), "ماي"),
    **dict.fromkeys(("zain", "zein", "zayn"), "زين"),
    **dict.fromkeys(("asia", "asya", "asiya"), "اسيا"),
    **dict.fromkeys(("asiacell", "asiasel", "asiacel"), "اسياسيل"),
    **dict.fromkeys(("korek", "kourek", "kurek", "korak"), "كورك"),
    # relation words
    **dict.fromkeys(("ibni", "ebni"), "ابني"),
    **dict.fromkeys(("binti", "bnti"), "بنتي"),
    **dict.fromkeys(("akhoy", "akhooy", "5oy", "akhuy", "a5oy"), "اخوي"),
    **dict.fromkeys(("ukhti", "okhti", "5ti", "o5ti", "u5ti"), "اختي"),
    **dict.fromkeys(("ummi", "umi", "yumma", "yoma", "mama"), "امي"),
    **dict.fromkeys(("abuya", "abooy", "aboy", "abuy", "baba"), "ابوي"),
    **dict.fromkeys(("marti", "mrti"), "مرتي"),
    **dict.fromkeys(("zawji", "zoji", "zawjy"), "زوجي"),
    # numbers and money words
    **dict.fromkeys(("wa7id", "wahid", "wa7ed"), "واحد"),
    **dict.fromkeys(("ithnain", "thnain", "tnen", "itnen", "ithnen"), "اثنين"),
    **dict.fromkeys(("khamsa", "khamse", "5amsa"), "خمسة"),
    **dict.fromkeys(("3ashra", "3ashara", "ashra", "3ashera"), "عشرة"),
    **dict.fromkeys(("3ishreen", "ishreen", "3eshreen", "3ishrin", "3eshrin"), "عشرين"),
    **dict.fromkeys(("tlatheen", "thalatheen", "tlatin", "tlateen"), "ثلاثين"),
    **dict.fromkeys(("arba3een", "arb3een", "arba3in"), "اربعين"),
    **dict.fromkeys(("khamseen", "khamsin", "5amseen", "5amsin"), "خمسين"),
    **dict.fromkeys(("mya", "miya", "mia", "meya", "miyya", "meeya"), "مية"),
    **dict.fromkeys(("mitain", "meetain", "miteen", "mitein", "mytain"), "ميتين"),
    **dict.fromkeys(("alf", "alif", "elf"), "الف"),
    **dict.fromkeys(("alfain", "alfein", "alfen"), "الفين"),
    **dict.fromkeys(("alaf", "aalaf", "talaf", "talaaf", "alaaf"), "الاف"),
    **dict.fromkeys(("malyoon", "malyon", "milyon", "million", "mlyon", "milyoon"), "مليون"),
    **dict.fromkeys(("nus", "nuss", "nos"), "نص"),
    **dict.fromkeys(("rub3", "ruba3", "rob3"), "ربع"),
    **dict.fromkeys(("dinar", "dinars", "denar"), "دينار"),
    **dict.fromkeys(("dollar", "dolar", "dollars", "dular"), "دولار"),
    **dict.fromkeys(("waraqa", "waraga", "wara8a", "warqa"), "ورقة"),
    **dict.fromkeys(("dafter", "daftar"), "دفتر"),
}

# Standalone particles that attach to the next word in Arabic script.
PARTICLES = {"l": "ل", "li": "ل", "il": "ال", "el": "ال", "al": "ال", "b": "ب", "bi": "ب",
             "w": "و", "wa": "و", "u": "و"}
# "la" before a word is "to" (la ahmad); on its own it is "no".
TO_WORDS = {"la", "lil"}

_DIGRAPHS = {"sh": ["ش"], "kh": ["خ"], "gh": ["غ"], "th": ["ث", "ت"], "dh": ["ذ", "ض"], "ch": ["ج"],
             "ph": ["ف"]}
_LONG = {"aa": ["ا"], "ee": ["ي"], "ii": ["ي"], "oo": ["و"], "ou": ["و"], "uu": ["و"], "ai": ["ي"],
         "ay": ["ي"], "ei": ["ي"], "ey": ["ي"], "aw": ["و"], "ow": ["و"]}
_CONSONANTS = {
    "b": ["ب"], "t": ["ت", "ط"], "j": ["ج"], "h": ["ه", "ح"], "d": ["د", "ض"], "r": ["ر"],
    "z": ["ز", "ظ"], "s": ["س", "ص"], "f": ["ف"], "q": ["ق"], "k": ["ك", "ق"], "l": ["ل"],
    "m": ["م"], "n": ["ن"], "w": ["و"], "y": ["ي"], "g": ["ك"], "v": ["ف"], "p": ["ب"], "c": ["ك"],
    "x": ["كس"], "2": ["ء", "ا"], "3": ["ع"], "5": ["خ"], "6": ["ط"], "7": ["ح"], "8": ["ق", "غ"],
    "9": ["ص", "ق"],
}
_VOWELS = "aeiou"
_FINAL = {"a": ["ه", "ا", "ي"], "e": ["ه", "ي", "ا"], "i": ["ي"], "o": ["و"], "u": ["و"]}
_MEDIAL = {"a": ["", "ا"], "e": ["", "ي", "ا"], "i": ["", "ي"], "o": ["", "و"], "u": ["", "و"]}

_LATIN = re.compile(r"[a-zA-Z]")


def has_latin(text: str) -> bool:
    return bool(_LATIN.search(text or ""))


def _segments(word: str) -> list[list[str]]:
    """Options for each sound of a Latin word, left to right."""
    word = re.sub(r"([b-df-hj-np-tv-z])\1", r"\1", word)  # doubled consonant = one letter
    out, i = [], 0
    while i < len(word):
        pair = word[i:i + 2]
        if pair in _DIGRAPHS:
            out.append(_DIGRAPHS[pair])
            i += 2
        elif pair in _LONG:
            if i == 0:  # a word-initial vowel is carried by alif: "ayman" -> ايمن
                long = _LONG[pair][0]
                out.append(["ا"] if long == "ا" else ["ا" + long, "ا"])
            else:
                out.append(_LONG[pair])
            i += 2
        elif word[i] in _VOWELS:
            if i == 0:
                out.append(["ا"])
            elif i == len(word) - 1:
                out.append(_FINAL[word[i]])
            else:
                out.append(_MEDIAL[word[i]])
            i += 1
        else:
            out.append(_CONSONANTS.get(word[i], [""]))
            i += 1
    return out


def variants(word: str) -> list[str]:
    """Arabic spellings of one Latin word, most likely first ("3ali" -> ["علي", "عالي"])."""
    key = re.sub(r"[^a-z0-9]", "", (word or "").lower())
    if not key:
        return []
    if key in LEXICON:
        return [LEXICON[key]]
    if key.isdigit():
        return [key]
    found = []
    for letters in product(*_segments(key)):
        spelled = "".join(letters)
        if spelled and spelled not in found:
            found.append(spelled)
        if len(found) >= MAX_VARIANTS:
            break
    return found


def _first_reading(word: str) -> str:
    options = variants(word)
    return options[0] if options else word


def to_arabic_words(text: str) -> list[tuple[str, str]]:
    """Each word as (what the user typed, Arabic reading). Arabic words pass through unchanged."""
    raw = (text or "").split()
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(raw):
        word = raw[i]
        low = re.sub(r"[^a-z0-9]", "", word.lower())
        nxt = raw[i + 1] if i + 1 < len(raw) else None
        nxt_key = re.sub(r"[^a-z0-9]", "", (nxt or "").lower())
        joins = low in PARTICLES or (low in TO_WORDS and nxt_key not in LEXICON)
        if joins and nxt is not None and (has_latin(nxt) or nxt[0].isdigit()):
            prefix = PARTICLES.get(low, "ل")
            reading = nxt_key if nxt_key.isdigit() else _first_reading(nxt)
            if prefix == "ال" and reading.startswith("ال"):
                prefix = ""
            out.append((f"{word} {nxt}", prefix + reading))
            i += 2
            continue
        if has_latin(word):
            out.append((word, _first_reading(word)))
        else:
            out.append((word, word))
        i += 1
    return out


def to_arabic(text: str) -> str:
    """The sentence in Arabic script; text without Latin letters comes back unchanged."""
    if not has_latin(text):
        return text or ""
    return " ".join(arabic for _, arabic in to_arabic_words(text))


def latin_name_variants(mention: str, limit: int = MAX_VARIANTS) -> list[str]:
    """Arabic readings of a Latin name mention ("l ahmad" -> ["اهمد", "احمد", ...]).

    A leading "to" particle (l, li, la) is dropped. Readings are folded like payments.text.fold.
    """
    words = [w for w in (mention or "").lower().split() if re.sub(r"[^a-z0-9]", "", w)]
    while words and re.sub(r"[^a-z0-9]", "", words[0]) in {"l", "li", "la", "lil", "to"}:
        words = words[1:]
    options = [variants(w) or [w] for w in words]
    found = []
    for combo in product(*options):
        name = fold(" ".join(combo))
        if name not in found:
            found.append(name)
        if len(found) >= limit:
            break
    return found
