"""Sending Instagram direct messages: the WhatsApp `Graph` protocol (channels/whatsapp/graph.py) over Meta's Send API.

Every limit here is one Meta documents (channels/instagram/README.md gives the page for each), or is marked as our
own choice. What differs from WhatsApp:

- One text is at most 1000 bytes of UTF-8 (about 500 Arabic letters): a longer reply is sent as several messages.
- Reply buttons are a button template (640 characters, one to three postback buttons). A longer card sends its first
  lines as plain text and keeps the buttons under the last part.
- A list is a text with quick replies (at most 13, titles of 20 characters); the descriptions go into the text.
- A voice reply cannot be sent: Instagram takes audio by public link in aac, m4a, wav or mp4, and the agent's voice is
  an ogg file to upload. `send_audio` and `upload_media` raise `NotSupported`, and the channel falls back to text.
- "Seen" is set per conversation, not per message: `mark_read` needs to know who wrote the message (`remember`).

The access token travels in the Authorization header, only to the configured Graph host (one of two, checked), never
in a URL and never in an exception. A media link is downloaded without the token, only from Meta's CDN hosts.

Synchronous, like the WhatsApp clients. Only rate limits are retried: anything else may have been delivered.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from collections import OrderedDict
from typing import Any, Callable, Iterable

import httpx

from channels.whatsapp.graph import Inbound, SendError

log = logging.getLogger("instagram.client")

API_HOSTS = ("graph.instagram.com", "graph.facebook.com")  # Instagram Login, Facebook Login
DEFAULT_BASE = "https://graph.instagram.com"
DEFAULT_VERSION = "v27.0"

TEXT_MAX_BYTES = 1000  # "Message text must be UTF-8 and be a 1000 bytes or less"
TEXT_MAX_CHARS = 4096  # our own cap on one reply, the same as the WhatsApp channel's
QUICK_REPLIES_MAX = 13  # "A maximum of 13 quick replies are supported"
QUICK_REPLY_TITLE_MAX = 20  # "up to 20 characters before being truncated"
TEMPLATE_TEXT_MAX = 640  # button template: "up to 640 characters"
TEMPLATE_BUTTONS_MAX = 3  # button template: "1-3 buttons"
BUTTON_TITLE_MAX = 20  # postback button title (Messenger Platform button reference)
PAYLOAD_MAX = 1000  # postback payload (Messenger Platform button reference); applied to quick replies too
MAX_INBOUND_MEDIA = 16_000_000  # our own cap on a download, the same as the WhatsApp channel's
MAX_REDIRECTS = 3
RATE_LIMIT_CODE = 613  # "Calls to this api have exceeded the rate limit."
# Where an attachment link may point. Meta's sample payloads use lookaside.fbsbx.com/ig_messaging_cdn/; the other
# two are Meta's CDN domains a redirect may name.
CDN_HOSTS = (".fbsbx.com", ".fbcdn.net", ".cdninstagram.com")

_ACCOUNT_ID = re.compile(r"\d{1,32}|me")
_VERSION = re.compile(r"v\d{1,3}\.\d{1,2}")


class ApiError(SendError):
    """The Graph API refused a call. Carries the HTTP status and Meta's error code and subcode (numbers only)."""

    def __init__(self, status: int, code: int | None = None, subcode: int | None = None, detail: str = ""):
        self.status, self.code, self.subcode = status, code, subcode
        parts = [str(status)]
        if code is not None:
            parts.append(f"code {code}")
        if subcode is not None:
            parts.append(f"subcode {subcode}")
        super().__init__(" ".join(parts) + (f": {detail}" if detail else ""))


class NotSupported(SendError):
    """Instagram has no such message. The channel falls back to text."""


# ---------------------------------------------------------------- pure helpers


def api_base(raw: str | None) -> str:
    """The Graph host the token may be sent to: https://graph.instagram.com (Instagram Login) or
    https://graph.facebook.com (Facebook Login). Anything else is refused, so a typo cannot send the token away."""
    text = (raw or "").strip().rstrip("/") or DEFAULT_BASE
    try:
        url = httpx.URL(text)
    except Exception:
        raise ValueError("INSTAGRAM_API_BASE is not a URL") from None
    if (url.scheme != "https" or url.host not in API_HOSTS or url.port not in (None, 443) or url.userinfo
            or url.path not in ("", "/") or url.query or url.fragment):
        raise ValueError("INSTAGRAM_API_BASE must be https://graph.instagram.com or https://graph.facebook.com")
    return f"https://{url.host}"


def api_version(raw: str | None) -> str:
    text = (raw or "").strip() or DEFAULT_VERSION
    if not _VERSION.fullmatch(text):
        raise ValueError("INSTAGRAM_API_VERSION must look like v27.0")
    return text


