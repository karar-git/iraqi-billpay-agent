"""Real accounts on WhatsApp, end to end: sign-up in three languages, recognition by number, contacts, transfers
to registered and unregistered numbers, bills, history, the PIN (set, verify, lock, block, reset, change),
notices, Meta retries and double taps. Offline: fake Graph, the open StubCore, a hand-moved clock."""

from __future__ import annotations

import logging

import pytest

from channels.whatsapp.tests import kit
from channels.whatsapp.tests.kit import ALICE, BOB, PIN
from channels.whatsapp.tests.test_whatsapp_channel import Harness
from core.stub import StubCore

CAROL = "9647700000003"  # no account yet
DAN = "9647700000004"  # no account yet
GOOD_PIN = "2583"


class Spy(StubCore):
    def __init__(self):
        super().__init__()
        self.heard: list[str] = []
        self.contexts: list[dict] = []

    def understand(self, text, context=None):
        self.heard.append(text)
        self.contexts.append(dict(context or {}))
        return super().understand(text, context)


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path, core=Spy())
    yield harness
    harness.stop()


def tap_id(h: Harness, who: str, button_id: str, title: str = "x") -> None:
    h.send(kit.tap(who, button_id, title, h.clock))


def sign_up(h: Harness, who: str, *, first: str = "هلا", gender: str = "o:f", pin: str = GOOD_PIN) -> None:
    h.say(who, first)
    tap_id(h, who, "o:open")
    tap_id(h, who, "o:name_yes")  # the WhatsApp profile name of the kit's webhooks: «tester»
    tap_id(h, who, gender)
    h.say(who, pin)
    h.say(who, pin)


def card_text(h: Harness, who: str) -> str:
    return h.graph.last_card(who)["body"]["text"]


def pay(h: Harness, who: str, pin: str) -> None:
    h.tap(who, h.graph.last_card(who)["action"]["buttons"][0]["reply"]["title"])
    h.say(who, pin)


# ---------------------------------------------------------------- sign-up


def test_sign_up_in_iraqi_from_offer_to_an_account(h):
    h.say(CAROL, "هلا")
    offer = h.graph.cards(CAROL)[-1]
    assert "هذا الرقم ما عليه حساب" in offer["body"]["text"]
    assert "رصيد داخل الخدمة" in offer["body"]["text"]  # balances and payments stay inside the service
    assert "تجريبية" not in offer["body"]["text"]  # a real account, not a trial
    assert list(kit.card_buttons(offer)) == ["افتح حساب", "مو هسه"]
    tap_id(h, CAROL, "o:open")
    assert "أسجلك باسم «tester»" in h.graph.cards(CAROL)[-1]["body"]["text"]
    tap_id(h, CAROL, "o:name_other")
    h.say(CAROL, "زينب 2")
    assert "بلا أرقام" in h.graph.texts(CAROL)[-1]
    h.say(CAROL, "زينب علي")
    tap_id(h, CAROL, "o:f")
    assert "اختاري رمز سري من 4 إلى 6 أرقام" in h.graph.texts(CAROL)[-1]
    h.say(CAROL, "1234")
    assert "سهل ينحزر" in h.graph.texts(CAROL)[-1]
    h.say(CAROL, "0003")  # the end of her own number
    assert "سهل ينحزر" in h.graph.texts(CAROL)[-1]
    h.say(CAROL, GOOD_PIN)
    h.say(CAROL, "9753")
    assert "الرمزين مو نفس الشي" in h.graph.texts(CAROL)[-1]
    h.say(CAROL, GOOD_PIN)
    h.say(CAROL, GOOD_PIN)
    done = h.graph.texts(CAROL)[-1]
    assert done.startswith("تم فتح حسابج ✅ رقم حسابج هو رقم واتسابج 0770 000 0003. رصيدج 0 دينار.")
    user = h.channel.accounts.by_phone(CAROL)
    assert user["name"] == "زينب علي" and user["gender"] == "f" and user["status"] == "active"
    assert user["pin_state"] == "set" and user["pin_hash"].startswith("pbkdf2_sha256p$")
    assert GOOD_PIN not in user["pin_hash"]
    assert all("چ" not in t for t in h.graph.texts(CAROL))
    assert h.channel.store.onboarding(h.channel.sender_key(CAROL)) is None
    h.say(CAROL, "شكد رصيدي")
    assert h.graph.texts(CAROL)[-1] == "رصيدج 0 دينار."


