"""Regressions from the audit of 2026-11-06: amounts next to a scale, names, the fake-support scam,
fee wording, the word «الفاتورة», the stand-in core's reading, and the eval's allowed_outcomes."""

from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from agent.session import Session, _display
from core.interface import Intent
from core.stub import StubCore
from eval import run_eval
from payments import amounts, arabizi, names, scam
from payments.messages import CATALOGS, MESSAGES, render
from payments.text import fold
from tests.test_session import PAY, PIN, SEED, UID
from wallet.api import create_app

UNSAVED = "07711119999"


def seed_with(*contacts: tuple[str, str], balance: int | None = None) -> dict:
    seed = copy.deepcopy(SEED)
    for i, (name, phone) in enumerate(contacts):
        seed["contacts"].append({"contact_id": f"x{i}", "user_id": UID, "display_name": name, "phone": phone})
    if balance is not None:
        seed["users"][0]["balance"] = balance
    return seed


@pytest.fixture
def make():
    """A session over its own wallet: make(core, *contacts, balance=...) -> (session, client)."""
    clients = []

    def build(core=None, *contacts, balance=None):
        client = TestClient(create_app(":memory:", seed=seed_with(*contacts, balance=balance)))
        clients.append(client)
        return Session(wallet=client, core=core or StubCore()), client

    yield build
    for client in clients:
        client.close()


class Says(StubCore):
    """A core that returns one fixed reading, the way another core might point at the words."""

    def __init__(self, **intent):
        self.intent = intent

    def understand(self, text, context=None):
        return Intent(**self.intent)


def amount_of(client, reply) -> int:
    return client.get(f"/quotes/{reply.quote_id}").json()["amount"]


def target_of(client, reply) -> dict:
    return client.get(f"/quotes/{reply.quote_id}").json()["target"]


# ---------------------------------------------------------------- A. amounts next to a scale


@pytest.mark.parametrize("text, value", [
    ("الربع مليون", 250_000), ("النص مليون", 500_000), ("الخمسين الف", 50_000), ("الميتين الف", 200_000),
    ("بالخمسين الف", 50_000), ("العشر تلاف", 10_000), ("الخمس مية الف", 500_000), ("الربع مليون دينار", 250_000),
])
def test_article_on_a_number_before_a_scale_is_its_value(text, value):
    assert amounts.parse(text).value_iqd == value


@pytest.mark.parametrize("text", [
    "ثلثين مليون", "تلثين مليون", "ثلث مليون", "الثلثين مليون", "كم مليون", "كام مليون", "جم مليون", "كم الف",
    "كم مية الف", "عدة ملايين", "مليون وشوية", "مية الف وكسور", "مليون وكم", "مليون والنص", "مليون والربع",
    "خمسن الف", "خمسيين الف", "ميتن الف", "بخمسن الف", "الست مليون", "الثمن مليون",
])
def test_a_vague_word_at_a_scale_asks_and_never_shows_the_bare_scale(text):
    found = amounts.parse(text)
    assert found.needs_question and found.value_iqd is None, text


@pytest.mark.parametrize("text, value", [
    ("دز لعلي مليون", 1_000_000), ("دز لعلي الحلاق مليون", 1_000_000), ("حول لبنين مليون", 1_000_000),
    ("دز بنين مليون", 1_000_000), ("حول علي الف", 1_000), ("دز فد مليون", 1_000_000), ("تلث مية الف", 300_000),
    ("ربع مليون", 250_000), ("مليون ونص", 1_500_000), ("مية الف", 100_000),
])
def test_ordinary_words_next_to_a_scale_change_nothing(text, value):
    assert amounts.parse(text).value_iqd == value


@pytest.mark.parametrize("text", ["الخمسين", "النص", "الربع", "ادفع الخمسين"])
def test_article_on_a_number_with_no_scale_is_no_amount(text):
    assert amounts.parse(text).reason == amounts.NO_AMOUNT


@pytest.mark.parametrize("text, value", [("half a million", 500_000), ("half million", 500_000),
                                         ("quarter million", 250_000), ("el rob3 malyon", 250_000),
                                         ("el nus malyon", 500_000)])
def test_latin_fractions(text, value):
    assert amounts.parse(arabizi.to_arabic(text)).value_iqd == value


@pytest.mark.parametrize("text", ["a few thousand", "few million", "several million", "kam malyon"])
def test_latin_vague_counts_ask(text):
    assert amounts.parse(arabizi.to_arabic(text)).needs_question


