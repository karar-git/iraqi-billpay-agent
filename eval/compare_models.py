"""Which generative model is big enough? Run the public development cases once per candidate and compare.

Each candidate is one route for the "understand" role, read by the open reference core
(core.open_model.OpenModelCore) inside the real orchestrator and the mock wallet, exactly as eval/run_eval.py
plays a case. The rule-based stand-in runs first, as the baseline the bar is measured against.

    python -m eval.compare_models --candidate small=ollama:qwen2.5:3b --candidate mid=ollama:qwen2.5:7b
    python -m eval.compare_models --candidate mid=ollama:qwen2.5:7b --limit 20        # a quick sample
    python -m eval.compare_models --candidate host=groq:llama-3.1-8b-instant --allow-paid

    --candidate NAME=provider:model   a route as in docs/MODELS.md (options after "?" work too). Repeatable.
                                      List the candidates from the smallest model to the largest.
    --bar 0.95        a candidate meets the bar with zero wrong cards, no money moved without a tap, a clean
                      wallet audit, and at least this share of the stand-in's pass count (default 0.95)
    --limit N         only N cases, spread evenly over the set
    --allow-paid      needed for any candidate on a hosted provider (one that takes a key)
    --json PATH       also write the table and the per-case results
    -v, --verbose     show the model layer's warnings (which route failed, with which status)

What it prints, in this order: the estimated number of model calls, then (only after that, and only when
every candidate may run) one table with passed / total, wrong cards, money moved without a tap, average seconds
per case, total cost and calls, and the smallest candidate that meets the bar: the first one, in the order
given, that does.

It reads data/cases/dev.jsonl and nothing else. The locked set is never an input here: choosing a model is
tuning, and tuning happens on the development set only. A hosted candidate is refused without --allow-paid,
before any call is made.

Exit code: 0 after a run, 1 when money moved without a tap or the wallet audit failed for any candidate,
2 when nothing was run (a hosted candidate without --allow-paid, a route that cannot be used, a bad argument).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, TextIO

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # allow `python eval/compare_models.py` as well as `python -m eval.compare_models`
    sys.path.insert(0, str(ROOT))

from core.open_model import ROLE, SCHEMA, SYSTEM_PROMPT, OpenModelCore, reads_locally  # noqa: E402
from decision import Gate  # noqa: E402
from eval import run_eval as E  # noqa: E402
from llm import LLM, LLMError, Registry, Route  # noqa: E402

DEV_CASES = ROOT / "data" / "cases" / "dev.jsonl"   # the only case file this script reads
DEFAULT_BAR = 0.95
BASELINE = "stand-in (rules)"

MakeLLM = Callable[[Registry], LLM]


@dataclass
class Candidate:
    name: str
    route: Route
    local: bool          # a provider that needs no key
    usable: bool
    reason: str          # why it is usable or not: names of variables only, never a key
    host: str

    @property
    def spec(self) -> str:
        return self.route.spec


class BadArgument(Exception):
    """An argument that cannot be used. The message is safe to print."""


# ------------------------------------------------------------------ the plan


def parse_candidate(text: str, registry: Registry, env: Mapping[str, str]) -> Candidate:
    name, sep, spec = str(text).partition("=")
    name = name.strip()
    if not sep or not name or not spec.strip():
        raise BadArgument("a candidate is written NAME=provider:model")
    try:
        route = Route.parse(spec)
    except LLMError as exc:
        raise BadArgument(f"candidate {name}: {exc}") from None
    [entry] = Registry(registry.providers, {ROLE: [route]}).status(ROLE, env)
    return Candidate(name, route, bool(entry["local"]), bool(entry["usable"]), str(entry["reason"]), str(entry["host"]))


def sample(cases: list[dict], limit: int | None) -> list[dict]:
    """`limit` cases spread evenly over the set (the file is ordered by case type), or all of them."""
    if not limit or limit >= len(cases):
        return list(cases)
    return [cases[(i * len(cases)) // limit] for i in range(limit)]


def estimate_calls(cases: list[dict]) -> int:
    """The most generative calls one candidate can make on these cases: one per message that is not read
    locally, plus the typed yes a bill case may add. The decision model answers some of them itself."""
    messages = sum(not reads_locally(message) for case in cases for message in case["messages"])
    yeses = sum((case["expected"].get("fields") or {}).get("kind") == "bill" for case in cases)
    return messages + yeses


def tokens_per_call() -> int:
    """A rough size of one call: the fixed prompt and the schema in, a short JSON answer out."""
    return int((len(SYSTEM_PROMPT) + len(json.dumps(SCHEMA))) / 3.5) + 150


def print_plan(cases: list[dict], total: int, candidates: list[Candidate], out: TextIO) -> None:
    per = estimate_calls(cases)
    messages = sum(len(case["messages"]) for case in cases)
    print(f"Development set: {len(cases)} of {total} cases, {messages} messages ({DEV_CASES.name})", file=out)
    print(f"Estimated model calls: at most {per} per candidate, {per * len(candidates)} in total for "
          f"{len(candidates)} candidate(s). A timeout is retried once.", file=out)
    print(f"Roughly {tokens_per_call()} tokens per call, about "
          f"{per * tokens_per_call() // 1000} thousand tokens per candidate.", file=out)
    for candidate in candidates:
        where = "local, no key" if candidate.local else f"hosted, {candidate.host or 'unknown host'}"
        print(f"  {candidate.name}: {candidate.spec}  ({where})", file=out)


# ------------------------------------------------------------------ running


@contextlib.contextmanager
def quiet_model_log(quiet: bool = True):
    """Hold back the model layer's per-call warnings during a run (the table counts the failed calls), and put
    the logger back as it was afterwards."""
    logger = logging.getLogger("llm")
    level = logger.level
    if quiet:
        logger.setLevel(logging.ERROR)
    try:
        yield
    finally:
        logger.setLevel(level)


def _row(name: str, spec: str, local: bool | None, results: list[dict], seconds: float) -> dict[str, Any]:
    summary = E.summarise(results, name)
    return {"name": name, "route": spec, "local": local, "cases": summary["cases"], "passed": summary["passed"],
            "wrong_cards": summary["wrong_cards"], "executed_without_tap": summary["executed_without_tap"],
            "audit_failures": summary["audit_failures"], "errors": summary["errors"],
            "seconds_per_case": round(seconds / max(len(results), 1), 3), "usd": 0.0, "calls": 0,
            "failed_calls": 0, "fallbacks": 0, "direct": 0, "vetoes": 0, "dropped": 0,
            "per_type": summary["per_type"], "results": results}


def run_baseline(cases: list[dict], seed: dict, parts: E.Parts, timer: Callable[[], float]) -> dict[str, Any]:
    start = timer()
    results = [E.run_case(case, seed, parts) for case in cases]
    return _row(BASELINE, "core.stub:StubCore", None, results, timer() - start)


def run_candidate(candidate: Candidate, cases: list[dict], seed: dict, parts: E.Parts, registry: Registry,
                  make_llm: MakeLLM, gate: Gate, timer: Callable[[], float]) -> dict[str, Any]:
    """Every case through the open reference core with this one route for "understand" (no backup, no other
    role), so the numbers are this model's own."""
    llm = make_llm(Registry(registry.providers, {ROLE: [candidate.route]}))
    core = OpenModelCore(llm, gate)
    own = E.Parts(parts.session_cls, lambda: core, parts.new_wallet, candidate.name)
    start = timer()
    try:
        results = [E.run_case(case, seed, own) for case in cases]
        seconds = timer() - start
        stats = core.stats()
        route_stats = (llm.stats().get(ROLE) or [{}])[0]
    finally:
        llm.close()
    row = _row(candidate.name, candidate.spec, candidate.local, results, seconds)
    row.update(usd=round(float(stats["spent_usd"]), 6), calls=int(stats["calls"]),
               failed_calls=int(route_stats.get("failures", 0)), fallbacks=int(stats["fallbacks"]),
               direct=int(stats["direct"]), vetoes=int(stats["vetoes"]), dropped=int(stats["dropped"]))
    return row