def is_cdn(url: str, hosts: Iterable[str] = CDN_HOSTS) -> bool:
    """An attachment link we may download: https, the default port, no user part, on one of Meta's CDN domains."""
    try:
        u = httpx.URL(url)
    except Exception:
        return False
    if u.scheme != "https" or u.port not in (None, 443) or u.userinfo or not u.host:
        return False
    return any(u.host == h.lstrip(".") or u.host.endswith(h if h.startswith(".") else "." + h) for h in hosts)


def utf8_len(text: str) -> int:
    return len(text.encode("utf-8", "replace"))


def _joins(ch: str) -> bool:
    """A character that belongs to the one before it (a vowel mark, a variation selector, a joiner)."""
    return unicodedata.category(ch) in ("Mn", "Mc", "Me") or ch in "‍️ـ"


def _fit(text: str, limit: int, measure: Callable[[str], int]) -> int:
    """How many leading characters of `text` fit in `limit`."""
    if measure is len:
        return min(len(text), limit)
    low, high = 0, min(len(text), limit)
    while low < high:
        mid = (low + high + 1) // 2
        if measure(text[:mid]) <= limit:
            low = mid
        else:
            high = mid - 1
    return low


def split_text(text: str, limit: int = TEXT_MAX_BYTES, measure: Callable[[str], int] = utf8_len) -> list[str]:
    """A text as parts that each fit `limit` (bytes of UTF-8 by default), cut at a line, else at a space, else
    between two letters (never inside one, never before a vowel mark). Nothing is dropped but the white space at a
    cut."""
    if limit < 8:
        raise ValueError("limit too small")
    rest = (text or "").strip()
    parts: list[str] = []
    while rest:
        if measure(rest) <= limit:
            parts.append(rest)
            break
        room = _fit(rest, limit, measure)
        cut = rest.rfind("\n", 0, room + 1)
        if cut < room // 2:
            cut = rest.rfind(" ", 0, room + 1)
        if cut < room // 2:
            cut = room
            while cut > room - 8 and cut > 1 and (_joins(rest[cut]) or rest[cut - 1] == "‍"):
                cut -= 1
        head, rest = rest[:cut].rstrip(), rest[cut:].lstrip()
        if head:
            parts.append(head)
    return parts


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _payload(value: str) -> str:
    """A button's id, as it will come back in the webhook. Never cut: half an id would name nothing, or the wrong
    thing."""
    if not isinstance(value, str) or not value or len(value) > PAYLOAD_MAX:
        raise ValueError(f"a button payload must be 1 to {PAYLOAD_MAX} characters")
    return value


def text_payloads(to: str, text: str) -> list[dict]:
    """A text as one message, or as many as the 1000-byte limit needs."""
    whole = _clip(text, TEXT_MAX_CHARS)
    return [{"recipient": {"id": to}, "message": {"text": part}} for part in split_text(whole)]


def _framed(body: str, header: str | None, footer: str | None) -> str:
    """Instagram has no header or footer: they become the first and the last line."""
    return "\n\n".join(part.strip() for part in (header or "", body or "", footer or "") if part and part.strip())


def buttons_payloads(to: str, body: str, buttons: list[tuple[str, str]], header: str | None = "",
                     footer: str | None = "") -> list[dict]:
    """A card with one to three buttons: a button template with postback buttons (title at most 20 characters, the
    id as the payload). A text over 640 characters sends its first lines as plain messages, buttons on the last."""
    if not 1 <= len(buttons) <= TEMPLATE_BUTTONS_MAX:
        raise ValueError("Instagram template buttons: one to three")
    text = _framed(body, header, footer)
    if not text:
        raise ValueError("a card needs a text")
    parts = split_text(text, TEMPLATE_TEXT_MAX, len)
    out: list[dict] = []
    for lead in parts[:-1]:
        out += text_payloads(to, lead)
    out.append({"recipient": {"id": to}, "message": {"attachment": {"type": "template", "payload": {
        "template_type": "button", "text": parts[-1],
        "buttons": [{"type": "postback", "title": _clip(title, BUTTON_TITLE_MAX), "payload": _payload(bid)}
                    for bid, title in buttons]}}}})
    return out


