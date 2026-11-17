"""Regressions from the review of the audit fixes: amounts that showed a wrong card, the scam stop on both sides,
names one letter apart, voice notes that named a relative, and Latin names read with other vowels."""

from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from agent.session import Session, _display
from core.interface import Intent
from core.stub import StubCore
from payments import amounts, arabizi, names, scam
from payments.messages import CATALOGS, render
from tests.test_session import SEED, UID
from wallet.api import create_app

UNSAVED = "07711119999"


def seed_with(contacts=(), balance=50_000_000):
    seed = copy.deepcopy(SEED)
    seed["users"][0]["balance"] = balance
    seed["users"][0]["daily_limit"] = 10 ** 10
    for i, (name, phone) in enumerate(contacts):
        seed["contacts"].append({"contact_id": f"x{i}", "user_id": UID, "display_name": name, "phone": phone})
    return seed


@pytest.fixture
def make():
    clients = []

    def build(core=None, *contacts, balance=50_000_000):
        client = TestClient(create_app(":memory:", seed=seed_with(contacts, balance)))
        clients.append(client)
        return Session(wallet=client, core=core or StubCore()), client

    yield build
    for client in clients:
        client.close()


class Says(StubCore):
    """A core that returns one fixed reading."""

    def __init__(self, **intent):
        self.intent = intent

    def understand(self, text, context=None):
        return Intent(**self.intent)


def pays(action="transfer", payee="لهادي", amount=None):
    return Says(action=action, payee_mention=payee, amount_text=amount)


def card_of(client, reply):
    assert reply.card is not None, reply.text
    quote = client.get(f"/quotes/{reply.quote_id}").json()
    return quote["amount"], quote["target"].get("display_name")


def is_unclear(found):
    return found.needs_question and found.value_iqd is None


# ---------------------------------------------------------------- amounts: three, teens, minus


@pytest.mark.parametrize("text, value", [
    ("تلت مية", 300_000), ("تلتمية الف", 300_000), ("تلت مية الف", 300_000), ("تلتميت الف", 300_000),
    ("تلتميه الف", 300_000), ("تلاتمية الف", 300_000), ("ثلاتمية الف", 300_000), ("تلات مية", 300_000),
    ("تلت ارباع مليون", 750_000), ("ثلث ارباع مليون", 750_000), ("تلات ارباع مليون", 750_000),
    ("ثلاث ارباع مليون", 750_000), ("تلت تلاف", 3_000), ("ثلث تالاف", 3_000),
])
def test_the_spellings_of_three(text, value):
    assert amounts.parse(text).value_iqd == value


@pytest.mark.parametrize("text", ["تلت مليون", "تلت الف", "ثلث مليون", "التلت مليون"])
def test_bare_talt_before_a_scale_may_be_a_third(text):
    assert is_unclear(amounts.parse(text))


@pytest.mark.parametrize("text, value", [
    ("خمس طعش الف", 15_000), ("ستطعشر الف", 16_000), ("خمسطعشر الف", 15_000), ("سبعطعشر الف", 17_000),
    ("تسعطعشر الف", 19_000), ("ثمنطعشر الف", 18_000), ("تلطعشر الف", 13_000), ("حدعشر الف", 11_000),
    ("تلاثطعش الف", 13_000), ("خمس طعش", 15_000), ("ست طعش", 16_000), ("اربعتعشر", 14_000), ("تلت طعش الف", 13_000),
    ("خمسة عشر الف", 15_000), ("أحد عشر ألف", 11_000),
])
def test_teens_however_they_are_written(text, value):
    assert amounts.parse(text).value_iqd == value


def test_an_unreadable_teen_before_a_scale_asks():
    assert is_unclear(amounts.parse("فلانطعش الف"))


@pytest.mark.parametrize("text, value", [
    ("مليون الا الربع", 750_000), ("مليون الا ربع", 750_000), ("الف الا الربع", 750), ("مليونين الا الربع", 1_750_000),
    ("ميتين الا الربع", 175_000),
])
def test_minus_a_quarter(text, value):
    assert amounts.parse(text).value_iqd == value


@pytest.mark.parametrize("text", ["مليون الا ثلث", "مليون الا الثلث", "مليون الا شوية", "مليون الا النص", "مية الف الا شي"])
def test_a_minus_we_cannot_compute_is_never_lost(text):
    assert is_unclear(amounts.parse(text))


def test_the_word_for_except_with_no_amount_before_it_is_nothing():
    assert amounts.parse("الا هادي").reason == amounts.NO_AMOUNT


@pytest.mark.parametrize("first, second, value", [
    ("دز لهادي", "تلت مية", 300_000), ("دز لهادي", "تلتمية الف", 300_000), ("دز لهادي", "تلت ارباع مليون", 750_000),
    ("دز لهادي", "خمس طعش الف", 15_000), ("دز لهادي", "ستطعشر الف", 16_000),
])
def test_the_amount_given_in_the_second_turn(make, first, second, value):
    session, client = make()
    session.handle_text(UID, first)
    assert card_of(client, session.handle_text(UID, second)) == (value, "هادي")


def test_whole_phrase_from_a_core(make):
    session, client = make(pays(amount="تلت مية الف"))
    assert card_of(client, session.handle_text(UID, "دز لهادي تلت مية الف")) == (300_000, "هادي")


def test_a_quarter_off_end_to_end(make):
    session, client = make()
    assert card_of(client, session.handle_text(UID, "دز لهادي مليون الا الربع")) == (750_000, "هادي")


# ---------------------------------------------------------------- ال on a number: a name or a count


@pytest.mark.parametrize("name, text, value", [
    ("حيدر السبع", "دز لحيدر السبع مليون", 1_000_000), ("حيدر السبع", "دز لحيدر السبع الف", 1_000),
    ("علي الاربعين", "دز لعلي الاربعين مليون", 1_000_000), ("علي العشرة", "دز لعلي العشرة الف", 1_000),
    ("حيدر السبع", "دز لحيدر السبع خمسين الف", 50_000),
])
def test_a_number_that_is_the_end_of_a_saved_name_is_the_name(make, name, text, value):
    session, client = make(None, (name, "07700000021"))
    assert card_of(client, session.handle_text(UID, text)) == (value, name)


def test_the_same_with_a_core_that_points_only_at_the_scale(make):
    session, client = make(pays(payee="لحيدر السبع", amount="مليون"), ("حيدر السبع", "07700000021"))
    assert card_of(client, session.handle_text(UID, "دز لحيدر السبع مليون")) == (1_000_000, "حيدر السبع")


@pytest.mark.parametrize("text", ["دز لحيدر السبع مليون", "دز لحيدر السبع الف"])
def test_a_nickname_that_is_not_saved_asks(make, text):
    session, _ = make(None, ("حيدر", "07700000021"))
    reply = session.handle_text(UID, text)
    assert reply.card is None and reply.kind == "ask"


