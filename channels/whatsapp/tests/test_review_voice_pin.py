"""Review: a PIN said in a voice note (or typed in English) must never reach the core, never become an
amount, and never be said back in a voice reply. A card shown again after a language switch is in the new
language, so one voice note is one language."""

from __future__ import annotations

import pytest

from channels.whatsapp.tests import kit
from channels.whatsapp.tests.kit import ALICE, PIN
from channels.whatsapp.tests.test_language_and_voice import Speaking
from channels.whatsapp.tests.test_whatsapp_channel import Harness
from payments.quote import amount_in_words


class Spy(Speaking):
    def __init__(self, heard: str = ""):
        super().__init__(heard=heard)
        self.seen: list[str] = []

    def understand(self, text, context):
        self.seen.append(text)
        return super().understand(text, context)


def _tapped(tmp_path, core, request="دز لهادي خمسين الف", yes="إي، ادفع"):
    h = Harness(tmp_path, core=core)
    h.say(ALICE, "هلا")
    h.say(ALICE, request)
    h.tap(ALICE, yes)
    core.seen.clear()
    core.said.clear()
    h.graph.clear()
    return h


@pytest.mark.parametrize("heard", ["الرمز 1234", "رمزي ١٢٣٤", "1234.", "PIN 1234"])
def test_pin_said_in_a_voice_note_is_never_read_as_an_amount_or_said_back(tmp_path, heard):
    core = Spy(heard)
    h = _tapped(tmp_path, core)
    try:
        h.graph.media_files["m1"] = (b"OggS-in", "audio/ogg")
        h.send(kit.voice(ALICE, "m1", h.clock))
        assert core.seen == [], "the PIN went to the core's understanding"
        spoken = " ".join(text for text, _ in core.said)
        for lang in ("iq", "en", "msa"):
            assert amount_in_words(int(PIN), lang) not in spoken, f"the voice reply says the PIN: {spoken}"
        assert "1,234" not in h.graph.everything(ALICE), "a new card was made for the PIN as an amount"
    finally:
        h.stop()


def test_english_worded_pin_after_the_tap_never_reaches_the_core(tmp_path):
    core = Spy()
    h = _tapped(tmp_path, core, request="send 50000 to Hadi", yes="Yes, pay")
    try:
        h.say(ALICE, "my pin is 5678")  # a wrong PIN, worded in English («الرمز 5678» is caught)
        assert core.seen == [], "a worded English PIN went to the core's understanding"
    finally:
        h.stop()


def test_voice_reply_is_one_language_when_the_sender_switches_with_a_card_open(tmp_path):
    core = Spy("إي ادفع")
    h = Harness(tmp_path, core=core)
    try:
        h.say(ALICE, "hello")
        h.say(ALICE, "send 50000 to Hadi")  # an English card
        core.said.clear()
        h.graph.media_files["m1"] = (b"OggS-in", "audio/ogg")
        h.send(kit.voice(ALICE, "m1", h.clock))  # an Iraqi voice note while it is open
        [(spoken, lang)] = core.said
        assert lang == "iq"
        assert "You're sending" not in spoken, f"Iraqi voice style asked to read English: {spoken}"
    finally:
        h.stop()