@pytest.mark.parametrize("text", ["دز لهادي كم مليون", "دز لهادي ثلثين مليون", "دز لهادي مليون وشوية"])
def test_a_vague_amount_end_to_end_asks(make, text):
    session, _ = make()
    reply = session.handle_text(UID, text)
    assert reply.card is None and reply.kind == "ask" and reply.ask_field == "amount"


def test_a_core_that_points_only_at_the_scale_still_asks(make):
    session, _ = make(Says(action="transfer", payee_mention="لهادي", amount_text="مليون"))
    reply = session.handle_text(UID, "دز لهادي كم مليون")
    assert reply.card is None and reply.ask_field == "amount"


def test_a_core_that_points_at_the_scale_of_a_fraction_with_article_asks(make):
    session, _ = make(Says(action="transfer", payee_mention="لهادي", amount_text="مليون"))
    reply = session.handle_text(UID, "دز لهادي الربع مليون")
    assert reply.card is None and reply.ask_field == "amount"


def test_article_amount_end_to_end(make):
    session, client = make()
    reply = session.handle_text(UID, "دز لهادي الربع مليون")
    assert reply.card is not None and amount_of(client, reply) == 250_000


# ---------------------------------------------------------------- D. names

CONTACTS = [{"contact_id": "h", "display_name": "هدى", "phone": "07700000001"},
            {"contact_id": "a", "display_name": "علي", "phone": "07700000002"},
            {"contact_id": "s", "display_name": "ام سجاد", "phone": "07700000003"},
            {"contact_id": "b", "display_name": "ابو علي", "phone": "07700000004"},
            {"contact_id": "m", "display_name": "سيد مهدي", "phone": "07700000005"},
            {"contact_id": "l", "display_name": "سلما", "phone": "07700000006"}]


@pytest.mark.parametrize("said_as, cid", [("هدا", "h"), ("لهدا", "h"), ("هدى", "h"), ("هدي", "h"),
                                          ("سلمى", "l"), ("سلما", "l"), ("لسلمى", "l")])
def test_final_alif_and_alif_maqsura_are_one_name(said_as, cid):
    assert names.pick(names.resolve(said_as, CONTACTS)).contact_id == cid


@pytest.mark.parametrize("said_as, saved", [("علا", "علي"), ("علي", "علا"), ("رنا", "رني"), ("ندي", "ندا")])
def test_final_alif_and_final_ya_stay_different_people(said_as, saved):
    contact = [{"contact_id": "c", "display_name": saved, "phone": "07700000009"}]
    assert names.pick(names.resolve(said_as, contact)) is None


@pytest.mark.parametrize("said_as", ["ام", "لام", "ابو", "لابو"])
def test_a_lone_kunya_word_is_not_a_first_name(said_as):
    assert names.pick(names.resolve(said_as, CONTACTS)) is None


def test_a_full_kunya_still_matches():
    assert names.pick(names.resolve("ام سجاد", CONTACTS)).contact_id == "s"
    assert names.pick(names.resolve("لابو علي", CONTACTS)).contact_id == "b"


@pytest.mark.parametrize("said_as", ["السيد مهدي", "للسيد مهدي", "سيد مهدي", "مهدي"])
def test_sayyid_is_a_title(said_as):
    assert names.pick(names.resolve(said_as, CONTACTS)).contact_id == "m"


def test_saved_sayyida_is_a_title_too():
    contact = [{"contact_id": "z", "display_name": "السيدة زهراء", "phone": "07700000010"}]
    assert names.pick(names.resolve("زهراء", contact)).contact_id == "z"


def test_a_lone_kunya_in_a_voice_note_is_not_guessed():
    contacts = [{"contact_id": "s", "display_name": "ام سجاد", "phone": "07700000003"}]
    assert Session._heard_name("لام", contacts) is None


def test_spelling_hada_for_huda_end_to_end(make):
    session, client = make(None, ("هدى", "07700000001"))
    reply = session.handle_text(UID, "حول لهدا تلاثين الف")
    assert reply.card is not None and target_of(client, reply)["display_name"] == "هدى"


# ---------------------------------------------------------------- E. the orchestrator and names


