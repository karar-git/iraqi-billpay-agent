from payments import rules
from payments.rules import Checks, PastPayment, PaymentRequest, WalletView, check


def view(balance=500_000, limit=1_000_000, spent=0, history=()):
    return WalletView(balance=balance, daily_limit=limit, spent_today=spent, history=list(history))


def transfer(amount, payee="c1", currency="IQD"):
    return PaymentRequest("transfer", amount, payee, currency)


PAID_BEFORE = [PastPayment("c1", 40_000, minutes_ago=60 * 24 * 7)]


def test_clean_transfer_passes():
    result = check(transfer(50_000), view(history=PAID_BEFORE))
    assert result == Checks([], [])
    assert result.ok


def test_insufficient_balance_counts_fee():
    # 50,000 + 250 fee > 50,000
    result = check(transfer(50_000), view(balance=50_000, history=PAID_BEFORE))
    assert result.hard_stops == [rules.INSUFFICIENT_BALANCE]
    assert not result.ok
    # exactly enough with the fee
    assert check(transfer(49_750), view(balance=50_000, history=PAID_BEFORE)).ok


def test_bill_has_no_fee():
    bill = PaymentRequest("bill", 50_000, "acct-1")
    assert check(bill, view(balance=50_000)).ok


def test_over_daily_limit():
    result = check(transfer(300_000), view(limit=1_000_000, spent=800_000, history=PAID_BEFORE))
    assert result.hard_stops == [rules.OVER_DAILY_LIMIT]


def test_non_positive_amount():
    for amount in (0, -5_000, None):
        result = check(transfer(amount), view())
        assert result.hard_stops == [rules.NON_POSITIVE_AMOUNT]
        assert result.soft_flags == []


def test_non_iqd():
    result = check(transfer(100, currency="USD"), view(history=PAID_BEFORE))
    assert rules.NON_IQD in result.hard_stops
    assert check(transfer(None, currency=None), view()).hard_stops == [
        rules.NON_IQD, rules.NON_POSITIVE_AMOUNT]


def test_several_hard_stops_in_fixed_order():
    result = check(transfer(600_000), view(balance=100_000, limit=500_000))
    assert result.hard_stops == [rules.INSUFFICIENT_BALANCE, rules.OVER_DAILY_LIMIT]


def test_first_time_payee():
    assert check(transfer(50_000, payee="new"), view(history=PAID_BEFORE)).soft_flags == [
        rules.FIRST_TIME_PAYEE]
    # a failed attempt does not make the payee known
    failed = [PastPayment("c9", 50_000, 600, "FAILED")]
    assert rules.FIRST_TIME_PAYEE in check(transfer(50_000, payee="c9"), view(history=failed)).soft_flags


def test_first_time_not_flagged_for_saved_bill_accounts():
    bill = PaymentRequest("bill", 30_000, "acct-1")
    assert check(bill, view()).soft_flags == []


def test_duplicate_within_ten_minutes_includes_unknown_and_pending():
    for status in ("SUCCEEDED", "UNKNOWN", "EXECUTING", "pending"):
        history = PAID_BEFORE + [PastPayment("c1", 50_000, minutes_ago=4, status=status)]
        assert rules.DUPLICATE_RECENT in check(transfer(50_000), view(history=history)).soft_flags, status


def test_duplicate_ignores_failed_old_or_different():
    cases = [
        PastPayment("c1", 50_000, minutes_ago=4, status="FAILED"),
        PastPayment("c1", 50_000, minutes_ago=4, status="CANCELLED"),
        PastPayment("c1", 50_000, minutes_ago=11),
        PastPayment("c1", 60_000, minutes_ago=2),
        PastPayment("c2", 50_000, minutes_ago=2),
    ]
    for past in cases:
        flags = check(transfer(50_000), view(history=PAID_BEFORE + [past])).soft_flags
        assert rules.DUPLICATE_RECENT not in flags, past


def test_duplicate_also_for_bills():
    bill = PaymentRequest("bill", 30_000, "acct-1")
    history = [PastPayment("acct-1", 30_000, minutes_ago=1, status="UNKNOWN")]
    assert check(bill, view(history=history)).soft_flags == [rules.DUPLICATE_RECENT]


def test_amount_jump():
    # largest past amount to c1 is 40,000 -> more than 120,000 is a jump
    assert check(transfer(120_000), view(history=PAID_BEFORE)).soft_flags == []
    assert check(transfer(120_001), view(history=PAID_BEFORE)).soft_flags == [rules.AMOUNT_JUMP]


def test_explicit_fee_overrides_default():
    request = PaymentRequest("transfer", 10_000, "c1", fee=0)
    assert check(request, view(balance=10_000, history=PAID_BEFORE)).ok


def test_remaining_today():
    assert view(limit=500_000, spent=450_000).remaining_today == 50_000
    assert view(limit=500_000, spent=600_000).remaining_today == 0
