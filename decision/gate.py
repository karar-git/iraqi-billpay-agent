"""The gate: where the decision model sits around the generative model.

    before   a message that is, with high confidence, a request with nothing to fill in (balance, history,
             cancel) is answered at once: no generative call, no cost, no data leaves the machine.
    after    a cross-check of the generative reading. When one model says "a payment" and the decision model is
             confident of the opposite, the reading becomes a plain question (Intent("chat")). The reverse too:
             a reading that is not a payment, on a message the decision model is confident is one.
    fallback when no generative model answered at all: the decision model's action with no slots, so the
             orchestrator asks who / how much with its own fixed questions.

Money safety, by construction and tested (tests/test_decision_gate.py):
- `before` never returns a payment action;
- `after` returns either the very Intent it was given, untouched, or a bare Intent("chat"). It cannot create a
  payment request, and it cannot set or change an amount, a payee, a biller or a currency;
- `fallback` never fills a slot. A payment still needs the orchestrator's questions, its checks, the card and
  the tap with the PIN.

Two requests in one message («جدد النت وراويني آخر العمليات»): the model gives one label, so the gate reads
the message a second time in parts (every leading and every trailing run of words). `before` answers only when
no part reads as a different request, and `after` does not overrule a payment reading when a part reads as a
payment.

The decision model reads one standalone message. Inside an open question or with an open card (the context
says so) the gate steps aside: `before` and `after` abstain, and `fallback` only recognises a cancel.

Standard library only.
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

from core.interface import ACTIONS, PAYMENT_ACTIONS, Intent
from decision.intent import Decision, IntentModel

# Actions with nothing to fill in. `before` may answer these directly, and only these.
SLOT_FREE_ACTIONS = ("balance", "history", "cancel")
# Generative readings `after` leaves alone when the decision model says "this is a payment": a stop, a refusal
# and a question move nothing and start nothing, so turning them into a question would make nothing safer.
NEVER_OVERRULED = ("cancel", "unsupported", "chat")
DEFAULT_DIRECT = 0.90
DEFAULT_VETO = 0.80
DEFAULT_FLOOR = 0.50
DEFAULT_PART = 0.90
VERDICTS = ("agree", "ask", "direct", "abstain")
MAX_DIRECT_WORDS = 12  # a longer message is never answered directly (and the second reading stays cheap)
MAX_VETO_WORDS = 40  # a longer payment reading is never overruled

ReadMany = Callable[[Sequence[str]], Sequence[Decision]]


def payment_probability(decision: Decision) -> float:
    """How likely the message is a payment request of any kind (bill, transfer or top-up)."""
    return sum(decision.scores.get(action, 0.0) for action in PAYMENT_ACTIONS)


def is_open(context: dict[str, Any] | None) -> bool:
    """True inside an open question or with an open card: the message may be an answer, not a new request."""
    return bool(context) and bool(context.get("asking") or context.get("pending_action"))


def is_blank(text: str | None) -> bool:
    """True for a message with nothing to read (empty, or white space only)."""
    return not (text or "").strip()


def parts(text: str) -> list[str]:
    """Every leading and every trailing run of words of a message (the whole message is not a part)."""
    words = text.split()
    return [" ".join(words[:cut]) for cut in range(1, len(words))] \
        + [" ".join(words[cut:]) for cut in range(1, len(words))]


def other_request(read_many: ReadMany, text: str, decision: Decision) -> float:
    """How strongly some part of the message reads as a DIFFERENT request than `decision` (chat aside): the
    highest such confidence, 0.0 when no part does, 1.0 for a message over MAX_DIRECT_WORDS words."""
    if len(text.split()) > MAX_DIRECT_WORDS:
        return 1.0
    return max((d.confidence for d in read_many(parts(text)) if d.label not in (decision.label, "chat")),
               default=0.0)


def payment_part(read_many: ReadMany, text: str) -> float:
    """How strongly some part of the message reads as a payment request: the highest payment probability of a
    part, 0.0 for a one-word message, 1.0 for a message over MAX_VETO_WORDS words."""
    if len(text.split()) > MAX_VETO_WORDS:
        return 1.0
    return max((payment_probability(d) for d in read_many(parts(text))), default=0.0)


class Gate:
    """Thresholds: `direct` for answering without the generative model, `veto` for overruling it into a question,
    `floor` for trusting the fallback, `part` for the second reading in parts. Left as None they come from the
    model file, where decision/train.py wrote the values chosen on the tune set; a model file without them gives
    0.90 / 0.80 / 0.50 / 0.90."""

    def __init__(self, model: IntentModel | None = None, *, direct: float | None = None, veto: float | None = None,
                 floor: float | None = None, part: float | None = None,
                 direct_actions: tuple[str, ...] | None = None) -> None:
        self._model = model
        self._direct, self._veto, self._floor, self._part = direct, veto, floor, part
        self._direct_actions = direct_actions

    # ------------------------------------------------------------------ settings (resolved lazily)

    @property
    def model(self) -> IntentModel:
        if self._model is None:
            self._model = IntentModel.load()
        return self._model

    def _setting(self, given: float | None, key: str, default: float) -> float:
        if given is not None:
            return float(given)
        meta = getattr(self.model, "meta", None) or {}
        value = (meta.get("gate") or {}).get(key)
        return float(default if value is None else value)

    @property
    def direct(self) -> float:
        return self._setting(self._direct, "direct", DEFAULT_DIRECT)

    @property
    def veto(self) -> float:
        return self._setting(self._veto, "veto", DEFAULT_VETO)

    @property
    def floor(self) -> float:
        return self._setting(self._floor, "floor", DEFAULT_FLOOR)

    @property
    def part(self) -> float:
        return self._setting(self._part, "part", DEFAULT_PART)

    @property
    def direct_actions(self) -> tuple[str, ...]:
        """The actions `before` may answer: never a payment action, whatever the caller or the model file says."""
        wanted = self._direct_actions
        if wanted is None:
            meta = getattr(self.model, "meta", None) or {}
            wanted = (meta.get("gate") or {}).get("direct_actions", SLOT_FREE_ACTIONS)
        return tuple(action for action in wanted if action in SLOT_FREE_ACTIONS)

    def read(self, text: str) -> Decision:
        """The decision model's reading of one message."""
        return self.model.predict(text)

    def read_many(self, texts: Sequence[str]) -> list[Decision]:
        return [self.model.predict(text) for text in texts]

    @staticmethod
    def _evidence(decision: Decision, generative: str | None, verdict: str, **more: float) -> dict[str, Any]:
        return {"decision": decision.label, "confidence": round(decision.confidence, 4), "generative": generative,
                "verdict": verdict, "payment_probability": round(payment_probability(decision), 4),
                **{key: round(value, 4) for key, value in more.items()}}

    # ------------------------------------------------------------------ before the generative model

    def before_evidence(self, text: str, context: dict[str, Any] | None = None) -> tuple[Intent | None, dict[str, Any]]:
        """(`before`'s answer, the evidence). Verdict "direct" when it answers, else "abstain"."""
        decision = self.read(text)
        if is_open(context) or is_blank(text) or decision.label not in self.direct_actions \
                or decision.label not in ACTIONS or decision.confidence < self.direct \
                or len(text.split()) > MAX_DIRECT_WORDS:
            return None, self._evidence(decision, None, "abstain")
        other = other_request(self.read_many, text, decision)
        if other >= self.part:  # a second request in the same message: the generative model reads it
            return None, self._evidence(decision, None, "abstain", other_request=other)
        return Intent(decision.label), self._evidence(decision, None, "direct", other_request=other)

    def before(self, text: str, context: dict[str, Any] | None = None) -> Intent | None:
        """A confident slot-free action (balance, history, cancel), or None: let the generative model read it."""
        return self.before_evidence(text, context)[0]

    # ------------------------------------------------------------------ after the generative model

    def after(self, text: str, intent: Intent, context: dict[str, Any] | None = None) -> tuple[Intent, dict[str, Any]]:
        """Cross-check a generative reading. Returns (`intent` itself or a bare Intent("chat"), the evidence).

        "ask": the two models are on opposite sides of "is this a payment?" and the decision model is at least
        `veto` sure of its side. The reading is dropped and the orchestrator asks. "agree": the same side, and
        the decision model is sure of it (the kind of payment may still differ; the evidence shows both).
        "abstain": the decision model is not sure, or it steps aside.

        A reading "pays" when its action, or the action of one of its extra requests, is a payment action.
        Never overruled: a generative "cancel", "unsupported" or "chat" that does not pay (stopping and
        refusing are always safe, and a chat is already a question; its words are left as they are), and a
        payment reading when the message is very long or a part of it reads as a payment. The Intent that comes
        back is the one given, the same object and unchanged, or a new Intent("chat") with no slot filled (only
        the language hint is kept)."""
        decision = self.read(text)
        generative = intent.action
        if is_open(context) or is_blank(text):
            return intent, self._evidence(decision, generative, "abstain")
        p_payment = payment_probability(decision)
        generative_pays = any(i.action in PAYMENT_ACTIONS for i in (intent, *intent.extra))
        if generative_pays and 1.0 - p_payment >= self.veto:
            in_part = payment_part(self.read_many, text)
            if in_part >= self.part or len(text.split()) > MAX_VETO_WORDS:
                return intent, self._evidence(decision, generative, "abstain", payment_part=in_part)
            return (Intent("chat", language=intent.language),
                    self._evidence(decision, generative, "ask", payment_part=in_part))
        if not generative_pays and p_payment >= self.veto:
            if generative in NEVER_OVERRULED:
                return intent, self._evidence(decision, generative, "abstain")
            return Intent("chat", language=intent.language), self._evidence(decision, generative, "ask")
        sure_of_side = max(p_payment, 1.0 - p_payment) >= self.veto
        return intent, self._evidence(decision, generative, "agree" if sure_of_side else "abstain")

    # ------------------------------------------------------------------ instead of the generative model

    def fallback_evidence(self, text: str, context: dict[str, Any] | None = None) -> tuple[Intent, dict[str, Any]]:
        """(`fallback`'s answer, the evidence). Verdict "direct" when the model's action is used, else "abstain"."""
        decision = self.read(text)
        if is_blank(text):
            return Intent("chat"), self._evidence(decision, None, "abstain")
        if is_open(context):  # the message answers an open question: only a clear cancel is read here
            if decision.label == "cancel" and decision.confidence >= self.direct:
                return Intent("cancel"), self._evidence(decision, None, "direct")
            return Intent("chat"), self._evidence(decision, None, "abstain")
        if decision.label in ACTIONS and decision.confidence >= self.floor:
            return Intent(decision.label), self._evidence(decision, None, "direct")
        return Intent("chat"), self._evidence(decision, None, "abstain")

    def fallback(self, text: str, context: dict[str, Any] | None = None) -> Intent:
        """No generative model answered: the decision model's action with NO slots (the orchestrator then asks who
        and how much), or a plain Intent("chat") when the model is not sure enough."""
        return self.fallback_evidence(text, context)[0]
