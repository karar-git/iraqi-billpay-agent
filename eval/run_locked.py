"""Run the locked test set (eval/locked/) and write the result to eval/locked/RUNS.md.

The locked set is never tuned on. A core gets ONE blind run; every later run is a regression check and is
titled so. Every miss is written to RUNS.md, with what was expected and what happened.

Run from the repo root:
    python eval/run_locked.py --dry                              # check the file and print the plan, run nothing
    python eval/run_locked.py --label stub-first                 # one run with the core named by $BILLPAY_CORE
                                                                 # (else core.stub.StubCore), recorded in RUNS.md
    python eval/run_locked.py --label mycore-1 --core pkg.module:Core
    python eval/run_locked.py --label stub-first --regression    # a repeat: recorded as "regression check, not blind"
    python eval/run_locked.py --label x --record-core-as "production core"   # the name written to RUNS.md

The cases go through the same functions as eval/run_eval.py (imported, not copied): a fresh in-memory wallet per
case, a simulated user who taps the card, and a score read from the wallet's own records.

Exit code: 0 after a recorded run, 1 when money moved without a tap or the wallet audit failed (still recorded),
2 when a part of the repo the run needs is missing, 3 when the locked file does not match LOCK.json,
4 when the label is missing, malformed or already used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # allow `python eval/run_locked.py` as well as `python -m eval.run_locked`
    sys.path.insert(0, str(ROOT))

from eval.run_eval import (  # noqa: E402  (the shared harness; nothing here imports the agent yet)
    OUTCOMES,
    SEED_PATH,
    MissingPart,
    load_cases,
    load_parts,
    load_seed,
    print_report,
    run_case,
    summarise,
    with_pin_hashes,
)

LOCK_DIR = ROOT / "eval" / "locked"
LOCK_NAME = "LOCK.json"
RUNS_NAME = "RUNS.md"
ID_PREFIX = "lk_"
DEFAULT_CORE = "core.stub:StubCore"
BLIND = "blind run"
REGRESSION = "regression check, not blind"
LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,59}")
MARKER_RE = re.compile(r"^<!-- locked-run (\{.*\}) -->\s*$", re.MULTILINE)
RUNS_HEADER = (
    "# Runs of the locked set\n\n"
    "Written by `python eval/run_locked.py`, newest last. Do not edit by hand. The first run of a core is its one\n"
    "blind run; every later run is a regression check. Every missed case is listed.\n"
)


class LockError(Exception):
    """The locked file is missing, changed or malformed. Nothing may run."""


# ------------------------------------------------------------------ the lock


def file_sha256(path: Path) -> str:
    """SHA-256 of the file with CRLF read as LF, so a Windows checkout gives the same hash as a Unix one."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def read_lock(lock_dir: Path = LOCK_DIR) -> dict:
    path = lock_dir / LOCK_NAME
    if not path.is_file():
        raise LockError(f"{path} is missing")
    lock = json.loads(path.read_text(encoding="utf-8"))
    missing = [key for key in ("file", "sha256", "cases", "frozen", "note") if key not in lock]
    if missing:
        raise LockError(f"{path} lacks {', '.join(missing)}")
    return lock


def verify_lock(lock_dir: Path = LOCK_DIR) -> tuple[dict, Path, list[dict]]:
    """Check the hash FIRST, then the shape of the file. Returns (lock, case file, cases) or raises LockError."""
    lock = read_lock(lock_dir)
    path = lock_dir / lock["file"]
    if not path.is_file():
        raise LockError(f"{path} is missing")
    actual = file_sha256(path)
    if actual != lock["sha256"]:
        raise LockError(f"{path.name} does not match {LOCK_NAME}: sha256 is {actual}, the lock says {lock['sha256']}. "
                        "The locked set was changed; nothing was run.")
    cases = load_cases([path])
    ids = [case.get("id", "") for case in cases]
    if len(cases) != lock["cases"]:
        raise LockError(f"{path.name} has {len(cases)} cases, the lock says {lock['cases']}")
    if len(set(ids)) != len(ids) or not all(i.startswith(ID_PREFIX) for i in ids):
        raise LockError(f"{path.name}: ids must be unique and start with {ID_PREFIX!r}")
    bad = [case["id"] for case in cases if case.get("expected", {}).get("outcome") not in OUTCOMES]
    if bad:
        raise LockError(f"{path.name}: no valid expected outcome in {', '.join(bad)}")
    return lock, path, cases


# ------------------------------------------------------------------ the record of runs


