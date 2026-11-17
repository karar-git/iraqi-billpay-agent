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

from payments import names
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
    **dict.fromkeys(("7isab", "7sab", "7esab", "hisab", "7isaab"), "حساب"),
    **dict.fromkeys(("7isabak", "7sabak", "7esabak", "hisabak", "7sabk", "7isabk"), "حسابك"),
    **dict.fromkeys(("7isabich", "7sabich", "7sabech", "7isabech", "7sabij", "7isabij"), "حسابج"),
    **dict.fromkeys(("7isabi", "7sabi", "7esabi", "hisabi"), "حسابي"),
    **dict.fromkeys(("yinqifil", "yenqefel", "yinqefel", "yenqifil", "yingifil", "yengefel", "yin8ifil", "yin8efel"),
                    "ينقفل"),
    **dict.fromkeys(("mawqoof", "mawgoof", "maw8oof", "mawqouf", "mawgouf"), "موقوف"),
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
    **dict.fromkeys(("malyoon", "malyon", "milyon", "million", "mlyon", "milyoon", "mil"), "مليون"),
    **dict.fromkeys(("nus", "nuss", "nos"), "نص"),
    **dict.fromkeys(("rub3", "ruba3", "rob3"), "ربع"),
    **dict.fromkeys(("dinar", "dinars", "denar"), "دينار"),
    **dict.fromkeys(("dollar", "dolar", "dollars", "dular"), "دولار"),
    **dict.fromkeys(("waraqa", "waraga", "wara8a", "warqa"), "ورقة"),
    **dict.fromkeys(("dafter", "daftar"), "دفتر"),
}

# English words a user may type ("send 50000 to Ahmed", "pay the electricity bill"), read as the Arabic
# keyword they stand for, so the open stand-in core and the verbatim check can follow an English message.
# Kept apart from the Arabizi lexicon: payments.language counts these as English, not as Iraqi.
ENGLISH_LEXICON = {
    **dict.fromkeys(("send", "sent"), "دز"),
    **dict.fromkeys(("transfer", "wire"), "حول"),
    **dict.fromkeys(("pay", "settle"), "ادفع"),
    **dict.fromkeys(("recharge", "topup", "top"), "اشحن"),
    **dict.fromkeys(("credit", "up"), "رصيد"),
    "balance": "رصيدي",
    "bill": "فاتورة", "bills": "فواتير",
    "electricity": "كهرباء", "power": "كهرباء",
    "generator": "مولدة",
    "water": "ماي",
    "stop": "بطلت",
    **dict.fromkeys(("one",), "واحد"), "two": "اثنين", "three": "ثلاثة", "four": "اربعة", "five": "خمسة",
    "six": "ستة", "seven": "سبعة", "eight": "ثمانية", "nine": "تسعة", "ten": "عشرة",
    "eleven": "احدعش", "twelve": "اثنعش", "thirteen": "تلطعش", "fourteen": "اربعطعش",
    "fifteen": "خمسطعش", "sixteen": "ستطعش", "seventeen": "سبعطعش", "eighteen": "ثمنطعش", "nineteen": "تسعطعش",
    "twenty": "عشرين", "thirty": "ثلاثين", "forty": "اربعين", "fifty": "خمسين",
    "sixty": "ستين", "seventy": "سبعين", "eighty": "ثمانين", "ninety": "تسعين",
    "hundred": "مية", "thousand": "الف", "thousands": "الاف", "billion": "مليار", "trillion": "تريليون",
    "half": "نص", "quarter": "ربع", "quarters": "ارباع", "third": "ثلث", "thirds": "ثلثين",
    "and": "و", "or": "او",
    **dict.fromkeys(("few", "couple", "several"), "كم"),  # «a few thousand» is not 1,000: payments.amounts asks
    # hedges and comparisons: «around 50», «over a million», «less than a million»: payments.amounts asks
    **dict.fromkeys(("around", "about", "roughly", "approx", "approximately", "almost", "nearly", "maybe", "least",
                     "most"), "حوالي"),
    "over": "فوق", "under": "تحت", "more": "اكثر", "less": "اقل", "than": "من",
    # money that is not dinars: payments.amounts asks
    **dict.fromkeys(("buck", "bucks"), "دولار"),
    **dict.fromkeys(("euro", "euros"), "يورو"),
    **dict.fromkeys(("pound", "pounds"), "جنيه"),
    **dict.fromkeys(("riyal", "riyals", "rial", "rials"), "ريال"),
    **dict.fromkeys(("dirham", "dirhams"), "درهم"),
    **dict.fromkeys(("lira", "liras"), "ليرة"),
    **dict.fromkeys(("toman", "tomans"), "تومان"),
    "iqd": "دينار", "usd": "دولار",
    "mom": "امي", "mother": "امي", "dad": "ابوي", "father": "ابوي", "brother": "اخوي", "sister": "اختي",
    "son": "ابني", "daughter": "بنتي", "wife": "مرتي", "husband": "زوجي",
}

