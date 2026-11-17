"""Conversation review: bugs found by simulating real chats offline (fake Graph, the open StubCore).

Each test showed a bug; they are fixed now and these tests keep them fixed."""

from __future__ import annotations

import re

import pytest

from channels.whatsapp.tests import kit
from channels.whatsapp.tests.kit import ALICE, PIN
from channels.whatsapp.tests.test_whatsapp_channel import Harness


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path)
    yield harness
    harness.stop()


def _pay_first_button(h: Harness, who: str, pin: str) -> None:
    h.tap(who, h.graph.last_card(who)["action"]["buttons"][0]["reply"]["title"])
    h.say(who, pin)


def test_contact_saved_in_english_can_be_paid_by_name_in_english(h):
    h.say(ALICE, "hello")  # English from here on
    h.say(ALICE, "add Mustafa 07701234567")
    assert h.graph.texts(ALICE)[-1] == "Saved Mustafa (0770 123 4567)."
    h.say(ALICE, "send 5000 to Mustafa")
    cards = h.graph.cards(ALICE)
    assert cards and "4567" in cards[-1]["body"]["text"], h.graph.texts(ALICE)[-1]


def test_no_first_time_warning_right_after_paying_the_same_number(h):
    h.say(ALICE, "دز عشرة الاف لـ 07700000002")
    _pay_first_button(h, ALICE, PIN)
    assert "حفظت الرقم بأسمائج" in h.graph.texts(ALICE)[-1]
    h.say(ALICE, "حول خمسة الاف لعلي")
    body = h.graph.last_card(ALICE)["body"]["text"]
    assert "0002" in body
    assert "أول مرة" not in body


def test_a_command_typed_while_the_save_bill_card_is_open_still_runs(h):
    h.say(ALICE, "ضيف حساب الكهرباء 2024939794")
    assert list(kit.card_buttons(h.graph.last_card(ALICE))) == ["احفظ", "لا"]
    h.say(ALICE, "فواتيري")
    assert h.graph.texts(ALICE)[-1].startswith("حساباتك:") or h.graph.texts(ALICE)[-1].startswith("حساباتج:")


def test_rename_hint_from_the_receipt_works(h):
    # the seed already has «أحمد» on 07502220002; BOB's number 07700000002 also ends in 0002
    h.say(ALICE, "دز عشرة الاف لـ 07700000002")
    _pay_first_button(h, ALICE, PIN)
    hint = re.search(r"«سمّي (\d+) اسم جديد»", h.graph.texts(ALICE)[-1])
    assert hint and hint.group(1).endswith("0002") and len(hint.group(1)) > 4  # unique among her contacts
    h.say(ALICE, f"سمّي {hint.group(1)} علي")
    names = [c["display_name"] for c in h.channel.accounts.contacts(h.uid(ALICE))]
    assert "علي" in names, h.graph.texts(ALICE)[-1]
