"""A request to take back money already sent. Deterministic: the agent cannot reverse a transfer, so it says so.

A card here would send more money, so this runs before the core's reading counts. A false alarm costs a request, so it
stays quiet when the message also asks for a payment now («عمر رجعلي الفلوس، دز لهادي 50 الف», "pay hadi 20000 refund for
the shoes"), changes an amount («لا حولت غلط خليها 45»), or opens with a cancel («الغي، دفعتله كاش»).

«رجعلي» alone is two things. First in its clause it is an order: «رجعلي الفلوس». After someone's name it is what he did:
«عمر رجعلي العشرين». «الغي» alone stays the cancel word of the open card; with a transfer of the past it is a reversal.
"""

from __future__ import annotations

import re

from payments import arabizi
from payments.amounts import amount_phrases
from payments.names import is_verb_form, normalize
from payments.text import fold


def _words(*phrases: str) -> frozenset[str]:
    return frozenset(normalize(p) for p in phrases)


# An order wherever it stands.
_BACK = _words("ارجعلي", "ارجعها", "ترجعلي", "ترجعها", "استرجع", "استرجعلي", "استرجعها", "استرجاع", "استرداد", "استرد", "اعكس", "اعكسها")
# An order only first in its clause; after a name it is what somebody did.
_BACK_IF_FIRST = _words("رجع", "رجعلي", "رجعها", "رجعه", "رجعهم", "رجعولي", "رجعوها")
_BEFORE_AN_ORDER = _words("لو", "سمحت", "سمحتي", "رجاء", "ارجوك", "ارجوكم", "بليز", "لطفا", "يا", "لا", "اريد", "اريدك", "لازم", "ممكن",
                          "هسه", "عفوا", "اخي", "اختي")
_UNDO = _words("الغي", "لغي", "الغيها", "لغيها", "بطل", "بطلها")
_MISTAKE = _words("غلط", "بالغلط", "غلطت", "خطأ", "بالخطأ")
_PAST = _words("امس", "البارحة", "البارح", "مبارح")
# The speaker's own past: دزيتها, حولتله, دفعتها. «دزتلي» and «حولتلي» are money that came in.
_SENT = tuple(normalize(w) for w in ("دزيت", "حولت", "ارسلت", "بعثت", "ابعثت", "انطيت", "دفعت", "سددت"))
_CHANGED_MIND = _words("رايي", "رأيي")  # «حولت رايي»: I changed my mind
_TRANSFER_NOUNS = _words("حوالة", "الحوالة", "حوالتي", "تحويل", "التحويل", "تحويلي", "دفعة", "الدفعة", "دفعتي")
_SENT_NOUNS = _TRANSFER_NOUNS | _words("الفلوس", "فلوسي", "مبلغي", "المبلغ")
_NEW_AMOUNT = _words("خليها", "خليه", "خلوها", "سويها", "سويه", "عدلها", "عدله")
_PAY_NOW_LATIN = {"dez", "diz", "dizz", "7awil", "7awel", "7awl", "idfa3", "sadid"}
_PAY_NOW_AR = _words("سدد", "سددلي", "سددها", "ادفعلي", "ادفعها", "اشحن", "اشحنلي", "اشحنله", "عبي", "عبيلي", "عبيله")
_PAY_NOW_EN = re.compile(r"(?:^|[.,;!?؟،]\s*|\b(?:and|please|now|then)\s+)(?:send|transfer|pay|wire)\b(?! back)")
_CANCEL_FIRST = re.compile(r"^\W*(?:" + "|".join(sorted(_UNDO, key=len, reverse=True)) + r"|cancel)\s*[.,;!،؛]")
_SOMEONE_RETURNED = re.compile(r"\b(?:gave|paid|sent|returned)(?: it| that| the money)? (?:me|back to me)\b")
_EN = re.compile(
    r"\b(?:refund|reimburse|reverse|reversal|chargeback)\b"
    r"|\bmoney back\b"
    r"|\b(?:get|take|want) (?:my|the|\d+)\b.*\bback\b"
    r"|\b(?:undo|cancel|take back|get back|send back|give back|return)\b.*\b(?:sent|paid|transferred)\b"
    r"|\b(?:sent|paid|transferred)\b.*\b(?:by mistake|by accident|wrong person|wrong number|mistakenly)\b")


def _clauses(text: str) -> list[list[str]]:
    return [normalize(part).split() for part in re.split(r"[.,;:!?؟،؛\n]", text)]


def _pays_now(text: str) -> bool:
    """A pay order in the message: a verb that is not the speaker's own past («دز», "pay hadi"), never «دزيتها»."""
    folded = fold(text)
    if _PAY_NOW_EN.search(folded) or set(folded.split()) & _PAY_NOW_LATIN:
        return True
    return any((is_verb_form(w) or w in _PAY_NOW_AR) and not w.startswith(_SENT) and w not in {"send", "transfer", "pay", "wire"}
               for w in normalize(text).split())


def _order_to_return(text: str) -> bool:
    """A word for «give it back» standing as an order: anywhere for the clear ones, first in its clause for «رجعلي»."""
    for words in _clauses(text):
        if set(words) & _BACK:
            return True
        i = 0
        while i < len(words) and words[i] in _BEFORE_AN_ORDER:
            i += 1
        if i < len(words) and words[i] in _BACK_IF_FIRST:
            return True
    return False


def asks_for_money_back(text: str) -> bool:
    """True when the message asks to reverse money already sent (Arabic, Arabizi or English)."""
    text = text or ""
    words = set(normalize(text).split())
    if _pays_now(text) or _CANCEL_FIRST.search(fold(text)) or _SOMEONE_RETURNED.search(fold(text)):
        return False
    if words & _NEW_AMOUNT and amount_phrases(arabizi.to_arabic(text)):
        return False
    for source in {text, arabizi.to_arabic(text)}:
        lowered = normalize(source).lower()
        if _EN.search(lowered):
            return True
        words = set(lowered.split())
        sent = any(w.startswith(_SENT) and not w.endswith("لي") for w in words) and not words & _CHANGED_MIND
        if _order_to_return(source) and (sent or words & _SENT_NOUNS or amount_phrases(arabizi.to_arabic(source))):
            return True
        if sent and words & (_UNDO | _MISTAKE):
            return True
        if words & _UNDO and words & _TRANSFER_NOUNS and words & _PAST:  # «الغي الحوالة مال امس»
            return True
    return False
