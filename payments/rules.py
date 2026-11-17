"""Deterministic payment rules: hard stops block a payment, soft flags warn on the card."""

from __future__ import annotations

from dataclasses import dataclass, field

FEES = {"transfer": 250, "bill": 0, "topup": 0}  # synthetic, mirrors the wallet API
DUPLICATE_WINDOW_MIN = 10
JUMP_FACTOR = 3

# Hard stops: the payment must not go ahead.
NON_IQD = "non_iqd"
NON_POSITIVE_AMOUNT = "non_positive_amount"
INSUFFICIENT_BALANCE = "insufficient_balance"
OVER_DAILY_LIMIT = "over_daily_limit"
PIN_RESET_COOLDOWN = "pin_reset_cooldown"  # the 24 h after an admin PIN reset: transfers and top-ups capped
# The most one payment may be for this account's verification level (kyc.policy): more is refused, whatever the
# balance. Only accounts that have a level have this cap; the number comes from code, never from a model.
OVER_SINGLE_MAX = "over_single_max"

# Soft flags: shown as warning lines on the confirmation card.
FIRST_TIME_PAYEE = "first_time_payee"
DUPLICATE_RECENT = "duplicate_recent"
AMOUNT_JUMP = "amount_jump"
UNREGISTERED_RECIPIENT = "unregistered_recipient"  # a number with no account yet: the money is held for it

# Past payments in these states never reached the payee.
NOT_SENT = {"FAILED", "CANCELLED", "EXPIRED", "QUOTED"}


@dataclass
class PaymentRequest:
    kind: str  # 'transfer' | 'bill' | 'topup'
    amount: int | None
    payee_id: str  # contact_id, bill account id or top-up phone (same key as PastPayment)
    currency: str | None = "IQD"
    fee: int | None = None  # None -> FEES[kind]

    @property
    def fee_iqd(self) -> int:
        return self.fee if self.fee is not None else FEES.get(self.kind, 0)


@dataclass
class PastPayment:
    payee_id: str
    amount: int
    minutes_ago: float
    status: str = "SUCCEEDED"  # quote/transaction state, any case


@dataclass
class WalletView:
    balance: int
    daily_limit: int
    spent_today: int = 0  # amounts already sent today (pending/UNKNOWN included)
    gender: str = "f"  # 'f' | 'm', used by the card and messages
    history: list[PastPayment] = field(default_factory=list)
    cooldown_left: int | None = None  # after a PIN reset: how much more may go out (None: no cooldown)
    single_max: int | None = None  # the most one payment may be at this verification level (None: no such cap)

    @property
    def remaining_today(self) -> int:
        return max(self.daily_limit - self.spent_today, 0)


@dataclass
class Checks:
    hard_stops: list[str]
    soft_flags: list[str]

    @property
    def ok(self) -> bool:
        return not self.hard_stops


def check(request: PaymentRequest, wallet_view: WalletView) -> Checks:
    """Run every rule. Hard stops come in a fixed order; soft flags only for a valid amount."""
    hard, soft = [], []
    amount = request.amount
    if (request.currency or "").upper() != "IQD":
        hard.append(NON_IQD)
    if not isinstance(amount, int) or amount <= 0:
        hard.append(NON_POSITIVE_AMOUNT)
        return Checks(hard, soft)

    if wallet_view.single_max is not None and amount > wallet_view.single_max:
        hard.append(OVER_SINGLE_MAX)  # first: no balance and no other day makes this amount possible
    if amount + request.fee_iqd > wallet_view.balance:
        hard.append(INSUFFICIENT_BALANCE)
    if wallet_view.spent_today + amount > wallet_view.daily_limit:
        hard.append(OVER_DAILY_LIMIT)
    if request.kind != "bill" and wallet_view.cooldown_left is not None and amount > wallet_view.cooldown_left:
        hard.append(PIN_RESET_COOLDOWN)

    same_payee = [p for p in wallet_view.history if p.payee_id == request.payee_id]
    sent = [p for p in same_payee if p.status.upper() not in NOT_SENT]
    done = [p for p in same_payee if p.status.upper() == "SUCCEEDED"]
    # Bill accounts are saved by the user and the biller sets the amount,
    # so "new payee" and "unusual amount" only make sense for transfers and top-ups.
    if request.kind != "bill" and not done:
        soft.append(FIRST_TIME_PAYEE)
    if any(p.amount == amount and p.minutes_ago <= DUPLICATE_WINDOW_MIN for p in sent):
        soft.append(DUPLICATE_RECENT)
    if request.kind != "bill" and done and amount > JUMP_FACTOR * max(p.amount for p in done):
        soft.append(AMOUNT_JUMP)
    return Checks(hard, soft)
