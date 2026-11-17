"""Which language to answer in, decided per message with deterministic marker lists.

Three reply languages:
- "iq"  Baghdadi / Iraqi Arabic, also for Arabizi (Iraqi Arabic in Latin letters and digits);
- "msa" Modern Standard Arabic (فصحى), for MSA and for every other Arabic dialect (Gulf, Levantine,
        Egyptian, Maghrebi...): the agent speaks Iraqi or it speaks Fusha, never a third dialect;
- "en"  English.

detect(text, previous) looks for marker words of each language in one message:
- a message with no markers at all (a number, a name, «ادفع الكهرباء», «إي») keeps `previous`;
- a message with markers of the language already in use keeps it, even if it also has markers of
  another one: one mixed message never flips the language;
- otherwise the language with the most markers wins, and a tie keeps `previous`.
The one exception: an Arabic-script message with no markers after English goes back to Iraqi
(the user stopped writing English, and Iraqi is the agent's home language).

The conversational core may also return a language hint (Intent.language) when it is sure; the
orchestrator lets that hint override this detector.
"""

from __future__ import annotations

import re

from payments.arabizi import ENGLISH_LEXICON, LEXICON
from payments.text import DIACRITICS, fold

IRAQI, MSA, ENGLISH = "iq", "msa", "en"
LANGUAGES = (IRAQI, MSA, ENGLISH)
DEFAULT = IRAQI


def _folded(*words: str) -> frozenset[str]:
    return frozenset(fold(w) for w in words)


_STRIP = re.compile(f"[{DIACRITICS}ـ]")


def light(text: str) -> str:
    """Diacritics and tatweel off, every letter kept. MSA markers are matched on this form, because
    fold() would turn «إلى» into Iraqi «الي» (= اللي) and Levantine «هلأ» into the greeting «هلا»."""
    return _STRIP.sub("", text or "")


def _light(*words: str) -> frozenset[str]:
    return frozenset(light(w) for w in words)


# Iraqi / Baghdadi words. A word of these (or one starting with an Iraqi verb stem) is an Iraqi marker.
IRAQI_WORDS = _folded(
    "شلون", "شلونك", "شلونج", "شلونكم", "هسه", "هسة", "هسا", "هسع", "اكو", "ماكو", "شكد", "شقد", "اشكد",
    "شنو", "شني", "شكو", "هواية", "هواي", "كلش", "باجر", "يمعود", "اغاتي", "عيوني", "خوش", "شوكت", "اشوكت",
    "هيج", "شبيك", "شبيج", "مالتي", "مالك", "مالج", "ياهو", "ياهي", "هذوله", "هذولة", "بطلت", "بطلنا",
    "خلصلي", "عبيلي", "تره", "ترة", "لعد", "بلكي", "يابه", "يبه", "يمه", "ديروح", "اكدر", "تكدر", "تكدرين",
    "اكدرلك", "لتدز", "ماعرف", "مااعرف", "اني", "احنه", "انتو", "اخوية", "بيه", "بيها", "بيج", "الج",
    "عليج", "ويا", "وياي", "وياك", "وياج", "لهسه", "بعدج", "صدك", "جان", "كوليلي", "كلي", "كوللي",
    "شسوي", "شنسوي", "شتحتاج", "شتريد", "شتريدين", "منو", "اريدن", "دتكدر", "دنروح",
    # everyday Iraqi: «مال هالشهر», «الماي», «وين وصلت», «ليش ما اندفعت», «اخوي»
    "مال", "الماي", "وين", "ليش", "اخوي", "اخويه", "شكو", "ماكو",
)
# «هالشهر», «هالمرة»: the Iraqi هال- prefix (a word of at least 3 letters after it).
IRAQI_HAL = fold("هال")
# «ادفعلي», «اشحنلي», «دفعلج»: a verb with the dative glued on, as Iraqis type it (MSA writes «ادفع لي»).
IRAQI_GLUED_STEMS = tuple(fold(p) for p in ("ادفع", "اشحن", "دفع", "شحن"))
IRAQI_GLUED_ENDS = tuple(fold(p) for p in ("لي", "لج", "لك", "له", "لها"))
# Iraqi verb stems: «دز», «دزلي», «دزله», «يدز», «ادزلك»... ; «اگدر»...
IRAQI_PREFIXES = tuple(fold(p) for p in ("دز", "ادز", "تدز", "ندز", "يدز", "دزي", "اگدر", "تگدر", "شلون"))
# Letters that only Iraqi spelling uses in this setting.
IRAQI_LETTERS = tuple(chr(c) for c in (0x06AF, 0x0686, 0x06A4))  # gaf, che, veh as users type them; we never write che

