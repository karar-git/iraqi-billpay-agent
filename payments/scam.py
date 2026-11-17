"""Words of the fake-support scam: someone speaks as support or staff, or threatens the account, to get money sent.

Deterministic. Words alone refuse nothing: agent/session.py refuses only when they come with a payment to a number
that is not a saved contact, so a saved contact called «موظف الدعم ابو علي» and a remark about «حسابي بالبنك» stay
ordinary messages. A cue also holds for a few turns, because the number may come after the threat (holds_for_later);
wanting no confirmation holds only for its own request. Four cues:

- a claim to BE the wallet's support or staff (any desk that takes calls about the wallet, in any word order);
- a threat to the wallet account: closing, freezing, stopped, hacked, in Arabic, Arabizi or English; also the user
  repeating it about their own account under a condition («حسابي ينقفل اذا ما احول»);
- paying «على رقم التحقق» or the like: no real check is ever paid. Naming the code alone is no cue;
- fake authority (fake_authority): text that speaks as the system or an admin, overrides the rules, claims the user
  already confirmed, or wants the card or the confirmation skipped. No text ever skips a card: to a saved contact the
  normal card still shows. «بدون عمولة», «شنو الكارت» and «ما اريد تأكيد» are no cue, and neither is a role word inside a
  sentence («رقم الادمن: 077...»), someone else's card («لان هو بدون بطاقة») or a top-up's scratch card («اشحنله بدون كارت»).

A threat or a verification number also puts a warning line on a card to a saved contact (threat_or_verification):
a scammer's number may have been saved earlier.

Tuned against both sides: a company, a bank or a charity is not the wallet («انا موظف بالشركة», «دعم العوائل»).
"""

from __future__ import annotations

import re

from payments import arabizi
from payments.amounts import PAY_STEMS
from payments.names import normalize
from payments.text import fold


def _alt(*phrases: str) -> str:
    """A regex alternative of the phrases as normalize() writes them (ق->ك, ة->ه), longest first."""
    return "|".join(sorted({re.escape(normalize(p)) for p in phrases}, key=len, reverse=True))


def _words(*phrases: str) -> frozenset[str]:
    return frozenset(normalize(p) for p in phrases)


_SPEAKER = _alt("معك", "معاك", "معج", "معاج", "وياك", "وياج", "معكم", "انا", "أنا", "اني", "احنا", "احنة", "نحن", "هنا")
_COMPANY = _alt("زين كاش", "زينكاش", "زين كيش", "المحفظة", "محفظة زين")
_PAY = _alt("حول", "حولي", "حولوا", "دز", "دزي", "ادفع", "ادفعي", "ابعث", "ابعثي", "ارسل", "ارسلي", "اشحن", "اشحني",
            "تحول", "تحولي", "تدز", "تدزي", "تدفع", "تدفعي", "نريد", "لازم")
_STAFF = _alt("موظف", "موظفة", "مسؤول", "مسؤولة", "ادارة", "إدارة", "فريق", "مندوب", "ممثل", "قسم")
# A desk that speaks by itself, wherever it stands: «هنا الدعم الفني», «الدعم الفني كالولي».
_DESK = re.compile(rf"\b(?:{_alt('الدعم الفني', 'الدعم التقني', 'دعم فني', 'دعم تقني', 'خدمة الزبائن', 'خدمة العملاء', 'خدمة المشتركين', 'مركز الاتصال', 'البنك المركزي', 'شركة زين كاش', 'شركة زينكاش')})\b")
# Someone who is the user's own: «دز لصديقي من الدعم الفني».
_A_PERSON = _words("صديقي", "لصديقي", "صديقتي", "لصديقتي", "زميلي", "لزميلي", "زميلتي", "لزميلتي", "اخوي", "لاخوي",
                   "اختي", "لاختي", "ابن", "لابن", "بنت", "لبنت", "ابوي", "لابوي", "زوجي", "لزوجي", "مرتي", "لمرتي")