def past_runs(runs_path: Path) -> list[dict]:
    """The runs already written to RUNS.md: [{"label", "core", "blind"}], oldest first."""
    if not runs_path.is_file():
        return []
    return [json.loads(found) for found in MARKER_RE.findall(runs_path.read_text(encoding="utf-8"))]


def run_kind(label: str, core: str, runs: list[dict], regression: bool) -> tuple[str | None, str]:
    """(title, reason). Title None means the run is refused."""
    if any(run["label"] == label for run in runs):
        if not regression:
            return None, (f"the label {label!r} is already in {RUNS_NAME}. A locked set gets one blind run per core. "
                          "Pass --regression to record a repeat as a regression check, or choose a new label.")
        return REGRESSION, "a repeat of an earlier label (--regression)"
    if regression:
        return REGRESSION, "--regression was passed"
    if any(run["core"] == core for run in runs):
        return REGRESSION, f"the core {core!r} was already run on this set"
    return BLIND, f"the first run of the core {core!r} on this set"


def git_commit(root: Path = ROOT) -> str:
    """Short commit id of the checkout, read-only. 'unknown' outside a git checkout."""
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}

    def git(*args: str) -> str | None:
        try:
            done = subprocess.run(["git", *args], cwd=root, env=env, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() if done.returncode == 0 else None

    commit = git("rev-parse", "--short", "HEAD")
    if not commit:
        return "unknown"
    changed = git("status", "--porcelain")
    return f"{commit} (with uncommitted changes)" if changed else commit


def why_missed(result: dict) -> str:
    """One line for a missed case. An orchestrator error gives its type only: a message could quote a core."""
    if result.get("error"):
        last = result["error"].strip().splitlines()[-1]
        return "orchestrator error: " + last.split(":", 1)[0].strip()
    parts = list(result.get("wrong_cards") or [])
    parts += [f"money moved without a tap ({quote})" for quote in result.get("untapped") or []]
    if not result.get("audit_ok", True):
        parts.append("wallet audit failed")
    if not parts:
        parts.append("wrong outcome")
    return "; ".join(parts).replace("|", "/").replace("\n", " ")


def totals(results: list[dict]) -> dict:
    """Per expected outcome: how many cases, how many passed, and what the agent did instead."""
    out = {}
    for expected in OUTCOMES:
        rows = [r for r in results if r["expected"] == expected]
        out[expected] = {"cases": len(rows), "passed": sum(bool(r["pass"]) for r in rows),
                         "got": dict(Counter(r["actual"] for r in rows))}
    return out


def render_entry(*, day: str, label: str, title: str, core: str, commit: str, lock: dict, results: list[dict]) -> str:
    """The Markdown block appended to RUNS.md for one run."""
    marker = json.dumps({"label": label, "core": core, "blind": title == BLIND}, ensure_ascii=False)
    passed = sum(bool(r["pass"]) for r in results)
    lines = [
        "",
        f"## {day} | {label} | {title}",
        "",
        f"<!-- locked-run {marker} -->",
        "",
        f"- Set: `{lock['file']}`, {lock['cases']} cases, frozen {lock['frozen']}, sha256 `{lock['sha256'][:16]}`",
        f"- Core: `{core}`",
        f"- Commit: {commit}",
        f"- Passed: {passed} / {len(results)}",
    ]
    for expected, row in totals(results).items():
        got = ", ".join(f"{name} {count}" for name, count in sorted(row["got"].items())) or "nothing"
        lines.append(f"- Expected {expected}: {row['passed']} / {row['cases']} passed (the agent did: {got})")
    lines += [
        f"- Wrong cards: {sum(len(r.get('wrong_cards') or []) for r in results)}",
        f"- Money moved without a tap: {sum(len(r.get('untapped') or []) for r in results)}",
        f"- Wallet audit failures: {sum(not r.get('audit_ok', True) for r in results)}",
        f"- Orchestrator errors: {sum(r.get('error') is not None for r in results)}",
        "",
    ]
    missed = [r for r in results if not r["pass"]]
    if not missed:
        lines.append("Missed cases: none.")
    else:
        lines += [f"Missed cases ({len(missed)}, all of them):", "",
                  "| Case | Type | Expected | Got | Why |", "|---|---|---|---|---|"]
        lines += [f"| {r['id']} | {r['type']} | {r['expected']} | {r['actual']} | {why_missed(r)} |" for r in missed]
    return "\n".join(lines) + "\n"


def append_entry(runs_path: Path, entry: str) -> None:
    text = runs_path.read_text(encoding="utf-8") if runs_path.is_file() else RUNS_HEADER
    with runs_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text.rstrip("\n") + "\n" + entry)


# ------------------------------------------------------------------ main


