"""Score a reader on the specimen images of data/kyc/: which fields it copied right, and whether the code's
verdict on its reading is the expected one.

    python -m kyc.evaluate            # the vision route of the model layer, from the environment

Running it calls the configured vision model once per image (15 calls): with a hosted model that costs what
the route costs, with a local one nothing. The tests never run this against a model; they call `score()` with
a fake reader. Use it to find the smallest vision model that reads every specimen right.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

from kyc.check import check, normalize_number, parse_date
from kyc.reader import Reader, can_read, reader_from
from payments import names

SPECIMEN_DIR = Path(__file__).resolve().parents[1] / "data" / "kyc"
FIELDS = ("is_identity_document", "document_type", "full_name_ar", "full_name_latin", "document_number", "birth_date",
          "expiry_date", "sex", "looks_like_photo_of_screen_or_copy", "marked_as_specimen")


def load_specimens(folder: Path | None = None) -> dict:
    return json.loads(((folder or SPECIMEN_DIR) / "specimens.json").read_text(encoding="utf-8"))


def _comparable(field: str, value):
    if value is None or isinstance(value, bool):
        return value
    if field == "full_name_ar":
        return names.normalize(value)
    if field == "full_name_latin":
        return re.sub(r"[^a-z]+", " ", value.lower()).strip()
    if field == "document_number":
        return normalize_number(value)
    if field in ("birth_date", "expiry_date"):
        return parse_date(value)
    return value


def score(reader: Reader, folder: Path | None = None) -> dict:
    """Run `reader` over every specimen. Fields are compared after the same cleaning the check uses; a field of
    an image that is not a document counts only for is_identity_document."""
    folder = folder or SPECIMEN_DIR
    book = load_specimens(folder)
    today = date.fromisoformat(book["reference_date"])
    fields = {field: [0, 0] for field in FIELDS}
    verdicts_right, misses = 0, []
    for spec in book["specimens"]:
        truth = spec["truth"]
        raw = reader((folder / spec["file"]).read_bytes(), spec["mime"])
        verdict = check(raw, typed_name=spec["typed_name"], today=today, allow_specimen=book["allow_specimen"])
        got = {"status": verdict.status, "reason": verdict.reason}
        wrong = []
        for field in FIELDS if truth["is_identity_document"] else FIELDS[:1]:
            fields[field][1] += 1
            read = raw.get(field) if isinstance(raw, dict) else None
            if _comparable(field, read) == _comparable(field, truth[field]):
                fields[field][0] += 1
            else:
                wrong.append(field)
        verdicts_right += got == spec["expected"]
        if wrong or got != spec["expected"]:
            misses.append({"file": spec["file"], "expected": spec["expected"], "got": got, "wrong_fields": wrong})
    return {"images": len(book["specimens"]), "verdicts_right": verdicts_right, "fields_right": fields, "misses": misses}


def main() -> int:
    try:
        from llm import LLM, Registry
    except ImportError:
        print("The model layer (the llm package) is not available, so there is no reader to score.")
        return 2
    llm = LLM(Registry.from_env())
    if not can_read(llm):
        print("No usable route for the vision role. Set LLM_VISION (see docs/MODELS.md) and run again.")
        return 2
    result = score(reader_from(llm))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["verdicts_right"] == result["images"] and not result["misses"] else 1


if __name__ == "__main__":
    sys.exit(main())
