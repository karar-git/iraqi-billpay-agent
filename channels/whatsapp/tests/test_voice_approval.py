"""Voice approval (VOICE_APPROVAL=1): a voice note from the account's owner approves a payment with the tap and no PIN.

- The voice is kept only after a card made from a voice note was paid with the right PIN; a text-started payment
  keeps nothing. The first time, one line says what that allows and how to delete it.
- A later voice note that matches, for at most VOICE_APPROVAL_MAX_IQD to a known payee, makes a card that says so,
  and «إي، ادفع» pays it with no PIN asked. Anything else (no match, over the cap, a payee saved a moment ago, a
  forwarded note, an audio file, a locked PIN, the flag off) is the PIN, as before.
- «امسح صوتي» / "delete my voice" deletes it; the admin can clear it; the demo intro mentions it only when it works.

Offline: the fake Graph API, the stand-in core with ears (Speaking), and a fake embedder: no model, no ffmpeg."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from accounts.voiceprint import Voiceprint
from channels.whatsapp import texts, ycloud
from channels.whatsapp.app import create_app
from channels.whatsapp.graph import _inbound
from channels.whatsapp.tests import kit
from channels.whatsapp.tests.kit import ALICE, PIN
from channels.whatsapp.tests.test_admin import auth, form_of, post
from channels.whatsapp.tests.test_language_and_voice import Speaking
from channels.whatsapp.tests.test_whatsapp_channel import Harness

# Three speakers, as unit vectors the fake embedder hands out: the owner twice (close), and someone else.
OWNER = (1.0, 0.0, 0.0, 0.0)
OWNER_AGAIN = (0.96, 0.28, 0.0, 0.0)  # cosine 0.96 with OWNER
STRANGER = (0.2, 0.0, 0.98, 0.0)  # cosine 0.2 with OWNER
VOICES = {"owner": OWNER, "again": OWNER_AGAIN, "stranger": STRANGER}
NO_EMBEDDER = object()


class FakeEmbedder:
    """audio bytes «<voice>-<n>» -> that voice's vector; «short-…» -> None (too little speech)."""

    model = "fake-campplus"

    def __init__(self):
        self.calls: list[bytes] = []

    def embed(self, audio: bytes, mime: str) -> Voiceprint | None:
        self.calls.append(audio)
        vector = VOICES.get(audio.decode().split("-")[0])
        return Voiceprint(vector, 3.0, self.model) if vector else None


class VoiceHarness(Harness):
    def __init__(self, tmp_path, *, embedder=None, **overrides):
        self.embedder = FakeEmbedder() if embedder is None else (None if embedder is NO_EMBEDDER else embedder)
        overrides.setdefault("voice_approval", True)
        super().__init__(tmp_path, core=overrides.pop("core", None) or Speaking(), **overrides)

    def start(self) -> None:
        self.app = create_app(self.settings(), graph=self.graph, core=self.core, seed=kit.seed(), clock=self.clock,
                              voiceprint=self.embedder)
        self._cm = TestClient(self.app)
        self.client = self._cm.__enter__()
        self.channel = self.app.state.channel

    def voice(self, who: str, media: str, heard: str, **audio_fields) -> None:
        """A voice note whose audio is «media» and whose words the core hears as `heard`."""
        self.core.heard = heard
        self.graph.media_files[media] = (media.encode(), "audio/ogg")
        message = kit.voice(who, media, self.clock)
        message["audio"].update(audio_fields.pop("audio", {}))
        message.update(audio_fields)
        self.send(message)

    def samples(self, uid: str = "u1") -> int:
        return self.channel.wallet.voiceprint_samples(uid)

    def audit(self, action: str, uid: str = "u1") -> list[dict]:
        return [row for row in self.channel.accounts.audit_rows(user_id=uid, limit=200) if row["action"] == action]


@pytest.fixture
def h(tmp_path):
    harness = VoiceHarness(tmp_path)
    yield harness
    harness.stop()


def say(key: str, **slots) -> str:
    return texts.say(key, "f", "iq", **slots)  # Alice (u1) is a woman, written to in Iraqi


ENROLLED = say("voice_enrolled", cap=100_000)
MATCHED = say("voice_matched")
ASK_PIN = say("ask_pin")


def body(card: dict) -> str:
    return card["body"]["text"]


def pin_paid_voice_card(h: VoiceHarness, media: str = "owner-1", heard: str = "دز لهادي خمسين الف") -> None:
    """A card made from a voice note, then «إي، ادفع» and the right PIN."""
    h.voice(ALICE, media, heard)
    h.tap(ALICE, "إي، ادفع")
    assert h.graph.texts(ALICE)[-1] == ASK_PIN
    h.say(ALICE, PIN)


