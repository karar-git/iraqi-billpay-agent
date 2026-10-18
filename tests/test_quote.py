import string

import pytest

from payments import messages, rules
from payments.amounts import parse
from payments.messages import FAILURE_KEYS, MESSAGES, format_period, render
from payments.quote import BUTTONS, Card, QuoteDetails, amount_in_words, render_card
from payments.rules import PastPayment, WalletView


# ---- amount in Iraqi words ----------------------------------------------------

@pytest.mark.parametrize("n,words", [
    (1_000, "ألف دينار"),
    (2_000, "ألفين دينار"),
    (3_000, "تلاث تالاف دينار"),
    (5_000, "خمس تالاف دينار"),
    (10_000, "عشر تالاف دينار"),
    (11_000, "حدعش ألف دينار"),
    (15_000, "خمسطعش ألف دينار"),
    (25_000, "خمسة وعشرين ألف دينار"),
    (50_000, "خمسين ألف دينار"),
    (100_000, "مية ألف دينار"),
    (150_000, "مية وخمسين ألف دينار"),
    (200_000, "ميتين ألف دينار"),
    (250_000, "ربع مليون دينار"),
    (500_000, "نص مليون دينار"),
    (750_000, "سبعمية وخمسين ألف دينار"),
    (1_000_000, "مليون دينار"),
    (1_250_000, "مليون وربع دينار"),
    (1_500_000, "مليون ونص دينار"),
    (2_000_000, "مليونين دينار"),
    (3_000_000, "تلاث ملايين دينار"),
    (10_000_000, "عشر ملايين دينار"),
    (50_250, "خمسين ألف وميتين وخمسين دينار"),
])
def test_amount_in_words(n, words):
    assert amount_in_words(n) == words


def test_words_round_trip_through_parser():
    amounts = list(range(1_000, 1_000_001, 1_000)) + list(range(1_000_000, 10_000_001, 50_000))
    for n in amounts:
        assert parse(amount_in_words(n)).value_iqd == n, n


def test_words_reject_out_of_range():
    with pytest.raises(ValueError):
        amount_in_words(0)


# ---- the card -----------------------------------------------------------------

TRANSFER = QuoteDetails("q1", "transfer", 50_000, fee=250, payee_id="c1",
                        payee_name="أحمد علي", phone="07700000101")
BILL = QuoteDetails("q2", "bill", 35_000, payee_id="acct-7", payee_name="زينب حسن",
                    biller_name="مولدة الحاج كريم", period="2026-09", account_no="G-1029")
TOPUP = QuoteDetails("q3", "topup", 5_000, payee_id="07800000123", phone="07800000123")

KNOWN = [PastPayment("c1", 40_000, minutes_ago=10_000)]


def test_transfer_card_female():
    card = render_card(TRANSFER, WalletView(200_000, 1_000_000, gender="f", history=KNOWN))
    assert isinstance(card, Card)
    assert card.quote_id == "q1"
    text = card.text
    assert "تدزين" in text and "50,000 دينار" in text and "خمسين ألف دينار" in text
    assert "أحمد علي" in text and "0101" in text and "07700000101" not in text
    assert "250 دينار" in text and "50,250 دينار" in text
    assert "رصيدج بعد الدفع: 149,750 دينار" in text
    assert card.flags == [] and card.flag_codes == []
    assert [b.label for b in card.buttons] == ["إي، ادفع", "غيّر", "لا، بطّلت"]
    assert [b.id for b in card.buttons] == ["confirm", "change", "cancel"]


def test_transfer_card_male():
    card = render_card(TRANSFER, WalletView(200_000, 1_000_000, gender="m", history=KNOWN))
    assert "راح تدز 50,000" in card.text
    assert "رصيدك" in card.text and "متأكد؟" in card.text
    assert "تدزين" not in card.text and "رصيدج" not in card.text


def test_bill_card_shows_biller_holder_period_amount():
    card = render_card(BILL, WalletView(100_000, 1_000_000, gender="f"))
    text = card.text
    assert "تدفعين" in text
    assert "مولدة الحاج كريم" in text
    assert "زينب حسن" in text
    assert "أيلول 2026" in text
    assert "35,000 دينار" in text and "خمسة وتلاثين ألف دينار" in text
    assert "بدون عمولة" in text
    assert card.flags == []  # saved bill account: no first-time warning


def test_topup_card():
    card = render_card(TOPUP, {"gender": "m"})
    assert "تشحن رصيد 5,000 دينار" in card.text
    assert "0123" in card.text
    assert card.flags == []  # no WalletView, no computed flags