def needed_passes(bar: float, baseline_passed: int) -> int:
    return math.ceil(bar * baseline_passed - 1e-9)


def meets_bar(row: dict[str, Any], needed: int) -> bool:
    return (row["wrong_cards"] == 0 and row["executed_without_tap"] == 0 and row["audit_failures"] == 0
            and row["passed"] >= needed)


# ------------------------------------------------------------------ the table


def print_table(baseline: dict[str, Any], rows: list[dict[str, Any]], bar: float, out: TextIO) -> dict | None:
    """Print the comparison and return the smallest candidate that meets the bar (None when none does)."""
    needed = needed_passes(bar, baseline["passed"])
    width = max(len(row["name"]) for row in [baseline, *rows]) + 2
    route_width = max(len(row["route"]) for row in [baseline, *rows]) + 2
    header = (f"{'candidate':<{width}}{'route':<{route_width}}{'passed':>9}{'wrong cards':>13}{'no-tap moves':>14}"
              f"{'s/case':>9}{'cost USD':>11}{'calls':>7}{'bar':>6}")
    print("\n" + header, file=out)
    print("-" * len(header), file=out)
    for row in [baseline, *rows]:
        is_baseline = row is baseline
        row["meets_bar"] = None if is_baseline else meets_bar(row, needed)
        mark = "" if is_baseline else ("yes" if row["meets_bar"] else "no")
        passed = f"{row['passed']}/{row['cases']}"
        print(f"{row['name']:<{width}}{row['route']:<{route_width}}{passed:>9}{row['wrong_cards']:>13}"
              f"{row['executed_without_tap']:>14}{row['seconds_per_case']:>9.2f}{row['usd']:>11.4f}"
              f"{row['calls']:>7}{mark:>6}", file=out)
    print(f"\nThe bar: zero wrong cards, no money moved without a tap, a clean wallet audit, and at least "
          f"{needed} of {baseline['cases']} cases passed ({bar:.0%} of the stand-in's {baseline['passed']}).", file=out)
    for row in rows:
        print(f"  {row['name']}: {row['direct']} answered by the decision model alone, {row['fallbacks']} readings "
              f"fell back to it ({row['failed_calls']} failed calls), {row['vetoes']} turned into a question by the "
              f"cross-check, {row['dropped']} pointers dropped (not the user's words), "
              f"{row['errors']} orchestrator errors.", file=out)
    winner = next((row for row in rows if row["meets_bar"]), None)
    if winner is not None:
        print(f"\nSmallest candidate that meets the bar: {winner['name']} ({winner['route']}). "
              "Candidates are taken in the order given, so list the smallest model first.", file=out)
    else:
        print("\nNo candidate meets the bar. The agent stays safe with any of them (it asks instead of guessing); "
              "a larger model, or the rule-based stand-in, completes more requests in one message.", file=out)
    return winner


