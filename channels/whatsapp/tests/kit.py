"""Test kit for the WhatsApp channel: a fake Graph API, a clock, webhook builders, and two accounts preloaded on
ALICE's and BOB's numbers (u1, u2: real accounts, as if they had signed up)."""

from __future__ import annotations

import copy
import itertools
import json
import threading
from datetime import datetime, timedelta, timezone

from channels.whatsapp.graph import audio_payload, buttons_payload, list_payload, sign, text_payload

SECRET = "test-app-secret"
VERIFY = "test-verify-token"
PNID = "100200300"
ALICE = "9647700000001"
BOB = "9647700000002"
PIN = "1234"

SEED = {
    "users": [
        {"id": "u1", "name": "سارة كريم", "phone": "07700000001", "balance": 500_000,
         "daily_limit": 1_000_000, "pin": PIN, "gender": "f", "source": "whatsapp"},
        {"id": "u2", "name": "علي حسن", "phone": "07700000002", "balance": 300_000,
         "daily_limit": 1_000_000, "pin": "4321", "gender": "m", "source": "whatsapp"},
    ],
    "contacts": [
        {"contact_id": "c1", "user_id": "u1", "display_name": "هادي", "phone": "07711112222"},
        {"contact_id": "c2", "user_id": "u1", "display_name": "أحمد", "phone": "07801110001"},
        {"contact_id": "c3", "user_id": "u1", "display_name": "أحمد", "phone": "07502220002"},
        {"contact_id": "c4", "user_id": "u2", "display_name": "زينب", "phone": "07903334444"},
    ],
    "billers": [
        {"id": "generator", "kind": "generator", "name_ar": "مولدة الحجي جاسم", "name_en": "Hajji Jasim Generator"},
        {"id": "asiacell", "kind": "topup", "name_ar": "آسياسيل", "name_en": "Asiacell"},
    ],
    "bill_accounts": [
        {"id": "a1", "user_id": "u1", "biller_id": "generator", "account_no": "350",
         "holder_name": "سارة كريم", "label_ar": "المولدة"},
    ],
    "bills": [
        {"id": "b1", "account_id": "a1", "period": "2026-10", "amount_due": 45_000, "status": "unpaid"},
    ],
    "transactions": [],
}


def seed() -> dict:
    return copy.deepcopy(SEED)


class Clock:
    """A clock the test moves by hand (aware UTC). 06:00 UTC is 09:00 in Baghdad."""

    def __init__(self, start: datetime | None = None):
        self.now = start or datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


