"""The contract between the conversational core and the open payment layer.

The core reads what the user wrote and says what they want (an Intent). It never touches money,
never computes amounts and never picks a contact: it only points at the user's own words.
The orchestrator (agent/session.py) checks that `amount_text` and `payee_mention` really appear in
the user's message, parses and resolves them with deterministic code, and asks when unsure.

The production core is closed source (see core/README.md). core/stub.py is an open, rule-based
stand-in that implements the same interface for tests and the offline demo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

ACTIONS = ("pay_bill", "transfer", "topup", "balance", "cancel", "unsupported", "chat", "history", "add_contact")
PAYMENT_ACTIONS = ("pay_bill", "transfer", "topup")
OPERATORS = ("zain", "asiacell", "korek")


@dataclass
class Intent:
    """What the user asked for, pointing at their own words. Missing parts stay None."""

    action: str = "chat"  # one of ACTIONS
    payee_mention: str | None = None  # the payee as the user wrote it: "لأحمد", "امي", "l ahmad"
    biller_mention: str | None = None  # "المولدة", "النت", "سما نت"
    amount_text: str | None = None  # the amount words as written: "خمسين", "٥٠٬٠٠٠", "ورقة"
    currency: str | None = None  # 'IQD' | 'USD' | None (dollar words -> 'USD')
    operator: str | None = None  # top-up network: 'zain' | 'asiacell' | 'korek'
    extra: list[Intent] = field(default_factory=list)  # further requests in the same message
    period: str | None = None  # bills only: 'current' | 'previous' | 'YYYY-MM' | 'MM' (that month) | None
    # The language of the message, only when the core is sure: 'iq' (Iraqi, Arabizi too) | 'msa' (Modern
    # Standard Arabic or any other Arabic dialect) | 'en'. None leaves it to payments.language.
    language: str | None = None

    def __post_init__(self) -> None:
        if self.action not in ACTIONS:
            raise ValueError(f"unknown action: {self.action}")


@runtime_checkable
class Core(Protocol):
    """Anything that understands a message and phrases a question.

    `context` carries what the orchestrator knows about the conversation:
    {"user_id": str, "gender": 'f'|'m', "asking": the field of the open question or None,
     "pending_action": the action being filled in or None,
     "language": the reply language so far, 'iq' | 'msa' | 'en',
     "phone_tokens": placeholder words that stand for phone numbers in the text (e.g. «هاتف_ا»). The core
         never sees a phone number: a placeholder is the payee mention, copied as it is,
     "vocabulary": {"contacts": the user's saved names and aliases, "bill_labels": their saved bill labels,
         "billers": biller names} (no phone numbers), so a misheard name can be written the saved way,
     "history": the last few turns [{"role": "user"|"assistant", "text": ...}] (never a PIN),
     "source": "voice" for a voice-note transcript, else "text"}.

    Actions beyond payments: "history" (the user asks for their recent transactions) and "add_contact"
    (save a person: payee_mention is the name or the phone placeholder).
    """

    def understand(self, text: str, context: dict[str, Any]) -> Intent:
        """Read one user message and return the request(s) in it."""
        ...

    def phrase(self, question_key: str, slots: dict[str, Any]) -> str:
        """Text of a question (keys from payments.messages, e.g. 'ask_amount') in the language slots['lang']
        ('iq' when absent). slots has 'gender' too."""
        ...


@runtime_checkable
class VoiceCore(Core, Protocol):
    """A core that can also hear voice notes. Optional: Session.handle_voice uses it when present.

    The transcript then goes through understand() like typed text, and through the same checks.
    """

    def transcribe(self, audio: bytes, mime: str, context: dict[str, Any]) -> str:
        """What the voice note says, as text ('' when it could not be heard)."""
        ...


@runtime_checkable
class HearingCore(VoiceCore, Protocol):
    """A core that hears and understands a voice note in one step. Optional: preferred when present.

    Returns (transcript, Intent): the Intent points at words of that transcript only (the orchestrator
    re-checks them against it), with the same `context` as understand() plus the user's vocabulary."""

    def hear(self, audio: bytes, mime: str, context: dict[str, Any]) -> tuple[str, Intent | None]:
        ...


@runtime_checkable
class ExplainingCore(Protocol):
    """A core that can say what it checked. Optional: the orchestrator adds it to the turn's evidence.

    evidence(user_id) returns what the core checked on this user's last understand() / hear() call, facts only
    and no user text beyond short labels, or None when it does not know. Keys the orchestrator reads:
    {"decision": the action a decision model chose, "confidence": 0..1, "generative": the action a generative
     model read, "verdict": "agree" | "ask" | "direct" | "abstain", "route": which route answered (a short
     label), "fallback": True when a backup answered}. Every key is optional. A core keeps this per user id
    (a small bounded dict, safe to call from several threads).
    """

    def evidence(self, user_id: str) -> dict[str, Any] | None:
        ...


@runtime_checkable
class ConversingCore(Protocol):
    """A core that can write a reply in its own words. Optional: the orchestrator uses it when present
    (Session.talk), and sends its fixed template whenever it is absent or gives nothing usable.

    The orchestrator still decides everything that happens: the action, the question, the options, the card,
    every amount, payee and outcome. `situation` names what the reply is for: a question key of
    payments.messages ("ask_amount_for", "offer_next", ...) or one of "hello", "thanks", "unsupported_media",
    "small_talk", "intro_followup", "chat", "done_warm", "refusal_warm".

    `facts` maps each fact the reply may use to its kind ("money" | "text" | "digits"): never its value. The
    reply writes a fact only as its placeholder, "{balance}", "{amount}", "{name}", "{last4}", "{reference}"...,
    and the orchestrator fills it in with its own value. A reply with any other placeholder, a digit outside a
    placeholder, or a required placeholder missing is not used.

    `context`: {"user_id", "language": 'iq' | 'msa' | 'en', "gender": 'f' | 'm', "gender_known": bool,
     "text": the user's message (phone numbers as placeholders, digits masked as «#», never a PIN; "" when the
     orchestrator speaks first), "reference": the template the reply replaces, with placeholders for its facts,
     "required": the placeholders the reply must hold, "question": True when the reply must ask the template's
     question, "history": the last turns [{"role", "text"}] (masked the same way, the orchestrator's own actions
     as short notes such as «[بطاقة تحويل لـ أحمد]»), "first_turn": no reply was given yet in this conversation,
     "time_of_day": "morning" | "evening" in Baghdad, "voice": True when the reply will also be spoken,
     "vocabulary": the user's saved names (no numbers), "demo": True for a demo account (pretend money)}.

    Returns the reply text with its placeholders, or None (the template goes out). The orchestrator checks the
    reply again on its own: outside "done_*" it never says money moved, and no reply promises money back.
    """

    def talk(self, situation: str, facts: dict[str, Any], context: dict[str, Any]) -> str | None:
        ...


@runtime_checkable
class SpeechCore(Protocol):
    """A core that can also speak: a reply as a voice note. Optional: the channels use it when present.

    `text` is what to say, already in `language` ('iq' | 'msa' | 'en'); it never holds a PIN. Returns
    (audio bytes, mime type), e.g. an ogg/opus voice note, or None when it could not speak (the text
    reply still goes out). `context` has "user_id" and "gender".
    """

    def speak(self, text: str, language: str, context: dict[str, Any]) -> tuple[bytes, str] | None:
        ...
