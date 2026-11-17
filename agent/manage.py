"""Account conversations that need no model: contacts, bill accounts, history, my account and the PIN.

Fixed patterns in Iraqi Arabic, MSA and English are matched on the user's own text before anything reaches
the core (so no phone number and no PIN ever leaves the service for these). Each flow keeps a small state
per user (like the orchestrator's open question) and its buttons use ids «m:<action>:<arg>».

- contacts: «ضيف أحمد 07701234567», «احفظ 0770… باسم أحمد», «add Ahmed 0770…», a message that is only a number
  («احفظه» / «حوّل له» / «لا»), «أسمائي», «سمّي 4567 أحمد», «غيّر اسم أحمد إلى حمودي», «امسح أحمد»;
- bill accounts: «ضيف حساب المولدة 350», «add electricity account 2024939794», «فواتيري», «امسح حساب المولدة»;
  the mock biller feed issues this month's bill as soon as an account is saved;
- «آخر العمليات» / «كشف» / «history»: the last 10 entries in and out, and today's spend against the limit;
- «حسابي» / «my account»; «غيّر اسمي»;
- «غيّر الرمز»: current PIN (checked by the wallet, counted towards the lockout), new PIN twice; «نسيت الرمز»:
  a reset request to the admin (the PIN is a hash: nobody can read it back); «الرمز»: that it is secret. A demo
  account (SIGNUP_MODE=demo, pretend money) gets a fresh PIN in the chat instead: the reply carries it in `secret`,
  so the channel keeps it out of any voice reply, and it is kept nowhere but in the wallet's hash.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from accounts import phones, pins
from accounts import texts as T
from accounts.service import Accounts, clean_name
from payments import names
from payments.messages import format_iqd, format_period
from wallet.db import BAGHDAD, from_iso

HISTORY_LIMIT = 10
CONTACTS_PAGE = 20


def _n(text: str) -> str:
    return names.normalize(text or "")


def _forms(*words: str) -> frozenset[str]:
    return frozenset(_n(w) for w in words)


SAVE_VERBS = _forms("ضيف", "ضيفي", "احفظ", "احفظي", "سجل", "سجلي", "سجّل", "اضف", "أضف", "أضيفي", "add", "save")
DELETE_VERBS = _forms("امسح", "امسحي", "احذف", "احذفي", "شيل", "شيلي", "delete", "remove")
RENAME_VERBS = _forms("سمي", "سمّي", "سم", "سمّ", "name", "rename")
ACCOUNT_WORDS = _forms("حساب", "account", "فاتورة", "فاتوره", "اشتراك")
AS_WORDS = _forms("باسم", "بأسم", "as", "named", "اسمه", "اسمها")
CONTACT_LIST = _forms("أسمائي", "اسمائي", "جهاتي", "جهات الاتصال", "جهات اتصالي", "الأسماء", "الاسماء", "contacts",
                      "my contacts", "اسماء", "الأسماء المحفوظة")
CONTACTS_MORE = _forms("الباقي", "more", "البقية")
BILL_LIST = _forms("حساباتي", "فواتيري", "حسابات الفواتير", "my bills", "my accounts", "bills", "الفواتير", "فواتير")
HISTORY = _forms("آخر العمليات", "اخر العمليات", "العمليات", "كشف", "كشف حساب", "كشف الحساب", "سجلي", "سجل العمليات",
                 "history", "transactions", "statement", "my transactions", "recent", "شنو صرفت", "شنو صرفت اليوم",
                 "عملياتي", "آخر المعاملات", "المعاملات")
MY_ACCOUNT = _forms("حسابي", "رقم حسابي", "معلومات حسابي", "my account", "account info", "account")
CHANGE_PIN = _forms("غير الرمز", "غيّر الرمز", "غير رمزي", "غيّر رمزي", "تغيير الرمز", "تغيير الرمز السري",
                    "غير الرمز السري", "change pin", "change my pin", "change the pin", "new pin")
FORGOT_PIN = _forms("نسيت الرمز", "نسيت رمزي", "نسيت الرمز السري", "نسيت الرقم السري", "نسيته", "forgot pin",
                    "forgot my pin", "i forgot my pin", "forgot the pin", "reset pin", "reset my pin")
PIN_WORD = _forms("الرمز", "رمز", "الرمز السري", "شنو الرمز", "شنو رمزي", "رمزي", "pin", "my pin", "what is my pin")
RENAME_ME = _forms("غير اسمي", "غيّر اسمي", "تغيير اسمي", "change my name", "rename me")
CANCEL = _forms("الغاء", "إلغاء", "الغي", "بطلت", "لا", "cancel", "stop", "no", "خلاص")
BILL_KIND_WORDS = {
    "electricity": _forms("كهرباء", "كهربا", "الكهرباء", "الوطنية", "وطنية", "electricity", "power"),
    "generator": _forms("مولدة", "المولدة", "مولده", "المولده", "امبير", "امبيرات", "الامبير", "generator"),
    "internet": _forms("نت", "النت", "انترنت", "الانترنت", "انترنيت", "internet", "wifi"),
    "water": _forms("ماء", "الماء", "ماي", "الماي", "مياه", "اسالة", "water"),
}


# Natural phrasings («شنو فواتيري», «show me my bills», «منو الاسماء اللي عندي»): with no digit in the message,
# once these filler words are set aside, every other word must name one topic. Anything else (an amount, «ادفع»,
# a name) is left to the orchestrator, so listing never opens a payment card.
FILLER = _forms(
    "show", "me", "my", "the", "all", "list", "see", "view", "what", "whats", "what's", "are", "is", "i", "want", "to",
    "please", "can", "could", "you", "where", "which", "do", "have", "get", "give", "check", "how", "of", "in",
    "شنو", "منو", "وين", "شوف", "شوفني", "وريني", "ورني", "اريد", "ابي", "اشوف", "رؤية", "رويه", "عرض", "اعرض",
    "اعرضلي", "من", "فضلك", "فضلج", "رجاء", "لو", "سمحت", "اللي", "الي", "عندي", "كل", "هي", "هم", "لي", "طلعلي",
    "طلعيلي", "اكو", "هنا", "بالله", "ممكن", "ارني", "أرني", "شلون", "كيف", "المحفوظة", "محفوظة", "saved", "ماكو")
TOPIC_WORDS = {
    "contacts": _forms("contacts", "contact", "names", "people", "الاسماء", "الأسماء", "اسماء", "أسماء", "اسمائي",
                       "أسمائي", "جهات", "الاتصال", "اتصالي", "جهاتي"),
    "bills": _forms("bills", "bill", "accounts", "فواتيري", "فواتير", "الفواتير", "حساباتي", "حسابات", "الحسابات"),
    "history": _forms("history", "transactions", "statement", "payments", "عملياتي", "العمليات", "عمليات",
                      "المعاملات", "معاملاتي", "كشف", "تحويلاتي", "دفعاتي"),
    "account": _forms("account", "حسابي", "فلوسي", "held", "holds", "money", "محجوزة", "المحجوزة", "محجوز",
                      "المحجوز", "المحجوزه"),
}
FUND_VERBS = _forms("اشحن", "شحن", "اشحني", "احط", "حط", "حطي", "احطلي", "اضيف", "أضيف", "اضف", "أضف", "اعبي", "عبي",
                    "اودع", "ايداع", "إيداع", "add", "put", "top", "topup", "load", "fund", "deposit", "اعبّي")
FUND_PLACES = _forms("محفظتي", "المحفظة", "محفظة", "بمحفظتي", "لمحفظتي", "حسابي", "بحسابي", "لحسابي", "wallet",
                     "account", "balance")
MONEY_WORDS = _forms("فلوس", "مال", "money", "funds", "cash", "رصيد")


ENGLISH_COMMANDS = frozenset((
    "add", "save", "delete", "remove", "name", "rename", "show", "my", "contacts", "bills", "history", "fund", "top",
    "change", "forgot", "reset", "account", "transactions", "statement", "what", "where", "deposit", "list"))


def command_language(text: str, lang: str) -> str:
    """English for an account command written in English («add Mustafa 0770…»), whatever the language so far:
    a Latin name with an Iraqi number reads like Arabizi, but the command word says English."""
    rest = phones.mask(text or "")[0]
    rest = re.sub(rf"{phones.TOKEN_PREFIX}\w+", " ", rest)
    words = rest.lower().split()
    if words and words[0].strip("?.,!") in ENGLISH_COMMANDS and not re.search("[؀-ۿ]", rest):
        return "en"
    return lang


def pin_command(text: str) -> str | None:
    """"change" or "forgot" when `text` starts the change-PIN or the forgot-PIN step, else None.

    A channel where the chat is not the account's number (Instagram) asks this before Manage.try_handle and sends
    those steps to WhatsApp: the PIN is the only payment factor, and a door opened with a link code must not be
    able to replace it, or ask the admin to, without the owner's own chat seeing it."""
    norm = _n(text)
    if norm in CHANGE_PIN:
        return "change"
    if norm in FORGOT_PIN:
        return "forgot"
    return None


