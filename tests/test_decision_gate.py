"""The gate around the generative model: what it can never do (money safety), when it steps aside, and that the
thresholds in the model file keep their promise on the tune set.

Property tests use a seeded random generator and a fake model, so they cover readings the real model never gives.
"""

import copy
import random

import pytest

from core.interface import ACTIONS, PAYMENT_ACTIONS, Intent
from decision import Decision, Gate, IntentModel, metrics
from decision.data.build import load_part
from decision.gate import (DEFAULT_DIRECT, DEFAULT_FLOOR, DEFAULT_PART, DEFAULT_VETO, MAX_DIRECT_WORDS,
                           MAX_VETO_WORDS, NEVER_OVERRULED, SLOT_FREE_ACTIONS, VERDICTS, is_open, parts,
                           payment_probability)

TUNE = load_part("tune")
TRAIN = load_part("train")
SLOTS = ("payee_mention", "biller_mention", "amount_text", "currency", "operator", "period")
OPEN_CONTEXTS = ({"asking": "amount", "pending_action": "transfer"}, {"asking": "payee"},
                 {"pending_action": "pay_bill"}, {"asking": None, "pending_action": "topup"})
CLOSED_CONTEXTS = (None, {}, {"asking": None, "pending_action": None}, {"language": "iq", "history": []})


def decide(label: str, confidence: float) -> Decision:
    """A reading with `confidence` on `label` and the rest spread evenly over the other actions."""
    rest = (1.0 - confidence) / (len(ACTIONS) - 1)
    return Decision(label, confidence, {action: confidence if action == label else rest for action in ACTIONS})


def side(p_payment: float) -> Decision:
    """A reading that puts `p_payment` on transfer and the rest on chat."""
    label = "transfer" if p_payment >= 0.5 else "chat"
    scores = {action: 0.0 for action in ACTIONS}
    scores.update(transfer=p_payment, chat=1.0 - p_payment)
    return Decision(label, scores[label], scores)


class FakeModel:
    """Says what the test tells it to: per text, else one default reading. No thresholds of its own."""

    def __init__(self, default: Decision, answers: dict[str, Decision] | None = None, meta: dict | None = None):
        self.default, self.answers, self.meta = default, answers or {}, meta or {}

    def predict(self, text: str) -> Decision:
        return self.answers.get(text, self.default)


def bare(intent: Intent) -> bool:
    return all(getattr(intent, slot) is None for slot in SLOTS) and intent.extra == []


def random_intent(rng: random.Random, depth: int = 0) -> Intent:
    intent = Intent(rng.choice(ACTIONS))
    for slot, values in (("payee_mention", ("لاحمد", "امي", "هاتف_ا")), ("biller_mention", ("المولدة", "النت")),
                         ("amount_text", ("خمسين", "٥٠٬٠٠٠", "ورقة")), ("currency", ("IQD", "USD")),
                         ("operator", ("zain", "korek")), ("period", ("current", "2026-10")),
                         ("language", ("iq", "msa", "en"))):
        if rng.random() < 0.5:
            setattr(intent, slot, rng.choice(values))
    if depth == 0 and rng.random() < 0.3:
        intent.extra = [random_intent(rng, 1) for _ in range(rng.randint(1, 2))]
    return intent


def random_gate(rng: random.Random, text: str) -> Gate:
    """A gate over a fake model with random readings of the message and of each of its parts, and any thresholds."""
    answers = {piece: decide(rng.choice(ACTIONS), rng.choice((0.3, 0.6, 0.85, 0.95, 1.0))) for piece in parts(text)}
    model = FakeModel(decide(rng.choice(ACTIONS), rng.choice((0.2, 0.5, 0.79, 0.81, 0.9, 0.99, 1.0))), answers)
    return Gate(model, direct=rng.choice((0.0, 0.5, 0.9)), veto=rng.choice((0.0, 0.5, 0.8, 0.95)),
                floor=rng.choice((0.0, 0.5, 0.9)), part=rng.choice((0.0, 0.7, 0.9, 1.01)),
                direct_actions=rng.choice((None, SLOT_FREE_ACTIONS, ACTIONS, PAYMENT_ACTIONS)))