_EN_UNITS = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine"}
_EN_TENS = {"twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"}
_EN_TEENS = {"ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"}

# Common names in Latin letters and how they are written in Arabic. Arabizi drops short vowels, so a reading with
# other vowels is only a guess (payments-level matching treats it as "did you mean?"): these spellings are not.
NAME_READINGS = {
    latin: arabic for arabic, spellings in {
        "علي": "ali aly", "هادي": "hadi hady", "فاطمة": "fatima fatma fatimah", "عباس": "abbas 3abbas abas 3abas",
        "كرار": "karar krar", "سجاد": "sajjad sajad", "زهراء": "zahraa zahra", "سارة": "sara sarah", "نور": "noor nour nur",
        "مريم": "maryam mariam", "جاسم": "jasim jasem", "كاظم": "kadhim kazim kathim", "سامر": "samer samir",
        "سامي": "sami samy", "عمار": "ammar 3ammar amar", "ياسر": "yasir yaser", "باسم": "basim basem", "رضا": "ridha rida reda",
        "مصطفى": "mustafa mustapha", "محمد": "mohammed mohammad muhammad mohamed", "ابراهيم": "ibrahim ebrahim",
        "خالد": "khalid khaled", "حامد": "hamid hamed", "ماجد": "majid majed", "نادية": "nadia nadya", "سلام": "salam",
        "سالم": "salem salim", "رقية": "ruqaya roqaya ruqayya", "هدى": "huda hoda", "ليلى": "layla laila leila",
        "سلمى": "salma", "ندى": "nada", "منى": "muna mona", "تبارك": "tabarak", "بنين": "baneen banin", "دعاء": "duaa doaa",
        "حوراء": "hawraa hawra", "وسام": "wisam wesam", "حسام": "hussam husam hosam", "امير": "ameer amir", "نبيل": "nabeel nabil",
        "جميل": "jameel jamil", "كريم": "kareem karim", "رحيم": "raheem rahim", "جواد": "jawad", "موسى": "musa mousa",
        "عيسى": "issa isa", "صادق": "sadiq sadeq", "صالح": "saleh salih", "ناصر": "nasir naser", "قاسم": "qasim qasem",
        "هاشم": "hashim hashem", "ميثم": "maytham maitham", "مازن": "mazin mazen", "عبدالله": "abdullah abdallah",
        "رنا": "rana", "رانيا": "rania ranya", "زينب": "zainab zaynab zeinab", "حيدر": "haider haidar hayder 7ayder 7aider",
        "حسين": "hussein husain hussain", "حسن": "hassan hasan", "احمد": "ahmed ahmad", "يوسف": "yousif yusuf yousef",
    }.items() for latin in spellings.split()
}

# Names whose last vowel is a short one left out in Arabic, though the shape says a written letter (_writes_a_long_vowel).
# Not in NAME_READINGS: that would make them more certain than the shape-free reading, and pick سندس over a saved سندوس.
_SHORT_LAST_VOWEL = frozenset("sundus sondos rusul rusol muhammed muhamed mo7ammed mo7amed mu7ammed mu7amed".split())
# How the name سعيد is often typed. Its first reading is the title سيد, so a saved سعيد has that word where the title reads.
_SAEED = frozenset(("saeed", "said", "saied"))

# Standalone particles that attach to the next word in Arabic script.
PARTICLES = {"l": "ل", "li": "ل", "il": "ال", "el": "ال", "al": "ال", "b": "ب", "bi": "ب",
             "w": "و", "wa": "و", "u": "و", "to": "ل"}
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


