"""From a photo to raw fields: the only place of this package that calls a model.

`reader_from(llm)` wraps the model layer's vision role into a `Reader`: (image bytes, mime) -> the raw dict
of `kyc.schema.SCHEMA`, or None when nothing usable came back. A reader never decides anything; `kyc.check`
does. `verify(...)` is the whole step for a channel: the image limits, the reader, then the decision.

The image is held in memory for the one call and passed to the model layer. It is never written to disk and
never logged here, and neither is the model's answer: a failure logs the kind of error and nothing else.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date

from kyc.check import REASONS, Verdict, check
from kyc.schema import PROMPT, SCHEMA

log = logging.getLogger("kyc.reader")

Reader = Callable[[bytes, str], "dict | None"]

ROLE = "vision"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MIME_TYPES = ("image/jpeg", "image/png", "image/webp")


def clean_mime(mime: str | None) -> str:
    """'image/JPG; charset=binary' -> 'image/jpeg'."""
    kind = (mime or "").split(";")[0].strip().lower()
    return "image/jpeg" if kind in ("image/jpg", "image/pjpeg") else kind


def sniff(image: bytes) -> str | None:
    """The image type its first bytes say, whatever the sender declared."""
    if image[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if image[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image[:4] == b"RIFF" and image[8:12] == b"WEBP":
        return "image/webp"
    return None


def image_problem(image: bytes | None, mime: str | None) -> str | None:
    """Why this image is not sent to a model: "image_too_large", "image_type", or None when it may be read.
    The declared type has to be one we accept and has to be what the bytes really are."""
    if not isinstance(image, (bytes, bytearray)) or not image:
        return "image_type"
    if len(image) > MAX_IMAGE_BYTES:
        return "image_too_large"
    kind = clean_mime(mime)
    if kind not in MIME_TYPES or sniff(bytes(image[:16])) != kind:
        return "image_type"
    return None


def _model_errors() -> tuple[type[BaseException], ...]:
    """The errors of a model call that mean "no reading": the model layer's own error (imported only when a call
    is made, so this package loads without it) and the plain network ones. Anything else is a bug and is raised."""
    errors: tuple[type[BaseException], ...] = (TimeoutError, ConnectionError)
    try:
        from llm import LLMError
    except Exception:  # the model layer is not installed: a fake or another client is in use
        return errors
    return errors + (LLMError,)


def can_read(llm) -> bool:
    """True when the model layer has a usable route for the vision role."""
    try:
        return bool(llm.has(ROLE))
    except Exception:
        return False


def reader_from(llm, *, timeout_s: float | None = None) -> Reader:
    """A Reader over `llm.vision_json("vision", image, mime, PROMPT, SCHEMA)`. `llm` is the model layer's LLM, or
    anything with that one method. The reader gives None for an image that fails the limits (no call is made),
    for a failed call and for an answer that is not a JSON object."""

    def read(image: bytes, mime: str) -> dict | None:
        if image_problem(image, mime):
            return None
        try:
            answer = llm.vision_json(ROLE, bytes(image), clean_mime(mime), PROMPT, SCHEMA, timeout_s=timeout_s)
        except _model_errors() as error:
            log.warning("kyc: the vision call failed (%s)", type(error).__name__)
            return None
        return answer if isinstance(answer, dict) else None

    return read


def verify(image: bytes, mime: str, *, reader: Reader, typed_name: str, today: date,
           allow_specimen: bool = False, front: dict | None = None) -> Verdict:
    """One sign-up photo, start to finish: the image limits, the reader, then `kyc.check.check`.
    An image over the size limit or of another type gets a "retry" verdict and no model is called.
    `allow_specimen` is passed on to check(): only a demo or a test sets it.

    `front` is the raw reading of the front of a national card whose verdict was "back_needed": this image is then
    its back, and the two are decided together. A back the reader gives nothing for is "no_reading" (never the
    front decided alone again)."""
    problem = image_problem(image, mime)
    if problem:
        return Verdict(REASONS[problem], problem, None, (("image", problem),))
    answer = reader(image, mime)
    if front is None:
        return check(answer, typed_name=typed_name, today=today, allow_specimen=allow_specimen)
    if answer is None:
        return Verdict(REASONS["no_reading"], "no_reading", None, (("back", "no_reading"),))
    return check(front, typed_name=typed_name, today=today, allow_specimen=allow_specimen, back=answer)
