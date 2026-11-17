"""The WhatsApp channel end to end: webhook -> orchestrator -> the shared accounts database -> replies.

Offline: a fake Graph API records what would be sent, the core is the open StubCore (or a small
subclass of it), and the clock is moved by hand. No network, no keys. The two test accounts (kit.SEED)
are preloaded on ALICE's and BOB's numbers; sign-up itself is in test_accounts_channel.py.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from channels.whatsapp import texts
from channels.whatsapp.app import create_app
from channels.whatsapp.config import Settings
from channels.whatsapp.graph import BUTTON_TITLE_MAX, parse
from channels.whatsapp.tests import kit
from channels.whatsapp.tests.kit import ALICE, BOB, PIN, PNID, SECRET, VERIFY
from core.stub import StubCore
from payments.quote import BUTTONS


class Harness:
    def __init__(self, tmp_path, *, core=None, clock=None, **overrides):
        self.tmp_path = tmp_path
        self.core = core
        self.clock = clock or kit.Clock()
        self.overrides = overrides
        self.graph = kit.FakeGraph()
        self._cm = None
        self.start()

    def settings(self) -> Settings:
        base = dict(token="test-token", app_secret=SECRET, verify_token=VERIFY, phone_number_id=PNID,
                    data_dir=self.tmp_path, debounce_seconds=0.0, daily_budget_usd=0.0,
                    signup_mode="open")  # the typed sign-up these tests cover; demo mode: test_demo_accounts.py
        return Settings(**{**base, **self.overrides})

    def start(self) -> None:
        self.app = create_app(self.settings(), graph=self.graph, core=self.core, seed=kit.seed(), clock=self.clock)
        self._cm = TestClient(self.app)
        self.client = self._cm.__enter__()
        self.channel = self.app.state.channel

    def stop(self) -> None:
        self._cm.__exit__(None, None, None)

    def restart(self) -> None:
        self.stop()
        self.start()

    def send(self, *messages: dict):
        response = kit.post(self.client, kit.webhook(*messages))
        assert response.status_code == 200
        assert self.channel.join(20)
        return response

    def say(self, who: str, body: str) -> None:
        self.send(kit.text(who, body, self.clock))

    def tap(self, who: str, title: str, card: dict | None = None) -> None:
        card = card or self.graph.last_card(who)
        self.send(kit.tap(who, kit.card_buttons(card)[title], title, self.clock))

    def uid(self, who: str) -> str:
        return self.channel.accounts.by_phone(who)["id"]

    def wallet(self, who: str):
        return self.channel._runtime(self.channel.sender_key(who), self.uid(who)).wallet

    def balance(self, who: str, uid: str | None = None) -> int:
        return self.wallet(who).user(uid or self.uid(who))["balance"]

    def transactions(self, who: str, uid: str | None = None) -> list[dict]:
        return self.wallet(who).transactions(uid or self.uid(who))


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path)
    yield harness
    harness.stop()


def greeted(h: Harness, who: str = ALICE) -> None:
    """Say hello once and forget the greeting."""
    h.say(who, "هلا")
    h.graph.clear()


def open_card(h: Harness, who: str = ALICE, body: str = "دز لهادي خمسين الف") -> dict:
    h.say(who, body)
    return h.graph.last_card(who)


# ---------------------------------------------------------------- webhook


def test_verification_handshake(h):
    ok = h.client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": VERIFY,
                                          "hub.challenge": "12345"})
    assert ok.status_code == 200 and ok.text == "12345"
    bad = h.client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "nope",
                                           "hub.challenge": "12345"})
    assert bad.status_code == 403


def test_bad_or_missing_signature_is_403_and_nothing_happens(h):
    body = kit.webhook(kit.text(ALICE, "ادفع المولدة", h.clock))
    assert kit.post(h.client, body, secret="wrong-secret").status_code == 403
    assert kit.post(h.client, body, signature="").status_code == 403
    assert kit.post(h.client, body, signature="sha1=abc").status_code == 403
    h.channel.join()
    assert h.graph.sent == []


def test_no_app_secret_means_every_post_is_refused(tmp_path):
    harness = Harness(tmp_path, app_secret="")
    try:
        assert kit.post(harness.client, kit.webhook(kit.text(ALICE, "هلا", harness.clock))).status_code == 403
    finally:
        harness.stop()


def test_statuses_and_other_numbers_are_ignored(h):
    h.send()  # an empty change
    kit.post(h.client, kit.webhook(statuses=[{"id": "wamid.x", "status": "delivered"}]))
    kit.post(h.client, kit.webhook(kit.text(ALICE, "هلا", h.clock), pnid="999"))  # the store's number
    h.channel.join()
    assert h.graph.sent == []


def test_meta_retry_of_the_same_message_is_handled_once(h):
    message = kit.text(ALICE, "هلا", h.clock, msg_id="wamid.same")
    h.send(message)
    h.send(message)
    assert len(h.graph.texts(ALICE)) == 1


def test_healthz(tmp_path, h):
    body = h.client.get("/healthz").json()
    assert body == {"ok": True, "missing": [], "voice": False}  # nothing about accounts on a public endpoint
    assert h.channel.accounts.schema_version() == 6
    empty = Harness(tmp_path / "x", token="", phone_number_id="")
    try:
        r = empty.client.get("/healthz")
        assert r.status_code == 503
        assert r.json()["missing"] == ["WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID"]
    finally:
        empty.stop()


def test_parse_reads_buttons_voice_and_username_accounts():
    clock = kit.Clock()
    body = kit.webhook(kit.tap(ALICE, "confirm:q1", "إي، ادفع", clock), kit.voice(ALICE, "m1", clock))
    username = {"from_user_id": "IQ.123", "id": "wamid.u", "timestamp": "0", "type": "text", "text": {"body": "هلا"}}
    body["entry"][0]["changes"][0]["value"]["messages"].append(username)
    items = parse(body, PNID)
    assert [(i.kind, i.button_id, i.media_id) for i in items[:2]] == [("button", "confirm:q1", ""), ("voice", "", "m1")]
    assert items[2].sender == "IQ.123" and items[2].has_phone is False


# ---------------------------------------------------------------- greeting


def test_a_known_number_is_greeted_by_name_with_the_balance_and_no_demo_words(h):
    h.say(ALICE, "هلا")
    [greeting] = h.graph.texts(ALICE)
    assert greeting == "صباح الخير سارة! رصيدج 500,000 دينار. شنو تحتاجين؟"  # 09:00 in Baghdad, her own account
    assert PIN not in greeting and "تجريب" not in greeting and "وهمية" not in greeting and "چ" not in greeting


def test_a_request_from_a_known_number_is_answered_at_once(h):
    h.say(ALICE, "ادفع المولدة")
    assert h.graph.texts(ALICE) == []  # no greeting first: she is recognised by her number
    assert "مولدة الحجي جاسم" in h.graph.last_card(ALICE)["body"]["text"]


# ---------------------------------------------------------------- the card and the PIN


def test_card_is_an_interactive_message_with_the_three_buttons(h):
    greeted(h)
    card = open_card(h)
    body = card["body"]["text"]
    assert "50,000 دينار (خمسين ألف دينار)" in body  # digits and words
    assert "هادي" in body
    assert "العمولة: 250 دينار، المجموع: 50,250 دينار" in body
    assert "رصيدج بعد الدفع: 449,750 دينار" in body
    assert "ما عنده حساب بعد: المبلغ ينحجز إله 7 أيام" in body  # Hadi's number has no account: a hold
    assert "صالحة 5 دقايق" in body
    assert card["header"]["text"] == "تأكيد تحويل"
    assert card["footer"]["text"] == "تنتهي 09:05 بتوقيت بغداد"
    assert list(kit.card_buttons(card)) == ["إي، ادفع", "غيّر", "لا، بطّلت"]
    assert all(bid.split(":")[0] in ("confirm", "change", "cancel") for bid in kit.card_buttons(card).values())
    assert all(len(b.label) <= BUTTON_TITLE_MAX for b in BUTTONS)
    assert len(body) <= 1024


def test_full_bill_payment_with_the_button_and_the_demo_pin(h):
    greeted(h)
    open_card(h, body="ادفع المولدة")
    h.tap(ALICE, "إي، ادفع")
    ask = h.graph.texts(ALICE)[-1]
    assert "اكتبي رمزج السري" in ask and "تجربة" not in ask
    assert h.balance(ALICE) == 500_000  # the tap alone moves nothing
    h.say(ALICE, PIN)
    receipt = h.graph.texts(ALICE)[-1]
    assert receipt.startswith("تمت. دفعتي فاتورة مولدة الحجي جاسم")
    assert "\nوصل\n" in receipt and "وهمية" not in receipt
    assert "الدفعة تبقى داخل الخدمة ولسه ما توصل للجهة" in receipt  # a bill does not reach the biller yet
    assert "فاتورة: مولدة الحجي جاسم، عن شهر تشرين الأول 2026" in receipt
    assert "المبلغ: 45,000 دينار" in receipt and "المجموع: 45,000 دينار" in receipt
    assert "الرصيد بعد الدفع: 455,000 دينار" in receipt and "رقم العملية: t" in receipt
    assert h.balance(ALICE) == 455_000
    assert [t["status"] for t in h.transactions(ALICE)] == ["succeeded"]


def test_transfer_with_eastern_digit_pin(h):
    greeted(h)
    open_card(h)
    h.tap(ALICE, "إي، ادفع")
    h.say(ALICE, "١٢٣٤")
    assert "تحويل إلى: هادي" in h.graph.texts(ALICE)[-1]
    assert h.balance(ALICE) == 449_750


def test_wrong_pin_moves_nothing_and_the_card_stays_open(h):
    greeted(h)
    open_card(h)
    h.tap(ALICE, "إي، ادفع")
    h.say(ALICE, "9999")
    reply = h.graph.texts(ALICE)[-1]
    assert "الرمز السري غلط" in reply and "ما انخصم منج شي" in reply and "اكتبيه مرة ثانية" in reply
    assert "بعد 5 مرات غلط" in reply  # the lockout is warned
    assert h.balance(ALICE) == 500_000 and h.transactions(ALICE) == []
    h.say(ALICE, PIN)  # the right PIN on the same card pays, once
    assert h.balance(ALICE) == 449_750
    assert len(h.transactions(ALICE)) == 1


def test_expired_card_is_refused_without_asking_for_a_pin(h):
    greeted(h)
    open_card(h)
    h.clock.advance(minutes=6)
    h.tap(ALICE, "إي، ادفع")
    reply = h.graph.texts(ALICE)[-1]
    assert "خلص وقت البطاقة" in reply and "ما انخصم منج شي" in reply
    h.say(ALICE, PIN)  # digits now are not a PIN for anything
    assert h.balance(ALICE) == 500_000 and h.transactions(ALICE) == []


def test_pin_typed_after_the_card_expired_moves_nothing(h):
    greeted(h)
    open_card(h)
    h.tap(ALICE, "إي، ادفع")
    h.clock.advance(minutes=6)
    h.say(ALICE, PIN)
    assert "خلص وقت البطاقة" in h.graph.texts(ALICE)[-1]
    assert h.balance(ALICE) == 500_000


def test_second_tap_on_a_paid_card_never_pays_twice(h):
    greeted(h)
    card = open_card(h)
    h.tap(ALICE, "إي، ادفع", card)
    h.say(ALICE, PIN)
    h.tap(ALICE, "إي، ادفع", card)  # the same card again, as a new message
    again = h.graph.texts(ALICE)[-1]
    assert "اندفعت قبل، مرة وحدة بس" in again
    h.say(ALICE, PIN)  # and a PIN after it pays nothing either
    assert h.balance(ALICE) == 449_750
    assert len(h.transactions(ALICE)) == 1
    assert h.wallet(ALICE).audit()["ok"] is True


def test_the_same_pin_message_replayed_by_meta_pays_once(h):
    greeted(h)
    open_card(h)
    h.tap(ALICE, "إي، ادفع")
    pin = kit.text(ALICE, PIN, h.clock, msg_id="wamid.pin")
    h.send(pin)
    h.send(pin)
    assert len(h.transactions(ALICE)) == 1


def test_typed_yes_never_pays(h):
    greeted(h)
    open_card(h)
    h.say(ALICE, "إي")
    assert "دوسي على زر «إي، ادفع»" in h.graph.everything(ALICE)
    assert h.balance(ALICE) == 500_000


def test_cancel_and_change_buttons(h):
    greeted(h)
    open_card(h)
    h.tap(ALICE, "لا، بطّلت")
    assert "بطّلناها" in h.graph.texts(ALICE)[-1]
    open_card(h)
    h.tap(ALICE, "غيّر")
    assert "المبلغ لو الاسم" in h.graph.texts(ALICE)[-1]
    assert h.balance(ALICE) == 500_000


def test_pin_never_reaches_the_core(tmp_path):
    class Spy(StubCore):
        def __init__(self):
            super().__init__()
            self.seen: list[str] = []

        def understand(self, text, context=None):
            self.seen.append(text)
            return super().understand(text, context)

    spy = Spy()
    harness = Harness(tmp_path, core=spy)
    try:
        greeted(harness)
        open_card(harness)
        harness.tap(ALICE, "إي، ادفع")
        harness.say(ALICE, "9999")
        harness.say(ALICE, PIN)
        assert not any("9999" in s or PIN in s for s in spy.seen)
        assert harness.balance(ALICE) == 449_750
    finally:
        harness.stop()


# ---------------------------------------------------------------- isolation and persistence


def test_two_numbers_are_two_accounts(h):
    greeted(h, ALICE)
    greeted(h, BOB)
    card = open_card(h, ALICE)
    h.tap(ALICE, "إي، ادفع", card)
    h.say(ALICE, PIN)
    assert h.balance(ALICE) == 449_750
    assert h.balance(BOB) == 300_000  # Bob is his own account (u2)
    h.tap(BOB, "إي، ادفع", card)  # Bob taps Alice's card id: not his card, nothing happens
    h.say(BOB, "4321")
    assert h.balance(BOB) == 300_000 and h.transactions(BOB) == []
    assert h.balance(ALICE) == 449_750


def test_restart_keeps_wallet_history_greeting_and_dedup(h):
    greeted(h)
    card = open_card(h)
    h.tap(ALICE, "إي، ادفع", card)
    paid = kit.text(ALICE, PIN, h.clock, msg_id="wamid.paid")
    h.send(paid)
    h.restart()
    assert h.balance(ALICE) == 449_750  # the wallet file survived
    assert len(h.transactions(ALICE)) == 1
    h.graph.clear()
    h.send(paid)  # Meta retries the PIN message after the restart: already seen
    h.tap(ALICE, "إي، ادفع", card)  # and the old card: paid once, never again
    assert "اندفعت قبل" in h.graph.texts(ALICE)[-1]
    h.say(ALICE, "شكد رصيدي")
    assert "449,750" in h.graph.texts(ALICE)[-1]
    assert len(h.transactions(ALICE)) == 1


def test_restart_while_waiting_for_the_pin_never_pays_a_stale_card(h):
    greeted(h)
    open_card(h)
    h.tap(ALICE, "إي، ادفع")
    h.restart()
    h.say(ALICE, PIN)  # the PIN step was remembered: the digits never go to the core
    assert "ما تشتغل بعد" in h.graph.texts(ALICE)[-1]
    assert h.balance(ALICE) == 500_000


def test_unanswered_message_is_answered_after_a_restart(h):
    greeted(h)
    raw = {"msg_id": "wamid.lost", "sender": ALICE, "has_phone": True, "kind": "text", "text": "شكد رصيدي",
           "button_id": "", "media_id": "", "mime": "", "timestamp": h.clock().timestamp(), "name": None}
    h.channel.store.first_time("wamid.lost", h.clock().timestamp(), raw)  # taken, then the process died
    h.restart()
    assert h.channel.join()
    assert "500,000" in h.graph.texts(ALICE)[-1]


# ---------------------------------------------------------------- commands


def test_the_demo_commands_are_gone_and_the_pin_is_never_told(h):
    greeted(h)
    h.say(ALICE, "الرمز")
    secret = h.graph.texts(ALICE)[-1]
    assert PIN not in secret and "ما نگدر نشوفه ولا نرجعه" in secret
    for gone in ("بدّل", "الحسابات", "صفّر المحفظة", "switch", "accounts", "reset"):
        h.say(ALICE, gone)
        assert "علي حسن" not in h.graph.everything(ALICE) and "4321" not in h.graph.everything(ALICE)
    assert h.balance(ALICE) == 500_000 and h.uid(ALICE) == "u1"


def test_help_command(h):
    greeted(h)
    h.say(ALICE, "مساعدة")
    reply = h.graph.texts(ALICE)[-1]
    assert "«إي، ادفع»" in reply and "ضيف أحمد 07701234567" in reply and "آخر العمليات" in reply
    assert "رمز التجربة" not in reply and "تجريب" not in reply and "وهمية" not in reply


# ---------------------------------------------------------------- guard rails


class PaidStub(StubCore):
    """The stand-in core, but every understand() call 'costs' one cent (reported as spent_usd)."""

    def __init__(self):
        super().__init__()
        self.spent_usd = 0.0
        self.calls = 0

    def understand(self, text, context=None):
        self.calls += 1
        self.spent_usd += 0.01
        return super().understand(text, context)


def test_daily_budget_cap_rests_until_tomorrow(tmp_path):
    core = PaidStub()
    harness = Harness(tmp_path, core=core, daily_budget_usd=0.015)
    try:
        greeted(harness)  # «هلا» is only greeted: no core call
        harness.say(ALICE, "شكد رصيدي")
        harness.say(ALICE, "شكد رصيدي")
        assert core.calls == 2
        harness.say(ALICE, "شكد رصيدي")
        harness.say(ALICE, "ادفع المولدة")
        assert core.calls == 2  # over the cap: no paid work at all
        rest = [t for t in harness.graph.texts(ALICE) if "مرتاحة لباجر" in t]
        assert len(rest) == 1  # said once, then quiet
        assert harness.channel.store.spent(harness.channel._day()) == pytest.approx(0.02)
        harness.restart()  # the day's spend survives a restart
        harness.say(ALICE, "شكد رصيدي")
        assert core.calls == 2
        harness.clock.advance(days=1)
        harness.say(ALICE, "شكد رصيدي")
        assert core.calls == 3 and "500,000" in harness.graph.texts(ALICE)[-1]
    finally:
        harness.stop()


def test_rate_limit_per_sender_per_hour(tmp_path):
    harness = Harness(tmp_path, max_msgs_per_user_hour=3)
    try:
        for _ in range(5):
            harness.say(ALICE, "شكد رصيدي")
        replies = harness.graph.texts(ALICE)
        assert sum("حد الرسائل" in t for t in replies) == 1
        assert len(replies) == 3 + 1  # three answers, one notice, then quiet
        harness.say(BOB, "شكد رصيدي")  # someone else is not limited
        assert "300,000" in harness.graph.texts(BOB)[-1]
        harness.clock.advance(hours=1, minutes=1)
        harness.say(ALICE, "شكد رصيدي")
        assert "500,000" in harness.graph.texts(ALICE)[-1]
    finally:
        harness.stop()


def test_allow_list(tmp_path):
    harness = Harness(tmp_path, allowed_numbers=frozenset({ALICE}))
    try:
        harness.say(BOB, "هلا")
        harness.say(ALICE, "هلا")
        assert harness.graph.texts(BOB) == [] and len(harness.graph.texts(ALICE)) == 1
    finally:
        harness.stop()


def test_old_backlog_is_dropped(h):
    message = kit.text(ALICE, "هلا", h.clock)
    message["timestamp"] = str(int(h.clock().timestamp()) - 3600)
    h.send(message)
    assert h.graph.sent == []


# ---------------------------------------------------------------- media


def test_unsupported_types_get_a_polite_reply(h):
    greeted(h)
    for kind in ("sticker", "image", "location", "video"):
        h.send(kit.other(ALICE, kind, h.clock))
    replies = h.graph.texts(ALICE)
    assert len(replies) == 4
    assert "ستيكر" in replies[0] and "صورة" in replies[1] and "الموقع" in replies[2] and "ما أگدر أفتحه" in replies[3]
    assert all("«ادفع المولدة»" in r for r in replies)
    h.send(kit.other(ALICE, "reaction", h.clock))
    assert len(h.graph.texts(ALICE)) == 4  # a reaction gets no answer


def test_voice_note_with_the_stand_in_core(h):
    greeted(h)
    h.graph.media_files["m1"] = (b"OggS-fake", "audio/ogg")
    h.send(kit.voice(ALICE, "m1", h.clock))
    assert "النسخة الكاملة من المساعد" in h.graph.texts(ALICE)[-1]


def test_voice_note_with_a_core_that_hears(tmp_path):
    class Hearing(StubCore):
        def __init__(self):
            super().__init__()
            self.got = None

        def transcribe(self, audio, mime, context):
            self.got = (audio, mime)
            return "ادفع المولدة"

    core = Hearing()
    harness = Harness(tmp_path, core=core)
    try:
        greeted(harness)
        harness.graph.media_files["m1"] = (b"OggS-fake", "audio/ogg; codecs=opus")
        harness.send(kit.voice(ALICE, "m1", harness.clock))
        assert core.got == (b"OggS-fake", "audio/ogg")
        card = harness.graph.last_card(ALICE)
        assert "سمعتج" not in card["body"]["text"]  # a card already says who and how much: no echo
        assert "مولدة الحجي جاسم" in card["body"]["text"]
        harness.send(kit.voice(ALICE, "missing", harness.clock))  # a download that fails
        assert "ما سمعت الرسالة الصوتية زين" in harness.graph.texts(ALICE)[-1]
    finally:
        harness.stop()


# ---------------------------------------------------------------- bursts and texts


def test_a_burst_of_texts_is_read_as_one_message(tmp_path):
    harness = Harness(tmp_path, debounce_seconds=0.6)
    try:
        greeted(harness)
        kit.post(harness.client, kit.webhook(kit.text(ALICE, "دز لهادي", harness.clock)))
        time.sleep(0.1)
        kit.post(harness.client, kit.webhook(kit.text(ALICE, "خمسين الف", harness.clock)))
        assert harness.channel.join(20)
        assert harness.graph.texts(ALICE) == []  # no «شكد؟» in between
        assert "50,000 دينار" in harness.graph.last_card(ALICE)["body"]["text"]
    finally:
        harness.stop()


def test_channel_texts_are_iraqi_and_fit_whatsapp():
    for text in texts.all_texts():
        assert "چ" not in text
    assert len(texts.say("card_footer", time="23:59")) <= 60
    for key in ("card_header_transfer", "card_header_bill", "card_header_topup"):
        assert len(texts.say(key)) <= 60
