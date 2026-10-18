import csv
from pathlib import Path

import pytest

from payments import amounts
from payments.amounts import Amount, find_amount, parse

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "amounts.csv"


def _rows():
    with CSV_PATH.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


ROWS = _rows()


def test_dataset_is_big_enough():
    assert len(ROWS) >= 150
    assert any(r["currency"] == "USD" for r in ROWS)
    assert any(r["needs_question"] == "true" and r["currency"] == "" for r in ROWS)


@pytest.mark.parametrize("row", ROWS, ids=[f"{i}:{r['text']}" for i, r in enumerate(ROWS)])
def test_every_csv_row(row):
    got = parse(row["text"])
    expected_value = int(row["value_iqd"]) if row["value_iqd"] else None
    assert got.value_iqd == expected_value
    assert got.currency == (row["currency"] or None)
    assert got.needs_question == (row["needs_question"] == "true")
    if got.needs_question:
        assert got.reason, "a question always carries a reason"


def test_spec_examples():
    assert parse("خمسين").value_iqd == 50_000
    assert parse("مية وخمسين").value_iqd == 150_000
    assert parse("ميتين").value_iqd == 200_000
    assert parse("خمسمية").value_iqd == 500_000
    assert parse("خمس تالاف").value_iqd == 5_000
    assert parse("ربع مليون").value_iqd == 250_000
    assert parse("نص مليون").value_iqd == 500_000
    assert parse("مليون ونص").value_iqd == 1_500_000
    assert parse("٥٠٬٠٠٠").value_iqd == 50_000
    assert parse("خمسطعش").value_iqd == 15_000


def test_dollars_never_converted():
    for text in ("ورقة", "ورقتين", "دفتر", "100 دولار", "$100"):
        got = parse(text)
        assert got == Amount(None, "USD", True, amounts.USD)


def test_reasons():
    assert parse("").reason == amounts.NO_AMOUNT
    assert parse("خمسين او ستين").reason == amounts.MULTIPLE
    assert parse("حوالي خمسين").reason == amounts.APPROXIMATE
    assert parse("نص").reason == amounts.FRACTION_ALONE
    assert parse("الاف").reason == amounts.MISSING_COUNT
    assert parse("خمسين ستين").reason == amounts.UNCLEAR
    assert parse("صفر").reason == amounts.ZERO
    assert parse("1.2345").reason == amounts.NOT_WHOLE
    assert parse("12.5.3").reason == amounts.BAD_DIGITS


def test_find_amount_returns_matched_text():
    amount, matched = find_amount("دز لأحمد خمسين")
    assert amount.value_iqd == 50_000
    assert matched == "خمسين"

    amount, matched = find_amount("حول لعلي مية وخمسين الف لو سمحت")
    assert amount.value_iqd == 150_000
    assert matched == "مية وخمسين الف"

    amount, matched = find_amount("اشحن رقمي بـ ٥ الاف")
    assert amount.value_iqd == 5_000
    assert matched == "٥ الاف"


def test_find_amount_none_without_amount():
    assert find_amount("ادفع المولدة") is None
    assert find_amount("شكد رصيدي؟") is None
    assert find_amount("ادفع فاتورة شهر 9") is None  # a month, not money


def test_phone_numbers_are_not_amounts():
    amount, matched = find_amount("اشحن 07701234567 بعشرة")
    assert amount.value_iqd == 10_000
    assert matched == "بعشرة"


def test_trailing_connector_not_in_match():
    amount, matched = find_amount("دزله خمسين و")
    assert amount.value_iqd == 50_000
    assert matched == "خمسين"


@pytest.mark.parametrize("text", ["؟؟؟", "و", "$", "١,", "الا ربع", "abc def", "٫٫", "0"])
def test_never_raises(text):
    got = parse(text)
    assert isinstance(got, Amount)