def test_the_parser_alone_never_counts_a_likely_name():
    assert is_unclear(amounts.parse("دز لحيدر السبع مليون"))
    assert is_unclear(amounts.parse("دز لعلي الاربعين مليون"))


@pytest.mark.parametrize("text, value", [("دز لحيدر الشعلة الخمسين الف", 50_000), ("دز لحيدر الشعلة الربع مليون", 250_000),
                                         ("دز لحيدر الشعلة العشر تلاف", 10_000)])
def test_a_number_after_the_whole_saved_name_is_the_amount(make, text, value):
    session, client = make(None, ("حيدر الشعلة", "07700000021"))
    assert card_of(client, session.handle_text(UID, text))[0] == value


def test_the_amount_word_stays_when_the_name_has_the_same_word(make):
    session, client = make(None, ("ست نادية", "07700000021"))
    assert card_of(client, session.handle_text(UID, "دز لست نادية ست تلاف")) == (6_000, "ست نادية")
    session, _ = make(None, ("ست نادية", "07700000021"))
    assert session.handle_text(UID, "دز لست نادية حوالي ست تلاف").card is None


# ---------------------------------------------------------------- words we do not know, right before a bare scale


@pytest.mark.parametrize("word", ["خميسن", "مييه", "شوية", "فلان", "تمنطعشش", "خمص"])
def test_an_unknown_word_before_a_bare_scale_asks_in_the_session(make, word):
    session, _ = make()
    session.handle_text(UID, "دز لهادي")
    reply = session.handle_text(UID, f"{word} الف")
    assert reply.card is None and reply.ask_field == "amount"


def test_an_unknown_word_before_a_bare_hundred_asks(make):
    session, _ = make(pays(amount="مية"))
    assert session.handle_text(UID, "دز لهادي خمص مية").card is None


@pytest.mark.parametrize("text, value", [
    ("دز لهادي مليون", 1_000_000), ("حول لهادي بس مليون", 1_000_000), ("دز مليون لهادي", 1_000_000),
    ("دز لهادي فد مليون", 1_000_000), ("دز لهادي مبلغ مليون", 1_000_000), ("دز لهادي مية", 100_000),
    ("أريد أن أحوّل إلى هادي مليون", 1_000_000),
])
def test_ordinary_words_before_a_bare_scale_change_nothing(make, text, value):
    session, client = make()
    assert card_of(client, session.handle_text(UID, text)) == (value, "هادي")


@pytest.mark.parametrize("name, text", [("علي الحلاق", "دز لعلي الحلاق مليون"), ("ابو حسن", "دز لابو حسن مليون"),
                                        ("بنين", "حول بنين مليون"), ("الحاج جاسم", "دز للحاج جاسم مليون")])
def test_the_saved_name_before_a_bare_scale_is_no_unknown_word(make, name, text):
    session, client = make(None, (name, "07700000021"))
    assert card_of(client, session.handle_text(UID, text)) == (1_000_000, name)


def test_a_nickname_before_a_bare_scale_is_the_payee(make):
    session, client = make(None, ("فاطمة", "07700000021"))
    reply = session.handle_text(UID, "دز فطومة مليون")
    assert card_of(client, reply) == (1_000_000, "فاطمة")


@pytest.mark.parametrize("text, value", [
    ("اتنين الف", 2_000), ("اتنين مليون", 2_000_000), ("ثلاتة مليون", 3_000_000), ("تلته مليون", 3_000_000),
    ("تمن مليون", 8_000_000), ("ثلاتة الف", 3_000),
])
def test_misspelt_units_are_read(text, value):
    assert amounts.parse(text).value_iqd == value


@pytest.mark.parametrize("text", ["ثلثا مليون", "سدس مليون", "ثلاثة اخماس مليون", "شوية مليون", "ثلثا الف", "شوية الف",
                                  "فد شوية الف", "خميسن الف", "مليون وثلث", "مليون وتلت", "مليون وثلثين", "الف وثلث",
                                  "مليون و شوية", "مليون و كم", "مليون شوية"])
def test_vague_counts_and_tails_ask(text):
    assert is_unclear(amounts.parse(text))


def test_rice_is_no_amount():
    assert amounts.parse("دز لهادي التمن").reason == amounts.NO_AMOUNT
    assert amounts.parse("تمن").reason == amounts.NO_AMOUNT


@pytest.mark.parametrize("text", ["عشرة مليار", "خمسين مليار", "مليار", "two billion"])
def test_scales_past_what_a_wallet_sends_ask(text):
    found = amounts.parse(arabizi.to_arabic(text))
    assert found.reason == amounts.TOO_LARGE and found.value_iqd is None


def test_a_billion_from_a_core_asks(make):
    session, _ = make(pays(amount="خمسين مليار"))
    assert session.handle_text(UID, "دز لهادي خمسين مليار").card is None


# ---------------------------------------------------------------- money that is not dinars


@pytest.mark.parametrize("text", ["خمسين بالدولار", "مية بالدولار", "الف دولاري", "مية يورو", "مية ريال", "خمسين درهم",
                                  "مية تومان", "مية جنيه", "مية ليرة", "خمسين دينار اردني", "مية دينار كويتي"])
def test_every_other_currency_asks(text):
    found = amounts.parse(text)
    assert found.needs_question and found.value_iqd is None and found.currency == "USD"


@pytest.mark.parametrize("text", ["send hadi fifty bucks", "send hadi 50 euros", "send hadi 100 pounds"])
def test_other_currencies_in_english_ask_in_the_session(make, text):
    session, _ = make()
    reply = session.handle_text(UID, text)
    assert reply.card is None and reply.ask_field == "amount"


def test_a_core_that_points_only_at_the_number_still_asks(make):
    session, _ = make(pays(amount="خمسين"))
    reply = session.handle_text(UID, "دز لهادي خمسين بالدولار")
    assert reply.card is None and reply.text == render("ask_usd", "f", "iq")


def test_a_currency_word_alone_is_a_name(make):
    assert amounts.parse("دز ريال خمسين").value_iqd == 50_000
    assert amounts.parse("ريال").reason == amounts.NO_AMOUNT


# ---------------------------------------------------------------- واحد next to a number


@pytest.mark.parametrize("text", ["كل وحدة عشرة", "كل وحدة عشرين الف", "كل واحد خمسين", "خمسين وحدة", "مية وحدة",
                                  "مليون وحدة", "واحد خمسين", "لكل واحد عشرين"])
def test_one_next_to_a_number_asks(text):
    assert is_unclear(amounts.parse(text))


@pytest.mark.parametrize("text, value", [("مليون واحد", 1_000_000), ("الف واحد", 1_000), ("واحد مليون", 1_000_000),
                                         ("واحد وعشرين", 21_000), ("عشرين وواحد", 21_000), ("واحد", 1_000)])
def test_one_where_it_means_one(text, value):
    assert amounts.parse(text).value_iqd == value


