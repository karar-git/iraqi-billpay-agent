"""Scores for the decision model and for the gate's two paths. Standard library only.

Used by decision/train.py (to choose the thresholds on the tune set) and by eval/run_decision.py (to report).
`direct_path` and `veto_path` score lists of decisions, which is what training has for its out-of-fold lines;
`replay` runs the real Gate object on labelled lines, which is what the report prints. A test checks that the
two agree on the tune set (tests/test_decision_gate.py).
"""

from __future__ import annotations

import math
from typing import Sequence

from core.interface import PAYMENT_ACTIONS, Intent
from decision.gate import (DEFAULT_DIRECT, DEFAULT_VETO, SLOT_FREE_ACTIONS, Gate, ReadMany, other_request,
                           payment_part, payment_probability)
from decision.intent import Decision

TARGET_PRECISION = 0.99
GRID = tuple(round(0.50 + 0.01 * i, 2) for i in range(50))  # 0.50 .. 0.99
MIN_DIRECT_ANSWERS = 10  # an action answered fewer times than this on the tune set has no evidence behind it
PART_VALUES = (0.70, 0.80, 0.90)  # candidates for the second reading in parts (decision/gate.py)
OFF = 1.01  # a threshold no probability reaches: the path is switched off


def score(rows: Sequence[dict], decisions: Sequence[Decision], labels: Sequence[str]) -> dict:
    """Accuracy, per-label precision / recall, the confusion matrix, log loss, calibration, and every miss."""
    labels = list(labels)
    confusion = {expected: {got: 0 for got in labels} for expected in labels}
    misses, log_loss = [], 0.0
    for row, decision in zip(rows, decisions):
        confusion[row["label"]][decision.label] += 1
        log_loss -= math.log(max(decision.scores.get(row["label"], 0.0), 1e-12))
        if decision.label != row["label"]:
            misses.append({"text": row["text"], "expected": row["label"], "got": decision.label,
                           "confidence": round(decision.confidence, 4), "style": row.get("style", ""),
                           "source": row.get("source", "")})
    total = len(rows)
    per_label = {}
    for label in labels:
        hit = confusion[label][label]
        said = sum(confusion[other][label] for other in labels)
        support = sum(confusion[label].values())
        per_label[label] = {"precision": hit / said if said else None, "recall": hit / support if support else None,
                            "support": support, "predicted": said}
    by_source: dict[str, list[int]] = {}
    for row, decision in zip(rows, decisions):
        cell = by_source.setdefault(row.get("source", "") or "?", [0, 0])
        cell[0] += decision.label == row["label"]
        cell[1] += 1
    return {"lines": total, "correct": total - len(misses), "accuracy": (total - len(misses)) / total if total else 0.0,
            "log_loss": log_loss / total if total else 0.0, "per_label": per_label, "confusion": confusion,
            "by_source": {name: {"correct": c, "lines": n} for name, (c, n) in sorted(by_source.items())},
            "calibration": calibration(rows, decisions), "misses": misses}


def calibration(rows: Sequence[dict], decisions: Sequence[Decision], bins: int = 10) -> dict:
    """Reliability: in each confidence band, how often the model was right. ECE is the weighted gap."""
    cells = [[0, 0, 0.0] for _ in range(bins)]  # lines, correct, sum of confidence
    for row, decision in zip(rows, decisions):
        cell = cells[min(bins - 1, int(decision.confidence * bins))]
        cell[0] += 1
        cell[1] += decision.label == row["label"]
        cell[2] += decision.confidence
    total = sum(cell[0] for cell in cells)
    table = [{"from": i / bins, "to": (i + 1) / bins, "lines": n, "accuracy": c / n, "mean_confidence": s / n}
             for i, (n, c, s) in enumerate(cells) if n]
    ece = sum(band["lines"] * abs(band["accuracy"] - band["mean_confidence"]) for band in table) / total if total else 0.0
    return {"ece": ece, "bands": table}


def second_readings(read_many: ReadMany, rows: Sequence[dict], decisions: Sequence[Decision],
                    direct_minimum: float = DEFAULT_DIRECT, veto_minimum: float = DEFAULT_VETO,
                    ) -> tuple[list[float], list[float]]:
    """The gate's second reading in parts, for the lines where it can matter.

    (others, payment_parts): per line, how strongly a part reads as a different request (only computed for a
    slot-free reading of at least `direct_minimum`) and as a payment (only for a not-a-payment reading of at
    least `veto_minimum`); 0.0 elsewhere."""
    others, payment_parts = [], []
    for row, decision in zip(rows, decisions):
        candidate = decision.label in SLOT_FREE_ACTIONS and decision.confidence >= direct_minimum
        others.append(other_request(read_many, row["text"], decision) if candidate else 0.0)
        candidate = 1.0 - payment_probability(decision) >= veto_minimum
        payment_parts.append(payment_part(read_many, row["text"]) if candidate else 0.0)
    return others, payment_parts