def enrolled(h: VoiceHarness) -> None:
    pin_paid_voice_card(h)
    assert h.samples() == 1
    h.graph.clear()


# ---------------------------------------------------------------- enrolment


def test_a_voice_card_paid_with_the_pin_keeps_the_voice_and_says_so_once(h):
    pin_paid_voice_card(h)
    said = h.graph.texts(ALICE)[-1]
    assert "وصل" in said and ENROLLED in said and "امسح صوتي" in said and "100,000" in said
    assert h.samples() == 1 and len(h.audit("voice_enrolled")) == 1
    assert "چ" not in said
    h.graph.clear()
    pin_paid_voice_card(h, "again-1", "دز لهادي مية وخمسين الف")  # over the cap: the PIN, and a second sample
    assert h.samples() == 2 and ENROLLED not in h.graph.everything(ALICE)  # said once


def test_the_first_card_needs_the_pin_and_shows_no_voice_line(h):
    h.voice(ALICE, "owner-1", "دز لهادي خمسين الف")
    assert MATCHED not in body(h.graph.last_card(ALICE))  # nothing kept yet: nothing can match
    h.tap(ALICE, "إي، ادفع")
    assert h.graph.texts(ALICE)[-1] == ASK_PIN


def test_a_text_started_pin_payment_keeps_no_voice(h):
    h.say(ALICE, "دز لهادي خمسين الف")
    h.tap(ALICE, "إي، ادفع")
    h.say(ALICE, PIN)
    assert "وصل" in h.graph.texts(ALICE)[-1] and ENROLLED not in h.graph.everything(ALICE)
    assert h.samples() == 0 and h.embedder.calls == []


def test_a_voice_card_paid_after_a_wrong_pin_then_the_right_one_still_keeps_it(h):
    h.voice(ALICE, "owner-1", "دز لهادي خمسين الف")
    h.tap(ALICE, "إي، ادفع")
    h.say(ALICE, "9999")
    assert h.samples() == 0
    h.say(ALICE, PIN)
    assert h.samples() == 1


def test_a_voice_card_cancelled_keeps_nothing(h):
    h.voice(ALICE, "owner-1", "دز لهادي خمسين الف")
    h.tap(ALICE, "لا، بطّلت")
    assert h.samples() == 0


def test_too_little_speech_keeps_nothing_and_pays_with_the_pin(h):
    pin_paid_voice_card(h, "short-1")
    assert h.samples() == 0 and ENROLLED not in h.graph.everything(ALICE)


# ---------------------------------------------------------------- approval


def test_a_matching_voice_note_pays_with_the_tap_and_no_pin(h):
    enrolled(h)
    before = h.balance(ALICE)
    h.voice(ALICE, "again-1", "دز لهادي عشرين الف")
    card = h.graph.last_card(ALICE)
    assert MATCHED in body(card) and "20,000" in body(card)
    h.graph.clear()
    h.tap(ALICE, "إي، ادفع", card)
    said = h.graph.texts(ALICE)
    assert ASK_PIN not in said and any("وصل" in text for text in said), said
    assert say("delete_pin") not in "\n".join(said)  # no PIN was typed
    assert h.balance(ALICE) == before - 20_000 - 250
    [row] = h.audit("voice_approved")
    assert '"score"' in row["details_json"] and '"amount": 20000' in row["details_json"]
    assert h.channel.wallet.audit()["ok"]
    h.graph.clear()
    h.tap(ALICE, "إي، ادفع", card)  # the same card again: paid once, its receipt
    assert h.balance(ALICE) == before - 20_250
    assert say("paid_once") in h.graph.texts(ALICE)[-1]


def test_a_saved_bill_is_paid_by_voice_too(h):
    enrolled(h)
    h.voice(ALICE, "again-1", "ادفع المولدة")
    card = h.graph.last_card(ALICE)
    assert MATCHED in body(card) and "45,000" in body(card)
    h.tap(ALICE, "إي، ادفع", card)
    assert ASK_PIN not in h.graph.texts(ALICE)
    assert h.channel.accounts.bills("a1")[0]["status"] == "paid"


@pytest.mark.parametrize("media, heard, amount", [
    ("stranger-1", "دز لهادي عشرين الف", "20,000"),  # another speaker
    ("again-1", "دز لهادي مية وخمسين الف", "150,000"),  # over 100,000
    ("short-1", "دز لهادي عشرين الف", "20,000"),  # too little speech to tell
])
def test_anything_else_asks_for_the_pin(h, media, heard, amount):
    enrolled(h)
    h.voice(ALICE, media, heard)
    card = h.graph.last_card(ALICE)
    assert MATCHED not in body(card) and amount in body(card)
    h.tap(ALICE, "إي، ادفع", card)
    assert h.graph.texts(ALICE)[-1] == ASK_PIN
    assert h.audit("voice_approved") == []


