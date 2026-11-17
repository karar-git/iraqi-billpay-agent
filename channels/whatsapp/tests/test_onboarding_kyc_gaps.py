"""The identity step's gaps found in review, each closed and checked end to end on the WhatsApp channel.

Offline, like test_kyc_signup: a fake Graph API serves the photo bytes, a fake reader answers for the vision model,
the clock is moved by hand. Every name, number and document is made up. What is checked here:
- the back of a national card: asked for when the front prints no birth date, decided with the front as one card,
  its front held in memory only (never on disk), asked again after a restart, two different cards start over;
- a reader that gives nothing (the vision call failed or timed out) costs no try, but still counts per day;
- a file that is not a photo at the step costs no try and is never read;
- a document that reads under 18: no «Later» around it at a fresh sign-up; on an account, a person looks;
- the admin can only reject a review with no document read or with an age under 18;
- SIGNUP_KYC=required with no reader never opens an account with the full limits;
- «Later» stays offered when the day's photos are used up.
"""

from __future__ import annotations

import sqlite3

import pytest

import kyc
from channels.whatsapp.graph import parse
from channels.whatsapp.onboarding import bare_record
from channels.whatsapp.tests import kit
from channels.whatsapp.tests.kit import BOB
from channels.whatsapp.tests.test_admin import PASSWORD, form_of, get, post
from channels.whatsapp.tests.test_kyc_signup import (CAROL, DAN, NUMBER, OTHER_NUMBER, PEPPER, KycHarness, _files, fund,
                                                     leaks, raw, set_pin, sign_up, to_document)

FRONT = raw(birth_date=None, expiry_date=None)  # the front of a unified national card: no dates on it
BACK = raw(full_name_ar=None, full_name_latin="ZAINAB HADI", document_number=NUMBER,
           birth_date="1995-04-12", expiry_date="2031-01-01", sex=None)  # its back: the dates, the same number


@pytest.fixture
def h(tmp_path):
    harness = KycHarness(tmp_path, manager=BOB, admin_password=PASSWORD)
    yield harness
    harness.stop()


def buttons(h: KycHarness, who: str) -> list[str]:
    payload = h.graph.to(who)[-1]
    return list(kit.card_buttons(payload["interactive"])) if payload["type"] == "interactive" else []


# ---------------------------------------------------------------- the back of a national card


@pytest.mark.parametrize("mode", ["optional", "required"])
def test_a_card_front_with_no_birth_date_asks_for_the_back_and_the_two_open_the_account_verified(tmp_path, mode):
    h = KycHarness(tmp_path, signup_kyc=mode)
    try:
        to_document(h, CAROL)
        h.photo(CAROL, FRONT)
        said = h.last(CAROL)
        assert "قرينا وجه البطاقة" in said and "اقلبي البطاقة، صوّري الوجه الثاني" in said
        assert "المحاولة" not in said  # no try was lost
        assert (buttons(h, CAROL) == ["بعدين"]) is (mode == "optional")
        assert (h.row(CAROL)["step"], h.row(CAROL)["tries"]) == ("document_back", 0) and h.held(CAROL) is None
        h.photo(CAROL, BACK)
        assert h.row(CAROL)["step"] == "pin" and h.reader.calls == 2
        assert h.last(CAROL).startswith("تم توثيق هويتج")
        user = set_pin(h, CAROL)
        assert (user["kyc_level"], user["kyc_reason"], user["kyc_birth_year"]) == ("verified", "ok", 1995)
        assert user["kyc_number_last4"] == NUMBER[-4:] and user["kyc_document_type"] == "national_card"
        assert user["kyc_name"] == "زينب هادي كريم السعدي"  # the front's name, never the back's Latin line
        assert h.channel.onboarding._fronts == {}  # the front's reading is gone once decided
        [front_line, back_line] = [a["details_json"] for a in h.audit("kyc_checked")][::-1]
        assert '"status": "back"' in front_line and '"reason": "back_needed"' in front_line
        assert '"status": "verified"' in back_line and '"back": "ok"' in back_line
    finally:
        h.stop()


def test_the_front_waits_in_memory_only_and_a_restart_asks_for_it_again(h):
    to_document(h, CAROL)
    front = h.photo(CAROL, FRONT)
    assert h.channel.onboarding._fronts  # held here, and nowhere else
    with sqlite3.connect(h.channel.settings.accounts_path) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    assert leaks(h.channel.root.parent, [front], [NUMBER, PEPPER, "PHOTO-BYTES-"]) == []
    h.restart()
    assert h.row(CAROL)["step"] == "document_back"  # the step survives, its front does not
    h.photo(CAROL, BACK)
    said = h.last(CAROL)
    assert "ما بقت عدنا قراءة وجه البطاقة" in said and "ما راحت عليج محاولة" in said and "صوّري وحدة من وثائقج" in said
    assert (h.row(CAROL)["step"], h.row(CAROL)["tries"]) == ("document", 0) and h.reader.calls == 1  # the back unread
    h.photo(CAROL, FRONT)
    h.photo(CAROL, BACK)
    assert set_pin(h, CAROL)["kyc_level"] == "verified"