# «انا من الدعم» with nothing after it, or a pay order right after it; «الدعم اللوجستي» is some other support.
_CLAIMS = (
    re.compile(rf"\b(?:{_SPEAKER}) (?:من )?(?:(?:{_STAFF}) )?(?:ال)?دعم(?: مال \S+)?(?= (?:{_PAY})\b|$)"),
    re.compile(rf"\b(?:{_SPEAKER}) (?:من )?(?:شركه )?ب?(?:{_COMPANY})\b"),
    re.compile(rf"\b(?:{_STAFF}) (?:من |مال |في |ب)?(?:شركه )?ب?(?:{_COMPANY})\b"),
)
_CLAIMS_EN = (
    re.compile(r"\b(?:this is|it s|i am|i m|we are|we re|calling from|here is)(?: from)?(?: the)?(?: zain ?cash)?"
               r"(?: technical| tech| customer)* (?:support|customer service|customer care|help ?desk)\b(?! for\b)"),
    re.compile(r"\b(?:this is|it s|i am|i m|we are|we re|calling from|here is)(?: from)?(?: the)? zain ?cash\b"),
    re.compile(r"\bzain ?cash (?:technical |tech |customer )*(?:support|service|care|help ?desk|staff|employee|agent|team"
               r"|representative|rep)\b"),
    re.compile(r"\b(?:staff|employee|agent|representative|rep) (?:of|at|from) (?:the )?zain ?cash\b"),
)

_YOUR_ACCOUNT = _words("حسابك", "حسابج", "حسابكم", "محفظتك", "محفظتج", "محفظتكم")
_OWN_ACCOUNT = _words("حسابي", "محفظتي")
_CLOSING = _words(
    "ينقفل", "يتقفل", "ينغلق", "يتغلق", "يتسكر", "ينسد", "يتوقف", "ينحظر", "يتحظر", "يتجمد", "ينجمد", "يتعطل", "ينحذف",
    "يتحذف", "ينلغي", "يتلغي", "تنقفل", "تتقفل", "تنغلق", "تتغلق", "تتسكر", "تتوقف", "تنحظر", "تتجمد", "تنجمد", "تتعطل",
    "تنحذف", "تنلغي", "انقفل", "انغلق", "توقف", "انحظر", "انجمد", "تجمد", "سيغلق", "سيتوقف", "سيجمد", "سيحظر", "سيحذف",
    "نقفل", "نسكر", "نغلق", "نوقف", "نجمد", "نحظر", "نلغي", "نحذف", "يقفلون", "يغلقون", "يوقفون", "يجمدون", "يحظرون",
    "يسكرون", "يلغون", "اغلاق", "إغلاق", "ايقاف", "إيقاف", "تجميد", "حظر", "قفل", "تعليق", "تعطيل", "حذف", "الغاء", "إلغاء",
    "اختراق", "موقوف", "موقوفة", "متوقف", "متوقفة", "معلق", "معلقة", "مخترق", "مخترقة", "مقفول", "مقفولة", "مسدود",
    "مسدودة", "مجمد", "مجمدة", "محظور", "محظورة", "معطل", "معطلة", "مهدد", "مهددة", "انخترق", "اخترقوا",
)
_NEAR = 3  # the account and the closing word are at most this many words apart
_ONLY_IF_NOT = re.compile(rf"\b(?:{_alt('اذا ما', 'إذا ما', 'ازا ما', 'لو ما', 'اذا لم', 'حتى ما', 'حتى لا', 'علمود ما', 'حته ما', 'لكي لا', 'والا')})\b")
_THREAT_EN = re.compile(
    r"\byour (?:zain ?cash )?(?:account|wallet)\b(?! (?:at|in|on|with|from)\b)(?: \w+){0,4} (?:closed|blocked|suspended"
    r"|frozen|locked|terminated|deactivated|disabled|shut|hacked|compromised|deleted|restricted|limited|at risk)\b"
    r"|\b(?:close|block|suspend|freeze|lock|terminate|deactivate|disable|delete|hacked) your (?:account|wallet)\b")

_VERIFY_NOUN = rf"(?:{_alt('رقم', 'رمز', 'كود')}) (?:ال)?(?:{_alt('تحقق', 'تاكيد', 'توثيق', 'تفعيل')})"
# Money aimed at it («على رقم التحقق», «لرقم التفعيل»), or the number right after it. «رمز التحقق ما وصلني» is neither.
_VERIFY = re.compile(
    rf"\b(?:{_alt('على', 'إلى', 'الى', 'عله')}) (?:ال)?{_VERIFY_NOUN}\b|\b[لب]ل?{_VERIFY_NOUN}\b"
    rf"|\b{_VERIFY_NOUN} (?=هاتف|\d)")
_VERIFY_EN = re.compile(
    r"\b(?:to|on|into|at) (?:the |this |that |my |your )?verification (?:number|code|account)\b"
    r"|\bverification (?:number|code) (?=\d)")