TEXTS = ("دز لاحمد خمسين", "شكد رصيدي", "الغي", "ادفع المولدة ودز لهادي عشرين", "هلا", "x", "",
         "cancel that and show me my balance")


# ------------------------------------------------------------------ money safety: what the gate can never do


def test_before_never_returns_a_payment_action_whatever_the_model_or_the_settings_say():
    rng = random.Random(1)
    answered = 0
    for _ in range(3000):
        text = rng.choice(TEXTS)
        result = random_gate(rng, text).before(text, rng.choice(CLOSED_CONTEXTS))
        if result is not None:
            answered += 1
            assert result.action in SLOT_FREE_ACTIONS and result.action not in PAYMENT_ACTIONS
            assert bare(result) and result.language is None
    assert answered > 100  # the property was really exercised
    for action in PAYMENT_ACTIONS:  # a model that is certain of a payment, a gate told to answer everything
        gate = Gate(FakeModel(decide(action, 1.0)), direct=0.0, part=1.01, direct_actions=ACTIONS)
        assert gate.before("دز لاحمد خمسين", {}) is None
        assert sorted(gate.direct_actions) == sorted(SLOT_FREE_ACTIONS)


def test_before_with_the_real_model_never_starts_a_payment_on_any_train_or_tune_line():
    gate = Gate()
    for row in TRAIN + TUNE:
        result = gate.before(row["text"], {})
        assert result is None or (result.action in gate.direct_actions and bare(result))


def test_after_returns_the_reading_untouched_or_a_bare_question_and_never_creates_a_payment():
    rng = random.Random(2)
    kept = asked = 0
    for _ in range(4000):
        text = rng.choice(TEXTS)
        intent = random_intent(rng)
        before = copy.deepcopy(intent)
        context = rng.choice(CLOSED_CONTEXTS + OPEN_CONTEXTS)
        result, evidence = random_gate(rng, text).after(text, intent, context)
        assert intent == before, "the gate changed the reading it was given"
        assert evidence["verdict"] in VERDICTS and evidence["generative"] == before.action
        if result is intent:
            kept += 1
            assert evidence["verdict"] in ("agree", "abstain")
        else:
            asked += 1
            assert evidence["verdict"] == "ask"
            assert result.action == "chat" and bare(result)  # no amount, no payee, no biller, no currency
            assert result.language == before.language
            pays = any(i.action in PAYMENT_ACTIONS for i in (before, *before.extra))
            assert pays or before.action not in NEVER_OVERRULED
        if result.action in PAYMENT_ACTIONS:  # a payment reading only ever comes back as the one that went in
            assert result is intent and before.action in PAYMENT_ACTIONS
            assert (result.amount_text, result.payee_mention, result.biller_mention, result.currency) == \
                (before.amount_text, before.payee_mention, before.biller_mention, before.currency)
    assert kept > 500 and asked > 500


def test_fallback_never_fills_a_slot_and_only_names_an_action():
    rng = random.Random(3)
    for _ in range(2000):
        text = rng.choice(TEXTS)
        result = random_gate(rng, text).fallback(text, rng.choice(CLOSED_CONTEXTS + OPEN_CONTEXTS))
        assert result.action in ACTIONS and bare(result) and result.language is None
    gate = Gate()
    for row in TUNE:
        assert bare(gate.fallback(row["text"], {}))


def test_the_evidence_holds_facts_only_never_the_users_words():
    gate = Gate()
    text = "دز لاحمد خمسين الف"
    for evidence in (gate.before_evidence(text, {})[1], gate.after(text, Intent("transfer", amount_text="خمسين"), {})[1],
                     gate.fallback_evidence(text, {})[1]):
        assert {"decision", "confidence", "generative", "verdict"} <= set(evidence)
        assert evidence["decision"] in ACTIONS and evidence["verdict"] in VERDICTS
        assert all(value is None or isinstance(value, (int, float)) or value in ACTIONS + VERDICTS
                   for value in evidence.values())


# ------------------------------------------------------------------ inside an open question or card


@pytest.mark.parametrize("context", OPEN_CONTEXTS)
@pytest.mark.parametrize("text", ["الغي", "شكد رصيدي", "cancel", "لا", "what is my balance"])
def test_before_returns_none_while_a_question_or_a_card_is_open(context, text):
    gate = Gate()
    assert gate.before(text, context) is None
    assert gate.before_evidence(text, context)[1]["verdict"] == "abstain"
    certain = Gate(FakeModel(decide("balance", 1.0)), direct=0.0)
    assert certain.before(text, context) is None