@pytest.mark.parametrize("first, offer_start, done_start", [
    ("Hello there", "Good morning! I'm the smart payment agent", "Your account is open ✅"),
    ("السلام عليكم، أود فتح حساب من فضلكم", "صباح الخير! أنا وكيل الدفع الذكي", "تم فتح حسابك ✅"),
])
def test_sign_up_follows_the_first_message_language(h, first, offer_start, done_start):
    h.say(CAROL, first)
    assert h.graph.cards(CAROL)[-1]["body"]["text"].startswith(offer_start)
    tap_id(h, CAROL, "o:open")
    tap_id(h, CAROL, "o:name_yes")
    tap_id(h, CAROL, "o:m")
    h.say(CAROL, GOOD_PIN)
    h.say(CAROL, GOOD_PIN)
    assert h.graph.texts(CAROL)[-1].startswith(done_start)
    assert h.channel.accounts.by_phone(CAROL)["language"] in ("en", "msa")


def test_typed_answers_work_too_and_not_now_and_cancel(h):
    h.say(CAROL, "هلا")
    h.say(CAROL, "مو هسه")
    assert "اكتب «افتح حساب»" in h.graph.texts(CAROL)[-1]
    assert h.channel.accounts.by_phone(CAROL) is None
    h.say(CAROL, "افتح حساب")  # straight to the name
    h.say(CAROL, "إي")
    h.say(CAROL, "الغاء")
    assert "لغيت فتح الحساب" in h.graph.texts(CAROL)[-1]
    sign_up(h, CAROL, gender="o:m")
    assert h.channel.accounts.by_phone(CAROL)["gender"] == "m"


def test_a_request_mid_sign_up_is_asked_to_wait_and_a_voice_note_to_be_typed(h):
    h.say(CAROL, "هلا")
    h.graph.clear()
    h.say(CAROL, "ادفع المولدة")
    assert "هذا الرقم ما عليه حساب" in h.graph.cards(CAROL)[-1]["body"]["text"]
    assert "خلي نكمل" not in h.graph.everything(CAROL)  # nothing was begun, so nothing is left to finish
    h.graph.media_files["v1"] = (b"OggS", "audio/ogg")
    h.send(kit.voice(CAROL, "v1", h.clock))
    assert "اكتبها كتابة" in h.graph.cards(CAROL)[-1]["body"]["text"]
    assert h.channel.core.heard == []  # nothing of a sign-up reaches the core


def test_a_restart_mid_sign_up_resumes_and_an_idle_one_restarts(h):
    h.say(CAROL, "هلا")
    tap_id(h, CAROL, "o:open")
    tap_id(h, CAROL, "o:name_yes")
    h.restart()
    tap_id(h, CAROL, "o:f")
    assert "اختاري رمز سري" in h.graph.texts(CAROL)[-1]
    h.clock.advance(hours=25)
    h.say(CAROL, GOOD_PIN)
    assert "هذا الرقم ما عليه حساب" in h.graph.cards(CAROL)[-1]["body"]["text"]  # offered again


def test_a_double_tap_on_the_last_step_makes_one_account(h):
    h.say(CAROL, "هلا")
    tap_id(h, CAROL, "o:open")
    tap_id(h, CAROL, "o:name_yes")
    tap_id(h, CAROL, "o:f")
    h.say(CAROL, GOOD_PIN)
    h.send(kit.text(CAROL, GOOD_PIN, h.clock), kit.text(CAROL, GOOD_PIN, h.clock))  # sent twice at once
    with h.channel.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users WHERE phone_e164 = ?", (CAROL,)).fetchone()[0] == 1


def test_a_username_only_sender_cannot_open_an_account(h):
    body = kit.webhook()
    body["entry"][0]["changes"][0]["value"]["messages"] = [
        {"from_user_id": "IQ.123", "id": "wamid.u1", "timestamp": str(int(h.clock().timestamp())), "type": "text",
         "text": {"body": "هلا"}}]
    with h.channel.db.connect() as conn:
        before = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    kit.post(h.client, body)
    assert h.channel.join(20)
    assert "نحتاج رقم موبايل" in h.graph.texts("IQ.123")[-1]
    with h.channel.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == before


