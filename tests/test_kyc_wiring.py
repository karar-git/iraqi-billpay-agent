"""The identity check wired into storage and rules: the schema migration (from the version before, with data),
what the account service writes and never writes, the level's limits, the admin's decision, the single-payment
cap as a hard stop of payments.rules, and the words around the step in three languages. No network, no channel:
the WhatsApp flow itself is in channels/whatsapp/tests/test_kyc_signup.py."""

from __future__ import annotations

import json
import sqlite3
import string
from datetime import date, datetime, timedelta, timezone

import pytest

import kyc
from accounts import texts as T
from kyc import texts as kyc_texts
from accounts.service import KYC_COLUMNS, Accounts
from payments import rules
from payments.rules import PastPayment, PaymentRequest, WalletView, check
from wallet import migrations
from wallet.api import Wallet
from wallet.db import Database, hash_account_pin

A = "9647700000001"
B = "9647700000002"
PEPPER = b"test-kyc-pepper-0123456789"  # a test value, not a real secret
NUMBER = "199012345678"
PRINTED = "زينب هادي كريم السعدي"
TODAY = date(2026, 11, 2)


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now


def raw(**changes) -> dict:
    base = {"is_identity_document": True, "document_type": "national_card", "readable": True,
            "full_name_ar": PRINTED, "full_name_latin": None, "document_number": NUMBER,
            "birth_date": "1995-04-12", "expiry_date": "2031-01-01", "sex": "f",
            "looks_like_photo_of_screen_or_copy": False, "marked_as_specimen": False, "notes": ""}
    return {**base, **changes}


def record(clock: Clock, **changes) -> dict:
    verdict = kyc.check(raw(**changes), typed_name="زينب هادي", today=TODAY)
    return kyc.record(verdict, pepper=PEPPER, checked_at=clock())


@pytest.fixture
def world(tmp_path):
    clock = Clock()
    db = Database(tmp_path / "accounts.db")
    wallet = Wallet(db, clock=clock)
    accounts = Accounts(db, wallet, clock=clock)
    yield db, wallet, accounts, clock
    db.close()


def open_account(wallet: Wallet, phone: str = A, name: str = "زينب هادي", daily_limit: int = 700_000) -> str:
    return wallet.open_account(phone, name, gender="f", language="iq", pin_hash=hash_account_pin("2583"),
                               daily_limit=daily_limit)["user"]["id"]


def audit(accounts: Accounts, action: str) -> list[dict]:
    return accounts.audit_rows(action=action, limit=100)


# ---------------------------------------------------------------- the migration


def test_a_database_of_the_version_before_migrates_with_its_accounts_and_a_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "accounts.db"
    with monkeypatch.context() as patched:  # the schema as it was before the identity check
        patched.setattr(migrations, "MIGRATIONS", [step for step in migrations.MIGRATIONS if step[0] <= 4])
        old = Database(path)
        wallet = Wallet(old)
        uid = open_account(wallet)
        wallet.admin_credit(uid, 250_000, "opening", op_id="op-1", actor="admin:test", notify=False)
        with old.connect() as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
            columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
            assert not any(column.startswith("kyc_") for column in columns)
            assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'kyc_signups'").fetchone() is None
        old.close()
    assert migrations.LATEST == 6 and migrations.MIGRATIONS[-1][0] == 6  # v6 (voice approval) runs after v5
    db = Database(path)
    wallet = Wallet(db)
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST
        user = dict(conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone())
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert set(KYC_COLUMNS.values()) <= set(user)
    assert all(user[column] is None for column in KYC_COLUMNS.values())  # no level: an account from before
    assert user["balance"] == 250_000 and user["daily_limit"] == 700_000 and user["phone_e164"] == A
    assert "kyc_signups" in tables and {"idx_users_kyc_fingerprint", "idx_kyc_signups_fingerprint"} <= indexes
    assert wallet.audit()["ok"]
    [snapshot] = list((tmp_path / "backups").glob("accounts-pre-v5-*.db"))  # taken before the first change
    with sqlite3.connect(snapshot) as copy:
        assert copy.execute("PRAGMA user_version").fetchone()[0] == 4
        assert copy.execute("SELECT balance FROM users WHERE id = ?", (uid,)).fetchone()[0] == 250_000
        assert not any(row[1].startswith("kyc_") for row in copy.execute("PRAGMA table_info(users)"))
    copy.close()
    accounts = Accounts(db, wallet)
    assert accounts.kyc(uid) is None and accounts.kyc_limits(uid) is None  # limits untouched, in code too
    db.close()
    again = Database(path)  # a second open changes nothing and takes no second snapshot
    with again.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST
        assert conn.execute("SELECT balance, daily_limit FROM users").fetchone()[:2] == (250_000, 700_000)
    again.close()
    assert len(list((tmp_path / "backups").glob("accounts-pre-v5-*.db"))) == 1