def test_arabizi_saved_name_is_exact(make):
    session, client = make(None, ("حسين الشمري", "07700000011"))
    reply = session.handle_text(UID, "diz l 7ussein el shamri 3ishreen")
    assert reply.card is not None and target_of(client, reply)["display_name"] == "حسين الشمري"
    assert amount_of(client, reply) == 20_000


def test_arabizi_mention_from_a_core_without_the_particle(make):
    core = Says(action="transfer", payee_mention="7ussein el shamri", amount_text="3ishreen")
    session, client = make(core, ("حسين الشمري", "07700000011"))
    reply = session.handle_text(UID, "diz 7ussein el shamri 3ishreen")
    assert reply.card is not None and target_of(client, reply)["display_name"] == "حسين الشمري"


def test_relation_phrase_lists_the_contacts_saved_with_it(make):
    session, client = make(None, ("رضا ابن عمي", "07700000012"), ("عمار ابن عمي", "07700000013"))
    reply = session.handle_text(UID, "دز لابن عمي خمسين")
    assert reply.ask_field == "payee" and "رضا ابن عمي" in reply.text and "عمار ابن عمي" in reply.text
    reply = session.handle_text(UID, "الثاني")
    assert reply.card is not None and target_of(client, reply)["display_name"] == "عمار ابن عمي"
    assert "عمي" not in session.state(UID).aliases  # the answer says who «ابن عمي» is, not who «عمي» is


def test_relation_phrase_with_one_saved_contact_asks_to_confirm(make):
    session, client = make(None, ("رضا ابن عمي", "07700000012"))
    reply = session.handle_text(UID, "دز لابن عمي خمسين")
    assert reply.ask_field == "payee" and "رضا ابن عمي" in reply.text
    reply = session.handle_text(UID, "اي")
    assert reply.card is not None and target_of(client, reply)["display_name"] == "رضا ابن عمي"


def test_a_relation_word_with_no_saved_contact_still_says_so(make):
    session, _ = make()
    reply = session.handle_text(UID, "دز لعمي خمسين")
    assert reply.ask_field == "payee" and "«عمي»" in reply.text
    assert session.state(UID).question.request.relation_word


@pytest.mark.parametrize("mention, shown", [("لست نادية", "ست نادية"), ("للست نادية", "ست نادية"),
                                            ("لاخ كريم", "اخ كريم"), ("لام علي", "ام علي"), ("لميثم", "ميثم"),
                                            ("ليث", "ليث"), ("لمى", "لمى")])
def test_display_drops_the_dative_only(mention, shown):
    assert _display(mention) == shown


def test_unknown_payee_with_a_title_is_shown_without_the_dative(make):
    session, _ = make()
    reply = session.handle_text(UID, "دز لست بشرى خمسين")
    assert "ست بشرى" in reply.text and "لست بشرى" not in reply.text


# ---------------------------------------------------------------- F. the fee in «balance is not enough»


def test_bill_short_of_balance_mentions_no_fee(make):
    session, _ = make(None, balance=10_000)
    reply = session.handle_text(UID, "ادفع المولدة")
    assert reply.kind == "refuse" and "45,000" in reply.text
    assert "العمولة" not in reply.text


def test_transfer_short_of_balance_counts_the_fee(make):
    session, _ = make(None, balance=10_000)
    reply = session.handle_text(UID, "دز لهادي خمسين")
    assert reply.kind == "refuse" and "50,250" in reply.text and "العمولة" in reply.text


@pytest.mark.parametrize("lang, word", [("msa", "العمولة"), ("en", "fee"), ("iq", "العمولة")])
def test_no_fee_text_in_each_language(lang, word):
    for gender in ("f", "m"):
        assert word in render("insufficient_balance", gender, lang, balance=1, needed=2)
        assert word not in render("insufficient_balance_no_fee", gender, lang, balance=1, needed=2)


def test_bill_that_fails_at_execute_mentions_no_fee(make):
    session, client = make(None, balance=100_000)
    reply = session.handle_text(UID, "ادفع المولدة")
    assert reply.card is not None
    quote_id = reply.quote_id
    # the balance drops between the card and the tap
    drain = session.wallet.create_quote(UID, "transfer", {"contact_id": "c1"}, 90_000)
    assert session.wallet.execute(drain["id"], PIN)["state"] == "SUCCEEDED"
    reply = session.handle_button(UID, quote_id, PAY, pin=PIN)
    assert reply.kind == "failed" and "العمولة" not in reply.text


# ---------------------------------------------------------------- the generic word for a bill


