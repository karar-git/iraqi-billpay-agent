"""The chat app's server side: one demo per browser, each with its own synthetic wallet.

A demo is one browser session id -> one in-process mock wallet (seeded from data/wallet_seed.json),
one orchestrator Session over it, and the user picked in the app. Everything the agent says comes
from the orchestrator; this module only turns its replies into JSON the page can draw (bubbles, a
confirmation card, receipts) and adds the demo controls (pick a user, reset, break the network on
purpose).

Only the orchestrator's public interface is used: Session.handle_text / handle_button / handle_option /
explain / menu / reconcile and Session.state(uid).unknown, plus the wallet client's read methods.

Options and evidence: a reply carries `options` (chips the page draws under the message; a tap posts the
option's id to /api/option, and the orchestrator looks it up against the open question, so a chip can never
pay) and `evidence` (what the code checked this turn: each check as a line in the reply's language, with
where its values came from), which the page shows in a «what I checked» expander.

Language: every reply carries the language the orchestrator chose for it ('iq' | 'msa' | 'en'); the
card's and the receipt's labels come from here in that language, so the page draws them as they are.
Voice replies: when the core can speak, a reply also carries `voice_reply` (base64 audio) under
VOICE_REPLY_MODE: mirror (default: when the user used the mic) | always | off. WEB_VOICE_REPLY_MODE,
when set, applies to the web chat instead: a host that mounts this chat next to a channel with its own
spending cap sets it to off, because this chat has no login and no budget of its own.
"""

from __future__ import annotations

import base64
import copy
import importlib
import json
import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from agent import speech
from agent.session import Reply, Session, biller_name
from agent.wallet_client import WalletClient, WalletError, WalletUnavailable
from payments.messages import render
from wallet.api import create_app
from wallet.db import hash_pin

ROOT = Path(__file__).resolve().parent.parent
SEED_PATH = Path(os.environ.get("WALLET_SEED_PATH") or ROOT / "data" / "wallet_seed.json")
DEFAULT_USER = "u02"
MAX_DEMOS = 200  # browsers kept in memory; the least recently used one is dropped first
IDLE_SECONDS = 3 * 60 * 60
PIN_LENGTH = 4
SID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
FINAL = {"SUCCEEDED", "FAILED"}
OPERATOR_BY_PREFIX = {"078": "زين", "079": "زين", "077": "آسيا", "075": "كورك"}

# Demo faults: each fires once, on the next payment.
DEMO_FAULTS = {
    "none": "none",
    "drop_after_commit": "drop_after_commit",  # the answer is lost; the same-key retry pays once
    "biller_down": "biller_down",  # the biller does not answer; nothing is taken
    "network_down": "drop_after_commit",  # plus the line stays down: the honest UNKNOWN reply
}

VOICE_NOTE = {
    "f": "الرسائل الصوتية تشتغل ويا النسخة الكاملة من المساعد. هسه اكتبيلي طلبج.",
    "m": "الرسائل الصوتية تشتغل ويا النسخة الكاملة من المساعد. هسه اكتبلي طلبك.",
}
VOICE_NOTES = {
    "iq": VOICE_NOTE,
    "msa": {"f": "الرسائل الصوتية تعمل مع النسخة الكاملة من المساعد. اكتبي لي طلبك الآن.",
            "m": "الرسائل الصوتية تعمل مع النسخة الكاملة من المساعد. اكتب لي طلبك الآن."},
    "en": {"f": "Voice notes work with the full version of the assistant. For now, please type your request.",
           "m": "Voice notes work with the full version of the assistant. For now, please type your request."},
}

