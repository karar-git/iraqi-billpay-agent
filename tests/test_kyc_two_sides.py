"""The two sides of a national card, decided in code.

The unified national card prints the birth date and the expiry on its back, so a photo of the front alone can carry
no birth date. check() then answers "back_needed" (no try is lost), and check(front, back=...) decides the two
readings as one card: the front gives the type, the name and the number, the back the dates the front lacks, and
both must be the same card. Every name and number is made up.
"""

from __future__ import annotations

from datetime import date

import pytest

from kyc import REASONS, check, verify
from kyc.check import NUMBER_FORMATS

TODAY = date(2026, 11, 13)
NUMBER = "199012345678"
JPEG = b"\xff\xd8\xff\xe0" + b"photo" * 10


def side(**changes) -> dict:
    """A reading of a national card in kyc.schema.SCHEMA's shape."""
    base = {"is_identity_document": True, "document_type": "national_card", "readable": True,
            "full_name_ar": "زينب هادي كريم السعدي", "full_name_latin": None, "document_number": NUMBER,
            "birth_date": None, "expiry_date": None, "sex": "f",
            "looks_like_photo_of_screen_or_copy": False, "marked_as_specimen": False, "notes": ""}
    return {**base, **changes}


FRONT = side()
# The back: the dates, the Latin name of the machine-readable lines, and in those lines the same 12-digit national
# number as the front (kyc.schema.PROMPT asks for that one, never the card's serial): the tie between the two photos.
BACK = side(full_name_ar=None, full_name_latin="ZAINAB HADI", document_number=NUMBER,
            birth_date="1995-04-12", expiry_date="2031-01-01", sex=None)
# A back whose national number was not read: only the card's serial, in another format.
SERIAL_ONLY = {**BACK, "document_number": "A12345678"}


def decide(front=FRONT, back=None, **options):
    return check(front, typed_name="زينب هادي", today=TODAY, back=back, **options)


def outcome(front=FRONT, back=None, **options) -> tuple[str, str]:
    verdict = decide(front, back, **options)
    return verdict.status, verdict.reason


def test_a_front_without_a_birth_date_asks_for_the_back_and_is_not_a_failed_try():
    verdict = decide()
    assert (verdict.status, verdict.reason) == ("retry", "back_needed")
    assert REASONS["back_needed"] == "retry" and verdict.text_key == "kyc_back_needed"
    assert verdict.reading.document_number == NUMBER  # the front itself passed every rule it can carry


def test_the_two_sides_together_are_one_verified_card():
    verdict = decide(back=BACK)
    assert (verdict.status, verdict.reason) == ("verified", "ok")
    reading = verdict.reading
    assert (reading.birth_date, reading.expiry_date) == (date(1995, 4, 12), date(2031, 1, 1))
    assert reading.document_number == NUMBER and reading.full_name_ar == "زينب هادي كريم السعدي"
    assert reading.full_name_latin is None  # the back's names are never used: the name is the front's
    assert ("back", "ok") in verdict.checks and ("expiry", "ok") in verdict.checks


def test_the_back_is_not_asked_for_the_rules_the_front_already_failed():
    assert outcome(side(document_number="12")) == ("retry", "bad_number")
    assert outcome(side(full_name_ar=None)) == ("retry", "name_missing")
    assert outcome(side(marked_as_specimen=True)) == ("refused", "specimen")
    assert outcome(side(readable=False)) == ("retry", "unreadable")
    assert outcome(side(birth_date="1995-04-12")) == ("verified", "ok")  # a front with the birth date: one photo
    assert outcome(side(birth_date="12/04/1995")) == ("retry", "bad_birth_date")  # printed but not clear: a retry


def test_a_passport_or_a_residence_card_never_asks_for_a_back():
    passport = side(document_type="passport", document_number="A12345678", expiry_date="2031-01-01")
    assert outcome(passport) == ("retry", "bad_birth_date")


@pytest.mark.parametrize("back, reason", [
    (side(document_number="200198765432", birth_date="1995-04-12", expiry_date="2031-01-01"), "sides_differ"),
    (side(document_type="passport", birth_date="1995-04-12", expiry_date="2031-01-01"), "sides_differ"),
    (side(is_identity_document=False, document_type="none"), "not_a_document"),
    ({"approved": True}, "no_reading"),
    ("not a dict", "no_reading"),
])
def test_a_back_that_is_another_card_or_no_card_is_a_retry(back, reason):
    verdict = decide(back=back)
    assert (verdict.status, verdict.reason) == ("retry", reason)
    assert verdict.reading is None and ("back", reason) in verdict.checks


def test_a_back_verifies_only_when_it_carries_the_fronts_number():
    assert NUMBER_FORMATS["national_card"].fullmatch("A12345678") is None
    assert outcome(back=BACK) == ("verified", "ok")
    assert outcome(back={**BACK, "document_number": "١٩٩٠١٢٣٤٥٦٧٨"}) == ("verified", "ok")  # the same, Eastern digits


