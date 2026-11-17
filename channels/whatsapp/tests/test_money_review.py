"""Money review: a restart after the wallet committed a payment, before the reply went out.

The PIN message stays in the inbox (store.done runs only after the turn), the restart replays it, the
sender's awaiting_pin is still the paid quote and the new session has no card. _guard answers «pin_stale»
with the stale_card text, which says nothing was taken, while the money did move."""

from __future__ import annotations

from dataclasses import asdict

import pytest

from channels.whatsapp.graph import Inbound
from channels.whatsapp.tests.kit import ALICE, PIN
from channels.whatsapp.tests.test_whatsapp_channel import Harness, greeted, open_card


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path)
    yield harness
    harness.stop()


def test_a_pin_replayed_after_a_restart_never_says_nothing_was_taken_when_it_was(h):
    greeted(h)
    open_card(h)
    h.tap(ALICE, "إي، ادفع")
    key = h.channel.sender_key(ALICE)
    qid = h.channel.store.sender(key)["awaiting_pin"]
    assert qid
    # The PIN message is taken (seen + inbox), the wallet commits, then the process dies before replying.
    now = h.clock().timestamp()
    item = Inbound(msg_id="wamid.pin-lost", sender=ALICE, has_phone=True, kind="text", text=PIN, button_id="",
                   media_id="", mime="", timestamp=now, name=None)
    assert h.channel.store.first_time(item.msg_id, now, asdict(item))
    paid = h.channel.wallet.execute(qid, PIN)
    assert paid["state"] == "SUCCEEDED"
    h.graph.clear()
    h.restart()  # create_app replays the inbox on start
    assert h.channel.join(20)
    said = h.graph.everything(ALICE)
    assert h.balance(ALICE) == 449_750
    assert "ما انخصم منج شي" not in said, f"told the payment took nothing after it was paid: {said!r}"


def test_a_pin_typed_in_the_chat_never_lands_on_the_volume(tmp_path):
    """The inbox keeps a PIN-bearing text without its text, and deleted rows are zeroed."""
    from channels.whatsapp.store import REDACTED, ChannelStore

    store = ChannelStore(tmp_path / "channel.db")
    raw = {"msg_id": "wamid.X", "sender": ALICE, "has_phone": True, "kind": "text", "text": "48264826",
           "button_id": "", "media_id": "", "mime": "", "timestamp": 1.0, "name": None}
    assert store.first_time("wamid.X", 1.0, raw)
    assert store.pending(0)[0]["kind"] == REDACTED and store.pending(0)[0]["text"] == ""
    store.first_time("wamid.Y", 1.0, {**raw, "msg_id": "wamid.Y", "text": "شكد رصيدي"})
    assert any(item["text"] == "شكد رصيدي" for item in store.pending(0))  # other texts are still answered
    store.done(["wamid.X", "wamid.Y"])
    store.close()
    blob = b"".join(p.read_bytes() for p in tmp_path.iterdir() if p.name.startswith("channel.db"))
    assert b"48264826" not in blob
    assert "شكد رصيدي".encode() not in blob
