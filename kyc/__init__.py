"""Identity document at sign-up. A vision model reads the photo; code decides what is accepted.

    from kyc import reader_from, verify, record, level_after, limits_for

    reader = reader_from(llm)                                  # llm: the model layer, vision role
    verdict = verify(image, mime, reader=reader, typed_name=name, today=date.today())
    level = level_after(verdict)                               # "none" | "pending" | "verified"
    row = record(verdict, pepper=pepper)                       # the only thing that may be stored

See kyc/README.md for the flow, the rules, the limits per level and what is never stored.
"""

from kyc.check import ACCEPTED_TYPES, REASONS, STATUSES, TEXT_KEYS, Reading, Verdict, check, names_match
from kyc.policy import LEVELS, MAX_ATTEMPTS, POLICY, level_after, limits_for, over
from kyc.privacy import RECORD_KEYS, fingerprint, last4, masked, record
from kyc.reader import MAX_IMAGE_BYTES, MIME_TYPES, Reader, can_read, image_problem, reader_from, verify
from kyc.schema import DOCUMENT_TYPES, PROMPT, SCHEMA

__all__ = [
    "ACCEPTED_TYPES", "DOCUMENT_TYPES", "LEVELS", "MAX_ATTEMPTS", "MAX_IMAGE_BYTES", "MIME_TYPES", "POLICY", "PROMPT",
    "REASONS", "RECORD_KEYS", "Reader", "Reading", "SCHEMA", "STATUSES", "TEXT_KEYS", "Verdict", "can_read", "check",
    "fingerprint", "image_problem", "last4", "level_after", "limits_for", "masked", "names_match", "over",
    "reader_from", "record", "verify",
]