def direct_path(rows: Sequence[dict], decisions: Sequence[Decision], threshold: float,
                actions: Sequence[str] = SLOT_FREE_ACTIONS, others: Sequence[float] | None = None,
                part: float = OFF) -> dict:
    """What Gate.before does on standalone messages: how many it answers and how often it is right.

    `others` (from second_readings) and `part` apply the second reading: a line whose part reads as another
    request at `part` or more is not answered."""
    per_action = {action: {"answered": 0, "correct": 0, "lines": 0} for action in actions}
    wrong = []
    for i, (row, decision) in enumerate(zip(rows, decisions)):
        if row["label"] in per_action:
            per_action[row["label"]]["lines"] += 1
        if decision.label in per_action and decision.confidence >= threshold \
                and (others is None or others[i] < part):
            cell = per_action[decision.label]
            cell["answered"] += 1
            if decision.label == row["label"]:
                cell["correct"] += 1
            else:
                wrong.append({"text": row["text"], "expected": row["label"], "got": decision.label,
                              "confidence": round(decision.confidence, 4)})
    for cell in per_action.values():
        cell["precision"] = cell["correct"] / cell["answered"] if cell["answered"] else None
        cell["recall"] = cell["correct"] / cell["lines"] if cell["lines"] else None
    answered = sum(cell["answered"] for cell in per_action.values())
    correct = sum(cell["correct"] for cell in per_action.values())
    return {"threshold": threshold, "actions": list(actions), "answered": answered, "correct": correct,
            "coverage": answered / len(rows) if rows else 0.0,
            "precision": correct / answered if answered else None, "per_action": per_action, "wrong": wrong}


def veto_path(rows: Sequence[dict], decisions: Sequence[Decision], threshold: float,
              payment_parts: Sequence[float] | None = None, part: float = OFF) -> dict:
    """What Gate.after rests on: when the model is `threshold` sure a message is / is not a payment, how often
    that side is the true one. With `payment_parts` and `part`, a not-a-payment reading does not count as sure
    when a part of the message reads as a payment (the second reading).

    `caught` is the share of all lines where a generative reading on the wrong side would be turned into a
    question; `false_alarms` the share where a correct generative reading would be."""
    sure = right = 0
    wrong = []
    for i, (row, decision) in enumerate(zip(rows, decisions)):
        p = payment_probability(decision)
        if p >= threshold:
            says_payment = True
        elif 1.0 - p >= threshold and (payment_parts is None or payment_parts[i] < part):
            says_payment = False
        else:
            continue
        sure += 1
        if says_payment == (row["label"] in PAYMENT_ACTIONS):
            right += 1
        else:
            wrong.append({"text": row["text"], "expected": row["label"], "got": decision.label,
                          "confidence": round(decision.confidence, 4), "payment_probability": round(p, 4)})
    total = len(rows)
    return {"threshold": threshold, "sure": sure, "right_side": right, "coverage": sure / total if total else 0.0,
            "precision": right / sure if sure else None, "caught": right / total if total else 0.0,
            "false_alarms": (sure - right) / total if total else 0.0, "wrong": wrong}


def fallback_path(rows: Sequence[dict], decisions: Sequence[Decision], floor: float) -> dict:
    """What Gate.fallback returns on standalone messages: the model's action at `floor` or more, else chat."""
    used = correct = wrong_payment = 0
    for row, decision in zip(rows, decisions):
        action = decision.label if decision.confidence >= floor else "chat"
        used += decision.confidence >= floor
        correct += action == row["label"]
        wrong_payment += action in PAYMENT_ACTIONS and row["label"] not in PAYMENT_ACTIONS
    total = len(rows)
    return {"floor": floor, "used_model": used, "correct": correct, "accuracy": correct / total if total else 0.0,
            "payment_question_for_a_non_payment": wrong_payment}


Evidence = Sequence[tuple[Sequence[dict], Sequence[Decision], Sequence[float]]]


def choose_direct(evidence: Evidence, minimum: float = DEFAULT_DIRECT) -> dict:
    """The direct threshold, the part threshold and the actions `before` may answer.

    `evidence` is a list of (rows, decisions, others): the tune set first, then any other lines the model did
    not train on. At a (direct, part) pair a slot-free action qualifies when it is answered with at least 99%
    precision on EVERY evidence set and at least MIN_DIRECT_ANSWERS times on the first one. The pair chosen
    (direct never below `minimum`) is the one whose qualifying actions give the most correct direct answers on
    the first set; on a tie, the stricter part threshold, then the lower direct threshold. An action that does
    not qualify there is not answered directly."""
    best: tuple[int, float, float, list[str]] | None = None
    for part in PART_VALUES:
        for threshold in GRID:
            if threshold < minimum:
                continue
            qualifying, correct = [], 0
            for action in SLOT_FREE_ACTIONS:
                cells = [direct_path(rows, decisions, threshold, (action,), others, part)["per_action"][action]
                         for rows, decisions, others in evidence]
                if cells[0]["answered"] >= MIN_DIRECT_ANSWERS \
                        and all(cell["precision"] is None or cell["precision"] >= TARGET_PRECISION for cell in cells):
                    qualifying.append(action)
                    correct += cells[0]["correct"]
            if qualifying and (best is None or correct > best[0]):
                best = (correct, threshold, part, qualifying)
    if best is None:
        return {"direct": None, "part": PART_VALUES[0], "direct_actions": []}
    return {"direct": best[1], "part": best[2], "direct_actions": best[3]}


