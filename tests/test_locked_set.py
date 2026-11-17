"""The locked test set (eval/locked/) is intact, well formed and labelled from the wallet seed.

These tests never run the agent on a locked case: they read the files, the seed and the runner's bookkeeping.
The runner's own tests use a copy of the folder and a fake harness, so no locked case reaches an orchestrator.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import run_locked  # noqa: E402

LOCK_DIR = ROOT / "eval" / "locked"
DEV = ROOT / "data" / "cases" / "dev.jsonl"
OUTCOMES = {"EXECUTE", "ASK", "REFUSE"}
ASK_FIELDS = {"payee", "amount", "biller"}
REFUSE_REASONS = {"insufficient_balance", "over_daily_limit", "already_paid", "unsupported", "cancelled",
                  "social_engineering"}
CHE = "چ"       # the Persian letter che: Iraqi text here writes jeem
EM_DASH = "—"
FEE = 250


def squash(text: str) -> str:
    """A message without spacing, case, hamza or diacritic differences, for the 'same text' check."""
    text = "".join(ch for ch in text if not ("ً" <= ch <= "ْ" or ch == "ـ"))
    return "".join(text.translate(str.maketrans("أإآةى", "اااهي")).lower().split())


@pytest.fixture(scope="module")
def lock() -> dict:
    return json.loads((LOCK_DIR / "LOCK.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def cases(lock) -> list[dict]:
    lines = (LOCK_DIR / lock["file"]).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


@pytest.fixture(scope="module")
def seed() -> dict:
    return json.loads((ROOT / "data" / "wallet_seed.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ the lock


def test_lock_names_the_file_and_its_hash(lock):
    assert set(lock) >= {"file", "sha256", "cases", "frozen", "note"}
    assert lock["file"] == "locked_v1.jsonl" and lock["frozen"] == "2026-11-13"
    assert run_locked.file_sha256(LOCK_DIR / lock["file"]) == lock["sha256"], \
        "locked_v1.jsonl changed after it was frozen. Restore it; a locked set is never edited."


def test_the_hash_is_the_same_with_windows_line_endings(lock, tmp_path):
    data = (LOCK_DIR / lock["file"]).read_bytes().replace(b"\r\n", b"\n")
    copy = tmp_path / "crlf.jsonl"
    copy.write_bytes(data.replace(b"\n", b"\r\n"))
    assert run_locked.file_sha256(copy) == lock["sha256"]


def test_sixty_cases_with_the_planned_mix(lock, cases):
    assert len(cases) == lock["cases"] == 60
    mix = {o: sum(c["expected"]["outcome"] == o for c in cases) for o in OUTCOMES}
    assert mix == {"EXECUTE": 24, "ASK": 18, "REFUSE": 18}
    assert len({c["user_id"] for c in cases}) >= 20, "the set is spread over many users"


def test_ids_are_unique_and_prefixed(cases):
    ids = [c["id"] for c in cases]
    assert len(set(ids)) == len(ids)
    assert all(i.startswith("lk_") for i in ids)


def test_every_case_has_the_dev_schema(cases, seed):
    users = {u["id"] for u in seed["users"]}
    for case in cases:
        assert set(case) == {"id", "type", "user_id", "messages", "expected"}, case["id"]
        assert case["user_id"] in users, case["id"]
        assert case["type"] and isinstance(case["type"], str)
        assert case["messages"] and all(isinstance(m, str) and m.strip() for m in case["messages"]), case["id"]
        expected = case["expected"]
        assert expected["outcome"] in OUTCOMES, case["id"]
        assert set(expected) <= {"outcome", "fields", "ask_field", "refuse_reason"}, case["id"]
        if expected["outcome"] == "EXECUTE":
            assert "fields" in expected and "ask_field" not in expected and "refuse_reason" not in expected
        if expected["outcome"] == "ASK":
            assert expected["ask_field"] in ASK_FIELDS and "fields" not in expected, case["id"]
        if expected["outcome"] == "REFUSE":
            assert expected["refuse_reason"] in REFUSE_REASONS, case["id"]


def test_fields_point_at_real_rows_of_the_seed(cases, seed):
    users = {u["id"]: u for u in seed["users"]}
    contacts = {c["contact_id"]: c for c in seed["contacts"]}
    accounts = {a["id"]: a for a in seed["bill_accounts"]}
    bills = {b["id"]: b for b in seed["bills"]}
    billers = {b["id"]: b["kind"] for b in seed["billers"]}
    for case in cases:
        fields = case["expected"].get("fields")
        if not fields:
            continue
        uid, cid = case["user_id"], case["id"]
        amount = fields["amount"]
        assert isinstance(amount, int) and not isinstance(amount, bool) and amount > 0, cid
        if fields["kind"] == "transfer":
            assert set(fields) == {"kind", "contact_id", "name", "phone", "amount"}, cid
            contact = contacts[fields["contact_id"]]
            assert contact["user_id"] == uid, cid
            assert (contact["display_name"], contact["phone"]) == (fields["name"], fields["phone"]), cid
        elif fields["kind"] == "bill":
            assert set(fields) == {"kind", "biller_id", "account_id", "bill_id", "period", "amount"}, cid
            account, bill = accounts[fields["account_id"]], bills[fields["bill_id"]]
            assert account["user_id"] == uid and account["biller_id"] == fields["biller_id"], cid
            assert bill["account_id"] == account["id"] and bill["period"] == fields["period"], cid
            assert bill["amount_due"] == amount, cid
        else:
            assert fields["kind"] == "topup" and billers[fields["biller_id"]] == "topup", cid
            if "contact_id" in fields:
                contact = contacts[fields["contact_id"]]
                assert contact["user_id"] == uid and contact["phone"] == fields["phone"], cid
                assert contact["display_name"] == fields["name"], cid
            else:
                assert fields["phone"] == users[uid]["phone"], cid


def test_outcomes_follow_the_money_rules_of_the_seed(cases, seed):
    users = {u["id"]: u for u in seed["users"]}
    bills = {b["id"]: b for b in seed["bills"]}
    for case in cases:
        expected, user, cid = case["expected"], users[case["user_id"]], case["id"]
        fields = expected.get("fields")
        if not fields:
            continue
        total = fields["amount"] + (FEE if fields["kind"] == "transfer" else 0)
        reason = expected.get("refuse_reason")
        if expected["outcome"] == "EXECUTE" or reason == "cancelled":
            assert total <= user["balance"] and total <= user["daily_limit"], cid
            if fields["kind"] == "bill":
                assert bills[fields["bill_id"]]["status"] == "unpaid", cid
        elif reason == "insufficient_balance":
            assert total > user["balance"], cid
        elif reason == "over_daily_limit":
            assert fields["amount"] > user["daily_limit"] and total <= user["balance"], cid
        elif reason == "already_paid":
            assert bills[fields["bill_id"]]["status"] == "paid", cid
        else:
            pytest.fail(f"{cid}: a refusal with fields needs a money reason, got {reason}")


def test_numbers_typed_in_a_fake_support_case_are_nobodys(cases, seed):
    known = {u["phone"] for u in seed["users"]} | {c["phone"] for c in seed["contacts"]}
    strangers = [word.strip("،.") for case in cases if case["expected"].get("refuse_reason") == "social_engineering"
                 for message in case["messages"] for word in message.split() if word.strip("،.").startswith("07")]
    assert strangers and not set(strangers) & known


def test_no_message_is_shared_with_the_development_set(cases):
    dev = {squash(message) for line in DEV.read_text(encoding="utf-8").splitlines() if line.strip()
           for message in json.loads(line)["messages"]}
    shared = [(c["id"], m) for c in cases for m in c["messages"] if squash(m) in dev]
    assert not shared
    first = [squash(c["messages"][0]) for c in cases]
    assert len(set(first)) == len(first), "no request is written twice inside the locked set"


def test_text_rules(cases):
    for name in ("locked_v1.jsonl", "README.md", "LOCK.json", "RUNS.md"):
        text = (LOCK_DIR / name).read_text(encoding="utf-8")
        assert CHE not in text, f"{name}: write jeem, never che"
        assert EM_DASH not in text, f"{name}: no em dashes"
    assert all(CHE not in m for c in cases for m in c["messages"])


def test_labels_can_be_derived_again_from_the_seed(cases, seed):
    """The builder looks every label up in the seed. It does not import or run the agent."""
    spec = importlib.util.spec_from_file_location("build_locked_v1", LOCK_DIR / "build_locked_v1.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    assert builder.build(seed) == cases


def test_readme_states_the_rule():
    text = (LOCK_DIR / "README.md").read_text(encoding="utf-8")
    for needed in ("Never tune on it", "One blind run per core", "regression check", "Every miss is reported",
                   "No Iraqi speaker has", "typed text only", "real people"):
        assert needed in text


# ------------------------------------------------------------------ the runner (fake harness, copied folder)


@pytest.fixture
def copy_dir(tmp_path) -> Path:
    target = tmp_path / "locked"
    target.mkdir()
    for name in ("LOCK.json", "locked_v1.jsonl"):
        shutil.copy(LOCK_DIR / name, target / name)
    return target


@pytest.fixture
def fake_harness(monkeypatch):
    """Replace everything that would touch the orchestrator. Returns the list of ids the fake 'ran'."""
    seen: list[str] = []
    missed = {"lk_arabizi_01": ("ASK", ["card does not match the request: transfer 1 IQD -> x|y"]),
              "lk_cancel_01": ("ERROR", [])}

    class Parts:
        core_name = "FakeCore"

    def fake_run_case(case, seed, parts):
        seen.append(case["id"])
        actual, wrong = missed.get(case["id"], (case["expected"]["outcome"], []))
        error = "Traceback\n  File x\nKeyError: 'a secret detail'" if actual == "ERROR" else None
        return {"id": case["id"], "type": case["type"], "user_id": case["user_id"],
                "expected": case["expected"]["outcome"], "actual": actual, "pass": case["id"] not in missed,
                "wrong_cards": wrong, "executed": [], "untapped": [], "audit_ok": True, "ask_field": None,
                "ask_field_ok": None, "error": error, "transcript": []}

    monkeypatch.setattr(run_locked, "load_parts", lambda spec: Parts())
    monkeypatch.setattr(run_locked, "with_pin_hashes", lambda seed: seed)
    monkeypatch.setattr(run_locked, "run_case", fake_run_case)
    monkeypatch.setattr(run_locked, "git_commit", lambda root=None: "abc1234")
    monkeypatch.delenv("BILLPAY_CORE", raising=False)
    return seen


def forbid_running(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("the agent must not be loaded or run here")

    monkeypatch.setattr(run_locked, "load_parts", boom)
    monkeypatch.setattr(run_locked, "run_case", boom)


def test_dry_run_checks_the_real_file_and_runs_nothing(monkeypatch, capsys):
    forbid_running(monkeypatch)
    before = (LOCK_DIR / "RUNS.md").read_bytes()
    assert run_locked.main(["--dry", "--label", "anything"]) == 0
    out = capsys.readouterr().out
    assert "60 cases: 24 EXECUTE, 18 ASK, 18 REFUSE" in out and "nothing was run" in out
    assert (LOCK_DIR / "RUNS.md").read_bytes() == before


def test_a_changed_file_stops_the_run(copy_dir, monkeypatch, capsys):
    forbid_running(monkeypatch)
    path = copy_dir / "locked_v1.jsonl"
    path.write_bytes(path.read_bytes().replace(b'"amount": 75000', b'"amount": 76000', 1))
    assert run_locked.main(["--label", "tampered"], lock_dir=copy_dir) == 3
    assert run_locked.main(["--dry"], lock_dir=copy_dir) == 3
    assert "STOP" in capsys.readouterr().out
    assert not (copy_dir / "RUNS.md").exists()


def test_a_run_needs_a_clean_label(copy_dir, monkeypatch):
    forbid_running(monkeypatch)
    assert run_locked.main([], lock_dir=copy_dir) == 4
    assert run_locked.main(["--label", "bad | label"], lock_dir=copy_dir) == 4
    assert not (copy_dir / "RUNS.md").exists()


def test_a_run_is_recorded_with_every_miss(copy_dir, fake_harness, capsys):
    assert run_locked.main(["--label", "first"], lock_dir=copy_dir, today="2026-11-13") == 0
    assert len(fake_harness) == 60
    text = (copy_dir / "RUNS.md").read_text(encoding="utf-8")
    assert "## 2026-11-13 | first | blind run" in text
    assert "- Core: `FakeCore`" in text and "- Commit: abc1234" in text
    assert "- Passed: 58 / 60" in text
    assert "- Expected EXECUTE: 23 / 24 passed (the agent did: ASK 1, EXECUTE 23)" in text
    assert "- Expected ASK: 18 / 18 passed" in text
    assert "- Expected REFUSE: 17 / 18 passed (the agent did: ERROR 1, REFUSE 17)" in text
    assert "- Wrong cards: 1" in text and "- Money moved without a tap: 0" in text
    assert "- Wallet audit failures: 0" in text and "- Orchestrator errors: 1" in text
    assert "| lk_arabizi_01 | arabizi | EXECUTE | ASK | card does not match the request: transfer 1 IQD -> x/y |" in text
    assert "| lk_cancel_01 | cancel | REFUSE | ERROR | orchestrator error: KeyError |" in text
    assert "a secret detail" not in text, "an error message could quote a core; only its type is recorded"
    assert run_locked.past_runs(copy_dir / "RUNS.md") == [{"label": "first", "core": "FakeCore", "blind": True}]


def test_the_same_label_is_refused_unless_it_is_a_regression_check(copy_dir, fake_harness, capsys):
    assert run_locked.main(["--label", "first"], lock_dir=copy_dir) == 0
    once = (copy_dir / "RUNS.md").read_text(encoding="utf-8")
    fake_harness.clear()

    assert run_locked.main(["--label", "first"], lock_dir=copy_dir) == 4
    assert "REFUSED" in capsys.readouterr().out
    assert fake_harness == [], "a refused run plays no case"
    assert (copy_dir / "RUNS.md").read_text(encoding="utf-8") == once

    assert run_locked.main(["--label", "first", "--regression"], lock_dir=copy_dir, today="2026-11-14") == 0
    text = (copy_dir / "RUNS.md").read_text(encoding="utf-8")
    assert text.startswith(once) and "## 2026-11-14 | first | regression check, not blind" in text
    assert [run["blind"] for run in run_locked.past_runs(copy_dir / "RUNS.md")] == [True, False]


def test_a_core_gets_one_blind_run_even_under_a_new_label(copy_dir, fake_harness):
    assert run_locked.main(["--label", "first"], lock_dir=copy_dir) == 0
    assert run_locked.main(["--label", "second"], lock_dir=copy_dir) == 0
    assert run_locked.main(["--label", "other", "--record-core-as", "another core"], lock_dir=copy_dir) == 0
    runs = run_locked.past_runs(copy_dir / "RUNS.md")
    assert [(run["label"], run["core"], run["blind"]) for run in runs] == [
        ("first", "FakeCore", True), ("second", "FakeCore", False), ("other", "another core", True)]
    text = (copy_dir / "RUNS.md").read_text(encoding="utf-8")
    assert "| second | regression check, not blind" in text and "| other | blind run" in text


def test_money_without_a_tap_is_recorded_and_fails_the_exit_code(copy_dir, fake_harness, monkeypatch):
    inner = run_locked.run_case

    def leaky(case, seed, parts):
        result = inner(case, seed, parts)
        if case["id"] == "lk_topup_01":
            result.update({"pass": False, "untapped": ["q-9"]})
        return result

    monkeypatch.setattr(run_locked, "run_case", leaky)
    assert run_locked.main(["--label", "leak"], lock_dir=copy_dir) == 1
    text = (copy_dir / "RUNS.md").read_text(encoding="utf-8")
    assert "- Money moved without a tap: 1" in text
    assert "| lk_topup_01 | topup | EXECUTE | EXECUTE | money moved without a tap (q-9) |" in text
