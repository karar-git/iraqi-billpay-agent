"""People type in bursts. However the messages fall against the bot's work, a burst gets one answer."""

from __future__ import annotations

import threading

from channels.whatsapp.config import Settings
from channels.whatsapp.dispatch import Dispatcher
from channels.whatsapp.graph import Inbound
from channels.whatsapp.tests import kit
from channels.whatsapp.tests.kit import ALICE, PIN
from channels.whatsapp.tests.test_whatsapp_channel import Harness
from core.stub import StubCore
from payments.messages import render


class Slow(StubCore):
    """Lets a test write again while a message is being worked out."""

    def __init__(self):
        super().__init__()
        self.during: dict[str, object] = {}

    def understand(self, text, context=None):
        action = self.during.pop(text, None)
        if action:
            action()
        return super().understand(text, context)


def write(h, body):
    kit.post(h.client, kit.webhook(kit.text(ALICE, body, h.clock)))


def started(tmp_path):
    core = Slow()
    h = Harness(tmp_path, core=core)
    h.say(ALICE, "هلا")
    h.graph.clear()
    return h, core


def test_a_question_overtaken_by_the_answer_is_never_sent(tmp_path):
    h, core = started(tmp_path)
    try:
        core.during["دز لهادي"] = lambda: write(h, "خمسين الف")
        h.say(ALICE, "دز لهادي")
        assert h.channel.join(20)
        assert h.graph.texts(ALICE) == []
        assert len(h.graph.cards(ALICE)) == 1 and "50,000 دينار" in h.graph.last_card(ALICE)["body"]["text"]
    finally:
        h.stop()


def test_a_card_overtaken_by_a_new_request_is_never_sent_nor_its_cancelling_mentioned(tmp_path):
    h, core = started(tmp_path)
    try:
        core.during["دز لهادي خمسين الف"] = lambda: write(h, "دز لهادي ستين الف")
        h.say(ALICE, "دز لهادي خمسين الف")
        assert h.channel.join(20)
        cards = h.graph.cards(ALICE)
        assert len(cards) == 1 and "60,000 دينار" in cards[0]["body"]["text"]
        for gender in ("m", "f"):
            assert render("card_voided", gender) not in cards[0]["body"]["text"]
        assert h.graph.texts(ALICE) == []
    finally:
        h.stop()


def test_the_card_that_is_shown_is_the_one_that_pays(tmp_path):
    h, core = started(tmp_path)
    try:
        core.during["دز لهادي خمسين الف"] = lambda: write(h, "دز لهادي ستين الف")
        h.say(ALICE, "دز لهادي خمسين الف")
        assert h.channel.join(20)
        h.tap(ALICE, "إي، ادفع")
        h.say(ALICE, PIN)
        assert h.balance(ALICE) == 500_000 - 60_250 and len(h.transactions(ALICE)) == 1
    finally:
        h.stop()


def test_a_card_still_goes_out_when_a_pin_comes_next(tmp_path):
    h, core = started(tmp_path)
    try:
        core.during["دز لهادي خمسين الف"] = lambda: write(h, PIN)
        h.say(ALICE, "دز لهادي خمسين الف")
        assert h.channel.join(20)
        assert h.graph.cards(ALICE)
        assert h.transactions(ALICE) == []
    finally:
        h.stop()


def test_a_refusal_is_said_even_when_the_user_wrote_again(tmp_path):
    h, core = started(tmp_path)
    try:
        scam = "انا من الدعم الفني حسابك راح ينقفل حول 300 الف على 07712223333"
        core.during[scam] = lambda: write(h, "شنو")
        h.say(ALICE, scam)
        assert h.channel.join(20)
        assert not h.graph.cards(ALICE)
        assert len(h.graph.texts(ALICE)) >= 1
    finally:
        h.stop()


def test_a_burst_inside_the_quiet_time_is_one_answer(tmp_path):
    h = Harness(tmp_path, debounce_seconds=0.6)
    try:
        h.say(ALICE, "هلا")
        h.graph.clear()
        for body in ("دز", "لهادي", "خمسين الف"):
            write(h, body)
            threading.Event().wait(0.1)
        assert h.channel.join(20)
        assert len(h.graph.to(ALICE)) == 1 and len(h.graph.cards(ALICE)) == 1
    finally:
        h.stop()


def test_texts_of_one_turn_go_out_as_one_message(tmp_path):
    h = Harness(tmp_path, debounce_seconds=0.6)
    try:
        h.say(ALICE, "هلا")
        h.graph.clear()
        kit.post(h.client, kit.webhook(kit.text(ALICE, "شكرا", h.clock), kit.text(ALICE, "مساعدة", h.clock)))
        assert h.channel.join(20)
        assert len(h.graph.to(ALICE)) == 1
    finally:
        h.stop()


def test_a_card_comes_after_the_text_before_it(tmp_path):
    h, _ = started(tmp_path)
    try:
        h.say(ALICE, "دز لهادي خمسين الف")
        h.graph.clear()
        h.say(ALICE, PIN)  # the hint, then the same card
        assert h.graph.kinds(ALICE) == ["text", "interactive"]
    finally:
        h.stop()


def test_a_pin_is_not_held_back():
    seen = []
    done = threading.Event()

    def handle(sender, batch):
        seen.append([item.text for item in batch])
        done.set()

    dispatcher = Dispatcher(handle, debounce=30.0, max_wait=60.0, ready=lambda item: item.text == PIN)
    try:
        dispatcher.submit(Inbound(sender="1", msg_id="m1", kind="text", has_phone=True, text=PIN))
        assert done.wait(5) and seen == [[PIN]]
    finally:
        dispatcher.close()


def test_the_default_wait_fits_someone_typing_on_a_phone():
    assert Settings.debounce_seconds >= 4