def test_each_of_two_people_never_adds_one(make):
    session, _ = make()
    reply = session.handle_text(UID, "دز لهادي وزينب كل واحد خمسين")
    assert reply.card is None and reply.kind == "ask"


# ---------------------------------------------------------------- digits and English


@pytest.mark.parametrize("text", [".5 مليون", ".5 الف", ",5 الف", "٫٥ مليون", "1000 000"])
def test_digit_forms_we_cannot_read_ask(text):
    found = amounts.parse(text)
    assert found.needs_question and found.value_iqd is None


@pytest.mark.parametrize("text, value", [("1 000 000", 1_000_000), ("50 000", 50_000), ("1 250 000", 1_250_000),
                                         ("١ ٠٠٠ ٠٠٠", 1_000_000), ("1.5 مليون", 1_500_000), ("50,000", 50_000)])
def test_digit_groups_with_spaces(text, value):
    assert amounts.parse(text).value_iqd == value


def test_two_numbers_with_a_space_are_not_one_number():
    assert is_unclear(amounts.parse("50 100"))


def test_a_comma_right_after_a_word_is_punctuation():
    assert amounts.parse("هادي,50 الف").value_iqd == 50_000


def test_a_written_out_amount_with_a_scale_after_it_asks():
    assert is_unclear(amounts.parse("50,000 الف"))


@pytest.mark.parametrize("text, value", [
    ("one and a half million", 1_500_000), ("two and a half million", 2_500_000), ("two and a half thousand", 2_500),
    ("twenty-five thousand", 25_000), ("twenty five thousand", 25_000), ("three quarters of a million", 750_000),
    ("half a million", 500_000), ("a million", 1_000_000), ("1 mil", 1_000_000), ("two hundred fifty thousand", 250_000),
    ("one hundred and fifty thousand", 150_000), ("fifteen thousand", 15_000), ("twenty one", 21_000),
])
def test_english_amounts(text, value):
    assert amounts.parse(arabizi.to_arabic(text)).value_iqd == value


@pytest.mark.parametrize("text", ["a third of a million", "two thirds of a million", "a couple of million",
                                  "a couple of thousand"])
def test_english_fractions_we_do_not_compute_ask(text):
    assert is_unclear(amounts.parse(arabizi.to_arabic(text)))


@pytest.mark.parametrize("text, value", [("send hadi one and a half million", 1_500_000), ("send hadi 1 mil", 1_000_000),
                                         ("دز لهادي 1 000 000", 1_000_000)])
def test_digit_and_english_forms_end_to_end(make, text, value):
    session, client = make()
    assert card_of(client, session.handle_text(UID, text)) == (value, "هادي")


@pytest.mark.parametrize("amount", ["two and a half million", "twenty-five thousand"])
def test_english_phrase_from_a_core(make, amount):
    session, client = make(pays(payee="hadi", amount=amount))
    reply = session.handle_text(UID, f"send hadi {amount}")
    assert card_of(client, reply)[0] == {"two and a half million": 2_500_000, "twenty-five thousand": 25_000}[amount]


def test_a_leading_dot_end_to_end(make):
    session, _ = make()
    assert session.handle_text(UID, "دز لهادي .5 مليون").card is None
    session, _ = make(pays(amount="5 مليون"))
    assert session.handle_text(UID, "دز لهادي .5 مليون").card is None


# ---------------------------------------------------------------- hedges and ranges


@pytest.mark.parametrize("text", [
    "قرابة مليون", "نحو مليون", "ما يقارب مليون", "زهاء مليون", "اقل من مليون", "اكثر من مليون", "فوق مليون", "تحت مليون",
    "تقريبن خمسين", "فدشي خمسين", "خمسين وفوك", "خمسين وفوق", "خمسين واكثر", "خمسين او اكثر", "خمسين لو اقل", "خمسين لستين",
    "من خمسين لستين", "فد خمسين الف", "فد مية",
])
def test_hedges_and_ranges_ask(text):
    assert amounts.parse(text).reason == amounts.APPROXIMATE


@pytest.mark.parametrize("text", ["almost a million", "over a million", "under a million", "less than a million",
                                  "more than a million", "around 50", "about 50", "roughly 50", "at least 50"])
def test_english_hedges_ask(text):
    assert amounts.parse(arabizi.to_arabic(text)).reason == amounts.APPROXIMATE


def test_a_person_after_an_amount_is_no_range():
    assert amounts.parse("دز خمسين لهادي").value_iqd == 50_000
    assert amounts.parse("دز خمسين لست نادية").value_iqd == 50_000


@pytest.mark.parametrize("core, text", [
    (pays(payee="hadi", amount="50"), "send hadi around 50"),
    (pays(amount="خمسين"), "دز لهادي اكثر من خمسين"),
    (None, "دز لهادي خمسين لستين"),
    (None, "دز لهادي خمسين او اكثر"),
])
def test_hedges_end_to_end(make, core, text):
    session, _ = make(core)
    reply = session.handle_text(UID, text)
    assert reply.card is None and reply.ask_field == "amount"


# ---------------------------------------------------------------- fractions and numbers side by side


@pytest.mark.parametrize("text", ["مليون ونص الف", "نص مليون ونص", "ربع مليون ونص", "مليون وربع ونص", "مية الف ونص",
                                  "ميتين الف ونص", "عشرين خمسة", "مية خمسين", "خمسة عشرين"])
def test_combinations_we_cannot_read_ask(text):
    assert is_unclear(amounts.parse(text))


@pytest.mark.parametrize("text, value", [("خمس تلاف ونص", 5_500), ("الف ونص", 1_500), ("مية ونص", 150_000),
                                         ("مليون ونص", 1_500_000), ("عشرين وخمسة", 25_000), ("خمسة وعشرين", 25_000),
                                         ("مية وخمسين", 150_000), ("مية وخمسين الف", 150_000), ("خمس مية", 500_000),
                                         ("خمسة عشر", 15_000), ("مليون وميتين", 1_200_000)])
def test_the_ordinary_ones_stay(text, value):
    assert amounts.parse(text).value_iqd == value


# ---------------------------------------------------------------- the scam stop: what it must catch