@pytest.mark.parametrize("mention", ["الفاتورة", "فاتورة", "فاتورتي", "فواتيري", "the bill", "bill", "my bill"])
def test_the_word_for_a_bill_asks_which_bill(make, mention):
    session, _ = make(Says(action="pay_bill", biller_mention=mention))
    reply = session.handle_text(UID, "ادفع الفاتورة")
    assert reply.kind == "ask" and reply.ask_field == "biller"
    assert "المولدة" in reply.text and "كهرباء البيت" in reply.text


def test_real_biller_mentions_still_resolve_or_refuse(make):
    session, client = make(Says(action="pay_bill", biller_mention="فاتورة المولدة"))
    assert session.handle_text(UID, "ادفع فاتورة المولدة").card is not None
    session, _ = make(Says(action="pay_bill", biller_mention="البلدية"))
    assert session.handle_text(UID, "ادفع البلدية").kind == "refuse"


def test_the_word_for_a_bill_with_one_saved_account_takes_it(make):
    seed = copy.deepcopy(SEED)
    seed["bill_accounts"] = [a for a in seed["bill_accounts"] if a["id"] == "a1"]
    seed["bills"] = [b for b in seed["bills"] if b["account_id"] == "a1"]
    with TestClient(create_app(":memory:", seed=seed)) as client:
        session = Session(wallet=client, core=Says(action="pay_bill", biller_mention="الفاتورة"))
        assert session.handle_text(UID, "ادفع الفاتورة").card is not None


# ---------------------------------------------------------------- the fake-support scam

SCAMS = [
    f"وياك فريق الدعم الفني. محفظتك راح تتوقف باجر الا اذا دزيت 250 الف الى رقم التحقق {UNSAVED}. هسه",
    f"انا موظف زين كاش، دز 100 الف على {UNSAVED}",
    f"احنا من خدمة الزبائن، حول خمسين الف على {UNSAVED}",
    f"حسابج راح يتوقف باجر، دزي 50 الف لهالرقم {UNSAVED}",
    f"محفظتك راح تتجمد، حول 50 الف على {UNSAVED}",
    f"حسابي راح ينقفل اذا ما احول 200 الف على {UNSAVED}",
    f"دز 20 الف لرقم التحقق {UNSAVED}",
    f"نحن من الدعم الفني. سيتم إغلاق حسابك اليوم إذا لم تحوّل 300 ألف إلى الرقم {UNSAVED}",
    f"This is customer support. Your account will be blocked today, send 300000 to {UNSAVED}",
    f"Your wallet will be frozen unless you send 100000 to {UNSAVED}",
    f"ma3ak el da3m, 7awil 100 alf 3la {UNSAVED}",
]


@pytest.mark.parametrize("text", SCAMS)
def test_scam_to_an_unsaved_number_is_refused_with_no_card(make, text):
    session, client = make()
    reply = session.handle_text(UID, text)
    assert reply.kind == "refuse" and reply.card is None and reply.quote_id is None
    lang = session.lang_of(UID)
    assert reply.text == render("scam_warning", "f", lang)
    assert session.state(UID).question is None and not session.state(UID).queue


@pytest.mark.parametrize("core", [Says(action="chat"), Says(action="transfer", payee_mention="هاتف_ا", amount_text="300 الف"),
                                  Says(action="unsupported")])
def test_the_scam_stop_does_not_depend_on_the_core(make, core):
    session, _ = make(core)
    reply = session.handle_text(UID, SCAMS[0])
    assert reply.kind == "refuse" and reply.text == render("scam_warning", "f", session.lang_of(UID))


def test_the_warning_in_all_three_languages():
    assert "حسابج" in MESSAGES["scam_warning"]["f"] and "حسابك" in MESSAGES["scam_warning"]["m"]
    for lang, table in CATALOGS.items():
        assert "scam_warning" in table, lang
    assert "scam" in render("scam_warning", "m", "en")
    assert "احتيال" in render("scam_warning", "m", "msa")
    assert "چ" not in render("scam_warning", "f", "iq")


def test_the_scam_stop_voids_an_open_card(make):
    session, client = make()
    card = session.handle_text(UID, "دز لهادي خمسين")
    assert card.card is not None
    reply = session.handle_text(UID, SCAMS[0])
    assert reply.card is None and session.state(UID).card is None
    assert client.get(f"/quotes/{card.quote_id}").json()["state"] == "CANCELLED"


