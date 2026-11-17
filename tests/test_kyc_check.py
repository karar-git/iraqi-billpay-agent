"""Every rule of kyc.check.check: the model's reading goes in as a plain dict, code decides."""

from datetime import date

import pytest

from kyc import REASONS, STATUSES, TEXT_KEYS, Reading, Verdict, check, names_match
from kyc.check import ACCEPTED_TYPES, age_on, normalize_number, parse_date
from kyc.schema import DOCUMENT_TYPES, PROMPT, SCHEMA

TODAY = date(2026, 11, 13)
NAME = "زينب هادي كريم السعدي"


def card(**changes) -> dict:
    """What a vision model would return for a good national card."""
    raw = {
        "is_identity_document": True,
        "document_type": "national_card",
        "readable": True,
        "full_name_ar": NAME,
        "full_name_latin": None,
        "document_number": "000123456789",
        "birth_date": "1995-03-04",
        "expiry_date": "2031-06-30",
        "sex": "f",
        "looks_like_photo_of_screen_or_copy": False,
        "marked_as_specimen": False,
        "notes": "",
    }
    raw.update(changes)
    return raw


def passport(**changes) -> dict:
    return card(**{"document_type": "passport", "document_number": "A12345678",
                   "full_name_latin": "ZAINAB HADI KAREEM AL-SAADI", **changes})


def decide(raw, typed_name="زينب هادي", today=TODAY, **options) -> Verdict:
    return check(raw, typed_name=typed_name, today=today, **options)


def outcome(raw, **kwargs) -> tuple[str, str]:
    verdict = decide(raw, **kwargs)
    return verdict.status, verdict.reason


# ---- the good card ----

def test_good_card_is_verified_with_cleaned_fields():
    verdict = decide(card())
    assert (verdict.status, verdict.reason) == ("verified", "ok")
    assert verdict.reading == Reading(
        document_type="national_card", full_name_ar=NAME, full_name_latin=None, document_number="000123456789",
        birth_date=date(1995, 3, 4), expiry_date=date(2031, 6, 30), sex="f", screen_or_copy=False, notes="")
    assert verdict.reading.name_on_document == NAME
    assert verdict.text_key == "kyc_ok"


def test_the_same_reading_always_gives_the_same_verdict():
    assert decide(card()) == decide(card())


def test_checks_are_evidence_without_personal_data():
    verdict = decide(card())
    assert dict(verdict.checks) == {
        "reading": "ok", "document": "ok", "readable": "ok", "type": "national_card", "specimen_mark": "none",
        "number": "ok", "name_printed": "ok", "birth_date": "ok", "age": "ok", "expiry": "ok", "name": "ok",
        "original": "ok"}
    flat = " ".join(f"{what} {how}" for what, how in verdict.checks)
    assert "000123456789" not in flat and "زينب" not in flat and "1995" not in flat


def test_a_model_cannot_approve_anything():
    """Extra keys in the reading are ignored: only the copied fields count, and the rules run on them."""
    raw = card(birth_date="2012-01-01", approved=True, status="verified", eligible=True, level="verified")
    assert outcome(raw) == ("refused", "under_age")


# ---- nothing usable from the reader ----

@pytest.mark.parametrize("raw", [None, "verified", [], {}, {"is_identity_document": True}])
def test_no_reading_is_a_retry(raw):
    verdict = decide(raw)
    assert (verdict.status, verdict.reason, verdict.reading) == ("retry", "no_reading", None)


@pytest.mark.parametrize("changes", [
    {"is_identity_document": "yes"}, {"readable": 1}, {"looks_like_photo_of_screen_or_copy": None},
    {"marked_as_specimen": "no"}, {"document_type": "student_card"}, {"document_number": 123456789012},
    {"birth_date": 19950304}, {"full_name_ar": ["زينب", "هادي"]},
])
def test_a_reading_of_the_wrong_shape_is_a_retry(changes):
    assert outcome(card(**changes)) == ("retry", "no_reading")


def test_a_missing_field_is_a_retry():
    raw = card()
    del raw["expiry_date"]
    assert outcome(raw) == ("retry", "no_reading")


# ---- not a document, not readable, not an accepted type ----

@pytest.mark.parametrize("changes", [{"is_identity_document": False}, {"document_type": "none"},
                                      {"is_identity_document": False, "document_type": "none", "readable": False}])
def test_not_a_document_is_a_retry(changes):
    verdict = decide(card(**changes))
    assert (verdict.status, verdict.reason, verdict.reading) == ("retry", "not_a_document", None)


def test_an_unreadable_photo_is_a_retry():
    assert outcome(card(readable=False)) == ("retry", "unreadable")


@pytest.mark.parametrize("kind", [k for k in DOCUMENT_TYPES if k not in ACCEPTED_TYPES and k != "none"])
def test_other_document_types_are_refused(kind):
    assert kind in ("driving_licence", "other")
    assert outcome(card(document_type=kind)) == ("refused", "type_not_accepted")


def test_accepted_types():
    assert ACCEPTED_TYPES == ("national_card", "passport", "residence_card")


# ---- specimens ----

