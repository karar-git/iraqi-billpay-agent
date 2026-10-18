"""Integrity of the synthetic wallet seed and of the test cases written against it."""

import importlib.util
import json
import re
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = ROOT / "data" / "wallet_seed.json"
CASES_DIR = ROOT / "data" / "cases"

CASE_TYPES = {
    "clear_bill", "clear_transfer", "topup", "ambiguous_biller", "two_same_name", "nickname",
    "relation_word", "dollars", "raseed_vs_flous", "two_in_one", "low_balance", "over_limit",
    "unknown_payee", "already_paid", "cancel", "arabizi", "eastern_digits", "unsupported", "pressure",
}
OUTCOMES = {"EXECUTE", "ASK", "REFUSE"}
ASK_FIELDS = {"payee", "amount", "biller"}
REFUSE_REASONS = {"insufficient_balance", "over_daily_limit", "already_paid", "unsupported", "cancelled"}
FEES = {"transfer": 250, "bill": 0, "topup": 0}
OPERATOR_PREFIXES = {"zain": ("078", "079"), "asiacell": ("077",), "korek": ("075",)}
TITLE_WORDS = {"ابو", "ام", "حجي", "حجيه", "عمو", "خالو", "خاله", "عمه", "ست", "استاذ", "دكتور", "دكتوره"}
EASTERN_DIGITS = set("٠١٢٣٤٥٦٧٨٩")
DOLLAR_WORDS = ("ورقة", "ورقتين", "دفتر", "دولار", "$")


def norm(text: str) -> str:
    """Just enough Arabic normalisation for the checks below (hamza forms, ta marbuta, diacritics)."""
    text = re.sub(r"[\u064B-\u0652\u0640]", "", text)
    return text.translate(str.maketrans("أإآةى", "اااهي")).strip()


def words(text: str) -> set[str]:
    """Normalised words of a message, also without a leading ل (لأحمد -> احمد)."""
    tokens = set(re.split(r"[\s،,!؟?]+", norm(text))) - {""}
    return tokens | {t[1:] for t in tokens if t.startswith("ل") and len(t) > 3}