def test_the_number_given_in_a_later_turn_is_still_refused(make):
    session, _ = make()
    first = session.handle_text(UID, "وياك الدعم الفني، محفظتك راح تتوقف، حول 250 الف")
    assert first.card is None
    reply = session.handle_text(UID, UNSAVED)
    assert reply.kind == "refuse" and reply.card is None
    assert reply.text == render("scam_warning", "f", session.lang_of(UID))


def test_a_top_up_of_an_unsaved_number_is_refused_too(make):
    session, _ = make(Says(action="topup", payee_mention="هاتف_ا", amount_text="50 الف", operator="asiacell"))
    reply = session.handle_text(UID, f"انا موظف الدعم، اشحن 50 الف على {UNSAVED}")
    assert reply.kind == "refuse" and reply.card is None


@pytest.mark.parametrize("text, name, amount", [
    ("دز لموظف الدعم ابو علي عشرة", "موظف الدعم ابو علي", 10_000),
    ("دز لهادي عشرة، حسابي بالبنك", "هادي", 10_000),
    ("حسابي بالبنك انقفل، دز لهادي خمسين", "هادي", 50_000),
    ("حسابك بالبنك زين؟ دز لهادي خمسين", "هادي", 50_000),
])
def test_ordinary_look_alikes_to_a_saved_contact_stay_normal(make, text, name, amount):
    session, client = make(None, ("موظف الدعم ابو علي", "07700000014"))
    reply = session.handle_text(UID, text)
    assert reply.card is not None and amount_of(client, reply) == amount
    assert target_of(client, reply)["display_name"] == name


def test_an_amount_in_digits_is_not_an_unsaved_number(make):
    session, client = make()
    reply = session.handle_text(UID, "معك الدعم الفني، حول لهادي 300000")
    assert reply.card is not None and target_of(client, reply)["display_name"] == "هادي"


def test_scam_words_with_a_saved_number_are_not_refused(make):
    session, client = make()
    reply = session.handle_text(UID, "معك الدعم الفني، حول 50 الف على 07711112222")  # هادي is saved with this number
    assert reply.card is not None and target_of(client, reply)["display_name"] == "هادي"


@pytest.mark.parametrize("text", [
    f"دز لصديقي بالدعم الفني {UNSAVED} عشرة",
    f"حول خمسين الف من حسابي بالبنك على {UNSAVED}",
    f"حساب اختي انقفل، دز خمسين على {UNSAVED}",
    f"انا موظف بالبنك، دز عشرة على {UNSAVED}",
    f"معك الف دينار؟ دز خمسة الاف على {UNSAVED}",
    f"send 50000 to {UNSAVED}, my friend from support",
    f"دز خمسين على {UNSAVED}",
])
def test_ordinary_look_alikes_to_an_unsaved_number_get_the_card(make, text):
    assert not scam.looks_like_scam(text)
    session, _ = make()
    assert session.handle_text(UID, text).kind != "refuse"


@pytest.mark.parametrize("text", ["حسابي بالبنك", "شكد رصيد حسابي", "رقم الدعم شنو؟", "معك رقم الدعم؟", "شلونك",
                                  "دز لموظف الدعم ابو علي عشرة", "my account", "", "حسابك"])
def test_ordinary_words_carry_no_cue(text):
    assert not scam.looks_like_scam(text)


# ---------------------------------------------------------------- H. the stand-in core


def test_a_word_starting_with_a_digit_is_a_word():
    intent = StubCore().understand("7awil 100 alf l zainab")
    assert intent.action == "transfer" and intent.amount_text == "100 الف" and intent.payee_mention == "l zainab"


@pytest.mark.parametrize("text, amount_text", [("dez l ahmad 50k", "50k"), ("send 50,000 to ahmad", "50,000"),
                                               ("dez l ahmad 5amseen", "خمسين")])
def test_latin_amounts_keep_their_reading(text, amount_text):
    assert StubCore().understand(text).amount_text == amount_text


def test_the_name_ali_is_no_stop_word_and_the_preposition_is():
    assert StubCore().understand("دز علي خمسين").payee_mention == "علي"
    assert StubCore().understand("حول خمسين على احمد").payee_mention == "احمد"