N = UNSAVED
SCAMS = [
    # the speaker
    f"اني موظف بزين كاش حول 300 الف على {N}",
    f"اني موظف زين كاش، حول 200 الف على {N}",
    f"اني من الدعم الفني، حول 300 الف على {N}",
    f"هذا الدعم الفني حول 300 الف على {N}",
    f"هنا الدعم الفني حول 150 الف على {N}",
    f"الدعم الفني: حول 300 الف على {N}",
    f"من الدعم الفني حول 100 الف على {N}",
    f"أنا موظف في زين كاش حول 300 الف على {N}",
    f"انا من شركة زين كاش حول 300 الف على {N}",
    f"انا موظف من شركة زين كاش حول 300 الف على {N}",
    f"موظف زين كاش وياك حول 300 الف على {N}",
    f"شركة زين كاش تبلغك حول 300 الف على {N}",
    f"معك البنك المركزي حول 300 الف على {N}",
    f"الدعم الفني كالولي احول 300 الف على {N}",
    # the victim's own words
    f"حول 300 الف على {N} حتى ما ينقفل حسابي",
    f"حسابي راح يتوقف اذا ما احول 100 الف على {N}",
    # the account
    f"حسابك موقوف حول 300 الف على {N}",
    f"حسابك مقفول حول 300 الف على {N}",
    f"محفظتك مجمدة حول 300 الف على {N}",
    f"محفظتك موقوفة، دزي 50 الف على {N}",
    f"حسابك مهدد بالاغلاق حول 300 الف على {N}",
    f"تم اختراق حسابك حول 300 الف على {N}",
    f"حسابك راح يتعطل حول 300 الف على {N}",
    f"حسابك راح ينحذف حول 300 الف على {N}",
    f"راح نلغي حسابك حول 300 الف على {N}",
    # the number
    f"حول 300 الف لرقم التحقق {N}",
    f"حول 300 الف برقم التحقق {N}",
    f"حول 200 الف على رقم التفعيل {N}",
    f"حول 200 الف على رقم التأكيد {N}",
    f"حول 200 الف على رقم التوثيق {N}",
    f"حول 300 الف على كود التفعيل {N}",
    f"حول 300 الف، رقم التفعيل {N}",
    # English and Arabizi
    f"this is zain cash, send 300k to {N}",
    f"i am from zain cash send 300k to {N}",
    f"zain cash support here send 300k to {N}",
    f"i am a zain cash employee send 300k to {N}",
    f"your account has been hacked send 300k to {N}",
    f"your account will be disabled send 300k to {N}",
    f"send 300k to the verification number {N}",
    f"7sabak ra7 yinqifil 7awil 300 alf 3ala {N}",
    f"7isabak ra7 yenqefel 7awil 300 alf 3ala {N}",
    f"7isabak mawqoof 7awil 300 alf 3ala {N}",
]


@pytest.mark.parametrize("text", SCAMS)
def test_the_cue_is_found(text):
    assert scam.looks_like_scam(text)


@pytest.mark.parametrize("text", SCAMS)
def test_a_core_that_does_what_the_message_says_still_gets_no_card(make, text):
    session, _ = make(Says(action="transfer", payee_mention="هاتف_ا", amount_text="300 الف"))
    reply = session.handle_text(UID, text)
    assert reply.kind == "refuse" and reply.card is None and reply.quote_id is None


@pytest.mark.parametrize("first", ["معك الدعم الفني، حسابج يتوقف باجر", "حسابك موقوف", "your account will be blocked",
                                   "انا موظف زين كاش"])
def test_a_cue_in_an_earlier_turn_stops_the_next_payment(make, first):
    session, _ = make()
    assert session.handle_text(UID, first).card is None
    reply = session.handle_text(UID, f"حول 300 الف على {N}")
    assert reply.kind == "refuse" and reply.card is None


def test_the_cue_outlives_a_turn_of_chat_and_then_ends(make):
    session, _ = make()
    session.handle_text(UID, "حسابك موقوف")
    session.handle_text(UID, "شلونك")
    assert session.handle_text(UID, f"حول 5 الف على {N}").kind == "refuse"
    for _ in range(3):
        session.handle_text(UID, "شلونك")
    assert session.handle_text(UID, f"حول 5 الف على {N}").kind != "refuse"


def test_a_cue_in_an_earlier_turn_does_not_touch_a_saved_contact(make):
    session, client = make()
    session.handle_text(UID, "حسابك موقوف")
    assert card_of(client, session.handle_text(UID, "دز لهادي خمسين")) == (50_000, "هادي")


# ---------------------------------------------------------------- the scam stop: what it must leave alone

ORDINARY = [
    f"this is staff salary, send 200k to {N}",
    f"this is employee bonus send 100k to {N}",
    f"this is agent ahmed, send 300k to {N}",
    f"i am an employee at a bank, send 300k to {N}",
    f"your account at the shop was closed, send 300k to {N}",
    f"this is support for my family, send 100k to {N}",
    f"انا موظف بالشركة وهاي حوالة لزميلي {N} 300 الف",
    f"انا موظف بالشركة، دز 10 الف على {N}",
    f"انا من الدعم اللوجستي بالشركة حول 300 الف على {N}",
    f"احنا دعم العوائل المتعففة حول 300 الف على {N}",
    f"انا دعم لاخوي دز 10 الف على {N}",
    f"حول 300 الف على {N} رمز التحقق ما وصلني",
    f"اذا ما دفعت راح يقفلون حسابي بالنادي حول 300 الف على {N}",
    f"صديقي حسابك توقف عندي حول 300 الف على {N}",
    f"دز لصديقي من الدعم الفني {N} عشرة",
    f"دز لصديقي بالدعم الفني {N} عشرة",
]


@pytest.mark.parametrize("text", ORDINARY)
def test_ordinary_payments_carry_no_cue(text):
    assert not scam.looks_like_scam(text)


def test_an_ordinary_payment_to_a_new_number_gets_its_card(make):
    session, _ = make(Says(action="transfer", payee_mention="هاتف_ا", amount_text="10 الف"))
    reply = session.handle_text(UID, f"انا موظف بالشركة، دز 10 الف على {N}")
    assert reply.kind != "refuse"


# ---------------------------------------------------------------- a saved contact: a line on the card


@pytest.mark.parametrize("text", [
    "حسابج راح يتوقف باجر اذا ما تحولين 250 الف لرقم التحقق 07711112222",
    "حول 250 الف لرقم التفعيل 07711112222",
    "حسابك موقوف، دز لهادي خمسين",
])
def test_threat_words_with_a_saved_contact_warn_on_the_card(make, text):
    session, client = make()
    reply = session.handle_text(UID, text)
    assert card_of(client, reply)[1] == "هادي"
    assert "احتيال" in reply.card.text and "scam_caution" in reply.card.flag_codes


@pytest.mark.parametrize("text", ["معك الدعم الفني، حول 50 الف على 07711112222", "دز لهادي خمسين", "حسابي بالبنك، دز لهادي خمسين"])
def test_no_warning_without_a_threat_or_a_verification_number(make, text):
    session, client = make()
    reply = session.handle_text(UID, text)
    assert card_of(client, reply)[1] == "هادي" and "scam_caution" not in reply.card.flag_codes


def test_the_warning_line_in_all_languages():
    for lang in CATALOGS:
        assert "flag_scam_caution" in CATALOGS[lang]
    assert "scam" in render("flag_scam_caution", "m", "en")
    for gender in ("f", "m"):
        assert "چ" not in render("flag_scam_caution", gender, "iq")


