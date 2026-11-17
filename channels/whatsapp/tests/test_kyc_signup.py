"""The identity document step of sign-up, and «وثّق حسابي» afterwards, end to end on the WhatsApp channel.

Offline: a fake Graph API serves the photo bytes, a fake reader stands in for the vision model (it answers with the
raw fields the test wrote for that photo), the clock is moved by hand. Every name, number and document is made up.
What is checked: the question and its notice in three languages, each verdict in each mode, the skip, three bad
photos, one document on two accounts, the limits of each level (the single cap as a hard stop with its real number,
and the evidence line), verifying later, and that neither the photo nor the whole document number is anywhere on
disk afterwards.
"""

from __future__ import annotations

import logging
import sqlite3

import pytest

import kyc
from channels.whatsapp.app import create_app
from channels.whatsapp.config import Settings
from channels.whatsapp.tests import kit
from channels.whatsapp.tests.kit import ALICE, BOB
from channels.whatsapp.tests.test_whatsapp_channel import Harness
from core.stub import StubCore
from fastapi.testclient import TestClient

CAROL = "9647700000003"  # no account yet
DAN = "9647700000004"  # no account yet
GOOD_PIN = "2583"
PEPPER = "test-kyc-pepper-0123456789"  # a test value, not a real secret
NUMBER = "199012345678"  # a made-up 12-digit number
OTHER_NUMBER = "200198765432"
NAME = "زينب هادي"
PRINTED = "زينب هادي كريم السعدي"
JPEG = b"\xff\xd8\xff\xe0"
SMALL = kyc.limits_for("none")
FULL = kyc.limits_for("verified")


def raw(**changes) -> dict:
    """What a perfect reader copies from a good national card (kyc.schema.SCHEMA)."""
    base = {"is_identity_document": True, "document_type": "national_card", "readable": True,
            "full_name_ar": PRINTED, "full_name_latin": None, "document_number": NUMBER,
            "birth_date": "1995-04-12", "expiry_date": "2031-01-01", "sex": "f",
            "looks_like_photo_of_screen_or_copy": False, "marked_as_specimen": False, "notes": ""}
    return {**base, **changes}


class Reader:
    """The vision model's stand-in: the raw fields the test set for exactly these bytes, else nothing."""

    def __init__(self):
        self.answers: dict[bytes, dict | None] = {}
        self.calls = 0

    def __call__(self, image: bytes, mime: str) -> dict | None:
        self.calls += 1
        return self.answers.get(bytes(image))


class KycHarness(Harness):
    def __init__(self, tmp_path, *, reader=None, kyc_llm=None, **overrides):
        self.reader = reader if reader is not None else Reader()
        self.kyc_llm = kyc_llm
        self.photos = 0
        super().__init__(tmp_path, core=overrides.pop("core", None) or StubCore(),
                         **{"signup_kyc": "optional", "kyc_pepper": PEPPER, "signup_mode": "open", **overrides})

    def start(self) -> None:
        self.app = create_app(self.settings(), graph=self.graph, core=self.core, seed=kit.seed(), clock=self.clock,
                              kyc_reader=self.reader or None, kyc_llm=self.kyc_llm)
        self._cm = TestClient(self.app)
        self.client = self._cm.__enter__()
        self.channel = self.app.state.channel

    def photo(self, who: str, reading: dict | None, mime: str = "image/jpeg", body: bytes | None = None) -> bytes:
        """Send a photo whose reading is `reading` (None: the reader gives nothing). Returns its bytes."""
        self.photos += 1
        data = body if body is not None else JPEG + f"PHOTO-BYTES-{self.photos:04d}-".encode() * 40
        media_id = f"photo{self.photos}"
        self.graph.media_files[media_id] = (data, mime)
        if isinstance(self.reader, Reader):
            self.reader.answers[data] = reading
        self.send(kit.image(who, media_id, self.clock, mime=mime))
        return data

    def tap_id(self, who: str, button_id: str) -> None:
        self.send(kit.tap(who, button_id, "x", self.clock))

    def row(self, who: str) -> dict | None:
        return self.channel.store.onboarding(self.channel.sender_key(who))

    def user(self, who: str) -> dict | None:
        return self.channel.accounts.by_phone(who)

    def last(self, who: str) -> str:
        """The words of the last message sent to `who`, whether it was a text or a message with buttons."""
        payload = self.graph.to(who)[-1]
        return payload["text"]["body"] if payload["type"] == "text" else payload["interactive"]["body"]["text"]

    def held(self, who: str) -> dict | None:
        return self.channel.accounts.kyc_held(who)

    def audit(self, action: str) -> list[dict]:
        return self.channel.accounts.audit_rows(action=action, limit=500)


@pytest.fixture
def h(tmp_path):
    harness = KycHarness(tmp_path)
    yield harness
    harness.stop()


@pytest.fixture
def required(tmp_path):
    harness = KycHarness(tmp_path, signup_kyc="required")
    yield harness
    harness.stop()


def to_document(h: KycHarness, who: str, *, name: str = NAME, gender: str = "o:f", first: str = "هلا") -> None:
    """From nothing to the open document question."""
    h.say(who, first)
    h.tap_id(who, "o:open")
    h.tap_id(who, "o:name_other")
    h.say(who, name)
    h.tap_id(who, gender)
    assert h.row(who)["step"] == "document"


def set_pin(h: KycHarness, who: str, pin: str = GOOD_PIN) -> dict:
    h.say(who, pin)
    h.say(who, pin)
    user = h.user(who)
    assert user is not None and user["status"] == "active", h.graph.everything(who)
    return user


def sign_up(h: KycHarness, who: str, reading: dict | None | str = "later", **kw) -> dict:
    to_document(h, who, **kw)
    if reading == "later":
        h.tap_id(who, "o:kyc_later")
    else:
        h.photo(who, reading)
    return set_pin(h, who)


def fund(h: KycHarness, who: str, amount: int = 400_000) -> None:
    h.channel.wallet.admin_credit(h.user(who)["id"], amount, "test funds", op_id=f"op-{who}-{amount}", actor="admin:test",
                                  notify=False)


# ---------------------------------------------------------------- the question and its notice