@pytest.mark.parametrize("context", CLOSED_CONTEXTS)
def test_before_answers_a_plain_slot_free_request_when_nothing_is_open(context):
    gate = Gate()
    assert gate.before("شكد رصيدي", context) == Intent("balance")
    assert gate.before("الغي", context) == Intent("cancel")
    assert gate.before_evidence("شكد رصيدي", context)[1]["verdict"] == "direct"


def test_is_open_reads_the_two_context_keys():
    assert not any(is_open(context) for context in CLOSED_CONTEXTS)
    assert all(is_open(context) for context in OPEN_CONTEXTS)


@pytest.mark.parametrize("context", OPEN_CONTEXTS)
def test_after_steps_aside_while_a_question_or_a_card_is_open(context):
    gate = Gate(FakeModel(decide("chat", 1.0)), veto=0.5)
    intent = Intent("transfer", payee_mention="لاحمد", amount_text="خمسين")
    result, evidence = gate.after("خمسين", intent, context)
    assert result is intent and evidence["verdict"] == "abstain"


@pytest.mark.parametrize("context", OPEN_CONTEXTS)
def test_fallback_reads_only_a_clear_cancel_while_a_question_or_a_card_is_open(context):
    gate = Gate()
    assert gate.fallback("الغي", context) == Intent("cancel")
    assert gate.fallback("خمسين", context) == Intent("chat")  # an answer: the orchestrator reads it itself
    assert gate.fallback("دز لاحمد خمسين", context) == Intent("chat")
    assert gate.fallback("شكد رصيدي", context) == Intent("chat")


# ------------------------------------------------------------------ before: when it answers


def test_before_needs_the_confidence_and_a_slot_free_action():
    assert Gate(FakeModel(decide("balance", 0.89)), direct=0.9).before("x", {}) is None
    assert Gate(FakeModel(decide("balance", 0.9)), direct=0.9).before("x", {}) == Intent("balance")
    for action in set(ACTIONS) - set(SLOT_FREE_ACTIONS):
        assert Gate(FakeModel(decide(action, 1.0)), direct=0.5).before("x", {}) is None
    only_cancel = Gate(FakeModel(decide("balance", 1.0)), direct=0.5, direct_actions=("cancel",))
    assert only_cancel.before("x", {}) is None


def test_before_leaves_a_message_with_a_second_request_to_the_generative_model():
    whole, text = decide("balance", 0.97), "شكد رصيدي ودز لاحمد خمسين"
    model = FakeModel(decide("chat", 0.4), {text: whole, "ودز لاحمد خمسين": decide("transfer", 0.95)})
    answer, evidence = Gate(model, direct=0.9, part=0.9).before_evidence(text, {})
    assert answer is None and evidence["verdict"] == "abstain" and evidence["other_request"] == 0.95
    assert Gate(model, direct=0.9, part=0.96).before(text, {}) == Intent("balance")  # the part is not sure enough
    real = Gate()
    for message in ("cancel that and show me my balance", "شكد رصيدي ودز لاحمد خمسين الف",
                    "الغي العملية وادفع فاتورة المولدة"):
        assert real.before(message, {}) is None


def test_before_never_answers_a_long_or_an_empty_message():
    gate = Gate(FakeModel(decide("balance", 1.0)), direct=0.5, part=1.01)
    assert gate.before(" ".join(["رصيدي"] * MAX_DIRECT_WORDS), {}) == Intent("balance")
    assert gate.before(" ".join(["رصيدي"] * (MAX_DIRECT_WORDS + 1)), {}) is None
    for blank in ("", "   ", "\n\t"):
        assert gate.before(blank, {}) is None


# ------------------------------------------------------------------ after: only a confident disagreement