def test_a_specimen_is_refused_unless_a_demo_allows_it():
    assert outcome(card(marked_as_specimen=True)) == ("refused", "specimen")
    assert outcome(card(marked_as_specimen=True), allow_specimen=True) == ("verified", "ok")
    assert dict(decide(card(marked_as_specimen=True), allow_specimen=True).checks)["specimen_mark"] == "allowed"


# ---- the number ----

@pytest.mark.parametrize("number", ["٠٠٠١٢٣٤٥٦٧٨٩", "۰۰۰۱۲۳۴۵۶۷۸۹", "0001 2345 6789", "0001-2345-6789", " 000123456789 "])
def test_eastern_digits_and_separators_are_normalised(number):
    verdict = decide(card(document_number=number))
    assert (verdict.status, verdict.reading.document_number) == ("verified", "000123456789")


@pytest.mark.parametrize("number", ["12345678901", "1234567890123", "A12345678", "00012345678X", "", None, "null"])
def test_a_national_card_number_is_12_digits(number):
    verdict = decide(card(document_number=number))
    assert (verdict.status, verdict.reason) == ("retry", "bad_number")
    assert verdict.reading.document_number is None


@pytest.mark.parametrize("number, ok", [("A12345678", True), ("a 1234 5678", True), ("A١٢٣٤٥٦٧٨", True),
                                         ("AB1234567", False), ("A1234567", False), ("123456789", False),
                                         ("000123456789", False)])
def test_a_passport_number_is_a_letter_and_8_digits(number, ok):
    verdict = decide(passport(document_number=number))
    assert (verdict.reason == "ok") is ok
    assert verdict.reading.document_number == ("A12345678" if ok else None)


@pytest.mark.parametrize("number, ok", [("RC00123456", True), ("1234567", True), ("R123456789012", True),
                                         ("123456", False), ("ABC1234567", False), ("RC-00-12-34-56", True),
                                         ("1234567890123", False)])
def test_a_residence_card_number_is_up_to_2_letters_and_7_to_12_digits(number, ok):
    raw = passport(document_type="residence_card", document_number=number)
    assert (decide(raw).reason == "ok") is ok


def test_normalize_number():
    assert normalize_number(" a-١٢٣ 45.678 ") == "A12345678"
    assert normalize_number(None) == ""


# ---- birth date and age ----

@pytest.mark.parametrize("birth", ["04-03-1995", "04/03/1995", "1995-13-01", "1995-02-30", "March 1995",
                                    "2027-01-01", "1890-01-01"])
def test_a_birth_date_that_is_not_a_plausible_date_is_a_retry(birth):
    verdict = decide(card(birth_date=birth))
    assert (verdict.status, verdict.reason) == ("retry", "bad_birth_date")
    assert verdict.reading.birth_date is None


@pytest.mark.parametrize("birth", [None, ""])
def test_a_missing_birth_date_is_a_retry_on_a_passport_and_asks_for_the_back_of_a_national_card(birth):
    assert outcome(passport(birth_date=birth)) == ("retry", "bad_birth_date")
    verdict = decide(card(birth_date=birth))
    assert (verdict.status, verdict.reason) == ("retry", "back_needed")
    assert ("birth_date", "on_the_back") in verdict.checks


def test_eastern_digits_in_dates():
    verdict = decide(card(birth_date="١٩٩٥-٠٣-٠٤", expiry_date="٢٠٣١/٠٦/٣٠"))
    assert verdict.status == "verified"
    assert (verdict.reading.birth_date, verdict.reading.expiry_date) == (date(1995, 3, 4), date(2031, 6, 30))


@pytest.mark.parametrize("birth, status, reason", [
    ("2012-05-01", "refused", "under_age"),
    ("2008-11-14", "refused", "under_age"),   # 18 tomorrow
    ("2008-11-13", "verified", "ok"),         # 18 today
    ("2008-11-12", "verified", "ok"),
])
def test_under_18_is_refused(birth, status, reason):
    assert outcome(card(birth_date=birth)) == (status, reason)


def test_age_on_a_leap_day_birthday():
    born = date(2008, 2, 29)
    assert age_on(born, date(2026, 2, 28)) == 17
    assert age_on(born, date(2026, 3, 1)) == 18
    assert outcome(card(birth_date="2008-02-29"), today=date(2026, 2, 28)) == ("refused", "under_age")


def test_parse_date_is_strict():
    assert parse_date("2001-4-3") == date(2001, 4, 3)
    assert parse_date("03-04-2001") is None and parse_date("20010403") is None and parse_date(None) is None


# ---- expiry ----

@pytest.mark.parametrize("expiry, status, reason", [
    ("2026-11-12", "refused", "expired"),
    ("2020-01-01", "refused", "expired"),
    ("2026-11-13", "verified", "ok"),      # valid through its last day
    ("2026-11-14", "verified", "ok"),
    ("soon", "retry", "bad_expiry_date"),
    ("2031-02-30", "retry", "bad_expiry_date"),
])
def test_expiry(expiry, status, reason):
    assert outcome(card(expiry_date=expiry)) == (status, reason)


