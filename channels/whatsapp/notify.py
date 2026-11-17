"""Notices to account holders (money received, holds claimed or refunded, admin actions), delivered on
WhatsApp only inside the person's 24-hour window.

The wallet writes a notice into the outbox in the same transaction as the money (wallet.api). A notice goes
out at once when the person wrote to us in the last 23 h 50 min; otherwise it waits and goes out as the
first lines of their next conversation. Messaging outside the window needs a Meta-approved template and a
payment method on the business account: not part of this build (the outbox has the shape for it).
A notice that fails to send stays pending and is tried again on the next sweep. Never a PIN.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from accounts import phones
from accounts import texts as T

if TYPE_CHECKING:
    from channels.whatsapp.channel import Channel

log = logging.getLogger("whatsapp.notify")

KEYS = {"transfer_in": "n_transfer_in", "hold_claimed": "n_hold_claimed", "hold_refunded": "n_hold_refunded",
        "admin_credit": "n_admin_credit", "admin_debit": "n_admin_debit", "frozen": "n_frozen",
        "unfrozen": "n_unfrozen"}
SILENT = {"pin_reset", "hold_expiring", "closed"}  # said by the sign-up flow itself, or nothing to say


class Notifier:
    def __init__(self, channel: "Channel"):
        self.ch = channel

    def lines(self, user: dict, rows: list[dict]) -> list[str]:
        lang, gender = user.get("language") or "iq", user.get("gender") or "m"
        out = []
        money = False
        for row in rows:
            key = KEYS.get(row["kind"])
            if key is None:
                continue
            slots = json.loads(row["payload_json"] or "{}")
            if row["kind"] == "hold_refunded" and slots.get("reason") not in (None, "expired"):
                key = "n_hold_cancelled"
            slots.setdefault("name", slots.pop("sender_name", ""))
            slots.setdefault("last4", slots.pop("sender_last4", ""))
            slots.setdefault("reason", "")
            try:
                line = T.say(key, gender, lang, **slots)
            except (KeyError, IndexError):
                continue
            if not slots.get("last4"):  # a sender with no number (a username account): no «(…)» left behind
                line = line.replace(" (…)", "").replace("(…)", "")
            out.append(line)
            money = money or row["kind"] in ("transfer_in", "hold_refunded", "admin_credit", "admin_debit")
        if out and money:
            balance = int((self.ch.accounts.get(user["id"]) or {}).get("balance") or 0)
            out.append(T.say("n_balance", gender, lang, balance=balance))
        return out

    def deliver(self, user: dict, *, force: bool = False, to: tuple[str, bool] | None = None) -> int:
        """Send this user's pending notices as one message (inside their window). Returns how many.

        `to` is the chat to send them into (sender, has_phone), for an account with no number (a WhatsApp username
        account, accounts.phones.user_key): its notices wait for its own turn, and go to the chat it writes from."""
        address = to or ((user.get("phone_e164"), True) if phones.dialable(user.get("phone_e164")) else None)
        if address is None or not address[0] or not (force or self.ch.accounts.in_window(user)):
            return 0
        rows = self.ch.accounts.pending_notices(user["id"])
        if not rows:
            return 0
        text = "\n".join(self.lines(user, rows))
        ids = [r["id"] for r in rows]
        if text:
            try:
                self.ch.graph.send_text(address[0], address[1], text)
                self.ch.store.count(self.ch._day(), "msgs_out_text")
            except Exception as exc:  # stays pending: the next sweep tries again
                log.warning("notice send failed: %s", type(exc).__name__)
                return 0
        self.ch.accounts.mark_delivered(ids)
        return len(ids)

    def after_turn(self) -> int:
        """Deliver what is waiting for people inside their window (e.g. the payee of the payment just made)."""
        sent = 0
        for user in self.ch.accounts.users_with_notices(limit=20):
            sent += self.deliver(user)
        return sent

    sweep = after_turn
