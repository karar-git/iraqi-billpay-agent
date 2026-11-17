"""The Cloud API client over a fake transport: payload shapes, rate-limit retries, media only from Meta."""

from __future__ import annotations

import json

import httpx
import pytest

from channels.whatsapp.config import Settings
from channels.whatsapp.graph import GraphClient, SendError, buttons_payload, is_meta, sign, signature_ok


def client(handler) -> tuple[GraphClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return GraphClient("tok", "PN1", "v25.0", transport=httpx.MockTransport(record), sleep=lambda s: None), seen


def test_send_text_and_buttons_shapes():
    graph, seen = client(lambda r: httpx.Response(200, json={"messages": [{"id": "wamid.1"}]}))
    graph.send_text("9647700000001", True, "هلا")
    graph.send_buttons("IQ.9", False, "بطاقة", [("confirm:q1", "إي، ادفع"), ("cancel:q1", "لا، بطّلت")],
                       header="تأكيد تحويل", footer="تنتهي")
    first, second = (json.loads(r.content) for r in seen)
    assert str(seen[0].url) == "https://graph.facebook.com/v25.0/PN1/messages"
    assert seen[0].headers["Authorization"] == "Bearer tok"
    assert first == {"messaging_product": "whatsapp", "to": "9647700000001", "recipient_type": "individual",
                     "type": "text", "text": {"preview_url": False, "body": "هلا"}}
    assert second["recipient"] == "IQ.9" and "to" not in second  # a username-only account
    assert second["interactive"]["type"] == "button"
    assert [b["reply"]["id"] for b in second["interactive"]["action"]["buttons"]] == ["confirm:q1", "cancel:q1"]


def test_button_limits():
    with pytest.raises(ValueError):
        buttons_payload("1", True, "x", [])
    long = buttons_payload("1", True, "ب" * 2000, [("a", "ع" * 30)])["interactive"]
    assert len(long["body"]["text"]) == 1024 and len(long["action"]["buttons"][0]["reply"]["title"]) == 20


def test_rate_limits_are_retried_other_errors_are_not():
    replies = iter([httpx.Response(429, text="slow down"), httpx.Response(200, json={})])
    graph, seen = client(lambda r: next(replies))
    graph.send_text("1", True, "x")
    assert len(seen) == 2
    graph, seen = client(lambda r: httpx.Response(500, text="boom"))
    with pytest.raises(SendError):
        graph.send_text("1", True, "x")
    assert len(seen) == 1  # a 5xx may have been delivered: never sent twice


def test_media_is_downloaded_only_from_meta_hosts():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "graph.facebook.com" and request.url.path.endswith("/m1"):
            return httpx.Response(200, json={"url": "https://lookaside.fbsbx.com/whatsapp/m1", "mime_type": "audio/ogg",
                                             "file_size": 9})
        if request.url.host == "graph.facebook.com" and request.url.path.endswith("/m2"):
            return httpx.Response(200, json={"url": "https://evil.example.com/m2", "mime_type": "audio/ogg"})
        return httpx.Response(200, content=b"OggS-data")

    graph, seen = client(handler)
    assert graph.media("m1") == (b"OggS-data", "audio/ogg")
    with pytest.raises(SendError):
        graph.media("m2")
    assert not any(r.url.host == "evil.example.com" for r in seen)  # the token never left Meta
    assert is_meta("https://scontent.whatsapp.net/x") and not is_meta("http://lookaside.fbsbx.com/x")


def test_signature_check():
    raw = b'{"a":1}'
    assert signature_ok(raw, sign(raw, "s3cret"), "s3cret")
    assert not signature_ok(raw, sign(raw, "other"), "s3cret")
    assert not signature_ok(raw, sign(raw, "s3cret"), "")


def test_settings_from_env_never_show_secrets():
    s = Settings.from_env({"WHATSAPP_TOKEN": "EAAG-secret", "WHATSAPP_APP_SECRET": "appsecret",
                           "WHATSAPP_VERIFY_TOKEN": "verify", "WHATSAPP_PHONE_NUMBER_ID": "123",
                           "ALLOWED_NUMBERS": "+964 770 000 0001, 9647700000002", "MANAGER_WHATSAPP": "+964 780 1",
                           "DAILY_BUDGET_USD": "3.5", "MAX_MSGS_PER_USER_HOUR": "x"})
    assert "EAAG-secret" not in repr(s) and "appsecret" not in repr(s) and "verify" not in repr(s)
    assert s.allowed("9647700000001") and s.allowed("+964 770 000 0002") and not s.allowed("9647700000003")
    assert s.manager == "9647801" and s.daily_budget_usd == 3.5 and s.max_msgs_per_user_hour == 60
    assert s.missing == []