def test_a_contact_saved_a_moment_ago_is_not_a_known_payee_until_a_day_has_passed(h):
    enrolled(h)
    h.say(ALICE, "ضيف سامر 07809990001")
    h.graph.clear()
    h.voice(ALICE, "again-1", "دز لسامر عشرة الاف")
    card = h.graph.last_card(ALICE)
    assert MATCHED not in body(card)  # someone holding the phone could have saved their own number a moment ago
    h.tap(ALICE, "لا، بطّلت", card)
    h.clock.advance(hours=25)
    h.voice(ALICE, "again-2", "دز لسامر عشرة الاف")
    assert MATCHED in body(h.graph.last_card(ALICE))


def test_a_number_never_paid_before_needs_the_pin(h):
    enrolled(h)
    h.voice(ALICE, "again-1", "دز عشرة الاف لـ 07809990002")
    card = h.graph.last_card(ALICE)
    assert MATCHED not in body(card)
    h.tap(ALICE, "إي، ادفع", card)
    assert h.graph.texts(ALICE)[-1] == ASK_PIN


def test_a_forwarded_voice_note_and_an_audio_file_never_count(h):
    enrolled(h)
    calls = len(h.embedder.calls)
    h.voice(ALICE, "again-1", "دز لهادي عشرين الف", context={"forwarded": True})
    card = h.graph.last_card(ALICE)
    assert MATCHED not in body(card)
    h.tap(ALICE, "إي، ادفع", card)
    assert h.graph.texts(ALICE)[-1] == ASK_PIN
    h.say(ALICE, PIN)  # paid with the PIN: a forwarded note is still never kept
    h.voice(ALICE, "again-2", "دز لهادي عشرين الف", audio={"voice": False})  # an audio file, not a voice note
    assert MATCHED not in body(h.graph.last_card(ALICE))
    h.tap(ALICE, "إي، ادفع")
    h.say(ALICE, PIN)
    assert len(h.embedder.calls) == calls and h.samples() == 1


def test_a_frequently_forwarded_note_is_forwarded_too_on_both_providers():
    message = {"from": ALICE, "id": "wamid.x", "timestamp": "1", "type": "audio",
               "audio": {"id": "m", "voice": True}, "context": {"frequently_forwarded": True}}
    assert _inbound(message, {}).forwarded is True
    assert _inbound({**message, "context": {"id": "wamid.reply"}}, {}).forwarded is False  # a reply is no forward
    clock = kit.Clock()
    audio = {"link": kit.yc_media_link("v1"), "id": "v1", "mime_type": "audio/ogg; codecs=opus", "voice": True}
    [forwarded] = ycloud.parse(kit.yc_event(ALICE, clock, type="audio", audio=audio, context={"forwarded": True}),
                               kit.YC_BUSINESS)
    [own] = ycloud.parse(kit.yc_event(ALICE, clock, type="audio", audio=audio), kit.YC_BUSINESS)
    [file_] = ycloud.parse(kit.yc_event(ALICE, clock, type="audio", audio={**audio, "voice": False}), kit.YC_BUSINESS)
    assert forwarded.forwarded and forwarded.kind == "voice"
    assert not own.forwarded and own.kind == "voice" and file_.kind == "audio"


def test_a_locked_pin_turns_voice_off(h):
    enrolled(h)
    with h.channel.db.connect() as conn:
        conn.execute("UPDATE users SET pin_locked_until = '2099-01-01T00:00:00Z' WHERE id = 'u1'")
        conn.commit()
    h.voice(ALICE, "again-1", "دز لهادي عشرين الف")
    assert MATCHED not in body(h.graph.last_card(ALICE))
    assert h.audit("voice_approved") == []


def test_a_voice_note_while_the_pin_is_asked_is_still_never_heard(h):
    enrolled(h)
    h.voice(ALICE, "stranger-1", "دز لهادي عشرين الف")
    h.tap(ALICE, "إي، ادفع")
    calls = len(h.embedder.calls)
    h.voice(ALICE, "again-1", "1234")
    assert h.graph.texts(ALICE)[-1] == say("pin_by_voice")
    assert len(h.embedder.calls) == calls and h.audit("voice_approved") == []


