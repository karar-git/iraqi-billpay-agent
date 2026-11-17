"""Someone with no account yet also types in bursts: one answer for the burst, and only the question still open."""

from __future__ import annotations

from channels.whatsapp import texts
from channels.whatsapp.tests import kit
from channels.whatsapp.tests.kit import ALICE
from channels.whatsapp.tests.test_accounts_channel import CAROL, GOOD_PIN, sign_up, tap_id
from channels.whatsapp.tests.test_whatsapp_channel import Harness
from accounts import phones


def burst(h, who, *bodies):
    h.send(*[kit.text(who, body, h.clock) for body in bodies])


def test_a_burst_from_a_new_number_gets_one_offer(tmp_path):
    h = Harness(tmp_path, debounce_seconds=0.3)
    try:
        burst(h, CAROL, "هلا", "شلونكم", "اريد ادز فلوس", "لو سمحت", "وينكم")
        assert len(h.graph.to(CAROL)) == 1
        assert len(h.graph.cards(CAROL)) == 1
    finally:
        h.stop()


def test_a_burst_at_an_offer_already_made_gets_one_message(tmp_path):
    h = Harness(tmp_path, debounce_seconds=0.3)
    try:
        h.say(CAROL, "هلا")
        h.graph.clear()
        burst(h, CAROL, "شلونكم", "اريد ادز فلوس", "وينكم")
        assert len(h.graph.to(CAROL)) == 1
    finally:
        h.stop()


def test_a_yes_inside_the_burst_still_opens_the_account(tmp_path):
    h = Harness(tmp_path, debounce_seconds=0.3)
    try:
        burst(h, CAROL, "هلا", "افتح حساب")
        assert len(h.graph.to(CAROL)) == 1
        assert h.channel.store.onboarding(h.channel.sender_key(CAROL))["step"] != "offer"
    finally:
        h.stop()


def test_an_answered_question_is_not_asked_again(tmp_path):
    h = Harness(tmp_path, debounce_seconds=0.3)
    try:
        h.say(CAROL, "هلا")
        tap_id(h, CAROL, "o:open")
        step = h.channel.store.onboarding(h.channel.sender_key(CAROL))["step"]
        if step == "name_confirm":
            tap_id(h, CAROL, "o:name_other")
        h.graph.clear()
        burst(h, CAROL, "سلمى كريم", "انثى")  # the name, then the gender, in one go
        row = h.channel.store.onboarding(h.channel.sender_key(CAROL))
        assert row["step"] == "pin" and row["gender"] == "f"
        assert len(h.graph.to(CAROL)) == 1 and not h.graph.cards(CAROL)  # no gender buttons: it was answered
    finally:
        h.stop()


def test_the_same_question_is_said_once(tmp_path):
    h = Harness(tmp_path, debounce_seconds=0.3)
    try:
        h.say(CAROL, "هلا")
        tap_id(h, CAROL, "o:open")
        step = h.channel.store.onboarding(h.channel.sender_key(CAROL))["step"]
        if step == "name_confirm":
            tap_id(h, CAROL, "o:name_other")
        burst(h, CAROL, "سلمى كريم")
        h.graph.clear()
        burst(h, CAROL, "ما ادري", "شنو هذا", "هاا")  # not answers: the gender question, once
        assert len(h.graph.to(CAROL)) == 1 and len(h.graph.cards(CAROL)) == 1
    finally:
        h.stop()


def test_a_pin_typed_twice_in_a_burst_finishes_the_sign_up(tmp_path):
    h = Harness(tmp_path, debounce_seconds=0.3)
    try:
        h.say(CAROL, "هلا")
        tap_id(h, CAROL, "o:open")
        step = h.channel.store.onboarding(h.channel.sender_key(CAROL))["step"]
        if step == "name_confirm":
            tap_id(h, CAROL, "o:name_other")
        h.say(CAROL, "سلمى كريم")
        tap_id(h, CAROL, "o:f")
        h.graph.clear()
        burst(h, CAROL, GOOD_PIN, GOOD_PIN)
        assert h.channel.accounts.by_phone(phones.canonical(CAROL)) is not None
        assert len(h.graph.to(CAROL)) == 2  # a PIN is not held back: each is its own step
        assert GOOD_PIN not in h.graph.everything(CAROL)
    finally:
        h.stop()


def test_sign_up_one_message_at_a_time_still_works(tmp_path):
    h = Harness(tmp_path, debounce_seconds=0.3)
    try:
        sign_up(h, CAROL)
        assert h.channel.accounts.by_phone(phones.canonical(CAROL)) is not None
    finally:
        h.stop()


def test_someone_with_an_account_is_unaffected(tmp_path):
    h = Harness(tmp_path, debounce_seconds=0.3)
    try:
        h.say(ALICE, "هلا")
        h.graph.clear()
        burst(h, ALICE, "دز لهادي", "خمسين الف")
        assert len(h.graph.to(ALICE)) == 1 and len(h.graph.cards(ALICE)) == 1
    finally:
        h.stop()


def test_english_at_an_old_arabic_offer_is_answered_in_english(tmp_path):
    h = Harness(tmp_path, debounce_seconds=0.3)
    try:
        h.say(CAROL, "هلا")
        h.graph.clear()
        burst(h, CAROL, "hey", "hey", "whats up")
        body = h.graph.cards(CAROL)[-1]["body"]["text"]
        assert len(h.graph.to(CAROL)) == 1 and "This number has no account" in body
        assert not any("\u0600" <= ch <= "\u06ff" for ch in body)
        assert list(kit.card_buttons(h.graph.cards(CAROL)[-1])) == ["Open account", "Not now"]
    finally:
        h.stop()


def test_midnight_is_not_the_morning():
    assert texts.hello(0, "en") == texts.hello(23, "en") != texts.hello(9, "en")
    assert texts.hello(4, "iq") == "مساء الخير" and texts.hello(5, "iq") == "صباح الخير"
