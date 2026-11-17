"""WhatsApp Cloud API plumbing: the webhook signature, parsing inbound messages, sending replies.

Everything here is synchronous (the orchestrator is synchronous too) and runs in the channel's
worker threads. The access token is only ever sent to Meta's own hosts.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

log = logging.getLogger("whatsapp.graph")

MAX_INBOUND_MEDIA = 16_000_000  # WhatsApp's own cap for audio; a photo or a PDF over it is refused too
BODY_MAX = 4096  # a text message
INTERACTIVE_BODY_MAX = 1024
HEADER_MAX = 60
FOOTER_MAX = 60
BUTTON_TITLE_MAX = 20

# Interactive list messages (a menu behind one button). Source, read on 2026-11-13:
# https://developers.facebook.com/docs/whatsapp/cloud-api/messages/interactive-list-messages
#   button text 20, body 4096, header 60 (text only, optional), footer 60 (optional), section title 24,
#   row id 200, row title 24, row description 72 (optional), up to 10 sections, up to 10 rows over all sections.
# The body is held to 1024 here, not 4096: the second provider's request schema stops an interactive body there
# (see ycloud.py), and one payload has to pass on both.
LIST_BODY_MAX_CLOUD_API = 4096  # what the page above allows; not used for clipping
LIST_BODY_MAX = INTERACTIVE_BODY_MAX
LIST_BUTTON_MAX = 20
LIST_SECTIONS_MAX = 10  # this module sends one section
LIST_ROWS_MAX = 10  # over all sections together
LIST_SECTION_TITLE_MAX = 24
LIST_ROW_ID_MAX = 200
LIST_ROW_TITLE_MAX = 24
LIST_ROW_DESCRIPTION_MAX = 72

# An inbound photo, or a file that is one. Sources, read on 2026-11-13:
# https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/reference/messages/image
#   image: {id, mime_type, sha256, caption (only when the sender wrote one), url (being rolled out; not used here)}
# https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/reference/messages/document
#   document: the same fields and filename
IMAGE_DOCUMENT_MIMES = ("application/pdf",)  # besides image/*: a scanned bill or an ID sent as a file
CAPTION_MAX = 1024  # how much of a caption is kept: it is a note next to the photo, never a command
_META_HOSTS = (".facebook.com", ".fbsbx.com", ".whatsapp.net", ".fbcdn.net")


# ---------------------------------------------------------------- webhook


def signature_ok(raw: bytes, header: str | None, app_secret: str) -> bool:
    """X-Hub-Signature-256: sha256=<hex HMAC-SHA256 of the raw body with the app secret>."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
    # bytes on both sides: a forged header with a non-ASCII character is a mismatch, not a TypeError
    return hmac.compare_digest(expected.encode(), header[len("sha256="):].strip().encode("utf-8", "replace"))


def sign(raw: bytes, app_secret: str) -> str:
    """The header value Meta would send for this body (tests and local tools)."""
    return "sha256=" + hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()


@dataclass
class Inbound:
    """One message from a WhatsApp user, flattened."""

    msg_id: str
    sender: str  # the phone number (wa_id), or the user id of a username-only account
    has_phone: bool
    kind: str  # text | button | voice | audio | image | sticker | location | video | document | reaction | other
    text: str = ""
    button_id: str = ""  # interactive button replies: "<action>:<quote id>"; a tapped list row: its row id
    media_id: str = ""  # voice / audio / image: what Graph.media() takes
    mime: str = ""
    timestamp: float = 0.0
    name: str | None = None  # the WhatsApp profile name (never used for payments)
    caption: str = ""  # image only: what the sender wrote under the photo (never read as a typed message)
    # WhatsApp marks a forwarded message (context.forwarded / frequently_forwarded, on Meta and YCloud alike): a
    # forwarded voice note is somebody's recording, so it never approves a payment by voice and never enrols one.
    forwarded: bool = False


def parse(body: dict, phone_number_id: str = "") -> list[Inbound]:
    """Every user message in a webhook body. Statuses are dropped, and so is anything addressed to
    another phone number of the same business account (when `phone_number_id` is set)."""
    out: list[Inbound] = []
    for entry in body.get("entry") or []:
        for change in entry.get("changes") or []:
            if change.get("field") != "messages":
                continue
            value = change.get("value") or {}
            pnid = str((value.get("metadata") or {}).get("phone_number_id") or "")
            if phone_number_id and pnid and pnid != phone_number_id:
                continue
            names: dict[str, str | None] = {}
            for contact in value.get("contacts") or []:
                for key in (contact.get("wa_id"), contact.get("user_id")):
                    if key:
                        names[key] = (contact.get("profile") or {}).get("name")
            for message in value.get("messages") or []:
                item = _inbound(message, names)
                if item is not None:
                    out.append(item)
    return out