def test_after_flags_a_disagreement_only_when_the_decision_model_is_confident():
    payment = Intent("transfer", payee_mention="لاحمد", amount_text="خمسين")
    for p_payment, verdict in ((0.5, "abstain"), (0.21, "abstain"), (0.19, "ask"), (0.0, "ask")):
        gate = Gate(FakeModel(side(p_payment)), veto=0.8, part=1.01)
        result, evidence = gate.after("x y", payment, {})
        assert evidence["verdict"] == verdict
        assert (result is payment) == (verdict == "abstain")
        assert (result == Intent("chat")) == (verdict == "ask")
    reading = Intent("balance")
    for p_payment, verdict in ((0.5, "abstain"), (0.79, "abstain"), (0.81, "ask"), (1.0, "ask")):
        gate = Gate(FakeModel(side(p_payment)), veto=0.8)
        result, evidence = gate.after("x y", reading, {})
        assert evidence["verdict"] == verdict
        assert (result is reading) == (verdict == "abstain")
    for p_payment in (0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0):  # a gate that is never sure never overrules
        gate = Gate(FakeModel(side(p_payment)), veto=metrics.OFF)
        assert gate.after("x y", payment, {})[0] is payment and gate.after("x y", reading, {})[0] is reading


def test_after_agrees_when_both_are_on_the_same_side():
    gate = Gate(FakeModel(decide("pay_bill", 0.95)), veto=0.8)
    intent = Intent("transfer", payee_mention="لاحمد")  # another kind of payment: still the payment side
    result, evidence = gate.after("x", intent, {})
    assert result is intent
    assert evidence == {"decision": "pay_bill", "confidence": 0.95, "generative": "transfer", "verdict": "agree",
                        "payment_probability": pytest.approx(0.9625, abs=1e-4)}
    gate = Gate(FakeModel(decide("history", 0.95)), veto=0.8)
    assert gate.after("x", Intent("balance"), {})[1]["verdict"] == "agree"


def test_after_never_overrules_a_stop_a_refusal_or_a_question():
    gate = Gate(FakeModel(decide("transfer", 1.0)), veto=0.5)
    assert NEVER_OVERRULED == ("cancel", "unsupported", "chat")
    for intent in (Intent("cancel"), Intent("unsupported"), Intent("chat", amount_text="خمسين"), Intent("chat")):
        result, evidence = gate.after("خمسين", intent, {})
        assert result is intent and evidence["verdict"] == "abstain"
    for action in ("balance", "history", "add_contact"):  # the other readings that are not a payment are asked about
        result, evidence = gate.after("خمسين", Intent(action), {})
        assert result == Intent("chat") and evidence["verdict"] == "ask"


def test_after_keeps_a_payment_reading_when_a_part_of_the_message_reads_as_a_payment():
    text = "شكرا على المساعدة بس دز لاحمد خمسين"
    model = FakeModel(decide("chat", 0.3), {text: decide("chat", 0.95), "دز لاحمد خمسين": decide("transfer", 0.97)})
    intent = Intent("transfer", payee_mention="لاحمد", amount_text="خمسين")
    result, evidence = Gate(model, veto=0.8, part=0.9).after(text, intent, {})
    assert result is intent and evidence["verdict"] == "abstain" and evidence["payment_part"] > 0.9
    result, evidence = Gate(model, veto=0.8, part=1.01).after(text, intent, {})  # second reading switched off
    assert result == Intent("chat") and evidence["verdict"] == "ask"
    long_text = " ".join(["كلمة"] * (MAX_VETO_WORDS + 1))
    gate = Gate(FakeModel(decide("chat", 1.0)), veto=0.8, part=0.9)
    assert gate.after(long_text, intent, {})[0] is intent  # a very long message is never overruled


def test_after_counts_a_payment_among_the_extra_requests():
    gate = Gate(FakeModel(decide("chat", 1.0)), veto=0.8, part=1.01)
    intent = Intent("balance", extra=[Intent("transfer", payee_mention="لاحمد", amount_text="خمسين")])
    result, evidence = gate.after("x y", intent, {})
    assert result == Intent("chat") and evidence["verdict"] == "ask" and evidence["generative"] == "balance"


def test_after_with_the_real_model():
    gate = Gate()
    wrong = Intent("transfer", payee_mention="رصيدي")
    result, evidence = gate.after("شكد رصيدي", wrong, {})
    assert result == Intent("chat") and evidence["verdict"] == "ask" and evidence["decision"] == "balance"
    right = Intent("transfer", payee_mention="لاحمد", amount_text="خمسين")
    result, evidence = gate.after("دز لاحمد خمسين", right, {})
    assert result is right and evidence["verdict"] == "agree"
    result, evidence = gate.after("دز لاحمد خمسين", Intent("balance"), {})
    assert result == Intent("chat") and evidence["verdict"] == "ask"
    kept_language = gate.after("what is my balance", Intent("topup", language="en"), {})[0]
    assert kept_language == Intent("chat", language="en")