@pytest.mark.parametrize("first, lang, asks, pilot, kept, stored, later", [
    ("هلا", "iq", "صوّري وحدة من وثائقج", "تجريبية", "وما تنحفظ", "آخر 4 أرقام", "بعدين"),
    ("السلام عليكم، أود فتح حساب من فضلكم", "msa", "صوّري إحدى وثائقك", "تجريبية", "ولا تُحفظ", "آخر 4 أرقام", "لاحقاً"),
    ("Hello there", "en", "take a photo of one of these", "pilot", "is not kept", "the last 4 digits", "Later"),
])
def test_the_question_names_the_documents_and_says_plainly_what_is_kept(h, first, lang, asks, pilot, kept, stored, later):
    to_document(h, CAROL, first=first, name="Zainab Hadi" if lang == "en" else NAME)
    ask = h.graph.cards(CAROL)[-1]
    text = ask["body"]["text"]
    assert h.row(CAROL)["lang"] == lang
    assert asks in text and pilot in text and kept in text and stored in text
    for word in ({"iq": ("البطاقة الوطنية الموحدة", "الجواز", "بطاقة الإقامة", "رصيد داخل الخدمة", "نوع الوثيقة",
                         "الاسم", "سنة الميلاد", "قارئ آلي مرة وحدة"),
                  "msa": ("البطاقة الوطنية الموحدة", "جواز السفر", "بطاقة الإقامة", "رصيد داخل الخدمة", "نوع الوثيقة",
                          "الاسم", "سنة الميلاد", "قارئ آلي مرة واحدة"),
                  "en": ("unified national card", "passport", "residence card", "mock ledger", "document type",
                         "the name", "birth year", "read once by an automated reader")}[lang]):
        assert word in text, word
    assert list(kit.card_buttons(ask)) == [later]  # optional: one button, the skip
    assert "50,000" in text and "150,000" in text  # the small limits, as numbers
    assert len(text) <= 1024 and "چ" not in text and "—" not in text


def test_required_mode_has_no_skip_button_and_says_so(required):
    to_document(required, CAROL)
    assert required.graph.to(CAROL)[-1]["type"] == "text"  # no buttons at all
    text = required.graph.texts(CAROL)[-1]
    assert "هالخطوة لازمة حتى ينفتح الحساب" in text and "«الغاء»" in text and "بعدين" not in text
    required.say(CAROL, "بعدين")  # typed: not a skip here, the question again
    assert required.row(CAROL)["step"] == "document" and required.held(CAROL) is None
    assert "صوّري وحدة من وثائقج" in required.graph.texts(CAROL)[-1]
    required.tap_id(CAROL, "o:kyc_later")  # a button from another deployment's message: no skip either
    assert required.row(CAROL)["step"] == "document"
    assert required.user(CAROL) is None and required.reader.calls == 0


# ---------------------------------------------------------------- verified


@pytest.mark.parametrize("mode", ["optional", "required"])
def test_a_good_document_goes_on_to_the_pin_and_the_account_opens_verified(tmp_path, mode):
    h = KycHarness(tmp_path, signup_kyc=mode)
    try:
        to_document(h, CAROL)
        h.photo(CAROL, raw())
        said = h.graph.texts(CAROL)[-1]
        assert said.startswith("تم توثيق هويتج. صار حدج اليومي 1,000,000 دينار")
        assert "اختاري رمز سري من 4 إلى 6 أرقام" in said
        assert h.row(CAROL)["step"] == "pin" and h.user(CAROL) is None  # no account before the PIN
        user = set_pin(h, CAROL)
        assert user["kyc_level"] == "verified" and user["daily_limit"] == FULL["daily_limit"] == 1_000_000
        assert user["kyc_document_type"] == "national_card" and user["kyc_name"] == PRINTED
        assert user["kyc_number_last4"] == NUMBER[-4:] and user["kyc_birth_year"] == 1995
        assert user["kyc_number_fingerprint"] == kyc.fingerprint(NUMBER, PEPPER.encode())
        assert user["kyc_reason"] == "ok" and user["kyc_checked_at"] == "2026-11-02T06:00:00Z"
        assert h.held(CAROL) is None and h.row(CAROL) is None and h.reader.calls == 1
        [line] = h.audit("kyc_level_set")
        assert '"to": "verified"' in line["details_json"] and PRINTED not in line["details_json"]
        assert h.channel.accounts.open_requests() == []
    finally:
        h.stop()


def test_a_latin_name_and_a_passport_are_read_too(h):
    to_document(h, CAROL, name="Zainab Kareem", first="Hello there", gender="o:f")
    h.photo(CAROL, raw(document_type="passport", document_number="A12345678", full_name_latin="ZAINAB HADI KAREEM",
                       full_name_ar=None), mime="image/png", body=b"\x89PNG\r\n\x1a\n" + b"PASSPORT-PHOTO" * 30)
    assert "Your identity is verified" in h.graph.texts(CAROL)[-1]
    user = set_pin(h, CAROL)
    assert user["kyc_level"] == "verified" and user["kyc_document_type"] == "passport"
    assert user["kyc_number_last4"] == "5678" and user["kyc_name"] == "ZAINAB HADI KAREEM"


# ---------------------------------------------------------------- the skip


@pytest.mark.parametrize("how", ["button", "بعدين", "later", "مو هسه"])
def test_later_opens_the_account_with_the_small_limits(h, how):
    to_document(h, CAROL)
    if how == "button":
        h.tap_id(CAROL, "o:kyc_later")
    else:
        h.say(CAROL, how)
    said = h.graph.texts(CAROL)[-1]
    assert "«وثّق حسابي»" in said and "اختاري رمز سري" in said
    user = set_pin(h, CAROL)
    assert user["kyc_level"] == "none" and user["kyc_reason"] == "skipped"
    assert user["daily_limit"] == SMALL["daily_limit"] == 150_000
    assert user["kyc_number_fingerprint"] is None and user["kyc_name"] is None
    assert h.reader.calls == 0 and h.held(CAROL) is None