# ------------------------------------------------------------------ main


def main(argv: list[str] | None = None, *, make_llm: MakeLLM | None = None, env: Mapping[str, str] | None = None,
         out: TextIO | None = None, timer: Callable[[], float] = time.perf_counter) -> int:
    parser = argparse.ArgumentParser(description="Compare candidate models for the understand role on the "
                                                 "public development cases.")
    parser.add_argument("--candidate", action="append", default=[], metavar="NAME=provider:model",
                        help="a route to try (repeatable; smallest model first)")
    parser.add_argument("--bar", type=float, default=DEFAULT_BAR,
                        help="share of the stand-in's pass count a candidate must reach (default 0.95)")
    parser.add_argument("--limit", type=int, help="only this many cases, spread evenly over the set")
    parser.add_argument("--allow-paid", action="store_true", help="allow candidates on hosted providers")
    parser.add_argument("--json", type=Path, help="write the table and the per-case results here")
    parser.add_argument("-v", "--verbose", action="store_true", help="show the model layer's warnings")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse printed the problem
        return 2 if exc.code else 0
    out = out or sys.stdout
    if hasattr(out, "reconfigure"):
        out.reconfigure(encoding="utf-8")  # Arabic on a Windows console
    env = os.environ if env is None else env
    make_llm = make_llm or (lambda registry: LLM(registry, env=env))

    try:
        if not args.candidate:
            raise BadArgument("give at least one --candidate NAME=provider:model")
        if not 0 < args.bar <= 1:
            raise BadArgument("--bar is a share between 0 and 1")
        if args.limit is not None and args.limit < 1:
            raise BadArgument("--limit is a number of cases, 1 or more")
        try:
            registry = Registry.from_env(env)
        except LLMError as exc:
            raise BadArgument(f"the model configuration cannot be read: {exc}") from None
        candidates = [parse_candidate(text, registry, env) for text in args.candidate]
        if len({candidate.name for candidate in candidates}) != len(candidates):
            raise BadArgument("two candidates have the same name")
    except BadArgument as exc:
        print(f"compare_models: {exc}", file=out)
        return 2

    all_cases = E.load_cases([DEV_CASES])
    cases = sample(all_cases, args.limit)
    print_plan(cases, len(all_cases), candidates, out)

    hosted = [candidate for candidate in candidates if not candidate.local]
    if hosted and not args.allow_paid:
        names = ", ".join(f"{candidate.name} ({candidate.spec})" for candidate in hosted)
        print(f"\nRefused, nothing was called. On a hosted provider, where calls can cost money: {names}. "
              "Read the estimate above, then add --allow-paid to run, or use --limit N for a small sample "
              "first.", file=out)
        return 2
    unusable = [candidate for candidate in candidates if not candidate.usable]
    if unusable:
        for candidate in unusable:
            print(f"\nCannot run {candidate.name} ({candidate.spec}): {candidate.reason}. "
                  "See `python -m llm check` and docs/MODELS.md.", file=out)
        return 2

    parts = E.load_parts("core.stub:StubCore")
    seed = E.with_pin_hashes(E.load_seed())
    gate = Gate()
    print(f"\nRunning {BASELINE} ...", file=out)
    baseline = run_baseline(cases, seed, parts, timer)
    rows = []
    with quiet_model_log(not args.verbose):
        for candidate in candidates:
            print(f"Running {candidate.name} ({candidate.spec}) ...", file=out)
            rows.append(run_candidate(candidate, cases, seed, parts, registry, make_llm, gate, timer))
    winner = print_table(baseline, rows, args.bar, out)

    if args.json:
        report = {"cases": len(cases), "of": len(all_cases), "bar": args.bar,
                  "needed": needed_passes(args.bar, baseline["passed"]),
                  "smallest_that_meets_the_bar": winner["name"] if winner else None,
                  "baseline": baseline, "candidates": rows}
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nwrote {args.json}", file=out)
    unsafe = any(row["executed_without_tap"] or row["audit_failures"] for row in [baseline, *rows])
    return 1 if unsafe else 0


if __name__ == "__main__":
    sys.exit(main())