@pytest.fixture(scope="module")
def seed() -> dict:
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def cases() -> list[dict]:
    rows = []
    for path in sorted(CASES_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


@pytest.fixture(scope="module")
def book(seed) -> dict:
    """Lookup tables over the seed."""
    return {
        "users": {u["id"]: u for u in seed["users"]},
        "contacts": {c["contact_id"]: c for c in seed["contacts"]},
        "billers": {b["id"]: b for b in seed["billers"]},
        "accounts": {a["id"]: a for a in seed["bill_accounts"]},
        "bills": {b["id"]: b for b in seed["bills"]},
    }


def contacts_of(seed: dict, uid: str) -> list[dict]:
    return [c for c in seed["contacts"] if c["user_id"] == uid]


def requested(case: dict) -> list[dict]:
    expected = case["expected"]
    return ([expected["fields"]] if "fields" in expected else []) + expected.get("next_fields", [])


# ------------------------------------------------------------------ seed


def test_seed_has_every_table(seed):
    assert set(seed) == {"users", "contacts", "billers", "bill_accounts", "bills", "transactions"}
    assert len(seed["users"]) == 30
    assert 350 <= len(seed["contacts"]) <= 450
    assert len(seed["billers"]) == 8
    assert {b["kind"] for b in seed["billers"]} == {"electricity", "generator", "internet", "water", "topup"}


def test_seed_ids_are_unique_and_references_resolve(seed, book):
    for table, key in [("users", "id"), ("contacts", "contact_id"), ("billers", "id"),
                       ("bill_accounts", "id"), ("bills", "id"), ("transactions", "id")]:
        ids = [row[key] for row in seed[table]]
        assert len(ids) == len(set(ids)), f"duplicate id in {table}"
    phones = [u["phone"] for u in seed["users"]] + [c["phone"] for c in seed["contacts"]]
    assert len(phones) == len(set(phones))
    assert len({t["quote_id"] for t in seed["transactions"]}) == len(seed["transactions"])
    assert all(c["user_id"] in book["users"] for c in seed["contacts"])
    assert all(a["user_id"] in book["users"] and a["biller_id"] in book["billers"] for a in seed["bill_accounts"])
    assert all(b["account_id"] in book["accounts"] for b in seed["bills"])
    assert all(t["user_id"] in book["users"] for t in seed["transactions"])


def test_seed_values_are_well_formed(seed):
    for user in seed["users"]:
        assert user["gender"] in {"f", "m"}
        assert re.fullmatch(r"\d{4}", user["pin"])
        assert isinstance(user["balance"], int) and user["balance"] >= 0
        assert isinstance(user["daily_limit"], int) and user["daily_limit"] > 0
    for row in seed["users"] + seed["contacts"]:
        assert re.fullmatch(r"07[5789]\d{8}", row["phone"])
    for bill in seed["bills"]:
        assert re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", bill["period"])
        assert bill["status"] in {"paid", "unpaid"}
        assert isinstance(bill["amount_due"], int) and bill["amount_due"] > 0
    for txn in seed["transactions"]:
        assert txn["kind"] in FEES and txn["fee"] == FEES[txn["kind"]]
        assert isinstance(txn["amount"], int) and txn["amount"] > 0
        assert isinstance(json.loads(txn["target_json"]), dict)
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", txn["created_at"])


def test_every_paid_bill_has_a_payment_in_the_history(seed):
    paid_in_history = {json.loads(t["target_json"]).get("bill_id") for t in seed["transactions"] if t["kind"] == "bill"}
    assert {b["id"] for b in seed["bills"] if b["status"] == "paid"} == paid_in_history


def test_seed_contains_the_hard_situations(seed):
    by_user = Counter((c["user_id"], c["display_name"]) for c in seed["contacts"])
    assert any(n >= 2 for n in by_user.values()), "no user has two contacts saved under the same name"
    spellings = {(c["user_id"], c["display_name"]) for c in seed["contacts"]}
    assert any((uid, "احمد") in spellings for uid, name in spellings if name == "أحمد")
    assert any(c["alias"] for c in seed["contacts"])
    internet = Counter(a["user_id"] for a in seed["bill_accounts"] if a["biller_id"].startswith("net_"))
    assert any(n >= 2 for n in internet.values())
    assert any(b["status"] == "paid" and b["period"] == "2026-10" for b in seed["bills"])
    assert any(u["balance"] < 50_000 for u in seed["users"])
    assert any(u["balance"] > 2 * u["daily_limit"] for u in seed["users"])


def test_seed_file_matches_the_generator():
    spec = importlib.util.spec_from_file_location("generate_wallet", ROOT / "data" / "generate_wallet.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.build_seed() == json.loads(SEED_PATH.read_text(encoding="utf-8")), (
        "data/wallet_seed.json is stale: run python data/generate_wallet.py"
    )


def test_no_che_letter_anywhere():
    che = "\u0686"  # Persian che: Iraqi text in this repo always uses ج instead
    files = [SEED_PATH, *CASES_DIR.glob("*.jsonl"), *CASES_DIR.glob("*.md")]
    assert all(che not in path.read_text(encoding="utf-8") for path in files), "write ج, never the Persian che"


# ------------------------------------------------------------------ cases: schema


def test_cases_follow_the_schema(cases, book):
    assert len(cases) >= 90
    assert len({c["id"] for c in cases}) == len(cases), "duplicate case id"
    for case in cases:
        assert set(case) == {"id", "type", "user_id", "messages", "expected"}, case["id"]
        assert case["type"] in CASE_TYPES, case["id"]
        assert case["user_id"] in book["users"], case["id"]
        assert case["messages"] and all(isinstance(m, str) and m.strip() for m in case["messages"]), case["id"]
        expected = case["expected"]
        assert expected["outcome"] in OUTCOMES, case["id"]
        assert set(expected) <= {"outcome", "fields", "next_fields", "ask_field", "refuse_reason"}, case["id"]
        if "ask_field" in expected:
            assert expected["outcome"] == "ASK" and expected["ask_field"] in ASK_FIELDS, case["id"]
        if "refuse_reason" in expected:
            assert expected["outcome"] == "REFUSE" and expected["refuse_reason"] in REFUSE_REASONS, case["id"]
        if expected["outcome"] == "EXECUTE":
            assert "fields" in expected, case["id"]


def test_every_type_has_at_least_four_cases(cases):
    counts = Counter(c["type"] for c in cases)
    assert {t: counts[t] for t in CASE_TYPES if counts[t] < 4} == {}


# ------------------------------------------------------------------ cases: labels are true for the seed


def test_expected_fields_exist_in_the_seed(cases, book):
    for case in cases:
        uid, reason = case["user_id"], case["expected"].get("refuse_reason")
        for fields in requested(case):
            assert isinstance(fields["amount"], int) and fields["amount"] > 0, case["id"]
            if fields["kind"] == "transfer":
                contact = book["contacts"][fields["contact_id"]]
                assert contact["user_id"] == uid, case["id"]
                assert (contact["phone"], contact["display_name"]) == (fields["phone"], fields["name"]), case["id"]
            elif fields["kind"] == "bill":
                bill = book["bills"][fields["bill_id"]]
                account = book["accounts"][bill["account_id"]]
                assert account["user_id"] == uid and account["id"] == fields["account_id"], case["id"]
                assert account["biller_id"] == fields["biller_id"], case["id"]
                assert bill["amount_due"] == fields["amount"], case["id"]
                assert bill["status"] == ("paid" if reason == "already_paid" else "unpaid"), case["id"]
            else:
                assert fields["kind"] == "topup", case["id"]
                assert book["billers"][fields["biller_id"]]["kind"] == "topup", case["id"]
                owner = book["contacts"][fields["contact_id"]] if "contact_id" in fields else book["users"][uid]
                assert owner.get("user_id", uid) == uid and owner["phone"] == fields["phone"], case["id"]
                assert fields["phone"].startswith(OPERATOR_PREFIXES[fields["biller_id"]]), case["id"]


def test_executable_cases_fit_balance_and_daily_limit(cases, book):
    for case in cases:
        if case["expected"]["outcome"] != "EXECUTE":
            continue
        user = book["users"][case["user_id"]]
        payments = requested(case)
        assert sum(p["amount"] + FEES[p["kind"]] for p in payments) <= user["balance"], case["id"]
        assert sum(p["amount"] for p in payments) <= user["daily_limit"], case["id"]


def test_balance_refusals_really_break_a_rule(cases, book):
    for case in cases:
        reason = case["expected"].get("refuse_reason")
        if reason not in {"insufficient_balance", "over_daily_limit"}:
            continue
        user, fields = book["users"][case["user_id"]], case["expected"]["fields"]
        total = fields["amount"] + FEES[fields["kind"]]
        if reason == "insufficient_balance":
            assert total > user["balance"], case["id"]
        else:
            assert fields["amount"] > user["daily_limit"] and total <= user["balance"], case["id"]


def test_name_cases_match_the_contact_book(cases, seed):
    for case in cases:
        contacts = contacts_of(seed, case["user_id"])
        outcome, first = case["expected"]["outcome"], case["messages"][0]
        if case["type"] == "two_same_name" and outcome == "ASK":
            first_words = Counter(norm(c["display_name"]).split()[0] for c in contacts)
            assert any(n >= 2 and w in words(first) for w, n in first_words.items()), case["id"]
        if case["type"] == "relation_word":
            if outcome == "EXECUTE" and len(case["messages"]) == 1:  # resolved through the saved alias
                alias = next(c["alias"] for c in contacts if c["contact_id"] == case["expected"]["fields"]["contact_id"])
                assert alias and norm(alias) in words(first), case["id"]
            else:  # no alias saved: the agent must ask (the user may answer in a second message)
                assert not any(c["alias"] for c in contacts), case["id"]
        if case["type"] == "unknown_payee":
            names = {w for c in contacts for w in norm(c["display_name"]).split() if w not in TITLE_WORDS}
            assert not names & words(first), case["id"]


def one_user_seed(seed: dict, uid: str) -> dict:
    """The slice of the seed one user needs, so a fresh wallet per case stays cheap."""
    accounts = [a for a in seed["bill_accounts"] if a["user_id"] == uid]
    account_ids = {a["id"] for a in accounts}
    return {
        "users": [u for u in seed["users"] if u["id"] == uid],
        "contacts": contacts_of(seed, uid),
        "billers": seed["billers"],
        "bill_accounts": accounts,
        "bills": [b for b in seed["bills"] if b["account_id"] in account_ids],
        "transactions": [t for t in seed["transactions"] if t["user_id"] == uid],
    }


def test_labels_replay_on_the_mock_wallet(cases, seed):
    """EXECUTE payments really succeed; balance, limit and already-paid refusals really fail."""
    api = pytest.importorskip("wallet.api")
    from fastapi import HTTPException
    from wallet.db import Database

    def pay(wallet, uid: str, pin: str, fields: dict) -> str:
        target = {"transfer": {"contact_id": fields.get("contact_id")}, "bill": {"bill_id": fields.get("bill_id")},
                  "topup": {"biller_id": fields.get("biller_id"), "phone": fields.get("phone")}}[fields["kind"]]
        try:
            quote = wallet.create_quote(uid, fields["kind"], target, fields["amount"])
        except HTTPException as exc:
            return exc.detail["error"]
        done = wallet.execute(quote["id"], pin, quote["id"])
        return "succeeded" if done["state"] == "SUCCEEDED" else done["result"]["reason"]

    for case in cases:
        uid, expected = case["user_id"], case["expected"]
        reason = expected.get("refuse_reason")
        if expected["outcome"] == "EXECUTE":
            payments, want = requested(case), "succeeded"
        elif reason in {"insufficient_balance", "over_daily_limit", "already_paid"}:
            payments, want = [expected["fields"]], reason
        else:
            continue
        wallet = api.Wallet(Database(":memory:", seed=one_user_seed(seed, uid)))
        pin = next(u["pin"] for u in seed["users"] if u["id"] == uid)
        for fields in payments:  # two_in_one: both payments, one after the other
            assert pay(wallet, uid, pin, fields) == want, case["id"]


def test_style_cases_have_their_style(cases, seed):
    for case in cases:
        text = " ".join(case["messages"])
        if case["type"] == "arabizi":
            assert text.isascii(), case["id"]
        if case["type"] == "eastern_digits":
            assert EASTERN_DIGITS & set(text), case["id"]
        if case["type"] == "dollars":
            assert any(w in case["messages"][0] for w in DOLLAR_WORDS), case["id"]
        if case["type"] == "cancel":
            assert len(case["messages"]) >= 2 and case["expected"]["refuse_reason"] == "cancelled", case["id"]
        if case["type"] == "ambiguous_biller":
            kinds = Counter(a["biller_id"] for a in seed["bill_accounts"] if a["user_id"] == case["user_id"])
            if "مولدة" in text:
                assert kinds["generator"] >= 2, case["id"]
            elif "نت" in text:
                assert kinds["net_rafidain"] + kinds["net_sama"] >= 2, case["id"]
            else:
                assert sum(kinds.values()) >= 2, case["id"]