def test_a_text_at_the_step_repeats_the_question_and_a_burst_gets_one_answer(tmp_path):
    for mode in ("optional", "required"):
        h = KycHarness(tmp_path / mode, signup_kyc=mode, debounce_seconds=0.3)
        try:
            to_document(h, CAROL)
            h.graph.clear()
            h.say(CAROL, "شنو يعني؟")
            assert len(h.graph.to(CAROL)) == 1 and "صوّري وحدة من وثائقج" in h.last(CAROL)
            h.graph.clear()
            h.send(*[kit.text(CAROL, body, h.clock) for body in ("ما فهمت", "شلون", "وينكم")])
            assert len(h.graph.to(CAROL)) == 1 and "صوّري وحدة من وثائقج" in h.last(CAROL), mode
            assert h.row(CAROL)["step"] == "document" and h.reader.calls == 0
        finally:
            h.stop()


def test_a_voice_note_at_the_step_asks_for_a_photo_and_cancel_drops_everything(h):
    to_document(h, CAROL)
    h.graph.media_files["v1"] = (b"OggS", "audio/ogg")
    h.send(kit.voice(CAROL, "v1", h.clock))
    assert "دزي صورة، مو رسالة صوتية" in h.last(CAROL) and "صوّري وحدة من وثائقج" in h.last(CAROL)
    h.photo(CAROL, raw(looks_like_photo_of_screen_or_copy=True))  # a review: a record is held for the sign-up
    assert h.held(CAROL)["level"] == "pending"
    h.say(CAROL, "الغاء")
    assert "لغيت فتح الحساب" in h.graph.texts(CAROL)[-1]
    assert h.row(CAROL) is None and h.held(CAROL) is None and h.user(CAROL) is None


def test_one_name_word_is_asked_for_two_before_the_document(h):
    h.say(CAROL, "هلا")
    h.tap_id(CAROL, "o:open")
    h.tap_id(CAROL, "o:name_yes")  # the profile name «tester»: one word can never match a document
    assert "اسمك واسم أبوك على الأقل" in h.graph.texts(CAROL)[-1] and h.row(CAROL)["step"] == "name"
    h.say(CAROL, "زينب")
    assert "اسمك واسم أبوك على الأقل" in h.graph.texts(CAROL)[-1] and h.row(CAROL)["step"] == "name"
    h.say(CAROL, NAME)
    assert h.row(CAROL)["step"] == "gender"


# ---------------------------------------------------------------- retry, and three bad photos


def test_a_photo_that_is_no_document_says_why_and_asks_again(h):
    to_document(h, CAROL)
    h.photo(CAROL, raw(is_identity_document=False, document_type="none"))
    again = h.graph.cards(CAROL)[-1]["body"]["text"]
    assert again.startswith("هاي الصورة مو هوية.") and "المحاولة 1 من 3." in again
    assert "صوّري وحدة من وثائقج" in again and list(kit.card_buttons(h.graph.cards(CAROL)[-1])) == ["بعدين"]
    assert h.row(CAROL)["step"] == "document" and h.row(CAROL)["tries"] == 1 and h.held(CAROL) is None
    h.photo(CAROL, raw(readable=False))
    assert h.graph.cards(CAROL)[-1]["body"]["text"].startswith("الصورة مو واضحة") and h.row(CAROL)["tries"] == 2
    h.photo(CAROL, raw())  # the third photo is a good one
    assert h.row(CAROL)["step"] == "pin"
    assert set_pin(h, CAROL)["kyc_level"] == "verified"


@pytest.mark.parametrize("mode", ["optional", "required"])
def test_three_bad_photos_go_to_a_person_and_the_account_opens_pending(tmp_path, mode):
    h = KycHarness(tmp_path, signup_kyc=mode, manager=BOB)
    try:
        to_document(h, CAROL)
        h.photo(CAROL, raw(is_identity_document=False, document_type="none"))  # a photo of something else
        assert "هاي الصورة مو هوية" in h.last(CAROL)
        h.photo(CAROL, raw(document_number="12"))
        assert "رقم الهوية ما انقرا صح" in h.last(CAROL) and "المحاولة 2 من 3." in h.last(CAROL)
        h.photo(CAROL, raw(readable=False))
        said = h.graph.texts(CAROL)[-1]
        assert "بعد 3 محاولات. موظف راح يراجع طلبج" in said
        assert "الصورة مو واضحة" not in said  # the last photo's «take it again» would contradict the end of the tries
        assert "بالحدود الصغيرة: 50,000 دينار بالدفعة الوحدة و150,000 دينار باليوم" in said and "اختاري رمز سري" in said
        assert h.row(CAROL)["step"] == "pin" and h.reader.calls == 3
        user = set_pin(h, CAROL)
        assert user["kyc_level"] == "pending" and user["kyc_reason"] == "too_many_attempts"
        assert user["daily_limit"] == 150_000 and user["kyc_document_type"] is None
        [request] = h.channel.accounts.open_requests()
        assert request["kind"] == "kyc_review" and request["user_id"] == user["id"]
        assert any("توثيق ينتظر مراجعة" in t and "0770 000 0003" in t for t in h.graph.texts(BOB))  # the manager is told
    finally:
        h.stop()


def test_a_file_that_is_not_a_photo_or_is_too_large_is_never_sent_to_the_reader(h):
    to_document(h, CAROL)
    h.photo(CAROL, raw(), mime="application/pdf", body=b"%PDF-1.7 a scanned card")
    assert "هالملف ما نگدر نفتحه" in h.last(CAROL)
    h.photo(CAROL, raw(), mime="image/png", body=JPEG + b"a jpeg declared as png")
    assert "هالملف ما نگدر نفتحه" in h.last(CAROL)
    assert h.reader.calls == 0 and h.row(CAROL)["tries"] == 2


def test_a_photo_that_cannot_be_downloaded_costs_no_try(h):
    to_document(h, CAROL)
    h.send(kit.image(CAROL, "missing-media", h.clock))
    assert "ما گدرنا نقرا الصورة هسه" in h.last(CAROL) and "صوّري وحدة من وثائقج" in h.last(CAROL)
    assert h.row(CAROL)["tries"] == 0 and h.reader.calls == 0 and h.audit("kyc_checked") == []


# ---------------------------------------------------------------- review