def test_a_front_older_than_its_time_is_asked_again(h):
    to_document(h, CAROL)
    h.photo(CAROL, FRONT)
    h.clock.advance(hours=1)
    h.photo(CAROL, BACK)
    assert "ما بقت عدنا قراءة وجه البطاقة" in h.last(CAROL) and h.row(CAROL)["step"] == "document"


def test_two_different_cards_start_again_from_the_front(h):
    to_document(h, CAROL)
    h.photo(CAROL, FRONT)
    h.photo(CAROL, {**BACK, "document_number": OTHER_NUMBER})
    said = h.last(CAROL)
    assert "الوجهين مو من نفس البطاقة" in said and "المحاولة 1 من 3." in said and "صوّري وحدة من وثائقج" in said
    assert (h.row(CAROL)["step"], h.row(CAROL)["tries"]) == ("document", 1) and h.channel.onboarding._fronts == {}


def test_a_back_that_cannot_be_read_is_asked_again_and_keeps_its_front(h):
    to_document(h, CAROL)
    h.photo(CAROL, FRONT)
    h.photo(CAROL, {**BACK, "birth_date": None})
    said = h.last(CAROL)
    assert "تاريخ الميلاد ما انقرا" in said and "المحاولة 1 من 3." in said and "اقلبي البطاقة" in said
    assert (h.row(CAROL)["step"], h.row(CAROL)["tries"]) == ("document_back", 1)
    h.photo(CAROL, BACK)
    assert set_pin(h, CAROL)["kyc_level"] == "verified"


def test_later_a_text_and_cancel_at_the_back_step(h):
    to_document(h, CAROL)
    h.photo(CAROL, FRONT)
    h.say(CAROL, "شنو اسوي؟")
    assert "اقلبي البطاقة" in h.last(CAROL) and h.row(CAROL)["step"] == "document_back"
    h.say(CAROL, "الغاء")
    assert h.row(CAROL) is None and h.channel.onboarding._fronts == {}
    to_document(h, DAN)
    h.photo(DAN, FRONT)
    h.tap_id(DAN, "o:kyc_later")
    assert h.row(DAN)["step"] == "pin" and h.channel.onboarding._fronts == {}
    user = set_pin(h, DAN)
    assert (user["kyc_level"], user["kyc_reason"], user["kyc_number_last4"]) == ("none", "skipped", None)


def test_verifying_later_reads_the_back_too(h):
    sign_up(h, CAROL)
    h.say(CAROL, "وثّق حسابي")
    h.photo(CAROL, FRONT)
    said = h.graph.texts(CAROL)[-1]
    assert "اقلبي البطاقة" in said and "«الغاء»" in said and h.row(CAROL)["step"] == "verify_back"
    h.photo(CAROL, BACK)
    assert h.graph.texts(CAROL)[-1].startswith("تم توثيق هويتج") and h.row(CAROL) is None
    user = h.user(CAROL)
    assert (user["kyc_level"], user["daily_limit"], user["kyc_birth_year"]) == ("verified", 1_000_000, 1995)


def test_verifying_later_a_lost_front_and_cancel_at_the_back(h):
    sign_up(h, CAROL)
    h.say(CAROL, "وثّق حسابي")
    h.photo(CAROL, FRONT)
    h.channel.onboarding._fronts.clear()  # as after a restart
    h.photo(CAROL, BACK)
    assert "ما بقت عدنا قراءة وجه البطاقة" in h.graph.texts(CAROL)[-1] and h.row(CAROL)["step"] == "verify"
    h.photo(CAROL, FRONT)
    h.say(CAROL, "الغاء")
    assert h.row(CAROL) is None and h.channel.onboarding._fronts == {} and h.user(CAROL)["kyc_level"] == "none"


# ---------------------------------------------------------------- a reader that gives nothing


def test_a_failed_reading_costs_no_try_and_is_still_counted_per_day(h):
    to_document(h, CAROL)
    h.photo(CAROL, None)  # the vision call failed or timed out
    said = h.last(CAROL)
    assert "ما گدرنا نقرا الصورة هسه" in said and "المحاولة" not in said and "صوّري وحدة من وثائقج" in said
    assert (h.row(CAROL)["step"], h.row(CAROL)["tries"]) == ("document", 0)
    [line] = h.audit("kyc_checked")
    assert '"status": "later"' in line["details_json"] and '"reason": "no_reading"' in line["details_json"]
    for _ in range(5):
        h.photo(CAROL, None)
    assert h.reader.calls == 6 and h.row(CAROL)["tries"] == 0
    h.photo(CAROL, raw())  # an outage cannot loop on the model: the day's reads are used up
    assert "وصلتي حد محاولات التوثيق لليوم" in h.last(CAROL) and h.reader.calls == 6
    assert buttons(h, CAROL) == ["بعدين"]  # «Later» is still there in optional mode
    h.tap_id(CAROL, "o:kyc_later")
    assert set_pin(h, CAROL)["kyc_level"] == "none"


