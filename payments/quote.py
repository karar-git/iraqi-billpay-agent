"""Confirmation card: a deterministic template built from a quote, in the user's language.

The card is the only place the user approves money. It shows a title (transfer / bill / top-up), who
gets the money (name + the last 4 digits of the phone, or the biller + holder + account), the amount
in digits and in words, the fee and the total, the balance after, how long the card is valid, any
soft-flag warnings, and three buttons. Languages: 'iq' (Baghdadi, the reference), 'msa' (فصحى), 'en'.
Card.spoken is a short summary for a voice reply: who, how much in words, the fee, and which button to
tap. It never holds a PIN.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields

from payments import rules
from payments.messages import catalog, flag_line, format_iqd, format_period

MILLION = 1_000_000
IRAQI, MSA, ENGLISH = "iq", "msa", "en"


@dataclass(frozen=True)
class Button:
    id: str  # 'confirm' | 'change' | 'cancel' -> Session.handle_button(..., button=id)
    label: str


BUTTONS = (
    Button("confirm", "إي، ادفع"),
    Button("change", "غيّر"),
    Button("cancel", "لا، بطّلت"),
)
# The same three buttons in each language. Ids never change; titles stay within WhatsApp's 20 characters.
BUTTONS_BY_LANG = {
    IRAQI: BUTTONS,
    MSA: (Button("confirm", "نعم، ادفع"), Button("change", "تعديل"), Button("cancel", "لا، إلغاء")),
    ENGLISH: (Button("confirm", "Yes, pay"), Button("change", "Change"), Button("cancel", "No, cancel")),
}
CARD_TITLES = {
    IRAQI: {"transfer": "تأكيد تحويل", "bill": "تأكيد دفع فاتورة", "topup": "تأكيد شحن رصيد"},
    MSA: {"transfer": "تأكيد تحويل", "bill": "تأكيد دفع فاتورة", "topup": "تأكيد شحن رصيد"},
    ENGLISH: {"transfer": "Confirm transfer", "bill": "Confirm bill payment", "topup": "Confirm top-up"},
}


def buttons_for(lang: str | None = None) -> tuple[Button, ...]:
    return BUTTONS_BY_LANG.get(lang or IRAQI, BUTTONS)


def card_title(kind: str, lang: str | None = None) -> str:
    titles = CARD_TITLES.get(lang or IRAQI, CARD_TITLES[IRAQI])
    return titles.get(kind, titles["transfer"])


@dataclass
class QuoteDetails:
    """What the card needs: the wallet quote plus display names looked up by the orchestrator."""

    quote_id: str
    kind: str  # 'transfer' | 'bill' | 'topup'
    amount: int
    fee: int = 0
    payee_id: str = ""  # contact_id, bill account id or top-up phone (same key as rules.PastPayment)
    payee_name: str = ""  # transfer: contact display name; bill: account holder name
    phone: str = ""  # transfer / top-up: only the last 4 digits are shown
    biller_name: str = ""  # bill: e.g. "مولدة الحاج كريم"
    period: str = ""  # bill: 'YYYY-MM'
    account_no: str = ""  # bill
    operator: str = ""  # top-up network name
    expires_in_min: int = 5

    @classmethod
    def from_dict(cls, data: Mapping) -> "QuoteDetails":
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in data.items() if k in known}
        if "quote_id" not in values and "id" in data:
            values["quote_id"] = data["id"]
        return cls(**values)


@dataclass
class Card:
    quote_id: str
    lines: list[str]
    flags: list[str]  # warning lines, in the card's language
    buttons: list[Button]
    flag_codes: list[str] = field(default_factory=list)
    title: str = ""  # "تأكيد تحويل" / "Confirm transfer" ...
    lang: str = IRAQI
    spoken: str = ""  # a short summary for a voice reply (never a PIN)

    @property
    def text(self) -> str:
        return "\n".join(self.lines[:-1] + self.flags + self.lines[-1:])


# ---- amount in Iraqi words -------------------------------------------------

_UNITS = {1: "واحد", 2: "اثنين", 3: "تلاثة", 4: "اربعة", 5: "خمسة",
          6: "ستة", 7: "سبعة", 8: "ثمنية", 9: "تسعة"}
_TEENS = {10: "عشرة", 11: "حدعش", 12: "ثنعش", 13: "تلطعش", 14: "اربطعش",
          15: "خمسطعش", 16: "ستطعش", 17: "سبعطعش", 18: "ثمنطعش", 19: "تسعطعش"}
_TENS = {20: "عشرين", 30: "تلاثين", 40: "اربعين", 50: "خمسين",
         60: "ستين", 70: "سبعين", 80: "ثمانين", 90: "تسعين"}
_HUNDREDS = {1: "مية", 2: "ميتين", 3: "تلاثمية", 4: "اربعمية", 5: "خمسمية",
             6: "ستمية", 7: "سبعمية", 8: "ثمنمية", 9: "تسعمية"}
_COUNTS = {3: "تلاث", 4: "اربع", 5: "خمس", 6: "ست", 7: "سبع", 8: "ثمن", 9: "تسع", 10: "عشر"}


def _below_1000(n: int) -> str:
    parts = []
    hundreds, rest = divmod(n, 100)
    if hundreds:
        parts.append(_HUNDREDS[hundreds])
    if 0 < rest < 10:
        parts.append(_UNITS[rest])
    elif 10 <= rest < 20:
        parts.append(_TEENS[rest])
    elif rest:
        unit, ten = rest % 10, rest - rest % 10
        parts.append(f"{_UNITS[unit]} و{_TENS[ten]}" if unit else _TENS[ten])
    return " و".join(parts)


def _plural_tail(k: int) -> int:
    """For a count above 10, its last two digits when they are 3 to 10 (103 -> 3, 110 -> 10), else 0:
    such a count takes the plural («مية وتلاث تالاف», «مئة وثلاثة آلاف»), not «ألف»."""
    tail = k % 100
    return tail if k > 10 and 3 <= tail <= 10 else 0


def _thousands(k: int) -> str:
    if k == 1:
        return "ألف"
    if k == 2:
        return "ألفين"
    if k <= 10:
        return f"{_COUNTS[k]} تالاف"
    tail = _plural_tail(k)
    if tail:
        return f"{_below_1000(k - tail)} و{_COUNTS[tail]} تالاف"
    return f"{_below_1000(k)} ألف"


def _millions(m: int) -> str:
    if m == 1:
        return "مليون"
    if m == 2:
        return "مليونين"
    if m <= 10:
        return f"{_COUNTS[m]} ملايين"
    tail = _plural_tail(m)
    if tail:
        return f"{_below_1000(m - tail)} و{_COUNTS[tail]} ملايين"
    return f"{_below_1000(m)} مليون"


def amount_in_words(n: int, lang: str | None = None) -> str:
    """50,000 -> "خمسين ألف دينار", 250,000 -> "ربع مليون دينار", 1,500,000 -> "مليون ونص دينار".

    lang 'msa' -> "خمسون ألف دينار", 'en' -> "fifty thousand dinars".
    """
    if n <= 0 or n >= 1000 * MILLION:
        raise ValueError(f"amount out of range: {n}")
    if lang == MSA:
        return _msa_words(n)
    if lang == ENGLISH:
        return _english_words(n)
    if n == 250_000:
        return "ربع مليون دينار"
    if n == 500_000:
        return "نص مليون دينار"
    millions, rest = divmod(n, MILLION)
    thousands, units = divmod(rest, 1000)
    if millions and rest == 500_000:
        return f"{_millions(millions)} ونص دينار"
    if millions and rest == 250_000:
        return f"{_millions(millions)} وربع دينار"
    parts = []
    if millions:
        parts.append(_millions(millions))
    if thousands:
        parts.append(_thousands(thousands))
    if units:
        parts.append(_below_1000(units))
    return " و".join(parts) + " دينار"


# ---- amount in MSA words (فصحى) -------------------------------------------------

_MSA_UNITS = {1: "واحد", 2: "اثنان", 3: "ثلاثة", 4: "أربعة", 5: "خمسة",
              6: "ستة", 7: "سبعة", 8: "ثمانية", 9: "تسعة", 10: "عشرة"}
_MSA_TEENS = {10: "عشرة", 11: "أحد عشر", 12: "اثنا عشر", 13: "ثلاثة عشر", 14: "أربعة عشر",
              15: "خمسة عشر", 16: "ستة عشر", 17: "سبعة عشر", 18: "ثمانية عشر", 19: "تسعة عشر"}
_MSA_TENS = {20: "عشرون", 30: "ثلاثون", 40: "أربعون", 50: "خمسون",
             60: "ستون", 70: "سبعون", 80: "ثمانون", 90: "تسعون"}
_MSA_HUNDREDS = {1: "مئة", 2: "مئتان", 3: "ثلاثمئة", 4: "أربعمئة", 5: "خمسمئة",
                 6: "ستمئة", 7: "سبعمئة", 8: "ثمانمئة", 9: "تسعمئة"}


def _msa_below_1000(n: int, construct: bool = False) -> str:
    """construct: «مئتا» right before ألف / مليون instead of «مئتان»."""
    parts = []
    hundreds, rest = divmod(n, 100)
    if hundreds:
        parts.append("مئتا" if hundreds == 2 and construct and not rest else _MSA_HUNDREDS[hundreds])
    if 0 < rest < 10:
        parts.append(_MSA_UNITS[rest])
    elif 10 <= rest < 20:
        parts.append(_MSA_TEENS[rest])
    elif rest:
        unit, ten = rest % 10, rest - rest % 10
        parts.append(f"{_MSA_UNITS[unit]} و{_MSA_TENS[ten]}" if unit else _MSA_TENS[ten])
    return " و".join(parts)


def _msa_scale(k: int, one: str, two: str, plural: str) -> str:
    """two: the dual as it stands here («ألفان», or the construct «ألفا» right before دينار)."""
    if k == 1:
        return one
    if k == 2:
        return two
    if k <= 10:
        return f"{_MSA_UNITS[k]} {plural}"
    tail = _plural_tail(k)
    if tail:  # «مئة وثلاثة آلاف», «مئة وعشرة آلاف»
        return f"{_msa_below_1000(k - tail)} و{_MSA_UNITS[tail]} {plural}"
    return f"{_msa_below_1000(k, construct=True)} {one}"


def _msa_words(n: int) -> str:
    """50,000 -> "خمسون ألف دينار", 250,000 -> "مئتان وخمسون ألف دينار"."""
    millions, rest = divmod(n, MILLION)
    thousands, units = divmod(rest, 1000)
    parts = []
    if millions:  # the dual right before دينار is the construct: «مليونا دينار», «ألفا دينار»
        parts.append(_msa_scale(millions, "مليون", "مليونا" if not rest else "مليونان", "ملايين"))
    if thousands:
        parts.append(_msa_scale(thousands, "ألف", "ألفا" if not units else "ألفان", "آلاف"))
    if units:
        parts.append(_msa_below_1000(units))
    return " و".join(parts) + " دينار"


# ---- amount in English words ------------------------------------------------------

_EN_UNITS = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
             "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_EN_TENS = {20: "twenty", 30: "thirty", 40: "forty", 50: "fifty", 60: "sixty", 70: "seventy",
            80: "eighty", 90: "ninety"}


def _en_below_1000(n: int) -> str:
    parts = []
    hundreds, rest = divmod(n, 100)
    if hundreds:
        parts.append(f"{_EN_UNITS[hundreds]} hundred")
    if 0 < rest < 20:
        parts.append(_EN_UNITS[rest])
    elif rest:
        unit, ten = rest % 10, rest - rest % 10
        parts.append(f"{_EN_TENS[ten]}-{_EN_UNITS[unit]}" if unit else _EN_TENS[ten])
    return " ".join(parts)


def _english_words(n: int) -> str:
    """50,000 -> "fifty thousand dinars", 1,500,000 -> "one million five hundred thousand dinars"."""
    millions, rest = divmod(n, MILLION)
    thousands, units = divmod(rest, 1000)
    parts = []
    if millions:
        parts.append(f"{_en_below_1000(millions)} million")
    if thousands:
        parts.append(f"{_en_below_1000(thousands)} thousand")
    if units:
        parts.append(_en_below_1000(units))
    return " ".join(parts) + (" dinar" if n == 1 else " dinars")


# ---- the card ---------------------------------------------------------------

def _gender(view) -> str:
    g = view.get("gender") if isinstance(view, Mapping) else getattr(view, "gender", None)
    return "m" if g == "m" else "f"


def _balance(view) -> int | None:
    b = view.get("balance") if isinstance(view, Mapping) else getattr(view, "balance", None)
    return b if isinstance(b, int) else None


def _last4(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")[-4:]


# Fixed words of the card, per language. {"f": ..., "m": ...}: the user's gender (English has one form).
CARD_WORDS = {
    IRAQI: {
        "dinar": "دينار",
        "bill_head": {"f": "راح تدفعين فاتورة {biller}", "m": "راح تدفع فاتورة {biller}"},
        "holder": "باسم: {holder}", "account": "، رقم الحساب {account}",
        "period": "عن شهر: {period}", "due": "المبلغ المطلوب: {money} ({words})",
        "topup_head": {"f": "راح تشحنين رصيد {money} ({words})", "m": "راح تشحن رصيد {money} ({words})"},
        "topup_target": "للرقم المنتهي بـ {last4}", "network": "، شبكة {operator}",
        "transfer_head": {"f": "راح تدزين {money} ({words})", "m": "راح تدز {money} ({words})"},
        "recipient": "المستلم: {name}، الرقم ينتهي بـ {last4}",
        "fee": "العمولة: {fee}، المجموع: {total}", "no_fee": "بدون عمولة، المجموع: {total}.",
        "balance_after": {"f": "رصيدج بعد الدفع: {money}", "m": "رصيدك بعد الدفع: {money}"},
        "valid": {"f": "البطاقة صالحة {minutes} دقايق. متأكدة؟", "m": "البطاقة صالحة {minutes} دقايق. متأكد؟"},
        "line_of": "الرقم المنتهي بـ {last4}",
    },
    MSA: {
        "dinar": "دينار",
        "bill_head": {"f": "ستدفعين فاتورة {biller}", "m": "ستدفع فاتورة {biller}"},
        "holder": "باسم: {holder}", "account": "، رقم الحساب {account}",
        "period": "عن شهر: {period}", "due": "المبلغ المطلوب: {money} ({words})",
        "topup_head": {"f": "ستشحنين رصيداً بقيمة {money} ({words})",
                       "m": "ستشحن رصيداً بقيمة {money} ({words})"},
        "topup_target": "للرقم المنتهي بـ {last4}", "network": "، شبكة {operator}",
        "transfer_head": {"f": "ستحوّلين {money} ({words})", "m": "ستحوّل {money} ({words})"},
        "recipient": "المستلم: {name}، الرقم المنتهي بـ {last4}",
        "fee": "العمولة: {fee}، المجموع: {total}", "no_fee": "بلا عمولة، المجموع: {total}.",
        "balance_after": "رصيدك بعد الدفع: {money}",
        "valid": {"f": "البطاقة صالحة لمدة {minutes} دقائق. هل أنتِ متأكدة؟",
                  "m": "البطاقة صالحة لمدة {minutes} دقائق. هل أنت متأكد؟"},
        "line_of": "الرقم المنتهي بـ {last4}",
    },
    ENGLISH: {
        "dinar": "dinars",
        "bill_head": "You're paying the {biller} bill",
        "holder": "Account holder: {holder}", "account": ", account no. {account}",
        "period": "For: {period}", "due": "Amount due: {money} ({words})",
        "topup_head": "You're topping up {money} ({words}) of credit",
        "topup_target": "For the number ending in {last4}", "network": ", {operator} network",
        "transfer_head": "You're sending {money} ({words})",
        "recipient": "To: {name}, number ending in {last4}",
        "fee": "Fee: {fee}, total: {total}", "no_fee": "No fee, total: {total}.",
        "balance_after": "Your balance after paying: {money}",
        "valid": "This card is valid for {minutes} minutes. Are you sure?",
        "line_of": "the number ending in {last4}",
    },
}

# What a voice reply says for a card: who, how much (in words), the fee, and which button to tap.
SPOKEN = {
    IRAQI: {
        "transfer": {"f": "راح تدزين {words} لـ {name}، {fee}.", "m": "راح تدز {words} لـ {name}، {fee}."},
        "bill": {"f": "راح تدفعين فاتورة {biller} لشهر {period}، المبلغ {words}، {fee}.",
                 "m": "راح تدفع فاتورة {biller} لشهر {period}، المبلغ {words}، {fee}."},
        "topup": {"f": "راح تشحنين رصيد {words} للرقم المنتهي بـ {last4}، {fee}.",
                  "m": "راح تشحن رصيد {words} للرقم المنتهي بـ {last4}، {fee}."},
        "fee": "والعمولة {words}", "no_fee": "بدون عمولة",
        "flags": {"f": "انتبهي للتنبيه اللي بالبطاقة.", "m": "انتبه للتنبيه اللي بالبطاقة."},
        "tap": {"f": "إذا كلشي صح، دوسي إي ادفع حتى تأكدين.", "m": "إذا كلشي صح، دوس إي ادفع حتى تأكد."},
    },
    MSA: {
        "transfer": {"f": "ستحوّلين مبلغاً قدره {words} إلى {name}، {fee}.",
                     "m": "ستحوّل مبلغاً قدره {words} إلى {name}، {fee}."},
        "bill": {"f": "ستدفعين فاتورة {biller} لشهر {period}، والمبلغ {words}، {fee}.",
                 "m": "ستدفع فاتورة {biller} لشهر {period}، والمبلغ {words}، {fee}."},
        "topup": {"f": "ستشحنين رصيداً قدره {words} للرقم المنتهي بـ {last4}، {fee}.",
                  "m": "ستشحن رصيداً قدره {words} للرقم المنتهي بـ {last4}، {fee}."},
        "fee": "والعمولة {words}", "no_fee": "بلا عمولة",
        "flags": {"f": "انتبهي إلى التنبيه في البطاقة.", "m": "انتبه إلى التنبيه في البطاقة."},
        "tap": {"f": "إذا كان كل شيء صحيحاً، فاضغطي نعم، ادفع للتأكيد.",
                "m": "إذا كان كل شيء صحيحاً، فاضغط نعم، ادفع للتأكيد."},
    },
    ENGLISH: {
        "transfer": "You're sending {words} to {name}, {fee}.",
        "bill": "You're paying the {biller} bill for {period}: {words}, {fee}.",
        "topup": "You're topping up {words} of credit for the number ending in {last4}, {fee}.",
        "fee": "and the fee is {words}", "no_fee": "with no fee",
        "flags": "Please read the warning on the card.",
        "tap": "If everything is right, tap Yes, pay to confirm.",
    },
}


def _pick(entry, female: bool) -> str:
    return entry if isinstance(entry, str) else entry["f" if female else "m"]


def _money(n: int, lang: str | None = None) -> str:
    return f"{format_iqd(n)} {CARD_WORDS.get(lang or IRAQI, CARD_WORDS[IRAQI])['dinar']}"


def _payee_label(q: QuoteDetails, lang: str | None = None) -> str:
    if q.kind == "bill":
        return q.biller_name
    if q.kind == "topup":
        return CARD_WORDS.get(lang or IRAQI, CARD_WORDS[IRAQI])["line_of"].format(last4=_last4(q.phone))
    return q.payee_name


def _flag_key(q: QuoteDetails, code: str) -> str:
    """A top-up card warns about a phone line, so it has its own wording when one exists."""
    topup = f"{code}_topup"
    return topup if q.kind == "topup" and f"flag_{topup}" in catalog() else code


def _flags(q: QuoteDetails, view, flags) -> list[str]:
    if flags is not None:
        return list(flags)
    if not isinstance(view, rules.WalletView):
        return []
    request = rules.PaymentRequest(q.kind, q.amount, q.payee_id, "IQD", q.fee)
    return rules.check(request, view).soft_flags


def spoken_summary(q: QuoteDetails, gender: str = "f", lang: str | None = None, has_flags: bool = False,
                   flag: str | None = None) -> str:
    """The card said out loud: who, how much in words, the fee, the first warning, and which button
    confirms. Never a PIN. `flag` is the card's first warning line, said as it is (the voice layer reads
    its numbers as words); without it a card with warnings says «read the warning on the card»."""
    lang = lang if lang in SPOKEN else IRAQI
    talk, female = SPOKEN[lang], gender != "m"
    fee = talk["fee"].format(words=amount_in_words(q.fee, lang)) if q.fee else talk["no_fee"]
    kind = q.kind if q.kind in ("transfer", "bill", "topup") else "transfer"
    line = _pick(talk[kind], female).format(
        words=amount_in_words(q.amount, lang), name=q.payee_name, biller=q.biller_name,
        period=format_period(q.period, lang), last4=_last4(q.phone), fee=fee)
    parts = [line]
    if flag:
        parts.append(flag.strip())
    elif has_flags:
        parts.append(_pick(talk["flags"], female))
    parts.append(_pick(talk["tap"], female))
    return " ".join(parts)


def render_card(quote, view, flags: list[str] | None = None, lang: str | None = None) -> Card:
    """Build the confirmation card.

    quote: QuoteDetails (or a dict with its fields).
    view: rules.WalletView (or anything with .gender / .balance).
    flags: soft-flag codes; when None they are computed with rules.check from a WalletView.
    lang: 'iq' (default) | 'msa' | 'en'.
    """
    q = quote if isinstance(quote, QuoteDetails) else QuoteDetails.from_dict(quote)
    lang = lang if lang in CARD_WORDS else IRAQI
    w = CARD_WORDS[lang]
    g = _gender(view)
    f = g == "f"
    words = amount_in_words(q.amount, lang)
    money = _money(q.amount, lang)
    lines = []

    if q.kind == "bill":
        lines.append(_pick(w["bill_head"], f).format(biller=q.biller_name))
        holder = w["holder"].format(holder=q.payee_name)
        if q.account_no:
            holder += w["account"].format(account=q.account_no)
        lines.append(holder)
        lines.append(w["period"].format(period=format_period(q.period, lang)))
        lines.append(w["due"].format(money=money, words=words))
    elif q.kind == "topup":
        lines.append(_pick(w["topup_head"], f).format(money=money, words=words))
        target = w["topup_target"].format(last4=_last4(q.phone))
        if q.operator:
            target += w["network"].format(operator=q.operator)
        lines.append(target)
    else:
        lines.append(_pick(w["transfer_head"], f).format(money=money, words=words))
        lines.append(w["recipient"].format(name=q.payee_name, last4=_last4(q.phone)))

    total = _money(q.amount + q.fee, lang)
    if q.fee:
        lines.append(w["fee"].format(fee=_money(q.fee, lang), total=total))
    else:
        lines.append(w["no_fee"].format(total=total))
    balance = _balance(view)
    if balance is not None:
        lines.append(_pick(w["balance_after"], f).format(money=_money(balance - q.amount - q.fee, lang)))
    lines.append(_pick(w["valid"], f).format(minutes=q.expires_in_min))

    codes = _flags(q, view, flags)
    flag_lines = [flag_line(_flag_key(q, code), g, lang, name=_payee_label(q, lang)) for code in codes]
    return Card(q.quote_id, lines, flag_lines, list(buttons_for(lang)), codes, title=card_title(q.kind, lang),
                lang=lang, spoken=spoken_summary(q, g, lang, has_flags=bool(codes),
                                                 flag=flag_lines[0] if flag_lines else None))