# The card's and the receipt's labels, per language ({"f", "m"} where the words follow the user's gender).
UI = {
    "iq": {
        "title": {"transfer": "تحويل", "bill": "دفع فاتورة", "topup": "شحن رصيد"},
        "phone": "رقم الهاتف", "ends": "ينتهي بـ {last4}", "holder": "صاحب الحساب", "account_no": "رقم الحساب",
        "period": "عن شهر", "number": "الرقم", "network": "شبكة {name}", "fee": "العمولة", "no_fee": "بلا عمولة",
        "total": "المجموع", "balance_after": "الرصيد بعد الدفع", "dinar": "دينار", "valid": "صالحة",
        "flags": "تنبيهات", "valid_note": "البطاقة صالحة خمس دقايق.",
        "succeeded": "تمت العملية", "failed": "ما تمت العملية", "unknown": "لهسه ما نعرف النتيجة",
        "with_fee": "مع العمولة", "balance_now": "الرصيد هسه", "time": "الوقت", "reference": "رقم العملية",
        "no_repeat": {"f": "لا تعيديها، راح نتأكد", "m": "لا تعيدها، راح نتأكد"},
        "returned": {"f": "رجعت الفلوس لمحفظتج", "m": "رجعت الفلوس لمحفظتك"},
        "nothing_taken": {"f": "ما انخصم منج شي", "m": "ما انخصم منك شي"},
        "check": {"f": "تأكدي هسه", "m": "تأكد هسه"},
    },
    "msa": {
        "title": {"transfer": "تحويل", "bill": "دفع فاتورة", "topup": "شحن رصيد"},
        "phone": "رقم الهاتف", "ends": "ينتهي بـ {last4}", "holder": "صاحب الحساب", "account_no": "رقم الحساب",
        "period": "عن شهر", "number": "الرقم", "network": "شبكة {name}", "fee": "العمولة", "no_fee": "بلا عمولة",
        "total": "المجموع", "balance_after": "الرصيد بعد الدفع", "dinar": "دينار", "valid": "صالحة",
        "flags": "تنبيهات", "valid_note": "البطاقة صالحة لمدة خمس دقائق.",
        "succeeded": "تمت العملية", "failed": "لم تتم العملية", "unknown": "لا نعرف النتيجة حتى الآن",
        "with_fee": "مع العمولة", "balance_now": "الرصيد الآن", "time": "الوقت", "reference": "رقم العملية",
        "no_repeat": {"f": "لا تكرريها، سنتأكد", "m": "لا تكررها، سنتأكد"},
        "returned": "أُعيدت الأموال إلى محفظتك",
        "nothing_taken": "لم يُخصم منك شيء",
        "check": {"f": "تأكدي الآن", "m": "تأكد الآن"},
    },
    "en": {
        "title": {"transfer": "Transfer", "bill": "Bill payment", "topup": "Top-up"},
        "phone": "Phone", "ends": "ending in {last4}", "holder": "Account holder", "account_no": "Account no.",
        "period": "For", "number": "Number", "network": "{name} network", "fee": "Fee", "no_fee": "No fee",
        "total": "Total", "balance_after": "Balance after", "dinar": "dinars", "valid": "Valid",
        "flags": "Warnings", "valid_note": "The card is valid for five minutes.",
        "succeeded": "Payment done", "failed": "Payment not done", "unknown": "We don't know the result yet",
        "with_fee": "With the fee", "balance_now": "Balance now", "time": "Time", "reference": "Reference",
        "no_repeat": "Don't repeat it, we'll check", "returned": "The money went back to your wallet",
        "nothing_taken": "Nothing was taken", "check": "Check now",
    },
}


def web_voice_mode(env: dict[str, str] | None = None) -> str:
    """The web chat's voice-reply mode: WEB_VOICE_REPLY_MODE when set, else VOICE_REPLY_MODE."""
    env = os.environ if env is None else env
    own = (env.get("WEB_VOICE_REPLY_MODE") or "").strip()
    return speech.reply_mode({"VOICE_REPLY_MODE": own} if own else env)


def ui_labels(lang: str | None, gender: str = "f") -> dict:
    """The card / receipt labels of one language, gendered words resolved."""
    table = UI.get(lang or "iq", UI["iq"])
    return {k: (v["m" if gender == "m" else "f"] if isinstance(v, dict) and set(v) == {"f", "m"} else v)
            for k, v in table.items()}


# ---------------------------------------------------------------- core selection


def core_spec() -> str | None:
    """BILLPAY_CORE="package.module:Class" picks the conversational core; unset means StubCore."""
    spec = (os.environ.get("BILLPAY_CORE") or "").strip()
    return spec or None