def test_the_migration_is_safe_to_run_twice_and_the_seed_has_no_level():
    from wallet.api import DEFAULT_SEED_PATH

    db = Database(":memory:", seed=DEFAULT_SEED_PATH)
    with db.connect() as conn:
        migrations._v5(conn)  # every statement is IF NOT EXISTS or a column check
        assert conn.execute("SELECT COUNT(*) FROM users WHERE kyc_level IS NOT NULL").fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO kyc_signups (phone_e164, level, created_at) VALUES ('x', 'gold', 'now')")
    assert Wallet(db).audit()["ok"]
    db.close()


# ---------------------------------------------------------------- what is stored


def test_applying_a_record_sets_the_level_the_limit_and_one_audit_line_without_personal_data(world):
    db, wallet, accounts, clock = world
    uid = open_account(wallet)
    limits = accounts.kyc_apply(uid, record(clock))
    assert limits == {"daily_limit": 1_000_000, "single_max": 1_000_000}
    user = accounts.get(uid)
    assert user["kyc_level"] == "verified" and user["daily_limit"] == 1_000_000
    assert user["kyc_document_type"] == "national_card" and user["kyc_name"] == PRINTED
    assert user["kyc_number_last4"] == "5678" and user["kyc_birth_year"] == 1995 and user["kyc_reason"] == "ok"
    assert user["kyc_number_fingerprint"] == kyc.fingerprint(NUMBER, PEPPER) and len(user["kyc_number_fingerprint"]) == 64
    assert user["kyc_checked_at"] == "2026-11-02T06:00:00Z"  # written as the database writes every time
    shown = accounts.kyc(uid)
    assert shown == {"level": "verified", "document_type": "national_card", "name": PRINTED, "number_last4": "5678",
                     "birth_year": 1995, "checked_at": "2026-11-02T06:00:00Z", "reason": "ok",
                     "daily_limit": 1_000_000, "single_max": 1_000_000}
    assert "fingerprint" not in " ".join(shown)  # what is shown never includes the fingerprint
    [line] = audit(accounts, "kyc_level_set")
    details = json.loads(line["details_json"])
    assert details == {"from": None, "to": "verified", "reason": "ok", "document_type": "national_card",
                       "daily_limit_from": 700_000, "daily_limit": 1_000_000, "single_max": 1_000_000}
    assert line["user_id"] == uid and line["actor"] == "system"
    with db.connect() as conn:  # nothing anywhere holds the whole number or the whole birth date
        dump = "\n".join(str(tuple(row)) for table in ("users", "audit_log", "outbox", "kyc_signups")
                         for row in conn.execute(f"SELECT * FROM {table}"))
    assert NUMBER not in dump and "1995-04-12" not in dump and "2031-01-01" not in dump


@pytest.mark.parametrize("level, daily", [("none", 150_000), ("pending", 150_000), ("verified", 1_000_000)])
def test_each_level_sets_its_daily_limit(world, level, daily):
    _, wallet, accounts, clock = world
    uid = open_account(wallet)
    changes = {"none": {"readable": False}, "pending": {"looks_like_photo_of_screen_or_copy": True}, "verified": {}}
    made = record(clock, **changes[level])
    assert made["level"] == level
    assert accounts.kyc_apply(uid, made) == kyc.limits_for(level)
    assert accounts.get(uid)["daily_limit"] == daily == wallet.user_profile(uid)["daily_limit"]
    assert accounts.kyc_limits(uid) == {"daily_limit": daily, "single_max": kyc.limits_for(level)["single_max"]}


def test_only_the_eight_fields_of_a_record_are_written_whatever_else_is_handed_in(world):
    db, wallet, accounts, clock = world
    uid = open_account(wallet)
    extra = {**record(clock), "document_number": NUMBER, "image": b"\xff\xd8\xff photo bytes", "birth_date": "1995-04-12",
             "daily_limit": 9_000_000, "approved": True}
    assert accounts.kyc_apply(uid, extra)["daily_limit"] == 1_000_000  # the limit is the level's, not the dict's
    accounts.kyc_hold(B, extra)
    with db.connect() as conn:
        dump = "\n".join(str(tuple(row)) for table in ("users", "audit_log", "kyc_signups")
                         for row in conn.execute(f"SELECT * FROM {table}"))
    assert NUMBER not in dump and "photo bytes" not in dump and "1995-04-12" not in dump and "9000000" not in dump
    with pytest.raises(ValueError):
        accounts.kyc_apply(uid, {**record(clock), "level": "gold"})
    with pytest.raises(ValueError):
        accounts.kyc_hold(B, {"level": None})
    with pytest.raises(KeyError):
        accounts.kyc_apply("nobody", record(clock))
    assert accounts.get(uid)["kyc_level"] == "verified"