def list_payloads(to: str, body: str, rows: list[tuple[str, str, str]], header: str | None = None,
                  footer: str | None = None) -> list[dict]:
    """A list of choices: a text with quick replies (at most 13, each title at most 20 characters, the row id as
    the payload). A quick reply shows a title only, so each row's full title and description are written in the
    text, one line per row. A text over 1000 bytes sends its first lines as plain messages."""
    if not 1 <= len(rows) <= QUICK_REPLIES_MAX:
        raise ValueError(f"Instagram quick replies: one to {QUICK_REPLIES_MAX}")
    lines = []
    for _, title, description in rows:
        title, description = (title or "").strip(), (description or "").strip()
        if description:
            lines.append(f"{title}: {description}")
        elif len(title) > QUICK_REPLY_TITLE_MAX:
            lines.append(title)  # the chip will cut it: the whole title is in the text
    text = _framed("\n\n".join(part for part in ((body or "").strip(), "\n".join(lines)) if part), header, footer)
    if not text:
        raise ValueError("a list needs a text")
    parts = split_text(text)
    out: list[dict] = []
    for lead in parts[:-1]:
        out += text_payloads(to, lead)
    out.append({"recipient": {"id": to}, "message": {"text": parts[-1], "quick_replies": [
        {"content_type": "text", "title": _clip(title, QUICK_REPLY_TITLE_MAX), "payload": _payload(rid)}
        for rid, title, _ in rows]}})
    return out


def action_payload(to: str, action: str) -> dict:
    """mark_seen | typing_on | typing_off. Sent alone: nothing else may ride in the same request."""
    if action not in ("mark_seen", "typing_on", "typing_off"):
        raise ValueError("unknown sender action")
    return {"recipient": {"id": to}, "sender_action": action}


# ---------------------------------------------------------------- the client