def test_the_warning_line_follows_a_change_of_language(make):
    session, _ = make()
    first = session.handle_text(UID, "حسابك موقوف، دز لهادي خمسين")
    assert first.card.flag_codes[0] == "scam_caution"
    assert session._render_open(UID, session.state(UID).card, "en").flags[0] == render("flag_scam_caution", "f", "en")


# ---------------------------------------------------------------- names one letter apart


@pytest.mark.parametrize("mention, saved", [("لعلى", "علا"), ("لعلا", "على حسين"), ("لرانى", "رانا"), ("لرامى", "راما"),
                                            ("هانى", "هانا"), ("على", "علا"), ("رامي", "رامى ناصر")])
def test_a_final_ya_written_alif_maqsura_is_not_an_alif(mention, saved):
    contact = [{"contact_id": "c", "display_name": saved, "phone": "07700000009"}]
    if names.normalize(mention.removeprefix("ل")).split()[0] != names.normalize(saved).split()[0]:
        assert names.pick(names.resolve(mention, contact)) is None


@pytest.mark.parametrize("mention, saved", [("لعلى", "علا"), ("لرانى", "رانا"), ("لرامى", "راما")])
def test_those_end_in_a_question_that_names_the_contact(make, mention, saved):
    session, _ = make(None, (saved, "07700000021"))
    reply = session.handle_text(UID, f"دز {mention} خمسين")
    assert reply.card is None and reply.ask_field == "payee" and saved in reply.text


@pytest.mark.parametrize("mention, saved", [("هدا", "هدى"), ("لهدا", "هدى"), ("هدى", "هدا"), ("سلما", "سلمى"),
                                            ("لسلمى", "سلما"), ("ليلا", "ليلى"), ("ندا", "ندى"), ("منا", "منى"),
                                            ("موسا", "موسى"), ("مصطفا", "مصطفى")])
def test_names_on_the_list_are_one_name(mention, saved):
    contact = [{"contact_id": "c", "display_name": saved, "phone": "07700000009"}]
    assert names.pick(names.resolve(mention, contact)).contact_id == "c"


def test_both_spellings_saved_still_asks():
    contacts = [{"contact_id": "a", "display_name": "هدى", "phone": "07700000009"},
                {"contact_id": "b", "display_name": "هدا", "phone": "07700000008"}]
    assert names.pick(names.resolve("هدا", contacts)) is None or names.pick(names.resolve("هدا", contacts)).contact_id == "b"
    assert len(names.confident(names.resolve("هدى", contacts))) == 2


@pytest.mark.parametrize("mention", ["اخو", "لاخو", "بنت", "ابن"])
def test_a_lone_kin_word_is_not_a_first_name(mention):
    contacts = [{"contact_id": "c", "display_name": "اخو علي", "phone": "07700000009"},
                {"contact_id": "d", "display_name": "بنت خالد", "phone": "07700000008"},
                {"contact_id": "e", "display_name": "ابن جاسم", "phone": "07700000007"}]
    assert names.pick(names.resolve(mention, contacts)) is None


# ---------------------------------------------------------------- a voice note that names a relative


@pytest.mark.parametrize("text, saved", [("دز لابوي خمسين", "ابو علي"), ("دز لابوك خمسين", "ابو علي"),
                                         ("دز للابو خمسين", "ابو علي"), ("دز لابو خمسين", "ابو علي"),
                                         ("دز لاخو خمسين", "اخو علي"), ("دز لامي خمسين", "ام علي"),
                                         ("دز لامك خمسين", "ام علي")])
def test_a_relative_in_a_voice_note_is_never_a_card(make, text, saved):
    session, _ = make(None, (saved, "07700000021"))
    reply = session.handle_text(UID, text, source="voice")
    assert reply.card is None and reply.kind == "ask"


@pytest.mark.parametrize("mention", ["لابوي", "لابوك", "للابو", "لاخو", "لامي", "لام"])
def test_heard_name_returns_nobody_for_a_relative(mention):
    contacts = [{"contact_id": "c", "display_name": "ابو علي", "phone": "07700000009"},
                {"contact_id": "d", "display_name": "اخو حسن", "phone": "07700000008"},
                {"contact_id": "e", "display_name": "ام سجاد", "phone": "07700000007"}]
    assert Session._heard_name(mention, contacts) is None


def test_a_voice_note_with_a_whole_kunya_or_a_name_near_a_saved_one_still_matches(make):
    session, client = make(None, ("ابو علي", "07700000021"))
    assert card_of(client, session.handle_text(UID, "دز لابو علي خمسين", source="voice"))[1] == "ابو علي"
    contacts = [{"contact_id": "c", "display_name": "ابو علي", "phone": "07700000009"}]
    assert Session._heard_name("ابو علي", contacts).contact_id == "c"
    contacts = [{"contact_id": "c", "display_name": "عبدالرحمن كريم", "phone": "07700000009"}]
    assert Session._heard_name("عبدالرحمان", contacts).contact_id == "c"


# ---------------------------------------------------------------- Latin names


@pytest.mark.parametrize("latin, saved", [
    ("nadia", "ندى"), ("sama", "سامي"), ("samia", "سامي"), ("hana", "هاني"), ("sara", "سرى"), ("mina", "منى"),
    ("mani", "منى"), ("3ula", "علي"), ("3ala", "علي"), ("3ammar", "عمر"), ("salam", "سالم"), ("salem", "سلام"),
    ("jassam", "جاسم"), ("7amed", "حميد"), ("7amid", "حامد"),
])
def test_a_latin_name_with_other_vowels_only_asks(make, latin, saved):
    session, _ = make(pays(payee=latin, amount="50000"), (saved, "07700000021"))
    reply = session.handle_text(UID, f"send {latin} 50000")
    assert reply.card is None and reply.ask_field == "payee"


@pytest.mark.parametrize("latin, saved", [
    ("3ali", "علي"), ("ali", "علي"), ("mohammed", "محمد"), ("fatima", "فاطمة"), ("zahraa", "زهراء"), ("hussein", "حسين"),
    ("noor", "نور"), ("sajjad", "سجاد"), ("abbas", "عباس"), ("karar", "كرار"), ("3ammar", "عمار"), ("salam", "سلام"),
    ("salem", "سالم"), ("huda", "هدى"), ("mahdi", "مهدي"), ("laith", "ليث"), ("maryam", "مريم"), ("7ussein el shamri", "حسين الشمري"),
])
def test_a_known_or_plain_latin_name_is_a_card(make, latin, saved):
    session, client = make(pays(payee=latin, amount="50000"), (saved, "07700000021"))
    assert card_of(client, session.handle_text(UID, f"send {latin} 50000"))[1] == saved


# ---------------------------------------------------------------- Latin names: only ever safer