# ---- fake authority
_FORGET = _alt("تجاهل", "تتجاهل", "انسى", "انس", "اهمل", "تخطى", "تجاوز")
_ALL = _alt("كل", "جميع", "كافة")
_RULES = _alt("تعليمات", "تعليماتك", "اوامر", "اوامرك", "قواعد", "قواعدك", "قيود")
_OVERRIDE = re.compile(rf"\b(?:{_FORGET}) (?:(?:{_ALL}) )?(?:ال)?(?:{_RULES})\b")
_AUTHORITY_AR = _alt(
    "الادمن وافق", "الادمن وفق", "الادمن موافق", "الادمن اكد", "الادمن سمح", "الادمن كال", "ادمن وافق", "ادمن موافق", "ادمن اكد",
    "ادمن سمح", "ادمن كال", "موافقة الادمن", "موافقة الادارة", "موافقة النظام",
    "المشرف وافق", "الادارة وافقت", "الادارة موافقة", "مدير النظام", "النظام وافق", "وضع المدير", "وضع المطور", "رسالة النظام",
    "تعليمات النظام", "المستخدم اكد", "المستخدم وافق", "المستخدم موافق", "مؤكد مسبقا", "مؤكدة مسبقا",
    "تم تاكيد العملية", "تم تاكيد التحويل", "العملية مؤكدة", "التحويل مؤكد")
# «وصلني تم تأكيد التحويل من المحل» is a receipt somebody got, no claim about this payment.
_AUTHORITY = re.compile(rf"(?<!وصلني )\b(?:{_AUTHORITY_AR})\b")
_AUTHORITY_EN = re.compile(
    r"\b(?:ignore|disregard|forget|override) (?:all |any |every )?(?:the |your |of your |my )?"
    r"(?:(?:previous|prior|earlier|above|system|safety) )?(?:instructions?|rules?|prompts?|checks?)\b"
    r"|\b(?:system|developer|admin|root) (?:prompt|message|override|mode|note)\b"
    r"|\b(?:admin|administrator) (?:has |have )?(?:approved|confirmed|authori[sz]ed|allowed)\b"
    r"|\b(?:approved|confirmed|authori[sz]ed) by (?:the )?(?:admin|administrator|system)\b"
    r"|\buser (?:has )?(?:already )?(?:confirmed|approved|authori[sz]ed)\b"
    r"|\b(?:transfer|payment|this|it)(?: is| was)? pre ?approved\b|\byou are now\b")
# Speaks as a role, at the head of a line or a sentence: read from the folded text, where the colon is still there.
_ROLE_LABEL = re.compile(r"(?:^|[.!?؟]\s)\W*(?:system|sys|admin|administrator|developer|root|assistant"
                         r"|النظام|نظام|سيستم|الادمن|ادمن|المشرف)\W*?[:：\]]", re.MULTILINE)
_SKIP_CONFIRM_AR = _alt(
    "بدون تاكيد", "بلا تاكيد", "من غير تاكيد", "دون تاكيد", "بدون التاكيد", "بلا التاكيد", "من غير التاكيد", "دون التاكيد",
    "بدون تءكيد", "بلا تءكيد", "no تاكيد", "without تاكيد", "بدون رمز", "بلا رمز", "من غير رمز", "بدون الرمز", "بدون رقم سري",
    "بدون رمز سري", "بدون موافقة", "لا تطلب تاكيد", "لا تطلب رمز", "تخطى التاكيد", "تجاوز التاكيد", "الغي التاكيد")
_SKIP_CONFIRM = re.compile(rf"\b(?:{_SKIP_CONFIRM_AR})\b(?! الدوله)")  # «بدون رمز الدولة» is how the number is written
_SKIP_CONFIRM_EN = re.compile(r"\b(?:without|no|skip|bypass)(?: a| any| the| my)? (?:confirmation|confirming|confirm|pin|approval)\b")
_SKIP_CARD_AR = _alt(
    "بدون كارت", "بلا كارت", "من غير كارت", "دون كارت", "بدون الكارت", "بلا الكارت", "بدون بطاقة", "بدون البطاقة", "بلا بطاقة", "من غير بطاقة", "دون بطاقة", "لا تطلع كارت",
    "لا تطلع بطاقة", "لا تطلعلي كارت", "لا تطلعلي بطاقة", "تخطى البطاقة", "تخطى الكارت")