def core_spec(arg: str | None) -> str:
    return arg or os.environ.get("BILLPAY_CORE", "").strip() or DEFAULT_CORE


def print_plan(lock: dict, path: Path, cases: list[dict], spec: str, label: str | None, runs: list[dict],
               regression: bool, record_as: str | None) -> None:
    counts = Counter(case["expected"]["outcome"] for case in cases)
    print(f"Locked set: {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")
    print(f"  sha256 matches {LOCK_NAME} ({lock['sha256'][:16]}...), frozen {lock['frozen']}")
    print(f"  {len(cases)} cases: " + ", ".join(f"{counts.get(o, 0)} {o}" for o in OUTCOMES)
          + f", {len({c['user_id'] for c in cases})} users, {len({c['type'] for c in cases})} types")
    print(f"Core that would run: {spec}" + (f" (recorded as {record_as!r})" if record_as else ""))
    print(f"Commit: {git_commit()}")
    print(f"Runs recorded so far: {len(runs)}" + "".join(
        f"\n  {run['label']} | {run['core']} | {BLIND if run['blind'] else REGRESSION}" for run in runs))
    if label:
        core = record_as or spec.rpartition(":")[2] or spec
        title, reason = run_kind(label, core, runs, regression)
        print(f"Label {label!r}: " + (f"would be recorded as '{title}' ({reason})" if title else f"REFUSED: {reason}"))
    else:
        print("No --label given: a real run needs one.")
    print("Dry run: nothing was run and nothing was written.")


def main(argv: list[str] | None = None, *, lock_dir: Path = LOCK_DIR, today: str | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the locked test set once and record the result.")
    parser.add_argument("--label", help="name of this run in RUNS.md (letters, digits, space, . _ -)")
    parser.add_argument("--core", help="core class as module:Class (default: $BILLPAY_CORE, else core.stub:StubCore)")
    parser.add_argument("--regression", action="store_true", help="record this run as a regression check, not blind")
    parser.add_argument("--dry", action="store_true", help="check the locked file and print the plan; run nothing")
    parser.add_argument("--record-core-as", metavar="NAME",
                        help="name written to RUNS.md instead of the core's class name")
    parser.add_argument("--seed", type=Path, default=SEED_PATH, help="wallet seed JSON")
    parser.add_argument("--json", type=Path, help="also write per-case results here (keep it out of the repo)")
    parser.add_argument("-v", "--verbose", action="store_true", help="print the conversation of missed cases")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Arabic on a Windows console

    try:
        lock, path, cases = verify_lock(lock_dir)
    except LockError as exc:
        print(f"locked set: STOP. {exc}")
        return 3

    runs_path = lock_dir / RUNS_NAME
    runs = past_runs(runs_path)
    spec = core_spec(args.core)
    if args.label is not None and not LABEL_RE.fullmatch(args.label):
        print("locked set: the label must be 1 to 60 characters: letters, digits, space, dot, underscore, hyphen.")
        return 4
    if args.record_core_as is not None and not LABEL_RE.fullmatch(args.record_core_as):
        print("locked set: --record-core-as takes 1 to 60 characters: letters, digits, space, dot, underscore, hyphen.")
        return 4
    if args.dry:
        print_plan(lock, path, cases, spec, args.label, runs, args.regression, args.record_core_as)
        return 0
    if not args.label:
        print("locked set: a run needs --label NAME (or use --dry to look without running).")
        return 4

    # Refuse a reused label before anything runs: looking at a result is what ends a blind run.
    known_core = args.record_core_as or spec.rpartition(":")[2] or spec
    title, reason = run_kind(args.label, known_core, runs, args.regression)
    if title is None:
        print(f"locked set: REFUSED. {reason}")
        return 4

    try:
        parts = load_parts(spec)
    except MissingPart as exc:
        print(f"locked set: nothing to run. {exc}")
        return 2
    core = args.record_core_as or parts.core_name
    title, reason = run_kind(args.label, core, runs, args.regression)
    seed = with_pin_hashes(load_seed(args.seed))
    results = [run_case(case, seed, parts) for case in cases]
    summary = summarise(results, core)
    print_report(summary, results, args.verbose)

    entry = render_entry(day=today or date.today().isoformat(), label=args.label, title=title, core=core,
                         commit=git_commit(), lock=lock, results=results)
    append_entry(runs_path, entry)
    print(f"\nRecorded as '{title}' ({reason}) in {runs_path}")
    if args.json:
        args.json.write_text(json.dumps({"summary": summary, "cases": results}, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        print(f"wrote {args.json}")
    return 1 if summary["executed_without_tap"] or summary["audit_failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