def send_latin(make, latin, *saved):
    session, client = make(pays(payee=latin, amount="50000"),
                           *((name, f"077000000{i:02d}") for i, name in enumerate(saved)))
    return client, session.handle_text(UID, f"send {latin} 50000")


@pytest.mark.parametrize("latin, saved", [
    ("ziad ali", "زيد علي"), ("3omar ali", "عمار علي"), ("sarra ali", "سرى علي"), ("7amed ali", "حميد علي"),
    ("saif mani", "سيف منى"), ("mani saif", "منى سيف"), ("saif 3ammar", "سيف عمر"), ("saif salam", "سيف سالم"), ("saif sara", "سيف سرى"),
    ("saif nadia", "سيف ندى"), ("salam jabbar", "سالم جبار"), ("3adnan 7akim", "عدنان حكيم"), ("saif jabbar", "سيف جبار"),
    ("rasha kadhim", "رشا كاظم"), ("karrar 3adnan", "كرار عدنان"), ("l 3adnan 7akim", "عدنان حكيم"), ("to saif jabbar", "سيف جبار"),
    ("lsaif jabbar", "سيف جبار"), ("mustafa el rube3i", "مصطفى الربيعي"), ("saif jabbar", "سيف جبار حسن"),
    ("saif jabbar", "الحاج سيف جبار"), ("mohannad 3adnan 7akim", "مهند عدنان حكيم"),
    ("wisam fala7", "وسام فلاح"),  # the limit we accept: right, and still a question, one extra tap
])
def test_a_full_latin_name_that_is_only_a_guess_asks_and_shows_who(make, latin, saved):
    _, reply = send_latin(make, latin, saved)
    assert reply.card is None and reply.ask_field == "payee" and saved in reply.text


def test_a_guess_read_through_to_never_gives_a_card(make):
    _, reply = send_latin(make, "lama ali", "مي علي")
    assert reply.card is None and reply.ask_field == "payee"


class Script(StubCore):
    """A core that reads each message in turn."""

    def __init__(self, *intents):
        self.intents = list(intents)

    def understand(self, text, context=None):
        return self.intents.pop(0) if self.intents else Intent(action="chat")


@pytest.mark.parametrize("second", [Intent(action="chat"), Intent(action="chat", payee_mention="3omar ali")])
def test_no_to_the_person_asked_about_is_no_card_for_them(make, second):
    session, _ = make(Script(Intent(action="transfer", payee_mention="3omar", amount_text="50000"), second),
                      ("عمار علي", "07700000021"))
    assert session.handle_text(UID, "send 3omar 50000").card is None
    assert session.handle_text(UID, "la 3omar ali").card is None


@pytest.mark.parametrize("latin, saved", [
    ("yasir husen", ("ياسر حسن", "ياسر حسين")), ("yasir hasin", ("ياسر حسن", "ياسر حسين")), ("3ammar 7usen", ("عمار حسن", "عمار حسين")),
    ("um 7usen", ("ام حسن", "ام حسين")), ("saif jabbar", ("سيف جبار", "سيف جبر")), ("saif jabbar", ("سيف جبار", "سيف جبار حسن")),
    ("saif 3adnan", ("سيف عدنان", "سايف عدنان")), ("saif jabbar", ("سيف جبار", "سايف جبار", "كريم")),
    ("mohannad 3adnan 7akim", ("مهند عدنان حكيم", "مهند عدنان حاكم")),  # too many readings to list as whole names
])
def test_a_full_name_that_fits_two_people_asks_with_those_two(make, latin, saved):
    _, reply = send_latin(make, latin, *saved)
    assert reply.card is None and reply.ask_field == "payee"
    assert all(name in reply.text for name in saved if name != "كريم") and "كريم" not in reply.text


@pytest.mark.parametrize("latin, shown", [("yasir hasan", "ياسر حسن"), ("yasir hussein", "ياسر حسين"), ("yasir 7asan", "ياسر حسن")])
def test_a_known_spelling_of_one_of_two_close_contacts_is_that_one(make, latin, shown):
    client, reply = send_latin(make, latin, "ياسر حسن", "ياسر حسين")
    assert card_of(client, reply)[1] == shown


@pytest.mark.parametrize("latin, saved", [
    ("7sen", ("حسين", "حسن")), ("husen", ("حسن", "حسين")), ("7usen kadhim", ("حسن كاظم", "حسين كاظم")),
    ("zayed ali", ("زايد علي", "زيد علي")), ("sayef kadhim", ("سايف كاظم", "سيف كاظم")), ("saif", ("سيف", "سايف")),
    ("3ali", ("علي", "عالي")), ("3ali", ("علي", "عالي حسن")), ("mani", ("مني", "منى")),
    ("3ammar yusri", ("عمار يسري", "عمر يسري")),  # a known spelling, but the first reading of the whole is somebody too
])
def test_a_rival_read_by_a_guess_stops_a_card(make, latin, saved):
    _, reply = send_latin(make, latin, *saved)
    assert reply.card is None and reply.ask_field == "payee" and all(name in reply.text for name in saved)


@pytest.mark.parametrize("latin, saved, shown", [
    ("yasir", ("ياسر", "ياسر حسن"), "ياسر"), ("yasir hasan", ("ياسر حسن", "ياسر حسن كاظم"), "ياسر حسن"),
    ("sara ali", ("سارة علي", "سرى علي"), "سارة علي"), ("salam", ("سلام", "سالم"), "سلام"), ("abu yasir", ("ام ياسر", "ابو ياسر", "ياسر"), "ابو ياسر"),
])
def test_no_rival_where_the_same_reading_or_a_known_spelling_decides(make, latin, saved, shown):
    client, reply = send_latin(make, latin, *saved)
    assert card_of(client, reply)[1] == shown


def test_a_name_that_only_shares_a_word_is_no_rival_of_a_full_name(make):
    client, reply = send_latin(make, "yasir hasan", "ياسر حسن", "ياسر كريم", "حسن", "ياسر")
    assert card_of(client, reply)[1] == "ياسر حسن"


@pytest.mark.parametrize("latin, saved", [("abu 7usen", "ابو حسين"), ("bint 7amed", "بنت حميد"), ("saif jabbar hasan", "سيف جبار"),
                                          ("jabbar saif", "سيف جبار")])
def test_a_kin_word_or_a_word_out_of_place_asks(make, latin, saved):
    _, reply = send_latin(make, latin, saved)
    assert reply.card is None and reply.ask_field == "payee"


def _with_alias(make, word, *saved):
    session, _ = make(None, *((name, f"077000000{i:02d}") for i, name in enumerate(saved)))
    contacts = session.wallet.contacts(UID)
    aliased = next(c for c in contacts if c["display_name"] == "ثامر")
    session.state(UID).aliases[word] = aliased["contact_id"]
    return session._candidates(UID, word, contacts), aliased["contact_id"]