def test_a_record_is_held_for_an_open_sign_up_then_moved_or_dropped_or_swept(world):
    _, wallet, accounts, clock = world
    assert accounts.kyc_held(A) is None and accounts.kyc_held(None) is None
    made = record(clock, looks_like_photo_of_screen_or_copy=True)
    accounts.kyc_hold(A, made)
    held = accounts.kyc_held(A)
    assert set(held) == set(kyc.RECORD_KEYS)
    assert {**held, "checked_at": None} == {**made, "checked_at": None} and held["checked_at"] == "2026-11-02T06:00:00Z"
    accounts.kyc_hold(A, record(clock))  # a later photo of the same sign-up replaces it
    assert accounts.kyc_held(A)["level"] == "verified"
    uid = open_account(wallet)
    accounts.kyc_apply(uid, accounts.kyc_held(A), drop_phone=A)  # one transaction: onto the account, out of the hold
    assert accounts.kyc_held(A) is None and accounts.get(uid)["kyc_level"] == "verified"
    accounts.kyc_hold(B, made)
    accounts.kyc_drop(B)
    accounts.kyc_drop(None)
    assert accounts.kyc_held(B) is None
    accounts.kyc_hold(B, made)
    clock.now += timedelta(hours=23)
    assert accounts.kyc_sweep() == 0 and accounts.kyc_held(B) is not None
    clock.now += timedelta(hours=2)
    assert accounts.kyc_sweep() == 1 and accounts.kyc_held(B) is None


def test_the_same_document_is_found_on_other_accounts_and_open_sign_ups_only(world):
    _, wallet, accounts, clock = world
    made = record(clock)
    fingerprint = made["number_fingerprint"]
    assert accounts.kyc_same_document(fingerprint) == [] and accounts.kyc_same_document(None) == []
    first = open_account(wallet, A)
    accounts.kyc_apply(first, made)
    assert accounts.kyc_same_document(fingerprint, uid=first) == []  # an account is not its own duplicate
    assert accounts.kyc_same_document(fingerprint, phone=A) == []
    assert accounts.kyc_same_document(fingerprint, phone=B) == [{"user_id": first, "phone": A}]
    accounts.kyc_hold(B, made)
    assert accounts.kyc_same_document(fingerprint, phone=B) == [{"user_id": first, "phone": A}]
    assert accounts.kyc_same_document(fingerprint, uid=first) == [{"user_id": None, "phone": B}]
    other = record(clock, document_number="200198765432")
    assert accounts.kyc_same_document(other["number_fingerprint"], phone=B) == []
    wallet.set_status(first, "closed", actor="admin:test", reason="left")
    accounts.kyc_drop(B)
    assert accounts.kyc_same_document(fingerprint, phone=B) == []  # a closed account holds no document


def test_the_admins_decision_changes_the_level_the_limit_the_audit_and_leaves_a_notice(world):
    _, wallet, accounts, clock = world
    uid = open_account(wallet)
    assert accounts.kyc_decide(uid, True, actor="admin:x", reason="ok") is False  # no level: nothing to decide
    accounts.kyc_apply(uid, record(clock, looks_like_photo_of_screen_or_copy=True))
    [request] = accounts.open_requests()
    assert request == {"user_id": uid, "at": "2026-11-02T06:00:00Z", "name": "زينب هادي", "phone": A,
                       "kind": "kyc_review", "reason": "screen_or_copy", "document_type": "national_card",
                       "approvable": True}
    assert accounts.overview()["requests"] == 1
    assert accounts.kyc_decide(uid, False, actor="admin:x", reason="not the original") is True
    user = accounts.get(uid)
    assert (user["kyc_level"], user["kyc_reason"], user["daily_limit"]) == ("none", "rejected", 150_000)
    assert user["kyc_number_fingerprint"]  # kept: the document can be recognised again
    # what the rejected document showed goes: its printed name, last 4 and birth year (it may be someone else's)
    assert (user["kyc_name"], user["kyc_number_last4"], user["kyc_birth_year"]) == (None, None, None)
    assert accounts.kyc_decide(uid, True, actor="admin:x", reason="again") is False  # not waiting any more
    [notice] = accounts.pending_notices(uid)
    assert notice["kind"] == "kyc_rejected"
    assert json.loads(notice["payload_json"]) == {"reason": "not the original", "daily_limit": 150_000, "single_max": 50_000}
    [line] = audit(accounts, "kyc_rejected")
    assert line["actor"] == "admin:x" and json.loads(line["details_json"])["reason"] == "not the original"
    assert accounts.open_requests() == []
    accounts.kyc_apply(uid, record(clock, looks_like_photo_of_screen_or_copy=True))
    clock.now += timedelta(minutes=5)
    assert accounts.kyc_decide(uid, True, actor="admin:y", reason="seen in person") is True
    user = accounts.get(uid)
    assert (user["kyc_level"], user["kyc_reason"], user["daily_limit"]) == ("verified", "approved", 1_000_000)
    assert [n["kind"] for n in accounts.pending_notices(uid)] == ["kyc_rejected", "kyc_approved"]
    assert json.loads(audit(accounts, "kyc_approved")[0]["details_json"])["daily_limit"] == 1_000_000