def test_a_failed_reading_of_the_back_keeps_the_front(h):
    to_document(h, CAROL)
    h.photo(CAROL, FRONT)
    h.photo(CAROL, None)
    assert "ما گدرنا نقرا الصورة هسه" in h.last(CAROL) and "اقلبي البطاقة" in h.last(CAROL)
    assert (h.row(CAROL)["step"], h.row(CAROL)["tries"]) == ("document_back", 0)
    h.photo(CAROL, BACK)
    assert set_pin(h, CAROL)["kyc_level"] == "verified"


def test_a_file_that_is_not_a_photo_costs_no_try_and_is_not_read(h):
    to_document(h, CAROL)
    h.graph.media_files["doc1"] = (b"PK\x03\x04 a zipped scan", "application/zip")
    h.send(kit.document(CAROL, "doc1", h.clock, mime="application/zip", filename="card.zip"))
    said = h.last(CAROL)
    assert "هالملف ما نگدر نفتحه" in said and "صوّري وحدة من وثائقج" in said
    assert h.row(CAROL)["tries"] == 0 and h.reader.calls == 0 and h.audit("kyc_checked") == []


def test_verify_file_answers_a_file_while_verifying(h):
    user = sign_up(h, CAROL)
    h.say(CAROL, "وثّق حسابي")
    row = h.row(CAROL)
    item = kit.document(CAROL, "doc2", h.clock, mime="application/zip")
    [inbound] = parse(kit.webhook(item), kit.PNID)
    h.channel.onboarding.verify_file(h.channel.sender_key(CAROL), inbound, row, "iq", "f")
    said = h.graph.texts(CAROL)[-1]
    assert "هالملف ما نگدر نفتحه" in said and "صوّري وحدة من وثائقج" in said and "«الغاء»" in said
    assert h.reader.calls == 0 and h.user(CAROL)["id"] == user["id"]


# ---------------------------------------------------------------- under 18


def test_after_an_under_age_document_a_fresh_sign_up_offers_no_later(h):
    to_document(h, CAROL)
    h.photo(CAROL, raw(birth_date="2012-05-01"))
    assert h.row(CAROL) is None and h.user(CAROL) is None
    to_document(h, CAROL)
    said = h.last(CAROL)
    assert "هالخطوة لازمة حتى ينفتح الحساب" in said and buttons(h, CAROL) == []
    h.tap_id(CAROL, "o:kyc_later")  # an old button, or the word typed: no account around the answer
    h.say(CAROL, "بعدين")
    assert h.row(CAROL)["step"] == "document" and h.user(CAROL) is None
    h.photo(CAROL, raw(is_identity_document=False, document_type="none"))  # another photo does not clear it
    assert buttons(h, CAROL) == []
    h.photo(CAROL, raw())  # a document that passes does
    assert set_pin(h, CAROL)["kyc_level"] == "verified"
    to_document(h, DAN)  # another sender is not touched
    assert buttons(h, DAN) == ["بعدين"]


def test_an_account_whose_document_reads_under_18_goes_to_a_person_who_can_only_reject(h):
    user = sign_up(h, CAROL)
    h.say(CAROL, "وثّق حسابي")
    h.photo(CAROL, raw(birth_date="2012-05-01"))
    said = h.graph.texts(CAROL)[-1]
    assert "الخدمة للي عمرهم 18 سنة وفوق" in said and "موظف راح يراجع حسابج" in said
    assert "الدفع من حسابج موقوف" in said and "تبقى حدودج الصغيرة" not in said
    after = h.user(CAROL)
    assert (after["kyc_level"], after["kyc_reason"], after["status"]) == ("pending", "under_age", "frozen")
    # only what the review needs: the birth year and the fingerprint, never the printed name or the last 4
    assert after["kyc_birth_year"] == 2012 and after["kyc_number_fingerprint"]
    assert (after["kyc_name"], after["kyc_number_last4"]) == (None, None)
    assert any("توثيق ينتظر مراجعة" in t for t in h.graph.texts(BOB))
    [request] = h.channel.accounts.open_requests()
    assert (request["reason"], request["approvable"]) == ("under_age", False)
    page = get(h, f"/admin/users/{user['id']}").text
    assert f"/admin/users/{user['id']}/kyc/reject" in page and f"/admin/users/{user['id']}/kyc/approve" not in page
    assert "Approve is not offered" in page
    requests_page = get(h, "/admin/requests").text
    assert f"/kyc/reject" in requests_page and f"/kyc/approve" not in requests_page
    assert h.channel.accounts.kyc_decide(user["id"], True, actor="admin:test", reason="looks older") is False
    assert h.user(CAROL)["kyc_level"] == "pending"
    assert h.channel.accounts.kyc_decide(user["id"], False, actor="admin:test", reason="under 18") is True
    rejected = h.user(CAROL)
    assert (rejected["kyc_level"], rejected["kyc_reason"], rejected["status"]) == ("none", "under_age", "frozen")


