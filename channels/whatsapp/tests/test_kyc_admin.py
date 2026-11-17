"""The admin's side of the identity check: the record on the person page (no image, the last 4 only), the reviews
on the requests page, approve and reject (a reason, the form token, the audit, the limits, the notice)."""

from __future__ import annotations

import pytest

from channels.whatsapp.tests.kit import ALICE
from channels.whatsapp.tests.test_admin import PASSWORD, form_of, get, post
from channels.whatsapp.tests.test_kyc_signup import (CAROL, DAN, GOOD_PIN, NUMBER, PRINTED, KycHarness, fund, raw,
                                                     set_pin, sign_up, to_document)


@pytest.fixture
def h(tmp_path):
    harness = KycHarness(tmp_path, admin_password=PASSWORD)
    yield harness
    harness.stop()


def pending(h: KycHarness, who: str = CAROL, **reading) -> dict:
    """An account whose check waits for a person (the photo looked like a copy)."""
    user = sign_up(h, who, raw(looks_like_photo_of_screen_or_copy=True, **reading))
    assert user["kyc_level"] == "pending"
    return user


def decide(h: KycHarness, uid: str, action: str, reason: str = "seen the card in person", page: str | None = None):
    path = f"/admin/users/{uid}/kyc/{action}"
    fields = form_of(get(h, page or f"/admin/users/{uid}").text, path)
    return post(h, path, {**fields, "reason": reason})


def test_the_person_page_shows_the_record_with_the_last_four_only(h):
    user = sign_up(h, CAROL, raw())
    page = get(h, f"/admin/users/{user['id']}")
    assert page.status_code == 200
    text = page.text
    assert "Identity check" in text and 'class="pill verified"' in text
    assert "national_card" in text and PRINTED in text and "••••" + NUMBER[-4:] in text and "1995" in text
    assert "1,000,000 IQD a day" in text and "The photo is not kept" in text
    assert NUMBER not in text and "1995-04-12" not in text and user["kyc_number_fingerprint"] not in text
    assert "<img" not in text
    assert "/kyc/approve" not in text  # nothing to decide on a verified account
    old = get(h, "/admin/users/u1").text  # an account from before the identity check
    assert "No level" in old and "/kyc/approve" not in old


def test_the_requests_page_lists_the_reviews_with_approve_and_reject(h):
    user = pending(h)
    page = get(h, "/admin/requests").text
    assert "kyc_review" in page and "screen_or_copy" in page and "national_card" in page
    assert f'href="/admin/users/{user["id"]}"' in page and "0770 000 0003" in page
    for action in ("approve", "reject"):
        fields = form_of(page, f"/admin/users/{user['id']}/kyc/{action}")
        assert fields["csrf"]
    assert NUMBER not in page
    assert h.channel.accounts.overview()["requests"] == 1


def test_approving_makes_the_account_verified_sets_the_limits_and_tells_the_person(h):
    user = pending(h)
    uid = user["id"]
    fund(h, CAROL)
    h.say(CAROL, "دز ستين الف لـ 0770 000 0002")
    assert "50,000" in h.graph.texts(CAROL)[-1]  # the small cap, while the review waits
    path = f"/admin/users/{uid}/kyc/approve"
    fields = form_of(get(h, "/admin/requests").text, path)  # decided from the requests page
    done = post(h, path, {**fields, "reason": "seen the card in person"})
    assert done.status_code == 303 and "approved" in done.headers["location"]
    after = h.channel.accounts.get(uid)
    assert after["kyc_level"] == "verified" and after["kyc_reason"] == "approved" and after["daily_limit"] == 1_000_000
    assert after["kyc_name"] == PRINTED and after["kyc_number_last4"] == NUMBER[-4:]  # the record stays
    [line] = h.audit("kyc_approved")
    assert line["actor"].startswith("admin:karar@") and line["user_id"] == uid
    assert '"reason": "seen the card in person"' in line["details_json"]
    assert h.channel.accounts.open_requests() == []
    h.graph.clear()
    h.channel.tick()  # inside her window: the notice goes out by itself
    assert h.graph.texts(CAROL) == ["تم توثيق حسابج ✅ صار حدج اليومي 1,000,000 دينار، وأكثر مبلغ بالدفعة الوحدة "
                                    "1,000,000 دينار."]
    h.say(CAROL, "دز ستين الف لـ 0770 000 0002")
    assert h.graph.last_card(CAROL)["action"]["buttons"][0]["reply"]["id"].startswith("confirm:")
    again = post(h, path, {**fields, "reason": "seen the card in person"})  # the same form, sent twice
    assert "not_waiting_for_review" in again.headers["location"] and len(h.audit("kyc_approved")) == 1
    assert h.channel.accounts.pending_notices(uid) == []  # and no second notice


