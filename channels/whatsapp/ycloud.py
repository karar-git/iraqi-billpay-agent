"""The same number through YCloud (WHATSAPP_PROVIDER=ycloud): the webhook signature, parsing, sending.

An alternative to graph.py with the same interface, so the rest of the channel does not know which one it talks to.
What differs from the Cloud API:

- Numbers are E.164 with a +. The channel keeps digits only, as Meta gives them: every sender is turned into digits
  on the way in and a + is added on the way out, so one person is one account and one sender key on both providers.
  A `from` that is not written as a phone number is never read as one: the user id answers for it.
- A message's id is its wamid (YCloud's own id only when there is none): retries are taken once, across a switch too.
- The webhook carries more than user messages. Only `whatsapp.inbound_message.received` is read: a message typed in
  the WhatsApp Business app (whatsapp.smb.message.echoes / .created), a history sync (whatsapp.smb.history, whose
  customer messages sit under the same `whatsappInboundMessage` key) and delivery updates never reach the channel.
  Nor do system notices (a changed number, a changed user id) and messages in a group: nobody wrote those to the bot.
- A message without any usable time is dropped: its age cannot be checked, and an old one must never be answered.
- Inbound media is a link: the link is what `media_id` holds, since the docs give no download by id. The API key goes
  only to the media paths of https://api.ycloud.com, and a redirect is never followed. A photo (and a file that is a
  picture or a PDF) comes the same way as a voice note: `image.link` / `document.link`, next to id, sha256, mime_type
  and caption, so `media()` downloads it by the same path.
- A voice note's `audio.voice` and a forwarded message's `context.forwarded` / `context.frequently_forwarded` come in
  the Cloud API's shape and are read as they are there (graph._inbound): a forwarded voice note or an audio file never
  counts for voice approval.
- A list message is the Cloud API's own `interactive` object, and a tapped row comes back as `interactive.list_reply`
  with the row's id, title and description. The limits are the same but one: the request schema stops the body of any
  interactive message at 1024 characters (LIST_BODY_MAX in graph.py is that number, so one payload passes on both).

Read on 2026-11-13:
  https://docs.ycloud.com/api-reference/whatsapp-messages/send-a-message-directly
    (interactive: header.text 60, body.text 1024, footer.text 60, action.button 20, 1 to 10 sections, a section title
    24 and required only with more than one section, 10 rows over all sections, row id 200, title 24, description 72;
    `to` or `recipient`)
  https://docs.ycloud.com/en/api-reference/webhook-reference/whatsapp-inbound-message-received
  https://docs.ycloud.com/en/api-reference/guides/examples/webhook-examples/whatsapp-inbound-message-webhook-examples
    (interactive.type button_reply | list_reply | nfm_reply | call_permission_reply; image and document with link, id,
    sha256, mime_type, caption, and filename on a document; the link opens by itself for a few minutes and with the
    X-API-Key header for a month)

Synchronous, like graph.py. Only rate limits are retried, for the same reason as there.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from channels.whatsapp.config import number
from channels.whatsapp.graph import (
    MAX_INBOUND_MEDIA,
    MAX_OUTBOUND_AUDIO,
    Inbound,
    SendError,
    _inbound,
    audio_payload,
    buttons_payload,
    is_image_mime,
    list_payload,
    text_payload,
)

log = logging.getLogger("whatsapp.ycloud")

API_HOST = "api.ycloud.com"
BASE = f"https://{API_HOST}/v2"
INBOUND_EVENT = "whatsapp.inbound_message.received"
MEDIA_PATH = "/v2/whatsapp/media/download/"
SIGNATURE_TOLERANCE = 300  # seconds either way
RATE_LIMIT_CODES = ("130429", "131056")
MEDIA_FIELDS = ("audio", "image", "document")  # the inbound media this channel downloads, each with a `link`


# ---------------------------------------------------------------- webhook


def signature_ok(raw: bytes, header: str | None, secret: str, *, now: float | None = None,
                 tolerance: int = SIGNATURE_TOLERANCE) -> bool:
    """YCloud-Signature: t=<unix seconds>,s=<hex HMAC-SHA256 of "<t>.<raw body>" with the endpoint's secret>. Any one
    matching s will do, in either case of hex, as in the verifier YCloud's docs give."""
    if not secret or not header:
        return False
    fields = [part.strip().partition("=") for part in header.split(",")]
    stamps = [value for key, _, value in fields if key == "t"]
    given = [value for key, _, value in fields if key == "s"]
    if len(stamps) != 1 or not given:
        return False
    stamp = stamps[0]
    if not (stamp.isascii() and stamp.isdigit() and len(stamp) <= 12):
        return False
    if abs((time.time() if now is None else now) - int(stamp)) > tolerance:
        return False
    expected = hmac.new(secret.encode(), stamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    # bytes on both sides: a forged header with a non-ASCII character is a mismatch, not a TypeError
    return any(hmac.compare_digest(expected.encode(), value.lower().encode("utf-8", "replace")) for value in given)


def sign(raw: bytes, secret: str, timestamp: int) -> str:
    """The header value YCloud would send for this body (tests and local tools)."""
    mac = hmac.new(secret.encode(), str(timestamp).encode() + b"." + raw, hashlib.sha256).hexdigest()
    return f"t={timestamp},s={mac}"


def _epoch(raw: Any) -> float:
    if not isinstance(raw, str) or not raw.strip():
        return 0.0
    try:
        when = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).timestamp()


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def parse(body: dict, business_number: str = "", service_number: str = "") -> list[Inbound]:
    """The user message in a webhook body, as graph.parse gives it. Everything but an inbound message event is dropped,
    so is a message not addressed to `business_number`, and so is anything that comes from this channel's own number."""
    if not isinstance(body, dict) or body.get("type") != INBOUND_EVENT:
        return []
    message = body.get("whatsappInboundMessage")
    if not isinstance(message, dict):
        return []
    if message.get("type") == "system" or message.get("groupId"):
        return []
    business = number(business_number)
    if not business or number(message.get("to")) != business:
        log.warning("dropped an inbound message not addressed to YCLOUD_FROM")
        return []
    phone = number(message.get("from"))
    user_id = _str(message.get("fromUserId"))
    if {phone, number(user_id)} & ({business, number(service_number)} - {""}):
        return []
    timestamp = _epoch(message.get("sendTime")) or _epoch(message.get("createTime")) or _epoch(body.get("createTime"))
    if not timestamp:
        log.warning("dropped an inbound message without a usable time")
        return []
    meta_shaped = {**message, "from": phone, "from_user_id": user_id,
                   "id": _str(message.get("wamid")) or _str(message.get("id")) or _str(body.get("id")),
                   "timestamp": timestamp}
    for field in MEDIA_FIELDS:  # the link is the handle: media() takes it, and nothing else of this account
        media = _dict(message.get(field))
        meta_shaped[field] = {**media, "id": _str(media.get("link"))}
    name = _str(_dict(message.get("customerProfile")).get("name")) or None
    try:
        item = _inbound(meta_shaped, {phone or user_id: name})
    except (AttributeError, TypeError, ValueError):
        log.warning("dropped a malformed inbound message")
        return []
    return [item] if item is not None else []