def new_core() -> Any:
    """A fresh core instance from BILLPAY_CORE, or the open rule-based stand-in.

    Uses core.loader.load_core (the repo's one place that reads BILLPAY_CORE) when it exists.
    """
    try:
        from core.loader import load_core
    except ImportError:
        load_core = None
    if load_core is not None:
        return load_core()
    spec = core_spec()
    if spec is None:
        from core.stub import StubCore

        return StubCore()
    module_name, _, class_name = spec.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, class_name or "Core")()


def voice_of(core: Any):
    """The core's voice path, when it has one: transcribe(audio, mime, context) -> str."""
    fn = getattr(core, "transcribe", None)
    return fn if callable(fn) else None


# ---------------------------------------------------------------- seed


def load_seed(path: Path = SEED_PATH) -> dict:
    """The synthetic seed with PINs hashed once, so a new demo wallet starts in milliseconds."""
    seed = json.loads(path.read_text(encoding="utf-8"))
    for user in seed.get("users", []):
        if "pin_hash" not in user and "pin" in user:
            user["pin_hash"] = hash_pin(str(user["pin"]))
    return seed


# ---------------------------------------------------------------- network line


class Line:
    """The link between the agent and its wallet. A demo can cut it right after a payment.

    When `cut_after_execute` is armed, the line to /quotes goes down right after the next execute
    call, so the agent's retry and read-back get no answer: the honest UNKNOWN case. `restore()`
    brings it back at the start of the next request, and the agent then reports the real outcome.
    """

    def __init__(self, http: Any):
        self.http = http
        self.down = False
        self.cut_after_execute = False

    def request(self, method: str, path: str, **kwargs: Any):
        if self.down and path.startswith("/quotes"):
            raise httpx.ConnectError("demo: network down")
        response = self.http.request(method, path, **kwargs)
        if self.cut_after_execute and path.endswith("/execute"):
            self.cut_after_execute = False
            self.down = True
        return response

    def restore(self) -> None:
        self.down = False

    def close(self) -> None:
        close = getattr(self.http, "close", None)
        if callable(close):
            close()


# ---------------------------------------------------------------- one demo


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _split_lines(lines: list[str]) -> list[str]:
    """Each non-empty line of a reply is one chat bubble."""
    out = []
    for line in lines or []:
        for part in str(line).split("\n"):
            if part.strip():
                out.append(part.strip())
    return out


def _last4(phone: Any) -> str:
    return re.sub(r"\D", "", str(phone or ""))[-4:]


def _amount_words(n: int, lang: str | None = None) -> str:
    try:
        from payments.quote import amount_in_words

        return amount_in_words(n, lang)
    except Exception:  # never let a display helper break a turn
        return ""


def _period(period: str, lang: str | None = None) -> str:
    try:
        from payments.messages import format_period

        return format_period(period, lang)
    except Exception:
        return period or ""


def payee_label(kind: str, target: dict, billers: dict[str, str] | None = None, lang: str | None = None) -> str:
    """Who or what a payment went to, for cards, receipts and history."""
    target = target or {}
    if kind == "transfer":
        return str(target.get("display_name") or target.get("name") or "")
    named = (biller_name(target, lang) if target.get("biller_name_ar") else "") \
        or (billers or {}).get(str(target.get("biller_id")))
    if kind == "bill":
        return str(named or target.get("label_ar") or "")
    return str(named or "")


