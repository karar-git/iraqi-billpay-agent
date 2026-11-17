"""The identity step's holes found in the second review, each closed and checked end to end on the WhatsApp channel.

Offline, like test_kyc_signup: a fake Graph API serves the photo bytes, a fake reader answers for the vision model,
the clock is moved by hand. Every name, number and document is made up. What is checked here:
- a card's back verifies only when it carries the front's number: a licence, a stranger's back or a back whose
  number was not read never makes a minor's front an adult account;
- a document is matched against the name typed at sign-up, never against one the person renamed the account to;
- after an under-18 answer, three bad photos or an untied back stop the sign-up instead of opening an account;
- an account whose document reads under 18 is frozen at once, keeps that answer until an admin unfreezes it, and
  keeps no printed name or last 4;
- the front of a card is dropped from memory on time, whoever writes next, and a failed back does not extend it;
- the end of the tries is said without a «start again» line in front of it.
"""

from __future__ import annotations

import pytest

from channels.whatsapp.tests import kit
from channels.whatsapp.tests.kit import BOB
from channels.whatsapp.tests.test_admin import PASSWORD, get
from channels.whatsapp.tests.test_kyc_signup import (CAROL, DAN, NUMBER, OTHER_NUMBER, KycHarness, fund, raw, set_pin,
                                                     sign_up, to_document)

FRONT = raw(birth_date=None, expiry_date=None)  # the front of a unified national card: no dates on it
BACK = raw(full_name_ar=None, full_name_latin="ZAINAB HADI", document_number=NUMBER,
           birth_date="1995-04-12", expiry_date="2031-01-01", sex=None)  # its back, with the same national number
# The back of a man born in 1970 (only the card's serial read), and his driving licence.
ADULT_BACK = raw(full_name_ar=None, full_name_latin="AHMED JASIM", document_number="B98765432",
                 birth_date="1970-01-01", expiry_date="2031-01-01", sex="m")
LICENCE = raw(document_type="driving_licence", full_name_ar="أحمد جاسم محمد", document_number="L1234567",
              birth_date="1970-01-01", expiry_date="2031-01-01", sex="m")
BORROWED = raw(full_name_ar="حسين علي جاسم", document_number=OTHER_NUMBER, birth_date="1970-01-01", sex="m")


@pytest.fixture
def h(tmp_path):
    harness = KycHarness(tmp_path, manager=BOB, admin_password=PASSWORD)
    yield harness
    harness.stop()


def buttons(h: KycHarness, who: str) -> list[str]:
    payload = h.graph.to(who)[-1]
    return list(kit.card_buttons(payload["interactive"])) if payload["type"] == "interactive" else []


# ---------------------------------------------------------------- the back must be the same card


def test_a_driving_licence_as_the_back_is_two_different_documents(h):
    to_document(h, CAROL)
    h.photo(CAROL, FRONT)
    h.photo(CAROL, LICENCE)
    assert "الوجهين مو من نفس البطاقة" in h.last(CAROL)
    assert (h.row(CAROL)["step"], h.row(CAROL)["tries"]) == ("document", 1) and h.held(CAROL) is None


def test_an_adults_back_with_no_number_never_makes_an_adult_account(h):
    to_document(h, CAROL)
    h.photo(CAROL, FRONT)
    h.photo(CAROL, ADULT_BACK)
    assert "ما گدرنا نتأكد إن الظهر من نفس البطاقة" in h.last(CAROL) and h.row(CAROL)["step"] == "pin"
    user = set_pin(h, CAROL)
    assert (user["kyc_level"], user["kyc_reason"], user["daily_limit"]) == ("pending", "sides_unconfirmed", 150_000)
    assert user["kyc_birth_year"] is None  # the 1970 came from a photo that may be someone else's
    [request] = h.channel.accounts.open_requests()
    assert (request["reason"], request["approvable"]) == ("sides_unconfirmed", False)
    assert h.channel.accounts.kyc_decide(user["id"], True, actor="admin:test", reason="looks fine") is False


def test_verifying_later_with_an_adults_back_does_not_verify(h):
    sign_up(h, CAROL)
    h.say(CAROL, "وثّق حسابي")
    h.photo(CAROL, FRONT)
    h.photo(CAROL, {**ADULT_BACK, "document_number": None})
    user = h.user(CAROL)
    assert (user["kyc_level"], user["kyc_reason"], user["daily_limit"]) == ("pending", "sides_unconfirmed", 150_000)