def test_a_closed_account_is_not_in_the_review_queue(world):
    _, wallet, accounts, clock = world
    uid = open_account(wallet)
    accounts.kyc_apply(uid, record(clock, looks_like_photo_of_screen_or_copy=True))
    wallet.set_status(uid, "closed", actor="admin:test", reason="left")
    assert accounts.open_requests() == []


def test_each_photo_read_is_one_audit_line_and_they_are_counted_per_sender_and_per_day(world):
    _, wallet, accounts, clock = world
    uid = open_account(wallet)
    verdict = kyc.check(raw(birth_date="2012-05-01"), typed_name="زينب هادي", today=TODAY)
    accounts.kyc_note_check(uid=None, ref="sender-key-1", status=verdict.status, reason=verdict.reason, attempt=1,
                            checks=verdict.checks, where="signup")
    accounts.kyc_note_check(uid=uid, ref="sender-key-2", status="retry", reason="unreadable", attempt=2, where="verify")
    [second, first] = audit(accounts, "kyc_checked")
    assert json.loads(first["details_json"]) == {
        "status": "refused", "reason": "under_age", "attempt": 1, "where": "signup",
        "checks": {"reading": "ok", "document": "ok", "readable": "ok", "type": "national_card",
                   "specimen_mark": "none", "number": "ok", "name_printed": "ok", "birth_date": "ok", "age": "under_18"}}
    assert first["user_id"] is None and first["ref"] == "sender-key-1" and second["user_id"] == uid
    for personal in (NUMBER, "5678", PRINTED, "زينب", "2012", A):
        assert personal not in first["details_json"] + second["details_json"]
    since = clock() - timedelta(hours=24)
    assert accounts.kyc_checks_since(since, ref="sender-key-1") == 1
    assert accounts.kyc_checks_since(since, uid=uid) == 1
    assert accounts.kyc_checks_since(since, uid=uid, ref="sender-key-1") == 2
    assert accounts.kyc_checks_since(since, ref="someone-else") == 0 and accounts.kyc_checks_since(since) == 0
    clock.now += timedelta(hours=25)
    assert accounts.kyc_checks_since(clock() - timedelta(hours=24), uid=uid, ref="sender-key-1") == 0


# ---------------------------------------------------------------- the single-payment cap in the rules


def view(balance=500_000, limit=1_000_000, spent=0, single_max=None):
    return WalletView(balance=balance, daily_limit=limit, spent_today=spent, single_max=single_max,
                      history=[PastPayment("c1", 40_000, minutes_ago=60 * 24 * 7)])


def test_no_cap_unless_the_account_has_a_level():
    assert check(PaymentRequest("transfer", 400_000, "c1"), view()).ok
    assert WalletView(balance=1, daily_limit=1).single_max is None


@pytest.mark.parametrize("kind", ["transfer", "bill", "topup"])
def test_one_payment_over_the_cap_is_a_hard_stop_whatever_its_kind(kind):
    cap = kyc.limits_for("none")["single_max"]
    request = PaymentRequest(kind, cap + 1, "c1")
    assert check(request, view(single_max=cap)).hard_stops == [rules.OVER_SINGLE_MAX]
    assert check(PaymentRequest(kind, cap, "c1"), view(single_max=cap)).ok  # exactly the cap passes


