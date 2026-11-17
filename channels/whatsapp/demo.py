"""The instant demo account (SIGNUP_MODE=demo): the first message from a new sender opens an account with pretend
money and a short past, and the person can pay at once.

- What is made is wallet code (wallet.api.Wallet.open_demo_account, one transaction, idempotent): about 425,000 IQD,
  two people (one saved with an alias, «أخوي»), a transfer to each three and six days back, this month's unpaid
  electricity and generator bills and last month's paid internet bill. The money is mock, every amount is a
  constant of DEMO_PLAN, and no model decides any of it. The demo people are paid to external:<number>: never a
  hold, never a real account that owns that number.
- The intro goes out before the person's own first message is answered, as two bubbles in their language (from the
  first text: Iraqi by default, MSA, English), in words that read for anyone while the gender is unknown: (1) that
  this is a demo with pretend money, the balance, the two earlier transfers (name, amount, days ago), the bills due
  and an example of what to say, typed or by voice; (2) the PIN alone. The WhatsApp profile name is used when
  accounts.service.clean_name accepts it.
- The PIN is made here (accounts.pins.new_pin), kept by the wallet only as a peppered hash, and its plaintext lives
  in a local variable for the turn (the caller hands it to the voice reply as never=, so speech never says it). It
  is never written to a database, a log, the outbox, the audit log or the channel store.
- Restart safety: the intro is pending while the sender's senders.greeted is not 1. An account that exists without
  its intro (a crash after the commit) gets a fresh PIN when the intro finally goes out.
- A sender whose WhatsApp hides the number (YCloud gives only fromUserId) gets a demo account too, under an account
  key made from that user id (accounts.phones.user_key: letters only, never a number). They can pay bills and send
  to numbers; nobody can send to them by number, and nothing is ever sent to that key as if it were a number.
- No identity step (KYC is bypassed by design in demo mode: the money is pretend), and the manager is not told of
  every visitor: they are counted («demo_signups», and «signups» as before for the budget page).
- Voice approval is mentioned only while it really works here (Channel.voice_approval_on: VOICE_APPROVAL and a working
  speaker model), with its cap: after the first payment asked for in a voice note and confirmed with the PIN, a voice
  note can approve later payments up to VOICE_APPROVAL_MAX_IQD to people and bills the account knows.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from accounts import phones, pins
from accounts import texts as T
from accounts.service import clean_name
from channels.whatsapp import texts as channel_texts
from channels.whatsapp.graph import Inbound
from payments import language
from wallet.api import DEMO_PLAN
from wallet.db import BAGHDAD, from_iso

if TYPE_CHECKING:
    from channels.whatsapp.channel import Channel

log = logging.getLogger("whatsapp.demo")


def first_text(batch: list[Inbound]) -> str:
    return next((item.text for item in batch if item.kind == "text" and (item.text or "").strip()), "")


def language_of(batch: list[Inbound]) -> str:
    """The intro's language: the first typed text's (Iraqi, MSA or English), Iraqi when nothing was typed."""
    return language.detect(first_text(batch), language.DEFAULT)


def profile_name(batch: list[Inbound]) -> str | None:
    """The WhatsApp profile name, when clean_name accepts it (no digits, no emoji in front, 2 to 40 letters)."""
    return next((name for name in (clean_name(item.name or "") for item in batch) if name), None)


def days_ago(moment: str, now: datetime) -> int:
    return max(0, (now.astimezone(BAGHDAD).date() - from_iso(moment).astimezone(BAGHDAD).date()).days)


def ago(days: int, lang: str) -> str:
    if days in (1, 2):
        return T.say(f"ago_{days}", None, lang)
    return T.say("ago_n" if days <= 10 else "ago_many", None, lang, n=days)


EN_NAMES = {c["name"]: c["name_en"] for c in DEMO_PLAN["contacts"] if c.get("name_en")}


def intro_lines(story: dict, *, lang: str, hello: str, name: str | None, now: datetime,
                voice_approval: bool = False, voice_cap: int = 100_000) -> list[str]:
    """Bubble one of the intro, filled from the wallet's own record (Wallet.demo_story). No PIN in it."""
    say = lambda key, **slots: T.say(key, None, lang, **slots)  # noqa: E731 - gender-neutral texts
    lines = [say("demo_open", hello=hello, name=f" {name}" if name else ""), say("demo_balance", balance=story["balance"])]
    sent = []
    for index, row in enumerate(story["transfers"][:2]):
        key = "demo_sent" if index == 0 else "demo_sent_more"
        name = EN_NAMES.get(row["name"], row["name"]) if lang == "en" else row["name"]  # "went to Hussein Ali"
        sent.append(say(key, ago=ago(days_ago(row["at"], now), lang), amount=row["amount"], name=name))
    if sent:
        sentence = say("demo_and").join(sent) + "."
        lines.append(sentence[:1].upper() + sentence[1:] if lang == "en" else sentence)
    bills = [say("demo_bill", biller=(row["name_en"] if lang == "en" and row.get("name_en") else row["name_ar"]),
                 amount=row["amount"]) for row in story["bills"]]
    if bills:
        lines.append(say("demo_bills", bills=say("demo_also").join(bills)))
    lines.append(say("demo_try"))
    if voice_approval:
        lines.append(say("demo_voice_approval", cap=voice_cap))
    return lines


