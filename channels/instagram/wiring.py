"""The Instagram chat as a second door to the same agent: what the WhatsApp channel needs to answer it.

The WhatsApp channel (channels/whatsapp/channel.py) runs every turn, whichever app the message came from. This module
gives it three things:

- `scoped(sender)`: an Instagram sender inside the channel is «ig:<Instagram-scoped id>». The prefix keeps it apart
  from any WhatsApp sender (a phone number, or the user id of a username-only account) in the inbox, the dispatcher's
  queues, the per-sender store rows and the open conversations. It is never a phone number, and nothing reads it as
  one: the account comes from the link, never from the id.
- `Instagram`: the settings, the client the replies go out with, and the link codes (kept in the accounts database,
  so the daily backup holds the links too).
- the words of the linking steps, already filled in, so the channel only decides where they go.

Sign-up stays on WhatsApp, where the number is the account. An Instagram user links an account that exists.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from channels.instagram import graph, texts
from channels.instagram.client import InstagramClient
from channels.instagram.config import Settings
from channels.instagram.link import CODE_TTL_SECONDS, LinkCodes, TooManyCodes, looks_like_code
from channels.whatsapp.graph import Inbound

log = logging.getLogger("instagram.wiring")

PREFIX = "ig:"


def scoped(sender: str) -> str:
    """The channel's name for an Instagram sender."""
    return PREFIX + sender


def is_instagram(sender: Any) -> bool:
    """A sender the Instagram door named. Only Instagram.inbound makes one: the channel refuses this prefix on a
    message that came in through the WhatsApp webhook (Channel.accept is told the door), so typing «ig:» in a sender
    field never reaches an Instagram chat."""
    return isinstance(sender, str) and sender.startswith(PREFIX)


def unscoped(sender: str) -> str:
    """The Instagram-scoped id back, for the Send API and the link table."""
    return sender[len(PREFIX):] if is_instagram(sender) else sender


def wa_chat(service_number: str) -> str:
    """A plain link to the service's WhatsApp chat (nothing typed in advance)."""
    return "https://wa.me/" + "".join(ch for ch in service_number or "" if ch.isdigit())


@dataclass
class Instagram:
    """The Instagram side, made by the app when every INSTAGRAM_* setting it needs is there.

    `client` is an `InstagramClient`, or a fake with the same methods in tests. `bind` opens the link table, once the
    channel knows where the accounts database is and which clock it runs on."""

    settings: Settings
    client: Any
    links: LinkCodes | None = None
    service_number: str = ""
    _conn: sqlite3.Connection | None = field(default=None, repr=False)
    _flood_told: bool = field(default=False, repr=False)  # the brake now on was reported to the admin

    @classmethod
    def from_settings(cls, settings: Settings, client: Any = None) -> "Instagram":
        if client is None:
            client = InstagramClient(settings.token, settings.account_id, settings.api_version,
                                     base=settings.api_base)
        return cls(settings, client)

    def bind(self, db_path: str | Path, clock: Callable[[], float], service_number: str) -> None:
        """Open the link table in the accounts database. A connection of its own: the wallet's connections run their
        own transactions, and a link must never commit or roll back inside one of them."""
        path = str(db_path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=10.0)
        self._conn.execute("PRAGMA busy_timeout = 10000")
        self.links = LinkCodes(self._conn, secret=self.settings.link_secret, clock=clock,
                               global_wrong_per_hour=self.settings.link_flood_per_hour)
        self.service_number = service_number

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------ webhook

    def inbound(self, body: Any) -> list[Inbound]:
        """What a webhook body carries, as the channel's messages: senders scoped, never a phone."""
        items = graph.parse(body, self.settings.account_id)
        remember = getattr(self.client, "remember", None)
        if callable(remember):
            remember(items)  # so that a mark_read without `to` still finds the conversation
        return [replace(item, sender=scoped(item.sender), has_phone=False) for item in items]

    # ------------------------------------------------------------ the link

    def linked(self, sender: str) -> str | None:
        """The account an Instagram sender talks about, or None."""
        return self.links.linked(unscoped(sender)) if self.links is not None else None

    def redeem(self, sender: str, text: str, lang: str) -> tuple[str | None, str | None]:
        """A message from a sender that is not linked yet. (user id, None) when it was a live code and the link is
        made; (None, words) when it was a code that did not link (wrong, used, expired, or the sender is locked);
        (None, None) when it was no code at all."""
        if looks_like_code(text) is None:
            return None, None
        raw = unscoped(sender)
        uid = self.links.redeem(raw, text)
        if uid is not None:
            return uid, None
        until = self.links.locked_until(raw)
        if until is not None:
            return None, texts.say("locked", "m", lang, minutes=texts.minutes(until - self.links.clock()))
        return None, texts.say("wrong_code", "m", lang, left=self.links.tries_left(raw))

    def linking_paused(self) -> bool:
        """The brake on a flood of wrong codes is on (senders that guessed wrong wait; the others still link)."""
        return self.links is not None and self.links.paused_until() is not None

    def flood_news(self) -> str | None:
        """The admin's one line when the brake has just come on (once a flood, never again while it lasts), else
        None. A count and a time: never a code, a sender or an account."""
        until = self.links.paused_until() if self.links is not None else None
        if until is None or self._flood_told:
            self._flood_told = until is not None  # off again: the next flood is news
            return None
        self._flood_told = True
        log.warning("instagram linking brake is on: %d wrong codes in an hour", self.links.global_wrong_per_hour)
        return texts.say("manager_link_flood", "m", "iq", count=self.links.global_wrong_per_hour,
                         minutes=texts.minutes(until - self.links.clock()))

    def not_linked(self, lang: str) -> str:
        return texts.say("not_linked", "m", lang, wa_link=texts.wa_link(self.service_number, lang),
                         phrase=texts.link_phrase(lang), minutes=texts.minutes(self.links.ttl_seconds
                                                                              if self.links else CODE_TTL_SECONDS))

    def issue(self, user_id: str, gender: str, lang: str) -> str:
        """«اربط انستغرام» in the WhatsApp chat: a fresh code and how to use it, or why there is none now."""
        try:
            code = self.links.issue(user_id)
        except TooManyCodes as exc:
            return texts.say("too_many_codes", gender, lang, minutes=texts.minutes(exc.retry_after))
        return texts.say("link_code", gender, lang, code=code, minutes=texts.minutes(self.links.ttl_seconds))

    def unlink(self, user_id: str, gender: str, lang: str) -> str:
        """«افصل انستغرام», from either chat."""
        if self.links.unlink(user_id):
            log.info("instagram link cut by its account")
            return texts.say("unlinked", gender, lang, phrase=texts.link_phrase(lang))
        return texts.say("not_linked_here", gender, lang)

    def whatsapp_only(self, gender: str, lang: str) -> str:
        return texts.say("whatsapp_only", gender, lang, wa_link=wa_chat(self.service_number))