def test_signup_modes_invite_and_closed(tmp_path):
    for mode, words in (("invite", "بالدعوة بس"), ("closed", "متوقف هسه")):
        harness = Harness(tmp_path / mode, signup_mode=mode, manager=BOB)
        try:
            harness.say(CAROL, "هلا")
            assert words in harness.graph.texts(CAROL)[-1]
            assert harness.channel.accounts.by_phone(CAROL) is None
            if mode == "invite":
                assert any("0770 000 0003" in t for t in harness.graph.texts(BOB))
        finally:
            harness.stop()


def test_an_invited_person_only_sets_a_pin_and_keeps_the_opening_balance(h):
    h.channel.wallet.invite(CAROL, "زينب علي", gender="f", opening=150_000, op_id="op-open", actor="admin:test")
    h.say(CAROL, "هلا")
    assert h.graph.texts(CAROL)[-1].startswith("صباح الخير زينب! فتحنالج حساب على هذا الرقم ورصيدج 150,000 دينار.")
    h.say(CAROL, GOOD_PIN)
    h.say(CAROL, GOOD_PIN)
    assert "تم فتح حسابج" in h.graph.texts(CAROL)[-1] and "150,000" in h.graph.texts(CAROL)[-1]
    assert h.channel.accounts.by_phone(CAROL)["status"] == "active"


def test_the_pin_typed_during_sign_up_never_reaches_logs_core_inbox_or_audit(h, caplog):
    caplog.set_level(logging.DEBUG)
    sign_up(h, CAROL, pin="8642")
    assert "8642" not in caplog.text
    assert not any("8642" in s for s in h.channel.core.heard)
    assert h.channel.store.pending(0) == []  # the inbox keeps nothing once a turn is answered
    with h.channel.db.connect() as conn:
        assert not any("8642" in r[0] for r in conn.execute("SELECT details_json FROM audit_log"))


# ---------------------------------------------------------------- recognition


def test_a_returning_number_is_the_same_account_across_restarts_and_evictions(h):
    sign_up(h, CAROL)
    uid = h.channel.accounts.by_phone(CAROL)["id"]
    h.restart()
    h.say(CAROL, "هلا")
    assert h.graph.texts(CAROL)[-1].startswith("صباح الخير tester! رصيدج 0 دينار")
    h.channel._open.clear()  # every open conversation evicted
    h.say(CAROL, "شكد رصيدي")
    assert h.channel.accounts.by_phone(CAROL)["id"] == uid
    assert h.channel.accounts.get(uid)["last_inbound_at"]


# ---------------------------------------------------------------- contacts and transfers to numbers


def test_add_a_contact_by_number_list_it_and_pay_it(h):
    h.say(ALICE, "ضيف علي 0770 000 0002")
    said = h.graph.texts(ALICE)[-1]
    assert said.startswith("حفظت علي (0770 000 0002).") and "التحويل إله يوصل فوراً" in said
    h.say(ALICE, "أسمائي")
    assert "علي · …0002 ✓" in h.graph.texts(ALICE)[-1]
    h.say(ALICE, "ضيف علي 0770 000 0002")
    assert "محفوظ من قبل" in h.graph.texts(ALICE)[-1]
    h.say(ALICE, "دز لعلي عشرين الف")
    assert "علي" in card_text(h, ALICE) and "0002" in card_text(h, ALICE)
    pay(h, ALICE, PIN)
    assert h.balance(BOB) == 320_000
    assert not any("0770" in s or "0000002" in s for s in h.channel.core.heard)  # the core never saw the number