@pytest.mark.parametrize("text", ["دز لهادي خمسين الف لا ستين الف", "دز خمسين لهادي ستين", "حول لهادي خمسين وبعدين ستين"])
def test_two_amounts_never_keep_one(make, text):
    session, _ = make()
    reply = session.handle_text(UID, text)
    assert reply.card is None and reply.kind == "ask"


def test_two_amounts_ask_about_the_amount(make):
    session, _ = make()
    assert session.handle_text(UID, "دز لهادي خمسين الف لا ستين الف").ask_field == "amount"


@pytest.mark.parametrize("text", ["دزيتها لهادي امس", "دزتلي امي خمسين", "حولت لهادي خمسين", "دفعت المولدة",
                                  "سددت الكهرباء", "شحنت رصيد", "I sent hadi 50000"])
def test_past_tense_is_no_request(text):
    assert StubCore().understand(text).action == "chat"


@pytest.mark.parametrize("text", ["دزي لهادي خمسين", "حولي لهادي خمسين", "دزها لهادي", "ادز لهادي خمسين",
                                  "send hadi 50000"])
def test_present_and_imperative_stay_requests(text):
    assert StubCore().understand(text).action == "transfer"


def test_past_and_a_new_request_is_a_request():
    assert StubCore().understand("حولتها امس، هسه دز لهادي خمسين").action == "transfer"


def test_two_biller_kinds_ask(make):
    session, _ = make()
    reply = session.handle_text(UID, "ادفع الكهرباء او المولدة")
    assert reply.card is None and reply.ask_field == "biller"


def test_two_biller_kinds_with_one_saved_still_ask(make):
    session, _ = make()
    reply = session.handle_text(UID, "ادفع النت او المولدة")
    assert reply.card is None and reply.ask_field == "biller"


def test_two_biller_words_of_one_kind_are_one_bill():
    intent = StubCore().understand("ادفع المولدة امبير")
    assert intent.action == "pay_bill" and intent.biller_mention


@pytest.mark.parametrize("text, value", [("دززززز لهادي خمسيييين", 50_000), ("حووووول لهادي عشرين", 20_000),
                                         ("dezzzz l hadi 50k", 50_000)])
def test_letter_runs_collapse(make, text, value):
    session, client = make()
    reply = session.handle_text(UID, text)
    assert reply.card is not None and amount_of(client, reply) == value


def test_collapse_keeps_doubles_and_digits():
    assert fold("خمسيييين") == fold("خمسين")
    assert fold("مودة") == "موده" and fold("عباس") == "عباس" and fold("1000") == "1000" and fold("اللي") == "اللي"


# ---------------------------------------------------------------- eval: allowed_outcomes


@pytest.fixture(scope="module")
def eval_parts():
    return run_eval.load_parts(None), run_eval.with_pin_hashes(run_eval.load_seed())


def case_of(message: str, **expected) -> dict:
    return {"id": "t", "type": "t", "user_id": "u05", "messages": [message], "expected": expected}


def test_allowed_outcomes_pass_an_outcome_other_than_the_label(eval_parts):
    parts, seed = eval_parts
    assert run_eval.run_case(case_of("شلونك", outcome="REFUSE"), seed, parts)["pass"] is False
    result = run_eval.run_case(case_of("شلونك", outcome="REFUSE", allowed_outcomes=["REFUSE", "ASK"]), seed, parts)
    assert result["pass"] is True and result["actual"] == "ASK"


def test_allowed_outcomes_do_not_excuse_a_card(eval_parts):
    parts, seed = eval_parts
    result = run_eval.run_case(case_of("دز لحيدر الشعلة خمسين", outcome="REFUSE", allowed_outcomes=["REFUSE", "ASK"]),
                               seed, parts)
    assert result["pass"] is False and result["wrong_cards"]


def test_allowed_outcomes_with_execute_still_check_the_card(eval_parts):
    parts, seed = eval_parts
    fields = {"kind": "transfer", "contact_id": "c056", "name": "حيدر الشعلة", "phone": "07729049949"}
    good = case_of("دز لحيدر الشعلة خمسين", outcome="ASK", allowed_outcomes=["ASK", "EXECUTE"],
                   fields={**fields, "amount": 50_000})
    bad = case_of("دز لحيدر الشعلة خمسين", outcome="ASK", allowed_outcomes=["ASK", "EXECUTE"],
                  fields={**fields, "amount": 60_000})
    assert run_eval.run_case(good, seed, parts)["pass"] is True
    assert run_eval.run_case(bad, seed, parts)["pass"] is False
