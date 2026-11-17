"""OpenModelCore: an open reference core that reads messages with real models, through the model layer.

    BILLPAY_CORE=core.open_model:OpenModelCore        (no arguments; see core/README.md)
    LLM_UNDERSTAND="ollama:qwen2.5:7b"                 the generative reading, hosted or fully local (docs/MODELS.md)
    LLM_STT="whisper:whisper-large-v3"                 optional: voice notes

It is not the production core. It shows the shape any core can take: small models first, a generative model
only for the job that needs one, and code that checks every answer.

What understand() does with one message, in order:

1. local    An empty message or a message with no letters (digits, spaces and signs only) is read by the
            rule-based stand-in, in this process; a message that names a PIN («الرمز 5678») is plain chat.
            A PIN never leaves the machine.
2. direct   `Gate.before`: when the decision model is confident the message is a request with nothing to fill
            in (balance, cancel), that is the answer. No generative call.
3. generative  `llm.json("understand", ...)`: a generative model points at the user's own words (who, which
            bill, how much) in a strict JSON schema. Code then keeps only what the user wrote:
            - a payee, a bill or an amount that is not written in the message is dropped;
            - a pointer at part of a number («50» of «50 الف») becomes the whole number as code reads it, and
              a rewritten amount («50000» for «خمسين الف») becomes the user's own words when both read as the
              same number, else it is dropped;
            - the currency, the network and the month are kept only when the user's words say them.
            The orchestrator checks all of this again.
4. cross-check  `Gate.after`: when the generative model says "a payment" and the decision model is confident of
            the opposite (or the reverse), the reading becomes a plain question.
5. fallback `Gate.fallback`: when no route answered, or the answer was unusable, the decision model's action
            with NO slots. The agent then asks for each part with its own fixed questions. The stand-in's
            keyword reading is never used for a payment.

Decision-only mode. With no usable "understand" route the core still works: steps 1, 2 and 5 only. Every
payment request is recognised by the decision model and filled in by questions (who, how much, which bill),
each answer read by the orchestrator's deterministic code. Nothing is sent to any model server.

phrase() returns the fixed templates of payments.messages, with no model call. transcribe() exists only when
an "stt" route is usable, so `isinstance(core, VoiceCore)` says the truth. evidence(user_id) says what was
checked on that user's last message. No model here touches money: a card, a tap and the PIN do.

What goes to the generative model: the message (phone numbers already replaced by placeholders by the
orchestrator), and at most three fixed sentences about the situation (the open question, an open request, a
voice note). Not the history, not the saved names, not a balance, never a PIN.
"""

from __future__ import annotations

import os
import re
import threading
from collections import OrderedDict
from typing import Any, Mapping

from agent import pins
from core.interface import ACTIONS, OPERATORS, PAYMENT_ACTIONS, Intent
from core.stub import StubCore
from decision import Gate
from llm import LLM, LLMError, Registry
from payments import amounts, arabizi, names, periods
from payments.text import fold

ROLE = "understand"
STT_ROLE = "stt"
ENV_TIMEOUT = "OPEN_CORE_TIMEOUT_S"            # seconds one generative reading may take (default 20)
ENV_STT_LANGUAGE = "OPEN_CORE_STT_LANGUAGE"    # optional hint for the speech model, e.g. "ar"
DEFAULT_TIMEOUT_S = 20.0
MAX_TOKENS = 600
MAX_CHARS = 2000        # a longer message is not sent to the generative model
MAX_MENTION_CHARS = 80
MAX_REQUESTS = 3        # requests read from one message
MAX_USERS = 2000        # users whose last evidence is kept
LANGUAGES = ("iq", "msa", "en")
ROUTE_LOCAL = "local rules"
ROUTE_DECISION = "decision model"
ROUTE_DECISION_ONLY = "decision model (decision-only mode)"

