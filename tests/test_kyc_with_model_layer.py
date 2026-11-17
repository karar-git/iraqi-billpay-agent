"""The identity step through the real model layer, offline: a mock HTTP transport plays the vision model.

Skipped when the llm package is not there. Only the interface fixed in the build plan is used:
LLM(registry, transport=..., env=...), Registry, Provider, Route and vision_json.
"""

import json
from datetime import date

import pytest

httpx = pytest.importorskip("httpx")
llm_package = pytest.importorskip("llm")

from kyc import PROMPT, can_read, reader_from, verify  # noqa: E402

TODAY = date(2026, 11, 13)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
GOOD = {
    "is_identity_document": True, "document_type": "passport", "readable": True,
    "full_name_ar": "زينب هادي كريم السعدي", "full_name_latin": "ZAINAB HADI KAREEM AL-SAADI",
    "document_number": "A12345678", "birth_date": "1995-03-04", "expiry_date": "2031-06-30", "sex": "f",
    "looks_like_photo_of_screen_or_copy": False, "marked_as_specimen": False, "notes": "",
}


def model_layer(answer, seen: list):
    """An LLM whose "vision" role is one local, keyless route answered by `answer` (a dict or an HTTP status)."""
    def handler(request):
        seen.append(json.loads(request.content))
        if isinstance(answer, int):
            return httpx.Response(answer, json={"error": "down"})
        text = json.dumps(answer, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": text}}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    registry = llm_package.Registry(
        {"local": llm_package.Provider("local", "http://127.0.0.1:9/v1", local=True)},
        {"vision": [llm_package.Route("local", "any-vision-model")]},
    )
    return llm_package.LLM(registry, transport=httpx.MockTransport(handler), sleep=lambda seconds: None, env={})


def test_a_good_reading_through_the_model_layer_is_verified():
    seen: list = []
    llm = model_layer(GOOD, seen)
    assert can_read(llm)
    verdict = verify(PNG, "image/png", reader=reader_from(llm), typed_name="zainab al saadi", today=TODAY)
    assert (verdict.status, verdict.reason) == ("verified", "ok")
    assert verdict.reading.document_number == "A12345678"
    assert len(seen) == 1
    sent = json.dumps(seen[0], ensure_ascii=False)
    assert "data:image/png;base64," in sent and PROMPT[:60] in sent


def test_an_answer_outside_the_schema_never_reaches_the_rules():
    """The model layer refuses an answer with a field missing or an extra verdict in it; the person is asked again."""
    for answer in ({k: v for k, v in GOOD.items() if k != "birth_date"}, {**GOOD, "approved": True},
                   {**GOOD, "document_type": "student_card"}):
        verdict = verify(PNG, "image/png", reader=reader_from(model_layer(answer, [])), typed_name="زينب هادي", today=TODAY)
        assert (verdict.status, verdict.reason, verdict.reading) == ("retry", "no_reading", None)


def test_a_model_that_is_down_is_a_retry_not_a_crash():
    seen: list = []
    verdict = verify(PNG, "image/png", reader=reader_from(model_layer(503, seen)), typed_name="زينب هادي", today=TODAY)
    assert (verdict.status, verdict.reason) == ("retry", "no_reading")
    assert seen  # the call was made, and failed


def test_an_image_over_the_limit_is_never_sent():
    seen: list = []
    reader = reader_from(model_layer(GOOD, seen))
    verdict = verify(PNG + b"\x00" * (8 * 1024 * 1024), "image/png", reader=reader, typed_name="زينب هادي", today=TODAY)
    assert (verdict.status, verdict.reason) == ("retry", "image_too_large")
    assert seen == []
