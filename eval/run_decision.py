"""Score the intent decision model (decision/) on its tune set or, once, on its locked set.

Run from the repo root:
    python eval/run_decision.py                            # the tune set: prints everything, writes nothing
    python eval/run_decision.py --set locked               # the locked set: checks its SHA-256, runs ONCE and
                                                           # appends the numbers and EVERY missed line to
                                                           # decision/RESULTS.md
    python eval/run_decision.py --set locked --regression  # any later run: recorded as a regression check
    python eval/run_decision.py --set locked --dry         # check the lock and say what would happen; run nothing

What is printed: accuracy, precision and recall per label, the confusion matrix, the score per style slice,
calibration, and what the gate (decision/gate.py) would do with these lines: coverage and precision of the direct
path (Gate.before) and of the cross-check (Gate.after), and the fallback (Gate.fallback). The gate numbers come
from the real Gate object (decision.metrics.replay).

The tune set chose the settings and the thresholds, so its numbers are optimistic. The locked set was frozen
before the first training run; its first run is the one blind run and is never repeated as such.

Standard library only: the model is read by decision/intent.py. No network, no generative model.

Exit code: 0 after a run, 2 when the model file is missing, 3 when the locked file does not match LOCK.json,
4 when a locked run is refused (a second run without --regression, or --regression with no blind run before).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # allow `python eval/run_decision.py` as well as `python -m eval.run_decision`
    sys.path.insert(0, str(ROOT))

from decision import metrics  # noqa: E402
from decision.data.build import DATA_DIR, LOCK_NAME, SLICES, file_sha256, load_part, read_lock  # noqa: E402
from decision.gate import Gate  # noqa: E402
from decision.intent import MODEL_PATH, IntentModel  # noqa: E402

RESULTS_PATH = ROOT / "decision" / "RESULTS.md"
BLIND = "blind run"
REGRESSION = "regression check, not blind"
MARKER_RE = re.compile(r"^<!-- decision-run (\{.*\}) -->\s*$", re.MULTILINE)
RESULTS_HEADER = (
    "# Results of the decision model on its locked set\n\n"
    "Written by `python eval/run_decision.py --set locked`, newest last. Do not edit by hand. The first run is the\n"
    "one blind run: the locked lines were frozen before the first training run and nothing was tuned on them.\n"
    "Every later run is a regression check. Every missed line is listed.\n"
)


class LockError(Exception):
    """The locked file is missing or does not match LOCK.json. Nothing may run."""


# ------------------------------------------------------------------ scoring


def evaluate(model: IntentModel, rows: list[dict]) -> dict:
    """Everything this script reports about `rows`: the model's scores and the gate's three paths."""
    labels = list(model.labels)  # loads the file here, outside the timing
    started = time.perf_counter()
    decisions = [model.predict(row["text"]) for row in rows]
    seconds = time.perf_counter() - started
    result = metrics.score(rows, decisions, labels)
    result["labels"] = labels
    result["ms_per_prediction"] = 1000.0 * seconds / len(rows) if rows else 0.0
    result["gate"] = metrics.replay(Gate(model), rows)
    return result


def share(part: int, whole: int) -> str:
    return f"{part} / {whole} ({100.0 * part / whole:.1f}%)" if whole else f"{part} / {whole}"


def ratio(value: float | None) -> str:
    return "none answered" if value is None else f"{100.0 * value:.1f}%"


def cell(text: object) -> str:
    """A value safe inside a Markdown table row."""
    return str(text).replace("|", "/").replace("\n", " ").strip()


def report_lines(result: dict) -> list[str]:
    """The report as Markdown lines (printed for the tune set, written to RESULTS.md for the locked set)."""
    labels = result["labels"]
    gate = result["gate"]
    direct, veto, fallback = gate["direct"], gate["veto"], gate["fallback"]
    lines = [
        f"- Accuracy: {share(result['correct'], result['lines'])}",
        f"- Log loss: {result['log_loss']:.4f}. Calibration gap (ECE, 10 bands): {result['calibration']['ece']:.4f}",
        "",
        "Per label:",
        "",
        "| Label | Lines | Said by the model | Precision | Recall |",
        "|---|---|---|---|---|",
    ]
    for label in labels:
        row = result["per_label"][label]
        lines.append(f"| {label} | {row['support']} | {row['predicted']} | {ratio(row['precision'])} | "
                     f"{ratio(row['recall'])} |")
    lines += ["", "Confusion matrix (rows: the label in the file, columns: what the model said):", "",
              "| | " + " | ".join(labels) + " |", "|---|" + "---|" * len(labels)]
    for expected in labels:
        lines.append(f"| **{expected}** | " + " | ".join(str(result["confusion"][expected][got]) for got in labels)
                     + " |")
    lines += ["", "Per style slice:", "", "| Slice | Correct | What it holds |", "|---|---|---|"]
    for name, row in result["by_source"].items():
        lines.append(f"| {name} | {share(row['correct'], row['lines'])} | {SLICES.get(name, '')} |")
    lines += ["", "Confidence against reality:", "", "| Confidence | Lines | Right |", "|---|---|---|"]
    for band in result["calibration"]["bands"]:
        lines.append(f"| {band['from']:.1f} to {band['to']:.1f} | {band['lines']} | "
                     f"{100.0 * band['accuracy']:.1f}% |")

    actions = ", ".join(direct["actions"]) or "no action (the path is off)"
    lines += [
        "",
        "The gate on these lines (each read as a standalone message, no open question, no card):",
        "",
        f"- Direct path (`Gate.before`, threshold {direct['threshold']:.2f}, actions: {actions}): answered "
        f"{share(direct['answered'], result['lines'])} of all lines without a generative model, "
        f"{direct['correct']} of them right (precision {ratio(direct['precision'])}).",
    ]
    for action, row in direct["per_action"].items():
        lines.append(f"  - {action}: answered {row['answered']}, right {row['correct']}, out of {row['lines']} "
                     f"{action} lines in the set (precision {ratio(row['precision'])}, recall {ratio(row['recall'])})")
    lines += [
        f"- Cross-check (`Gate.after`, threshold {veto['threshold']:.2f}). Two generative readings are simulated "
        "per line: the right one, and one on the wrong side of \"is this a payment?\".",
        f"  - Wrong-side readings turned into a question: {share(veto['caught'], result['lines'])}",
        f"  - Right readings turned into a question (false alarms): {share(veto['false_alarms'], result['lines'])}",
        f"  - Precision of an overrule: {ratio(veto['precision'])}",
        f"- Fallback (`Gate.fallback`, floor {fallback['floor']:.2f}): right action on "
        f"{share(fallback['correct'], result['lines'])}. Lines that are not a payment but would get a payment "
        f"question: {fallback['payment_question_for_a_non_payment']}.",
    ]
    for title, rows in (("Direct answers that were wrong", direct["wrong"]),
                        ("Right readings the cross-check would turn into a question", veto["wrong"]),
                        ("Lines that are not a payment where the fallback would start a payment question",
                         fallback["wrong"])):
        if rows:
            lines += ["", f"{title} ({len(rows)}, all of them):", "", "| Expected | Model said | Confidence | Text |",
                      "|---|---|---|---|"]
            lines += [f"| {r['expected']} | {r['got']} | {r['confidence']:.2f} | {cell(r['text'])} |" for r in rows]
        else:
            lines += ["", f"{title}: none."]

    misses = result["misses"]
    if not misses:
        lines += ["", "Missed lines: none."]
    else:
        lines += ["", f"Missed lines ({len(misses)}, all of them):", "",
                  "| Expected | Model said | Confidence | Slice | Style | Text |", "|---|---|---|---|---|---|"]
        lines += [f"| {m['expected']} | {m['got']} | {m['confidence']:.2f} | {cell(m['source'])} | {cell(m['style'])} | "
                  f"{cell(m['text'])} |" for m in misses]
    return lines


# ------------------------------------------------------------------ the locked set


def verify_lock(data_dir: Path = DATA_DIR) -> dict:
    """The lock, after checking that locked.jsonl is the frozen file. Raises LockError otherwise."""
    lock = read_lock(data_dir)
    if lock is None:
        raise LockError(f"{LOCK_NAME} is missing in {data_dir}")
    path = data_dir / lock["file"]
    if not path.is_file():
        raise LockError(f"{lock['file']} is missing")
    found = file_sha256(path)
    if found != lock["sha256"]:
        raise LockError(f"{lock['file']} has the hash {found[:16]}..., {LOCK_NAME} says {lock['sha256'][:16]}... "
                        "The locked file was changed; restore it from version control.")
    return lock


def past_runs(results_path: Path) -> list[dict]:
    """The runs already recorded in RESULTS.md."""
    if not results_path.is_file():
        return []
    return [json.loads(found) for found in MARKER_RE.findall(results_path.read_text(encoding="utf-8"))]


def model_sha256(model: IntentModel) -> str:
    return hashlib.sha256(model.path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def render_entry(*, day: str, title: str, lock: dict, model: IntentModel, result: dict) -> str:
    """The Markdown block appended to RESULTS.md for one run of the locked set."""
    meta = model.meta
    gate = meta.get("gate") or {}
    digest = model_sha256(model)
    marker = json.dumps({"model": meta.get("name", model.path.stem), "model_sha256": digest[:16],
                         "blind": title == BLIND, "correct": result["correct"], "lines": result["lines"]})
    lines = [
        "",
        f"## {day} | {meta.get('name', model.path.stem)} | {title}",
        "",
        f"<!-- decision-run {marker} -->",
        "",
        f"- Set: `{lock['file']}`, {lock['lines']} lines, frozen {lock['frozen']}, sha256 `{lock['sha256'][:16]}`",
        f"- Model: `{model.path.name}`, trained {meta.get('trained', 'unknown')}, sha256 `{digest[:16]}`",
        f"- Gate thresholds, chosen on the tune set before this run: direct {gate.get('direct')} for "
        f"{', '.join(gate.get('direct_actions') or []) or 'no action'}; cross-check {gate.get('veto')}; "
        f"fallback floor {gate.get('floor')}; second reading in parts {gate.get('part')}",
    ]
    return "\n".join(lines + report_lines(result)) + "\n"


def append_entry(results_path: Path, entry: str) -> None:
    text = results_path.read_text(encoding="utf-8") if results_path.is_file() else RESULTS_HEADER
    with results_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text.rstrip("\n") + "\n" + entry)


# ------------------------------------------------------------------ main


def main(argv: list[str] | None = None, *, data_dir: Path = DATA_DIR, model_path: Path = MODEL_PATH,
         results_path: Path = RESULTS_PATH, today: str | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score the intent decision model on its tune set or, once, on "
                                                 "its locked set.")
    parser.add_argument("--set", dest="part", choices=("tune", "locked"), default="tune",
                        help="tune (default; prints, writes nothing) or locked (one run, recorded)")
    parser.add_argument("--regression", action="store_true",
                        help="locked only: a run after the blind one, recorded as a regression check")
    parser.add_argument("--dry", action="store_true", help="locked only: check the lock and the history, run nothing")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Arabic on a Windows console

    if not Path(model_path).is_file():
        print(f"MISSING: {model_path}. Train first: python -m decision.train", file=sys.stderr)
        return 2
    model = IntentModel(model_path)

    if args.part == "tune":
        rows = load_part("tune", data_dir)
        result = evaluate(model, rows)
        print(f"Decision model {model.meta.get('name')} on the TUNE set ({len(rows)} lines). The settings and the "
              "thresholds were chosen on these lines, so the numbers are optimistic. Nothing is written.")
        print(f"About {result['ms_per_prediction']:.2f} ms per prediction on this machine.\n")
        print("\n".join(report_lines(result)))
        return 0

    try:
        lock = verify_lock(data_dir)
    except LockError as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 3
    runs = past_runs(results_path)
    blind_done = any(run.get("blind") for run in runs)
    if blind_done and not args.regression:
        print(f"REFUSED: the locked set already had its one blind run (see {results_path.name}). A later run is a "
              "regression check: add --regression.", file=sys.stderr)
        return 4
    if args.regression and not blind_done:
        print("REFUSED: there is no blind run yet, so this cannot be a regression check. Run without --regression.",
              file=sys.stderr)
        return 4
    title = REGRESSION if blind_done else BLIND
    if args.dry:
        print(f"{lock['file']}: {lock['lines']} lines, sha256 matches {LOCK_NAME}. {len(runs)} run(s) recorded. "
              f"The next run would be recorded as: {title}. Nothing was run.")
        return 0

    rows = load_part("locked", data_dir)
    result = evaluate(model, rows)
    entry = render_entry(day=today or date.today().isoformat(), title=title, lock=lock, model=model, result=result)
    append_entry(results_path, entry)
    print(entry)
    print(f"Recorded in {results_path} as: {title}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