# ------------------------------------------------------------------ fallback


def test_fallback_gives_the_action_without_slots_or_a_plain_chat():
    gate = Gate()
    assert gate.fallback("دز لاحمد خمسين", {}) == Intent("transfer")  # the orchestrator asks who and how much
    assert gate.fallback("ادفع المولدة", None) == Intent("pay_bill")
    assert gate.fallback("شكد رصيدي", {}) == Intent("balance")
    assert gate.fallback("", {}) == Intent("chat")
    unsure = Gate(FakeModel(decide("transfer", 0.49)), floor=0.5)
    assert unsure.fallback("x", {}) == Intent("chat")
    assert unsure.fallback_evidence("x", {})[1]["verdict"] == "abstain"
    sure = Gate(FakeModel(decide("transfer", 0.5)), floor=0.5)
    assert sure.fallback("x", {}) == Intent("transfer")
    assert sure.fallback_evidence("x", {})[1]["verdict"] == "direct"


# ------------------------------------------------------------------ thresholds


def test_thresholds_come_from_the_model_file_unless_given():
    stored = IntentModel.load().meta["gate"]
    gate = Gate()
    assert gate._model is None  # nothing is loaded until the gate is used
    assert (gate.direct, gate.veto, gate.floor, gate.part) == (stored["direct"], stored["veto"], stored["floor"],
                                                               stored["part"])
    assert gate.direct_actions == tuple(stored["direct_actions"])
    given = Gate(direct=0.99, veto=0.95, floor=0.7, part=0.8, direct_actions=("cancel",))
    assert (given.direct, given.veto, given.floor, given.part, given.direct_actions) == (0.99, 0.95, 0.7, 0.8,
                                                                                        ("cancel",))
    plain = Gate(FakeModel(decide("chat", 1.0)))  # a model file without thresholds
    assert (plain.direct, plain.veto, plain.floor, plain.part) == (DEFAULT_DIRECT, DEFAULT_VETO, DEFAULT_FLOOR,
                                                                   DEFAULT_PART)
    assert plain.direct_actions == SLOT_FREE_ACTIONS
    unsafe_file = Gate(FakeModel(decide("chat", 1.0), meta={"gate": {"direct_actions": ["transfer", "balance"]}}))
    assert unsafe_file.direct_actions == ("balance",)


def test_the_stored_thresholds_respect_the_floors_and_the_grid():
    stored = IntentModel.load().meta["gate"]
    for key, minimum in (("direct", DEFAULT_DIRECT), ("veto", DEFAULT_VETO)):
        assert stored[key] == metrics.OFF or (minimum <= stored[key] <= 0.99 and stored[key] in metrics.GRID)
    assert stored["part"] in metrics.PART_VALUES
    assert set(stored["direct_actions"]) <= set(SLOT_FREE_ACTIONS)
    assert stored["target_precision"] == metrics.TARGET_PRECISION == 0.99


def test_on_the_tune_set_the_real_gate_keeps_the_99_percent_promise():
    """Through the real Gate object, not through the scoring code that chose the thresholds."""
    gate = Gate()
    result = metrics.replay(gate, TUNE)
    direct, veto = result["direct"], result["veto"]
    assert set(direct["per_action"]) == set(gate.direct_actions)  # nothing outside the direct actions was answered
    for action, cell in direct["per_action"].items():
        assert cell["answered"] >= metrics.MIN_DIRECT_ANSWERS, action
        assert cell["precision"] >= 0.99, (action, direct["wrong"])
    assert veto["precision"] >= 0.99
    assert veto["caught"] > 0.5 * len(TUNE)  # the cross-check is not asleep
    assert result["fallback"]["accuracy"] > 0.9

    stored = gate.model.meta["gate"]["tune"]  # what decision/train.py measured when it chose the thresholds
    assert (direct["answered"], direct["correct"]) == (stored["direct"]["answered"], stored["direct"]["correct"])
    for action, cell in direct["per_action"].items():
        assert cell["answered"] == stored["direct"]["per_action"][action]["answered"]
    assert veto["caught"] == stored["veto"]["right_side"]
    assert veto["false_alarms"] <= stored["veto"]["sure"] - stored["veto"]["right_side"]
    assert result["fallback"]["correct"] == stored["fallback"]["correct"]


