"""Chat app: the card and receipt in the user's language, and voice replies when the user used the mic."""

from __future__ import annotations

import base64
import copy
import uuid

from fastapi.testclient import TestClient

from web.app import create_app
from web.service import DemoStore
from web.tests.test_web_app import PIN, SEED, UID, say, tap


class SpeakingCore:
    """Hears a fixed English sentence, reads it like StubCore, and speaks (pretend ogg)."""

    def __init__(self):
        from core.stub import StubCore

        self.inner = StubCore()
        self.said: list[tuple[str, str]] = []

    def understand(self, text, context):
        return self.inner.understand(text, context)

    def phrase(self, key, slots):
        return self.inner.phrase(key, slots)

    def transcribe(self, audio, mime, context):
        return "send 50000 to Hadi"

    def speak(self, text, language, context):
        self.said.append((text, language))
        return b"OggS-reply", "audio/ogg"


def _client(store):
    c = TestClient(create_app(store))
    c.headers["X-Demo-Session"] = str(uuid.uuid4())
    c.post("/api/start", json={"user_id": UID}).raise_for_status()
    return c


def test_english_card_and_receipt_labels():
    store = DemoStore(seed=copy.deepcopy(SEED))
    try:
        with _client(store) as c:
            p = say(c, "send 50000 to Hadi")
            card = p["card"]
            assert p["reply"]["lang"] == "en" and card["lang"] == "en"
            assert card["title"] == "Transfer" and card["payee"] == "هادي"
            assert card["amount_words"] == "fifty thousand dinars"
            assert card["rows"][0] == {"label": "Phone", "value": "ending in 2222"}
            assert card["labels"]["fee"] == "Fee" and card["labels"]["dinar"] == "dinars"
            assert [b["label"] for b in card["buttons"]] == ["Yes, pay", "Change", "No, cancel"]
            assert p["voice_reply"] is None  # typed, mode mirror: no voice
            done = tap(c, card["quote_id"], "confirm", PIN)
            assert done["receipt"]["status"] == "succeeded" and done["receipt"]["labels"]["succeeded"] == "Payment done"
    finally:
        store.close()


def test_msa_card_labels():
    store = DemoStore(seed=copy.deepcopy(SEED))
    try:
        with _client(store) as c:
            p = say(c, "أريد أن أدفع فاتورة المولدة")
            card = p["card"]
            assert card["lang"] == "msa" and card["title"] == "دفع فاتورة"
            assert card["amount_words"] == "خمسة وأربعون ألف دينار"
            assert [b["label"] for b in card["buttons"]] == ["نعم، ادفع", "تعديل", "لا، إلغاء"]
    finally:
        store.close()


def test_mic_turn_gets_a_voice_reply_in_the_users_language(monkeypatch):
    monkeypatch.setenv("BILLPAY_CORE", f"{__name__}:SpeakingCore")
    store = DemoStore(seed=copy.deepcopy(SEED))
    try:
        with _client(store) as c:
            assert c.get("/api/config").json()["speaks"] is True
            body = c.post("/api/voice", content=b"audio", headers={"Content-Type": "audio/webm"}).json()
            assert body["card"]["lang"] == "en"
            voice = body["voice_reply"]
            assert voice["mime"] == "audio/ogg" and base64.b64decode(voice["data"]) == b"OggS-reply"
            assert voice["text"].startswith("You're sending fifty thousand dinars to هادي")
            assert PIN not in voice["text"]
            typed = say(c, "what is my balance")
            assert typed["voice_reply"] is None  # mirror: typed text gets text
    finally:
        store.close()


def test_voice_reply_mode_always_and_off(monkeypatch):
    monkeypatch.setenv("BILLPAY_CORE", f"{__name__}:SpeakingCore")
    monkeypatch.setenv("VOICE_REPLY_MODE", "always")
    store = DemoStore(seed=copy.deepcopy(SEED))
    try:
        with _client(store) as c:
            assert say(c, "شكد رصيدي")["voice_reply"]["text"] == "رصيدج نص مليون دينار."  # money in words
    finally:
        store.close()
    monkeypatch.setenv("VOICE_REPLY_MODE", "off")
    store = DemoStore(seed=copy.deepcopy(SEED))
    try:
        with _client(store) as c:
            body = c.post("/api/voice", content=b"audio", headers={"Content-Type": "audio/webm"}).json()
            assert body["voice_reply"] is None
    finally:
        store.close()