# Modern Standard Arabic and the other Arabic dialects (all answered in MSA), with their hamzas and ى.
FUSHA_WORDS = _light(
    # MSA function words and verbs Iraqis do not use in chat (they say شكد، شلون، شوكت، وين، هسه، بس، كلش)
    "هل", "ماذا", "لماذا", "كيف", "متى", "أين", "كم", "الآن", "أن", "أود", "أرغب", "أرجو", "لقد", "سوف",
    "ليس", "لست", "لدي", "لديك", "لدينا", "الذي", "التي", "الذين", "هذه", "ذلك", "تلك", "إلى", "الى", "نعم",
    "حسناً", "حسنا", "أيضاً", "ايضا", "فقط", "جداً", "جدا", "أستطيع", "يمكنني", "يمكنك", "تستطيع", "أحتاج",
    "إلغاء", "ألغِ", "قيمته", "بقيمة", "أريده", "أرسله", "هاتفي", "هاتف", "لهاتفي",
)
# Other Arabic dialects: answered in MSA, and never written in an MSA reply.
DIALECT_WORDS = _light(
    # Gulf
    "ابغى", "أبغى", "ابغا", "ودي", "الحين", "وايد", "عشان", "يبيله", "وش", "تبين", "تبي", "يبي",
    # Levantine
    "بدي", "بدك", "بدنا", "هلق", "هلأ", "شو", "كتير", "هيك", "منيح", "مبارح", "لسا", "هلّق", "قديش", "مين",
    # Egyptian
    "عايز", "عايزة", "عاوز", "عاوزة", "ازاي", "إزاي", "دلوقتي", "كده", "كدا", "بتاع", "بتاعي", "اوي", "مش",
    "ازيك", "إزيك", "ازيكم", "كام", "ابعت", "ابعتلي", "ابعتله",
    # Maghrebi
    "بغيت", "واش", "دابا", "بزاف", "كيفاش", "علاش", "فين", "ديال", "ديالي", "مزيان",
)
MSA_WORDS = FUSHA_WORDS | DIALECT_WORDS
# MSA phrases of more than one word (looked for in the message, diacritics off).
MSA_PHRASES = tuple(dict.fromkeys(light(p) for p in (
    "أريد أن", "أرغب في", "أود أن", "قم ب", "ما هو", "ما هي", "شكراً لك", "شكرا لك", "عامل ايه", "عاملة ايه",
    "عامل إيه", "عاملة إيه")))
# «أريد» with its hamza and a verbal noun after it («أريد دفع فاتورة») is MSA; Iraqis write «اريد ادفع»
# (no hamza, or a verb that starts with ا / أ after it), so «أريد أدفع» stays neutral.
MSA_WANT = light("أريد")

# English words. Loanwords that Iraqis type inside Arabic chats («ok», «pin») are left out.
ENGLISH_WORDS = frozenset((
    "i", "i'm", "im", "me", "my", "you", "your", "we", "our", "he", "she", "it", "they", "the", "a", "an",
    "to", "for", "of", "in", "on", "at", "from", "with", "and", "or", "but", "please", "pls", "plz", "want",
    "wanna", "would", "like", "need", "can", "could", "will", "do", "does", "did", "is", "are", "was",
    "be", "have", "has", "send", "sent", "transfer", "pay", "paid", "bill", "bills", "electricity",
    "generator", "water", "phone", "credit", "recharge", "top", "up", "topup", "top-up", "balance", "money",
    "how", "much", "many", "what", "which", "who", "when", "where", "why", "yes", "yeah", "yep", "no",
    "nope", "not", "don't", "dont", "cancel", "stop", "change", "confirm", "thanks", "thank", "hello", "hi",
    "hey", "good", "morning", "evening", "this", "that", "month", "last", "previous", "current", "first",
    "second", "one", "two", "three", "four", "five", "ten", "twenty", "fifty", "hundred", "thousand",
    "dinars", "dollars", "wallet", "help", "account", "number", "mine", "him", "her", "brother", "sister",
    "mother", "mom", "dad", "father", "wife", "husband", "son", "daughter", "friend", "let's", "lets", "now",
    "again", "sorry", "just", "make", "it's", "its", "let", "go", "send", "transfer",
))
_ENGLISH_ONLY_LEXICON = frozenset({"cancel", "yes", "no", "card", "net", "internet", "dollar", "dollars",
                                   "dinar", "dinars", "million", "may", "mama", "baba", "zain", "korek", "asia",
                                   "asiacell", "ah", "mai", "mia"})