_NULLABLE = ["string", "null"]
REQUEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "payee_mention": {"type": _NULLABLE},
        "biller_mention": {"type": _NULLABLE},
        "amount_text": {"type": _NULLABLE},
        "currency": {"type": _NULLABLE, "enum": ["IQD", "USD", None]},
        "operator": {"type": _NULLABLE, "enum": [*OPERATORS, None]},
        "period": {"type": _NULLABLE},
    },
    "required": ["action", "payee_mention", "biller_mention", "amount_text", "currency", "operator", "period"],
    "additionalProperties": False,
}
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "requests": {"type": "array", "items": REQUEST_SCHEMA},
        "language": {"type": "string", "enum": list(LANGUAGES)},
        "language_sure": {"type": "boolean"},
    },
    "required": ["requests", "language", "language_sure"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You read one message a user sent to a wallet assistant in Iraq and say what the user asks for.
The message can be Iraqi Arabic, Modern Standard Arabic, Arabic typed in Latin letters, or English.
You do not answer the user and you do not decide anything. You only fill in the JSON.

"requests": the requests in the message, in order. Usually one. Each has an "action":
- pay_bill: pay a bill or a subscription (electricity, generator, internet, water)
- transfer: send money to a person or to a phone number
- topup: buy mobile credit for a line
- balance: the user asks how much money they have
- history: the user asks about past payments
- add_contact: the user asks to save a person or a number
- cancel: the user stops or takes back a request
- unsupported: a service this wallet does not have (a loan, a flight, cash out, money abroad, games)
- chat: anything else: a greeting, a question, a story about the past, or a short answer to the assistant

The other fields point at the user's own words. Copy them from the message letter for letter, in the same
script and with the same digits. Do not translate, correct, complete, add up or convert anything.
When the user did not write it, the field is null. Never guess.
- payee_mention: who the money or the credit is for, as written ("لأحمد", "امي", "l ahmad").
  A word like هاتف_ا stands for a phone number: copy it as it is. For the user's own line or account it is null.
- biller_mention: the words that name the bill ("المولدة", "سما نت", "the internet").
- amount_text: all the amount words, as written ("خمسين", "50 الف", "ورقة", "fifty thousand").
- currency: "USD" only when the user wrote dollars, "IQD" when the user wrote dinars, else null.
- operator: "zain", "asiacell" or "korek", only when the user named the network.
- period: for a bill only, and only when the user said which month: "current" (this month),
  "previous" (last month), or the month as two digits ("06").

When the message only answers a question the assistant asked, use action "chat" and fill the one field the
answer gives.

"language": "iq" for Iraqi Arabic (in Latin letters too), "msa" for any other Arabic, "en" for English.
"language_sure": true only when there is no doubt."""

# One fixed sentence per open question. Nothing from the conversation itself is put in the prompt.
ASKING_NOTES = {
    "payee": "The assistant has just asked who the money is for. A name alone is the payee_mention.",
    "amount": "The assistant has just asked how much. A number alone is the amount_text.",
    "biller": "The assistant has just asked which bill. A bill name alone is the biller_mention.",
    "period": "The assistant has just asked which month the bill is for.",
    "operator": "The assistant has just asked which mobile network.",
    "full_amount": "The assistant has just asked whether to pay the bill in full.",
    "change": "A payment waits for the user's confirmation. The user may change one part of it.",
}
PENDING_NOTE = "A {action} request is open. A message that changes one part of it is action chat with that field."
VOICE_NOTE = "The message is the transcript of a voice note, so a word may be misspelled."

_LETTER = re.compile(r"[^\W\d_]")
_WORD = re.compile(r"[^\W_]+")
# Particles Arabic attaches to a word: «أحمد» is written in «لأحمد».
_PARTICLES = ("ل", "ب", "و", "لل", "ال", "بال", "وال", "ول", "وب")
_OPERATOR_WORDS = {
    **{names.normalize(word): operator for word, operator in (
        ("زين", "zain"), ("آسيا", "asiacell"), ("آسياسيل", "asiacell"), ("اسيسيل", "asiacell"),
        ("كورك", "korek"))},
    "zain": "zain", "asia": "asiacell", "asiacell": "asiacell", "korek": "korek",
}
_CURRENT_WORDS = frozenset(fold(word) for word in ("هالشهر", "الحالي"))
_PREVIOUS_WORDS = frozenset(fold(word) for word in ("فات", "الفات", "الماضي", "السابق", "القبل"))
_MONTH_WORDS = frozenset(fold(word) for word in ("الشهر", "شهر"))
_ENGLISH_PREVIOUS = frozenset(("last", "previous", "past"))
_ENGLISH_CURRENT = frozenset(("this", "current"))


# ------------------------------------------------------------------ what the user wrote (pure functions)


def _tokens(text: str) -> list[str]:
    return names.normalize(text or "").split()


def reads_locally(text: str | None) -> bool:
    """True for a message no model server may see or needs to see: empty, no letters at all (digits, spaces
    and signs only), or carrying a PIN («الرمز 5678»)."""
    text = text or ""
    if not text.strip() or not _LETTER.search(fold(text)):
        return True
    return pins.pin_in(text) is not None


def _holds(words: list[str], fragment: list[str]) -> bool:
    """`fragment` stands in `words` as a run; its first word may carry an attached particle there."""
    if not fragment:
        return False
    for i in range(len(words) - len(fragment) + 1):
        head = words[i]
        if (head == fragment[0] or any(head == particle + fragment[0] for particle in _PARTICLES)) \
                and words[i + 1:i + len(fragment)] == fragment[1:]:
            return True
    return False


def written(fragment: str | None, text: str) -> bool:
    """True when `fragment` stands in the user's own words (normalised; Arabizi also read as Arabic).

    The same test the orchestrator runs again (agent.session.said): «أحمد» is written in «دز لأحمد خمسين»,
    «خمسة وسبعين» is not."""
    wanted = _tokens(fragment or "")
    return bool(wanted) and any(_holds(_tokens(source), wanted) for source in {text, arabizi.to_arabic(text)})


def whole_amount(fragment: str, text: str) -> str:
    """The amount words to pass on for a pointer the user wrote.

    When the pointer is only part of one number in the message («50» of «50 الف»), the whole number as
    payments.amounts reads it; otherwise the pointer itself. Never words the user did not write."""
    wanted = _tokens(arabizi.to_arabic(fragment))
    phrases = [phrase for _, phrase in amounts.amount_phrases(arabizi.to_arabic(text))]
    if not wanted or any(_tokens(phrase) == wanted for phrase in phrases):
        return fragment
    around = [phrase for phrase in phrases if _holds(_tokens(phrase), wanted)]
    return around[0] if len(around) == 1 else fragment


def same_amount(value: str, text: str) -> str | None:
    """The user's own amount words for a model's rewriting of them («50000» for «خمسين الف»), or None.

    Only when the message holds exactly one amount and payments.amounts reads both as the same number of
    dinars. What is passed on is the user's phrase, never the model's."""
    if len(value) > MAX_MENTION_CHARS:
        return None
    said = amounts.parse(arabizi.to_arabic(value))
    found = amounts.amount_phrases(arabizi.to_arabic(text))
    if said.needs_question or said.value_iqd is None or len(found) != 1:
        return None
    amount, phrase = found[0]
    if amount.needs_question or amount.value_iqd != said.value_iqd or amount.currency != said.currency:
        return None
    return phrase


def dollars_written(text: str) -> bool:
    """The user wrote a dollar amount or a dollar word somewhere in the message."""
    return any(amount.currency == "USD" for amount, _ in amounts.amount_phrases(arabizi.to_arabic(text)))


def operators_written(text: str) -> list[str]:
    """The mobile networks the message names, in order, each once."""
    found: list[str] = []
    for word in _tokens(arabizi.to_arabic(text)) + _tokens(text):
        forms = [word] + [word[len(p):] for p in ("وال", "بال", "لل", "ال", "و", "ب", "ل")
                          if word.startswith(p) and len(word) - len(p) >= 2]
        operator = next((_OPERATOR_WORDS[form] for form in forms if form in _OPERATOR_WORDS), None)
        if operator and operator not in found:
            found.append(operator)
    return found


def period_written(text: str) -> str | None:
    """The month the user's words name: 'MM', 'current', 'previous', or None (none, or more than one)."""
    arabic = arabizi.to_arabic(text)
    named = periods.months_named(arabic) | periods.months_named(text)
    if len(named) == 1:
        return f"{next(iter(named)):02d}"
    if named:
        return None
    english = set(re.sub(r"[^a-z\s]", " ", text.lower()).split())
    if "month" in english:
        if english & _ENGLISH_PREVIOUS:
            return "previous"
        if english & _ENGLISH_CURRENT:
            return "current"
    words = set(_WORD.findall(fold(arabic)))
    if words & _CURRENT_WORDS:
        return "current"
    if words & _MONTH_WORDS and words & _PREVIOUS_WORDS:
        return "previous"
    return None


def _year_written(year: str, text: str) -> bool:
    return year in re.findall(r"\d+", fold(text))


def build_messages(text: str, context: Mapping[str, Any] | None = None) -> list[dict[str, str]]:
    """What the generative model is sent: the fixed instructions, at most three fixed sentences about the
    situation, and the user's message. Nothing else of the conversation."""
    context = context or {}
    notes = []
    asking, pending = context.get("asking"), context.get("pending_action")
    if asking in ASKING_NOTES:
        notes.append(ASKING_NOTES[asking])
    if pending in PAYMENT_ACTIONS:
        notes.append(PENDING_NOTE.format(action=pending))
    if context.get("source") == "voice":
        notes.append(VOICE_NOTE)
    system = SYSTEM_PROMPT + ("\n\nSituation: " + " ".join(notes) if notes else "")
    return [{"role": "system", "content": system}, {"role": "user", "content": text}]


def intent_from(answer: Any, text: str, context: Mapping[str, Any] | None = None) -> tuple[Intent | None, int]:
    """(the Intent a model answer stands for, how many of its pointers were dropped), after the code checks.

    (None, 0) when the answer is unusable. Every payee, bill and amount in the Intent is written in `text`;
    the currency, the network and the month are there only when the user's words say them."""
    context = context or {}
    requests = answer.get("requests") if isinstance(answer, dict) else None
    if not isinstance(requests, list) or not requests:
        return None, 0
    requests = requests[:MAX_REQUESTS]
    if any(not isinstance(raw, dict) or raw.get("action") not in ACTIONS for raw in requests):
        return None, 0
    dropped = 0

    def copied(value: Any) -> str | None:
        nonlocal dropped
        if not isinstance(value, str) or not value.strip():
            return None
        value = value.strip()
        if len(value) > MAX_MENTION_CHARS or not written(value, text):
            dropped += 1
            return None
        return value

    def amount_words(value: Any) -> str | None:
        nonlocal dropped
        if not isinstance(value, str) or not value.strip():
            return None
        value = value.strip()
        if len(value) <= MAX_MENTION_CHARS and written(value, text):
            return whole_amount(value, text)
        rewritten = same_amount(value, text)
        if rewritten is None:
            dropped += 1
        return rewritten

    named_operators = operators_written(text)
    month = period_written(text)
    usd = dollars_written(text)
    bills = sum(raw["action"] == "pay_bill" for raw in requests)
    intents = []
    for raw in requests:
        action = raw["action"]
        intent = Intent(action)
        if action in ("transfer", "topup", "chat", "add_contact"):
            intent.payee_mention = copied(raw.get("payee_mention"))
        if action in ("pay_bill", "chat"):
            intent.biller_mention = copied(raw.get("biller_mention"))
        if action in (*PAYMENT_ACTIONS, "chat"):
            amount_text = amount_words(raw.get("amount_text"))
            if amount_text:
                reading = amounts.parse(arabizi.to_arabic(amount_text))
                if reading.currency == "USD" or (raw.get("currency") == "USD" and usd):
                    intent.currency = "USD"
                elif raw.get("currency") == "IQD":
                    intent.currency = "IQD"
            elif raw.get("currency") == "USD" and usd:
                intent.currency = "USD"  # «دولارات» with no number: the orchestrator asks for dinars
            intent.amount_text = amount_text
        if action == "topup" or (action == "chat" and context.get("asking") == "operator"):
            wanted = raw.get("operator")
            if wanted in named_operators:
                intent.operator = wanted
            elif len(named_operators) == 1:
                intent.operator = named_operators[0]
        if action in ("pay_bill", "chat"):
            intent.period = _period(raw.get("period"), month, text, alone=bills <= 1)
        intents.append(intent)
    first = intents[0]
    first.extra = [intent for intent in intents[1:] if intent.action != "chat"]
    if answer.get("language_sure") is True and answer.get("language") in LANGUAGES:
        first.language = answer["language"]
    return first, dropped


def _period(wanted: Any, month: str | None, text: str, *, alone: bool) -> str | None:
    """The model's month when the user's words say the same; else the month code reads, for a lone bill."""
    if isinstance(wanted, str):
        wanted = wanted.strip()
        if month is not None and wanted == month:
            return wanted
        if re.fullmatch(r"\d{4}-\d{2}", wanted) and wanted[5:] == month and _year_written(wanted[:4], text):
            return wanted
    return month if alone else None


# ------------------------------------------------------------------ the core


class OpenModelCore:
    """A Core (core/interface.py) that reads with the decision model and, when one is configured, a generative
    model through the model layer. Safe to share between threads.

    OpenModelCore()                      the model layer from the environment (llm.Registry.from_env)
    OpenModelCore(llm, gate)             a given model layer and gate (tests, evaluation)
    """

    def __init__(self, llm: LLM | None = None, gate: Gate | None = None, *, timeout_s: float | None = None,
                 env: Mapping[str, str] | None = None) -> None:
        env = os.environ if env is None else env
        self.llm = llm if llm is not None else LLM(Registry.from_env(env), env=env)
        self.gate = gate if gate is not None else Gate()
        self.gate.model  # read the decision model's file now: a missing file stops the start, not a message
        self.timeout_s = float(timeout_s) if timeout_s is not None else _seconds(env.get(ENV_TIMEOUT))
        self.stt_language = (env.get(ENV_STT_LANGUAGE) or "").strip() or None
        self._stub = StubCore()
        self._lock = threading.Lock()
        self._evidence: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._counts = {name: 0 for name in ("messages", "local", "direct", "calls", "answered", "fallbacks",
                                             "decision_only", "vetoes", "dropped", "backups", "voice_notes")}
        if self.llm.has(STT_ROLE):
            # Only a core that can hear has transcribe(): isinstance(core, VoiceCore) stays honest.
            self.transcribe = self._transcribe

    # ---- the interface ------------------------------------------------------------------------------

    @property
    def mode(self) -> str:
        """"generative" when an understand route is usable, else "decision-only"."""
        return "generative" if self.llm.has(ROLE) else "decision-only"

    def understand(self, text: str, context: dict[str, Any] | None = None) -> Intent:
        context = context or {}
        text = text or ""
        self._count("messages")
        if reads_locally(text):
            self._count("local")
            self._remember(context, {"route": ROUTE_LOCAL, "fallback": False})
            carried = pins.pin_in(text)
            if carried is not None and carried[0] != "bare":
                return Intent("chat")  # «الرمز 5678»: its digits are nobody's amount
            return self._stub.understand(text, context)

        direct, facts = self.gate.before_evidence(text, context)
        if direct is not None:
            self._count("direct")
            self._remember(context, {**facts, "route": ROUTE_DECISION, "fallback": False})
            return direct

        generative = self.llm.has(ROLE)
        if generative and len(text) <= MAX_CHARS:
            intent, route, backup = self._read(text, context)
            if intent is not None:
                checked, facts = self.gate.after(text, intent, context)
                if facts.get("verdict") == "ask":
                    self._count("vetoes")
                self._remember(context, {**facts, "route": route, "fallback": backup})
                return checked

        intent, facts = self.gate.fallback_evidence(text, context)
        self._count("fallbacks" if generative else "decision_only")
        self._remember(context, {**facts, "route": ROUTE_DECISION if generative else ROUTE_DECISION_ONLY,
                                 "fallback": generative})
        return intent

    def phrase(self, question_key: str, slots: dict[str, Any]) -> str:
        """The fixed templates of payments.messages, in the language slots['lang']. No model call."""
        return self._stub.phrase(question_key, slots)

    def evidence(self, user_id: str) -> dict[str, Any] | None:
        """What was checked on this user's last message (core.interface.ExplainingCore): the decision model's
        label and confidence, the generative model's action, the verdict, which route read it, and whether a
        backup did. Labels and numbers only, never the user's words."""
        with self._lock:
            facts = self._evidence.get(user_id)
            return dict(facts) if facts else None

    def stats(self) -> dict[str, Any]:
        """Counters since the start: messages, local (read by rules), direct (answered by the decision model),
        calls (generative readings asked for), answered (usable ones), fallbacks (the decision model stood in
        after a failed generative reading), decision_only (readings with no generative route), vetoes (readings
        the cross-check turned into a question), dropped (pointers at words the user did not write), backups
        (a backup route answered), voice_notes, and spent_usd from the model layer."""
        with self._lock:
            counts = dict(self._counts)
        return {**counts, "mode": self.mode, "spent_usd": round(self.llm.spent_usd, 6)}

    @property
    def spent_usd(self) -> float:
        """What the model layer has spent so far (0.0 with local models). The channels read this for the
        daily budget."""
        return self.llm.spent_usd

    # ---- inside -------------------------------------------------------------------------------------

    def _read(self, text: str, context: Mapping[str, Any]) -> tuple[Intent | None, str, bool]:
        """(the checked generative reading or None, the route that answered, True when it was a backup)."""
        self._count("calls")
        try:
            answer = self.llm.json(ROLE, build_messages(text, context), SCHEMA, timeout_s=self.timeout_s,
                                   max_tokens=MAX_TOKENS, temperature=0.0)
        except LLMError:
            answer = None
        if answer is None:
            return None, "", False
        last = self.llm.last
        intent, dropped = intent_from(answer, text, context)
        if intent is None:
            return None, "", False
        self._count("answered")
        self._count("dropped", dropped)
        backup = int(last.get("route") or 1) > 1
        if backup:
            self._count("backups")
        return intent, f"{ROLE}: {last.get('provider', '?')}:{last.get('model', '?')}", backup

    def _transcribe(self, audio: bytes, mime: str, context: dict[str, Any] | None = None) -> str:
        """What the voice note says ('' when it could not be heard). Bound as `transcribe` only when an "stt"
        route is usable. The transcript then goes through understand() and the same checks as typed text."""
        self._count("voice_notes")
        try:
            return self.llm.transcribe(STT_ROLE, audio, mime or "audio/ogg", language=self.stt_language).strip()
        except LLMError:
            return ""

    def _count(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counts[name] += amount

    def _remember(self, context: Mapping[str, Any], facts: dict[str, Any]) -> None:
        user_id = context.get("user_id")
        if not isinstance(user_id, str) or not user_id:
            return
        with self._lock:
            self._evidence[user_id] = facts
            self._evidence.move_to_end(user_id)
            while len(self._evidence) > MAX_USERS:
                self._evidence.popitem(last=False)


def _seconds(value: Any) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_S
    return seconds if 0 < seconds < 600 else DEFAULT_TIMEOUT_S