def _topic(norm: str) -> str | None:
    """«fund» (how to put money in), or the list a natural phrasing asks for (contacts / bills / history /
    account), else None."""
    if re.search(r"\d", norm):
        return None
    words = [w.strip("?؟.,!،") for w in norm.split()]
    words = [w for w in words if w]
    if set(words) & FUND_VERBS and (set(words) & FUND_PLACES or (set(words) & MONEY_WORDS and "رصيد" not in words)):
        return "fund"
    if words in (["deposit"], ["ايداع"], ["إيداع"]):
        return "fund"
    rest = [w for w in words if w not in FILLER]
    if not rest:
        return None
    found = {topic for topic, forms in TOPIC_WORDS.items() for w in rest if w in forms}
    if len(found) == 1 and all(any(w in forms for forms in TOPIC_WORDS.values()) for w in rest):
        return found.pop()
    return None


@dataclass
class ManageReply:
    lines: list[str] = field(default_factory=list)
    buttons: list[tuple[str, str]] = field(default_factory=list)  # (id, title): a WhatsApp reply-button message
    kind: str = "info"
    send_to: str | None = None  # «حوّل له»: start a transfer to this number (the orchestrator asks the amount)
    secret: str = field(default="", repr=False)  # a demo account's new PIN shown in `lines`: never spoken, never logged

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass
class Step:
    name: str
    data: dict[str, Any] = field(default_factory=dict)
    tries: int = 0