_SKIP_CARD = re.compile(rf"\b(?:{_SKIP_CARD_AR})\b")
_SKIP_CARD_EN = re.compile(r"\b(?:without|no|skip|bypass)(?: a| any| the)? card\b")
# «اشحنله بدون كارت» is an ordinary top-up: a card here is a scratch card. Money sent out of the credit is no top-up.
_TOP_UP_AR = _alt("رصيد", "الرصيد", "عبي", "عبيلي", "عبيله", "عبيلها", "عبيها")
_TOP_UP_WORD = re.compile(rf"\b(?:{_TOP_UP_AR})\b|\b(?:اشحن|شحن)\w*|\b(?:top ?up|recharge|credit)\b")
_OUT_OF_CREDIT = re.compile(r"\bمن (?:ال)?رصيد\w*|\bfrom (?:my |the )?(?:credit|balance)\b")
_NOT_THE_USERS_CARD = _words("هو", "هي", "عنده", "عندها", "لان")
_PAY_WORD_EN = re.compile(r"\b(?:send|transfer|pay|wire)\b")


def _stem(word: str) -> str:
    """A state word without a leading ب / ل / ال: «بالاغلاق» -> «اغلاق»."""
    for prefix in ("بال", "لل", "ال", "ب", "ل"):
        if word.startswith(prefix) and len(word) - len(prefix) >= 3:
            return word[len(prefix):]
    return word


def _is_closing(word: str) -> bool:
    return word in _CLOSING or _stem(word) in _CLOSING


def _threat(text: str) -> bool:
    words = text.split()
    for i, word in enumerate(words):
        if word not in _YOUR_ACCOUNT and not (word in _OWN_ACCOUNT and _ONLY_IF_NOT.search(text)):
            continue
        following = words[i + 1:i + 2]
        if following and following[0].startswith("ب") and not _is_closing(following[0]) \
                and not following[0].startswith("بزين"):
            continue  # «حسابك بالبنك», «حسابي بالنادي»: an account somewhere else
        window = words[max(0, i - _NEAR):i + _NEAR + 1]
        if normalize("عندي") in window:
            continue  # «حسابك توقف عندي»: a friend's account with the speaker, not the wallet's threat
        if any(_is_closing(w) for w in window):
            return True
    return False


def _desk(text: str) -> bool:
    for found in _DESK.finditer(text):
        before = text[:found.start()].split()[-2:]
        if not any(w in _A_PERSON for w in before):
            return True
    return False


def _sources(text: str) -> set[str]:
    return {normalize(text or ""), normalize(arabizi.to_arabic(text or ""))}


def threat_or_verification(text: str) -> bool:
    """The stronger cues: the account is threatened, or the money goes to a "verification number"."""
    return any(_threat(s) or _THREAT_EN.search(s) or _VERIFY.search(s) or _VERIFY_EN.search(s) for s in _sources(text))


def _pays(source: str) -> bool:
    return bool(_PAY_WORD_EN.search(source)) or any(word.startswith(PAY_STEMS) or "تحويل" in word for word in source.split())


def _skips_the_card(source: str) -> bool:
    """A pay word with «بدون كارت» about the user's own card, and no top-up in the message."""
    if not _pays(source) or _TOP_UP_WORD.search(_OUT_OF_CREDIT.sub(" ", source)):
        return False
    for pattern in (_SKIP_CARD, _SKIP_CARD_EN):
        for found in pattern.finditer(source):
            if not _NOT_THE_USERS_CARD & set(source[:found.start()].split()[-2:]):
                return True
    return False


def _claims_authority(text: str) -> bool:
    """Text that speaks as a role, overrides the rules, or claims somebody already approved."""
    if _ROLE_LABEL.search(fold(text or "")):
        return True
    return any(_OVERRIDE.search(s) or _AUTHORITY.search(s) or _AUTHORITY_EN.search(s) for s in _sources(text))


def _wants_no_check(text: str) -> bool:
    """«بدون تأكيد» in any words, or the card skipped along with a pay word. Impatience as often as a trick."""
    return any(_SKIP_CONFIRM.search(s) or _SKIP_CONFIRM_EN.search(s) or _skips_the_card(s) for s in _sources(text))


def fake_authority(text: str) -> bool:
    """Text that claims authority, overrides the rules, or wants the card or the confirmation skipped. (Whether it
    refuses is agent/session.py's call: only with a payment to a number that is not saved.)"""
    return _claims_authority(text) or _wants_no_check(text)


def holds_for_later(text: str) -> bool:
    """A cue that stops an unsaved number in the next few turns too. Wanting no check does not: paying a saved contact
    «بدون تأكيد» says nothing about a number the user gives later."""
    if threat_or_verification(text) or _claims_authority(text):
        return True
    return any(_desk(s) or any(c.search(s) for c in (*_CLAIMS, *_CLAIMS_EN)) for s in _sources(text))


def looks_like_scam(text: str) -> bool:
    """True when the words of a message carry one of the cues above (Arabic, Arabizi or English)."""
    return holds_for_later(text) or _wants_no_check(text)