class FakeGraph:
    """Records what the channel sends; serves media from a dict. Nothing touches the network."""

    def __init__(self):
        self.sent: list[dict] = []
        self.read: list[str] = []
        self.media_files: dict[str, tuple[bytes, str]] = {}
        self.uploads: list[dict] = []  # voice replies uploaded by the channel
        self.fail_uploads = False
        self._lock = threading.Lock()

    def send_text(self, to, has_phone, text):
        with self._lock:
            self.sent.append({"to": to, "payload": text_payload(to, has_phone, text)})

    def send_buttons(self, to, has_phone, body, buttons, header="", footer=""):
        with self._lock:
            self.sent.append({"to": to, "payload": buttons_payload(to, has_phone, body, buttons, header, footer)})

    def send_list(self, to, has_phone, body, button, rows, header=None, footer=None, *, section=""):
        # built first: what the real clients refuse (too many rows, an empty title) is refused here too
        payload = list_payload(to, has_phone, body, button, rows, header, footer, section=section)
        with self._lock:
            self.sent.append({"to": to, "payload": payload})

    def mark_read(self, msg_id):
        self.read.append(msg_id)

    def media(self, media_id):
        if media_id not in self.media_files:
            raise RuntimeError("no such media")
        return self.media_files[media_id]

    def upload_media(self, data, mime):
        with self._lock:
            if self.fail_uploads:
                raise RuntimeError("upload refused")
            media_id = f"up{len(self.uploads) + 1}"
            self.uploads.append({"id": media_id, "data": data, "mime": mime})
            return media_id

    def send_audio(self, to, has_phone, media_id):
        with self._lock:
            self.sent.append({"to": to, "payload": audio_payload(to, has_phone, media_id)})

    # ---- views

    def to(self, who: str) -> list[dict]:
        """What went to `who`: the conversation, without the «شلون تأكدت؟» message under each card (an add-on the
        older tests do not count; whys() has them, and `sent` everything)."""
        return [s["payload"] for s in self.sent if s["to"] == who
                and not (s["payload"]["type"] == "interactive" and is_why(s["payload"]["interactive"]))]

    def texts(self, who: str) -> list[str]:
        return [p["text"]["body"] for p in self.to(who) if p["type"] == "text"]

    def cards(self, who: str) -> list[dict]:
        """Messages with reply buttons (the payment card and the like). A list menu is in lists(), and the
        «شلون تأكدت؟» message under a card in whys()."""
        return [p["interactive"] for p in self.to(who)
                if p["type"] == "interactive" and p["interactive"]["type"] != "list"]

    def whys(self, who: str) -> list[dict]:
        """The one-button «شلون تأكدت؟» messages sent under cards."""
        return [s["payload"]["interactive"] for s in self.sent if s["to"] == who
                and s["payload"]["type"] == "interactive" and is_why(s["payload"]["interactive"])]

    def all_kinds(self, who: str) -> list[str]:
        """Every message's kind, the «شلون تأكدت؟» ones as "why"."""
        return ["why" if s["payload"]["type"] == "interactive" and is_why(s["payload"]["interactive"])
                else s["payload"]["type"] for s in self.sent if s["to"] == who]

    def lists(self, who: str) -> list[dict]:
        """List menus sent with send_list, oldest first."""
        return [p["interactive"] for p in self.to(who)
                if p["type"] == "interactive" and p["interactive"]["type"] == "list"]

    def last_list(self, who: str) -> dict:
        menus = self.lists(who)
        assert menus, f"no list sent; texts: {self.texts(who)}"
        return menus[-1]

    def audios(self, who: str) -> list[dict]:
        return [p["audio"] for p in self.to(who) if p["type"] == "audio"]

    def kinds(self, who: str) -> list[str]:
        return [p["type"] for p in self.to(who)]

    def last_card(self, who: str) -> dict:
        cards = self.cards(who)
        assert cards, f"no card sent; texts: {self.texts(who)}"
        return cards[-1]

    def everything(self, who: str) -> str:
        out = []
        for p in self.to(who):
            if p["type"] == "text":
                out.append(p["text"]["body"])
            elif p["type"] == "interactive":
                out.append(p["interactive"]["body"]["text"])
        return "\n".join(out)

    def clear(self) -> None:
        self.sent.clear()


def is_why(interactive: dict) -> bool:
    """The message under a card that holds only the explain button: «opt_why:<quote id>» (the card it explains)."""
    buttons = (interactive.get("action") or {}).get("buttons") or []
    ids = [b["reply"]["id"] for b in buttons]
    return interactive.get("type") == "button" and len(ids) == 1 and ids[0].startswith("opt_why:")


_ids = itertools.count(1)


def _message(sender: str, clock: Clock, **fields) -> dict:
    return {"from": sender, "id": fields.pop("msg_id", None) or f"wamid.{next(_ids)}",
            "timestamp": str(int(clock().timestamp())), **fields}


def text(sender: str, body: str, clock: Clock, msg_id: str | None = None) -> dict:
    return _message(sender, clock, msg_id=msg_id, type="text", text={"body": body})


def tap(sender: str, button_id: str, title: str, clock: Clock, msg_id: str | None = None) -> dict:
    return _message(sender, clock, msg_id=msg_id, type="interactive",
                    interactive={"type": "button_reply", "button_reply": {"id": button_id, "title": title}})


def voice(sender: str, media_id: str, clock: Clock) -> dict:
    return _message(sender, clock, type="audio", audio={"id": media_id, "mime_type": "audio/ogg; codecs=opus",
                                                         "voice": True})


def list_tap(sender: str, row_id: str, title: str, clock: Clock, description: str = "",
             msg_id: str | None = None) -> dict:
    """The person picked a row of a list menu."""
    reply = {"id": row_id, "title": title, **({"description": description} if description else {})}
    return _message(sender, clock, msg_id=msg_id, type="interactive",
                    interactive={"type": "list_reply", "list_reply": reply})


def image(sender: str, media_id: str, clock: Clock, mime: str = "image/jpeg", caption: str = "",
          msg_id: str | None = None) -> dict:
    """A photo. Put its bytes in FakeGraph.media_files[media_id] for the channel to download."""
    media = {"id": media_id, "mime_type": mime, "sha256": "c3BlY2ltZW4=", **({"caption": caption} if caption else {})}
    return _message(sender, clock, msg_id=msg_id, type="image", image=media)


def document(sender: str, media_id: str, clock: Clock, mime: str = "application/pdf", filename: str = "specimen.pdf",
             caption: str = "", msg_id: str | None = None) -> dict:
    """A file. One that is a picture or a PDF is parsed as kind "image"; any other stays kind "document"."""
    media = {"id": media_id, "mime_type": mime, "sha256": "c3BlY2ltZW4=", "filename": filename,
             **({"caption": caption} if caption else {})}
    return _message(sender, clock, msg_id=msg_id, type="document", document=media)


