"""Instagram messaging webhooks: the handshake, the signature, and parsing what a person sent.

The shapes follow Meta's documentation as read on 2026-11-13 (channels/instagram/README.md lists the pages).
A webhook body is `{"object": "instagram", "entry": [{"id": <our account id>, "time": ..., "messaging": [...]}]}`
and each item of `messaging` carries `sender.id`, `recipient.id`, `timestamp` and one of `message`, `postback`,
`read`, `reaction`, `message_edit`, `referral`.

Parsing is pure and never raises: a webhook must always be answered with 200, and an unknown shape is dropped or
becomes kind "other".
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from channels.whatsapp.graph import Inbound

log = logging.getLogger("instagram.graph")

OBJECT = "instagram"
# The webhook fields this channel reads (subscribe to these in the app dashboard).
WEBHOOK_FIELDS = ("messages", "messaging_postbacks")
# Attachment types of the `messages` notification -> the kind the channel knows. Everything else (a shared post, a
# reel, a story mention, view-once media) is "other".
_ATTACHMENT_KINDS = {"audio": "audio", "image": "image", "video": "video", "file": "document"}
_HAS_CAPTION = "caption" in Inbound.__dataclass_fields__  # the transport worker's Inbound may carry one


# ---------------------------------------------------------------- webhook


def challenge(mode: str | None, token: str | None, value: str | None, verify_token: str) -> str | None:
    """The webhook handshake: Meta calls GET with hub.mode=subscribe, hub.verify_token and hub.challenge, and the
    endpoint answers with the challenge when the token is ours. None means: answer 403."""
    if not verify_token or mode != "subscribe" or not isinstance(token, str) or value is None:
        return None
    if not hmac.compare_digest(token.encode("utf-8", "replace"), verify_token.encode("utf-8", "replace")):
        return None
    return str(value)


def signature_ok(raw: bytes, header: str | None, app_secret: str) -> bool:
    """X-Hub-Signature-256: sha256=<hex HMAC-SHA256 of the raw body with the app secret>."""
    if not app_secret or not isinstance(header, str) or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
    # bytes on both sides: a forged header with a non-ASCII character is a mismatch, not a TypeError
    given = header[len("sha256="):].strip().lower().encode("utf-8", "replace")
    return hmac.compare_digest(expected.encode(), given)


def sign(raw: bytes, app_secret: str) -> str:
    """The header value Meta would send for this body (tests and local tools)."""
    return "sha256=" + hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- parsing


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _id(value: Any) -> str:
    """An id as text. Meta sends ids as strings; a bare number is accepted, anything else is no id."""
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    return value.strip() if isinstance(value, str) else ""


def _seconds(value: Any) -> float:
    """The documentation's samples are 13 digits (milliseconds) and name no unit: both units are read."""
    if isinstance(value, bool):
        return 0.0
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return 0.0
    if ts != ts or ts <= 0 or ts == float("inf"):
        return 0.0
    return ts / 1000.0 if ts > 1e11 else ts


def parse(body: Any, own_id: str = "") -> list[Inbound]:
    """Every message a person sent us in a webhook body, flattened to the channel's `Inbound`.

    The sender is the Instagram-scoped user id (`has_phone` is always False). A tapped quick reply and a tapped
    template button both arrive as kind "button" with their payload as `button_id`. An audio or image attachment
    carries its link as `media_id` (the client downloads it only from Meta's own hosts).

    Dropped: echoes of what we sent, anything from our own account (`own_id`), anything addressed to another
    account, read receipts, reactions, edits, deleted messages, referrals, and whatever arrives while another app
    holds the conversation (`standby`). Never raises."""
    if not isinstance(body, dict) or body.get("object") != OBJECT:
        return []
    own = _id(own_id)
    out: list[Inbound] = []
    for entry in _list(body.get("entry")):
        if not isinstance(entry, dict):
            continue
        events = list(_list(entry.get("messaging")))
        # Some set-ups wrap the same event as {"field": "messages", "value": {...}} under `changes`.
        for change in _list(entry.get("changes")):
            if isinstance(change, dict) and change.get("field") in WEBHOOK_FIELDS:
                events.append(change.get("value"))
        for event in events:
            try:
                out += _event(event, own)
            except Exception as exc:  # an unknown shape is never an error for the webhook
                log.warning("dropped a malformed event: %s", type(exc).__name__)
    return out


def _event(event: Any, own: str) -> list[Inbound]:
    if not isinstance(event, dict):
        return []
    sender = _id(_dict(event.get("sender")).get("id"))
    recipient = _id(_dict(event.get("recipient")).get("id"))
    if not sender or (own and sender == own):
        return []
    if own and recipient and recipient != own:
        log.warning("dropped an event addressed to another account")
        return []
    ts = _seconds(event.get("timestamp"))
    if isinstance(event.get("postback"), dict):
        return _postback(event["postback"], sender, ts, event.get("timestamp"))
    if isinstance(event.get("message"), dict):
        return _message(event["message"], sender, ts)
    return []  # read, reaction, message_edit, referral, optin, handover: nobody asked the agent anything


def _postback(postback: dict, sender: str, ts: float, raw_ts: Any) -> list[Inbound]:
    payload = _str(postback.get("payload"))
    # Old API versions send a postback without a message id: one tap is then named by who tapped what, and when.
    msg_id = _id(postback.get("mid")) or "postback:" + hashlib.sha256(
        f"{sender}|{raw_ts}|{payload}".encode("utf-8", "replace")).hexdigest()[:32]
    return [Inbound(msg_id=msg_id, sender=sender, has_phone=False, kind="button", button_id=payload,
                    text=_str(postback.get("title")), timestamp=ts)]


def _message(m: dict, sender: str, ts: float) -> list[Inbound]:
    if m.get("is_echo") or m.get("is_self") or m.get("is_deleted"):
        return []
    msg_id = _id(m.get("mid"))
    if not msg_id:
        return []  # a handover notice, or nothing that could be told apart from its own retry
    base: dict[str, Any] = {"sender": sender, "has_phone": False, "timestamp": ts}
    text = _str(m.get("text"))
    reply = m.get("quick_reply")
    if isinstance(reply, dict) and _str(reply.get("payload")):
        return [Inbound(msg_id=msg_id, kind="button", button_id=_str(reply.get("payload")), text=text, **base)]
    if m.get("is_unsupported"):
        return [Inbound(msg_id=msg_id, kind="other", **base)]
    kinds = [(_ATTACHMENT_KINDS.get(_str(a.get("type")).lower(), "other"), _str(_dict(a.get("payload")).get("url")))
             for a in _list(m.get("attachments")) if isinstance(a, dict)]
    media = [(kind, url) for kind, url in kinds if kind in ("audio", "image")]
    items: list[Inbound] = []
    for n, (kind, url) in enumerate(media):
        # several attachments share one message id: each gets its own, so that a retry is still taken once
        item = Inbound(msg_id=msg_id if n == 0 else f"{msg_id}#{n + 1}", kind=kind, media_id=url, **base)
        if kind == "image" and text and n == 0:  # words sent with a picture
            setattr(item, "caption" if _HAS_CAPTION else "text", text)
        items.append(item)
    if items:
        return items
    if text:  # words beside a shared post or a story reply are still words
        return [Inbound(msg_id=msg_id, kind="text", text=text, **base)]
    return [Inbound(msg_id=msg_id, kind=kinds[0][0] if kinds else "other", **base)]