def test_a_national_card_front_may_have_no_expiry_but_a_passport_must():
    verdict = decide(card(expiry_date=None))
    assert verdict.status == "verified" and dict(verdict.checks)["expiry"] == "not_printed"
    assert outcome(passport(expiry_date=None)) == ("retry", "bad_expiry_date")
    assert outcome(passport(document_type="residence_card", expiry_date=None)) == ("retry", "bad_expiry_date")


# ---- the name ----

@pytest.mark.parametrize("typed", [
    "زينب هادي", "زينب هادي كريم السعدي", "زينب السعدي", "زينب سعدي", "زينب كريم", "زَيْنَب هادي", "زينب  هادى",
    "zainab hadi", "Zaynab Kareem", "zeinab karim hadi", "السيدة زينب هادي", "zainab al sa3di",
])
def test_names_that_match(typed):
    assert names_match(typed, NAME)
    assert outcome(card(), typed_name=typed) == ("verified", "ok")


@pytest.mark.parametrize("typed", ["زينب", "هادي زينب", "سارة هادي", "زينب علي", "", "   ", "zainab", "sara hadi",
                                    "هادي كريم", "مريم السعدي"])
def test_names_that_do_not_match_go_to_a_human(typed):
    assert not names_match(typed, NAME)
    verdict = decide(card(), typed_name=typed)
    assert (verdict.status, verdict.reason) == ("review", "name_mismatch")
    assert verdict.reading is not None and dict(verdict.checks)["name"] == "differs_from_typed"


def test_name_on_a_latin_only_or_two_script_document():
    assert outcome(passport(full_name_ar=None), typed_name="زينب هادي") == ("verified", "ok")
    assert outcome(passport(full_name_ar=None), typed_name="Zainab Al-Saadi") == ("verified", "ok")
    assert outcome(passport(), typed_name="zainab al saadi") == ("verified", "ok")
    assert outcome(passport(full_name_ar=None), typed_name="Maryam Al-Saadi") == ("review", "name_mismatch")


def test_known_spellings_and_abd_names():
    assert names_match("Mohammed Ali", None, "MUHAMMAD ALI HASSAN")
    assert names_match("محمد علي", None, "MUHAMMAD ALI HASSAN")
    assert names_match("عبد الله حسن", "عبدالله حسن علي")
    assert names_match("abdullah hasan", "عبد الله حسن علي")
    assert not names_match("حسن عبد الله", "عبدالله حسن علي")


def test_no_name_on_the_document_is_a_retry():
    assert outcome(card(full_name_ar=None, full_name_latin=None)) == ("retry", "name_missing")
    assert outcome(card(full_name_ar="  ", full_name_latin="null")) == ("retry", "name_missing")


# ---- a photo of a screen or a copy ----

def test_a_photo_of_a_screen_goes_to_a_human():
    verdict = decide(card(looks_like_photo_of_screen_or_copy=True))
    assert (verdict.status, verdict.reason) == ("review", "screen_or_copy")
    assert verdict.reading.screen_or_copy is True


def test_both_review_reasons_are_in_the_evidence():
    verdict = decide(card(looks_like_photo_of_screen_or_copy=True), typed_name="سارة علي")
    assert (verdict.status, verdict.reason) == ("review", "name_mismatch")
    assert dict(verdict.checks)["original"] == "screen_or_copy"


def test_a_refusal_comes_before_a_review():
    raw = card(looks_like_photo_of_screen_or_copy=True, birth_date="2012-01-01")
    assert outcome(raw, typed_name="سارة علي") == ("refused", "under_age")
    raw = card(looks_like_photo_of_screen_or_copy=True, expiry_date="2020-01-01")
    assert outcome(raw, typed_name="سارة علي") == ("refused", "expired")


# ---- the fixed lists ----

def test_reason_codes_are_a_fixed_list():
    assert set(REASONS.values()) == set(STATUSES)
    assert set(TEXT_KEYS) == set(REASONS) and all(TEXT_KEYS[r] == f"kyc_{r}" for r in REASONS)
    assert REASONS["ok"] == "verified" and [r for r, s in REASONS.items() if s == "verified"] == ["ok"]
    assert {r for r, s in REASONS.items() if s == "review"} == {"name_mismatch", "screen_or_copy", "sides_unconfirmed"}
    assert {r for r, s in REASONS.items() if s == "refused"} == {"type_not_accepted", "specimen", "under_age", "expired"}


def test_schema_is_strict_and_the_prompt_forbids_guessing():
    assert SCHEMA["additionalProperties"] is False
    assert set(SCHEMA["required"]) == set(SCHEMA["properties"]) == set(card())
    assert SCHEMA["properties"]["document_type"]["enum"] == list(DOCUMENT_TYPES)
    assert SCHEMA["properties"]["sex"]["enum"] == ["m", "f", None]
    for field in ("full_name_ar", "full_name_latin", "document_number", "birth_date", "expiry_date"):
        assert SCHEMA["properties"][field]["type"] == ["string", "null"]
    low = PROMPT.lower()
    assert "never guess" in low and "null" in low and "only what is printed" in low and "\n" in PROMPT
