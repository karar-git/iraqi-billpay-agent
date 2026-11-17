"""The specimen images of data/kyc/: the files on disk, their ground truth, and the verdict the code gives.

No image library is needed here: the files are checked by size, hash and first bytes, and the generator's
plan (names, numbers, expected verdicts) is rebuilt without drawing anything.
"""

import hashlib
import importlib.util
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from kyc import SCHEMA, check, image_problem
from kyc.check import NUMBER_FORMATS
from kyc.evaluate import FIELDS, load_specimens, score
from kyc.reader import sniff

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "data" / "kyc"
BOOK = json.loads((FOLDER / "specimens.json").read_text(encoding="utf-8"))
SPECIMENS = BOOK["specimens"]
TODAY = date.fromisoformat(BOOK["reference_date"])
MAX_FILE_BYTES = 200 * 1024


@pytest.fixture(scope="module")
def generator():
    spec = importlib.util.spec_from_file_location("kyc_generate_ids", FOLDER / "generate_ids.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # drawing imports Pillow inside its functions only, so this needs none
    return module


def test_the_set_has_ten_documents_and_five_negatives():
    assert len(SPECIMENS) == 15 and len({s["file"] for s in SPECIMENS}) == 15
    groups = Counter(s["file"].split("_")[0] for s in SPECIMENS)
    assert groups == {"national": 7, "passport": 2, "residence": 1, "negative": 5}
    assert Counter(s["kind"] for s in SPECIMENS if not s["file"].startswith("negative")) == {
        "national_card": 7, "passport": 2, "residence_card": 1}
    assert {s["file"] for s in SPECIMENS if s["file"].startswith("negative")} == {
        "negative_expired_card.png", "negative_underage_card.png", "negative_screen_photo.jpg",
        "negative_receipt.png", "negative_wall.jpg"}
    assert BOOK["allow_specimen"] is True and load_specimens() == BOOK


@pytest.mark.parametrize("spec", SPECIMENS, ids=lambda s: s["file"])
def test_each_file_on_disk_is_the_one_described(spec):
    data = (FOLDER / spec["file"]).read_bytes()
    assert len(data) == spec["bytes"] <= MAX_FILE_BYTES
    assert hashlib.sha256(data).hexdigest() == spec["sha256"]
    assert sniff(data) == spec["mime"] and image_problem(data, spec["mime"]) is None
    assert spec["mime"] == ("image/jpeg" if spec["file"].endswith(".jpg") else "image/png")


def test_no_image_in_the_folder_is_missing_from_the_list():
    on_disk = {p.name for p in FOLDER.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")}
    assert on_disk == {s["file"] for s in SPECIMENS}


@pytest.mark.parametrize("spec", SPECIMENS, ids=lambda s: s["file"])
def test_the_truth_has_the_shape_of_the_schema(spec):
    truth = spec["truth"]
    assert list(truth) == SCHEMA["required"]
    for field, rule in SCHEMA["properties"].items():
        kinds = rule["type"] if isinstance(rule["type"], list) else [rule["type"]]
        value = truth[field]
        assert (value is None and "null" in kinds) or (isinstance(value, bool) and "boolean" in kinds) or (
            isinstance(value, str) and "string" in kinds), field
        if "enum" in rule:
            assert value in rule["enum"]


@pytest.mark.parametrize("spec", SPECIMENS, ids=lambda s: s["file"])
def test_the_code_gives_the_expected_verdict_on_a_perfect_reading(spec):
    verdict = check(spec["truth"], typed_name=spec["typed_name"], today=TODAY, allow_specimen=True)
    assert {"status": verdict.status, "reason": verdict.reason} == spec["expected"]


def test_every_verdict_kind_is_in_the_set():
    assert Counter(s["expected"]["reason"] for s in SPECIMENS) == {
        "ok": 8, "name_mismatch": 2, "expired": 1, "under_age": 1, "screen_or_copy": 1, "not_a_document": 2}
    assert {s["expected"]["status"] for s in SPECIMENS} == {"verified", "review", "refused", "retry"}


def test_outside_a_demo_every_specimen_document_is_refused():
    for spec in SPECIMENS:
        verdict = check(spec["truth"], typed_name=spec["typed_name"], today=TODAY)
        if spec["truth"]["is_identity_document"]:
            assert spec["truth"]["marked_as_specimen"] is True
            assert (verdict.status, verdict.reason) == ("refused", "specimen"), spec["file"]
        else:
            assert (verdict.status, verdict.reason) == ("retry", "not_a_document")


def test_numbers_are_in_format_and_marked_by_zeros():
    for spec in SPECIMENS:
        number = spec["truth"]["document_number"]
        if number is None:
            continue
        assert NUMBER_FORMATS[spec["kind"]].fullmatch(number), spec["file"]
        assert re.search(r"^[A-Z]{0,2}00", number), "a specimen number starts with a block of zeros"
    numbers = [s["truth"]["document_number"] for s in SPECIMENS if s["truth"]["document_number"]]
    assert len(set(numbers)) == len(numbers)


def test_the_generator_plans_exactly_what_is_in_the_file(generator):
    planned = generator.build_specs()
    stored = [{k: v for k, v in s.items() if k not in ("bytes", "sha256")} for s in SPECIMENS]
    assert planned == stored
    assert (generator.SEED, generator.REFERENCE_DATE) == (BOOK["seed"], BOOK["reference_date"])
    assert generator.build_specs() == planned  # a fixed seed: the same plan every time


def test_names_come_from_the_lists_in_the_script(generator):
    pool = {ar for group in (generator.FIRST_F, generator.FIRST_M, generator.FATHERS, generator.FAMILIES) for ar, _ in group}
    latin = {la.upper() for group in (generator.FIRST_F, generator.FIRST_M, generator.FATHERS, generator.FAMILIES)
             for _, la in group}
    for spec in SPECIMENS:
        name = spec["truth"]["full_name_ar"]
        if name:
            rest = name
            for word in sorted(pool, key=len, reverse=True):
                rest = rest.replace(word, "")
            assert not rest.strip(), spec["file"]
        if spec["truth"]["full_name_latin"]:
            assert set(spec["truth"]["full_name_latin"].split()) <= latin


def forms(*codes: int) -> str:
    """Presentation-form letters by code point, so this file shows which form is meant."""
    return "".join(map(chr, codes))


def test_arabic_shaping_without_an_engine(generator):
    # عـلـي: initial ain, medial lam, final yeh
    assert generator.shape("علي") == forms(0xFECB, 0xFEE0, 0xFEF2)
    # دار: dal and alef never join the next letter, so all three stand alone
    assert generator.shape("دار") == forms(0xFEA9, 0xFE8D, 0xFEAD)
    # سلام: initial seen, the lam-alef ligature in its final form, a lone meem
    assert generator.shape("سلام") == forms(0xFEB3, 0xFEFC, 0xFEE1)
    assert generator.shape("لا") == forms(0xFEFB)
    assert generator.shape("A 12") == "A 12"
    assert generator.visual("علي") == forms(0xFEF2, 0xFEE0, 0xFECB)  # drawn left to right, read right to left
    assert len(generator.FORMS) == 36


def test_scoring_a_perfect_reader_and_a_blind_one():
    by_hash = {s["sha256"]: s["truth"] for s in SPECIMENS}
    result = score(lambda image, mime: dict(by_hash[hashlib.sha256(image).hexdigest()]))
    assert result["images"] == result["verdicts_right"] == 15 and result["misses"] == []
    assert set(result["fields_right"]) == set(FIELDS)
    assert all(right == total for right, total in result["fields_right"].values())
    assert result["fields_right"]["is_identity_document"] == [15, 15]
    assert result["fields_right"]["document_number"] == [13, 13]  # the two non-documents have no fields to score

    blind = score(lambda image, mime: None)
    assert blind["verdicts_right"] == 0 and len(blind["misses"]) == 15
    assert all(miss["got"] == {"status": "retry", "reason": "no_reading"} for miss in blind["misses"])


def test_scoring_notices_one_misread_digit():
    by_hash = {s["sha256"]: s for s in SPECIMENS}

    def sloppy(image, mime):
        spec = by_hash[hashlib.sha256(image).hexdigest()]
        raw = dict(spec["truth"])
        if spec["file"] == "national_01.png":
            raw["document_number"] = raw["document_number"][:-1]  # one digit lost
        return raw

    result = score(sloppy)
    assert result["verdicts_right"] == 14
    assert result["misses"] == [{"file": "national_01.png", "expected": {"status": "verified", "reason": "ok"},
                                 "got": {"status": "retry", "reason": "bad_number"}, "wrong_fields": ["document_number"]}]