def _consonant_options(word: str) -> list[list[str]]:
    """Like _segments, with one choice for every vowel: the same vowels as the first reading, consonants open."""
    options = _segments(word)
    plain = re.sub(r"([b-df-hj-np-tv-z])\1", r"\1", word)
    out, i, slot = [], 0, 0
    while i < len(plain):
        pair = plain[i:i + 2]
        if pair in _DIGRAPHS:
            out.append(options[slot])
            i += 2
        elif pair in _LONG:
            out.append(options[slot][:1])
            i += 2
        else:
            out.append(options[slot][:1] if plain[i] in _VOWELS else options[slot])
            i += 1
        slot += 1
    return out


def _writes_a_long_vowel(key: str) -> bool:
    """The last vowel of a Latin name reads as a written letter, not a short vowel left out: «mahmud» has و, «husen» has
    ي. u and o in the last syllable do, unless the syllable before is long already («yousuf»). e and i do after one
    consonant («husen», «7sen», «3amer»), not after two («ahmed», «mohsin») or a long syllable («haider»). A title is
    written without one («syed», «sit»), and title_not_saved says whether that title is the saved one."""
    if key in _SHORT_LAST_VOWEL or names.is_title(_first_reading(key)):
        return False
    plain = re.sub(r"sh|kh|gh|th|dh|ch|ph", "C", re.sub(r"([b-df-hj-np-tv-z])\1", r"\1", key))
    last = re.search(r"([^aeiou])([eiou])[^aeiou]$", plain)
    if not last:
        return False
    before = plain[:last.start(1)]
    if not re.search(r"[aeiou]", before):
        return True
    if re.search(r"(aa|ee|ii|oo|ou|uu|ai|ei)$", before):
        return False
    return before[-1] in _VOWELS or last.group(2) in "ou"


def _sure_word_readings(word: str, shape_counts: bool = True) -> list[str]:
    key = re.sub(r"[^a-z0-9]", "", word.lower())
    if key in NAME_READINGS:
        return [NAME_READINGS[key]]
    if key in LEXICON or key in ENGLISH_LEXICON or key.isdigit() or not key:
        return variants(word) or [word]
    if shape_counts and _writes_a_long_vowel(key):  # its first reading leaves that letter out: every reading of it is a guess
        return []
    found = list(dict.fromkeys("".join(letters) for letters in product(*_consonant_options(key))))
    return found[:MAX_VARIANTS]


def _latin_words(mention: str) -> list[str]:
    words = [re.sub(r"[^a-z0-9]", "", w) for w in (mention or "").lower().split()]
    return [w for w in words if w and w not in {"l", "li", "la", "lil", "to"}]


def known_name_spelling(mention: str) -> bool:
    """Every word of the Latin name is a spelling in NAME_READINGS."""
    words = _latin_words(mention)
    return bool(words) and all(w in NAME_READINGS for w in words)


def first_reading_is_a_guess(mention: str) -> bool:
    """A word of the Latin name says more than its first reading, so that reading is not to be trusted: «saif 3ammar» is
    سيف عمار (a known spelling), and its first reading سيف عمر is another person; so is «mahmud», which has a و."""
    return any(w in NAME_READINGS or (w not in LEXICON and w not in ENGLISH_LEXICON and _writes_a_long_vowel(w))
               for w in _latin_words(mention))


def ends_differently(mention: str, saved: str) -> bool:
    """A Latin name with a word ending in the sound of ي against a saved name with a word ending in ى: «mani», «منى».
    The words need not stand in the same place, a saved title or a word before the name moves them («استاذ منى», «mama
    mani», «عبد الله منى»): the last word against the end of the saved name, or any word that spells a saved one."""
    keys = [re.sub(r"[^a-z0-9]", "", word) for word in (mention or "").lower().split()]
    ending = [key for key in keys if key and key not in NAME_READINGS and key[-1] in "iy"]
    words = [word for word in (saved or "").split() if word.endswith("ى")]
    if not ending or not words:
        return False
    if keys[-1] in ending and (saved or "").rstrip().endswith("ى"):
        return True
    spelled = {names.normalize(reading) for key in ending for reading in variants(key)}
    return any(names.normalize(word) in spelled or "ل" + names.normalize(word) in spelled for word in words)  # «lmany»