def _inbound(m: dict, names: dict[str, str | None]) -> Inbound | None:
    sender = m.get("from") or m.get("from_user_id")
    msg_id = m.get("id") or ""
    if not sender or not msg_id:
        return None
    try:
        ts = float(m.get("timestamp") or 0)
    except (TypeError, ValueError):
        ts = 0.0
    base = {"msg_id": msg_id, "sender": str(sender), "has_phone": bool(m.get("from")), "timestamp": ts,
            "name": names.get(sender), "forwarded": is_forwarded(m)}
    kind = m.get("type") or "other"
    if kind == "text":
        return Inbound(kind="text", text=str((m.get("text") or {}).get("body") or ""), **base)
    if kind == "interactive":
        it = m.get("interactive") or {}
        # A tapped list row is a tap like any other: {"type": "list_reply", "list_reply": {id, title, description}}
        # (the list page named above; the same shape on both providers).
        if it.get("type") in ("button_reply", "list_reply"):
            reply = it.get(it["type"]) or {}
            return Inbound(kind="button", button_id=str(reply.get("id") or ""), text=str(reply.get("title") or ""),
                           **base)
        reply = it.get("button_reply") or it.get("list_reply") or {}
        return Inbound(kind="text", text=str(reply.get("title") or ""), **base)
    if kind == "button":  # a quick reply on a template
        return Inbound(kind="text", text=str((m.get("button") or {}).get("text") or ""), **base)
    if kind == "audio":
        media = m.get("audio") or {}
        return Inbound(kind="voice" if media.get("voice") else "audio", media_id=str(media.get("id") or ""),
                       mime=str(media.get("mime_type") or "audio/ogg"), **base)
    if kind == "reaction":
        return Inbound(kind="reaction", text=str((m.get("reaction") or {}).get("emoji") or ""), **base)
    if kind in ("image", "document"):
        media = m.get(kind) if isinstance(m.get(kind), dict) else {}
        mime = str(media.get("mime_type") or "")
        if kind == "image" or is_image_mime(mime):  # a photo, or a file that is one (sent "as a document")
            return Inbound(kind="image", media_id=str(media.get("id") or ""), mime=mime,
                           caption=str(media.get("caption") or "")[:CAPTION_MAX], **base)
        return Inbound(kind="document", **base)
    if kind in ("sticker", "location", "video"):
        return Inbound(kind=kind, **base)
    return Inbound(kind="other", **base)


def is_forwarded(m: dict) -> bool:
    """The message's context says it was forwarded (once or many times): `forwarded` / `frequently_forwarded` (the
    Cloud API's names, which YCloud passes on in the same object)."""
    context = m.get("context")
    if not isinstance(context, dict):
        return False
    return any(bool(context.get(key)) for key in ("forwarded", "frequently_forwarded", "frequentlyForwarded"))


def is_image_mime(mime: str) -> bool:
    """A file that can be read as a picture: any image type, or a PDF."""
    kind = (mime or "").split(";")[0].strip().lower()
    return kind.startswith("image/") or kind in IMAGE_DOCUMENT_MIMES


# ---------------------------------------------------------------- sending


class SendError(RuntimeError):
    pass


class Graph(Protocol):
    """What the channel needs from the Cloud API (the tests pass a fake)."""

    def send_text(self, to: str, has_phone: bool, text: str) -> None: ...

    def send_buttons(self, to: str, has_phone: bool, body: str, buttons: list[tuple[str, str]],
                     header: str = "", footer: str = "") -> None: ...

    def send_list(self, to: str, has_phone: bool, body: str, button: str, rows: list[tuple[str, str, str]],
                  header: str | None = None, footer: str | None = None) -> None: ...

    def mark_read(self, msg_id: str) -> None: ...

    def media(self, media_id: str) -> tuple[bytes, str]: ...

    # Optional (voice replies): a channel without these sends text only.
    def upload_media(self, data: bytes, mime: str) -> str: ...

    def send_audio(self, to: str, has_phone: bool, media_id: str) -> None: ...


MAX_OUTBOUND_AUDIO = 16_000_000  # WhatsApp's cap for an audio upload