def test_voice_deleted_after_the_card_falls_back_to_the_pin(h):
    enrolled(h)
    before = h.balance(ALICE)
    h.voice(ALICE, "again-1", "دز لهادي عشرين الف")
    card = h.graph.last_card(ALICE)
    assert MATCHED in body(card)
    h.channel.wallet.delete_voice("u1", actor="admin:test")  # e.g. from the admin page, while the card is open
    h.graph.clear()
    h.tap(ALICE, "إي، ادفع", card)
    assert h.graph.texts(ALICE)[-1] == say("voice_fell_back") + "\n" + ASK_PIN
    assert h.balance(ALICE) == before
    h.say(ALICE, PIN)
    assert h.balance(ALICE) == before - 20_250


# ---------------------------------------------------------------- the switch, deleting, the admin, the intro


def test_with_the_flag_off_nothing_is_kept_and_the_pin_is_asked(tmp_path):
    h = VoiceHarness(tmp_path, voice_approval=False)
    try:
        assert h.channel.voice_approval_on is False
        pin_paid_voice_card(h)
        assert h.samples() == 0 and h.embedder.calls == [] and ENROLLED not in h.graph.everything(ALICE)
    finally:
        h.stop()


def test_with_the_flag_on_but_no_model_it_is_off(tmp_path):
    h = VoiceHarness(tmp_path, embedder=NO_EMBEDDER)
    try:
        assert h.channel.voice_approval_on is False
        pin_paid_voice_card(h)
        assert h.samples() == 0
    finally:
        h.stop()


@pytest.mark.parametrize("words, lang", [("امسح صوتي", "iq"), ("delete my voice", "en"), ("احذف صوتي", "iq")])
def test_delete_my_voice(h, words, lang):
    enrolled(h)
    h.say(ALICE, words)
    assert h.graph.texts(ALICE)[-1] == texts.say("voice_deleted", "f", lang)
    assert h.samples() == 0 and len(h.audit("voice_deleted")) == 1
    h.voice(ALICE, "again-1", "دز لهادي عشرين الف")
    assert MATCHED not in body(h.graph.last_card(ALICE))
    h.graph.clear()
    h.say(ALICE, words)
    assert h.graph.texts(ALICE)[-1] == texts.say("voice_none", "f", lang)


def test_delete_my_voice_said_in_a_voice_note(h):
    enrolled(h)
    h.voice(ALICE, "again-1", "امسح صوتي")
    assert h.graph.texts(ALICE)[-1] == say("voice_deleted") and h.samples() == 0


def test_the_admin_sees_a_voice_badge_and_can_clear_it(tmp_path):
    h = VoiceHarness(tmp_path, admin_password="test-admin-password")
    try:
        enrolled(h)
        people = h.client.get("/admin/users", headers=auth()).text
        assert ">voice<" in people
        page = h.client.get("/admin/users/u1", headers=auth()).text
        assert "1 sample(s)" in page
        fields = form_of(page, "/admin/users/u1/voice/clear")
        assert post(h, "/admin/users/u1/voice/clear", {**fields, "reason": "asked by phone"}).status_code == 303
        assert h.samples() == 0
        [row] = h.audit("voice_deleted")
        assert row["actor"].startswith("admin:")
        assert ">voice<" not in h.client.get("/admin/users", headers=auth()).text
    finally:
        h.stop()


def test_the_demo_intro_mentions_voice_approval_only_when_it_works(tmp_path):
    carol = "9647700000003"
    lines = {"iq": "100,000", "en": "up to 100,000 dinars"}
    for flag, embedder, expected in ((True, None, True), (True, NO_EMBEDDER, False), (False, None, False)):
        h = VoiceHarness(tmp_path / f"{flag}{embedder is None}", signup_mode="demo", voice_approval=flag,
                         embedder=embedder)
        try:
            h.say(carol, "هلا")
            intro = h.graph.texts(carol)[0]
            assert ("بالرمز السري" in intro and lines["iq"] in intro) is expected, intro
            assert "چ" not in intro and "شركة" not in intro
        finally:
            h.stop()
    h = VoiceHarness(tmp_path / "en", signup_mode="demo")
    try:
        h.say(carol, "hi")
        assert lines["en"] in h.graph.texts(carol)[0]
    finally:
        h.stop()


def test_the_voice_texts_follow_the_house_rules():
    for lang in ("iq", "msa", "en"):
        for key in ("voice_matched", "voice_fell_back", "voice_enrolled", "voice_deleted", "voice_none"):
            for gender in ("f", "m"):
                line = texts.say(key, gender, lang, cap=100_000)
                assert "چ" not in line and "شركة" not in line and len(line) < 220, (lang, key)
    assert re.search(r"صوتج|إلج", texts.say("voice_none", "f", "iq"))
