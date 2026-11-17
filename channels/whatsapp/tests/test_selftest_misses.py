"""Typed messages the live self-test got wrong: «50k» and a contact saved with its name in the same message."""

from __future__ import annotations

from channels.whatsapp.tests import kit
from channels.whatsapp.tests.kit import ALICE
from channels.whatsapp.tests.test_whatsapp_channel import Harness
from core.interface import Intent
from core.stub import StubCore
from payments import amounts, arabizi


def test_50k_is_fifty_thousand_dinars_not_the_letters_of_its_digits():
    for typed, value in (("50k", 50_000), ("25K", 25_000), ("5k.", 5_000)):
        assert amounts.parse(arabizi.to_arabic(typed)).value_iqd == value


class Reads(StubCore):
    """The core's reading of the self-test message, whatever the open stub would make of it."""

    def __init__(self, intent: Intent):
        super().__init__()
        self.intent = intent

    def understand(self, text, context=None):
        return self.intent


def test_send_50k_to_hadi_gets_the_card(tmp_path):
    h = Harness(tmp_path, core=Reads(Intent("transfer", payee_mention="Hadi", amount_text="50k", language="en")))
    try:
        h.say(ALICE, "send 50k to Hadi please")
        assert "50,000" in h.graph.last_card(ALICE)["body"]["text"], h.graph.texts(ALICE)[-1]
    finally:
        h.stop()


def test_a_number_saved_with_its_name_is_not_asked_for_the_name_again(tmp_path):
    h = Harness(tmp_path, core=Reads(Intent("add_contact", payee_mention="هاتف_ا", language="msa")))
    try:
        h.say(ALICE, "أريد حفظ الرقم 07801234567 باسم كرار")
        last = h.graph.texts(ALICE)[-1]
        assert "كرار" in last and "؟" not in last, last
    finally:
        h.stop()