def test_rejecting_keeps_the_small_limits_and_tells_the_person_inside_the_window(h):
    user = pending(h)
    uid = user["id"]
    done = decide(h, uid, "reject", reason="الصورة مو من الهوية الأصلية")
    assert done.status_code == 303 and "rejected" in done.headers["location"]
    after = h.channel.accounts.get(uid)
    assert after["kyc_level"] == "none" and after["kyc_reason"] == "rejected" and after["daily_limit"] == 150_000
    [line] = h.audit("kyc_rejected")
    assert line["actor"].startswith("admin:karar@") and "الصورة مو من الهوية الأصلية" in line["details_json"]
    assert h.channel.accounts.open_requests() == []
    h.graph.clear()
    h.channel.tick()
    [notice] = h.graph.texts(CAROL)
    assert notice.startswith("ما گدرنا نوثّق حسابج. السبب: الصورة مو من الهوية الأصلية")
    assert "تبقى حدودج الصغيرة" in notice and "«وثّق حسابي»" in notice
    h.say(CAROL, "وثّق حسابي")  # and she may send another document
    assert h.row(CAROL)["step"] == "verify"
    h.photo(CAROL, raw())
    assert h.user(CAROL)["kyc_level"] == "verified"


def test_a_notice_outside_the_window_waits_for_the_next_message(h):
    user = pending(h)
    h.clock.advance(hours=30)
    decide(h, user["id"], "approve")
    h.graph.clear()
    h.channel.tick()
    assert h.graph.texts(CAROL) == []  # her window is closed
    h.say(CAROL, "شكد رصيدي")
    assert h.graph.texts(CAROL)[0].startswith("تم توثيق حسابج ✅")


def test_a_decision_needs_a_reason_a_token_and_this_origin(h):
    user = pending(h)
    uid = user["id"]
    path = f"/admin/users/{uid}/kyc/approve"
    fields = form_of(get(h, f"/admin/users/{uid}").text, path)
    no_reason = post(h, path, {**fields, "reason": " "})
    assert no_reason.status_code == 303 and "reason" in no_reason.headers["location"] and "bad=1" in no_reason.headers["location"]
    assert post(h, path, {**fields, "reason": "fine"}, origin="https://evil.example").status_code == 403
    assert post(h, path, {"csrf": "forged", "reason": "fine"}).status_code == 403
    reject = form_of(get(h, f"/admin/users/{uid}").text, f"/admin/users/{uid}/kyc/reject")
    assert post(h, path, {**reject, "reason": "fine"}).status_code == 403  # the other form's token
    assert h.client.post(path, data={**fields, "reason": "fine"}).status_code == 401  # no login
    assert h.channel.accounts.get(uid)["kyc_level"] == "pending"
    assert h.audit("kyc_approved") == [] and h.audit("kyc_rejected") == []
    assert post(h, "/admin/users/nobody/kyc/approve", {**fields, "reason": "fine"}).status_code in (403, 404)


def test_an_account_with_no_review_cannot_be_decided(h):
    verified = sign_up(h, CAROL, raw())
    waiting = pending(h, DAN, document_number="200198765432")
    fields = form_of(get(h, f"/admin/users/{waiting['id']}").text, f"/admin/users/{waiting['id']}/kyc/approve")
    # a token is bound to its path: the waiting account's form cannot decide another account
    assert post(h, f"/admin/users/{verified['id']}/kyc/approve", {**fields, "reason": "fine"}).status_code == 403
    assert post(h, "/admin/users/u1/kyc/reject", {**fields, "reason": "fine"}).status_code == 403
    assert h.channel.accounts.kyc_decide("u1", True, actor="admin:test", reason="x") is False
    assert h.channel.accounts.kyc_decide(verified["id"], False, actor="admin:test", reason="x") is False
    assert h.channel.accounts.get("u1")["kyc_level"] is None and h.channel.accounts.get("u1")["daily_limit"] == 1_000_000
    assert h.channel.accounts.get(verified["id"])["kyc_level"] == "verified"


def test_a_document_seen_on_two_accounts_is_linked_on_both_pages(h):
    first = sign_up(h, CAROL, raw())
    to_document(h, DAN)
    h.photo(DAN, raw())
    second = set_pin(h, DAN)
    assert second["kyc_reason"] == "duplicate_document"
    page = get(h, f"/admin/users/{second['id']}").text
    assert "Same document as" in page and f'href="/admin/users/{first["id"]}"' in page
    assert f'href="/admin/users/{second["id"]}"' in get(h, f"/admin/users/{first['id']}").text


def test_the_users_export_has_the_level_and_nothing_of_the_document(h):
    user = sign_up(h, CAROL, raw())
    text = get(h, "/admin/export/users.csv").text
    header = text.splitlines()[0]
    assert "kyc_level" in header and "kyc_name" not in header and "fingerprint" not in header
    assert "verified" in text and PRINTED not in text and NUMBER[-4:] + "," not in text.replace(CAROL, "")
    assert user["kyc_number_fingerprint"] not in text
    audit = get(h, "/admin/export/audit.csv").text
    assert "kyc_checked" in audit and NUMBER not in audit and PRINTED not in audit


def test_every_admin_page_still_renders_with_a_review_waiting(h):
    pending(h)
    for path in ("/admin", "/admin/users", "/admin/requests", "/admin/audit", f"/admin/users/{h.uid(CAROL)}",
                 f"/admin/users/{h.uid(ALICE)}"):
        assert get(h, path).status_code == 200, path
    assert GOOD_PIN not in get(h, "/admin/audit").text