class Demo:
    def __init__(self, channel: "Channel"):
        self.ch = channel

    @property
    def on(self) -> bool:
        return self.ch.settings.signup_mode == "demo"

    def account_key(self, sender: str, has_phone: bool) -> str | None:
        """The key a sender's account is found (and a demo account opened) under: the canonical Iraqi number; for a
        sender with no number in the webhook (a WhatsApp username), the key made from its user id; and in demo mode
        for a number from outside Iraq, a key made from that number (the wallet's accounts are Iraqi numbers, and a
        foreign one must never look like one). None when there is nothing to key on."""
        if has_phone:
            e164 = phones.canonical(sender)
            if e164 is not None or not self.on:
                return e164
            digits = "".join(ch for ch in sender or "" if ch.isdigit())
            return phones.user_key("tel:" + digits) if digits else None
        return phones.user_key(sender) if (sender or "").strip() else None

    def pending(self, key: str, user: dict | None) -> bool:
        """A demo account whose intro has not gone out to this sender yet."""
        if user is None or user.get("source") != "demo" or user.get("status") != "active":
            return False
        return int((self.ch.store.sender(key) or {}).get("greeted") or 0) != 1

    def open(self, key: str, account_key: str, batch: list[Inbound]) -> tuple[dict, str | None]:
        """Open the demo account (idempotent). (the account, its PIN) when it was made now; (the account, None) when
        it was there already (the intro, if still owed, then makes a fresh PIN)."""
        ch = self.ch
        ch.store.end_onboarding(key)  # a half-done sign-up of the old flow is dropped
        if account_key.isdigit():
            ch.accounts.kyc_drop(account_key)
        lang = language_of(batch)
        name = profile_name(batch)
        pin = pins.new_pin(account_key if account_key.isdigit() else None)
        result = ch.wallet.open_demo_account(account_key, name or T.say("demo_guest", None, lang), language=lang,
                                             pin_hash=pins.hash_pin(pin),
                                             wa_name=next((item.name for item in batch if item.name), None))
        profile = result["user"]
        user = ch.accounts.get(profile["id"]) or profile
        row = ch.store.add_sender(key, user["id"], ch._now())  # greeted stays 0 until the intro is out
        if row.get("user_id") != user["id"]:  # a row left by the rate limit (no account then)
            ch.store.update_sender(key, user_id=user["id"], greeted=0, awaiting_pin=None)
        if not result.get("created"):
            return user, None
        day = ch._day()
        ch.store.count(day, "signups")  # /admin/budget counts accounts opened, as before
        ch.store.count(day, "demo_signups")  # the manager is not told of every visitor: they are counted
        log.info("demo account opened (user %s)", user["id"])
        if result.get("claimed"):
            ch.notifier.after_turn()  # the senders of claimed holds hear about it (inside their window)
        return user, pin

    def intro(self, key: str, user: dict, batch: list[Inbound], pin: str | None = None) -> tuple[str, str]:
        """Send the two intro bubbles now (before the first message is answered) and mark the sender greeted once
        the PIN bubble went out (a failed send leaves the intro owed: the next message gets it with a fresh PIN).
        Returns (the PIN, bubble one), for the caller's turn only. A missing `pin` means the account was made
        earlier (a restart between the commit and the intro): a fresh PIN replaces the one nobody saw."""
        ch = self.ch
        uid = user["id"]
        own = user.get("phone_e164") if phones.dialable(user.get("phone_e164")) else None
        if pin is None:
            pin = pins.new_pin(own)
            ch.wallet.set_pin(uid, pins.hash_pin(pin), action="demo_pin_issued")
        texts = [item.text for item in batch if item.kind == "text" and (item.text or "").strip()]
        lang = language_of(batch) if texts else (user.get("language") or language.DEFAULT)
        now = ch.clock()
        hello = channel_texts.answer_salam(" ".join(texts), channel_texts.hello(now.astimezone(BAGHDAD).hour, lang),
                                           lang)
        named = user.get("name") if user.get("name") not in (T.say("demo_guest", None, lang),
                                                              T.say("demo_guest", None, "en")) else None
        first = (named or "").split(" ")[0] or None
        story = ch.wallet.demo_story(uid)
        lines = intro_lines(story, lang=lang, hello=hello, name=first, now=now,
                            voice_approval=ch.voice_approval_on, voice_cap=ch.settings.voice_approval_max_iqd)
        item = batch[0]
        bubble = "\n".join(lines)
        ch._post_text(item, bubble)
        shown = ch._post_text(item, T.say("demo_pin", None, lang, pin=pin)) is not False
        ch.accounts.set_language(uid, lang)
        ch.store.add_sender(key, uid, ch._now())
        ch.store.update_sender(key, user_id=uid, greeted=1 if shown else 0, awaiting_pin=None, hello_day=ch._day())
        ch.forget(key)  # an open conversation from before is closed: a fresh start with this account
        return pin, bubble