def test_transfer_to_a_registered_number_credits_at_once_notifies_and_saves_the_contact(h):
    h.say(BOB, "هلا")  # Bob wrote today: his window is open
    h.say(ALICE, "دز 25 الف لـ 0770 000 0002")
    body = card_text(h, ALICE)
    assert "علي ح." in body and "0002" in body  # the registered short name, never the full name
    assert "علي حسن" not in body
    pay(h, ALICE, PIN)
    receipt = h.graph.texts(ALICE)[-1]
    assert "حفظت الرقم بأسمائج باسم «علي ح.»" in receipt
    assert h.balance(ALICE) == 500_000 - 25_250 and h.balance(BOB) == 325_000
    assert "وصلك 25,000 دينار من سارة ك. (…0001)." in h.graph.texts(BOB)[-1]
    assert "رصيدك هسه 325,000 دينار." in h.graph.texts(BOB)[-1]
    contacts = h.channel.accounts.contacts("u1")
    assert any(c["phone_e164"] == BOB and c["source"] == "auto_transfer" for c in contacts)
    assert all("0000002" not in s for s in h.channel.core.heard)
    assert any(c.get("phone_tokens") == ["هاتف_ا"] for c in h.channel.core.contexts)
    assert h.wallet(ALICE).audit()["ok"]


def test_a_notice_outside_the_window_waits_for_the_next_message(h):
    h.say(ALICE, "دز 10 الاف لـ 07700000002")
    pay(h, ALICE, PIN)
    assert h.graph.texts(BOB) == []  # Bob never wrote: no window, the notice waits
    h.say(BOB, "شكد رصيدي")
    first, last = h.graph.texts(BOB)[0], h.graph.texts(BOB)[-1]
    assert "وصلك 10,000 دينار من سارة ك. (…0001)." in first
    assert last == "رصيدك 310,000 دينار."
    h.say(BOB, "شكد رصيدي")
    assert sum("وصلك 10,000" in t for t in h.graph.texts(BOB)) == 1  # exactly once


def test_transfer_to_an_unregistered_number_is_held_then_claimed_at_sign_up(h):
    h.say(ALICE, "دز عشرة الاف لـ 0770 000 0003")
    body = card_text(h, ALICE)
    assert "0770 000 0003" in body and "ما عنده حساب بعد: المبلغ ينحجز إله 7 أيام" in body
    pay(h, ALICE, PIN)
    receipt = h.graph.texts(ALICE)[-1]
    assert "انحجز 10,000 دينار للرقم المنتهي بـ 0003" in receipt and "https://wa.me/9647714899059?text=" in receipt
    assert h.balance(ALICE) == 500_000 - 10_250
    [hold] = h.channel.wallet.holds(state="HELD")
    assert hold["phone_e164"] == CAROL and hold["amount"] == 10_000
    h.say(ALICE, "هلا")  # Alice is inside her window when Carol signs up
    sign_up(h, CAROL)
    done = h.graph.texts(CAROL)[-1]
    assert "وصلتج تحويلات كانت محجوزة لرقمج" in done and "10,000 دينار من سارة ك. (…0001)" in done
    assert h.channel.accounts.by_phone(CAROL)["balance"] == 10_000
    assert "فتح حساب واستلم 10,000 دينار" in h.graph.texts(ALICE)[-1]
    assert h.wallet(ALICE).audit()["ok"]


def test_a_hold_nobody_claims_comes_back_with_the_fee(h):
    from channels.whatsapp.app import run_jobs

    h.say(ALICE, "دز عشرة الاف لـ 07700000004")
    pay(h, ALICE, PIN)
    h.clock.advance(days=8)
    assert run_jobs(h.channel)["holds_expired"] == 1
    assert h.balance(ALICE) == 500_000
    h.say(ALICE, "آخر العمليات")
    assert "رجعة تحويل محجوز +10,250" in h.graph.texts(ALICE)[-1] or "رجعتلك 10,250" in h.graph.everything(ALICE)


def test_transfer_to_own_number_and_a_foreign_number_are_refused(h):
    h.say(ALICE, "دز عشرين الف لـ 07700000001")
    assert "هذا رقمج إنتِ" in h.graph.texts(ALICE)[-1]
    h.say(ALICE, "ضيف أحمد 00441234567890")
    assert "مو رقم موبايل عراقي" in h.graph.texts(ALICE)[-1]
    assert h.transactions(ALICE) == []


def test_a_phone_number_is_never_read_as_an_amount(h):
    h.say(ALICE, "دز لـ 07700000002 خمسين")
    assert "50,000 دينار" in card_text(h, ALICE)