@pytest.mark.parametrize("reading, reason, words", [
    (raw(full_name_ar="سارة محمد علي الجبوري"), "name_mismatch", "الاسم على الهوية يختلف عن الاسم اللي كتبتيه"),
    (raw(looks_like_photo_of_screen_or_copy=True), "screen_or_copy", "مأخوذة من شاشة أو من نسخة"),
])
@pytest.mark.parametrize("mode", ["optional", "required"])
def test_a_review_opens_the_account_pending_with_the_small_limits_and_a_request(tmp_path, mode, reading, reason, words):
    h = KycHarness(tmp_path, signup_kyc=mode)
    try:
        to_document(h, CAROL)
        h.photo(CAROL, reading)
        said = h.graph.texts(CAROL)[-1]
        assert words in said and "موظف راح يراجع طلبج" in said and "50,000 دينار بالدفعة الوحدة" in said
        assert "اختاري رمز سري" in said and h.row(CAROL)["step"] == "pin"
        user = set_pin(h, CAROL)
        assert user["kyc_level"] == "pending" and user["kyc_reason"] == reason and user["daily_limit"] == 150_000
        assert user["kyc_number_last4"] == NUMBER[-4:] and user["kyc_document_type"] == "national_card"
        [request] = h.channel.accounts.open_requests()
        assert (request["kind"], request["user_id"], request["reason"]) == ("kyc_review", user["id"], reason)
    finally:
        h.stop()


def test_the_same_document_on_a_second_account_is_never_verified_by_itself(h):
    first = sign_up(h, CAROL, raw())
    assert first["kyc_level"] == "verified"
    to_document(h, DAN, name=NAME)
    h.photo(DAN, raw())  # the very same card, a good reading, a matching name
    said = h.graph.texts(DAN)[-1]
    assert "هالوثيقة تحتاج مراجعة من موظف" in said and "حسابج ينفتح هسه بالحدود الصغيرة" in said
    second = set_pin(h, DAN)
    assert second["kyc_level"] == "pending" and second["kyc_reason"] == "duplicate_document"
    assert second["daily_limit"] == 150_000
    assert second["kyc_number_fingerprint"] == first["kyc_number_fingerprint"]
    assert h.user(CAROL)["kyc_level"] == "verified"  # the first account is untouched
    same = h.channel.accounts.kyc_same_document(second["kyc_number_fingerprint"], uid=second["id"])
    assert [o["user_id"] for o in same] == [first["id"]]
    [check] = [a for a in h.audit("kyc_checked") if '"duplicate_document"' in a["details_json"]]
    assert NUMBER not in check["details_json"]


def test_two_open_sign_ups_with_one_document_the_second_is_a_review(h):
    to_document(h, CAROL)
    h.photo(CAROL, raw())  # verified, waiting for its PIN
    to_document(h, DAN)
    h.photo(DAN, raw())
    assert h.held(CAROL)["level"] == "verified"
    assert (h.held(DAN)["level"], h.held(DAN)["reason"]) == ("pending", "duplicate_document")


def test_a_closed_account_does_not_block_its_document(h):
    first = sign_up(h, CAROL, raw())
    h.channel.wallet.set_status(first["id"], "closed", actor="admin:test", reason="left the pilot")
    assert sign_up(h, DAN, raw())["kyc_level"] == "verified"


# ---------------------------------------------------------------- refused


@pytest.mark.parametrize("reading, reason, words, again", [
    (raw(birth_date="2012-05-01"), "under_age", "الخدمة للي عمرهم 18 سنة وفوق", False),
    (raw(expiry_date="2025-01-01"), "expired", "هويتج منتهية", True),
    (raw(document_type="driving_licence"), "type_not_accepted", "هالنوع من الوثائق ما نقبله", True),
    (raw(marked_as_specimen=True), "specimen", "هاي صورة نموذج", True),
])
@pytest.mark.parametrize("mode", ["optional", "required"])
def test_a_refused_document_stops_the_sign_up_and_keeps_only_an_audit_line(tmp_path, mode, reading, reason, words, again):
    h = KycHarness(tmp_path, signup_kyc=mode)
    try:
        to_document(h, CAROL)
        h.photo(CAROL, reading)
        said = h.graph.texts(CAROL)[-1]
        assert words in said and "وگفنا فتح الحساب، وما انحفظ شي من الصورة." in said
        assert ("اكتبي «افتح حساب» وابدي من جديد" in said) is again
        assert h.row(CAROL) is None and h.user(CAROL) is None and h.held(CAROL) is None
        [line] = h.audit("kyc_checked")
        assert line["user_id"] is None and line["ref"] == h.channel.sender_key(CAROL)
        assert f'"reason": "{reason}"' in line["details_json"] and '"status": "refused"' in line["details_json"]
        for personal in (NUMBER, NUMBER[-4:], PRINTED, "زينب", "1995", "2012", CAROL, CAROL[-10:]):
            assert personal not in line["details_json"] and personal not in (line["ref"] or ""), personal
        with h.channel.db.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM kyc_signups").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM users WHERE kyc_level IS NOT NULL").fetchone()[0] == 0
        h.say(CAROL, "هلا")  # nothing blocks a fresh start
        assert h.row(CAROL)["step"] == "offer"
    finally:
        h.stop()


def test_specimens_are_accepted_only_where_the_demo_switch_is_on(tmp_path):
    h = KycHarness(tmp_path, kyc_allow_specimen=True)
    try:
        assert sign_up(h, CAROL, raw(marked_as_specimen=True))["kyc_level"] == "verified"
    finally:
        h.stop()


# ---------------------------------------------------------------- a photo at any other moment


def test_a_photo_outside_the_step_is_not_read(h):
    h.say(CAROL, "هلا")
    h.tap_id(CAROL, "o:open")
    h.photo(CAROL, raw())  # at the name step
    assert h.last(CAROL).startswith("أگدر أقرا صورة هوية بس وقت فتح الحساب أو وقت توثيقه.")
    assert "أسجلك باسم «tester»" in h.last(CAROL)  # and the open question again
    h.tap_id(CAROL, "o:name_other")
    h.say(CAROL, NAME)
    h.tap_id(CAROL, "o:f")
    h.tap_id(CAROL, "o:kyc_later")
    h.photo(CAROL, raw())  # at the PIN step
    assert "أگدر أقرا صورة هوية بس" in h.graph.texts(CAROL)[-1] and "اختاري رمز سري" in h.graph.texts(CAROL)[-1]
    user = set_pin(h, CAROL)
    assert user["kyc_level"] == "none"
    h.photo(CAROL, raw())  # from the account, with no verification open
    said = h.graph.texts(CAROL)[-1]
    assert said.startswith("أگدر أقرا صورة هوية بس وقت فتح الحساب أو وقت توثيقه.") and "«وثّق حسابي»" in said
    h.photo(ALICE, raw())  # an account from before the identity check
    assert h.graph.texts(ALICE)[-1] == "أگدر أقرا صورة هوية بس وقت فتح الحساب أو وقت توثيقه."
    assert h.reader.calls == 0 and h.audit("kyc_checked") == []