def choose_veto(evidence: Evidence, part: float, minimum: float = DEFAULT_VETO) -> float | None:
    """The smallest veto threshold (not below `minimum`) at which the payment / not-a-payment side is right at
    least 99% of the time on every evidence set of (rows, decisions, payment_parts). None when no threshold
    gets there."""
    for threshold in GRID:
        if threshold < minimum:
            continue
        results = [veto_path(rows, decisions, threshold, payment_parts, part)
                   for rows, decisions, payment_parts in evidence]
        if all(result["precision"] is not None and result["precision"] >= TARGET_PRECISION for result in results):
            return threshold
    return None


def replay(gate: Gate, rows: Sequence[dict]) -> dict:
    """The real Gate on labelled standalone messages (no open question, no card): what it would have done.

    direct    `gate.before(text)`: answered when it returns an Intent, correct when that is the label.
    veto      `gate.after` on two simulated generative readings of every line:
              - the RIGHT one, Intent(label). Overruled into a question = a false alarm;
              - one on the WRONG side of "is this a payment?": "balance" for a payment line, "transfer" for any
                other line. Overruled into a question = caught.
              precision = caught / (caught + false alarms): of the readings the gate would overrule, the share
              that deserved it. Neither simulated reading has slots; the gate never looks at slots.
    fallback  `gate.fallback(text)`: correct when its action is the label; how many payment questions it would
              start for a line that is not a payment.
    """
    direct_actions = list(gate.direct_actions)
    per_action = {action: {"answered": 0, "correct": 0, "lines": 0} for action in direct_actions}
    direct_wrong, veto_wrong, fallback_wrong = [], [], []
    caught = false_alarms = fallback_used = fallback_correct = fallback_wrong_payment = 0
    for row in rows:
        text, label = row["text"], row["label"]
        pays = label in PAYMENT_ACTIONS
        if label in per_action:
            per_action[label]["lines"] += 1

        answer, evidence = gate.before_evidence(text, None)
        if answer is not None:
            cell = per_action.setdefault(answer.action, {"answered": 0, "correct": 0, "lines": 0})
            cell["answered"] += 1
            if answer.action == label:
                cell["correct"] += 1
            else:
                direct_wrong.append({"text": text, "expected": label, "got": answer.action,
                                     "confidence": evidence["confidence"]})

        _, evidence = gate.after(text, Intent(label), None)
        if evidence["verdict"] == "ask":
            false_alarms += 1
            veto_wrong.append({"text": text, "expected": label, "got": evidence["decision"],
                               "confidence": evidence["confidence"],
                               "payment_probability": evidence["payment_probability"]})
        _, evidence = gate.after(text, Intent("balance" if pays else "transfer"), None)
        caught += evidence["verdict"] == "ask"

        answer, evidence = gate.fallback_evidence(text, None)
        fallback_used += evidence["verdict"] == "direct"
        fallback_correct += answer.action == label
        if answer.action in PAYMENT_ACTIONS and not pays:
            fallback_wrong_payment += 1
            fallback_wrong.append({"text": text, "expected": label, "got": answer.action,
                                   "confidence": evidence["confidence"]})

    for cell in per_action.values():
        cell["precision"] = cell["correct"] / cell["answered"] if cell["answered"] else None
        cell["recall"] = cell["correct"] / cell["lines"] if cell["lines"] else None
    total = len(rows)
    answered = sum(cell["answered"] for cell in per_action.values())
    correct = sum(cell["correct"] for cell in per_action.values())
    overruled = caught + false_alarms
    return {
        "lines": total,
        "direct": {"threshold": gate.direct, "part": gate.part, "actions": direct_actions, "answered": answered,
                   "correct": correct, "coverage": answered / total if total else 0.0,
                   "precision": correct / answered if answered else None, "per_action": per_action,
                   "wrong": direct_wrong},
        "veto": {"threshold": gate.veto, "part": gate.part, "caught": caught, "false_alarms": false_alarms,
                 "coverage": overruled / total if total else 0.0,
                 "precision": caught / overruled if overruled else None,
                 "caught_share": caught / total if total else 0.0,
                 "false_alarm_share": false_alarms / total if total else 0.0, "wrong": veto_wrong},
        "fallback": {"floor": gate.floor, "used_model": fallback_used, "correct": fallback_correct,
                     "accuracy": fallback_correct / total if total else 0.0,
                     "payment_question_for_a_non_payment": fallback_wrong_payment, "wrong": fallback_wrong},
    }
