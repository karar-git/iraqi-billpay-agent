"""Orchestrator: turns one user message (or one button tap) into the next reply.

Guiding rule: the core understands and talks; deterministic code touches the money.

- The core (closed source, or core.stub.StubCore offline) returns an Intent that points at the
  user's words. Every amount and payee it points at is checked against what the user actually
  wrote; if it is not there, we ask instead of guessing.
- Amounts are parsed by payments.amounts, payees resolved by payments.names, bills and accounts
  read from the wallet, and payments.rules decides what is allowed. Unclear -> one question.
- A payment is shown as a confirmation card (payments.quote) backed by a wallet quote. One open
  card per user; any new text voids it. Typed "إي" never pays: only handle_button with the card's
  quote id and the PIN executes. A PIN typed in the chat after we asked for one is caught here
  and never passed to the core.
- Execute uses the quote id as the idempotency key. A timeout or 5xx means UNKNOWN: we retry with
  the same key, then read the quote back, and never call a payment failed until the wallet says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from agent.wallet_client import WalletClient, WalletError, WalletUnavailable
from core.interface import PAYMENT_ACTIONS, Core, Intent
from payments import amounts, arabizi, names, rules
from payments.messages import render
from payments.quote import BUTTONS, Card, QuoteDetails, render_card
from payments.text import fold

BAGHDAD = timezone(timedelta(hours=3))
EXECUTE_ATTEMPTS = 2  # the first call plus one retry with the same idempotency key
NO_LIMIT = 10**12
FINAL_STATES = {"SUCCEEDED", "FAILED", "EXPIRED", "CANCELLED"}
TXN_STATUS = {"succeeded": "SUCCEEDED", "reversed": "FAILED", "pending": "EXECUTING", "unknown": "UNKNOWN"}

BUTTON_IDS = {b.label: b.id for b in BUTTONS} | {b.id: b.id for b in BUTTONS} | {
    "/pay": "confirm", "/change": "change", "/cancel": "cancel"}


def _norm_set(*words: str) -> set[str]:
    return {names.normalize(w) for w in words}


YES_WORDS = _norm_set("اي", "إي", "ايي", "ايه", "اكيد", "نعم", "صح", "تمام", "اوك", "ok", "yes", "موافق",
                      "ادفع", "ادفعها", "دزها", "حولها", "يلا", "هو", "هي", "هذا", "هاي", "زين")
NEGATIONS = _norm_set("مو", "مش", "موش", "لا", "ما")
ORDINALS = [
    _norm_set("1", "الاول", "الأولى", "اول", "اولى", "الواحد", "واحد", "١"),
    _norm_set("2", "الثاني", "الثانية", "ثاني", "ثانية", "التاني", "التانية", "٢"),
    _norm_set("3", "الثالث", "الثالثة", "ثالث", "ثالثة", "التالت", "التالتة", "٣"),
    _norm_set("4", "الرابع", "الرابعة", "رابع", "رابعة", "٤"),
]
# Words that name a kind of bill. Matching also uses each account's biller name and label.
BILL_KIND_WORDS = {
    "electricity": _norm_set("كهرباء", "كهربا", "كهربه", "وطنية", "الوطنية"),
    "generator": _norm_set("مولدة", "مولده", "مولد", "مولدات", "امبير", "امبيرات"),
    "internet": _norm_set("نت", "انترنت", "انترنيت", "واي فاي"),
    "water": _norm_set("ماي", "ماء", "مياه", "اسالة", "مي"),
}
ALL_KIND_WORDS = set().union(*BILL_KIND_WORDS.values())
OPERATOR_PREFIXES = {"zain": ("078", "079"), "asiacell": ("077",), "korek": ("075",)}
OPERATOR_WORDS = {names.normalize("زين"): "zain", names.normalize("آسيا"): "asiacell",
                  names.normalize("آسياسيل"): "asiacell", names.normalize("كورك"): "korek",
                  "zain": "zain", "asia": "asiacell", "asiacell": "asiacell", "korek": "korek"}
OPERATOR_NAMES = {"zain": "زين", "asiacell": "آسياسيل", "korek": "كورك"}
PREFIXES = ("ل", "ب", "و", "لل", "ال", "بال", "وال", "ول", "وب")


# ---------------------------------------------------------------- data


@dataclass
class Reply:
    """What the user sees after one message or tap."""

    lines: list[str] = field(default_factory=list)  # message lines (the card is separate)
    card: Card | None = None
    kind: str = "info"  # card | ask | refuse | done | failed | unknown | cancelled | info
    state: str | None = None  # wallet quote state when a quote is involved
    quote_id: str | None = None  # the card's quote id when a card is shown
    ask_field: str | None = None  # payee | amount | biller | operator | change, when kind == "ask"

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def render(self) -> str:
        """Plain-text view for a terminal: message lines, card, buttons."""
        parts = list(self.lines)
        if self.card is not None:
            parts += [""] if parts else []
            parts += [self.card.text, "  ".join(f"[{b.label}]" for b in self.card.buttons)]
        return "\n".join(parts)


@dataclass
class Request:
    """One payment the user asked for, filled in step by step."""

    action: str  # transfer | pay_bill | topup
    text: str = ""  # everything the user wrote about this request (for the verbatim check)
    payee_mention: str | None = None
    contact: dict | None = None
    biller_mention: str | None = None
    account: dict | None = None
    period: str | None = None
    amount_text: str | None = None
    currency: str | None = None
    amount: int | None = None
    operator: str | None = None
    relation_word: str | None = None  # saved as an alias once the user names the person
    notes: list[str] = field(default_factory=list)  # lines to show before the next reply


@dataclass
class Question:
    request: Request
    field: str  # payee | amount | biller | operator | change
    options: list[dict] = field(default_factory=list)  # contacts or bill accounts offered


@dataclass
class OpenCard:
    quote: dict
    request: Request
    card: Card


@dataclass
class UserState:
    card: OpenCard | None = None
    question: Question | None = None
    queue: list[Request] = field(default_factory=list)  # the rest of a two-in-one message
    aliases: dict[str, str] = field(default_factory=dict)  # relation word -> contact id
    unknown: dict[str, Request] = field(default_factory=dict)  # quote id -> request, outcome unknown
    awaiting_pin: str | None = None  # quote id of the card we just asked a PIN for
    gender: str = "f"  # from the last profile read, for replies when the wallet is down


# ---------------------------------------------------------------- text helpers


def _tokens(text: str) -> list[str]:
    return names.normalize(text or "").split()


def said(fragment: str, text: str) -> bool:
    """True when `fragment` appears in the user's own words (normalised, Arabizi read as Arabic).

    The first word may carry an attached particle in the text: "أحمد" is found in "لأحمد".
    """
    frag = _tokens(fragment)
    if not frag:
        return False
    for source in {text, arabizi.to_arabic(text)}:
        words = _tokens(source)
        for i in range(len(words) - len(frag) + 1):
            head = words[i]
            if (head == frag[0] or any(head == p + frag[0] for p in PREFIXES)) \
                    and words[i + 1:i + len(frag)] == frag[1:]:
                return True
    return False


def _is_yes(text: str) -> bool:
    words = _tokens(arabizi.to_arabic(text))
    return bool(words) and all(w in YES_WORDS for w in words)


def _ordinal(text: str) -> int | None:
    """The one option a short answer points at ("الثاني" -> 1), else None so we ask again.

    A negation ("مو الأول", "الأول لا") or two ordinals ("مو الاول، الثاني") never picks one.
    """
    words = _tokens(text)
    if not words or len(words) > 3 or any(w in NEGATIONS for w in words):
        return None
    hits = {index for index, forms in enumerate(ORDINALS) if any(w in forms for w in words)}
    return hits.pop() if len(hits) == 1 else None


def _looks_like_pin(text: str) -> bool:
    """A message that is only 4 to 8 digits (Western or Eastern)."""
    return re.fullmatch(r"\d{4,8}", re.sub(r"\s", "", fold(text or ""))) is not None


def _bare(word: str) -> str:
    """Drop a leading و / ب / ال / لل: "والماي" -> "ماي"."""
    for prefix in ("وال", "بال", "لل", "ال", "و", "ب"):
        if word.startswith(prefix) and len(word) - len(prefix) >= 2:
            return word[len(prefix):]
    return word


def _digits(text: Any) -> str:
    return re.sub(r"\D", "", names.normalize(str(text or "")))


def _display(mention: str) -> str:
    """A payee mention as we show it back: without the dative ل ("لميثم" -> "ميثم")."""
    words = (mention or "").split()
    if not words:
        return ""
    if arabizi.has_latin(mention):
        while len(words) > 1 and words[0].lower() in {"l", "li", "la", "lil", "to"}:
            words = words[1:]
        return " ".join(words)
    first = words[0]
    if first.startswith("لل"):
        first = first[1:]
    elif first.startswith("ل") and len(first) > 3:
        first = first[1:]
    return " ".join([first] + words[1:])


def _operator_of(phone: str) -> str | None:
    digits = _digits(phone)
    if digits.startswith("964"):
        digits = "0" + digits[3:]
    return next((op for op, prefixes in OPERATOR_PREFIXES.items() if digits.startswith(prefixes)), None)


def _operator_in(text: str) -> str | None:
    for word in _tokens(arabizi.to_arabic(text)) + _tokens(text):
        if _bare(word) in OPERATOR_WORDS:
            return OPERATOR_WORDS[_bare(word)]
    return None


def _previous_month(period: str) -> str:
    year, month = (int(x) for x in period.split("-"))
    year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return f"{year:04d}-{month:02d}"


def _pick_bill(bills: list[dict], hint: str | None) -> tuple[dict | None, str | None]:
    """The bill the user means: by month hint, else the oldest unpaid one, else the latest."""
    if not bills:
        return None, None
    by_period = sorted(bills, key=lambda b: b["period"])
    latest = by_period[-1]["period"]
    if hint == "current":
        target = latest
    elif hint == "previous":
        target = _previous_month(latest)
    elif hint and re.fullmatch(r"\d{4}-\d{2}", hint):
        target = hint
    else:
        unpaid = [b for b in by_period if b["status"] == "unpaid"]
        return (unpaid[0] if unpaid else by_period[-1]), None
    return next((b for b in by_period if b["period"] == target), None), target


def _target(quote: dict) -> dict:
    target = quote.get("target") or {}
    return target if isinstance(target, dict) else {}


def _details(quote: dict) -> QuoteDetails:
    """What the card shows, taken from the wallet's quote (not from what we think we asked for)."""
    t = _target(quote)
    try:
        created = datetime.fromisoformat(quote["created_at"].replace("Z", "+00:00"))
        expires = datetime.fromisoformat(quote["expires_at"].replace("Z", "+00:00"))
        minutes = max(1, round((expires - created).total_seconds() / 60))
    except (KeyError, ValueError, AttributeError):
        minutes = 5
    common = {"quote_id": quote["id"], "kind": quote["kind"], "amount": quote["amount"],
              "fee": quote.get("fee", 0), "expires_in_min": minutes}
    if quote["kind"] == "transfer":
        return QuoteDetails(**common, payee_id=t.get("contact_id", ""), payee_name=t.get("display_name", ""),
                            phone=t.get("phone", ""))
    if quote["kind"] == "bill":
        return QuoteDetails(**common, payee_id=t.get("account_id", ""), payee_name=t.get("holder_name", ""),
                            biller_name=t.get("biller_name_ar", ""), period=t.get("period", ""),
                            account_no=t.get("account_no", ""))
    return QuoteDetails(**common, payee_id=_digits(t.get("phone")), phone=t.get("phone", ""),
                        operator=t.get("biller_name_ar", ""))