# ---------------------------------------------------------------- sending


def is_ycloud(url: str) -> bool:
    """The API key only ever goes to YCloud's media files. A forged link naming another host, or another path of the
    account (.. in any encoding included), gets nothing."""
    try:
        u = httpx.URL(url)
    except Exception:
        return False
    return (u.scheme == "https" and u.host == API_HOST and u.port in (None, 443) and not u.userinfo
            and u.path.startswith(MEDIA_PATH) and ".." not in u.path)


def _e164(raw: str) -> str:
    plain = number(raw)
    return f"+{plain}" if plain else ""


class YCloudClient:
    """The Graph protocol (graph.py) over YCloud's API. `transport` lets tests swap the network out."""

    def __init__(self, api_key: str, from_number: str, *, transport: httpx.BaseTransport | None = None,
                 timeout: float = 30.0, sleep=time.sleep):
        self.from_number = _e164(from_number)
        self.sleep = sleep

        def with_key(request: httpx.Request) -> httpx.Request:  # as auth, not a client header: httpx would repr that
            request.headers["X-API-Key"] = api_key
            return request

        self.client = httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0), transport=transport, auth=with_key)

    def close(self) -> None:
        self.client.close()

    def _post(self, payload: dict) -> dict:
        if not self.from_number:
            raise SendError("YCLOUD_FROM is empty: nothing is sent")
        body = {"from": self.from_number, **payload}
        body.pop("recipient_type", None)  # not in YCloud's request schema
        err = ""
        for attempt in range(3):
            r = self.client.post(f"{BASE}/whatsapp/messages/sendDirectly", json=body)
            answer = _answer(r) if r.is_success else {}
            if r.is_success and answer.get("status") != "failed":
                return answer
            err = f"{answer.get('errorCode')}: {answer.get('errorMessage')}"[:300] if r.is_success else r.text[:300]
            # Only rate limits are retried: a 5xx may have been delivered, and the same bubble twice (a card above
            # all) looks worse than a rare lost one.
            if r.status_code == 429 or (r.status_code < 500 and any(code in err for code in RATE_LIMIT_CODES)):
                self.sleep(1.5 * (attempt + 1))
                continue
            raise SendError(f"{r.status_code}: {err}")
        raise SendError(f"gave up after rate limits: {err}")

    @staticmethod
    def _to(to: str, has_phone: bool) -> str:
        return _e164(to) if has_phone else to

    def send_text(self, to: str, has_phone: bool, text: str) -> None:
        self._post(text_payload(self._to(to, has_phone), has_phone, text))

    def send_buttons(self, to: str, has_phone: bool, body: str, buttons: list[tuple[str, str]],
                     header: str = "", footer: str = "") -> None:
        self._post(buttons_payload(self._to(to, has_phone), has_phone, body, buttons, header, footer))

    def send_list(self, to: str, has_phone: bool, body: str, button: str, rows: list[tuple[str, str, str]],
                  header: str | None = None, footer: str | None = None, *, section: str = "") -> None:
        self._post(list_payload(self._to(to, has_phone), has_phone, body, button, rows, header, footer,
                                section=section))

    def upload_media(self, data: bytes, mime: str) -> str:
        """Upload a voice note (ogg/opus) and return its media id, for send_audio."""
        if not self.from_number:
            raise SendError("YCLOUD_FROM is empty: nothing is sent")
        if not data or len(data) > MAX_OUTBOUND_AUDIO:
            raise SendError("audio empty or over the size cap")
        kind = (mime or "audio/ogg").split(";")[0].strip() or "audio/ogg"
        name = "reply.ogg" if "ogg" in kind or "opus" in kind else "reply.mp3"
        r = self.client.post(f"{BASE}/whatsapp/media/{self.from_number}/upload", files={"file": (name, data, kind)},
                             timeout=60)
        if not r.is_success:
            raise SendError(f"upload {r.status_code}: {r.text[:300]}")
        media_id = str(_answer(r).get("id") or "")
        if not media_id:
            raise SendError("upload answered without a media id")
        return media_id

    def send_audio(self, to: str, has_phone: bool, media_id: str) -> None:
        self._post(audio_payload(self._to(to, has_phone), has_phone, media_id))

    def mark_read(self, msg_id: str) -> None:
        """Blue ticks and the typing dots. Cosmetic: a failure never blocks a reply, and it is not tried twice."""
        if not msg_id.strip("."):  # quote() leaves dots alone, and they would climb out of this path
            return
        try:
            r = self.client.post(f"{BASE}/whatsapp/inboundMessages/{quote(msg_id, safe='')}/typing")
            if not r.is_success:
                log.info("read receipt failed: %s", r.status_code)
        except Exception as exc:
            log.info("read receipt failed: %s", type(exc).__name__)

    def media(self, media_id: str) -> tuple[bytes, str]:
        """Download an inbound media file (a voice note, a photo, a PDF) from its link (capped). The mime is empty
        when the answer does not name an audio type, an image type or a PDF: the channel then goes by what the
        webhook said (Inbound.mime)."""
        if not is_ycloud(media_id):
            raise SendError("media link missing or not on YCloud")
        chunks, size = [], 0
        with self.client.stream("GET", media_id) as r:
            if r.status_code != 200:
                raise SendError(f"media download {r.status_code}")
            kind = r.headers.get("content-type", "")
            for chunk in r.iter_bytes():
                size += len(chunk)
                if size > MAX_INBOUND_MEDIA:
                    raise SendError("media over the size cap")
                chunks.append(chunk)
        return b"".join(chunks), kind if kind.startswith("audio/") or is_image_mime(kind) else ""


def _answer(r: httpx.Response) -> dict:
    """A 2xx means it was sent, whatever the body: a body that does not parse must not look like a failure."""
    try:
        data = r.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