class InstagramClient:
    """The Graph protocol over Instagram's Send API. `transport` lets tests (and dry runs) swap the network out."""

    voice_replies = False  # no uploaded voice note on Instagram: do not spend on speech for this channel

    def __init__(self, token: str, account_id: str, version: str = DEFAULT_VERSION, *, base: str = DEFAULT_BASE,
                 page_id: str = "", transport: httpx.BaseTransport | None = None, timeout: float = 30.0,
                 sleep=time.sleep, cdn_hosts: Iterable[str] = CDN_HOSTS, max_media: int = MAX_INBOUND_MEDIA):
        account_id, page_id = (account_id or "").strip(), (page_id or "").strip()
        for value in (account_id, page_id):
            if value and not _ACCOUNT_ID.fullmatch(value):
                raise ValueError("INSTAGRAM_ACCOUNT_ID must be the account's numeric id")
        self.account_id = account_id
        self.root = api_base(base)
        self.host = httpx.URL(self.root).host
        self.base = f"{self.root}/{api_version(version)}"
        # Sends go from /<Instagram account id>/messages. Facebook Login alone sends from the linked Page instead:
        # pass its id as `page_id` (or "me", which a Page token reads as that Page).
        self.send_from = page_id or account_id
        self.sleep = sleep
        self.cdn_hosts = tuple(cdn_hosts)
        self.max_media = max_media
        self._lock = threading.Lock()
        self._senders: OrderedDict[str, str] = OrderedDict()  # message id -> who wrote it, for mark_read

        def with_token(request: httpx.Request) -> httpx.Request:  # as auth, not a client header: httpx would repr that
            if request.url.host == self.host and request.url.scheme == "https":
                request.headers["Authorization"] = f"Bearer {token}"
            return request

        self._auth = with_token
        self._scrub = (lambda text: text.replace(token, "[token]")) if token else (lambda text: text)
        self._has_token = bool(token)
        # No redirect is ever followed with the token; a media redirect is followed by hand, host by host.
        self.client = httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0), transport=transport,
                                   follow_redirects=False)

    def close(self) -> None:
        self.client.close()

    # ------------------------------------------------------------ sending

    def _post(self, payload: dict, attempts: int = 3) -> dict:
        if not self.send_from:
            raise SendError("INSTAGRAM_ACCOUNT_ID is empty: nothing is sent")
        if not self._has_token:
            raise SendError("INSTAGRAM_TOKEN is empty: nothing is sent")
        body = dict(payload)
        if self.host == "graph.facebook.com" and "message" in body:
            body = {"messaging_type": "RESPONSE", **body}  # as that login type's documented requests carry it
        last: ApiError | None = None
        for attempt in range(attempts):
            try:
                r = self.client.post(f"{self.base}/{self.send_from}/messages", json=body, auth=self._auth)
            except httpx.HTTPError as exc:  # may have been delivered: never sent twice
                raise SendError(f"network: {type(exc).__name__}") from None
            if r.is_success:
                return _answer(r)
            last = self._error(r)
            if (r.status_code == 429 or last.code == RATE_LIMIT_CODE) and attempt + 1 < attempts:
                self.sleep(1.5 * (attempt + 1))
                continue
            raise last
        raise last or SendError("nothing was sent")

    def _error(self, r: httpx.Response) -> ApiError:
        error = _answer(r).get("error")
        error = error if isinstance(error, dict) else {}

        def num(key: str) -> int | None:
            value = error.get(key)
            return value if isinstance(value, int) and not isinstance(value, bool) else None

        detail = error.get("message") if isinstance(error.get("message"), str) else ""
        return ApiError(r.status_code, num("code"), num("error_subcode"), self._scrub(detail)[:200])

    def send_text(self, to: str, has_phone: bool, text: str) -> None:
        """A text, as several messages when it is over 1000 bytes. `has_phone` is ignored: an Instagram user is
        always addressed by the Instagram-scoped id."""
        for payload in text_payloads(to, text):
            self._post(payload)

    def send_buttons(self, to: str, has_phone: bool, body: str, buttons: list[tuple[str, str]],
                     header: str = "", footer: str = "") -> None:
        for payload in buttons_payloads(to, body, buttons, header, footer):
            self._post(payload)

    def send_list(self, to: str, has_phone: bool, body: str, button: str, rows: list[tuple[str, str, str]],
                  header: str | None = None, footer: str | None = None) -> None:
        """`button` is WhatsApp's label for opening its list. Instagram shows the choices at once, so it is unused."""
        for payload in list_payloads(to, body, rows, header, footer):
            self._post(payload)

    def upload_media(self, data: bytes, mime: str) -> str:
        raise NotSupported("Instagram takes no uploaded voice note: audio is sent by public link, and not as ogg")

    def send_audio(self, to: str, has_phone: bool, media_id: str) -> None:
        raise NotSupported("Instagram takes no uploaded voice note: audio is sent by public link, and not as ogg")

    # ------------------------------------------------------------ seen and typing

    def remember(self, items: Iterable[Inbound]) -> None:
        """Note who wrote each message, so that `mark_read(msg_id)` can find the conversation. Call it with what
        `parse` returned. Kept in memory, a few thousand at most."""
        with self._lock:
            for item in items:
                self._senders[item.msg_id] = item.sender
                self._senders.move_to_end(item.msg_id)
            while len(self._senders) > 5000:
                self._senders.popitem(last=False)

    def mark_read(self, msg_id: str, to: str = "") -> None:
        """"Seen" under the person's last message, then the typing dots. Cosmetic: a failure never blocks a reply,
        and it is not tried twice. Instagram marks a conversation, not a message: `to` (or `remember`) names it."""
        if not to:
            with self._lock:
                to = self._senders.get(msg_id, "")
        if not to:
            return
        try:
            self._post(action_payload(to, "mark_seen"), attempts=1)
            self._post(action_payload(to, "typing_on"), attempts=1)
        except Exception as exc:
            log.info("read receipt failed: %s", type(exc).__name__)

    def typing(self, to: str, on: bool = True) -> None:
        try:
            self._post(action_payload(to, "typing_on" if on else "typing_off"), attempts=1)
        except Exception as exc:
            log.info("typing indicator failed: %s", type(exc).__name__)

    # ------------------------------------------------------------ media

    def media(self, media_id: str) -> tuple[bytes, str]:
        """Download an inbound attachment from its link (what `parse` put in `media_id`). Only https on Meta's CDN
        hosts, without the token, capped in size. The mime is what the answer names ("" when it names none)."""
        url = media_id if isinstance(media_id, str) else ""
        for _ in range(MAX_REDIRECTS + 1):
            if not is_cdn(url, self.cdn_hosts):
                raise SendError("media link missing or not on a Meta host")
            try:
                with self.client.stream("GET", url) as r:
                    if r.status_code in (301, 302, 303, 307, 308):
                        url = str(r.url.join(r.headers.get("location", "")))
                        continue
                    if r.status_code != 200:
                        raise SendError(f"media download {r.status_code}")
                    declared = r.headers.get("content-length", "")
                    if declared.isdigit() and int(declared) > self.max_media:
                        raise SendError("media over the size cap")
                    chunks, size = [], 0
                    for chunk in r.iter_bytes():
                        size += len(chunk)
                        if size > self.max_media:
                            raise SendError("media over the size cap")
                        chunks.append(chunk)
                    mime = r.headers.get("content-type", "").split(";")[0].strip().lower()
                    return b"".join(chunks), mime
            except httpx.HTTPError as exc:  # the link is a capability: it is never part of an error
                raise SendError(f"media download: {type(exc).__name__}") from None
        raise SendError("media link redirects too often")


def _answer(r: httpx.Response) -> dict[str, Any]:
    """A 2xx means it was sent, whatever the body: a body that does not parse must not look like a failure."""
    try:
        data = r.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