def test_soft_flags_computed_from_wallet_view():
    history = [PastPayment("c1", 50_000, minutes_ago=3, status="UNKNOWN")]
    card = render_card(TRANSFER, WalletView(200_000, 1_000_000, gender="f", history=history))
    assert card.flag_codes == [rules.FIRST_TIME_PAYEE, rules.DUPLICATE_RECENT]
    assert all(line.startswith("تنبيه:") for line in card.flags)
    assert "أحمد علي" in card.flags[0]
    lines = card.text.splitlines()
    assert lines[-1].endswith("متأكدة؟")  # warnings sit above the final question


def test_explicit_flags_override():
    card = render_card(TRANSFER, WalletView(200_000, 1_000_000, history=KNOWN),
                       flags=[rules.AMOUNT_JUMP])
    assert card.flag_codes == [rules.AMOUNT_JUMP]
    assert "3 مرات" in card.flags[0]


def test_card_from_dict_and_deterministic():
    data = {"id": "q9", "kind": "transfer", "amount": 50_000, "fee": 250, "payee_id": "c1",
            "payee_name": "أحمد علي", "phone": "07700000101", "state": "QUOTED"}
    v = WalletView(200_000, 1_000_000, history=KNOWN)
    assert render_card(data, v) == render_card(data, v)
    assert render_card(data, v).quote_id == "q9"


def test_buttons_constant():
    assert len(BUTTONS) == 3


# ---- fixed messages -----------------------------------------------------------

def _texts():
    for key, value in MESSAGES.items():
        if isinstance(value, dict):
            assert set(value) == {"f", "m"}, key
            yield from ((key, v) for v in value.values())
        else:
            yield key, value


def test_never_che():
    for key, text in _texts():
        assert "چ" not in text, key
    card = render_card(TRANSFER, WalletView(200_000, 1_000_000))
    assert "چ" not in card.text


def test_failures_say_whether_money_moved():
    money_status = ("ما انخصم", "ما ضاع", "لا تعيدي", "لا تعيد")
    for key in FAILURE_KEYS:
        for gender in ("f", "m"):
            template = MESSAGES[key][gender] if isinstance(MESSAGES[key], dict) else MESSAGES[key]
            assert any(s in template for s in money_status), (key, gender)


def test_hard_stop_codes_have_messages():
    for code in (rules.INSUFFICIENT_BALANCE, rules.OVER_DAILY_LIMIT,
                 rules.NON_POSITIVE_AMOUNT, rules.NON_IQD):
        assert code in MESSAGES and code in FAILURE_KEYS
    for code in (rules.FIRST_TIME_PAYEE, rules.DUPLICATE_RECENT, rules.AMOUNT_JUMP):
        assert f"flag_{code}" in MESSAGES


def test_unknown_outcome_text_matches_spec():
    assert render("unknown_outcome", "f") == "لهسه ما نعرف إذا انحوّلت، لا تعيديها. راح نتأكد ونرجعلج خبر."


def test_render_fills_slots_and_formats_numbers():
    text = render("insufficient_balance", "f", balance=20_000, needed=50_250)
    assert "20,000" in text and "50,250" in text and "ما انخصم منج شي" in text
    assert render("insufficient_balance", "m", balance=1, needed=2) != render(
        "insufficient_balance", "f", balance=1, needed=2)
    assert "أيلول 2026" in render("already_paid", "f", biller="كهرباء الحي", period="2026-09")


def test_every_message_renders_with_its_slots():
    for key, text in _texts():
        slots = {name: "X" for _, name, _, _ in string.Formatter().parse(text) if name}
        assert render(key, "f", **slots)
        assert render(key, "m", **slots)


def test_format_period():
    assert format_period("2026-01") == "كانون الثاني 2026"
    assert format_period("2026-12") == "كانون الأول 2026"
    assert format_period("bad") == "bad"


def test_flag_line():
    line = messages.flag_line(rules.FIRST_TIME_PAYEE, "m", name="علي")
    assert line.startswith("تنبيه:") and "علي" in line and "دزّيت " in line


def test_transfer_card_does_not_guess_the_payees_gender():
    card = render_card(TRANSFER, WalletView(200_000, 1_000_000, history=KNOWN))
    assert "الرقم ينتهي بـ 0101" in card.text and "رقمه" not in card.text


def test_topup_flags_talk_about_the_line_not_a_person():
    codes = [rules.FIRST_TIME_PAYEE, rules.DUPLICATE_RECENT, rules.AMOUNT_JUMP]
    card = render_card(TOPUP, WalletView(100_000, 1_000_000, gender="f"), flags=codes)
    assert card.flag_codes == codes
    assert "تشحنين" in card.flags[0]
    for line in card.flags:
        assert "لهالرقم" in line and "لـ الرقم" not in line and "دزّيت" not in line