# ---------------------------------------------------------------- the limits of each level


def test_the_single_cap_is_a_hard_stop_with_its_number_and_shows_in_the_checks(h):
    user = sign_up(h, CAROL)  # skipped: level none
    fund(h, CAROL)
    h.say(CAROL, "دز ستين الف لـ 0770 000 0002")
    refused = h.graph.texts(CAROL)[-1]
    assert "أكثر مبلغ بالدفعة الوحدة لحسابج هسه 50,000 دينار" in refused and "ما انخصم منج شي" in refused
    assert not h.graph.cards(CAROL)[-1]["action"]["buttons"][0]["reply"]["id"].startswith("confirm:")  # no card
    assert h.transactions(CAROL, user["id"]) == []
    h.say(CAROL, "ليش")
    why = h.graph.texts(CAROL)[-1]
    assert "حد الدفعة الوحدة: 50,000 دينار حسب توثيق الحساب، وهالدفعة 60,000 دينار، تعبر الحد، فرفضت." in why
    h.say(CAROL, "دز خمسين الف لـ 0770 000 0002")  # exactly the cap passes
    card = h.graph.last_card(CAROL)
    assert "50,000" in card["body"]["text"] and card["action"]["buttons"][0]["reply"]["id"].startswith("confirm:")
    h.say(CAROL, "ليش")
    assert "حد الدفعة الوحدة: 50,000 دينار حسب توثيق الحساب، وهالدفعة 50,000 دينار، ضمن الحد." in h.graph.texts(CAROL)[-1]


@pytest.mark.parametrize("first, name, ask, refusal, why", [
    ("Hello there", "Zainab Hadi", "send 60000 to 0770 000 0002",
     "The most your account can send in one payment right now is 50,000 dinars",
     "Limit per payment: 50,000 dinars at this account's verification level, this payment 60,000 dinars. "
     "Over the limit, so I refused."),
    ("السلام عليكم، أود فتح حساب من فضلكم", NAME, "حوّل 60000 دينار إلى 0770 000 0002 من فضلك",
     "أقصى مبلغ في الدفعة الواحدة لحسابك الآن 50,000 دينار",
     "حد الدفعة الواحدة: 50,000 دينار بحسب توثيق الحساب، وهذه الدفعة 60,000 دينار، تتجاوز الحد، فرفضت."),
])
def test_the_cap_refusal_and_its_check_in_the_other_two_languages(h, first, name, ask, refusal, why):
    sign_up(h, CAROL, first=first, name=name)
    fund(h, CAROL)
    h.say(CAROL, ask)
    assert refusal in h.graph.texts(CAROL)[-1], h.graph.texts(CAROL)[-1]
    h.say(CAROL, "why" if first.startswith("Hello") else "لماذا")
    assert why in h.graph.texts(CAROL)[-1]
    assert h.transactions(CAROL) == []


def test_the_small_daily_limit_holds_and_verifying_later_lifts_both(h):
    user = sign_up(h, CAROL)
    fund(h, CAROL)
    for _ in range(3):  # 3 x 50,000 = the whole day at level none
        h.say(CAROL, "دز خمسين الف لـ 0770 000 0002")
        h.tap(CAROL, "إي، ادفع")
        h.say(CAROL, GOOD_PIN)
    assert h.balance(CAROL) == 400_000 - 3 * 50_250
    h.say(CAROL, "دز عشرة الاف لـ 0770 000 0002")
    assert "حدج اليومي" in h.graph.texts(CAROL)[-1] and "150,000" in h.graph.texts(CAROL)[-1]
    h.say(CAROL, "وثّق حسابي")
    h.photo(CAROL, raw())
    assert h.graph.texts(CAROL)[-1].startswith("تم توثيق هويتج. صار حدج اليومي 1,000,000 دينار")
    after = h.user(CAROL)
    assert after["kyc_level"] == "verified" and after["daily_limit"] == 1_000_000 and after["id"] == user["id"]
    h.say(CAROL, "دز ستين الف لـ 0770 000 0002")  # over the old single cap and the old day
    assert h.graph.last_card(CAROL)["action"]["buttons"][0]["reply"]["id"].startswith("confirm:")


def test_an_account_from_before_the_feature_keeps_its_limits(h):
    before = h.user(ALICE)
    assert before["kyc_level"] is None and before["daily_limit"] == 1_000_000
    h.say(ALICE, "دز ستين الف لهادي")
    assert h.graph.last_card(ALICE)["action"]["buttons"][0]["reply"]["id"].startswith("confirm:")
    h.say(ALICE, "ليش")
    assert "حد الدفعة الوحدة" not in h.graph.texts(ALICE)[-1]  # no such cap, so no such check
    h.say(ALICE, "وثّق حسابي")
    assert h.graph.texts(ALICE)[-1] == "حسابج ما يحتاج توثيق: حدوده كاملة."
    assert h.row(ALICE) is None and h.user(ALICE)["daily_limit"] == 1_000_000


# ---------------------------------------------------------------- verifying later


