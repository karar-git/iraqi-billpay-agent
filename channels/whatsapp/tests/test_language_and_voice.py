"""WhatsApp: replies in the sender's language, the recipient card with three buttons, voice-note replies.

Offline: a fake Graph API, the open StubCore (with a pretend voice where a test needs one).
"""

from __future__ import annotations

import pytest

from channels.whatsapp import texts
from channels.whatsapp.graph import BUTTON_TITLE_MAX
from channels.whatsapp.tests import kit
from channels.whatsapp.tests.kit import ALICE, BOB, PIN
from channels.whatsapp.tests.test_whatsapp_channel import Harness
from core.stub import StubCore
from payments.quote import amount_in_words

OGG = b"OggS-voice-reply"


class Speaking(StubCore):
    """The stand-in core, with ears and a voice. Each spoken note costs a pretend $0.01."""

    def __init__(self, heard: str = "دز لهادي خمسين الف", fail: bool = False):
        super().__init__()
        self.heard, self.fail = heard, fail
        self.said: list[tuple[str, str]] = []
        self.spent = 0.0

    def transcribe(self, audio, mime, context):
        return self.heard

    def speak(self, text, language, context):
        if self.fail:
            raise RuntimeError("voice down")
        self.said.append((text, language))
        self.spent += 0.01
        return OGG, "audio/ogg"

    @property
    def spent_usd(self):
        return self.spent


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path)
    yield harness
    harness.stop()


def _voice(h, media="m1"):
    h.graph.media_files[media] = (b"OggS-in", "audio/ogg")
    h.send(kit.voice(ALICE, media, h.clock))


# ---------------------------------------------------------------- language


def test_english_sender_gets_english_greeting_card_buttons_pin_step_and_receipt(h):
    h.say(ALICE, "hello")
    [greeting] = h.graph.texts(ALICE)
    assert greeting == "Good morning سارة! Your balance is 500,000 dinars. What do you need?"
    h.graph.clear()
    h.say(ALICE, "send 50000 to Hadi")
    card = h.graph.last_card(ALICE)
    assert card["header"]["text"] == "Confirm transfer"
    body = card["body"]["text"]
    assert "You're sending 50,000 dinars (fifty thousand dinars)" in body
    assert "To: هادي, number ending in 2222" in body
    assert "Fee: 250 dinars, total: 50,250 dinars" in body
    assert "Your balance after paying: 449,750 dinars" in body
    assert "valid for 5 minutes" in body
    assert card["footer"]["text"].startswith("Expires ")
    assert list(kit.card_buttons(card)) == ["Yes, pay", "Change", "No, cancel"]
    assert all(bid.split(":")[0] in ("confirm", "change", "cancel") for bid in kit.card_buttons(card).values())
    h.tap(ALICE, "Yes, pay")
    assert h.graph.texts(ALICE)[-1].startswith("OK. Now type your PIN")
    h.say(ALICE, PIN)
    done = h.graph.texts(ALICE)[-1]
    assert done.startswith("Done. 50,000 dinars are held for the number ending in 2222")  # no account there yet
    assert "\nReceipt\n" in done and "demo" not in done.lower() and "Total: 50,250 dinars" in done
    assert h.balance(ALICE) == 449_750


def test_msa_sender_gets_fusha_and_msa_buttons(h):
    h.say(ALICE, "مرحبا، أريد أن أحوّل إلى هادي خمسين ألف دينار")
    assert h.graph.texts(ALICE) == []  # her own account: the request is answered at once
    card = h.graph.last_card(ALICE)
    assert card["header"]["text"] == "تأكيد تحويل"
    assert card["body"]["text"].startswith("ستحوّلين 50,000 دينار (خمسون ألف دينار)")
    assert list(kit.card_buttons(card)) == ["نعم، ادفع", "تعديل", "لا، إلغاء"]
    h.tap(ALICE, "تعديل")
    assert h.graph.texts(ALICE)[-1].startswith("حسناً، ألغيت هذه البطاقة")


def test_iraqi_stays_iraqi(h):
    h.say(ALICE, "هلا")
    h.say(ALICE, "دز لهادي خمسين الف")
    card = h.graph.last_card(ALICE)
    assert list(kit.card_buttons(card)) == ["إي، ادفع", "غيّر", "لا، بطّلت"]
    assert card["header"]["text"] == "تأكيد تحويل"
    assert card["body"]["text"].startswith("راح تدزين 50,000 دينار (خمسين ألف دينار)")


def test_language_follows_each_message_and_help_too(h):
    h.say(ALICE, "هلا")
    h.say(ALICE, "help")
    assert h.graph.texts(ALICE)[-1].startswith("I can pay your bills, send money to any Iraqi number")
    h.say(ALICE, "شكد رصيدي هسه")
    assert h.graph.texts(ALICE)[-1] == "رصيدج 500,000 دينار."


def test_rate_limit_notice_in_english(tmp_path):
    harness = Harness(tmp_path, max_msgs_per_user_hour=2)
    try:
        for body in ("hi", "what is my balance", "what is my balance"):
            harness.say(ALICE, body)
        assert harness.graph.texts(ALICE)[-1].startswith("You've reached this hour's message limit (2 messages)")
    finally:
        harness.stop()


def test_every_channel_text_exists_in_three_languages_and_fits():
    keys = set(texts.TEXTS) - set(texts.MANAGER_KEYS)
    for lang in ("msa", "en"):
        assert set(texts.BY_LANG[lang]) == keys, lang
    for lang in ("iq", "msa", "en"):
        for key in ("card_header_transfer", "card_header_bill", "card_header_topup"):
            assert len(texts.say(key, lang=lang)) <= 60
        assert len(texts.say("card_footer", lang=lang, time="23:59")) <= 60
    for text in texts.all_texts():
        assert "چ" not in text