@dataclass
class Demo:
    """One browser's demo: its own wallet, its own orchestrator, the picked user."""

    sid: str
    seed: dict
    user_id: str = DEFAULT_USER
    voice_mode: str = field(default_factory=lambda: web_voice_mode())
    lock: threading.RLock = field(default_factory=threading.RLock)
    last_used: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        self._build()

    def _build(self) -> None:
        self.app = create_app(":memory:", seed=copy.deepcopy(self.seed))
        from fastapi.testclient import TestClient

        self.http = TestClient(self.app)
        self.line = Line(self.http)
        self.wallet = WalletClient(self.line)
        self.core = new_core()
        self.session = Session(self.wallet, self.core)
        self._billers: dict[str, str] | None = None

    def close(self) -> None:
        try:
            self.http.close()
        finally:
            db = getattr(getattr(self.app.state, "wallet", None), "db", None)
            if db is not None:
                db.close()

    # ------------------------------------------------------------ controls

    def reset(self) -> None:
        """A brand new wallet and agent, as if the browser had never been here."""
        self.close()
        self._build()

    def start(self, user_id: str) -> None:
        """Talk as another user (or start over as the same one): a fresh agent, same wallet."""
        self.user_id = user_id
        self.line.restore()
        self.session = Session(self.wallet, self.core)

    def set_fault(self, mode: str) -> dict:
        self.line.restore()
        self.line.cut_after_execute = mode == "network_down"
        wallet_mode = DEMO_FAULTS[mode]
        self.wallet.set_fault(wallet_mode, times=None if wallet_mode == "none" else 1)
        return {"mode": mode}

    # ------------------------------------------------------------ turns

    def text(self, text: str) -> dict:
        return self._turn(lambda: self.session.handle_text(self.user_id, text))

    def button(self, quote_id: str, button: str, pin: str | None) -> dict:
        self.line.restore()
        before = self._quote(quote_id)
        # Only a tap on a still-open quote can run it; a repeated tap gets the agent's words only.
        executes = (button == "confirm" and bool((pin or "").strip())
                    and before is not None and before.get("state") == "QUOTED")
        payload = self._turn(lambda: self.session.handle_button(self.user_id, quote_id, button, pin=pin),
                             quote_id=quote_id)
        if executes:
            # Show what happened to the payment: a receipt once the wallet has a final answer, or
            # the honest UNKNOWN. A wrong PIN (still QUOTED) or a closed card gets no receipt.
            receipt = self._receipt(quote_id, before, payload["reply"]["kind"])
            if receipt is not None:
                payload["receipt"] = receipt
        return payload

    def lang(self) -> str:
        try:
            return self.session.lang_of(self.user_id)
        except Exception:
            return "iq"

    def voice(self, audio: bytes, mime: str) -> dict:
        """A voice note through the core's voice path; the transcript then runs like typed text."""
        heard: dict[str, str] = {}
        handle_voice = getattr(self.session, "handle_voice", None)
        if callable(handle_voice):
            def run() -> Reply:
                reply = handle_voice(self.user_id, audio, mime)
                heard["text"] = getattr(reply, "heard", None) or ""
                return reply
        else:  # an older orchestrator without handle_voice: transcribe here, then handle as text
            def run() -> Reply:
                gender = (self.profile() or {}).get("gender", "f")
                text = str(voice_of(self.core)(audio, mime, {"user_id": self.user_id, "gender": gender}) or "")
                heard["text"] = text.strip()
                if not heard["text"]:
                    return Reply([], kind="info")  # nothing heard; the page shows its voice note
                return self.session.handle_text(self.user_id, heard["text"])
        payload = self._turn(run, user_sent_voice=True)
        payload["transcript"] = heard.get("text", "")
        return payload

    def check(self) -> dict:
        return self._turn(lambda: self.session.reconcile(self.user_id))

    def option(self, option_id: str) -> dict:
        """A tapped chip. The orchestrator resolves the id against this user's open question (or the menu); a
        chip never executes anything: money moves only through button() with the quote id and the PIN."""
        return self._turn(lambda: self.session.handle_option(self.user_id, option_id))

    def explain(self) -> dict:
        """What the agent checked on its last turn, as bubbles (an open card stays open)."""
        return self._turn(lambda: self.session.explain(self.user_id))

    def menu(self) -> dict:
        """The main menu as a reply with options, for the page to draw as chips. Nothing changes."""
        return self._reply(self.session.menu(self.user_id))

    def _turn(self, run, quote_id: str | None = None, user_sent_voice: bool = False) -> dict:
        self.line.restore()
        unknown_before = set(self._unknown())
        reply: Reply = run()
        resolved = [qid for qid in unknown_before if qid not in set(self._unknown())]
        payload = {
            "reply": self._reply(reply),
            "card": self._card(reply),
            "receipt": None,
            "resolved": [r for r in (self._receipt(qid, None, None) for qid in resolved) if r is not None],
            "profile": self.profile(),
            "voice_reply": self._voice_reply(reply, user_sent_voice),
        }
        return payload

    def _demo_pin(self) -> str:
        return next((str(u.get("pin", "")) for u in self.seed.get("users", []) if u.get("id") == self.user_id), "")

    def _voice_reply(self, reply: Reply, user_sent_voice: bool) -> dict | None:
        """The reply as audio for the page to play (VOICE_REPLY_MODE), or None. Never the PIN."""
        if not speech.wanted(self.voice_mode, user_sent_voice) or not speech.can_speak(self.core):
            return None
        words = speech.spoken_text(reply, never=(self._demo_pin(),))
        gender = (self.profile() or {}).get("gender", "f")
        audio = speech.speak(self.core, words, getattr(reply, "lang", "iq"), {"user_id": self.user_id, "gender": gender})
        if audio is None:
            return None
        data, mime = audio
        return {"mime": mime, "data": base64.b64encode(data).decode("ascii"), "text": words}

    def _unknown(self) -> list[str]:
        try:
            return list(self.session.state(self.user_id).unknown)
        except Exception:
            return []

    # ------------------------------------------------------------ views

    def profile(self) -> dict | None:
        try:
            p = self.wallet.user(self.user_id)
        except (WalletError, WalletUnavailable):
            return None
        return {
            "id": p["id"],
            "name": p["name"],
            "first_name": str(p["name"]).split(" ")[0],
            "gender": "m" if p.get("gender") == "m" else "f",
            "balance": p["balance"],
            "daily_limit": p.get("daily_limit"),
            "daily_remaining": p.get("daily_remaining"),
            "phone_last4": _last4(p.get("phone")),
        }

    def _reply(self, reply: Reply) -> dict:
        lang = getattr(reply, "lang", "iq")
        checks = list(getattr(reply, "evidence", None) or [])
        lines = self.session.why_lines(self.user_id, checks) if checks else []
        gender = self.session.state(self.user_id).gender
        return {
            "lines": _split_lines(reply.lines),
            "kind": reply.kind,
            "state": reply.state,
            "quote_id": reply.quote_id,
            "ask_field": reply.ask_field,
            "lang": lang,
            # chips to tap: the answers to the question in `lines` ("answer"), or what can be done next ("next")
            "options": [{"id": o.id, "title": o.title, "description": o.description}
                        for o in getattr(reply, "options", None) or []],
            "options_title": getattr(reply, "options_title", None),
            "options_role": getattr(reply, "options_role", "answer"),
            # what the code checked this turn: the plain values, where they came from, and one line to read
            "evidence": [{"what": c.what, "value": c.value, "source": c.source,
                          "from": self.session.source_label(self.user_id, c.source), "line": line}
                         for c, line in zip(checks, lines)],
            "why_label": render("why_button", gender, lang) if checks else None,
            "reference": getattr(reply, "reference", None),
        }

    def _quote(self, qid: str | None) -> dict | None:
        if not qid:
            return None
        try:
            return self.wallet.quote(qid)
        except (WalletError, WalletUnavailable):
            return None

    def _card(self, reply: Reply) -> dict | None:
        """The confirmation card as data: the page draws it, the buttons tap Session.handle_button."""
        card = reply.card
        if card is None or not reply.quote_id:
            return None
        quote = self._quote(reply.quote_id) or {}
        kind = quote.get("kind", "transfer")
        target = quote.get("target") or {}
        amount = int(quote.get("amount") or 0)
        fee = int(quote.get("fee") or 0)
        expires = _parse_time(quote.get("expires_at"))
        expires_in = max(0, int((expires - _now()).total_seconds())) if expires else 300
        profile = self.profile() or {}
        balance = profile.get("balance")
        lang = getattr(card, "lang", None) or getattr(reply, "lang", "iq")
        L = ui_labels(lang, profile.get("gender", "f"))
        ends = L["ends"].format(last4=_last4(target.get("phone")))
        rows: list[dict] = []
        if kind == "transfer":  # the card's headline is the payee's name
            rows.append({"label": L["phone"], "value": ends})
        elif kind == "bill":
            rows.append({"label": L["holder"], "value": str(target.get("holder_name") or "")})
            if target.get("account_no"):
                rows.append({"label": L["account_no"], "value": str(target["account_no"]), "ltr": True})
            rows.append({"label": L["period"], "value": _period(str(target.get("period") or ""), lang)})
        else:  # top-up: the headline is the network
            rows.append({"label": L["number"], "value": ends})
        payee = payee_label(kind, target, lang=lang)
        return {
            "quote_id": reply.quote_id,
            "kind": kind,
            "lang": lang,
            "labels": L,
            "title": L["title"].get(kind, L["title"]["transfer"]),
            "payee": payee if kind != "topup" else L["network"].format(name=payee),
            "amount": amount,
            "amount_words": _amount_words(amount, lang) if amount > 0 else "",
            "fee": fee,
            "total": amount + fee,
            "balance_after": None if balance is None else balance - amount - fee,
            "rows": rows,
            "flags": list(getattr(card, "flags", []) or []),
            "lines": list(getattr(card, "lines", []) or []),
            "buttons": [{"id": b.id, "label": b.label} for b in getattr(card, "buttons", [])],
            "expires_in": expires_in,
            "state": quote.get("state", "QUOTED"),
        }

    def _receipt(self, qid: str, before: dict | None, kind: str | None) -> dict | None:
        """What happened to a payment, from the wallet's own record (or UNKNOWN if it is silent)."""
        quote = self._quote(qid)
        unknown = quote is None or quote.get("state") not in FINAL
        if unknown and kind != "unknown" and qid not in set(self._unknown()):
            return None  # cancelled, expired or never ran: the agent's words say it all
        base = quote or before
        if base is None:
            return None
        result = (quote or {}).get("result") or {}
        target = base.get("target") or {}
        status = "unknown" if unknown else ("succeeded" if base["state"] == "SUCCEEDED" else "failed")
        lang = self.lang()
        return {
            "quote_id": qid,
            "status": status,
            "kind": base.get("kind"),
            "lang": lang,
            "labels": ui_labels(lang, (self.profile() or {}).get("gender", "f")),
            "payee": payee_label(base.get("kind", ""), target, lang=lang),
            "period": _period(str(target.get("period") or ""), lang) if base.get("kind") == "bill" else "",
            "amount": base.get("amount"),
            "fee": base.get("fee"),
            "total": base.get("total", (base.get("amount") or 0) + (base.get("fee") or 0)),
            "reference": result.get("transaction_id"),
            "balance_after": result.get("balance_after"),
            "completed_at": result.get("completed_at"),
            "money_returned": bool(result.get("money_returned")),
            "reason": result.get("reason"),
        }

    def history(self, limit: int = 25) -> list[dict]:
        try:
            items = self.wallet.transactions(self.user_id)
        except (WalletError, WalletUnavailable):
            return []
        if self._billers is None:  # older rows name only the biller id
            try:
                self._billers = {b["id"]: b["name_ar"] for b in self.wallet.billers()}
            except (WalletError, WalletUnavailable):
                pass
        out = []
        for item in items[:limit]:
            target = item.get("target") if isinstance(item.get("target"), dict) else {}
            out.append({
                "quote_id": item.get("quote_id"),
                "kind": item.get("kind"),
                "payee": payee_label(item.get("kind", ""), target, self._billers),
                "period": _period(str(target.get("period") or "")) if item.get("kind") == "bill" else "",
                "amount": item.get("amount"),
                "fee": item.get("fee"),
                "status": item.get("status"),
                "created_at": item.get("created_at"),
            })
        return out

    def suggestions(self) -> list[dict]:
        """Example sentences that work for this user's own contacts and bills."""
        try:
            contacts = self.wallet.contacts(self.user_id)
            accounts = self.wallet.bill_accounts(self.user_id)
            profile = self.wallet.user(self.user_id)
        except (WalletError, WalletUnavailable):
            return []
        firsts: dict[str, int] = {}
        for c in contacts:
            key = _first_key(c["display_name"])
            firsts[key] = firsts.get(key, 0) + 1
        single = [c["display_name"] for c in contacts
                  if " " not in c["display_name"].strip() and firsts[_first_key(c["display_name"])] == 1]
        dupes = [c["display_name"] for c in contacts
                 if " " not in c["display_name"].strip() and firsts[_first_key(c["display_name"])] > 1]
        labels = [a.get("label_ar") or "" for a in accounts]
        out: list[dict] = []
        if any("المولدة" in label for label in labels):
            out.append({"text": "ادفع المولدة", "hint": "فاتورة"})
        elif labels:
            out.append({"text": f"ادفع {labels[0]}", "hint": "فاتورة"})
        if single:
            out.append({"text": f"دز ل{single[0]} خمسين الف", "hint": "تحويل"})
        if dupes:
            out.append({"text": f"دز ل{dupes[0]} خمسين", "hint": "يسأل ياهو"})
        operator = OPERATOR_BY_PREFIX.get(str(profile.get("phone", ""))[:3])
        out.append({"text": f"اشحن {operator} بخمسة" if operator else "اشحن رصيدي بخمسة", "hint": "رصيد"})
        out.append({"text": "شكد رصيدي", "hint": "رصيد المحفظة"})
        if single:
            out.append({"text": f"دز ل{single[0]} ورقة", "hint": "دولار؟ يسأل"})
            out.append({"text": f"دز ل{single[0]} عشر ملايين", "hint": "أكثر من الرصيد"})
        if len(labels) > 1 and single:
            bill_word = "النت" if any("نت" in label for label in labels) else "الكهرباء"
            out.append({"text": f"ادفع {bill_word} ودز ل{single[0]} عشرة", "hint": "طلبين"})
        out.append({"text": "احجزلي تكسي", "hint": "خارج الخدمة"})
        return out