def test_a_number_alone_offers_save_or_send(h):
    h.say(ALICE, "07700000003")
    card = h.graph.cards(ALICE)[-1]
    assert "تريدين تحفظينه لو تحوّليله فلوس" in card["body"]["text"]
    tap_id(h, ALICE, "m:send:")
    assert "شكد تريدين تدزين" in h.graph.texts(ALICE)[-1]
    h.say(ALICE, "خمسة الاف")
    assert "ما عنده حساب بعد" in card_text(h, ALICE)


def test_insufficient_balance_and_the_daily_limit_refuse(h):
    h.say(ALICE, "دز مليون لـ 07700000002")
    assert "يعبر حدج اليومي" in h.graph.texts(ALICE)[-1] or "رصيدج ما يكفي" in h.graph.texts(ALICE)[-1]
    h.say(ALICE, "دز 600 الف لـ 07700000002")
    assert "رصيدج ما يكفي" in h.graph.texts(ALICE)[-1]
    assert h.transactions(ALICE) == []


# ---------------------------------------------------------------- the PIN


def test_five_wrong_pins_lock_and_the_third_lockout_blocks_until_an_admin_reset(h):
    sign_up(h, CAROL)
    uid = h.channel.accounts.by_phone(CAROL)["id"]
    h.channel.wallet.admin_credit(uid, 300_000, "test credit", op_id="op-c", actor="admin:test")
    for round_ in range(3):
        h.say(CAROL, "دز 5 الاف لـ 07700000002")
        h.tap(CAROL, "إي، ادفع")
        for _ in range(5):
            h.say(CAROL, "1111")
        if round_ < 2:
            assert "انقفل الرمز السري ربع ساعة" in h.graph.texts(CAROL)[-1]
            h.clock.advance(minutes=16)
    assert h.channel.accounts.get(uid)["pin_state"] == "blocked"
    h.say(CAROL, "دز 5 الاف لـ 07700000002")
    assert "الرمز السري مقفول" in h.graph.texts(CAROL)[-1]
    h.channel.wallet.reset_pin(uid, actor="admin:test", reason="checked by phone")
    h.say(CAROL, "هلا")
    assert "صفّرنا رمزج السري" in h.graph.texts(CAROL)[-1]
    h.say(CAROL, "9753")
    h.say(CAROL, "9753")
    assert "انحفظ رمزج الجديد" in h.graph.texts(CAROL)[-1]
    h.say(CAROL, "دز 150 الف لـ 07700000002")  # over the 24 h cap after a reset
    assert "أول 24 ساعة تگدرين تدزين لحد 100,000 دينار" in h.graph.texts(CAROL)[-1]
    h.say(CAROL, "دز 50 الف لـ 07700000002")
    pay(h, CAROL, "9753")
    assert h.balance(BOB) == 350_000


def test_change_pin_and_forgot_pin(h):
    sign_up(h, CAROL)
    uid = h.channel.accounts.by_phone(CAROL)["id"]
    h.say(CAROL, "غيّر الرمز")
    h.say(CAROL, "1111")
    assert "الرمز الحالي غلط" in h.graph.texts(CAROL)[-1]
    h.say(CAROL, GOOD_PIN)
    h.say(CAROL, "1234")
    assert "سهل ينحزر" in h.graph.texts(CAROL)[-1]
    h.say(CAROL, "9753")
    h.say(CAROL, "9753")
    assert h.graph.texts(CAROL)[-1] == "تغيّر الرمز ✅"
    assert not any(p in s for s in h.channel.core.heard for p in (GOOD_PIN, "9753", "1111"))
    h.say(CAROL, "نسيت الرمز")
    tap_id(h, CAROL, "m:forgot:yes")
    assert "انطلب ✅" in h.graph.texts(CAROL)[-1]
    assert [r["user_id"] for r in h.channel.accounts.open_requests()] == [uid]


# ---------------------------------------------------------------- idempotency


