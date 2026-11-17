"""Review findings on the admin dashboard (fixed; these tests keep them fixed).

1. Every admin page sends Referrer-Policy: no-referrer. Under that policy a browser submits a same-origin
   form POST with `Origin: null` and no Referer (Fetch standard, "append a request Origin header";
   checked in Chromium). csrf_ok() then sees no usable origin and every admin action from a real browser
   is refused with 403. The existing tests pass only because they send a hand-made Origin header.
2. The login lockout keys on the left-most X-Forwarded-For entry, which the client writes itself, so a
   new value per try is never locked out (and every value adds an entry to the in-memory fail table).
"""

from __future__ import annotations

import pytest

from channels.whatsapp.tests.test_admin import HOST, auth, form_of, get, h  # noqa: F401  (h is the fixture)


def test_a_browser_form_post_from_the_admin_page_is_accepted(h):  # noqa: F811
    h.channel.wallet.invite("9647700000011", "Review Person", op_id="op-review-1", actor="admin:test")
    uid = h.channel.accounts.by_phone("9647700000011")["id"]
    page = get(h, f"/admin/users/{uid}")
    assert page.headers["Referrer-Policy"] == "same-origin"  # the browser now sends the real Origin
    path = f"/admin/users/{uid}/freeze"
    fields = form_of(page.text, path)
    # What Chromium / Firefox send for this form under the page's own Referrer-Policy: Origin null, no Referer.
    r = h.client.post(path, data={**fields, "reason": "review check"},
                      headers={**auth(), "Origin": "null", "Sec-Fetch-Site": "same-origin"},
                      follow_redirects=False)
    assert r.status_code == 303, r.status_code  # was 403 "bad form token or origin"
    # An opaque origin without the browser's same-origin mark is still refused, token or not.
    r = h.client.post(path, data={**fields, "reason": "review check"}, headers={**auth(), "Origin": "null"},
                      follow_redirects=False)
    assert r.status_code == 403


def test_rotating_x_forwarded_for_does_not_escape_the_login_lockout(h):  # noqa: F811
    for i in range(30):
        h.client.get("/admin", headers={**auth(password="bad"), "X-Forwarded-For": f"10.9.{i}.1"})
    r = h.client.get("/admin", headers={**auth(password="bad"), "X-Forwarded-For": "10.9.99.1"})
    assert r.status_code == 429, r.status_code  # was 401: 31 guesses from one client and no lockout
    assert HOST  # same test host as test_admin