def _first_key(name: str) -> str:
    first = name.strip().split(" ")[0]
    return first.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")


# ---------------------------------------------------------------- all demos


class DemoStore:
    """Every browser's demo, in memory, least recently used dropped first."""

    def __init__(self, seed: dict | None = None, max_demos: int = MAX_DEMOS):
        self.seed = seed if seed is not None else load_seed()
        self.max_demos = max_demos
        self._demos: OrderedDict[str, Demo] = OrderedDict()
        self._lock = threading.Lock()
        probe = new_core()
        self.voice = voice_of(probe) is not None
        self.speaks = speech.can_speak(probe)
        self.users = [
            {
                "id": u["id"],
                "name": u["name"],
                "gender": "m" if u.get("gender") == "m" else "f",
                "balance": u.get("balance"),
                "demo_pin": str(u.get("pin", "")),
            }
            for u in self.seed.get("users", [])
        ]
        self.user_ids = {u["id"] for u in self.users}
        self.voice_mode = web_voice_mode()

    def get(self, sid: str) -> Demo:
        if not SID_RE.match(sid or ""):
            raise ValueError("bad session id")
        with self._lock:
            self._expire()
            demo = self._demos.get(sid)
            if demo is None:
                demo = Demo(sid, self.seed)
                self._demos[sid] = demo
            self._demos.move_to_end(sid)
            demo.last_used = time.monotonic()
            while len(self._demos) > self.max_demos:
                _, old = self._demos.popitem(last=False)
                old.close()
            return demo

    def _expire(self) -> None:
        cutoff = time.monotonic() - IDLE_SECONDS
        for sid in [s for s, d in self._demos.items() if d.last_used < cutoff]:
            self._demos.pop(sid).close()

    def demo_pin(self, user_id: str) -> str:
        return next((u["demo_pin"] for u in self.users if u["id"] == user_id), "")

    def close(self) -> None:
        with self._lock:
            while self._demos:
                _, demo = self._demos.popitem()
                demo.close()