def test_meta_retries_and_double_taps_pay_once(h):
    h.say(ALICE, "دز 25 الف لـ 07700000002")
    card = h.graph.last_card(ALICE)
    confirm = kit.card_buttons(card)["إي، ادفع"]
    tap = kit.tap(ALICE, confirm, "إي، ادفع", h.clock, msg_id="wamid.tap1")
    h.send(tap)
    h.send(tap)  # Meta retries the tap
    pin = kit.text(ALICE, PIN, h.clock, msg_id="wamid.pin1")
    h.send(pin)
    h.send(pin)  # and the PIN message
    h.send(kit.tap(ALICE, confirm, "إي، ادفع", h.clock))  # a second real tap on the paid card
    h.say(ALICE, PIN)
    assert h.balance(ALICE) == 500_000 - 25_250 and h.balance(BOB) == 325_000
    assert len(h.transactions(ALICE)) == 1
    assert h.wallet(ALICE).audit()["ok"]


# ---------------------------------------------------------------- bills and history


def test_add_a_bill_account_get_the_mock_bill_and_pay_it(h):
    sign_up(h, CAROL)
    uid = h.channel.accounts.by_phone(CAROL)["id"]
    h.channel.wallet.admin_credit(uid, 200_000, "test credit", op_id="op-b", actor="admin:test")
    h.say(CAROL, "ضيف حساب المولدة 350")
    confirm = h.graph.cards(CAROL)[-1]
    assert "أحفظ حساب مولدة الحجي جاسم" in confirm["body"]["text"] and "رقم 350؟" in confirm["body"]["text"]
    tap_id(h, CAROL, "m:bill:yes")
    saved = h.graph.texts(CAROL)[-1]
    assert saved.startswith("انضاف ✅ عليه فاتورة تشرين الثاني 2026:") and "«ادفع المولدة»" in saved
    h.say(CAROL, "فواتيري")
    assert "المولدة (مولدة الحجي جاسم)" in h.graph.texts(CAROL)[-1] and ") رقم 350" in h.graph.texts(CAROL)[-1]
    h.say(CAROL, "ادفع المولدة")
    assert "مولدة الحجي جاسم" in card_text(h, CAROL)
    pay(h, CAROL, GOOD_PIN)
    assert "ولسه ما توصل للجهة" in h.graph.texts(CAROL)[-1]
    h.say(CAROL, "آخر العمليات")
    history = h.graph.texts(CAROL)[-1]
    assert history.startswith("آخر العمليات:") and "فاتورة مولدة الحجي جاسم" in history
    assert "إيداع من الإدارة +200,000" in history and "اليوم صرفتي" in history


def test_a_bad_bill_account_number_is_asked_again(h):
    h.say(ALICE, "ضيف حساب كهرباء 12")
    assert "رقم الحساب مو صحيح" in h.graph.texts(ALICE)[-1]
    h.say(ALICE, "2024939794")
    assert "أحفظ حساب كهرباء الوطنية رقم 2024939794؟" in h.graph.cards(ALICE)[-1]["body"]["text"]


def test_history_shows_money_in_and_out_and_my_account(h):
    h.say(ALICE, "دز 25 الف لـ 07700000002")
    pay(h, ALICE, PIN)
    h.say(BOB, "كشف")
    assert "وصلك من سارة ك. +25,000" in h.graph.texts(BOB)[-1]
    h.say(ALICE, "حسابي")
    mine = h.graph.texts(ALICE)[-1]
    assert "الرقم: 0770 000 0001" in mine and "الرصيد: 474,750 دينار" in mine


# ---------------------------------------------------------------- voice


class Hearing(StubCore):
    """A core that hears and understands in one step (HearingCore)."""

    def __init__(self, transcript: str):
        super().__init__()
        self.transcript = transcript
        self.contexts: list[dict] = []
        self.understood: list[str] = []

    def transcribe(self, audio, mime, context):  # pragma: no cover - hear() is preferred
        raise AssertionError("hear() should be used")

    def hear(self, audio, mime, context):
        self.contexts.append(dict(context))
        return self.transcript, StubCore.understand(self, self.transcript, context)

    def understand(self, text, context=None):
        self.understood.append(text)
        return super().understand(text, context)


def test_a_voice_note_is_heard_with_the_users_vocabulary_and_no_phone_numbers(tmp_path):
    core = Hearing("دز لهادي خمسين الف")
    harness = Harness(tmp_path, core=core)
    try:
        harness.graph.media_files["v1"] = (b"OggS", "audio/ogg")
        harness.send(kit.voice(ALICE, "v1", harness.clock))
        [context] = core.contexts
        assert context["source"] == "voice"
        assert "هادي" in context["vocabulary"]["contacts"] and "المولدة" in context["vocabulary"]["bill_labels"]
        assert not any(ch.isdigit() for ch in repr(context["vocabulary"]))  # names, never numbers
        assert core.understood == []  # the heard reading is used, not a second call
        assert "50,000 دينار" in harness.graph.last_card(ALICE)["body"]["text"]
        assert "سمعتج" not in harness.graph.last_card(ALICE)["body"]["text"]  # a card needs no echo
    finally:
        harness.stop()