def test_button_titles_fit_in_every_language():
    from payments.quote import BUTTONS_BY_LANG

    for buttons in BUTTONS_BY_LANG.values():
        assert all(len(b.label) <= BUTTON_TITLE_MAX for b in buttons)


# ---------------------------------------------------------------- voice-note replies


def test_a_voice_note_is_answered_with_a_voice_note_and_the_card(tmp_path):
    core = Speaking()
    harness = Harness(tmp_path, core=core)
    try:
        harness.say(ALICE, "هلا")
        harness.graph.clear()
        _voice(harness)
        assert harness.graph.kinds(ALICE) == ["audio", "interactive"]  # the voice first, the buttons last
        [audio] = harness.graph.audios(ALICE)
        assert audio["voice"] is True and harness.graph.uploads[0]["data"] == OGG
        [(spoken, lang)] = core.said
        assert lang == "iq"
        assert "هادي" in spoken and amount_in_words(50_000) in spoken and "دوسي إي ادفع" in spoken
        assert PIN not in spoken and "2222" not in spoken
        card = harness.graph.last_card(ALICE)
        assert list(kit.card_buttons(card)) == ["إي، ادفع", "غيّر", "لا، بطّلت"]
        # the tap and the typed PIN are not voice notes: in mirror mode they get text only
        harness.tap(ALICE, "إي، ادفع")
        harness.say(ALICE, PIN)
        assert len(harness.graph.audios(ALICE)) == 1
        assert harness.balance(ALICE) == 449_750
    finally:
        harness.stop()


def test_english_voice_note_gets_an_english_voice(tmp_path):
    core = Speaking(heard="send 50000 to Hadi")
    harness = Harness(tmp_path, core=core)
    try:
        harness.say(ALICE, "hi")
        _voice(harness)
        [(spoken, lang)] = core.said
        assert lang == "en"
        assert spoken.startswith("You're sending fifty thousand dinars to هادي, and the fee is")
        assert spoken.endswith("tap Yes, pay to confirm.")
        assert "I heard" not in spoken  # the transcript echo is text only
    finally:
        harness.stop()


def test_voice_reply_mode_off_and_always(tmp_path):
    core = Speaking()
    harness = Harness(tmp_path, core=core, voice_reply_mode="off")
    try:
        harness.say(ALICE, "هلا")
        _voice(harness)
        assert harness.graph.audios(ALICE) == [] and core.said == []
        assert harness.graph.last_card(ALICE)
    finally:
        harness.stop()
    core = Speaking()
    harness = Harness(tmp_path / "always", core=core, voice_reply_mode="always")
    try:
        harness.say(ALICE, "هلا")
        harness.say(ALICE, "شكد رصيدي")
        assert harness.graph.kinds(ALICE)[-2:] == ["audio", "text"]
        assert core.said[-1] == ("رصيدج نص مليون دينار.", "iq")  # money is said in words
    finally:
        harness.stop()


def test_the_pin_is_never_spoken(tmp_path):
    core = Speaking()
    harness = Harness(tmp_path, core=core, voice_reply_mode="always")
    try:
        harness.say(ALICE, "هلا")
        harness.say(ALICE, "الرمز")
        harness.say(ALICE, "دز لهادي خمسين الف")
        harness.tap(ALICE, "إي، ادفع")
        harness.say(ALICE, PIN)
        assert core.said and all(PIN not in text for text, _ in core.said)
    finally:
        harness.stop()


def test_a_voice_that_fails_leaves_the_text(tmp_path):
    core = Speaking(fail=True)
    harness = Harness(tmp_path, core=core)
    try:
        harness.say(ALICE, "هلا")
        harness.graph.clear()
        _voice(harness)
        assert harness.graph.kinds(ALICE) == ["interactive"]
    finally:
        harness.stop()
    core = Speaking()
    harness = Harness(tmp_path / "upload", core=core)
    try:
        harness.say(ALICE, "هلا")
        harness.graph.clear()
        harness.graph.fail_uploads = True
        _voice(harness)
        assert harness.graph.kinds(ALICE) == ["interactive"]
    finally:
        harness.stop()


def test_voice_counts_into_the_daily_budget(tmp_path):
    core = Speaking()
    harness = Harness(tmp_path, core=core, daily_budget_usd=0.015)
    try:
        harness.say(ALICE, "هلا")
        _voice(harness)  # $0.01 spoken: under the budget
        assert len(harness.graph.audios(ALICE)) == 1
        _voice(harness, "m2")  # $0.01 more would pass the budget: it was spent, so the next turn rests
        _voice(harness, "m3")
        assert "مرتاحة لباجر" in harness.graph.texts(ALICE)[-1]
        assert harness.channel.store.spent(harness.channel._day()) == pytest.approx(0.02)
    finally:
        harness.stop()


def test_bob_is_answered_in_his_own_language(tmp_path):
    harness = Harness(tmp_path)
    try:
        harness.say(ALICE, "what is my balance")
        harness.say(BOB, "شكد رصيدي")
        assert harness.graph.texts(ALICE)[-1] == "Your balance is 500,000 dinars."
        assert harness.graph.texts(BOB)[-1] == "رصيدك 300,000 دينار."  # Bob's own account, masculine
    finally:
        harness.stop()
