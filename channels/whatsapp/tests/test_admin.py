"""The admin dashboard: Basic auth, the login rate limit, CSRF, every page, every action audited, the money
actions' confirm step and idempotency, exports and backups. Offline, on the channel app with test accounts."""

from __future__ import annotations

import base64
import re

import pytest

from accounts.admin import BANNER
from channels.whatsapp.tests import kit
from channels.whatsapp.tests.kit import ALICE, BOB, PIN
from channels.whatsapp.tests.test_whatsapp_channel import Harness

PASSWORD = "test-admin-password"  # a test value, not a real secret
HOST = "testserver"


def auth(user: str = "karar", password: str = PASSWORD) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path, admin_password=PASSWORD)
    yield harness
    harness.stop()


def get(h, path: str, **kw):
    return h.client.get(path, headers=auth(), **kw)


def form_of(html: str, action: str) -> dict:
    """The hidden fields of the form posting to `action` (csrf, op_id ...)."""
    start = html.index(f'action="{action}"')
    end = html.index("</form>", start)
    return dict(re.findall(r'type="hidden" name="([a-z_]+)" value="([^"]*)"', html[start:end]))


def post(h, path: str, data: dict, *, origin: str = f"http://{HOST}", **headers):
    return h.client.post(path, data=data, headers={**auth(), "Origin": origin, **headers}, follow_redirects=False)


def test_admin_is_404_without_a_password_and_401_without_or_with_wrong_login(tmp_path, h):
    plain = Harness(tmp_path / "plain")
    try:
        assert plain.client.get("/admin").status_code == 404
    finally:
        plain.stop()
    assert h.client.get("/admin").status_code == 401
    assert h.client.get("/admin", headers=auth(password="nope")).status_code == 401
    assert h.client.get("/admin", headers=auth(user="someone")).status_code == 401
    assert get(h, "/admin").status_code == 200


def test_ten_failed_logins_lock_the_address(h):
    for _ in range(10):
        assert h.client.get("/admin", headers=auth(password="bad")).status_code == 401
    assert h.client.get("/admin", headers=auth()).status_code == 429


def test_every_page_renders_with_the_banner_strict_headers_and_escaping(h):
    h.channel.wallet.invite("9647700000009", "<script>x</script>", op_id="op1", actor="admin:test")
    uid = h.channel.accounts.by_phone("9647700000009")["id"]
    for path in ("/admin", "/admin/users", "/admin/users?q=0770", "/admin/users/new", f"/admin/users/{uid}",
                 "/admin/users/u1", "/admin/transactions", "/admin/holds", "/admin/requests", "/admin/billers",
                 "/admin/budget", "/admin/audit", "/admin/backups"):
        r = get(h, path)
        assert r.status_code == 200, path
        assert BANNER in r.text.replace("&#x27;", "'"), path
        assert "<script>x" not in r.text, path
        assert r.headers["Cache-Control"] == "no-store" and r.headers["X-Frame-Options"] == "DENY"
        assert "script-src" not in r.headers["Content-Security-Policy"]
        assert 'name="viewport"' in r.text and "@media (max-width:640px)" in r.text  # cards on a phone
    assert get(h, "/admin/users/nobody").status_code == 404
    assert h.client.get("/admin", headers={**auth(), "X-Forwarded-Proto": "http"}).status_code == 403


def test_a_post_without_origin_or_token_is_refused(h):
    page = get(h, "/admin/users/u1").text
    fields = form_of(page, "/admin/users/u1/freeze")
    assert post(h, "/admin/users/u1/freeze", {**fields, "reason": "check"}, origin="https://evil.example"
                ).status_code == 403
    assert post(h, "/admin/users/u1/freeze", {"csrf": "forged", "reason": "check"}).status_code == 403
    assert h.client.post("/admin/users/u1/freeze", data={**fields, "reason": "check"}, headers=auth()).status_code == 403
    assert h.channel.accounts.get("u1")["status"] == "active"


def test_credit_needs_a_confirm_step_and_a_resubmitted_form_moves_nothing(h):
    page = get(h, "/admin/users/u2").text
    fields = form_of(page, "/admin/users/u2/credit")
    data = {**fields, "amount": "100,000", "reason": "cash at the office", "notify": "1"}
    confirm = post(h, "/admin/users/u2/credit", data)
    assert confirm.status_code == 200 and "300,000 → <b>400,000</b>" in confirm.text
    assert h.channel.accounts.get("u2")["balance"] == 300_000  # nothing yet
    done = post(h, "/admin/users/u2/credit", {**data, "confirm": "1"})
    assert done.status_code == 303 and h.channel.accounts.get("u2")["balance"] == 400_000
    again = post(h, "/admin/users/u2/credit", {**data, "confirm": "1"})  # the same form, sent twice
    assert "Already+done" in again.headers["location"] and h.channel.accounts.get("u2")["balance"] == 400_000
    debit = form_of(get(h, "/admin/users/u2").text, "/admin/users/u2/debit")
    too_much = post(h, "/admin/users/u2/debit", {**debit, "amount": "500000", "reason": "test", "confirm": "1"})
    assert "below+0" in too_much.headers["location"]
    assert h.channel.wallet.audit()["ok"]
    h.say(BOB, "شكد رصيدي")  # the notice reaches Bob before the answer
    assert "انضافلك 100,000 دينار. السبب: cash at the office" in h.graph.texts(BOB)[0]