def test_the_second_witness_also_reached_99_percent():
    stored = IntentModel.load().meta["gate"]["train_out_of_fold"]
    for cell in stored["direct"]["per_action"].values():
        assert cell["precision"] is None or cell["precision"] >= 0.99
    assert stored["veto"]["precision"] >= 0.99


# ------------------------------------------------------------------ how the thresholds are chosen


def rows_and_decisions(spec):
    """spec: (true label, said label, confidence, how many)."""
    rows, decisions = [], []
    for truth, said, confidence, count in spec:
        rows += [{"text": f"{truth} {said} {confidence} {n}", "label": truth} for n in range(count)]
        decisions += [decide(said, confidence)] * count
    return rows, decisions, [0.0] * len(rows)


def test_an_action_that_cannot_reach_99_percent_is_dropped_from_the_direct_path():
    evidence = [rows_and_decisions([
        ("balance", "balance", 0.95, 40),
        ("history", "history", 0.999, 30), ("chat", "history", 0.999, 3),  # wrong even at the top: never 99%
        ("cancel", "cancel", 0.93, 20), ("chat", "cancel", 0.93, 1),       # 20 of 21 right at 0.93 ...
        ("cancel", "cancel", 0.97, 15),                                    # ... and clean from 0.94 up
    ])]
    chosen = metrics.choose_direct(evidence)
    assert chosen["direct_actions"] == ["balance", "cancel"]
    assert chosen["direct"] == 0.94  # the lowest threshold that keeps both clean: balance 40 + cancel 15

    nothing = metrics.choose_direct([rows_and_decisions([("chat", "balance", 0.999, 30), ("balance", "balance", 0.999, 5)])])
    assert nothing["direct"] is None and nothing["direct_actions"] == []
    too_few = metrics.choose_direct([rows_and_decisions([("balance", "balance", 0.99, metrics.MIN_DIRECT_ANSWERS - 1)])])
    assert too_few["direct_actions"] == []


def test_a_second_evidence_set_can_only_make_the_direct_path_stricter():
    tune = rows_and_decisions([("balance", "balance", 0.95, 40), ("cancel", "cancel", 0.95, 40)])
    witness = rows_and_decisions([("balance", "balance", 0.95, 40), ("chat", "cancel", 0.99, 5),
                                  ("cancel", "cancel", 0.95, 40)])
    assert metrics.choose_direct([tune])["direct_actions"] == ["balance", "cancel"]
    assert metrics.choose_direct([tune, witness])["direct_actions"] == ["balance"]


def test_the_veto_threshold_is_the_lowest_that_is_right_99_percent_of_the_time():
    rows = [{"text": str(n), "label": "transfer" if n < 100 else "chat"} for n in range(205)]
    decisions = [side(0.99)] * 100 + [side(0.01)] * 100 + [side(0.88)] * 5  # the last 5 are wrong at 0.88
    assert metrics.choose_veto([(rows, decisions, [0.0] * 205)], part=0.9) == 0.89
    parts_say_payment = [0.0] * 100 + [0.95] * 100 + [0.0] * 5  # a part reads as a payment: not counted as sure
    assert metrics.veto_path(rows, decisions, 0.89, parts_say_payment, 0.9)["sure"] == 100
    hopeless = [rows_and_decisions([("chat", "transfer", 0.999, 10), ("transfer", "transfer", 0.999, 10)])]
    assert metrics.choose_veto(hopeless, part=0.9) is None


def test_payment_probability_adds_the_three_payment_actions():
    decision = Decision("chat", 0.4, {"chat": 0.4, "transfer": 0.3, "pay_bill": 0.2, "topup": 0.1})
    assert payment_probability(decision) == pytest.approx(0.6)


def test_parts_are_the_leading_and_trailing_runs_of_words():
    assert parts("a b c") == ["a", "a b", "b c", "c"]
    assert parts("a") == [] and parts("") == []
