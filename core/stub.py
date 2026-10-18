"""StubCore: an open, rule-based stand-in for the closed-source conversational core.

It exists so the open payment layer can be tested and demoed offline. It knows a few dozen Iraqi
keywords and nothing else: no context beyond one message, no paraphrase, no voice. Its job is to
point at the user's words (payee, biller, amount); the orchestrator checks and resolves them.

Rules, in order: cancel words -> cancel; out-of-scope services -> unsupported; top-up cues
(اشحن, عبي, كارت, رصيد + network) -> topup; bill verbs/words (ادفع, سدد, جدد, فاتورة, المولدة ...)
-> pay_bill; transfer verbs (دز, حول, ارسل, ابعث) -> transfer; "شكد عندي / رصيدي" -> balance;
anything else -> chat. Two requests joined by و ("ادفع المولدة ودز لأحمد خمسين") are split.
Arabizi is read through payments.arabizi first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core.interface import Intent
from payments import arabizi
from payments.amounts import find_amount
from payments.messages import render
from payments.text import fold


def _key(word: str) -> str:
    """Folded word without punctuation: "الفاتورة،" -> "الفاتوره"."""
    return re.sub(r"[^\w]|_", "", fold(word))


def _keys(*words: str) -> set[str]:
    return {_key(w) for w in words}


TRANSFER_PREFIXES = tuple(_keys("دز", "حول", "ارسل", "ابعث", "بعث", "انطي",
                                "ادز", "تدز", "ندز", "احول", "تحول", "نحول", "ترسل", "تبعث"))
BILL_PREFIXES = tuple(_keys("ادفع", "دفع", "سدد", "اسدد", "جدد", "اجدد", "تدفع", "ندفع", "تسدد", "نسدد"))
BILL_VERBS = _keys("خلصلي", "خلصيلي", "خلصها")
BILL_NOUNS = _keys("فاتورة", "فاتورتي", "فواتير", "اشتراك")
TOPUP_PREFIXES = tuple(_keys("اشحن", "شحن", "تشحن", "نشحن"))
TOPUP_WORDS = _keys("عبي", "عبيلي", "عبيله", "عبيلها", "عبيها", "كارت", "كارتات", "كرت")
RASEED = _key("رصيد")
BALANCE_WORDS = _keys("رصيدي", "رصيدج", "رصيدك", "محفظتي", "محفظتج", "محفظتك")
QUESTION_WORDS = _keys("شكد", "شقد", "كم", "اشكد")
NEGATIONS = _keys("مو", "مش", "موش", "لا", "ما", "بدون")
BILLER_WORDS = _keys(
    "كهرباء", "كهربا", "كهرباء", "وطنية", "مولدة", "مولده", "مولد", "امبير", "امبيرات",
    "نت", "انترنت", "انترنيت", "ماي", "ماء", "مياه", "اسالة",
)
OPERATORS = {_key("زين"): "zain", _key("آسيا"): "asiacell", _key("آسياسيل"): "asiacell",
             _key("اسيسيل"): "asiacell", _key("كورك"): "korek"}
CANCEL_WORDS = _keys("بطلت", "بطلنا", "الغي", "الغيها", "الغيه", "لغيها", "كنسل", "كانسل", "خليها",
                     "بعدين", "cancel")
CANCEL_PHRASES = ("لا تدفع", "لا تدز", "لا تحول", "ما اريد", "ما ابي", "لا تسوي")
UNSUPPORTED_STEMS = tuple(_keys(
    "قرض", "سلفة", "تذكرة", "طيارة", "طيران", "حجز", "احجز", "توفير", "مصرف", "اسحب", "سحب",
    "ببجي", "شدات", "ماستر", "فيزا", "استثمار", "اسهم", "بتكوين", "كريبتو", "تداول", "فندق",
))
STOP = _keys(
    "بسرعة", "هسه", "هسة", "هسا", "هسع", "ضروري", "عاجل", "يلا", "رجاء", "رجاءا", "الله", "يخليك",
    "يخليج", "يمعود", "عيني", "حبيبي", "حبيبتي", "اليوم", "باجر", "قبل", "بعد", "من", "على",
    "اللي", "الي", "مال", "هالشهر", "الشهر", "شهر", "فات", "الماضي", "السابق", "هذا", "هاي",
    "اريد", "ابي", "بليز", "كل", "عندي", "بس", "ياريت", "مبلغ", "دينار", "فلوس", "رصيد", "مو",
    "مش", "لا", "ما", "و", "او", "لو", "يا", "خطي", "خط", "رقم", "رقمه", "لان", "حتى", "اصبر",
    "شوية", "ممكن", "تكدر", "تكدرين", "هم", "مرة", "ثانية",
)
APPROX_WORDS = _keys("حوالي", "تقريبا", "بحدود", "يمكن")
DATIVE_PRONOUNS = _keys("لي", "له", "لها", "لك", "لج", "لكم", "لهم", "لنا", "لان", "لكن", "لحد",
                        "لازم", "لسه", "لهسه", "لو", "للبيت")
CURRENT_MONTH = _keys("هالشهر", "الحالي")
PREVIOUS_MONTH = _keys("فات", "الفات", "الماضي", "السابق", "القبل")
MONTH = _keys("الشهر", "شهر")


@dataclass
class _Word:
    orig: str  # as typed (Latin words keep their spelling)
    ar: str  # Arabic reading
    key: str  # folded, no punctuation
    start: int  # offset of `ar` in the segment's Arabic text
    end: int

    @property
    def bare(self) -> str:
        """Key without a leading و / ب / ال: "والماي" -> "ماي", "بالمصرف" -> "مصرف"."""
        k = self.key
        for prefix in ("وال", "بال", "ال", "و", "ب"):
            if k.startswith(prefix) and len(k) - len(prefix) >= 2:
                return k[len(prefix):]
        return k


def _is_transfer_verb(k: str) -> bool:
    return k.startswith(TRANSFER_PREFIXES)


def _is_bill_verb(k: str) -> bool:
    return k.startswith(BILL_PREFIXES) or k in BILL_VERBS


def _is_topup_verb(k: str) -> bool:
    return k.startswith(TOPUP_PREFIXES) or k in TOPUP_WORDS


def _is_verb(k: str) -> bool:
    return _is_transfer_verb(k) or _is_bill_verb(k) or _is_topup_verb(k)


def _is_biller(word: _Word) -> bool:
    return word.key in BILLER_WORDS or word.bare in BILLER_WORDS


def _words(pairs: list[tuple[str, str]]) -> tuple[list[_Word], str]:
    """Words with offsets in the joined Arabic text of a segment."""
    out, pos = [], 0
    for orig, ar in pairs:
        out.append(_Word(orig, ar, _key(ar), pos, pos + len(ar)))
        pos += len(ar) + 1
    return out, " ".join(ar for _, ar in pairs)


def _clean(text: str) -> str:
    return re.sub(r"[^\w\s$]|_", "", text).strip()


class StubCore:
    """Keyword-based Core (core/interface.py)."""

    def understand(self, text: str, context: dict[str, Any] | None = None) -> Intent:
        context = context or {}
        pairs = arabizi.to_arabic_words(text)
        words, _ = _words(pairs)
        if self._is_cancel(words):
            return Intent("cancel")
        segments = self._split(pairs)
        intents, previous = [], None
        for segment in segments:
            intent = self._read(segment, context, previous)
            intents.append(intent)
            previous = intent.action
        first = intents[0]
        first.extra = [i for i in intents[1:] if i.action != "chat"]
        return first

    def phrase(self, question_key: str, slots: dict[str, Any]) -> str:
        slots = dict(slots)
        gender = slots.pop("gender", "f")
        return render(question_key, gender, **slots)

    # ---------------------------------------------------------------- pieces

    @staticmethod
    def _is_cancel(words: list[_Word]) -> bool:
        keys = [w.key for w in words]
        joined = " ".join(keys)
        has_verb = any(_is_verb(k) for k in keys)
        has_amount = find_amount(" ".join(w.ar for w in words)) is not None
        strong = any(k in CANCEL_WORDS for k in keys if k != _key("خليها") or not has_amount)
        phrase = any(p in joined for p in (" ".join(_key(x) for x in ph.split()) for ph in CANCEL_PHRASES))
        starts_no = bool(keys) and keys[0] in _keys("لا", "لاء", "no")
        return strong or phrase or (starts_no and not has_verb and not has_amount)

    @staticmethod
    def _split(pairs: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
        """Split "A وB" when B starts a new request (a verb or a biller word after و)."""
        segments, current = [], []
        for orig, ar in pairs:
            k = _key(ar)
            rest = k[1:]
            starts_new = (
                current and k.startswith("و") and len(rest) >= 2
                and (_is_verb(rest) or rest in BILLER_WORDS or rest.removeprefix("ال") in BILLER_WORDS)
            )
            if starts_new:
                segments.append(current)
                new_orig = orig[1:] if orig == ar else re.sub(r"^(w|wa|u)\s*", "", orig, flags=re.I)
                current = [(new_orig, ar[1:])]
            else:
                current.append((orig, ar))
        segments.append(current)
        return [s for s in segments if s]

    def _read(self, pairs: list[tuple[str, str]], context: dict, previous: str | None) -> Intent:
        """One request from one segment."""
        words, ar_text = _words(pairs)
        keys = [w.key for w in words]
        bares = [w.bare for w in words]
        if any(stem in k for k in keys for stem in UNSUPPORTED_STEMS):
            return Intent("unsupported")

        found = find_amount(ar_text)
        amount_idx: set[int] = set()
        amount_text = currency = None
        if found:
            amount, amount_text = found
            currency = amount.currency
            start = ar_text.find(amount_text)
            amount_idx = {i for i, w in enumerate(words) if w.start < start + len(amount_text) and w.end > start}
            first = min(amount_idx, default=None)
            if first and words[first - 1].key in APPROX_WORDS:  # "حوالي خمسين": the qualifier is part of it
                amount_idx.add(first - 1)
                amount_text = f"{words[first - 1].ar} {amount_text}"

        question = any(k in QUESTION_WORDS for k in keys)
        raseed_at = [i for i, b in enumerate(bares) if b == RASEED]
        raseed = any(not (i > 0 and keys[i - 1] in NEGATIONS) for i in raseed_at)
        topup_cue = any(_is_topup_verb(k) for k in keys) or (raseed and not question)
        bill_cue = any(_is_bill_verb(k) for k in keys) or any(b in BILL_NOUNS or k in BILL_NOUNS
                                                                for k, b in zip(keys, bares))
        transfer_cue = any(_is_transfer_verb(k) for k in keys)
        biller_idx = [i for i, w in enumerate(words) if _is_biller(w) and i not in amount_idx]

        asking = context.get("asking")
        operator_idx = [i for i, b in enumerate(bares)
                        if b in OPERATORS and (topup_cue or b != _key("زين") or asking == "operator")]
        operator = OPERATORS[bares[operator_idx[0]]] if operator_idx else None

        skip = set(amount_idx) | set(operator_idx) | set(biller_idx)
        skip |= {i for i, k in enumerate(keys) if _is_verb(k) or k in STOP or bares[i] in STOP
                 or k in APPROX_WORDS or k in BILL_NOUNS or bares[i] in BILL_NOUNS or bares[i] == RASEED}

        if topup_cue:
            action = "topup"
        elif bill_cue or (biller_idx and not transfer_cue):
            action = "pay_bill"
            has_bill_noun = any(k in BILL_NOUNS or b in BILL_NOUNS for k, b in zip(keys, bares))
            if not biller_idx and not has_bill_noun and amount_text and self._payee(words, skip, None):
                action = "transfer"  # "ادفع لأحمد خمسين": paying a person is a transfer
        elif transfer_cue:
            action = "transfer"
        elif any(k in BALANCE_WORDS for k in keys) or (question and any(
                k in _keys("عندي", "باقي", "رصيد", "الرصيد") for k in keys)):
            return Intent("balance")
        elif previous == "pay_bill" and biller_idx:
            action = "pay_bill"
        else:
            action = "chat"

        verb_at = next((i for i, k in enumerate(keys) if _is_verb(k)), None)
        payee = self._payee(words, skip, after=verb_at if action == "transfer" else None)
        biller = self._biller(words, biller_idx, skip) if action in ("pay_bill", "chat") else None
        if action == "chat" and not biller_idx:
            biller = None
        return Intent(
            action,
            payee_mention=payee if action in ("transfer", "topup", "chat") else None,
            biller_mention=biller,
            amount_text=amount_text,
            currency=currency,
            operator=operator,
            period=self._period(keys) if action in ("pay_bill", "chat") else None,
        )

    @staticmethod
    def _payee(words: list[_Word], skip: set[int], after: int | None) -> str | None:
        """The ل-word ("لأحمد", "l ahmad") and up to two following name words."""
        start = next((i for i, w in enumerate(words)
                      if i not in skip and w.key.startswith("ل") and len(w.key) >= 3
                      and w.key not in DATIVE_PRONOUNS and w.key[1:] not in BILLER_WORDS), None)
        if start is None and after is not None:  # "دز احمد خمسين": a name after the verb, else before it
            order = [*range(after + 1, len(words)), *range(after)]
            start = next((i for i in order
                          if i not in skip and words[i].key and not words[i].key.isdigit()), None)
        if start is None:
            return None
        end = start + 1
        while (end < len(words) and end - start < 3 and end not in skip
               and words[end].key and not words[end].key.isdigit() and not words[end].key.startswith("و")):
            end += 1
        mention = " ".join(_clean(w.orig) for w in words[start:end]).strip()
        return mention or None

    @staticmethod
    def _biller(words: list[_Word], biller_idx: list[int], skip: set[int]) -> str | None:
        """The biller word plus a qualifier next to it ("سما نت", "مولدة المحل")."""
        if not biller_idx:
            return None
        i = biller_idx[0]
        start, end = i, i + 1
        if i > 0 and (i - 1) not in skip and words[i - 1].key:
            start = i - 1
        if end < len(words) and end not in skip and words[end].key and not words[end].key.startswith("و"):
            end += 1
        return " ".join(_clean(w.ar) for w in words[start:end]).strip() or None

    @staticmethod
    def _period(keys: list[str]) -> str | None:
        if any(k in CURRENT_MONTH for k in keys):
            return "current"
        if any(k in MONTH for k in keys) and any(k in PREVIOUS_MONTH for k in keys):
            return "previous"
        return None