def test_verify_my_account_in_three_languages_and_from_the_my_account_button(h):
    sign_up(h, CAROL)
    for words, start in (("وثّق حسابي", "هسه توثيق الهوية: صوّري"), ("verify my account", "Now the identity check"),
                         ("أريد توثيق حسابي", "الآن توثيق الهوية")):
        h.say(CAROL, words)
        said = h.graph.texts(CAROL)[-1]
        assert said.startswith(start), said
        assert h.row(CAROL)["step"] == "verify"
    h.say(CAROL, "شكد رصيدي")  # an ordinary request in Iraqi: answered, and the photo is still awaited
    assert h.graph.texts(CAROL)[-1] == "رصيدج 0 دينار." and h.row(CAROL)["step"] == "verify"
    h.say(CAROL, "الغاء")
    assert h.graph.texts(CAROL)[-1] == "تمام، لغيت التوثيق. حدود حسابج مثل ما هي."
    assert h.row(CAROL) is None
    h.say(CAROL, "حسابي")
    account = h.graph.cards(CAROL)[-1]
    assert "التوثيق: غير موثّق (أكثر مبلغ بالدفعة الوحدة 50,000 دينار)" in account["body"]["text"]
    assert kit.card_buttons(account) == {"وثّق حسابي": "m:verify:start"}
    h.tap_id(CAROL, "m:verify:start")
    assert h.row(CAROL)["step"] == "verify" and "قارئ آلي مرة وحدة" in h.graph.texts(CAROL)[-1]
    h.photo(CAROL, raw())
    assert h.user(CAROL)["kyc_level"] == "verified" and h.row(CAROL) is None
    h.say(CAROL, "حسابي")
    assert "التوثيق: موثّق ✅" in h.graph.texts(CAROL)[-1]
    h.say(CAROL, "وثّق حسابي")
    assert h.graph.texts(CAROL)[-1].startswith("حسابج موثّق ✅ حدج اليومي 1,000,000 دينار")
    assert h.row(CAROL) is None and h.reader.calls == 1


def test_verify_my_account_said_in_a_voice_note_opens_the_same_step(tmp_path):
    class Hearing(StubCore):
        def transcribe(self, audio, mime, context):
            return "وثّق حسابي"

    h = KycHarness(tmp_path, core=Hearing(), voice_reply_mode="off")
    try:
        sign_up(h, CAROL)
        h.graph.media_files["v1"] = (b"OggS", "audio/ogg")
        h.send(kit.voice(CAROL, "v1", h.clock))
        assert h.row(CAROL)["step"] == "verify" and "صوّري وحدة من وثائقج" in h.graph.texts(CAROL)[-1]
    finally:
        h.stop()


def test_verifying_later_every_verdict(h):
    user = sign_up(h, CAROL)
    uid = user["id"]
    h.say(CAROL, "وثّق حسابي")
    h.photo(CAROL, raw(is_identity_document=False, document_type="none"))  # retry: the step stays open
    assert "هاي الصورة مو هوية" in h.graph.texts(CAROL)[-1] and "المحاولة 1 من 3." in h.graph.texts(CAROL)[-1]
    assert h.row(CAROL)["step"] == "verify" and h.user(CAROL)["kyc_level"] == "none"
    h.photo(CAROL, raw(expiry_date="2024-01-01"))  # refused: nothing changes, the step ends
    said = h.graph.texts(CAROL)[-1]
    assert "هويتج منتهية" in said and "حسابج وحدوده مثل ما هي، وما انحفظ شي من الصورة." in said
    assert h.row(CAROL) is None and h.user(CAROL)["kyc_level"] == "none" and h.user(CAROL)["kyc_name"] is None
    h.say(CAROL, "وثّق حسابي")
    h.photo(CAROL, raw(full_name_ar="سارة محمد علي الجبوري"))  # review
    assert "الاسم على الهوية يختلف" in h.graph.texts(CAROL)[-1]
    pending = h.user(CAROL)
    assert pending["kyc_level"] == "pending" and pending["kyc_reason"] == "name_mismatch" and pending["daily_limit"] == 150_000
    assert [r["user_id"] for r in h.channel.accounts.open_requests()] == [uid]
    h.say(CAROL, "وثّق حسابي")  # a pending account may send the document again
    h.photo(CAROL, raw())
    assert h.user(CAROL)["kyc_level"] == "verified" and h.channel.accounts.open_requests() == []
    kinds = [(a["user_id"], a["details_json"].split('"reason": "')[1].split('"')[0]) for a in h.audit("kyc_checked")]
    assert kinds == [(uid, "ok"), (uid, "name_mismatch"), (uid, "expired"), (uid, "not_a_document")]  # newest first


def test_three_bad_photos_while_verifying_later_go_to_a_person(h):
    sign_up(h, CAROL)
    h.say(CAROL, "وثّق حسابي")
    for _ in range(3):
        h.photo(CAROL, raw(readable=False))
    said = h.graph.texts(CAROL)[-1]
    assert "بعد 3 محاولات. موظف راح يراجع طلبج" in said
    assert h.user(CAROL)["kyc_level"] == "pending" and h.user(CAROL)["kyc_reason"] == "too_many_attempts"
    assert h.row(CAROL) is None


def test_the_step_survives_a_restart_and_an_open_card_keeps_its_cancel(h):
    to_document(h, CAROL)
    h.restart()
    assert h.row(CAROL)["step"] == "document"
    h.photo(CAROL, raw())
    assert set_pin(h, CAROL)["kyc_level"] == "verified"
    user = sign_up(h, DAN, name="علي حسن", gender="o:m")
    fund(h, DAN)
    h.say(DAN, "وثّق حسابي")
    h.restart()
    assert h.row(DAN)["step"] == "verify"
    h.say(DAN, "دز عشرين الف لـ 0770 000 0002")  # an ordinary request while the photo is awaited
    card = h.graph.last_card(DAN)
    h.send(kit.tap(DAN, kit.card_buttons(card)["لا، بطّلت"], "لا، بطّلت", h.clock))
    assert h.row(DAN)["step"] == "verify"  # the card was cancelled, not the verification
    h.photo(DAN, raw(full_name_ar="علي حسن جاسم العبيدي", document_number=OTHER_NUMBER, sex="m"))
    assert h.user(DAN)["kyc_level"] == "verified" and h.user(DAN)["id"] == user["id"]


def test_a_reading_is_refused_past_the_daily_number_of_photos(h):
    sign_up(h, CAROL)
    for _ in range(2):  # two rounds of three bad photos: six readings
        h.say(CAROL, "وثّق حسابي")
        for _ in range(3):
            h.photo(CAROL, raw(readable=False))
    assert h.reader.calls == 6
    h.say(CAROL, "وثّق حسابي")
    h.photo(CAROL, raw())
    assert "وصلتي حد محاولات التوثيق لليوم. جربي باجر." in h.graph.texts(CAROL)[-1]
    assert h.reader.calls == 6 and h.user(CAROL)["kyc_level"] == "pending"
    h.clock.advance(hours=25)
    h.say(CAROL, "وثّق حسابي")
    h.photo(CAROL, raw())
    assert h.user(CAROL)["kyc_level"] == "verified"