def other(sender: str, kind: str, clock: Clock) -> dict:
    payload = {"sticker": {"id": "s1"}, "image": {"id": "i1"}, "location": {"latitude": 33.3, "longitude": 44.4},
               "reaction": {"emoji": "👍", "message_id": "x"}, "video": {"id": "v1"}}.get(kind, {})
    return _message(sender, clock, type=kind, **({kind: payload} if payload else {}))


def webhook(*messages: dict, pnid: str = PNID, statuses: list[dict] | None = None) -> dict:
    value = {"messaging_product": "whatsapp", "metadata": {"phone_number_id": pnid},
             "contacts": [{"wa_id": m["from"], "profile": {"name": "tester"}} for m in messages]}
    if messages:
        value["messages"] = list(messages)
    if statuses:
        value["statuses"] = statuses
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "waba", "changes": [{"field": "messages", "value": value}]}]}


def post(client, body: dict, secret: str = SECRET, signature: str | None = None):
    raw = json.dumps(body, ensure_ascii=False).encode()
    sig = signature if signature is not None else sign(raw, secret)
    return client.post("/webhook", content=raw, headers={"Content-Type": "application/json",
                                                         "X-Hub-Signature-256": sig})


def card_buttons(card: dict) -> dict[str, str]:
    """title -> button id"""
    return {b["reply"]["title"]: b["reply"]["id"] for b in card["action"]["buttons"]}


def list_rows(menu: dict) -> dict[str, str]:
    """title -> row id, for a list menu (FakeGraph.last_list)"""
    return {row["title"]: row["id"] for part in menu["action"]["sections"] for row in part["rows"]}


# ---------------------------------------------------------------- the second provider (WHATSAPP_PROVIDER=ycloud)

YC_BUSINESS = "+9647700009999"  # the made-up number its test harness answers on
YC_INBOUND_EVENT = "whatsapp.inbound_message.received"
YC_MEDIA_LINK = "https://api.ycloud.com/v2/whatsapp/media/download/{id}?sig=abc&payload=def"


def yc_event(sender: str | None, clock: Clock, *, wamid: str | None = None, to: str = YC_BUSINESS, **fields) -> dict:
    """One inbound message event as that provider posts it. `sender` in digits (a + is added); None for a
    username-only account, which is then IQ.77."""
    n = next(_ids)
    message = {"id": f"ykit{n}", "wamid": wamid or f"wamid.kit{n}", "wabaId": "waba1", "fromUserId": "IQ.77",
               "customerProfile": {"name": "tester"}, "to": to, "sendTime": _iso(clock), **fields}
    if sender is not None:
        message["from"] = sender if sender.startswith("+") else f"+{sender}"
    return {"id": f"evtkit{n}", "type": YC_INBOUND_EVENT, "apiVersion": "v2", "createTime": _iso(clock),
            "whatsappInboundMessage": message}


def yc_list_tap(sender: str, row_id: str, title: str, clock: Clock, description: str = "", **kw) -> dict:
    reply = {"id": row_id, "title": title, **({"description": description} if description else {})}
    return yc_event(sender, clock, type="interactive", interactive={"type": "list_reply", "list_reply": reply},
                    context={"from": YC_BUSINESS.lstrip("+"), "id": "wamid.menu"}, **kw)


def yc_media_link(media_id: str) -> str:
    """The download link of an inbound media file: what Inbound.media_id holds on this provider."""
    return YC_MEDIA_LINK.format(id=media_id)


def yc_image(sender: str, media_id: str, clock: Clock, mime: str = "image/jpeg", caption: str = "",
             link: str | None = None, **kw) -> dict:
    media = {"link": yc_media_link(media_id) if link is None else link, "id": media_id, "sha256": "c3BlY2ltZW4=",
             "mime_type": mime, **({"caption": caption} if caption else {})}
    return yc_event(sender, clock, type="image", image=media, **kw)


def yc_document(sender: str, media_id: str, clock: Clock, mime: str = "application/pdf",
                filename: str = "specimen.pdf", caption: str = "", link: str | None = None, **kw) -> dict:
    media = {"link": yc_media_link(media_id) if link is None else link, "id": media_id, "sha256": "c3BlY2ltZW4=",
             "mime_type": mime, "filename": filename, **({"caption": caption} if caption else {})}
    return yc_event(sender, clock, type="document", document=media, **kw)


def _iso(clock: Clock) -> str:
    return clock().isoformat().replace("+00:00", "Z")