def test_the_cap_comes_first_among_the_stops_and_never_hides_them():
    result = check(PaymentRequest("transfer", 600_000, "c1"), view(balance=100_000, limit=500_000, single_max=50_000))
    assert result.hard_stops == [rules.OVER_SINGLE_MAX, rules.INSUFFICIENT_BALANCE, rules.OVER_DAILY_LIMIT]
    assert not result.ok
    small = check(PaymentRequest("transfer", 40_000, "c1"), view(limit=150_000, spent=120_000, single_max=50_000))
    assert small.hard_stops == [rules.OVER_DAILY_LIMIT]  # under the cap, over the level's day
    assert check(PaymentRequest("transfer", 0, "c1"), view(single_max=50_000)).hard_stops == [rules.NON_POSITIVE_AMOUNT]


def test_the_policy_and_the_rules_agree_on_every_level():
    for level in kyc.LEVELS:
        limits = kyc.limits_for(level)
        window = view(balance=10**9, limit=limits["daily_limit"], single_max=limits["single_max"])
        assert check(PaymentRequest("transfer", limits["single_max"], "c1"), window).ok
        over = check(PaymentRequest("transfer", limits["single_max"] + 1, "c1"), window).hard_stops
        assert rules.OVER_SINGLE_MAX in over
        assert kyc.over(level, limits["single_max"] + 1) == "kyc_single_max"


# ---------------------------------------------------------------- the words


def _fields(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def test_every_text_of_the_step_exists_in_three_languages_with_the_same_slots():
    keys = set(T.KYC_IQ) - set(T.MANAGER_KEYS)
    assert keys == set(T.KYC_MSA) == set(T.KYC_EN) and len(keys) > 20
    for key in sorted(keys):
        shapes = []
        for table in (T.KYC_IQ, T.KYC_MSA, T.KYC_EN):
            entry = table[key]
            forms = list(entry.values()) if isinstance(entry, dict) else [entry]
            assert all(forms), key
            shapes.append({frozenset(_fields(form)) for form in forms})
        assert shapes[0] == shapes[1] == shapes[2] and len(shapes[0]) == 1, key
    for lang in ("iq", "msa", "en"):
        assert T.BY_LANG[lang]["kyc_ask"] is {"iq": T.KYC_IQ, "msa": T.KYC_MSA, "en": T.KYC_EN}[lang]["kyc_ask"]


def test_the_texts_follow_the_house_rules_and_the_buttons_fit():
    for table in (T.KYC_IQ, T.KYC_MSA, T.KYC_EN):
        for key, entry in table.items():
            for form in (entry.values() if isinstance(entry, dict) else [entry]):
                assert not set(form) & {chr(0x0686), chr(0x2014), chr(0x2013)}, key  # che, em dash, en dash
    iraqi = " ".join(str(value) for value in T.KYC_IQ.values())
    assert "حسابج" in iraqi and "باجر" in iraqi  # ج where others would write the Persian letter
    for lang in ("iq", "msa", "en"):
        for key in ("btn_kyc_later", "btn_verify"):
            assert 0 < len(T.say(key, "f", lang)) <= 20, (lang, key)


def test_the_notice_is_honest_in_each_language():
    for lang, pilot, ledger, once, kept, stored in (
            ("iq", "تجريبية", "رصيد داخل الخدمة بس", "مرة وحدة", "وما تنحفظ", ("نوع الوثيقة", "الاسم", "آخر 4 أرقام", "سنة الميلاد")),
            ("msa", "تجريبية", "رصيد داخل الخدمة فقط", "مرة واحدة", "ولا تُحفظ", ("نوع الوثيقة", "الاسم", "آخر 4 أرقام", "سنة الميلاد")),
            ("en", "pilot", "mock ledger", "read once", "is not kept", ("document type", "the name", "last 4 digits", "birth year"))):
        notice = T.say("kyc_notice", "f", lang)
        assert pilot in notice and ledger in notice and once in notice and kept in notice
        assert all(word in notice for word in stored), lang
        ask = T.say("kyc_ask", "f", lang)
        assert {"iq": kyc_texts.ACCEPTED_IQ, "msa": kyc_texts.ACCEPTED_MSA, "en": kyc_texts.ACCEPTED_EN}[lang] in ask


def test_the_orchestrator_has_a_line_for_the_single_cap_check_in_each_language():
    from payments.messages import CATALOGS, render

    for lang in ("iq", "msa", "en"):
        for key in ("why_single_ok", "why_single_over"):
            assert _fields(CATALOGS[lang][key]) == {"limit", "amount"}
        line = render("why_single_over", "f", lang, limit=50_000, amount=60_000)
        assert "50,000" in line and "60,000" in line
        refusal = kyc_texts.render("kyc_single_max", "f", lang, single_max=50_000)
        assert "50,000" in refusal