def _payee_key(kind: str, target: Any) -> str:
    """Same key as payments.rules uses: contact id, bill account id, or top-up phone."""
    if not isinstance(target, dict):
        return ""
    if kind == "transfer":
        return str(target.get("contact_id") or "")
    if kind == "bill":
        return str(target.get("account_id") or "")
    return _digits(target.get("phone"))


def _parse_time(text: str) -> datetime:
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- the session


class Session:
    """Conversation state for every user, over one wallet and one core."""

    def __init__(self, wallet: Any, core: Core | None = None, *, clock: Callable[[], datetime] | None = None):
        self.wallet = wallet if isinstance(wallet, WalletClient) else WalletClient(wallet)
        if core is None:
            from core.stub import StubCore

            core = StubCore()
        self.core = core
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.users: dict[str, UserState] = {}

    def state(self, user_id: str) -> UserState:
        return self.users.setdefault(user_id, UserState())

    # ------------------------------------------------------------ public API

    def handle_text(self, user_id: str, text: str) -> Reply:
        """One typed (or transcribed) message from the user."""
        return self._turn(user_id, lambda profile: self._on_text(user_id, text or "", profile))

    def handle_button(self, user_id: str, quote_id: str, button: str, pin: str | int | None = None) -> Reply:
        """A tap on a card button. Only this can execute a payment."""
        return self._turn(user_id, lambda profile: self._on_button(user_id, quote_id, button, pin, profile))

    def reconcile(self, user_id: str) -> Reply:
        """Re-check payments whose outcome was unknown and report the ones now known."""
        reply = self._turn(user_id, lambda profile: Reply([], kind="info"))
        if not reply.lines and self.state(user_id).unknown:
            reply.kind = "unknown"
        return reply

    def _turn(self, uid: str, handle: Callable[[dict], Reply]) -> Reply:
        """Read the profile, report settled unknown payments, then run `handle`.

        The lines about earlier unknown payments are kept even when the wallet stops answering
        during the rest of the turn.
        """
        st = self.state(uid)
        try:
            profile = self.wallet.user(uid)
        except WalletUnavailable:
            return Reply([render("wallet_down", st.gender)], kind="failed")
        st.gender = self._gender(profile)
        notes = self._reconcile(uid, profile)
        try:
            reply = handle(profile)
        except WalletUnavailable:
            reply = Reply([render("wallet_down", st.gender)], kind="failed")
        reply.lines = notes + reply.lines
        return reply

    # ------------------------------------------------------------ text turns

    def _on_text(self, uid: str, text: str, profile: dict) -> Reply:
        st, g = self.state(uid), self._gender(profile)
        if st.card is not None and st.awaiting_pin == st.card.quote["id"] and _looks_like_pin(text):
            # A PIN typed in the chat: never pass it on to the core, keep the card as it is.
            return Reply([render("pin_in_chat", g)], card=st.card.card, kind="card", state="QUOTED",
                         quote_id=st.card.quote["id"])
        st.awaiting_pin = None
        if st.card is not None:
            if _is_yes(text):  # typed yes never pays: point at the button and keep the card
                reply = Reply([self._phrase("press_button", g)], card=st.card.card, kind="card",
                              state="QUOTED", quote_id=st.card.quote["id"])
                return reply
            open_card = st.card
            intent = self._understand(uid, text, profile)
            self._void_card(uid)
            if intent.action == "cancel":
                st.queue.clear()
                st.question = None
                return Reply([render("cancelled", g)], kind="cancelled", state="CANCELLED")
            if intent.action == "chat" and (intent.amount_text or intent.payee_mention or intent.biller_mention
                                            or intent.operator or intent.currency):
                st.question = Question(open_card.request, "change")  # "خليها خمسين": change the card
            reply = self._dispatch(uid, text, intent, profile)
            reply.lines = [render("card_voided", g)] + reply.lines
            return reply
        return self._dispatch(uid, text, self._understand(uid, text, profile), profile)

    def _understand(self, uid: str, text: str, profile: dict) -> Intent:
        st = self.state(uid)
        q = st.question
        context = {
            "user_id": uid,
            "gender": self._gender(profile),
            "asking": q.field if q else None,
            "pending_action": q.request.action if q else (st.card.request.action if st.card else None),
        }
        return self.core.understand(text, context)

    def _dispatch(self, uid: str, text: str, intent: Intent, profile: dict) -> Reply:
        st, g = self.state(uid), self._gender(profile)
        q = st.question
        if intent.action == "cancel":
            st.question = None
            st.queue.clear()
            return Reply([render("cancelled", g)], kind="cancelled")
        if intent.action == "balance":
            return Reply([render("balance", g, balance=profile["balance"])], kind="info")
        if intent.action == "unsupported":
            st.question = None
            st.queue.clear()
            return Reply([render("unsupported", g)], kind="refuse")
        if q is not None and intent.action in ("chat", q.request.action):
            return self._answer(uid, text, intent, profile)
        if intent.action not in PAYMENT_ACTIONS:
            return Reply([self._phrase("help", g)], kind="ask")
        st.question = None
        requests = [self._request(i, text) for i in [intent, *intent.extra] if i.action in PAYMENT_ACTIONS]
        st.queue = requests[1:]
        return self._advance(uid, requests[0], profile)

    @staticmethod
    def _request(intent: Intent, text: str) -> Request:
        return Request(intent.action, text=text, payee_mention=intent.payee_mention,
                       biller_mention=intent.biller_mention, period=intent.period,
                       amount_text=intent.amount_text, currency=intent.currency, operator=intent.operator)

    # ------------------------------------------------------------ answers to our questions

    def _answer(self, uid: str, text: str, intent: Intent, profile: dict) -> Reply:
        st = self.state(uid)
        q = st.question
        req = q.request
        req.text = f"{req.text}\n{text}"
        st.question = None
        if intent.action == req.action:  # the user restated the request: take the new parts
            self._merge(req, intent)
            return self._advance(uid, req, profile)

        if q.field == "payee":
            contact = self._pick_contact(uid, text, q.options)
            if contact is not None:
                req.contact = contact
                self._remember_alias(uid, req, profile)
            elif len(q.options) > 1 and _digits(text) and not intent.payee_mention:
                return self._ask_contacts(uid, req, q.options, profile)  # digits that fit none of them
            else:
                req.payee_mention, req.contact = intent.payee_mention or text.strip(), None
        elif q.field == "amount":
            req.amount, req.currency = None, intent.currency
            req.amount_text = intent.amount_text or text.strip()
        elif q.field == "biller":
            account = self._pick_account(text, intent, q.options)
            if account is None:
                return self._ask(uid, req, "biller", "ask_biller", profile, options=q.options)
            req.account = account
        elif q.field == "operator":
            operator = intent.operator or _operator_in(text)
            if operator is None:
                return self._ask(uid, req, "operator", "ask_operator", profile)
            req.operator = operator
        else:  # "change": a new amount, a new name or a new biller for the same request
            if not self._merge(req, intent):
                if req.action == "pay_bill":
                    req.biller_mention, req.account = text.strip(), None
                else:
                    req.payee_mention, req.contact = text.strip(), None
        return self._advance(uid, req, profile)

    @staticmethod
    def _merge(req: Request, intent: Intent) -> bool:
        """Copy the parts the new intent names into the request. True if anything changed."""
        changed = False
        if intent.payee_mention:
            req.payee_mention, req.contact, changed = intent.payee_mention, None, True
        if intent.biller_mention:
            req.biller_mention, req.account, changed = intent.biller_mention, None, True
        if intent.amount_text or intent.currency == "USD":
            req.amount_text, req.currency, req.amount, changed = intent.amount_text, intent.currency, None, True
        if intent.operator:
            req.operator, changed = intent.operator, True
        if intent.period:
            req.period, req.account, changed = intent.period, req.account, True
        return changed

    def _pick_contact(self, uid: str, text: str, options: list[dict]) -> dict | None:
        """Which contact the answer means: "الثاني", "إي", phone digits, or a name."""
        if options and _is_yes(text) and len(options) == 1:
            return options[0]
        index = _ordinal(text)
        if index is not None and index < len(options):
            return options[index]
        # Phone digits pick only among the contacts we listed. A reply that is only a number is
        # resolved later through names.resolve, which matches phone endings too.
        for digits in re.findall(r"\d{4,}", names.normalize(text)):
            by_phone = [c for c in options if _digits(c["phone"]).endswith(digits)]
            if len(by_phone) == 1:
                return by_phone[0]
        if options:
            picked = names.pick(self._candidates(uid, text, options))
            if picked:
                return next(c for c in options if c["contact_id"] == picked.contact_id)
        return None

    def _pick_account(self, text: str, intent: Intent, options: list[dict]) -> dict | None:
        if options and _is_yes(text) and len(options) == 1:
            return options[0]
        index = _ordinal(text)
        if index is not None and index < len(options):
            return options[index]
        matches = self._match_accounts(intent.biller_mention or text, options)
        return matches[0] if len(matches) == 1 else None

    def _remember_alias(self, uid: str, req: Request, profile: dict) -> None:
        """After "دز لأختي" + "زينب": remember that اختي means this contact."""
        if req.relation_word and req.contact:
            self.state(uid).aliases[req.relation_word] = req.contact["contact_id"]
            req.notes.append(render("alias_saved", self._gender(profile), name=req.contact["display_name"],
                                    word=req.relation_word))
            req.relation_word = None

    # ------------------------------------------------------------ building a payment

    def _advance(self, uid: str, req: Request, profile: dict) -> Reply:
        """Fill in what is missing, check the rules, and show a card (or ask / refuse)."""
        try:
            if req.action == "transfer":
                reply = self._transfer(uid, req, profile)
            elif req.action == "topup":
                reply = self._topup(uid, req, profile)
            else:
                reply = self._bill(uid, req, profile)
        except WalletError as exc:
            reply = self._quote_refused(uid, req, exc, profile)
        if req.notes:
            reply.lines = req.notes + reply.lines
            req.notes = []
        return reply

    def _transfer(self, uid: str, req: Request, profile: dict) -> Reply:
        ask = self._payee(uid, req, profile)
        if ask:
            return ask
        ask = self._amount(uid, req, profile, "ask_amount_for", name=req.contact["display_name"])
        if ask:
            return ask
        stop, view, flags = self._check(uid, req, profile, "transfer", req.contact["contact_id"])
        if stop:
            return stop
        quote = self.wallet.create_quote(uid, "transfer", {"contact_id": req.contact["contact_id"]}, req.amount)
        return self._show_card(uid, req, quote, view, flags)

    def _topup(self, uid: str, req: Request, profile: dict) -> Reply:
        if req.payee_mention:  # someone else's line: "دز لأحمد رصيد زين بخمسة"
            ask = self._payee(uid, req, profile)
            if ask:
                return ask
        phone = _digits(req.contact["phone"] if req.contact else profile["phone"])
        line_operator = _operator_of(phone)
        if req.operator is None:
            req.operator = line_operator
        if req.operator is None:
            return self._ask(uid, req, "operator", "ask_operator", profile)
        if line_operator and req.operator != line_operator:
            wrong = OPERATOR_NAMES.get(req.operator, req.operator)
            req.operator = None
            return self._ask(uid, req, "operator", "ask_operator_mismatch", profile, last4=phone[-4:],
                             operator=wrong)
        ask = self._amount(uid, req, profile, "ask_topup_amount")
        if ask:
            return ask
        stop, view, flags = self._check(uid, req, profile, "topup", phone)
        if stop:
            return stop
        quote = self.wallet.create_quote(uid, "topup", {"biller_id": req.operator, "phone": phone}, req.amount)
        return self._show_card(uid, req, quote, view, flags)

    def _bill(self, uid: str, req: Request, profile: dict) -> Reply:
        g = self._gender(profile)
        ask = self._account(uid, req, profile)
        if ask:
            return ask
        account = req.account
        biller = account.get("biller_name_ar") or account.get("label_ar") or ""
        bill, period = _pick_bill(self.wallet.bills(account["id"]), req.period)
        if bill is None:
            return Reply([render("no_unpaid_bill", g, biller=biller)], kind="refuse")
        if bill["status"] == "paid":
            return Reply([render("already_paid", g, biller=biller, period=bill["period"])], kind="refuse")
        req.amount = bill["amount_due"]  # the biller sets the amount
        stop, view, flags = self._check(uid, req, profile, "bill", account["id"])
        if stop:
            return stop
        quote = self.wallet.create_quote(uid, "bill", {"bill_id": bill["id"]})
        return self._show_card(uid, req, quote, view, flags)

    def _payee(self, uid: str, req: Request, profile: dict) -> Reply | None:
        """Resolve the payee to exactly one saved contact, or return the question to ask."""
        if req.contact is not None:
            return None
        mention = req.payee_mention
        if mention and not said(mention, req.text):
            mention = None  # the core pointed at words the user never wrote
        if not mention:
            return self._ask(uid, req, "payee", "ask_payee", profile)
        contacts = self.wallet.contacts(uid)
        candidates = self._candidates(uid, mention, contacts)
        picked = names.pick(candidates)
        if picked:
            req.contact = next(c for c in contacts if c["contact_id"] == picked.contact_id)
            if req.relation_word and not self._relation_word(mention):
                self._remember_alias(uid, req, profile)
            return None
        by_id = {c["contact_id"]: c for c in contacts}
        sure = names.confident(candidates)
        if len(sure) > 1:
            return self._ask_contacts(uid, req, [by_id[c.contact_id] for c in sure], profile)
        relation = self._relation_word(mention)
        if relation:
            req.relation_word = relation
            return self._ask(uid, req, "payee", "ask_relation", profile, word=relation)
        weak = [by_id[c.contact_id] for c in candidates if c.score >= 0.3][:3]
        if len(weak) == 1:
            return self._ask(uid, req, "payee", "ask_confirm_payee", profile, options=weak,
                             name=weak[0]["display_name"])
        if weak:
            return self._ask_contacts(uid, req, weak, profile)
        reply = self._ask(uid, req, "payee", "unknown_payee", profile, name=_display(mention))
        return reply

    def _candidates(self, uid: str, mention: str, contacts: list[dict]) -> list[names.Candidate]:
        aliases = self.state(uid).aliases
        if not arabizi.has_latin(mention):
            return names.resolve(mention, contacts, aliases)
        best: dict[str, names.Candidate] = {}  # Arabizi: try every Arabic reading of the name
        for reading in arabizi.latin_name_variants(mention):
            for cand in names.resolve(reading, contacts, aliases):
                if cand.contact_id not in best or cand.score > best[cand.contact_id].score:
                    best[cand.contact_id] = cand
        top = sorted(best.values(), key=lambda c: (-c.score, c.display_name, c.contact_id))
        if top and top[0].score >= names.THRESHOLD:  # keep only the best kind of match
            top = [c for c in top if c.score == top[0].score]
        return top

    @staticmethod
    def _relation_word(mention: str) -> str | None:
        readings = arabizi.latin_name_variants(mention) if arabizi.has_latin(mention) else [mention]
        for reading in readings:
            for word in names.normalize(reading).split():
                for form in (word, word[1:] if word.startswith("ل") else word):
                    if form in names.RELATION_WORDS:
                        return form
        return None

    def _account(self, uid: str, req: Request, profile: dict) -> Reply | None:
        """Resolve the bill account, or return the question (or refusal) to send."""
        if req.account is not None:
            return None
        accounts = [a for a in self.wallet.bill_accounts(uid) if a.get("biller_kind") != "topup"]
        if not req.biller_mention:
            if len(accounts) == 1:
                req.account = accounts[0]
                return None
            return self._ask(uid, req, "biller", "ask_biller", profile, options=accounts)
        matches = self._match_accounts(req.biller_mention, accounts)
        if len(matches) == 1:
            req.account = matches[0]
            return None
        if not matches:
            biller = " ".join(_tokens(arabizi.to_arabic(req.biller_mention))) or req.biller_mention
            return Reply([render("no_bill_account", self._gender(profile), biller=biller)], kind="refuse")
        return self._ask(uid, req, "biller", "ask_biller", profile, options=matches)

    @staticmethod
    def _match_accounts(mention: str, accounts: list[dict]) -> list[dict]:
        """Accounts that fit a biller mention: by kind word, then by biller name or the user's label."""
        words = [_bare(w) for w in _tokens(arabizi.to_arabic(mention))]
        kinds = {kind for w in words for kind, forms in BILL_KIND_WORDS.items() if w in forms}
        pool = [a for a in accounts if a.get("biller_kind") in kinds] if kinds else list(accounts)

        def overlap(account: dict) -> int:
            about = f"{account.get('biller_name_ar', '')} {account.get('label_ar', '')}"
            known = {_bare(w) for w in _tokens(about)}
            return sum(1 for w in words if w not in ALL_KIND_WORDS and len(w) > 1 and w in known)

        best = max((overlap(a) for a in pool), default=0)
        if best > 0:
            return [a for a in pool if overlap(a) == best]
        return pool if kinds else []

    def _amount(self, uid: str, req: Request, profile: dict, ask_key: str, **slots: Any) -> Reply | None:
        """Parse the amount from the user's own words, or return the question to ask."""
        if req.amount is not None:
            return None
        if req.amount_text and not said(req.amount_text, req.text):
            req.amount_text = None
        if req.currency == "USD":
            return self._ask(uid, req, "amount", "ask_usd", profile)
        if not req.amount_text:
            return self._ask(uid, req, "amount", ask_key, profile, **slots)
        parsed = amounts.parse(arabizi.to_arabic(req.amount_text))
        whole = self._whole_amount(req, parsed)
        if whole is not None:  # the core pointed at part of the amount ("مية" of "مية وخمسين")
            if whole.currency == "USD":
                req.currency = "USD"
                return self._ask(uid, req, "amount", "ask_usd", profile)
            return self._ask(uid, req, "amount", "ask_amount_unclear", profile)
        if not parsed.needs_question and self._approximate(req):
            parsed = amounts.Amount(None, None, True, amounts.APPROXIMATE)
        if parsed.currency == "USD":
            req.currency = "USD"
            return self._ask(uid, req, "amount", "ask_usd", profile)
        if parsed.needs_question:
            key = {amounts.APPROXIMATE: "ask_amount_exact", amounts.MULTIPLE: "ask_amount_multiple",
                   amounts.NO_AMOUNT: ask_key}.get(parsed.reason, "ask_amount_unclear")
            return self._ask(uid, req, "amount", key, profile, **(slots if key == ask_key else {}))
        req.amount = parsed.value_iqd
        return None

    @staticmethod
    def _amount_line(req: Request) -> str:
        """The latest line of the user's words that contains the core's amount_text."""
        return next((line for line in reversed(req.text.splitlines()) if said(req.amount_text, line)), "")

    def _approximate(self, req: Request) -> bool:
        """True when the user's own message hedges the amount ("حوالي خمسين"), whatever the core passed on."""
        found = amounts.find_amount(arabizi.to_arabic(self._amount_line(req)))
        return bool(found) and found[0].reason == amounts.APPROXIMATE

    def _whole_amount(self, req: Request, parsed: amounts.Amount) -> amounts.Amount | None:
        """The user's whole amount phrase when the core's amount_text is only part of it, else None.

        "دز لهادي مية وخمسين" with amount_text "مية" -> the phrase is worth 150,000, not 100,000.
        """
        fragment = arabizi.to_arabic(req.amount_text)
        phrases = amounts.amount_phrases(arabizi.to_arabic(self._amount_line(req)))
        around = [amount for amount, words in phrases if said(fragment, words) or said(req.amount_text, words)]
        if not around or any((a.value_iqd, a.currency) == (parsed.value_iqd, parsed.currency) for a in around):
            return None
        return around[0]

    def _check(self, uid: str, req: Request, profile: dict, kind: str,
               payee_id: str) -> tuple[Reply | None, rules.WalletView, list[str]]:
        """payments.rules on fresh wallet numbers. Returns (refusal or None, view, soft flags)."""
        g = self._gender(profile)
        now = self.clock()
        history, in_flight_today = [], 0
        day_start = now.astimezone(BAGHDAD).replace(hour=0, minute=0, second=0, microsecond=0)
        for t in self.wallet.transactions(uid):
            created = _parse_time(t["created_at"])
            status = TXN_STATUS.get(t.get("status", ""), str(t.get("status", "")).upper())
            history.append(rules.PastPayment(_payee_key(t["kind"], t.get("target")), t["amount"],
                                             (now - created).total_seconds() / 60, status))
            if status in ("EXECUTING", "UNKNOWN") and created >= day_start:
                in_flight_today += t["amount"]
        view = rules.WalletView(
            balance=profile["balance"],
            daily_limit=profile.get("daily_limit") or NO_LIMIT,
            spent_today=(profile.get("spent_today") or 0) + in_flight_today,
            gender=g,
            history=history,
        )
        checks = rules.check(rules.PaymentRequest(kind, req.amount, payee_id), view)
        if not checks.hard_stops:
            return None, view, checks.soft_flags
        code = checks.hard_stops[0]
        fee = rules.FEES.get(kind, 0)
        slots = {
            rules.INSUFFICIENT_BALANCE: {"balance": view.balance, "needed": (req.amount or 0) + fee},
            rules.OVER_DAILY_LIMIT: {"limit": view.daily_limit, "remaining": view.remaining_today},
        }.get(code, {})
        return Reply([render(code, g, **slots)], kind="refuse"), view, []

    def _show_card(self, uid: str, req: Request, quote: dict, view: rules.WalletView, flags: list[str]) -> Reply:
        card = render_card(_details(quote), view, flags)
        self.state(uid).card = OpenCard(quote, req, card)
        return Reply([], card=card, kind="card", state=quote.get("state", "QUOTED"), quote_id=quote["id"])

    def _quote_refused(self, uid: str, req: Request, exc: WalletError, profile: dict) -> Reply:
        """The wallet would not quote. Say so honestly; nothing was charged."""
        g = self._gender(profile)
        if exc.code == "already_paid":
            account = req.account or {}
            period = exc.detail.get("period") or ""
            return Reply([render("already_paid", g, biller=account.get("biller_name_ar", ""), period=period)],
                         kind="refuse")
        if exc.code in ("unknown_contact",):
            req.contact = None
            return self._ask(uid, req, "payee", "unknown_payee", profile, name=_display(req.payee_mention or ""))
        if exc.code == "non_positive_amount":
            return Reply([render("non_positive_amount", g)], kind="refuse")
        return Reply([render("failed", g)], kind="failed")

    # ------------------------------------------------------------ questions

    def _ask(self, uid: str, req: Request, field_name: str, key: str, profile: dict,
             options: list[dict] | None = None, **slots: Any) -> Reply:
        options = options or []
        if key == "ask_biller":
            slots["options"] = "، ".join(
                f"{i}) {a.get('label_ar') or a.get('biller_name_ar')} ({a.get('biller_name_ar')})"
                for i, a in enumerate(options, 1))
        self.state(uid).question = Question(req, field_name, options)
        if key == "unknown_payee":  # a fixed failure text, not a free question
            text = render(key, self._gender(profile), **slots)
        else:
            text = self._phrase(key, self._gender(profile), **slots)
        return Reply([text], kind="ask", ask_field=field_name)

    def _ask_contacts(self, uid: str, req: Request, options: list[dict], profile: dict) -> Reply:
        listed = "، ".join(f"{i}) {c['display_name']} (الرقم ينتهي بـ {_digits(c['phone'])[-4:]})"
                          for i, c in enumerate(options, 1))
        return self._ask(uid, req, "payee", "ask_which_payee", profile, options=options, options_text=listed)

    def _phrase(self, key: str, gender: str, **slots: Any) -> str:
        if "options_text" in slots:
            slots["options"] = slots.pop("options_text")
        return self.core.phrase(key, {"gender": gender, **slots})

    # ------------------------------------------------------------ buttons and execution

    def _on_button(self, uid: str, qid: str, button: str, pin: str | int | None, profile: dict) -> Reply:
        st, g = self.state(uid), self._gender(profile)
        action = BUTTON_IDS.get((button or "").strip())
        if st.card is None or st.card.quote["id"] != qid:
            return Reply([render("stale_card", g)], kind="refuse", quote_id=None)
        if action == "cancel":
            self._void_card(uid)
            st.queue.clear()
            return Reply([render("cancelled", g)], kind="cancelled", state="CANCELLED")
        if action == "change":
            request = st.card.request
            self._void_card(uid)
            st.question = Question(request, "change")
            if request.action == "pay_bill":  # the biller sets a bill's amount; say so
                biller = (request.account or {}).get("biller_name_ar", "")
                return Reply([self._phrase("ask_change_bill", g, biller=biller)], kind="ask", ask_field="change")
            return Reply([self._phrase("ask_change", g)], kind="ask", ask_field="change")
        if action != "confirm":
            return Reply([self._phrase("press_button", g)], card=st.card.card, kind="card", state="QUOTED",
                         quote_id=qid)
        if pin is None or str(pin).strip() == "":
            st.awaiting_pin = qid  # a PIN typed in the chat next is caught, never sent to the core
            return Reply([self._phrase("ask_pin", g)], card=st.card.card, kind="card", state="QUOTED",
                         quote_id=qid)
        return self._execute(uid, str(pin).strip(), profile)

    def _execute(self, uid: str, pin: str, profile: dict) -> Reply:
        st, g = self.state(uid), self._gender(profile)
        open_card = st.card
        qid, req = open_card.quote["id"], open_card.request
        try:
            quote, settled = self._execute_once(qid, pin)
        except WalletError as exc:
            if exc.code == "wrong_pin":  # nothing moved; the same card can be tapped again
                st.awaiting_pin = qid
                return Reply([render("wrong_pin", g)], card=open_card.card, kind="card", state="QUOTED",
                             quote_id=qid)
            if exc.code == "pin_locked":  # too many wrong PINs: nothing moved, close this card
                self._void_card(uid)
                st.queue.clear()
                return Reply([render("pin_locked", g)], kind="failed")
            st.card = None
            if exc.code == "unknown_quote":
                return Reply([render("stale_card", g)], kind="refuse")
            return Reply([render("failed", g)], kind="failed")
        st.card = None
        if quote is None:  # still no answer: tell the truth and keep checking
            st.unknown[qid] = req
            st.queue.clear()
            return Reply([render("unknown_outcome", g)], kind="unknown", state="UNKNOWN")
        reply = self._outcome(uid, quote, profile, settled)
        if st.queue:
            try:
                reply = self._next_in_queue(uid, reply, profile)
            except (WalletUnavailable, WalletError):
                # The payment above is final: report it, never replace it with a wallet error.
                st.queue.clear()
                reply.lines.append(render("next_request_failed", g))
        return reply

    def _execute_once(self, qid: str, pin: str) -> tuple[dict | None, bool]:
        """Execute with one same-key retry, then read the quote back.

        Returns (final quote or None if still unknown, whether we had to settle it ourselves).
        """
        for _ in range(EXECUTE_ATTEMPTS):
            try:
                return self.wallet.execute(qid, pin), False
            except WalletUnavailable:
                continue  # UNKNOWN: retry with the SAME key; the wallet never pays a quote twice
        return self._settle(qid), True

    def _settle(self, qid: str) -> dict | None:
        """Read an UNKNOWN quote back. If it never ran, close it so it never can, and say so."""
        try:
            quote = self.wallet.quote(qid)
            if quote["state"] in FINAL_STATES:
                return quote
            if quote["state"] == "QUOTED":
                return self.wallet.cancel(qid)
        except WalletError as exc:
            if exc.code == "not_cancellable" and isinstance(exc.detail.get("quote"), dict):
                quote = exc.detail["quote"]
                return quote if quote.get("state") in FINAL_STATES else None
        except WalletUnavailable:
            pass
        return None

    def _outcome(self, uid: str, quote: dict, profile: dict, settled: bool) -> Reply:
        """The result of an execute, in fixed words that say whether money moved."""
        g = self._gender(profile)
        state, result, t = quote["state"], quote.get("result") or {}, _target(quote)
        qid = quote["id"]
        if state == "SUCCEEDED":
            balance = result.get("balance_after", profile["balance"])
            if quote["kind"] == "transfer":
                line = render("done_transfer", g, amount=quote["amount"], name=t.get("display_name", ""),
                              balance=balance)
            elif quote["kind"] == "bill":
                line = render("done_bill", g, biller=t.get("biller_name_ar", ""), period=t.get("period", ""),
                              amount=quote["amount"], balance=balance)
            else:
                line = render("done_topup", g, amount=quote["amount"], last4=_digits(t.get("phone"))[-4:],
                              balance=balance)
            return Reply([line], kind="done", state=state)
        reason = result.get("reason")
        biller = t.get("biller_name_ar", "")
        if state == "CANCELLED":
            line = render("unknown_resolved_failed" if settled else "cancelled", g)
        elif state == "EXPIRED" or reason == "expired":
            line = render("quote_expired", g)
        elif reason == "insufficient_balance":
            fresh = self._fresh_profile(uid, profile)
            line = render(reason, g, balance=fresh["balance"], needed=quote.get("total", quote["amount"]))
        elif reason == "over_daily_limit":
            fresh = self._fresh_profile(uid, profile)
            line = render(reason, g, limit=fresh.get("daily_limit") or 0, remaining=fresh.get("daily_remaining") or 0)
        elif reason == "already_paid":
            line = render("already_paid", g, biller=biller, period=t.get("period", ""))
        elif reason == "biller_down":
            line = render("biller_down", g, biller=biller)
        elif reason == "biller_declined":
            line = render("biller_declined_refunded", g, biller=biller)
        else:
            line = render("failed", g)
        return Reply([line], kind="failed", state=state)

    def _fresh_profile(self, uid: str, profile: dict) -> dict:
        """Re-read the profile for exact numbers; the turn's copy if the wallet does not answer."""
        try:
            return self.wallet.user(uid)
        except WalletUnavailable:
            return profile

    def _next_in_queue(self, uid: str, reply: Reply, profile: dict) -> Reply:
        """Two-in-one: after the first payment, offer the next request as its own card."""
        st = self.state(uid)
        nxt = st.queue.pop(0)
        follow = self._advance(uid, nxt, self.wallet.user(uid))
        if follow.card is not None and reply.state == "SUCCEEDED":
            intro = self._phrase("offer_next", self._gender(profile), next=self._describe(nxt))
        else:
            intro = render("next_request")
        lines = reply.lines + [intro] + follow.lines
        kind = follow.kind if follow.kind in ("card", "ask") else reply.kind
        return Reply(lines, card=follow.card, kind=kind, state=follow.state or reply.state,
                     quote_id=follow.quote_id, ask_field=follow.ask_field)

    @staticmethod
    def _describe(req: Request) -> str:
        if req.action == "transfer" and req.contact:
            return f"التحويل لـ {req.contact['display_name']}"
        if req.action == "pay_bill" and req.account:
            return f"فاتورة {req.account.get('biller_name_ar', '')}"
        return "شحن الرصيد" if req.action == "topup" else "الطلب الثاني"

    # ------------------------------------------------------------ housekeeping

    def _void_card(self, uid: str) -> None:
        """Forget the open card and cancel its quote, so it can never be executed later."""
        st = self.state(uid)
        open_card, st.card = st.card, None
        if open_card is None:
            return
        try:
            self.wallet.cancel(open_card.quote["id"])
        except (WalletError, WalletUnavailable):
            pass  # already final or unreachable; we no longer hold its id, so we never execute it

    def _reconcile(self, uid: str, profile: dict) -> list[str]:
        """Lines about earlier UNKNOWN payments whose outcome is now known."""
        st = self.state(uid)
        lines = []
        for qid in list(st.unknown):
            quote = self._settle(qid)
            if quote is not None:
                del st.unknown[qid]
                lines += self._outcome(uid, quote, profile, settled=True).lines
        return lines

    @staticmethod
    def _gender(profile: dict | None) -> str:
        return "m" if (profile or {}).get("gender") == "m" else "f"