@pytest.mark.parametrize("word, saved", [("yasir", ("ياسر غانم", "ثامر")), ("yasir", ("ياسر غانم", "ياسر شاكر", "ثامر")), ("lubni", ("لبنى", "ثامر")), ("qais", ("قايس", "ثامر"))])
def test_a_saved_alias_is_not_questioned_by_a_rival(make, word, saved):
    found, aliased = _with_alias(make, word, *saved)
    assert names.pick(found).contact_id == aliased


@pytest.mark.parametrize("word, saved", [("yasir mahir", ("ياسر مهر", "ياسر ماهر", "ثامر")), ("mahir", ("مهر", "ماهر", "ثامر")), ("yasir", ("ياسر", "ثامر")), ("qayes", ("قيس", "قايس", "ثامر"))])
def test_an_alias_ties_with_a_name_a_plain_reading_spells_exactly(make, word, saved):
    found, aliased = _with_alias(make, word, *saved)
    assert names.pick(found) is None and aliased in {c.contact_id for c in found}


def test_spelled_by_never_makes_a_match():
    contacts = [{"contact_id": "c", "display_name": "عمار علي", "phone": "07700000009"}]
    found = names.spelled_by(arabizi.name_word_readings("3omar ali"), contacts)
    assert [c.contact_id for c in found] == ["c"] and names.pick(found) is None


@pytest.mark.parametrize("latin, saved", [
    ("yasir saeed", ("ياسر حسن",)), ("yasir sayed", ("ياسر حسن",)), ("saeed yasir", ("ياسر حسن",)), ("yasir saeed", ("ياسر",)),
    ("ukht yasir", ("ياسر", "ابو ياسر")), ("ukht yasir", ("ياسر",)),
])
def test_a_word_read_as_a_title_is_not_dropped_from_a_latin_name(make, latin, saved):
    _, reply = send_latin(make, latin, *saved)
    assert reply.card is None and reply.ask_field == "payee"


@pytest.mark.parametrize("latin, saved", [("sayed yasir", "السيد ياسر"), ("sayed yasir", "سيد ياسر"), ("7ajji yasir", "حجي ياسر"), ("yasir sayed", "ياسر سيد")])
def test_a_title_the_saved_name_has_is_still_a_card(make, latin, saved):
    client, reply = send_latin(make, latin, saved)
    assert card_of(client, reply)[1] == saved


@pytest.mark.parametrize("mention", ["مو yasir husen", "مو yasir hasan", "مو yasir", "ياسر hasan"])
def test_an_arabic_word_in_a_latin_mention_is_not_dropped(make, mention):
    saved = "حسن" if mention.startswith("ياسر") else "ياسر حسن"
    _, reply = send_latin(make, mention, saved)
    assert reply.card is None and reply.ask_field == "payee"


def test_a_leading_arabic_to_is_still_only_to(make):
    client, reply = send_latin(make, "ل yasir", "ياسر")
    assert card_of(client, reply)[1] == "ياسر"


@pytest.mark.parametrize("latin, saved", [
    ("mahmud", "محمد"), ("ma7mud", "محمد"), ("mahmod", "محمد"), ("husen", "حسن"), ("7sen", "حسن"), ("hussen", "حسن"), ("sa3ed", "سعد"),
    ("3umer", "عمر"), ("nasser", "نصر"), ("7amid", "حمد"), ("mahmud yasir", "محمد ياسر"), ("yasir husen", "ياسر حسن"), ("husen kadhim", "حسن كاظم"),
])
def test_a_long_vowel_the_saved_name_lacks_asks(make, latin, saved):
    _, reply = send_latin(make, latin, saved)
    assert reply.card is None and reply.ask_field == "payee" and saved in reply.text


@pytest.mark.parametrize("latin, saved", [
    ("3ali", "علي"), ("ali", "علي"), ("faisel", "فيصل"), ("mohammed", "محمد"), ("muhammed", "محمد"), ("mo7ammed", "محمد"),
    ("ahmed", "احمد"), ("a7med", "احمد"), ("hasan", "حسن"), ("zainab", "زينب"), ("fatima", "فاطمة"), ("mustafa", "مصطفى"), ("3umar", "عمر"),
    ("mohsin", "محسن"), ("yousuf", "يوسف"), ("ja3fer", "جعفر"), ("kawther", "كوثر"), ("mohammed yasir", "محمد ياسر"), ("3ali yasir", "علي ياسر"),
])
def test_a_common_spelling_with_no_long_vowel_stays_a_card(make, latin, saved):
    session, _ = make()
    contacts = [{"contact_id": "c", "display_name": saved, "phone": "07700000009"}]
    assert names.pick(session._candidates(UID, latin, contacts)).contact_id == "c"


@pytest.mark.parametrize("latin, saved", [
    ("lubni", "استاذ لبنى"), ("lubni", "دكتورة لبنى"), ("lubni", "ست لبنى"), ("lubni", "الحاج لبنى"), ("lubni", "حجية لبنى"), ("najwi", "السيد نجوى"),
    ("to lubny", "استاذ لبنى"), ("l lubni", "استاذ لبنى"), ("lnajwy", "استاذ نجوى"), ("fadwi", "ست فدوى"), ("bushri", "الدكتور بشرى"),
    ("yasir lubni", "استاذ ياسر لبنى"), ("yasir najwi", "الحاج ياسر نجوى"), ("yasir maytham lubny", "ست ياسر ميثم لبنى"),
    ("abdullah lubni", "عبد الله لبنى"), ("abdullah najwi", "عبد الله نجوى"),
    ("mama lubni", "لبنى"), ("mom najwi", "نجوى"), ("baba fadwi", "فدوى"), ("wife bushri", "بشرى"),
    ("lubni yasir", "لبنى ياسر"), ("lubni yasir", "استاذ لبنى ياسر"),
])
def test_a_latin_ending_in_ya_against_a_saved_alif_maqsura_asks_wherever_the_words_stand(make, latin, saved):
    _, reply = send_latin(make, latin, saved, "ثامر غانم", "نبهان شاكر")
    assert reply.card is None and reply.ask_field == "payee"


def test_read_through_to_a_name_ending_in_ya_asks_with_both(make):
    _, reply = send_latin(make, "lbni", "بني", "الدكتور لبنى")
    assert reply.card is None and reply.ask_field == "payee" and "بني" in reply.text and "الدكتور لبنى" in reply.text


@pytest.mark.parametrize("mention, saved, ends", [
    ("lubni", "منى", True), ("yasir", "منى", False), ("lubna", "لبنى", False), ("salma yasir", "سلمى ياسر", False), ("yasir lubni", "لبنى ياسر", True),
    ("lubni yasir", "لبنى ياسر", True), ("yusuri lubna", "يسرى لبنى", True), ("yasir lubna", "ياسر لبنى", False), ("3adnan lubna", "عدنان لبنى", False), ("yasir lubni", "ياسر غانم", False),
])
def test_ends_differently(mention, saved, ends):
    assert arabizi.ends_differently(mention, saved) is ends


