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

ACTIONS = ("pay_bill", "transfer", "topup", "balance", "cancel", "unsupported", "chat")
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
    period: str | None = None  # bills only: 'current' | 'previous' | 'YYYY-MM' | None

    def __post_init__(self) -> None:
        if self.action not in ACTIONS:
            raise ValueError(f"unknown action: {self.action}")


@runtime_checkable
class Core(Protocol):
    """Anything that understands a message and phrases a question.

    `context` carries what the orchestrator knows about the conversation:
    {"user_id": str, "gender": 'f'|'m', "asking": the field of the open question or None,
     "pending_action": the action being filled in or None}.
    """

    def understand(self, text: str, context: dict[str, Any]) -> Intent:
        """Read one user message and return the request(s) in it."""
        ...

    def phrase(self, question_key: str, slots: dict[str, Any]) -> str:
        """Text of a question (keys from payments.messages, e.g. 'ask_amount'). slots has 'gender'."""
        ...
