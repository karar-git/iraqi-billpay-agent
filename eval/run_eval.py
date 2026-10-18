"""Offline evaluation of the bill-pay agent on the hand-written cases in data/cases/.

Every case runs against a fresh in-memory mock wallet loaded from data/wallet_seed.json.
The simulated user sends the case's messages in order. When the reply to the LAST message shows a
card, the user taps "إي، ادفع" with their PIN, and keeps tapping any card that follows (up to 3).

Run from the repo root:
    python -m eval.run_eval                        # every data/cases/*.jsonl file
    python -m eval.run_eval --type cancel -v       # one case type, print failing conversations
    python -m eval.run_eval --json results.json    # also write per-case results
    python -m eval.run_eval --core pkg.module:Core # evaluate another core (default core.stub.StubCore)

Exit code: 0 after a run, 1 when money moved without a tap or the wallet audit failed,
2 when a part of the repo the eval needs (agent/, core/, wallet/) is missing.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import traceback
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # allow `python eval/run_eval.py` as well as `python -m eval.run_eval`
    sys.path.insert(0, str(ROOT))

SEED_PATH = ROOT / "data" / "wallet_seed.json"
CASES_DIR = ROOT / "data" / "cases"
CONFIRM_BUTTON = "إي، ادفع"
MAX_TAPS = 3
OUTCOMES = ("EXECUTE", "ASK", "REFUSE")
ASK_KINDS = {"ask", "question", "clarify", "clarification"}
REFUSE_KINDS = {"refuse", "refusal", "refused", "error", "failed", "failure", "cancel", "cancelled",
                "canceled", "blocked", "unsupported", "info", "done", "result", "chat"}


class MissingPart(Exception):
    """A part of the repo the evaluation needs is not in this checkout yet."""


@dataclass
class Parts:
    """What the evaluation drives: the orchestrator, a core factory and a wallet factory."""

    session_cls: type
    new_core: Callable[[], Any]
    new_wallet: Callable[[dict], Any]
    core_name: str


# ------------------------------------------------------------------ loading


def load_seed(path: Path = SEED_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_cases(paths: list[Path], types: set[str] | None = None) -> list[dict]:
    cases = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                case = json.loads(line)
                if not types or case["type"] in types:
                    cases.append(case)
    return cases


def _import(module: str, what: str):
    """Import a module, turning 'the folder is not there' into MissingPart (real bugs still raise)."""
    try:
        return importlib.import_module(module)
    except ModuleNotFoundError as exc:
        if exc.name and module.startswith(exc.name):
            raise MissingPart(f"{what} is not in this checkout yet (cannot import {module}).") from exc
        raise


def load_parts(core_spec: str | None) -> Parts:
    """Find the orchestrator, the core and the wallet app. Raises MissingPart if one is absent."""
    session_cls = _import("agent.session", "agent/ (the orchestrator)").Session
    if core_spec:
        module_name, _, class_name = core_spec.partition(":")
        core_cls = getattr(_import(module_name, f"core module {module_name}"), class_name or "Core")
    else:
        core_cls = _import("core.stub", "core/stub.py (the rule-based core)").StubCore
    create_app = _import("wallet.api", "wallet/ (the mock wallet)").create_app
    from fastapi.testclient import TestClient

    def new_wallet(seed: dict):
        return TestClient(create_app(":memory:", seed=seed))

    return Parts(session_cls, core_cls, new_wallet, core_cls.__name__)


def with_pin_hashes(seed: dict) -> dict:
    """Hash each synthetic PIN once, so building a fresh wallet per case stays fast."""
    try:
        from wallet.db import hash_pin
    except ImportError:
        return seed
    users = [{**u, "pin_hash": u.get("pin_hash") or hash_pin(u["pin"])} for u in seed["users"]]
    return {**seed, "users": users}


# ------------------------------------------------------------------ reading replies


def _get(obj: Any, *names: str) -> Any:
    """First non-empty attribute (or dict key) among `names`."""
    for name in names:
        value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
        if value not in (None, "", [], {}):
            return value
    return None


def reply_text(reply: Any) -> str:
    if isinstance(reply, str):
        return reply
    parts = [_get(reply, "text", "message") or ""]
    card = _get(reply, "card")
    if card is not None:
        parts += list(_get(card, "lines") or [])
    messages = _get(reply, "messages")
    if isinstance(messages, list):
        parts += [str(m) for m in messages]
    return "\n".join(p for p in parts if p)


def fetch_quote(client: Any, qid: str) -> dict | None:
    response = client.get(f"/quotes/{qid}")
    return response.json() if response.status_code == 200 else None


def card_quote_id(reply: Any, client: Any) -> str | None:
    """Quote id of the card in this reply, or None when the reply shows no card.

    A reply shows a card when it has a `card`, or when it names a quote that still waits for the tap.
    """
    card = _get(reply, "card")
    qid = _get(reply, "quote_id") or (card is not None and _get(card, "quote_id")) or None
    if not qid:
        return None
    if card is not None:
        return qid
    quote = fetch_quote(client, qid)
    return qid if quote and quote.get("state") == "QUOTED" else None


def classify(reply: Any) -> str:
    """ASK or REFUSE for a reply after which nothing was paid."""
    kind = str(_get(reply, "kind", "type", "status", "outcome") or "").strip().lower()
    if kind in ASK_KINDS:
        return "ASK"
    if kind in REFUSE_KINDS:
        return "REFUSE"
    return "ASK" if reply_text(reply).rstrip(" .!\n").endswith(("؟", "?")) else "REFUSE"


# ------------------------------------------------------------------ scoring one case


def _digits(phone: Any) -> str:
    return "".join(ch for ch in str(phone or "") if ch.isdigit())[-10:]


def card_matches(fields: dict, quote: dict) -> bool:
    """Does a quote (what the card shows) pay the right payee/biller the right amount?"""
    if quote.get("kind") != fields["kind"] or quote.get("amount") != fields["amount"]:
        return False
    target = quote.get("target") or {}
    if isinstance(target, str):
        target = json.loads(target)
    if fields["kind"] == "transfer":
        return target.get("contact_id") == fields["contact_id"] or _digits(target.get("phone")) == _digits(fields["phone"])
    if fields["kind"] == "bill":
        return target.get("bill_id") == fields["bill_id"]
    return target.get("biller_id") == fields["biller_id"] and _digits(target.get("phone")) == _digits(fields["phone"])


def describe(quote: dict | None) -> str:
    if not quote:
        return "a quote the wallet does not know"
    target = quote.get("target") or {}
    who = _get(target, "display_name", "label_ar", "biller_id", "contact_id") or "?"
    return f"{quote.get('kind')} {quote.get('amount', 0):,} IQD -> {who}"


def wrong_card_reason(case: dict, quote: dict | None, final: bool) -> str | None:
    """Why this card is wrong, or None when it is fine."""
    expected = case["expected"]
    allowed = ([expected["fields"]] if "fields" in expected else []) + expected.get("next_fields", [])
    if not allowed:
        return f"card shown ({describe(quote)}) but the case expects {expected['outcome']}"
    if quote is None or not any(card_matches(f, quote) for f in allowed):
        return f"card does not match the request: {describe(quote)}"
    if final and expected["outcome"] != "EXECUTE":
        return f"card shown ({describe(quote)}) where {expected['outcome']} was expected"
    return None


def run_case(case: dict, seed: dict, parts: Parts) -> dict:
    """Play one case through a fresh wallet and orchestrator and score it."""
    uid, messages = case["user_id"], case["messages"]
    pin = next(u["pin"] for u in seed["users"] if u["id"] == uid)
    client = parts.new_wallet(seed)
    result = {"id": case["id"], "type": case["type"], "user_id": uid, "expected": case["expected"]["outcome"],
              "actual": "ERROR", "pass": False, "wrong_cards": [], "executed": [], "untapped": [],
              "audit_ok": True, "ask_field": None, "ask_field_ok": None, "error": None, "transcript": []}
    log = result["transcript"]
    cards: list[tuple[str, bool]] = []  # (quote id, shown after the last message or a tap)
    tapped: list[str] = []
    try:
        session = parts.session_cls(wallet=client, core=parts.new_core())
        reply, qid = None, None
        for n, text in enumerate(messages, start=1):
            reply = session.handle_text(uid, text)
            log += [f"user: {text}", f"agent: {reply_text(reply)}"]
            qid = card_quote_id(reply, client)
            if qid:
                cards.append((qid, n == len(messages)))
        while qid and qid not in tapped and len(tapped) < MAX_TAPS:
            tapped.append(qid)
            log.append(f"user taps [{CONFIRM_BUTTON}] on {qid}")
            reply = session.handle_button(uid, qid, CONFIRM_BUTTON, pin=pin)
            log.append(f"agent: {reply_text(reply)}")
            qid = card_quote_id(reply, client)
            if qid and qid not in tapped:
                cards.append((qid, True))
    except Exception:  # an orchestrator crash is a failed case, not a crashed evaluation
        result["error"] = traceback.format_exc(limit=4)
        return result

    quotes = {qid: fetch_quote(client, qid) for qid, _ in cards}
    result["executed"] = [q for q in tapped if (quotes.get(q) or {}).get("state") == "SUCCEEDED"]
    recent = client.get(f"/users/{uid}/transactions", params={"since_minutes": 60})
    moved = [t["quote_id"] for t in (recent.json() if recent.status_code == 200 else [])
             if t.get("status") not in ("reversed",)]
    result["untapped"] = [q for q in moved if q not in tapped]
    audit = client.get("/admin/audit")
    result["audit_ok"] = audit.status_code != 200 or bool(audit.json().get("ok", True))
    result["wrong_cards"] = [reason for qid, final in cards
                             if (reason := wrong_card_reason(case, quotes[qid], final))]
    result["actual"] = "EXECUTE" if result["executed"] else classify(reply)
    if result["actual"] == "ASK":
        result["ask_field"] = _get(reply, "ask_field", "question_key", "missing", "slot")
        if result["ask_field"] and "ask_field" in case["expected"]:
            result["ask_field_ok"] = str(result["ask_field"]) == case["expected"]["ask_field"]
    result["pass"] = (result["actual"] == result["expected"] and not result["wrong_cards"]
                      and not result["untapped"] and result["audit_ok"])
    return result


# ------------------------------------------------------------------ report


def summarise(results: list[dict], core_name: str) -> dict:
    per_type: dict[str, list[int]] = {}
    for r in results:
        counts = per_type.setdefault(r["type"], [0, 0])
        counts[0] += r["pass"]
        counts[1] += 1
    matrix = {e: dict(Counter(r["actual"] for r in results if r["expected"] == e)) for e in OUTCOMES}
    named = [r for r in results if r["ask_field_ok"] is not None]
    return {
        "core": core_name,
        "cases": len(results),
        "passed": sum(r["pass"] for r in results),
        "per_type": per_type,
        "outcome_matrix": matrix,
        "wrong_cards": sum(len(r["wrong_cards"]) for r in results),
        "executed_without_tap": sum(len(r["untapped"]) for r in results),
        "audit_failures": sum(not r["audit_ok"] for r in results),
        "errors": sum(r["error"] is not None for r in results),
        "ask_field_agreement": [sum(r["ask_field_ok"] for r in named), len(named)],
    }


def print_report(summary: dict, results: list[dict], verbose: bool) -> None:
    n, passed = summary["cases"], summary["passed"]
    print(f"\nBill-pay agent evaluation | {n} cases | core: {summary['core']}\n")
    print(f"{'type':<18}{'pass':>8}")
    for case_type, (ok, total) in sorted(summary["per_type"].items()):
        print(f"{case_type:<18}{f'{ok}/{total}':>8}")
    print(f"{'TOTAL':<18}{f'{passed}/{n}':>8}  ({100 * passed / max(n, 1):.0f}%)\n")

    columns = (*OUTCOMES, "ERROR")
    print("Outcome matrix (rows = expected, columns = actual)")
    print(f"{'':<10}" + "".join(f"{c:>9}" for c in columns))
    for expected in OUTCOMES:
        row = summary["outcome_matrix"][expected]
        print(f"{expected:<10}" + "".join(f"{row.get(c, 0):>9}" for c in columns))

    agree, named = summary["ask_field_agreement"]
    print(f"\nWrong cards:          {summary['wrong_cards']}  (wrong payee/amount/biller, or a card where ASK/REFUSE was expected)")
    print(f"Executed without tap: {summary['executed_without_tap']}")
    print(f"Wallet audit failed:  {summary['audit_failures']}")
    print(f"Orchestrator errors:  {summary['errors']}")
    if named:
        print(f"Ask-field agreement:  {agree}/{named}  (only replies that name the field they ask for)")

    failures = [r for r in results if not r["pass"]]
    if failures:
        print("\nFailed cases")
        for r in failures:
            why = "; ".join(r["wrong_cards"] + [f"executed without tap: {q}" for q in r["untapped"]])
            if r["error"]:
                why = r["error"].strip().splitlines()[-1]
            print(f"  {r['id']:<22} expected {r['expected']:<8} got {r['actual']:<8} {why}")
            if verbose:
                for line in r["transcript"]:
                    print(f"      {line}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score the agent on the hand-written test cases.")
    parser.add_argument("--cases", nargs="*", type=Path, help="JSONL case files (default: data/cases/*.jsonl)")
    parser.add_argument("--type", action="append", dest="types", help="only this case type (repeatable)")
    parser.add_argument("--core", help="core class as module:Class (default core.stub:StubCore)")
    parser.add_argument("--seed", type=Path, default=SEED_PATH, help="wallet seed JSON")
    parser.add_argument("--json", type=Path, help="write per-case results and the summary here")
    parser.add_argument("-v", "--verbose", action="store_true", help="print the conversation of failed cases")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Arabic on a Windows console

    seed = load_seed(args.seed)
    cases = load_cases(args.cases or sorted(CASES_DIR.glob("*.jsonl")), set(args.types or []))
    try:
        parts = load_parts(args.core)
    except MissingPart as exc:
        types = len({c["type"] for c in cases})
        print(f"eval: nothing to run. {exc}")
        print(f"Loaded {len(cases)} cases (case types: {types}) and a {len(seed['users'])}-user wallet seed; "
              "they are ready once the missing part lands. `python -m pytest -q` checks the data files.")
        return 2

    seed = with_pin_hashes(seed)
    results = [run_case(case, seed, parts) for case in cases]
    summary = summarise(results, parts.core_name)
    print_report(summary, results, args.verbose)
    if args.json:
        args.json.write_text(json.dumps({"summary": summary, "cases": results}, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 1 if summary["executed_without_tap"] or summary["audit_failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