def test_a_misheard_name_close_to_one_contact_gets_the_card_and_a_question_gets_the_echo(tmp_path):
    core = Hearing("دز لهاضي خمسين الف")  # «هادي» heard slightly wrong
    harness = Harness(tmp_path, core=core)
    try:
        harness.graph.media_files["v1"] = (b"OggS", "audio/ogg")
        harness.send(kit.voice(ALICE, "v1", harness.clock))
        assert "هادي" in harness.graph.last_card(ALICE)["body"]["text"]
        core.transcript = "دز خمسين الف"
        harness.graph.media_files["v2"] = (b"OggS", "audio/ogg")
        harness.send(kit.voice(ALICE, "v2", harness.clock))
        assert harness.graph.texts(ALICE)[-1].startswith("سمعتج تگولين: «دز خمسين الف»")  # a question: echoed
    finally:
        harness.stop()


def test_a_pin_said_in_a_voice_note_during_change_pin_is_refused(tmp_path):
    core = Hearing("2583")
    harness = Harness(tmp_path, core=core)
    try:
        harness.say(ALICE, "غيّر الرمز")
        harness.graph.media_files["v1"] = (b"OggS", "audio/ogg")
        harness.send(kit.voice(ALICE, "v1", harness.clock))
        assert "كتابة رجاءً" in harness.graph.texts(ALICE)[-1]
        assert core.contexts == []  # the audio was never sent to be heard
    finally:
        harness.stop()


# ---------------------------------------------------------------- the public surface


def test_the_wallet_routes_are_never_public_and_admin_is_404_without_a_password(h):
    for path in ("/users/u1", "/quotes/x", "/admin/faults", "/admin/audit", "/admin", "/admin/users"):
        assert h.client.get(path).status_code == 404, path
    assert h.client.post("/quotes", json={"user_id": "u1", "kind": "transfer"}).status_code in (404, 405)


def test_no_demo_words_are_left_in_any_reply(h):
    sign_up(h, CAROL)
    h.say(CAROL, "مساعدة")
    h.say(ALICE, "دز 25 الف لـ 07700000002")
    pay(h, ALICE, PIN)
    for who in (ALICE, CAROL):
        said = h.graph.everything(who)
        for word in ("تجريبي", "وهمية", "رمز التجربة", "demo", "pretend"):
            if word == "تجريبي":
                said = said.replace("بمرحلة تجريبية", "")  # the pilot line is the truth about bills, not a demo
            assert word not in said, (who, word)


# ---------------------------------------------------------------- the move from the demo


def test_the_demo_senders_and_wallets_are_dropped_on_the_first_start(tmp_path):
    import sqlite3

    from channels.whatsapp.store import SCHEMA

    root = tmp_path / "whatsapp"
    (root / "wallets").mkdir(parents=True)
    (root / "wallets" / "abc.db").write_bytes(b"old synthetic wallet")
    conn = sqlite3.connect(root / "channel.db")
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO senders (key, user_id, greeted, created) VALUES ('abc', 'u03', 1, 0)")
    conn.execute("INSERT INTO seen (msg_id, ts) VALUES ('wamid.old', 0)")
    conn.commit()
    conn.close()
    harness = Harness(tmp_path)
    try:
        assert harness.channel.store.sender("abc") is None  # the demo user mapping is gone
        assert harness.channel.store.dropped_demo == 1
        assert not (root / "wallets").exists() and list(root.glob("wallets.dropped-*/abc.db"))
        assert not harness.channel.store.first_time("wamid.old", 1.0)  # dedup is kept
    finally:
        harness.stop()
    harness = Harness(tmp_path)  # a second start changes nothing
    try:
        assert list(root.glob("wallets.dropped-*/abc.db"))
    finally:
        harness.stop()