def test_a_review_with_no_document_read_offers_only_reject(h):
    to_document(h, CAROL)
    for _ in range(3):
        h.photo(CAROL, raw(readable=False))
    user = set_pin(h, CAROL)
    assert (user["kyc_level"], user["kyc_reason"], user["kyc_document_type"]) == ("pending", "too_many_attempts", None)
    page = get(h, f"/admin/users/{user['id']}").text
    assert "/kyc/approve" not in page and "/kyc/reject" in page and "Approve is not offered" in page
    assert h.channel.accounts.kyc_decide(user["id"], True, actor="admin:test", reason="seen in person") is False


def test_an_approve_posted_anyway_for_a_review_with_no_document_is_refused(h):
    user = sign_up(h, CAROL, raw(looks_like_photo_of_screen_or_copy=True))  # a review that could be approved
    path = f"/admin/users/{user['id']}/kyc/approve"
    fields = form_of(get(h, f"/admin/users/{user['id']}").text, path)  # a valid token for the approve form
    h.channel.accounts.kyc_apply(user["id"], bare_record(h.channel, "pending", "too_many_attempts"))  # since then
    response = post(h, path, {**fields, "reason": "seen the card in person"})
    assert response.status_code == 303 and "not_approvable" in response.headers["location"]
    assert h.user(CAROL)["kyc_level"] == "pending" and h.audit("kyc_approved") == []


# ---------------------------------------------------------------- required, with no reader


def test_required_with_no_reader_opens_accounts_with_the_small_limits_only(tmp_path):
    h = KycHarness(tmp_path, reader=False, signup_kyc="required")
    try:
        assert h.channel.kyc_mode == "off"
        h.say(CAROL, "هلا")
        h.tap_id(CAROL, "o:open")
        h.tap_id(CAROL, "o:name_yes")
        h.tap_id(CAROL, "o:f")
        assert h.row(CAROL)["step"] == "pin"  # the step cannot run
        user = set_pin(h, CAROL)
        assert (user["kyc_level"], user["kyc_reason"], user["daily_limit"]) == ("none", "unavailable", 150_000)
        assert "بالحدود الصغيرة: 50,000 دينار بالدفعة الوحدة" in h.graph.texts(CAROL)[-1]
        fund(h, CAROL)
        h.say(CAROL, "دز ستين الف لـ 0770 000 0002")
        assert "أكثر مبلغ بالدفعة الوحدة لحسابج هسه 50,000 دينار" in h.graph.texts(CAROL)[-1]
        h.say(CAROL, "وثّق حسابي")
        assert "توثيق الهوية مو متوفر هسه" in h.graph.texts(CAROL)[-1]
    finally:
        h.stop()


def test_required_without_a_pepper_also_gives_the_small_limits(tmp_path):
    h = KycHarness(tmp_path, signup_kyc="required", kyc_pepper="")
    try:
        h.say(CAROL, "هلا")
        h.tap_id(CAROL, "o:open")
        h.tap_id(CAROL, "o:name_yes")
        h.tap_id(CAROL, "o:f")
        user = set_pin(h, CAROL)
        assert (user["kyc_level"], user["daily_limit"], user["kyc_number_fingerprint"]) == ("none", 150_000, None)
    finally:
        h.stop()


def test_nothing_of_two_sides_is_on_disk_afterwards(tmp_path):
    h = KycHarness(tmp_path)
    photos = []
    try:
        to_document(h, CAROL)
        photos.append(h.photo(CAROL, FRONT))
        photos.append(h.photo(CAROL, BACK))
        set_pin(h, CAROL)
        h.restart()
        with sqlite3.connect(h.channel.settings.accounts_path) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    finally:
        h.stop()
    assert leaks(tmp_path, photos, [NUMBER, "A12345678", "1995-04-12", "2031-01-01", PEPPER, "PHOTO-BYTES-"]) == []
    stored = b"".join(content for name, content in _files(tmp_path).items() if name.endswith("accounts.db"))
    assert NUMBER[-4:].encode() in stored
    assert kyc.limits_for("verified")["daily_limit"] == 1_000_000