# ---------------------------------------------------------------- mode off


@pytest.mark.parametrize("overrides, reader, why", [
    ({"signup_kyc": "off"}, Reader(), None),
    ({"signup_kyc": "optional"}, False, "no usable vision route in the model layer (LLM_VISION)"),
    ({"signup_kyc": "optional", "kyc_pepper": ""}, Reader(), "KYC_PEPPER is missing or shorter than 16 bytes"),
    ({"signup_kyc": "optional", "kyc_pepper": "short"}, Reader(), "KYC_PEPPER is missing or shorter than 16 bytes"),
])
def test_without_the_setting_a_reader_or_a_pepper_sign_up_is_what_it_was(tmp_path, caplog, overrides, reader, why):
    caplog.set_level(logging.WARNING)
    h = KycHarness(tmp_path, reader=reader, **overrides)
    try:
        assert h.channel.kyc_mode == "off"
        said = [r.getMessage() for r in caplog.records if "identity document step is off" in r.getMessage()]
        assert said == ([f"SIGNUP_KYC={overrides['signup_kyc']} but {why}: the identity document step is off"] if why
                        else [])
        assert PEPPER not in caplog.text
        h.say(CAROL, "هلا")
        h.tap_id(CAROL, "o:open")
        h.tap_id(CAROL, "o:name_yes")  # one name word is fine again
        h.tap_id(CAROL, "o:f")
        assert h.row(CAROL)["step"] == "pin" and "اختاري رمز سري" in h.graph.texts(CAROL)[-1]
        h.photo(CAROL, raw())  # a photo is what it was: the open question again, nothing read
        assert h.graph.texts(CAROL)[-1].startswith("هسه اختاري رمز سري")
        user = set_pin(h, CAROL)
        assert user["kyc_level"] is None and user["daily_limit"] == 1_000_000 and user["kyc_checked_at"] is None
        h.photo(CAROL, raw())
        assert h.graph.texts(CAROL)[-1].startswith("وصلتني صورة، بس أنا أفهم الكتابة")  # the old words
        fund(h, CAROL)
        h.say(CAROL, "دز ستين الف لـ 0770 000 0002")  # no single cap
        assert h.graph.last_card(CAROL)["action"]["buttons"][0]["reply"]["id"].startswith("confirm:")
        assert (reader.calls if reader else 0) == 0
        assert h.audit("kyc_checked") == [] and h.audit("kyc_level_set") == []
        health = h.client.get("/healthz").json()
        assert health["missing"] == (["KYC_PEPPER"] if why and "PEPPER" in why else [])
    finally:
        h.stop()


def test_an_invited_person_and_a_pin_reset_never_see_the_document_step(h):
    h.channel.wallet.invite(CAROL, "زينب علي", gender="f", opening=150_000, daily_limit=700_000, op_id="op-open",
                            actor="admin:test")
    h.say(CAROL, "هلا")
    user = set_pin(h, CAROL)
    assert user["kyc_level"] is None and user["daily_limit"] == 700_000  # the admin's own limit stays
    verified = sign_up(h, DAN, raw())
    h.channel.wallet.reset_pin(verified["id"], actor="admin:test", reason="asked by phone")
    h.say(DAN, "هلا")
    assert h.row(DAN)["step"] == "pin"
    again = set_pin(h, DAN, "7391")
    assert again["kyc_level"] == "verified" and again["daily_limit"] == 1_000_000 and h.reader.calls == 1


def test_a_verification_left_open_does_not_confuse_a_later_pin_reset(h):
    user = sign_up(h, CAROL)
    h.say(CAROL, "وثّق حسابي")
    assert h.row(CAROL)["step"] == "verify"
    h.channel.wallet.reset_pin(user["id"], actor="admin:test", reason="asked by phone")
    h.say(CAROL, "هلا")
    assert h.row(CAROL)["step"] == "pin"
    assert set_pin(h, CAROL, "7391")["kyc_level"] == "none"


# ---------------------------------------------------------------- the settings


def test_settings_read_the_mode_and_never_show_the_pepper():
    env = {"SIGNUP_KYC": "Required", "KYC_PEPPER": PEPPER, "KYC_ALLOW_SPECIMEN": "1"}
    settings = Settings.from_env(env)
    assert settings.signup_kyc == "required" and settings.kyc_pepper_ok and settings.kyc_allow_specimen
    assert PEPPER not in repr(settings)
    assert settings.kyc_mode(True) == "required" and settings.kyc_mode(False) == "off"
    plain = Settings.from_env({})
    assert plain.signup_kyc == "optional" and not plain.kyc_pepper_ok and plain.kyc_mode(True) == "off"
    assert not plain.kyc_allow_specimen
    assert Settings.from_env({"SIGNUP_KYC": "off", "KYC_PEPPER": PEPPER}).kyc_mode(True) == "off"
    with pytest.raises(ValueError, match="SIGNUP_KYC"):
        Settings.from_env({"SIGNUP_KYC": "sometimes"})


def test_a_run_with_a_fake_graph_never_builds_a_reader_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_VISION", "ollama:some-vision-model")
    harness = Harness(tmp_path, signup_kyc="required", kyc_pepper=PEPPER)
    try:
        assert harness.channel.kyc_reader is None and harness.channel.kyc_mode == "off"
    finally:
        harness.stop()