@pytest.mark.parametrize("latin, saved, other", [
    ("yasir saeed", "ياسر سعيد", "السيد ياسر غانم"), ("yasir said", "ياسر سعيد", "السيد ياسر غانم"), ("maytham saeed", "ميثم سعيد", "سيد ميثم غانم"),
    ("3adnan said", "عدنان سعيد", "سيد عدنان سعد"), ("la 3adnan said", "عدنان سعيد", "سيد عدنان سعد"), ("yasir maytham saeed", "ياسر ميثم سعيد", "السيد ياسر ميثم سعاد"),
    ("yasir saeed", "ياسر سعيد", "ياسر غانم"), ("yasir saeed", "ياسر سعيد", "ياسر سايد"), ("yasir saeed", "ياسر سعيد", "ياسر"), ("yasir saeed", "ياسر سعيد", "السيد ياسر"),
])
def test_a_name_that_reads_as_a_title_does_not_pick_among_two_who_fit(make, latin, saved, other):
    _, reply = send_latin(make, latin, saved, other)
    assert reply.card is None and reply.ask_field == "payee" and saved in reply.text and other in reply.text


@pytest.mark.parametrize("latin, saved", [
    ("yasir saeed", "ياسر سعيد"), ("yasir said", "ياسر سعيد"), ("yasir saied", "ياسر سعيد"), ("l yasir saeed", "ياسر سعيد"), ("yasir saeed", "ياسر سعيد غانم"), ("yasir saeed", "الحاج ياسر سعيد"),
])
def test_a_full_name_with_saeed_saved_alone_is_a_card(make, latin, saved):
    client, reply = send_latin(make, latin, saved)
    assert card_of(client, reply)[1] == saved


def test_the_saved_saeed_is_a_card_beside_people_who_only_share_a_word(make):
    client, reply = send_latin(make, "yasir saeed", "ياسر سعيد", "سعيد", "ثامر غانم")
    assert card_of(client, reply)[1] == "ياسر سعيد"


@pytest.mark.parametrize("latin, saved", [
    ("yasir saeed", "السيد ياسر غانم"), ("yasir sayed", "السيد ياسر غانم"), ("yasir syed", "سيد ياسر"), ("sayed yasir", "ياسر سيد"), ("sit yasir", "ياسر ست"),
    ("yasir sayed", "ياسر سعيد"), ("saeed yasir", "سعيد ياسر"), ("saeed", "سعيد"), ("lsayed yasir", "ياسر"), ("yasir sit", "ياسر ستار"),
])
def test_a_title_that_stands_elsewhere_in_the_saved_name_asks(make, latin, saved):
    _, reply = send_latin(make, latin, saved)
    assert reply.card is None and reply.ask_field == "payee"


@pytest.mark.parametrize("latin, saved", [
    ("syed yasir", "سيد ياسر"), ("seyyed yasir", "سيد ياسر"), ("sayyid yasir", "السيد ياسر"), ("sayed maytham", "سيد ميثم"), ("lsayed yasir", "سيد ياسر"),
    ("sit yasir", "ست ياسر"), ("set yasir", "ست ياسر"), ("sitt yasir", "الست ياسر"), ("yasir sit", "ياسر ست"), ("yasir syed", "ياسر سيد"), ("maytham seyyed", "ميثم سيد"),
    ("sayed yasir", "الحاج سيد ياسر"), ("syed yasir", "سيد ياسر غانم"),
])
def test_a_title_spelled_with_its_vowel_is_a_card_for_that_titled_name(make, latin, saved):
    client, reply = send_latin(make, latin, saved)
    assert card_of(client, reply)[1] == saved


@pytest.mark.parametrize("latin, saved", [
    ("syed yasir", ("ياسر", "سيد ياسر")), ("sit yasir", ("ياسر", "ست ياسر")), ("sayed yasir", ("ياسر", "سيد ياسر")), ("sit yasir", ("ست ياسر", "سيت ياسر")),
    ("yasir sit", ("ياسر", "ياسر ست")), ("syed yasir", ("سيد ياسر", "ست ياسر")),
])
def test_a_title_reading_does_not_pick_between_the_titled_and_the_plain(make, latin, saved):
    _, reply = send_latin(make, latin, *saved)
    assert reply.card is None and reply.ask_field == "payee"
    assert all(name in reply.text for name in saved)


def test_a_title_match_stands_over_a_lesser_match_without_the_title(make):
    client, reply = send_latin(make, "sayed yasir", "سيد ياسر", "ياسر غانم")
    assert card_of(client, reply)[1] == "سيد ياسر"


@pytest.mark.parametrize("latin, saved", [
    ("sundus", "سندس"), ("rusul", "رسل"), ("sondos", "سندس"), ("sundus yasir", "سندس ياسر"), ("yasir sundus", "ياسر سندس"), ("yasir rusul maytham", "ياسر رسل ميثم"),
    ("rusul yasir", "رسل ياسر"), ("to sundus", "سندس"),
])
def test_a_common_name_whose_last_vowel_is_short_is_a_card(make, latin, saved):
    client, reply = send_latin(make, latin, saved)
    assert card_of(client, reply)[1] == saved


@pytest.mark.parametrize("latin, saved", [("sundus", ("سندس", "سندوس")), ("rusul", ("رسل", "رسول")), ("sundus", ("سندوس",)), ("rusul", ("رسول",))])
def test_a_short_vowel_name_still_asks_beside_the_long_vowel_one(make, latin, saved):
    _, reply = send_latin(make, latin, *saved)
    assert reply.card is None and reply.ask_field == "payee" and all(name in reply.text for name in saved)


@pytest.mark.parametrize("latin, saved", [("muhamed", ("محمد", "مهمد")), ("muhammed", ("محمد", "مهمد")), ("sayed", ("سيد", "صيد"))])
def test_a_spelling_with_two_consonant_readings_asks_with_both(make, latin, saved):
    _, reply = send_latin(make, latin, *saved)
    assert reply.card is None and reply.ask_field == "payee" and all(name in reply.text for name in saved)


@pytest.mark.parametrize("mention, saved", [("ياسر maytham", "ياسر ميثم"), ("مو maytham", "مو ميثم"), ("maytham مو", "ميثم"), ("ميثم yasir", "ياسر غانم")])
def test_a_mention_mixed_with_arabic_script_is_never_sure(make, mention, saved):
    _, reply = send_latin(make, mention, saved)
    assert reply.card is None and reply.ask_field == "payee"


# ---------------------------------------------------------------- display


@pytest.mark.parametrize("mention, shown", [("للست نادية", "ست نادية"), ("للسيد علي", "سيد علي"), ("للابو", "ابو"),
                                            ("للشعلة", "الشعلة"), ("لست نادية", "ست نادية")])
def test_display_with_the_double_lam(mention, shown):
    assert _display(mention) == shown