class Manage:
    def __init__(self, accounts: Accounts, clock: Callable[[], datetime] | None = None):
        self.accounts = accounts
        self.wallet = accounts.wallet
        self.clock = clock or accounts.clock
        self.steps: dict[str, Step] = {}

    # ------------------------------------------------------------ entry points

    def busy(self, uid: str) -> bool:
        return uid in self.steps

    def wants_pin(self, uid: str) -> bool:
        """A change-PIN step is open: a PIN-shaped message belongs here, never to the orchestrator."""
        step = self.steps.get(uid)
        return step is not None and step.name.startswith("pin_")

    def cancel(self, uid: str) -> None:
        self.steps.pop(uid, None)

    def try_handle(self, uid: str, text: str, lang: str, gender: str, *, idle: bool = True) -> ManageReply | None:
        """A reply when `text` is an account command (or answers our own open step), else None.

        `idle`: no payment card or question of the orchestrator is open (commands that could be an answer to
        it, like a bare number, are then left to the orchestrator)."""
        say = self._say(lang, gender)
        norm = _n(text)
        step = self.steps.get(uid)
        if step is not None:
            out = self._continue(uid, step, text, norm, lang, gender)
            if out is not None or uid in self.steps:
                return out
            # The open step was dropped (the message is not an answer to it): read it as a new message.
        if not norm:
            return None
        topic = _topic(norm)
        if topic == "fund":
            self.steps[uid] = Step("fund")
            return ManageReply([say("fund_how")], [("m:fund:yes", say("btn_request")), ("m:fund:no", say("btn_no"))],
                               kind="ask")
        if norm in CHANGE_PIN:
            self.steps[uid] = Step("pin_current")
            return ManageReply([say("change_current")], kind="ask")
        if norm in FORGOT_PIN:
            if self.is_demo(uid):
                return self.demo_pin(uid, lang, gender)
            self.steps[uid] = Step("forgot")
            return ManageReply([say("forgot")], [("m:forgot:yes", say("btn_request")), ("m:forgot:no", say("btn_no"))],
                               kind="ask")
        if norm in PIN_WORD:
            return ManageReply([say("pin_is_secret")])
        if norm in RENAME_ME:
            self.steps[uid] = Step("rename_me")
            return ManageReply([say("rename_me_ask")], kind="ask")
        if norm in HISTORY:
            return self.history(uid, lang, gender)
        if norm in MY_ACCOUNT:
            return self.my_account(uid, lang, gender)
        if norm in CONTACT_LIST:
            return self.list_contacts(uid, lang, gender)
        if norm in CONTACTS_MORE and uid in self._pages:
            return self.list_contacts(uid, lang, gender, offset=self._pages[uid])
        if norm in BILL_LIST or topic == "bills":
            return self.list_bills(uid, lang, gender)
        if topic == "contacts":
            return self.list_contacts(uid, lang, gender)
        if topic == "history":
            return self.history(uid, lang, gender)
        if topic == "account":
            return self.my_account(uid, lang, gender)
        words = norm.split()
        if words and words[0] in SAVE_VERBS:
            return self._save(uid, text, words, lang, gender)
        if words and words[0] in DELETE_VERBS and len(words) > 1:
            return self._delete(uid, text, words, lang, gender)
        if words and words[0] in RENAME_VERBS and len(words) > 2:
            return self._rename(uid, text, words, lang, gender)
        m = re.match(r"^(?:غير|غيّر|بدل|change)\s+(?:اسم|name of)\s+(.+?)\s+(?:الى|إلى|ل|to)\s+(.+)$", norm)
        if m:
            return self._rename_by_name(uid, m.group(1), m.group(2), text, lang, gender)
        if idle:
            only = phones.find(text)
            rest = phones.mask(text)[0]
            if len(only) == 1 and not re.sub(rf"{phones.TOKEN_PREFIX}\w+|[\s.,،]", "", rest):
                return self._number_only(uid, only[0], lang, gender)
        return None

    def from_intent(self, uid: str, action: str, text: str, mapping: dict[str, str], lang: str,
                    gender: str) -> ManageReply:
        """The core read «history» or «add_contact» in words the patterns missed."""
        if action == "history":
            return self.history(uid, lang, gender)
        found = phones.find(text)
        e164 = next((s.e164 for s in found if s.e164), None) or next(iter(mapping.values()), None)
        say = self._say(lang, gender)
        if e164 is None:
            return ManageReply([say("contact_bad_number")], kind="refuse")
        words = [w for w in phones.mask(text)[0].split() if not w.startswith(phones.TOKEN_PREFIX)]
        named = next((i for i, w in enumerate(words) if _n(w) in AS_WORDS), None)
        name = clean_name(" ".join(words[named + 1:])) if named is not None else None  # «… باسم كرار»
        if name is not None:
            return self._add(uid, name, e164, lang, gender)
        self.steps[uid] = Step("contact_name", {"e164": e164})
        return ManageReply([say("contact_ask_name", number=phones.display(e164))], kind="ask")

    def button(self, uid: str, action: str, arg: str, lang: str, gender: str) -> ManageReply | None:
        say = self._say(lang, gender)
        step = self.steps.get(uid)
        if action == "forgot":
            self.steps.pop(uid, None)
            if arg == "yes" and self.is_demo(uid):
                return self.demo_pin(uid, lang, gender)
            if arg == "yes":
                self.accounts.request_pin_reset(uid)
                return ManageReply([say("forgot_sent")], kind="done")
            return ManageReply([say("manage_cancelled")])
        if action == "fund":
            self.steps.pop(uid, None)
            if arg == "yes":
                self.accounts.request_funding(uid)
                return ManageReply([say("fund_sent")], kind="done")
            return ManageReply([say("manage_cancelled")])
        if action in ("save", "send", "no") and step is not None and step.name == "number":
            e164 = step.data["e164"]
            self.steps.pop(uid, None)
            if action == "save":
                self.steps[uid] = Step("contact_name", {"e164": e164})
                return ManageReply([say("contact_ask_name", number=phones.display(e164))], kind="ask")
            if action == "send":
                return ManageReply(send_to=e164)
            return ManageReply([say("manage_cancelled")])
        if action == "rename" and step is not None and step.name == "contact_other":
            self.steps.pop(uid, None)
            if arg == "yes":
                self.accounts.rename_contact(uid, step.data["contact_id"], step.data["name"])
                return ManageReply([say("contact_renamed", name=step.data["name"])], kind="done")
            return ManageReply([say("manage_cancelled")])
        if action == "delete" and step is not None and step.name == "contact_delete":
            self.steps.pop(uid, None)
            if arg == "yes":
                self.accounts.delete_contact(uid, step.data["contact_id"])
                return ManageReply([say("contact_deleted", name=step.data["name"])], kind="done")
            return ManageReply([say("manage_cancelled")])
        if action == "bill" and step is not None and step.name == "bill_confirm":
            self.steps.pop(uid, None)
            if arg != "yes":
                return ManageReply([say("manage_cancelled")])
            return self._save_bill(uid, step.data, lang, gender)
        return None

    # ------------------------------------------------------------ open steps

    def _continue(self, uid: str, step: Step, text: str, norm: str, lang: str, gender: str) -> ManageReply | None:
        say = self._say(lang, gender)
        if norm in CANCEL and not step.name.startswith("pin_"):
            self.steps.pop(uid, None)
            return ManageReply([say("manage_cancelled")])
        if step.name.startswith("pin_"):
            return self._pin_step(uid, step, text, norm, lang, gender)
        if step.name == "contact_name":
            name = clean_name(text)
            if name is None:
                step.tries += 1
                if step.tries >= 3:
                    self.steps.pop(uid, None)
                    return ManageReply([say("manage_cancelled")])
                return ManageReply([say("bad_name")], kind="ask")
            self.steps.pop(uid, None)
            return self._add(uid, name, step.data["e164"], lang, gender)
        if step.name == "rename_me":
            name = clean_name(text)
            if name is None:
                return ManageReply([say("bad_name")], kind="ask")
            self.steps.pop(uid, None)
            self.accounts.rename_user(uid, name, f"user:{uid}")
            return ManageReply([say("rename_me_done", name=name)], kind="done")
        if step.name == "bill_kind":
            kind = self._kind_in(norm.split())
            if kind is None:
                self.steps.pop(uid, None)
                return None  # not an answer: the message goes on as a new request
            step.data["kind"] = kind
            return self._bill_next(uid, step, text, lang, gender)
        if step.name == "bill_biller":
            options = step.data["options"]
            index = _ordinal(norm)
            picked = options[index] if index is not None and index < len(options) else None
            if picked is None:
                picked = next((b for b in options if _n(b["name_ar"]) in norm or _n(b["name_en"] or "") in norm), None)
            if picked is None:
                self.steps.pop(uid, None)
                return None
            step.data["biller"] = picked
            return self._bill_next(uid, step, text, lang, gender)
        if step.name == "bill_number":
            number = re.sub(r"[\s\-]", "", names.normalize(text))
            if not re.fullmatch(r"[0-9a-z]{1,20}", number):
                self.steps.pop(uid, None)
                return None
            step.data["account_no"] = number
            return self._bill_next(uid, step, text, lang, gender)
        if step.name in ("number", "contact_other", "contact_delete", "bill_confirm", "forgot", "fund"):
            self.steps.pop(uid, None)  # a typed message instead of a tap: drop the question, read the message
            return None
        self.steps.pop(uid, None)
        return None

    def _pin_step(self, uid: str, step: Step, text: str, norm: str, lang: str, gender: str) -> ManageReply:
        say = self._say(lang, gender)
        if norm in CANCEL:
            self.steps.pop(uid, None)
            return ManageReply([say("manage_cancelled")])
        user = self.accounts.get(uid) or {}
        if step.name == "pin_current":
            pin = pins.digits_of(text)
            if pin is None:
                return ManageReply([say("pin_not_digits")], kind="ask")
            result = self.wallet.check_pin(uid, pin)
            if result == "ok":
                self.steps[uid] = Step("pin_new")
                return ManageReply([say("change_new")], kind="ask")
            if result in ("locked", "blocked"):
                self.steps.pop(uid, None)
                return ManageReply([say("change_locked")], kind="failed")
            return ManageReply([say("change_wrong")], kind="ask")
        if step.name == "pin_new":
            status, pin = pins.check(text, user.get("phone_e164"))
            if status != pins.OK:
                step.tries += 1
                if step.tries >= 5:
                    self.steps.pop(uid, None)
                    return ManageReply([say("manage_cancelled")])
                return ManageReply([say("pin_weak" if status == pins.WEAK else "pin_not_digits")], kind="ask")
            self.steps[uid] = Step("pin_again", {"hash": pins.hash_pin(pin)})
            return ManageReply([say("pin_again")], kind="ask")
        # pin_again
        pin = pins.digits_of(text)
        if pin is None or not pins.matches(pin, step.data.get("hash")):
            self.steps[uid] = Step("pin_new", tries=step.tries + 1)
            return ManageReply([say("pin_mismatch")], kind="ask")
        self.steps.pop(uid, None)
        self.wallet.set_pin(uid, step.data["hash"], action="pin_changed")
        return ManageReply([say("change_done")], kind="done")

    # ------------------------------------------------------------ contacts

    def _save(self, uid: str, text: str, words: list[str], lang: str, gender: str) -> ManageReply | None:
        say = self._say(lang, gender)
        if len(words) > 1 and (words[1] in ACCOUNT_WORDS or (self._kind_in(words[1:3]) and not phones.find(text))):
            return self._add_bill(uid, text, words, lang, gender)
        found = [s for s in phones.find(text)]
        if not found:
            return None  # «ضيف» without a number: not ours
        span = found[0]
        if span.e164 is None:
            return ManageReply([say("contact_bad_number")], kind="refuse")
        rest = (text[:span.start] + " " + text[span.end:]).split()
        rest = [w for w in rest if _n(w) not in SAVE_VERBS and _n(w) not in AS_WORDS and _n(w) not in ("رقم", "number")]
        name = clean_name(" ".join(rest))
        if name is None:
            self.steps[uid] = Step("contact_name", {"e164": span.e164})
            return ManageReply([say("contact_ask_name", number=phones.display(span.e164))], kind="ask")
        return self._add(uid, name, span.e164, lang, gender)

    def _add(self, uid: str, name: str, e164: str, lang: str, gender: str) -> ManageReply:
        say = self._say(lang, gender)
        status, contact = self.accounts.add_contact(uid, name, e164)
        if status == "self":
            return ManageReply([say("contact_self")], kind="refuse")
        if status == "limit":
            return ManageReply([say("contact_limit", limit=200)], kind="refuse")
        if status == "same":
            return ManageReply([say("contact_same", name=contact["display_name"])])
        if status == "other_name":
            self.steps[uid] = Step("contact_other", {"contact_id": contact["contact_id"], "name": name})
            return ManageReply([say("contact_other", old=contact["display_name"], new=name)],
                               [("m:rename:yes", say("btn_rename")), ("m:rename:no", say("btn_keep"))], kind="ask")
        lines = [say("contact_saved", name=name, number=phones.display(e164))]
        if self.accounts.by_phone(e164):
            lines.append(say("contact_has_account"))
        return ManageReply(lines, kind="done")

    def _number_only(self, uid: str, span: phones.Span, lang: str, gender: str) -> ManageReply:
        say = self._say(lang, gender)
        if span.e164 is None:
            return ManageReply([say("contact_bad_number")], kind="refuse")
        self.steps[uid] = Step("number", {"e164": span.e164})
        return ManageReply([say("number_only", number=phones.display(span.e164))],
                           [("m:save:", say("btn_save")), ("m:send:", say("btn_send")), ("m:no:", say("btn_no"))],
                           kind="ask")

    _pages: dict[str, int] = {}

    def list_contacts(self, uid: str, lang: str, gender: str, offset: int = 0) -> ManageReply:
        say = self._say(lang, gender)
        contacts = self.accounts.contacts(uid)
        if not contacts:
            return ManageReply([say("contacts_empty")])
        page = contacts[offset:offset + CONTACTS_PAGE]
        lines = [say("contacts_title", count=len(contacts))]
        lines += [f"{offset + i}) {c['display_name']} · …{(c.get('phone_e164') or c['phone'])[-4:]}"
                  + (" ✓" if c.get("account_id") else "") for i, c in enumerate(page, 1)]
        more = len(contacts) - offset - len(page)
        if more > 0:
            self._pages[uid] = offset + len(page)
            lines.append(say("contacts_more", more=more))
        else:
            self._pages.pop(uid, None)
        return ManageReply(lines)

    def _delete(self, uid: str, text: str, words: list[str], lang: str, gender: str) -> ManageReply | None:
        say = self._say(lang, gender)
        if words[1] in ACCOUNT_WORDS:
            kind = self._kind_in(words[2:])
            accounts = [a for a in self.accounts.bill_accounts(uid) if kind is None or a["biller_kind"] == kind]
            if len(accounts) != 1:
                return None
            self.accounts.delete_bill_account(uid, accounts[0]["id"])
            return ManageReply([say("bill_deleted", label=accounts[0].get("label_ar") or accounts[0]["biller_name_ar"])],
                               kind="done")
        target = " ".join(text.split()[1:])
        matches = self.accounts.find_contacts(uid, target)
        if not matches:
            return ManageReply([say("contact_not_found")], kind="refuse")
        if len(matches) > 1:
            options = "، ".join(f"{c['display_name']} (…{(c.get('phone_e164') or c['phone'])[-4:]})" for c in matches)
            return ManageReply([say("contact_which", options=options)], kind="ask")
        c = matches[0]
        self.steps[uid] = Step("contact_delete", {"contact_id": c["contact_id"], "name": c["display_name"]})
        return ManageReply([say("contact_confirm_delete", name=c["display_name"],
                                last4=(c.get("phone_e164") or c["phone"])[-4:])],
                           [("m:delete:yes", say("btn_delete")), ("m:delete:no", say("btn_no"))], kind="ask")

    def _rename(self, uid: str, text: str, words: list[str], lang: str, gender: str) -> ManageReply | None:
        """«سمّي 4567 أحمد» / «name 4567 Ahmed»: the contact whose number ends in those digits."""
        say = self._say(lang, gender)
        raw = text.split()
        if not re.fullmatch(r"\d{4,}", names.normalize(raw[1])):
            return None
        matches = self.accounts.find_contacts(uid, raw[1])
        name = clean_name(" ".join(raw[2:]))
        if not matches or name is None:
            return ManageReply([say("contact_not_found")], kind="refuse")
        if len(matches) > 1:  # the same last digits: show each whole number, so the full one can be written
            options = "، ".join(f"{c['display_name']} ({phones.display(c.get('phone_e164') or phones.canonical(c['phone']))})"
                                for c in matches)
            return ManageReply([say("contact_which", options=options)], kind="ask")
        self.accounts.rename_contact(uid, matches[0]["contact_id"], name)
        return ManageReply([say("contact_renamed", name=name)], kind="done")

    def _rename_by_name(self, uid: str, old: str, new: str, text: str, lang: str, gender: str) -> ManageReply:
        say = self._say(lang, gender)
        matches = self.accounts.find_contacts(uid, old)
        name = clean_name(new)
        if not matches or name is None:
            return ManageReply([say("contact_not_found")], kind="refuse")
        if len(matches) > 1:
            options = "، ".join(f"{c['display_name']} (…{(c.get('phone_e164') or c['phone'])[-4:]})" for c in matches)
            return ManageReply([say("contact_which", options=options)], kind="ask")
        self.accounts.rename_contact(uid, matches[0]["contact_id"], name)
        return ManageReply([say("contact_renamed", name=name)], kind="done")

    # ------------------------------------------------------------ bill accounts

    @staticmethod
    def _kind_in(words: list[str]) -> str | None:
        for word in words:
            for kind, forms in BILL_KIND_WORDS.items():
                if word in forms:
                    return kind
        return None

    def _add_bill(self, uid: str, text: str, words: list[str], lang: str, gender: str) -> ManageReply:
        data: dict[str, Any] = {"kind": self._kind_in(words[1:])}
        digits = re.findall(r"\d[\d\-]*", names.normalize(text))
        if digits:
            data["account_no"] = digits[-1].replace("-", "")
        step = Step("bill_kind", data)
        self.steps[uid] = step
        return self._bill_next(uid, step, text, lang, gender)

    def _bill_next(self, uid: str, step: Step, text: str, lang: str, gender: str) -> ManageReply:
        say = self._say(lang, gender)
        data = step.data
        kinds = T.kinds(lang)
        if not data.get("kind"):
            step.name = "bill_kind"
            return ManageReply([say("bill_kind", options="، ".join(kinds.values()))], kind="ask")
        if not data.get("biller"):
            billers = self.accounts.billers(data["kind"])
            if not billers:
                self.steps.pop(uid, None)
                return ManageReply([say("manage_cancelled")])
            if len(billers) > 1:
                step.name = "bill_biller"
                data["options"] = billers
                listed = "، ".join(f"{i}) {self._biller_name(b, lang)}" for i, b in enumerate(billers, 1))
                return ManageReply([say("bill_which_biller", options=listed)], kind="ask")
            data["biller"] = billers[0]
        biller = data["biller"]
        name = self._biller_name(biller, lang)
        if not data.get("account_no"):
            step.name = "bill_number"
            return ManageReply([say("bill_ask_number", biller=name)], kind="ask")
        pattern = biller.get("account_pattern")
        if pattern and not re.fullmatch(pattern, data["account_no"]):
            data.pop("account_no")
            step.name = "bill_number"
            step.tries += 1
            if step.tries >= 3:
                self.steps.pop(uid, None)
            return ManageReply([say("bill_bad_number", biller=name)], kind="ask")
        step.name = "bill_confirm"
        return ManageReply([say("bill_confirm", biller=name, account=data["account_no"])],
                           [("m:bill:yes", say("btn_confirm_save")), ("m:bill:no", say("btn_no"))], kind="ask")

    def _save_bill(self, uid: str, data: dict, lang: str, gender: str) -> ManageReply:
        say = self._say(lang, gender)
        biller = data["biller"]
        label = {"electricity": "كهرباء البيت", "generator": "المولدة", "internet": "النت", "water": "الماي"}.get(
            biller["kind"], biller["name_ar"])
        status, account, bill = self.accounts.add_bill_account(uid, biller["id"], data["account_no"], label)
        if status == "exists":
            return ManageReply([say("bill_exists")])
        if status == "limit":
            return ManageReply([say("bill_limit", limit=10)], kind="refuse")
        if status != "added":
            return ManageReply([say("bill_bad_number", biller=self._biller_name(biller, lang))], kind="refuse")
        shown = label if lang != "en" else T.kinds("en").get(biller["kind"], label)
        if bill is None:
            return ManageReply([say("contact_saved", name=shown, number=data["account_no"])], kind="done")
        return ManageReply([say("bill_saved", month=format_period(bill["period"], lang), amount=bill["amount_due"],
                                label=shown)], kind="done")

    def list_bills(self, uid: str, lang: str, gender: str) -> ManageReply:
        say = self._say(lang, gender)
        self.accounts.ensure_bills(uid)
        accounts = self.accounts.bill_accounts(uid)
        if not accounts:
            return ManageReply([say("bills_empty")])
        lines = [say("bills_title")]
        for a in accounts:
            unpaid = [b for b in self.accounts.bills(a["id"]) if b["status"] == "unpaid"]
            due = (say("bills_due", amount=unpaid[0]["amount_due"], month=format_period(unpaid[0]["period"], lang))
                   if unpaid else say("bills_paid"))
            lines.append(say("bills_line", label=a.get("label_ar") or a["biller_name_ar"],
                             biller=self._biller_name(a, lang), account=a["account_no"], due=due))
        return ManageReply(lines)

    @staticmethod
    def _biller_name(biller: dict, lang: str) -> str:
        if lang == "en":
            return biller.get("name_en") or biller.get("biller_name_en") or biller.get("name_ar") or ""
        return biller.get("name_ar") or biller.get("biller_name_ar") or ""

    # ------------------------------------------------------------ history and my account

    def history(self, uid: str, lang: str, gender: str) -> ManageReply:
        say = self._say(lang, gender)
        rows = self.accounts.history(uid, HISTORY_LIMIT)
        profile = self.wallet.user_profile(uid)
        if not rows:
            return ManageReply([say("history_empty")])
        lines = [say("history_title")]
        for r in rows:
            when = from_iso(r["at"]).astimezone(BAGHDAD)
            slots = {"when": f"{when.day}/{when.month} {when:%H:%M}", "amount": format_iqd(r["amount"] + r["fee"]),
                     "name": r["name"] or f"…{r['last4'] or ''}", "last4": r["last4"] or "",
                     "biller": (r["biller_en"] if lang == "en" and r["biller_en"] else r["biller_ar"]) or ""}
            kind = r["kind"]
            if kind == "transfer":
                key = "h_transfer_in" if r["direction"] == "in" else (
                    "h_transfer_held" if r["status"] == "held" else "h_transfer_out")
                if r["direction"] == "in":
                    slots["amount"] = format_iqd(r["amount"])
            elif kind == "signup_credit" and r.get("reason") == "demo_opening":
                key = "h_demo_credit"  # a demo account's pretend opening money, never «هدية فتح الحساب»
            else:
                key = f"h_{kind}" if f"h_{kind}" in T.IQ else "h_transfer_out"
            line = say(key, **slots)
            if r["status"] == "reversed":
                line += say("h_reversed")
            lines.append(line)
        if profile.get("daily_limit"):
            lines.append(say("history_today", spent=profile.get("spent_today") or 0, limit=profile["daily_limit"]))
        return ManageReply(lines)

    def my_account(self, uid: str, lang: str, gender: str) -> ManageReply:
        say = self._say(lang, gender)
        p = self.wallet.user_profile(uid)
        number = "—" if phones.is_user_key(p.get("phone_e164")) else phones.display(p.get("phone_e164"))
        lines = [say("my_account", name=p["name"], number=number,
                     status=say(f"status_{p.get('status') or 'active'}"), balance=p["balance"],
                     limit=format_iqd(p.get("daily_limit") or 0), left=format_iqd(p.get("daily_remaining") or 0))]
        if p.get("open_holds"):
            lines.append(say("my_holds", count=p["open_holds"], amount=p["held_iqd"]))
        buttons: list[tuple[str, str]] = []
        kyc = self.accounts.kyc(uid)  # None for an account from before the identity check: nothing to show
        if kyc is not None:
            lines.append(say(f"kyc_line_{kyc['level']}", single_max=kyc["single_max"]))
            if kyc["level"] != "verified":  # «وثّق حسابي» to tap: the channel starts the document step
                buttons.append(("m:verify:start", say("btn_verify")))
        return ManageReply(lines, buttons)

    # ------------------------------------------------------------ the demo account's PIN

    def is_demo(self, uid: str) -> bool:
        return (self.accounts.get(uid) or {}).get("source") == "demo"

    def demo_pin(self, uid: str, lang: str, gender: str) -> ManageReply:
        """«نسيت الرمز» on a demo account: a fresh PIN at once, shown once (the wallet keeps only its hash). The
        money is pretend, so no admin has to look; the reply's `secret` keeps it out of the voice reply."""
        user = self.accounts.get(uid) or {}
        say = self._say(lang, gender)
        if user.get("pin_state") == "blocked" or user.get("status") != "active":
            # a blocked PIN or a frozen account is never undone from the chat: the person-checked request instead
            self.steps.pop(uid, None)
            self.accounts.request_pin_reset(uid)
            return ManageReply([say("forgot_sent")], kind="done")
        own = user.get("phone_e164") if phones.dialable(user.get("phone_e164")) else None
        pin = pins.new_pin(own)
        self.wallet.set_pin(uid, pins.hash_pin(pin), action="demo_pin_issued", keep_lock=True)  # lockouts stay
        self.steps.pop(uid, None)
        return ManageReply([say("demo_new_pin"), say("demo_pin", pin=pin)], kind="done", secret=pin)

    # ------------------------------------------------------------ helpers

    @staticmethod
    def _say(lang: str, gender: str) -> Callable[..., str]:
        return lambda key, **slots: T.say(key, gender, lang, **slots)


def _ordinal(norm: str) -> int | None:
    table = {"1": 0, "١": 0, "الاول": 0, "اول": 0, "first": 0, "2": 1, "٢": 1, "الثاني": 1, "ثاني": 1, "second": 1,
             "3": 2, "٣": 2, "الثالث": 2, "third": 2}
    for word in norm.split():
        if word in table:
            return table[word]
    return None