def test_add_person_freeze_unfreeze_pin_reset_limit_and_every_action_is_audited(h):
    page = get(h, "/admin/users/new").text
    fields = form_of(page, "/admin/users/new")
    r = post(h, "/admin/users/new", {**fields, "phone": "0770 000 0007", "name": "حسين علي", "language": "iq",
                                      "gender": "m", "opening": "50000", "limit": "500000", "note": "judge"})
    assert r.status_code == 303
    user = h.channel.accounts.by_phone("9647700000007")
    assert user["status"] == "invited" and user["balance"] == 50_000 and user["daily_limit"] == 500_000
    same = post(h, "/admin/users/new", {**fields, "phone": "07700000007", "name": "حسين علي"})
    assert f"/admin/users/{user['id']}" in same.headers["location"]  # an existing number goes to its page
    base = "/admin/users/u1"
    for action in ("freeze", "unfreeze", "pin/reset", "limit"):
        fields = form_of(get(h, base).text, f"{base}/{action}")
        extra = {"limit": "750000"} if action == "limit" else {"reason": "phone check"}
        assert post(h, f"{base}/{action}", {**fields, **extra}).status_code == 303, action
    alice = h.channel.accounts.get("u1")
    assert alice["status"] == "active" and alice["pin_state"] == "unset" and alice["daily_limit"] == 750_000
    actions = {r["action"] for r in h.channel.accounts.audit_rows(limit=100) if r["actor"].startswith("admin:karar@")}
    assert {"user_invited", "admin_credit", "frozen", "unfrozen", "pin_reset", "limit_changed"} <= actions
    # Alice's next message: set a new PIN first
    h.say(ALICE, "هلا")
    assert "صفّرنا رمزج السري" in h.graph.texts(ALICE)[-1]


def test_hold_cancel_refunds_the_sender(h):
    h.say(ALICE, "دز عشرة الاف لـ 0770 000 0005")
    h.tap(ALICE, "إي، ادفع")
    h.say(ALICE, PIN)
    [hold] = h.channel.wallet.holds(state="HELD")
    path = f"/admin/holds/{hold['id']}/cancel"
    fields = form_of(get(h, "/admin/holds").text, path)
    assert post(h, path, {**fields, "reason": "sent by mistake"}).status_code == 303
    assert h.channel.accounts.get("u1")["balance"] == 500_000
    assert h.channel.wallet.holds()[0]["state"] == "REFUNDED"


def test_csv_exports_have_a_bom_no_pin_hash_and_neutralise_formulas(h):
    h.channel.wallet.invite("9647700000008", "=HYPERLINK(1)", op_id="op2", actor="admin:test")
    r = get(h, "/admin/export/users.csv")
    assert r.status_code == 200 and r.text.startswith("﻿") and "attachment" in r.headers["content-disposition"]
    assert "pin_hash" not in r.text and "pbkdf2" not in r.text
    assert "'=HYPERLINK(1)" in r.text
    for name in ("transactions", "ledger", "holds", "audit"):
        assert get(h, f"/admin/export/{name}.csv").status_code == 200
    assert get(h, "/admin/export/secrets.csv").status_code == 404


def test_back_up_now(h):
    fields = form_of(get(h, "/admin/backups").text, "/admin/backups/now")
    r = post(h, "/admin/backups/now", fields)
    assert r.status_code == 303 and "Written" in r.headers["location"]
    assert "accounts-manual-" in get(h, "/admin/backups").text
    assert any(a["action"] == "backup" for a in h.channel.accounts.audit_rows(limit=10))


def test_billers_can_be_added_and_switched_off(h):
    fields = form_of(get(h, "/admin/billers").text, "/admin/billers")
    r = post(h, "/admin/billers", {**fields, "id": "gen_karrada", "kind": "generator", "name_ar": "مولدة الكرادة",
                                    "name_en": "Karrada Generator", "mock_min": "30000", "mock_max": "80000"})
    assert r.status_code == 303
    assert any(b["id"] == "gen_karrada" for b in h.channel.accounts.billers("generator"))
    fields = form_of(get(h, "/admin/billers").text, "/admin/billers/gen_karrada")
    post(h, "/admin/billers/gen_karrada", {**fields, "active": "0", "mock_min": "30000", "mock_max": "80000"})
    assert not any(b["id"] == "gen_karrada" for b in h.channel.accounts.billers("generator"))


def test_the_requests_page_lists_pin_reset_requests_and_sign_ups_in_progress(h):
    h.say(ALICE, "نسيت الرمز")
    h.send(kit.tap(ALICE, "m:forgot:yes", "x", h.clock))
    h.say("9647700000006", "هلا")
    page = get(h, "/admin/requests").text
    assert "pin_reset_requested" in page and "0770 000 0006" in page and "offer" in page
