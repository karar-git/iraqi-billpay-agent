"""The limits table per verification level, and the only record that may be stored."""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from kyc import (LEVELS, MAX_ATTEMPTS, POLICY, RECORD_KEYS, REASONS, Verdict, check, fingerprint, last4, level_after,
                 limits_for, masked, over, record)

TODAY = date(2026, 11, 13)
PEPPER = b"test-pepper-not-a-secret"
NUMBER = "000123456789"


def card(**changes) -> dict:
    raw = {
        "is_identity_document": True, "document_type": "national_card", "readable": True,
        "full_name_ar": "زينب هادي كريم السعدي", "full_name_latin": None, "document_number": NUMBER,
        "birth_date": "1995-03-04", "expiry_date": "2031-06-30", "sex": "f",
        "looks_like_photo_of_screen_or_copy": False, "marked_as_specimen": False, "notes": "",
    }
    raw.update(changes)
    return raw


def decide(raw, typed_name="زينب هادي") -> Verdict:
    return check(raw, typed_name=typed_name, today=TODAY)


# ---- policy ----

def test_policy_is_one_table_of_integer_dinars():
    assert tuple(POLICY) == LEVELS == ("none", "pending", "verified")
    for limits in POLICY.values():
        assert set(limits) == {"daily_limit", "single_max"}
        assert all(type(v) is int and v > 0 for v in limits.values())
        assert limits["single_max"] <= limits["daily_limit"]


def test_unverified_levels_are_small_and_verified_is_larger():
    assert POLICY["pending"] == POLICY["none"]  # waiting for a human opens nothing
    assert POLICY["none"]["daily_limit"] < POLICY["verified"]["daily_limit"]
    assert POLICY["none"]["single_max"] < POLICY["verified"]["single_max"]


def test_limits_for():
    assert limits_for("verified") == POLICY["verified"]
    assert limits_for("nobody-knows") == limits_for(None) == POLICY["none"]
    limits_for("none")["daily_limit"] = 10**12  # a copy: the table cannot be changed through it
    assert POLICY["none"]["daily_limit"] < 10**12


@pytest.mark.parametrize("status, level", [("verified", "verified"), ("review", "pending"), ("retry", "none"),
                                            ("refused", "none"), ("approved", "none"), ("", "none")])
def test_level_after(status, level):
    assert level_after(Verdict(status, "ok", None)) == level


def test_level_after_real_verdicts():
    assert level_after(decide(card())) == "verified"
    assert level_after(decide(card(), typed_name="سارة علي")) == "pending"
    assert level_after(decide(card(looks_like_photo_of_screen_or_copy=True))) == "pending"
    assert level_after(decide(card(birth_date="2012-01-01"))) == "none"
    assert level_after(decide(None)) == "none"


def test_every_reason_leads_to_a_known_level():
    for reason, status in REASONS.items():
        assert level_after(Verdict(status, reason, None)) in LEVELS


def test_over():
    single, daily = POLICY["none"]["single_max"], POLICY["none"]["daily_limit"]
    assert over("none", single) is None
    assert over("none", single + 1) == "kyc_single_max"
    assert over("none", single, spent_today=daily - single) is None
    assert over("none", single, spent_today=daily - single + 1) == "kyc_daily_limit"
    assert over("pending", single + 1) == "kyc_single_max"
    assert over("verified", single + 1) is None
    assert over("verified", POLICY["verified"]["single_max"] + 1) == "kyc_single_max"


def test_attempts_are_capped():
    assert type(MAX_ATTEMPTS) is int and 1 <= MAX_ATTEMPTS <= 5


# ---- privacy ----

def test_masked_shows_the_last_4_only():
    assert masked(NUMBER) == "••••6789"
    assert masked("A12345678") == "••••5678"
    assert masked("٠٠٠١ ٢٣٤٥ ٦٧٨٩") == "••••6789"
    assert last4(NUMBER) == "6789"
    assert masked("1234") == masked("") == masked(None) == "••••"  # a short number shows nothing
    assert last4("1234567") is None


def test_fingerprint_is_keyed_and_stable():
    mark = fingerprint(NUMBER, PEPPER)
    assert len(mark) == 64 and int(mark, 16) >= 0
    assert mark == fingerprint("٠٠٠١ ٢٣٤٥-٦٧٨٩", PEPPER) == fingerprint(f" {NUMBER} ", PEPPER)
    assert fingerprint("a12345678", PEPPER) == fingerprint("A 1234 5678", PEPPER)
    assert mark != fingerprint(NUMBER, b"another-pepper-16-bytes") and mark != fingerprint("000123456780", PEPPER)
    assert NUMBER not in mark


@pytest.mark.parametrize("pepper", [b"", b"short", "a-text-pepper-of-enough-length", None])
def test_fingerprint_needs_a_real_pepper(pepper):
    with pytest.raises(ValueError):
        fingerprint(NUMBER, pepper)
    with pytest.raises(ValueError):
        record(decide(None), pepper=pepper)


def test_fingerprint_needs_a_number():
    with pytest.raises(ValueError):
        fingerprint(" - ", PEPPER)


def test_record_is_exactly_the_allowed_fields():
    when = datetime(2026, 11, 13, 9, 30, 15, tzinfo=timezone(timedelta(hours=3)))
    row = record(decide(card()), pepper=PEPPER, checked_at=when)
    assert tuple(row) == RECORD_KEYS
    assert row == {
        "level": "verified", "document_type": "national_card", "name_on_document": "زينب هادي كريم السعدي",
        "number_last4": "6789", "number_fingerprint": fingerprint(NUMBER, PEPPER), "birth_year": 1995,
        "checked_at": "2026-11-13T06:30:15+00:00", "reason": "ok",
    }


@pytest.mark.parametrize("changes, typed", [({}, "زينب هادي"), ({}, "سارة علي"), ({"birth_date": "2012-01-01"}, "زينب هادي"),
                                             ({"expiry_date": "2020-02-03"}, "زينب هادي"),
                                             ({"looks_like_photo_of_screen_or_copy": True}, "زينب هادي")])
def test_record_never_holds_the_full_number_or_the_full_dates(changes, typed):
    raw = card(**changes)
    stored = json.dumps(record(decide(raw, typed), pepper=PEPPER), ensure_ascii=False)
    assert NUMBER not in stored and NUMBER[:8] not in stored
    assert raw["birth_date"] not in stored and raw["expiry_date"] not in stored
    assert "sex" not in stored and "notes" not in stored


def test_record_of_a_verdict_without_a_reading_or_a_number():
    row = record(decide(None), pepper=PEPPER)
    assert tuple(row) == RECORD_KEYS
    assert (row["level"], row["reason"]) == ("none", "no_reading")
    assert all(row[key] is None for key in RECORD_KEYS if key not in ("level", "reason", "checked_at"))
    assert datetime.fromisoformat(row["checked_at"]).tzinfo is not None

    row = record(decide(card(document_number="12345")), pepper=PEPPER)
    assert (row["reason"], row["number_last4"], row["number_fingerprint"]) == ("bad_number", None, None)
    assert "12345" not in json.dumps(row)


def test_two_accounts_with_the_same_document_share_a_fingerprint():
    first = record(decide(card()), pepper=PEPPER)
    second = record(decide(card(document_number="٠٠٠١٢٣٤٥٦٧٨٩", full_name_ar="زينب هادي كريم السعدي")), pepper=PEPPER)
    other = record(decide(card(document_number="000123456788")), pepper=PEPPER)
    assert first["number_fingerprint"] == second["number_fingerprint"] != other["number_fingerprint"]
