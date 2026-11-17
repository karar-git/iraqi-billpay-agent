"""The identity step's words: every reason has a text in the three languages, and the text rules hold."""

import re
from pathlib import Path

import pytest

from kyc import POLICY, REASONS, TEXT_KEYS, limits_for
from kyc.texts import TEXTS, TEXTS_EN, TEXTS_IQ, TEXTS_MSA, render

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ("iq", "msa", "en")
SLOTS = {"daily_limit": 150_000, "single_max": 50_000, "attempts": 3}
CHE = chr(0x0686)       # the Persian letter Iraqi text never writes
EM_DASH = chr(0x2014)
OWN_FILES = [*sorted((ROOT / "kyc").glob("*.py")), ROOT / "kyc" / "README.md", ROOT / "data" / "kyc" / "generate_ids.py",
             ROOT / "data" / "kyc" / "specimens.json", *sorted((ROOT / "tests").glob("test_kyc_*.py"))]


def test_the_three_languages_have_the_same_keys():
    assert tuple(TEXTS) == LANGUAGES
    assert set(TEXTS_IQ) == set(TEXTS_MSA) == set(TEXTS_EN)


def test_every_reason_has_its_text():
    for reason, key in TEXT_KEYS.items():
        assert key == f"kyc_{reason}"
        for lang in LANGUAGES:
            assert key in TEXTS[lang], (lang, key)
    assert set(TEXT_KEYS) == set(REASONS)
    for key in ("kyc_ask", "kyc_limits_note", "kyc_single_max", "kyc_daily_limit", "kyc_too_many_attempts"):
        assert key in TEXTS_IQ


@pytest.mark.parametrize("lang", LANGUAGES)
@pytest.mark.parametrize("gender", ["f", "m"])
def test_every_text_renders_with_its_slots(lang, gender):
    for key in TEXTS[lang]:
        text = render(key, gender, lang, **SLOTS)
        assert text.strip() and "{" not in text and "}" not in text, (lang, key)


def test_slots_are_the_same_in_every_language():
    def slots(template) -> set[str]:
        texts = template.values() if isinstance(template, dict) else [template]
        found = [set(re.findall(r"\{(\w+)\}", text)) for text in texts]
        assert all(f == found[0] for f in found)
        return found[0]

    for key in TEXTS_IQ:
        assert slots(TEXTS_IQ[key]) == slots(TEXTS_MSA[key]) == slots(TEXTS_EN[key]) <= set(SLOTS), key


def test_amounts_are_written_with_separators_and_come_from_the_policy_table():
    text = render("kyc_ok", "f", "iq", **limits_for("verified"))
    assert f"{POLICY['verified']['daily_limit']:,}" in text and f"{POLICY['verified']['single_max']:,}" in text
    assert "150,000" in render("kyc_limits_note", "m", "en", **limits_for("none"))
    assert "50,000" in render("kyc_single_max", "f", "msa", **limits_for("pending"))


def test_gender_and_language_fallbacks():
    assert render("kyc_expired", "m", "iq") != render("kyc_expired", "f", "iq")
    assert render("kyc_expired", "x", "iq") == render("kyc_expired", "f", "iq")
    assert render("kyc_expired", "f", None) == render("kyc_expired", "f", "iq") == render("kyc_expired", "f", "fr")
    assert "هويتج" in render("kyc_expired", "f", "iq") and "هويتك" in render("kyc_expired", "m", "iq")


def test_arabic_texts_are_arabic_and_english_is_english():
    arabic = re.compile(r"[ء-ي]")
    for key in TEXTS_IQ:
        for gender in ("f", "m"):
            assert arabic.search(render(key, gender, "iq", **SLOTS)), key
            assert arabic.search(render(key, gender, "msa", **SLOTS)), key
            assert not arabic.search(render(key, gender, "en", **SLOTS)), key


def test_a_failure_about_money_says_nothing_was_taken():
    for key in ("kyc_single_max", "kyc_daily_limit"):
        assert "ما انخصم من" in render(key, "f", "iq", **SLOTS)
        assert "لم يُخصم منك شيء" in render(key, "m", "msa", **SLOTS)
        assert "Nothing was taken" in render(key, "f", "en", **SLOTS)


def test_the_question_says_what_is_accepted_and_what_is_kept():
    for lang, words in (("iq", ("البطاقة الوطنية", "الجواز", "الإقامة", "ما نحفظها")),
                        ("msa", ("البطاقة الوطنية", "جواز السفر", "الإقامة", "لا نحتفظ بها")),
                        ("en", ("national card", "passport", "residence card", "do not keep it"))):
        text = render("kyc_ask", "f", lang)
        assert all(word in text for word in words), lang


@pytest.mark.parametrize("path", OWN_FILES, ids=lambda p: p.name)
def test_no_persian_che_and_no_em_dash_in_our_files(path):
    text = path.read_text(encoding="utf-8")
    assert CHE not in text, "Iraqi text writes ج"
    assert EM_DASH not in text