# ---------------------------------------------------------------- the name a document is matched against


def test_a_rename_by_the_person_does_not_move_the_name_a_document_is_matched_against(h):
    user = sign_up(h, CAROL)  # typed «زينب هادي» at sign-up, then «Later»
    h.channel.accounts.rename_user(user["id"], "حسين علي", f"user:{user['id']}")  # «غير اسمي» → «حسين علي»
    assert h.channel.accounts.kyc_match_name(user["id"]) == "زينب هادي"
    h.say(CAROL, "وثّق حسابي")
    h.photo(CAROL, BORROWED)  # a card printed «حسين علي جاسم», someone else's
    after = h.user(CAROL)
    assert (after["kyc_level"], after["kyc_reason"], after["daily_limit"]) == ("pending", "name_mismatch", 150_000)
    page = get(h, f"/admin/users/{user['id']}").text
    assert "Name matched against" in page and "زينب هادي" in page
    assert "Current name (renamed by the person)" in page and "حسين علي" in page


def test_a_rename_through_the_chat_is_not_matched_either(h):
    user = sign_up(h, CAROL)
    h.say(CAROL, "غير اسمي")
    h.say(CAROL, "حسين علي")
    assert h.user(CAROL)["name"] == "حسين علي"
    assert h.channel.accounts.kyc_match_name(user["id"]) == "زينب هادي"


def test_an_admins_rename_is_the_name_from_then_on(h):
    user = sign_up(h, CAROL)
    uid = user["id"]
    h.channel.accounts.rename_user(uid, "زينب كريم", f"user:{uid}")
    h.channel.accounts.update_profile(uid, name="حسين علي", actor="admin:test")  # the admin corrected it
    assert h.channel.accounts.kyc_match_name(uid) == "حسين علي"
    h.channel.accounts.rename_user(uid, "علي حسين", f"user:{uid}")  # and the person again: not followed
    assert h.channel.accounts.kyc_match_name(uid) == "حسين علي"


# ---------------------------------------------------------------- after an under-18 answer


def test_three_bad_photos_after_an_under_age_answer_stop_the_sign_up(h):
    to_document(h, CAROL)
    h.photo(CAROL, raw(birth_date="2012-05-01"))
    assert h.row(CAROL) is None
    to_document(h, CAROL)
    for _ in range(3):
        h.photo(CAROL, raw(readable=False))
    said = h.last(CAROL)
    assert "ما گدرنا نتأكد من عمرج من هالصور" in said and "وگفنا فتح الحساب" in said
    assert h.row(CAROL) is None and h.user(CAROL) is None and h.held(CAROL) is None
    assert h.channel.accounts.kyc_under_age_seen(h.channel.sender_key(CAROL))  # still no «Later» next time
    to_document(h, CAROL)
    assert buttons(h, CAROL) == []


def test_an_untied_back_after_an_under_age_answer_stops_the_sign_up(h):
    to_document(h, CAROL)
    h.photo(CAROL, raw(birth_date="2012-05-01"))
    to_document(h, CAROL)
    h.photo(CAROL, FRONT)
    h.photo(CAROL, ADULT_BACK)
    assert "ما گدرنا نتأكد من عمرج" in h.last(CAROL) and h.user(CAROL) is None


def test_only_a_review_that_read_an_adult_age_clears_the_under_age_mark(h):
    key = h.channel.sender_key(CAROL)
    to_document(h, CAROL)
    h.photo(CAROL, raw(birth_date="2012-05-01"))
    note = h.channel.accounts.kyc_note_check
    note(uid=None, ref=key, status="review", reason="too_many_attempts", attempt=3, checks=())
    note(uid=None, ref=key, status="review", reason="sides_unconfirmed", attempt=1, checks=(("age", "ok"),))
    assert h.channel.accounts.kyc_under_age_seen(key)
    note(uid=None, ref=key, status="review", reason="name_mismatch", attempt=1, checks=(("age", "ok"),))
    assert not h.channel.accounts.kyc_under_age_seen(key)