def test_the_reader_of_a_real_run_comes_from_the_vision_role(monkeypatch):
    from channels.whatsapp import app as app_module

    settings = Settings(signup_kyc="optional", kyc_pepper=PEPPER)
    for name in ("LLM_VISION", "LLM_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    assert app_module._kyc_reader(settings) == (None, None)  # no vision route: the step will be off
    monkeypatch.setenv("LLM_VISION", "ollama:some-vision-model")  # a local route needs no key
    reader, llm = app_module._kyc_reader(settings)
    try:
        assert callable(reader) and llm.has("vision")
    finally:
        llm.close()
    assert app_module._kyc_reader(Settings(signup_kyc="off")) == (None, None)


# ---------------------------------------------------------------- through the model layer, and the budget


def test_the_photo_goes_through_the_model_layer_and_its_cost_counts(tmp_path):
    from llm.testing import chat, fake_llm

    seen: list[dict] = []

    def answer(body, request):
        seen.append(body)
        return chat(raw(), tokens_in=900, tokens_out=120, cost=0.004)

    llm = fake_llm(answer)
    h = KycHarness(tmp_path, reader=kyc.reader_from(llm), kyc_llm=llm, daily_budget_usd=5.0)
    try:
        to_document(h, CAROL)
        data = h.photo(CAROL, raw())
        assert set_pin(h, CAROL)["kyc_level"] == "verified"
        assert len(seen) == 1 and "image_url" in str(seen[0])  # one call, with the photo
        assert h.channel.store.spent(h.channel._day()) == pytest.approx(0.004)
        assert data not in str(h.graph.sent).encode() and h.graph.uploads == []
    finally:
        h.stop()


def test_no_reading_once_the_daily_budget_is_spent(tmp_path):
    h = KycHarness(tmp_path, daily_budget_usd=1.0)
    try:
        to_document(h, CAROL)
        h.channel.store.add_spend(h.channel._day(), 1.5)
        h.photo(CAROL, raw())
        assert "ما گدرنا نقرا الصورة هسه" in h.last(CAROL) and h.reader.calls == 0
        assert h.row(CAROL)["step"] == "document" and h.row(CAROL)["tries"] == 0
    finally:
        h.stop()


# ---------------------------------------------------------------- nothing of the photo stays


def _files(folder) -> dict[str, bytes]:
    return {str(path): path.read_bytes() for path in folder.rglob("*") if path.is_file()}


def leaks(folder, photos: list[bytes], secrets: list[str]) -> list[tuple[str, str]]:
    """Every (file, what) where a file under `folder` holds a photo's bytes (whole, or its first 64) or one of
    `secrets` (a whole document number, a whole birth date, the pepper) as UTF-8 or UTF-16 text."""
    found = []
    for name, content in _files(folder).items():
        for index, data in enumerate(photos):
            if data in content or data[:64] in content:
                found.append((name, f"photo {index}"))
        for secret in secrets:
            if secret.encode() in content or secret.encode("utf-16-le") in content:
                found.append((name, secret))
    return found


def test_the_scan_for_leaks_finds_a_photo_or_a_number_wherever_it_is_put(tmp_path):
    photo = JPEG + b"PHOTO-BYTES-0001-" * 40
    (tmp_path / "whatsapp").mkdir()
    (tmp_path / "whatsapp" / "cache.bin").write_bytes(b"junk" + photo + b"junk")
    (tmp_path / "half.tmp").write_bytes(photo[:64])
    with sqlite3.connect(tmp_path / "side.db") as conn:
        conn.execute("CREATE TABLE notes (body TEXT)")
        conn.execute("INSERT INTO notes VALUES (?)", (f"number {NUMBER} read",))
    conn.close()
    (tmp_path / "log.txt").write_text(f"read {OTHER_NUMBER}", encoding="utf-16-le")
    found = leaks(tmp_path, [photo], [NUMBER, OTHER_NUMBER])
    assert {(name.replace("\\", "/").rsplit("/", 1)[-1], what) for name, what in found} == {
        ("cache.bin", "photo 0"), ("half.tmp", "photo 0"), ("side.db", NUMBER), ("log.txt", OTHER_NUMBER)}


def test_no_image_bytes_and_no_whole_number_anywhere_on_disk_in_logs_or_in_what_was_sent(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    h = KycHarness(tmp_path, manager=BOB)
    photos: list[bytes] = []
    try:
        # a verified sign-up, a review, a refusal, three bad photos, a skip then a later verification
        to_document(h, CAROL)
        photos.append(h.photo(CAROL, raw(is_identity_document=False, document_type="none")))
        photos.append(h.photo(CAROL, raw()))
        set_pin(h, CAROL)
        to_document(h, DAN, name="علي حسن", gender="o:m")
        photos.append(h.photo(DAN, raw(document_number=OTHER_NUMBER, full_name_ar="علي حسن جاسم العبيدي", sex="m",
                                       looks_like_photo_of_screen_or_copy=True)))
        set_pin(h, DAN)
        eve = "9647700000005"
        to_document(h, eve)
        photos.append(h.photo(eve, raw(document_number="300011112222", birth_date="2011-01-01")))
        frank = "9647700000006"
        sign_up(h, frank)
        h.say(frank, "وثّق حسابي")
        photos.append(h.photo(frank, raw(document_number="400033334444")))
        assert h.user(frank)["kyc_level"] == "verified"
        h.channel.tick()
        h.restart()  # everything the service remembers is on disk now
        with sqlite3.connect(h.channel.settings.accounts_path) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        h.stop()
    files = _files(tmp_path)
    assert any(name.endswith("accounts.db") for name in files) and any(name.endswith("channel.db") for name in files)
    assert len(photos) == 5 and h.reader.calls == 5  # every photo was read, each one once
    numbers = (NUMBER, OTHER_NUMBER, "300011112222", "400033334444")
    # no photo, no whole number, no whole birth date, no pepper: in any file, databases and their logs included
    assert leaks(tmp_path, photos, [*numbers, "2011-01-01", "1995-04-12", PEPPER, "PHOTO-BYTES-"]) == []
    logged = caplog.text
    assert "PHOTO-BYTES-" not in logged and PRINTED not in logged and PEPPER not in logged
    assert not any(number in logged for number in numbers)
    sent = str(h.graph.sent)
    assert "PHOTO-BYTES-" not in sent and not any(number in sent for number in numbers)
    assert h.graph.uploads == []
    stored = files[next(name for name in files if name.endswith("accounts.db"))]
    assert NUMBER[-4:].encode() in stored and PRINTED.encode() in stored  # what may be kept, is


def test_the_second_provider_reads_a_document_the_same_way(tmp_path):
    """The step does not depend on the transport: whatever Graph.media() returns is what is read."""
    h = KycHarness(tmp_path)
    try:
        to_document(h, CAROL)
        data = JPEG + b"LINKED-PHOTO" * 20
        h.graph.media_files["https://example.invalid/media/1"] = (data, "")  # no mime from the lookup: the webhook's
        h.reader.answers[data] = raw()
        h.send(kit.image(CAROL, "https://example.invalid/media/1", h.clock, mime="image/jpeg"))
        assert h.row(CAROL)["step"] == "pin"
    finally:
        h.stop()