@pytest.mark.parametrize("back", [
    SERIAL_ONLY,  # only the card's serial was read
    {**BACK, "document_number": None},  # no number at all
    {**BACK, "document_number": None, "full_name_latin": "AHMED JASIM MOHAMMED"},  # another person's back
    {**BACK, "document_number": None, "full_name_latin": "ZAINAB HADI KAREEM"},  # a name is not a tie
])
def test_a_back_with_no_tie_to_the_front_goes_to_a_person_who_cannot_approve_it(back):
    """The back's birth date is what the age rule reads: from a photo with no tie to the card it never verifies."""
    verdict = decide(back=back)
    assert (verdict.status, verdict.reason) == ("review", "sides_unconfirmed")
    assert ("back", "unconfirmed") in verdict.checks


def test_a_minors_front_and_an_adults_back_never_verify_an_adult():
    """The reviewed hole: a front with no birth date, plus the back of someone born in 1970."""
    adult_back = {**SERIAL_ONLY, "full_name_latin": "AHMED JASIM", "birth_date": "1970-01-01"}
    assert outcome(back=adult_back) == ("review", "sides_unconfirmed")
    assert outcome(back={**adult_back, "document_number": None}) == ("review", "sides_unconfirmed")
    assert outcome(back={**adult_back, "document_number": "200198765432"}) == ("retry", "sides_differ")


@pytest.mark.parametrize("back, reason", [
    ({**BACK, "document_type": "driving_licence"}, "sides_differ"),  # a licence prints a birth date too
    ({**BACK, "document_type": "other"}, "sides_differ"),
    ({**BACK, "document_type": "other", "readable": False, "document_number": None}, "sides_differ"),
    ({**BACK, "readable": False}, "unreadable"),  # its dates are not trusted either
])
def test_a_back_of_another_type_or_unreadable_is_a_retry(back, reason):
    verdict = decide(back=back)
    assert (verdict.status, verdict.reason) == ("retry", reason) and verdict.reading is None


def test_an_untied_back_can_still_be_refused_but_never_verified():
    assert outcome(back={**SERIAL_ONLY, "expiry_date": "2025-01-01"}) == ("refused", "expired")
    assert outcome(back={**SERIAL_ONLY, "birth_date": "2012-05-01"}) == ("refused", "under_age")
    assert outcome(side(full_name_ar="سارة محمد علي الجبوري"), back=SERIAL_ONLY) == ("review", "sides_unconfirmed")


@pytest.mark.parametrize("back, result", [
    ({**BACK, "birth_date": None}, ("retry", "bad_birth_date")),  # never "back_needed" twice
    ({**BACK, "expiry_date": None}, ("retry", "bad_expiry_date")),  # the back prints it: it must be read
    ({**BACK, "birth_date": "2012-05-01"}, ("refused", "under_age")),
    ({**BACK, "expiry_date": "2025-01-01"}, ("refused", "expired")),
    ({**BACK, "marked_as_specimen": True}, ("refused", "specimen")),
    ({**BACK, "looks_like_photo_of_screen_or_copy": True}, ("review", "screen_or_copy")),
])
def test_the_rules_hold_on_what_the_back_brings(back, result):
    assert outcome(back=back) == result


def test_a_specimen_back_passes_only_where_the_demo_switch_is_on():
    assert outcome(back={**BACK, "marked_as_specimen": True}, allow_specimen=True) == ("verified", "ok")


def test_a_date_the_front_prints_wins_over_the_back():
    verdict = decide(side(birth_date="1990-01-01"), back=BACK)
    assert verdict.reading.birth_date == date(1990, 1, 1) and verdict.reading.expiry_date == date(2031, 1, 1)


def test_the_name_rule_stays_on_the_front():
    """The back's Latin line reads like the typed name, the front is someone else's: a person looks."""
    assert outcome(side(full_name_ar="سارة محمد علي الجبوري"), back=BACK) == ("review", "name_mismatch")


def test_verify_reads_the_back_once_and_never_decides_the_front_alone_again():
    calls = []

    def reader(image, mime):
        calls.append((image, mime))
        return BACK

    verdict = verify(JPEG, "image/jpeg", reader=reader, typed_name="زينب هادي", today=TODAY, front=FRONT)
    assert (verdict.status, verdict.reason) == ("verified", "ok") and calls == [(JPEG, "image/jpeg")]
    nothing = verify(JPEG, "image/jpeg", reader=lambda image, mime: None, typed_name="زينب هادي", today=TODAY,
                     front=FRONT)
    assert (nothing.status, nothing.reason) == ("retry", "no_reading")  # not "back_needed" again
    calls.clear()
    wrong = verify(b"%PDF-1.7", "application/pdf", reader=reader, typed_name="زينب هادي", today=TODAY, front=FRONT)
    assert (wrong.status, wrong.reason) == ("retry", "image_type") and calls == []
