"""Money review: checks that only hold at quote time and are not repeated when the quote is executed.

Each test shows a gap between what a quote promised and what execute does five minutes later."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import wallet.api as wallet_api
from accounts.service import Accounts
from tests.test_accounts import A, B, C, env, transfer  # noqa: F401  (env is a fixture)
from wallet.db import hash_account_pin

D = "9647700000004"  # no account


def test_the_hold_cap_per_number_holds_when_two_senders_quote_at_the_same_time(env, monkeypatch):
    """HOLD_CAP_IQD is 'held for one number at a time'. It is checked at quote time only, so two senders whose
    cards are open together both pass and both execute."""
    clock, db, wallet, accounts, a, b = env
    monkeypatch.setattr(wallet_api, "HOLD_CAP_IQD", 100_000)
    wallet.admin_credit(b, 200_000, "funds for the test", op_id="op-b", actor="admin:test")
    qa = wallet.create_quote(a, "transfer", {"phone": D}, 80_000)  # held for D: 0, passes
    qb = wallet.create_quote(b, "transfer", {"phone": D}, 80_000)  # held for D: 0, passes
    wallet.execute(qa["id"], "2583")
    wallet.execute(qb["id"], "8642")
    held = sum(h["amount"] for h in wallet.holds(state="HELD") if h["phone_e164"] == D)
    assert held <= 100_000, f"{held} IQD held for one number, cap is 100000"


def test_a_recipient_closed_after_the_card_was_made_is_not_credited(env):
    """set_status('closed') needs a zero balance, but a card made before the close still credits the closed
    account at execute: the money lands in an account nobody can use."""
    clock, db, wallet, accounts, a, b = env
    quote = wallet.create_quote(a, "transfer", {"phone": B}, 10_000)
    wallet.set_status(b, "closed", actor="admin:test", reason="closed on request")
    try:
        wallet.execute(quote["id"], "2583")
    except HTTPException:
        pass
    closed = wallet.user_profile(b)
    assert closed["status"] == "closed"
    assert closed["balance"] == 0, f"a closed account received {closed['balance']} IQD"


def test_a_released_number_can_be_sent_money_again(env):
    """release_number says 'the number can sign up again', but the old phone column still matches in
    Wallet._account_on's fallback, so a transfer to the released number is refused as recipient_closed
    instead of being held for whoever signs up on it next."""
    clock, db, wallet, accounts, a, b = env
    wallet.set_status(b, "closed", actor="admin:test", reason="closed on request")
    assert accounts.release_number(b, "admin:test", "number given back")
    quote = wallet.create_quote(a, "transfer", {"phone": B}, 1_000)
    assert quote["target"].get("hold") is True


def test_a_bill_whose_amount_changed_after_the_card_is_not_marked_paid_at_the_old_amount(env):
    """The admin can change an unpaid bill (Accounts.set_bill). _run only re-checks the bill's status, so a
    card made before the change pays the old amount and marks the bill paid at its new amount."""
    clock, db, wallet, accounts, a, b = env
    status, account, bill = accounts.add_bill_account(a, "generator", "350", "المولدة")
    assert status == "added"
    old = bill["amount_due"]
    quote = wallet.create_quote(a, "bill", {"bill_id": bill["id"]}, None)
    assert accounts.set_bill(bill["id"], old + 30_000, "admin:test")
    result = wallet.execute(quote["id"], "2583")
    after = next(x for x in accounts.bills(account["id"]) if x["id"] == bill["id"])
    if result["state"] == "SUCCEEDED":
        assert result["amount"] == after["amount_due"], (
            f"paid {result['amount']} IQD, bill marked {after['status']} at {after['amount_due']} IQD")