# ---------------------------------------------------------------- an account whose document reads under 18


def under_age_account(h: KycHarness) -> dict:
    user = sign_up(h, CAROL)
    fund(h, CAROL)
    h.say(CAROL, "وثّق حسابي")
    h.photo(CAROL, raw(birth_date="2012-05-01"))
    return user


def test_an_under_age_account_is_frozen_at_once_and_cannot_pay(h):
    user = under_age_account(h)
    after = h.user(CAROL)
    assert (after["status"], after["frozen_reason"], after["kyc_reason"]) == ("frozen", "kyc_under_age", "under_age")
    balance = after["balance"]
    seen = len(h.graph.to(CAROL))
    h.say(CAROL, "دز خمسين الف لـ 0770 000 0002")
    replies = h.graph.to(CAROL)[seen:]
    assert replies and "'confirm:" not in str(replies)  # no confirm card (before the freeze there is one)
    assert h.user(CAROL)["balance"] == balance
    assert [n for n in h.channel.accounts.pending_notices(user["id"]) if n["kind"] == "frozen"] == []  # said already


def test_an_under_age_answer_stays_until_an_admin_unfreezes(h):
    user = under_age_account(h)
    h.say(CAROL, "وثّق حسابي")  # a borrowed adult card must not replace the answer
    assert "حسابج موقوف لحد ما يراجعه موظف" in h.graph.texts(CAROL)[-1] and h.row(CAROL) is None
    assert h.user(CAROL)["kyc_reason"] == "under_age"
    assert h.channel.accounts.kyc_decide(user["id"], False, actor="admin:test", reason="under 18") is True
    rejected = h.user(CAROL)
    assert (rejected["status"], rejected["kyc_reason"], rejected["kyc_birth_year"]) == ("frozen", "under_age", 2012)
    [notice] = [n for n in h.channel.accounts.pending_notices(user["id"]) if n["kind"].startswith(("kyc", "frozen"))]
    assert notice["kind"] == "frozen"  # «your account is paused», not «your small limits stay»
    h.say(CAROL, "وثّق حسابي")
    assert h.row(CAROL) is None  # rejected and still frozen: still not opened
    h.channel.wallet.set_status(user["id"], "active", actor="admin:test", reason="checked in person")
    h.say(CAROL, "وثّق حسابي")
    assert h.row(CAROL)["step"] == "verify"  # the admin's explicit decision lifts it


# ---------------------------------------------------------------- the front in memory


def test_an_abandoned_front_is_dropped_when_anyone_writes_after_its_time(h):
    to_document(h, CAROL)
    h.photo(CAROL, FRONT)
    assert h.channel.onboarding._fronts
    h.clock.advance(hours=48)
    h.say(DAN, "هلا")  # another sender: CAROL never writes again
    assert h.channel.onboarding._fronts == {} and NUMBER not in repr(h.channel.onboarding._fronts)


def test_a_failed_back_does_not_extend_the_fronts_time(h):
    to_document(h, CAROL)
    h.photo(CAROL, FRONT)
    held_at = h.channel.onboarding._fronts[h.channel.sender_key(CAROL)][1]
    h.clock.advance(minutes=20)
    h.photo(CAROL, {**BACK, "birth_date": None})  # a back that cannot be used: the front stays, its time too
    assert h.channel.onboarding._fronts[h.channel.sender_key(CAROL)][1] == held_at
    h.clock.advance(minutes=20)  # 40 minutes after the front was read
    h.photo(CAROL, BACK)
    assert "ما بقت عدنا قراءة وجه البطاقة" in h.last(CAROL) and h.row(CAROL)["step"] == "document"


# ---------------------------------------------------------------- the end of the tries


def test_the_end_of_the_tries_does_not_say_start_again(h):
    to_document(h, CAROL)
    h.photo(CAROL, raw(readable=False))
    h.photo(CAROL, raw(readable=False))
    h.photo(CAROL, FRONT)
    h.photo(CAROL, {**BACK, "document_number": OTHER_NUMBER})  # the third failed try is two different cards
    said = h.graph.texts(CAROL)[-1]
    assert "بعد 3 محاولات. موظف راح يراجع طلبج" in said and "نبدي من جديد" not in said
    assert h.row(CAROL)["step"] == "pin"