def saved_as_read(mention: str, saved: str) -> str:
    """The saved name as title_not_saved should see it: «ali saeed» reads علي سيد, and in a saved علي سعيد that word is
    there, as the name it is. No other name is: «ali said» against علي حسن still has a word the saved name lacks."""
    if not _SAEED.intersection(_latin_words(mention)):
        return saved
    return " ".join("سيد" if word == "سعيد" else word for word in (saved or "").split())


def _name_words(mention: str) -> list[tuple[str, str]]:
    """The words of a name as (prefix, word), without a leading «to». «el sha3la» is one word, written the way it is
    saved: ("ال", "sha3la"). An Arabic word stays: «مو ali husen» is not «ali husen»."""
    words = [w for w in (mention or "").lower().split() if re.search(r"\w", w)]
    while words and (re.sub(r"[^a-z0-9]", "", words[0]) in {"l", "li", "la", "lil", "to"} or words[0] in {"ل", "الى", "إلى"}):
        words = words[1:]
    out, i = [], 0
    while i < len(words):
        if words[i] in {"el", "il", "al"} and i + 1 < len(words):
            out.append(("ال", words[i + 1]))
            i += 2
        else:
            out.append(("", words[i]))
            i += 1
    return out


def name_word_readings(mention: str) -> list[list[str]]:
    """Every reading of each word of a name, word by word: what it could be, to find who else it could be. A known
    spelling names one name: «salam» is سلام, so سالم is no reading of it here, though latin_name_variants would offer
    it. Besides those, «zayed» and «saif» may be written with an alif, زايد and سايف, and «dawud» داود."""
    out = []
    for prefix, word in _name_words(mention):
        key = re.sub(r"[^a-z0-9]", "", word)
        if key in NAME_READINGS:
            readings = [NAME_READINGS[key]]
        else:
            readings = variants(word) or [word]
            if key not in LEXICON and key not in ENGLISH_LEXICON:
                readings = readings + [r for r in variants(re.sub(r"(?<=.)a([yiw])", r"aa\1", key)) if r not in readings]
        out.append([prefix + r for r in readings])
    return out


def sure_word_readings(mention: str, shape_counts: bool = True) -> list[list[str]]:
    """The readings of each word that a match may count as exact (sure_name_readings), word by word. A mention that mixes
    in Arabic script has none: it is not a name typed in Latin letters, and «علي hasan» is no more حسن than علي حسن."""
    words = _name_words(mention)
    if any(not has_latin(word) and not word.isdigit() for _, word in words):
        return [[]]
    return [[prefix + r for r in _sure_word_readings(word, shape_counts)] for prefix, word in words]


def sure_name_readings(mention: str, limit: int = MAX_VARIANTS, shape_counts: bool = True) -> list[str]:
    """The Arabic readings of a Latin name that a match may count as exact: a known spelling («fatima» -> فاطمة), or the
    first reading with only its consonants open («ahmad» -> احمد, h is ه or ح). Any other vowel placement in
    latin_name_variants («sara» -> سرى, «salam» -> سالم) is a guess, and the guess is shown as a question."""
    found = []
    for combo in product(*sure_word_readings(mention, shape_counts)):
        name = fold(" ".join(combo))
        if name and name not in found:
            found.append(name)
        if len(found) >= limit:
            break
    return found


def variants(word: str) -> list[str]:
    """Arabic spellings of one Latin word, most likely first ("3ali" -> ["علي", "عالي"])."""
    key = re.sub(r"[^a-z0-9]", "", (word or "").lower())
    if not key:
        return []
    if key in LEXICON:
        return [LEXICON[key]]
    if key in ENGLISH_LEXICON:
        return [ENGLISH_LEXICON[key]]
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
    raw = re.sub(r"(?<=[A-Za-z])-(?=[A-Za-z])", " ", text or "").split()  # «twenty-five» is two words
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
        thousands = re.fullmatch(r"(\d+(?:[.,]\d+)?)k[.,!?؟،]*", word.lower())
        if thousands:  # «50k»: fifty thousand, never the letters of 5 and 0
            out.append((word, f"{thousands.group(1)} الف"))
        elif has_latin(word):
            out.append((word, _first_reading(word)))
        else:
            out.append((word, word))
        i += 1
        # «twenty five», «hundred fifty»: English leaves out the و that Arabic joins the parts of a number with
        if low in _EN_TENS | {"hundred"} and nxt_key in _EN_UNITS | _EN_TENS | _EN_TEENS:
            out.append(("and", "و"))
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