def audio_payload(to: str, has_phone: bool, media_id: str) -> dict:
    """An uploaded audio file sent as a voice note (played inline, with the waveform)."""
    return {**address(to, has_phone), "recipient_type": "individual", "type": "audio",
            "audio": {"id": media_id, "voice": True}}


def is_meta(url: str) -> bool:
    """The token only ever goes to Meta. A forged media url naming another host gets nothing."""
    try:
        u = httpx.URL(url)
    except Exception:
        return False
    return u.scheme == "https" and any(u.host == h[1:] or u.host.endswith(h) for h in _META_HOSTS)


def _clip(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def address(to: str, has_phone: bool) -> dict:
    """Username-only accounts arrive without a phone number and are addressed by their user id."""
    return {"to": to} if has_phone else {"recipient": to}


def text_payload(to: str, has_phone: bool, text: str) -> dict:
    return {**address(to, has_phone), "recipient_type": "individual", "type": "text",
            "text": {"preview_url": False, "body": _clip(text, BODY_MAX)}}


def buttons_payload(to: str, has_phone: bool, body: str, buttons: list[tuple[str, str]],
                    header: str = "", footer: str = "") -> dict:
    """An interactive message with up to three reply buttons (title at most 20 characters)."""
    if not 1 <= len(buttons) <= 3:
        raise ValueError("WhatsApp reply buttons: one to three")
    interactive: dict[str, Any] = {
        "type": "button",
        "body": {"text": _clip(body, INTERACTIVE_BODY_MAX)},
        "action": {"buttons": [{"type": "reply", "reply": {"id": bid[:256], "title": _clip(title, BUTTON_TITLE_MAX)}}
                               for bid, title in buttons]},
    }
    if header:
        interactive["header"] = {"type": "text", "text": _clip(header, HEADER_MAX)}
    if footer:
        interactive["footer"] = {"text": _clip(footer, FOOTER_MAX)}
    return {**address(to, has_phone), "recipient_type": "individual", "type": "interactive",
            "interactive": interactive}


def list_payload(to: str, has_phone: bool, body: str, button: str, rows: list[tuple[str, str, str]],
                 header: str | None = None, footer: str | None = None, *, section: str = "") -> dict:
    """An interactive list message: `body`, and one `button` that opens a menu of up to ten rows.

    A row is (id, title, description); the description may be empty (a pair (id, title) is taken too). Every text is
    clipped to its limit. What cannot be sent at all is a ValueError, before anything goes out: no row, more rows than
    LIST_ROWS_MAX (the caller pages), an empty body, button, row id or row title, and two rows with one id (an id is
    cut at LIST_ROW_ID_MAX, so ids must differ inside that). All rows go in one section; `section` is its title,
    which the API asks for only when there are several sections."""
    if not 1 <= len(rows) <= LIST_ROWS_MAX:
        raise ValueError(f"WhatsApp list: one to {LIST_ROWS_MAX} rows")
    if not (body or "").strip() or not (button or "").strip():
        raise ValueError("WhatsApp list: the body and the button text are required")
    out: list[dict[str, str]] = []
    for row in rows:
        rid, title, *rest = row
        rid, title = str(rid or "")[:LIST_ROW_ID_MAX], str(title or "")
        description = str(rest[0] or "") if rest else ""
        if not rid.strip() or not title.strip():
            raise ValueError("WhatsApp list: every row needs an id and a title")
        item = {"id": rid, "title": _clip(title, LIST_ROW_TITLE_MAX)}
        if description.strip():
            item["description"] = _clip(description, LIST_ROW_DESCRIPTION_MAX)
        out.append(item)
    if len({item["id"] for item in out}) != len(out):
        raise ValueError("WhatsApp list: row ids must be unique")
    only: dict[str, Any] = {"title": _clip(section, LIST_SECTION_TITLE_MAX), "rows": out} if section else {"rows": out}
    interactive: dict[str, Any] = {
        "type": "list",
        "body": {"text": _clip(body, LIST_BODY_MAX)},
        "action": {"button": _clip(button, LIST_BUTTON_MAX), "sections": [only]},
    }
    if header:
        interactive["header"] = {"type": "text", "text": _clip(header, HEADER_MAX)}
    if footer:
        interactive["footer"] = {"text": _clip(footer, FOOTER_MAX)}
    return {**address(to, has_phone), "recipient_type": "individual", "type": "interactive",
            "interactive": interactive}


class GraphClient:
    """The real Cloud API over httpx. `transport` lets tests (and dry runs) swap the network out."""

    def __init__(self, token: str, phone_number_id: str, version: str = "v25.0", *,
                 transport: httpx.BaseTransport | None = None, timeout: float = 30.0,
                 sleep=time.sleep):
        self.phone_number_id = phone_number_id
        self.base = f"https://graph.facebook.com/{version}"
        self.sleep = sleep
        self.client = httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0), transport=transport,
                                   headers={"Authorization": f"Bearer {token}"})

    def close(self) -> None:
        self.client.close()

    def _post(self, payload: dict) -> dict:
        if not self.phone_number_id:
            raise SendError("WHATSAPP_PHONE_NUMBER_ID is empty: nothing is sent")
        body = {"messaging_product": "whatsapp", **payload}
        err = ""
        for attempt in range(3):
            r = self.client.post(f"{self.base}/{self.phone_number_id}/messages", json=body)
            if r.status_code < 400:
                return r.json()
            err = r.text[:300]
            # Only rate limits are retried: a 5xx may have been delivered, and the same bubble twice
            # (a card above all) looks worse than a rare lost one.
            if r.status_code == 429 or "130429" in err or "131056" in err:
                self.sleep(1.5 * (attempt + 1))
                continue
            raise SendError(f"{r.status_code}: {err}")
        raise SendError(f"gave up after rate limits: {err}")

    def send_text(self, to: str, has_phone: bool, text: str) -> None:
        self._post(text_payload(to, has_phone, text))

    def send_buttons(self, to: str, has_phone: bool, body: str, buttons: list[tuple[str, str]],
                     header: str = "", footer: str = "") -> None:
        self._post(buttons_payload(to, has_phone, body, buttons, header, footer))

    def send_list(self, to: str, has_phone: bool, body: str, button: str, rows: list[tuple[str, str, str]],
                  header: str | None = None, footer: str | None = None, *, section: str = "") -> None:
        self._post(list_payload(to, has_phone, body, button, rows, header, footer, section=section))

    def upload_media(self, data: bytes, mime: str) -> str:
        """Upload a voice note (ogg/opus) and return its media id, for send_audio."""
        if not self.phone_number_id:
            raise SendError("WHATSAPP_PHONE_NUMBER_ID is empty: nothing is sent")
        if not data or len(data) > MAX_OUTBOUND_AUDIO:
            raise SendError("audio empty or over the size cap")
        kind = (mime or "audio/ogg").split(";")[0].strip() or "audio/ogg"
        name = "reply.ogg" if "ogg" in kind or "opus" in kind else "reply.mp3"
        r = self.client.post(f"{self.base}/{self.phone_number_id}/media",
                             data={"messaging_product": "whatsapp", "type": kind},
                             files={"file": (name, data, kind)}, timeout=60)
        if r.status_code >= 400:
            raise SendError(f"upload {r.status_code}: {r.text[:300]}")
        media_id = str((r.json() or {}).get("id") or "")
        if not media_id:
            raise SendError("upload answered without a media id")
        return media_id

    def send_audio(self, to: str, has_phone: bool, media_id: str) -> None:
        self._post(audio_payload(to, has_phone, media_id))

    def mark_read(self, msg_id: str) -> None:
        """Blue ticks and the typing dots. Cosmetic: a failure never blocks a reply."""
        try:
            self._post({"status": "read", "message_id": msg_id, "typing_indicator": {"type": "text"}})
        except Exception as exc:
            log.info("read receipt failed: %s", type(exc).__name__)

    def media(self, media_id: str) -> tuple[bytes, str]:
        """Download an inbound media file (a voice note, a photo, a PDF): its url from the Graph API, then the bytes
        (capped). The mime is what the lookup says, and empty when it says nothing: the caller then goes by what the
        webhook said (Inbound.mime)."""
        meta = self.client.get(f"{self.base}/{media_id}", params={"phone_number_id": self.phone_number_id})
        if meta.status_code >= 400:
            raise SendError(f"media lookup {meta.status_code}")
        info = meta.json()
        url = str(info.get("url") or "")
        if int(info.get("file_size") or 0) > MAX_INBOUND_MEDIA or not is_meta(url):
            raise SendError("media too large or not on a Meta host")
        chunks, size = [], 0
        with self.client.stream("GET", url) as r:
            if r.status_code != 200:
                raise SendError(f"media download {r.status_code}")
            for chunk in r.iter_bytes():
                size += len(chunk)
                if size > MAX_INBOUND_MEDIA:
                    raise SendError("media over the size cap")
                chunks.append(chunk)
        return b"".join(chunks), str(info.get("mime_type") or "")
