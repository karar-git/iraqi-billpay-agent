"""Accounts review fixes, end to end over the fake Graph: how money gets in, natural phrasings of the account
commands, the reply language of a first English command, and a number said digit by digit."""

from __future__ import annotations

import pytest

from accounts import phones
from channels.whatsapp.tests.kit import ALICE
from channels.whatsapp.tests.test_whatsapp_channel import Harness


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path)
    yield harness
    harness.stop()


def test_asking_how_to_fund_the_wallet_explains_it_and_can_ask_the_admin(h):
    h.say(ALICE, "شلون احط فلوس بحسابي")
    body = h.graph.last_card(ALICE)["body"]["text"]
    assert "عن طريق الإدارة" in body and "شكد" not in body  # never the phone top-up question
    h.tap(ALICE, "إي، اطلب")
    assert "انطلب ✅" in h.graph.texts(ALICE)[-1]
    kinds = [r["kind"] for r in h.channel.accounts.open_requests() if r["user_id"] == h.uid(ALICE)]
    assert "funding_requested" in kinds
    h.channel.wallet.admin_credit(h.uid(ALICE), 1_000, "funding", op_id="op-fund-1", actor="admin:test")
    kinds = [r["kind"] for r in h.channel.accounts.open_requests() if r["user_id"] == h.uid(ALICE)]
    assert "funding_requested" not in kinds  # answered by the credit


def test_the_balance_refusal_points_to_the_funding_command(h):
    h.say(ALICE, "دز مليون لـ 07700000002")
    said = h.graph.everything(ALICE)
    assert "«اشحن محفظتي»" in said and "اشحني محفظتج" not in said


@pytest.mark.parametrize("text", ["شنو فواتيري", "show me my bills", "منو الاسماء اللي عندي", "show my contacts",
                                  "أريد رؤية جهات الاتصال من فضلك", "وين فلوسي المحجوزة"])
def test_natural_phrasings_of_the_account_lists_never_open_a_card(h, text):
    cards = len(h.graph.cards(ALICE))
    h.say(ALICE, text)
    assert len(h.graph.cards(ALICE)) == cards, h.graph.texts(ALICE)[-1]
    reply = h.graph.texts(ALICE)[-1]
    assert "شتحتاج" not in reply and "What do you need" not in reply and "بماذا أساعد" not in reply, reply


def test_a_first_english_account_command_is_answered_in_english(h):
    h.say(ALICE, "add Mustafa 07701234567")
    assert h.graph.texts(ALICE)[-1] == "Saved Mustafa (0770 123 4567)."


def test_a_number_said_digit_by_digit_is_read_as_a_number():
    said = "دز خمسة وعشرين ألف لصفر سبعة ثمانية واحد خمسة خمسة خمسة ستة ستة ستة ستة"
    written = phones.spoken_digits(said)
    assert [s.e164 for s in phones.find(written)] == ["9647815556666"]
    assert written.startswith("دز خمسة وعشرين ألف")  # the amount is left in words
    assert phones.spoken_digits("خمسة خمسة") == "خمسة خمسة"
