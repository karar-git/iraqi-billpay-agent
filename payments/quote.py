"""Confirmation card: a deterministic Iraqi-Arabic template built from a quote.

The card is the only place the user approves money. It shows the amount in digits
and in Iraqi words, who gets it, the fee, and any soft-flag warnings.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields

from payments import rules
from payments.messages import MESSAGES, flag_line, format_iqd, format_period

MILLION = 1_000_000


@dataclass(frozen=True)
class Button:
    id: str  # 'confirm' | 'change' | 'cancel' -> Session.handle_button(..., button=id)
    label: str


BUTTONS = (
    Button("confirm", "إي، ادفع"),
    Button("change", "غيّر"),
    Button("cancel", "لا، بطّلت"),
)


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
    flags: list[str]  # warning lines, Iraqi Arabic
    buttons: list[Button]
    flag_codes: list[str] = field(default_factory=list)

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


def _thousands(k: int) -> str:
    if k == 1:
        return "ألف"
    if k == 2:
        return "ألفين"
    if k <= 10:
        return f"{_COUNTS[k]} تالاف"
    return f"{_below_1000(k)} ألف"


def _millions(m: int) -> str:
    if m == 1:
        return "مليون"
    if m == 2:
        return "مليونين"
    if m <= 10:
        return f"{_COUNTS[m]} ملايين"
    return f"{_below_1000(m)} مليون"


def amount_in_words(n: int) -> str:
    """50,000 -> "خمسين ألف دينار", 250,000 -> "ربع مليون دينار", 1,500,000 -> "مليون ونص دينار"."""
    if n <= 0 or n >= 1000 * MILLION:
        raise ValueError(f"amount out of range: {n}")
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


# ---- the card ---------------------------------------------------------------

def _gender(view) -> str:
    g = view.get("gender") if isinstance(view, Mapping) else getattr(view, "gender", None)
    return "m" if g == "m" else "f"


def _balance(view) -> int | None:
    b = view.get("balance") if isinstance(view, Mapping) else getattr(view, "balance", None)
    return b if isinstance(b, int) else None


def _last4(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")[-4:]


def _money(n: int) -> str:
    return f"{format_iqd(n)} دينار"


def _payee_label(q: QuoteDetails) -> str:
    if q.kind == "bill":
        return q.biller_name
    if q.kind == "topup":
        return f"الرقم المنتهي بـ {_last4(q.phone)}"
    return q.payee_name


def _flag_key(q: QuoteDetails, code: str) -> str:
    """A top-up card warns about a phone line, so it has its own wording when one exists."""
    topup = f"{code}_topup"
    return topup if q.kind == "topup" and f"flag_{topup}" in MESSAGES else code


def _flags(q: QuoteDetails, view, flags) -> list[str]:
    if flags is not None:
        return list(flags)
    if not isinstance(view, rules.WalletView):
        return []
    request = rules.PaymentRequest(q.kind, q.amount, q.payee_id, "IQD", q.fee)
    return rules.check(request, view).soft_flags


def render_card(quote, view, flags: list[str] | None = None) -> Card:
    """Build the confirmation card.

    quote: QuoteDetails (or a dict with its fields).
    view: rules.WalletView (or anything with .gender / .balance).
    flags: soft-flag codes; when None they are computed with rules.check from a WalletView.
    """
    q = quote if isinstance(quote, QuoteDetails) else QuoteDetails.from_dict(quote)
    g = _gender(view)
    f = g == "f"
    words = amount_in_words(q.amount)
    lines = []

    if q.kind == "bill":
        lines.append(f"راح {'تدفعين' if f else 'تدفع'} فاتورة {q.biller_name}")
        holder = f"باسم: {q.payee_name}"
        if q.account_no:
            holder += f"، رقم الحساب {q.account_no}"
        lines.append(holder)
        lines.append(f"عن شهر: {format_period(q.period)}")
        lines.append(f"المبلغ المطلوب: {_money(q.amount)} ({words})")
    elif q.kind == "topup":
        lines.append(f"راح {'تشحنين' if f else 'تشحن'} رصيد {_money(q.amount)} ({words})")
        target = f"للرقم المنتهي بـ {_last4(q.phone)}"
        if q.operator:
            target += f"، شبكة {q.operator}"
        lines.append(target)
    else:
        lines.append(f"راح {'تدزين' if f else 'تدز'} {_money(q.amount)} ({words})")
        lines.append(f"المستلم: {q.payee_name}، الرقم ينتهي بـ {_last4(q.phone)}")

    if q.fee:
        lines.append(f"العمولة: {_money(q.fee)}، المجموع: {_money(q.amount + q.fee)}")
    else:
        lines.append("بدون عمولة.")
    balance = _balance(view)
    if balance is not None:
        lines.append(f"{'رصيدج' if f else 'رصيدك'} بعد الدفع: {_money(balance - q.amount - q.fee)}")
    lines.append(f"البطاقة صالحة {q.expires_in_min} دقايق. {'متأكدة؟' if f else 'متأكد؟'}")

    codes = _flags(q, view, flags)
    flag_lines = [flag_line(_flag_key(q, code), g, name=_payee_label(q)) for code in codes]
    return Card(q.quote_id, lines, flag_lines, list(BUTTONS), codes)
