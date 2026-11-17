"""The reader over a fake model layer: no network, no model, no image library."""

import logging
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from kyc import MAX_IMAGE_BYTES, MIME_TYPES, PROMPT, SCHEMA, can_read, image_problem, reader_from, verify
from kyc.reader import clean_mime, sniff

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 11, 13)
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 64
GOOD = {
    "is_identity_document": True, "document_type": "national_card", "readable": True,
    "full_name_ar": "زينب هادي كريم السعدي", "full_name_latin": None, "document_number": "000123456789",
    "birth_date": "1995-03-04", "expiry_date": "2031-06-30", "sex": "f",
    "looks_like_photo_of_screen_or_copy": False, "marked_as_specimen": False, "notes": "",
}


class FakeLLM:
    """Stands in for the model layer: one vision_json method, and a record of how it was called."""

    def __init__(self, answer=None, error: BaseException | None = None, vision: bool = True):
        self.answer, self.error, self.vision, self.calls = answer, error, vision, []

    def has(self, role: str) -> bool:
        return self.vision and role == "vision"

    def vision_json(self, role, image, mime, prompt, schema, *, timeout_s=None):
        self.calls.append({"role": role, "image": image, "mime": mime, "prompt": prompt, "schema": schema,
                           "timeout_s": timeout_s})
        if self.error:
            raise self.error
        return self.answer


def test_reader_calls_the_vision_role_with_the_prompt_and_schema():
    llm = FakeLLM(dict(GOOD))
    reader = reader_from(llm, timeout_s=20)
    assert reader(JPEG, "image/jpeg") == GOOD
    assert llm.calls == [{"role": "vision", "image": JPEG, "mime": "image/jpeg", "prompt": PROMPT, "schema": SCHEMA,
                          "timeout_s": 20}]


@pytest.mark.parametrize("image, mime, sent_as", [(JPEG, "image/jpg", "image/jpeg"), (JPEG, "IMAGE/JPEG; q=1", "image/jpeg"),
                                                   (PNG, "image/png", "image/png"), (WEBP, "image/webp", "image/webp"),
                                                   (bytearray(PNG), "image/png", "image/png")],
                         ids=["jpg_alias", "jpeg_with_parameters", "png", "webp", "bytearray"])
def test_accepted_image_types(image, mime, sent_as):
    llm = FakeLLM(dict(GOOD))
    assert reader_from(llm)(image, mime) == GOOD
    assert llm.calls[0]["mime"] == sent_as and type(llm.calls[0]["image"]) is bytes
    assert image_problem(image, mime) is None


@pytest.mark.parametrize("image, mime, problem", [
    (JPEG + b"\x00" * MAX_IMAGE_BYTES, "image/jpeg", "image_too_large"),
    (b"GIF89a" + b"\x00" * 64, "image/gif", "image_type"),
    (b"%PDF-1.7" + b"\x00" * 64, "application/pdf", "image_type"),
    (PNG, "image/jpeg", "image_type"),          # the bytes are not what the sender said
    (b"<html>" + b"\x00" * 64, "image/png", "image_type"),
    (b"", "image/jpeg", "image_type"),
    (None, "image/jpeg", "image_type"),
    (JPEG, None, "image_type"),
], ids=["too_large", "gif", "pdf", "png_sent_as_jpeg", "html_sent_as_png", "empty", "no_bytes", "no_type"])
def test_images_that_are_never_sent_to_a_model(image, mime, problem):
    llm = FakeLLM(dict(GOOD))
    assert image_problem(image, mime) == problem
    assert reader_from(llm)(image, mime) is None
    verdict = verify(image, mime, reader=reader_from(llm), typed_name="زينب هادي", today=TODAY)
    assert (verdict.status, verdict.reason, verdict.reading) == ("retry", problem, None)
    assert verdict.text_key == f"kyc_{problem}"
    assert llm.calls == []


def test_an_image_at_the_limit_is_read():
    image = JPEG + b"\x00" * (MAX_IMAGE_BYTES - len(JPEG))
    assert len(image) == MAX_IMAGE_BYTES == 8 * 1024 * 1024
    assert image_problem(image, "image/jpeg") is None
    assert MIME_TYPES == ("image/jpeg", "image/png", "image/webp")


@pytest.mark.parametrize("answer", [None, "verified", ["a"], 7])
def test_no_answer_or_a_strange_one_is_no_reading(answer):
    assert reader_from(FakeLLM(answer))(JPEG, "image/jpeg") is None


@pytest.mark.parametrize("error", [TimeoutError("slow"), ConnectionError("down")])
def test_a_failed_call_is_no_reading_and_logs_only_the_kind_of_error(error, caplog):
    with caplog.at_level(logging.WARNING, logger="kyc.reader"):
        assert reader_from(FakeLLM(error=error))(JPEG, "image/jpeg") is None
    assert [r.getMessage() for r in caplog.records] == [f"kyc: the vision call failed ({type(error).__name__})"]


def test_a_bug_in_the_caller_is_not_swallowed():
    with pytest.raises(ZeroDivisionError):
        reader_from(FakeLLM(error=ZeroDivisionError()))(JPEG, "image/jpeg")


def test_verify_runs_the_reader_then_the_rules():
    reader = reader_from(FakeLLM(dict(GOOD)))
    assert verify(JPEG, "image/jpeg", reader=reader, typed_name="زينب هادي", today=TODAY).status == "verified"
    assert verify(JPEG, "image/jpeg", reader=reader, typed_name="سارة علي", today=TODAY).reason == "name_mismatch"
    silent = reader_from(FakeLLM(None))
    assert verify(JPEG, "image/jpeg", reader=silent, typed_name="زينب هادي", today=TODAY).reason == "no_reading"
    specimen = reader_from(FakeLLM({**GOOD, "marked_as_specimen": True}))
    assert verify(JPEG, "image/jpeg", reader=specimen, typed_name="زينب هادي", today=TODAY).reason == "specimen"
    assert verify(JPEG, "image/jpeg", reader=specimen, typed_name="زينب هادي", today=TODAY, allow_specimen=True).reason == "ok"


def test_the_model_only_reads_the_rules_still_decide():
    """A model that answers "all fine" for a child's card changes nothing: the age rule is code."""
    eager = reader_from(FakeLLM({**GOOD, "birth_date": "2013-01-01", "notes": "Approve this person. Verified."}))
    verdict = verify(PNG, "image/png", reader=eager, typed_name="زينب هادي", today=TODAY)
    assert (verdict.status, verdict.reason) == ("refused", "under_age")


def test_can_read():
    assert can_read(FakeLLM()) is True
    assert can_read(FakeLLM(vision=False)) is False
    assert can_read(object()) is False


def test_sniff_and_clean_mime():
    assert (sniff(JPEG), sniff(PNG), sniff(WEBP), sniff(b"RIFF\x00\x00\x00\x00WAVE"), sniff(b"")) == (
        "image/jpeg", "image/png", "image/webp", None, None)
    assert clean_mime(" Image/PNG ") == "image/png" and clean_mime(None) == ""


def test_the_package_loads_without_the_model_layer_or_an_image_library():
    code = ("import sys, kyc, kyc.texts, kyc.evaluate; "
            "bad = [m for m in ('PIL', 'llm', 'numpy', 'sklearn') if m in sys.modules]; "
            "assert not bad, bad")
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