# Arabizi words that are not English words: «dizli», «shgad», «hassa», «3indi»...
ARABIZI_WORDS = frozenset(k for k in LEXICON if k not in ENGLISH_WORDS and k not in _ENGLISH_ONLY_LEXICON
                          and k not in ENGLISH_LEXICON) | {
    "shlon", "shlonak", "shlonich", "aku", "maku", "shnu", "shno", "hwaya", "kulish", "baachir", "bachir",
    "yaba", "habibi", "3ini", "3eni", "akhooya", "mal", "mali", "malti", "inta", "inti", "ani", "ahna",
    "wiyya", "liesh", "lesh", "ween", "minu", "shwakit", "agdar", "tigdar", "dazli", "dizzli",
}
_ARABIZI_DIGIT = re.compile(r"[a-z][2-9]|[2-9][a-z]")  # «3ali», «7awil», «ish7an»
_LATIN_WORD = re.compile(r"[a-z][a-z'\-]*[a-z]|[a-z]|[a-z0-9]*[a-z][a-z0-9]*")
_ARABIC_LETTER = re.compile(r"[ء-يٱ-ۓ]")
_LATIN_LETTER = re.compile(r"[A-Za-z]")


def _arabic_words(text: str) -> list[tuple[str, str]]:
    """(folded, light) forms of each word, punctuation and digits off."""
    out = []
    for w in (text or "").split():
        w = re.sub(r"[^\w]|_|\d", "", light(w))
        out.append((fold(w), w))
    return out


def _bare(word: str) -> str:
    """Without an attached و / ب / ل / ف."""
    return word[1:] if word[:1] in ("و", "ب", "ل", "ف") and len(word) > 3 else word


def scores(text: str) -> dict[str, int]:
    """How many marker words of each language the message has."""
    out = {IRAQI: 0, MSA: 0, ENGLISH: 0}
    raw = text or ""
    folded = fold(raw)
    # Iraqi-only letters, counted on the raw text (fold() maps them to plain letters)
    out[IRAQI] += sum(1 for w in raw.split() if any(ch in w for ch in IRAQI_LETTERS))
    words = _arabic_words(raw)
    for i, (word, plain) in enumerate(words):
        if not word or not _ARABIC_LETTER.search(word):
            continue
        if word in IRAQI_WORDS or _bare(word) in IRAQI_WORDS or word.startswith(IRAQI_PREFIXES) \
                or (word.startswith(IRAQI_HAL) and len(word) >= len(IRAQI_HAL) + 3) \
                or (word.startswith(IRAQI_GLUED_STEMS) and word.endswith(IRAQI_GLUED_ENDS)
                    and any(len(word) > len(s) + 1 for s in IRAQI_GLUED_STEMS if word.startswith(s))):
            out[IRAQI] += 1
        elif plain in MSA_WORDS or _bare(plain) in MSA_WORDS:
            out[MSA] += 1
        elif plain == MSA_WANT:
            after = words[i + 1][1] if i + 1 < len(words) else ""
            if after and not after.startswith(("ا", "أ", "إ")):
                out[MSA] += 1
    spaced = " " + re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", light(raw))) + " "
    for phrase in MSA_PHRASES:  # «قم ب» is followed by a verbal noun: «قم بتحويل»
        if f" {phrase} " in spaced or (phrase.endswith(" ب") and f" {phrase}" in spaced):
            out[MSA] += 1
    for word in re.findall(r"[a-z0-9'\-]+", folded):
        if not re.search(r"[a-z]", word):
            continue
        clean = word.strip("'-")
        if clean in ENGLISH_WORDS:
            out[ENGLISH] += 1
        elif clean in ARABIZI_WORDS or _ARABIZI_DIGIT.search(clean):
            out[IRAQI] += 1
    return out


def _arabic_script(text: str) -> bool:
    return len(_ARABIC_LETTER.findall(text or "")) > len(_LATIN_LETTER.findall(text or ""))


def detect(text: str, previous: str | None = None) -> str:
    """The language to answer this message in (see the module docstring)."""
    previous = previous if previous in LANGUAGES else DEFAULT
    found = scores(text)
    if found[previous] > 0:
        return previous  # the language in use is there: a mixed message never flips it
    best = max(found.values())
    if best == 0:
        if previous == ENGLISH and _arabic_script(text):
            return IRAQI
        return previous
    leaders = [lang for lang in LANGUAGES if found[lang] == best]
    return leaders[0] if len(leaders) == 1 else previous


def normalize(language: str | None) -> str | None:
    """A language hint as one of LANGUAGES, or None: 'ar-IQ' / 'iraqi' -> 'iq', 'ar' / 'fusha' -> 'msa'."""
    value = (language or "").strip().lower()
    if not value:
        return None
    if value in LANGUAGES:
        return value
    if value in ("ar-iq", "iraqi", "baghdadi", "arabizi", "ar_iq"):
        return IRAQI
    if value in ("ar", "arabic", "fusha", "msa", "standard", "ar-msa"):
        return MSA
    if value in ("en", "english", "en-us", "en-gb"):
        return ENGLISH
    return None
