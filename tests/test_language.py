"""Language mirroring: the agent answers in the user's language, per message.

Iraqi (Arabizi too) -> Iraqi; MSA or any other Arabic dialect -> MSA; English -> English. A message with
no markers keeps the language, a mixed one never flips it, and a confident core hint overrides.
"""

from __future__ import annotations

import copy
import string

import pytest
from fastapi.testclient import TestClient

from agent.session import Session
from agent.wallet_client import WalletClient
from core.interface import Intent
from core.stub import StubCore
from payments import language
from payments.amounts import parse
from payments.messages import CATALOGS, MESSAGES, render
from payments.quote import BUTTONS_BY_LANG, CARD_TITLES, QuoteDetails, amount_in_words, render_card
from payments.rules import WalletView
from tests.test_session import PIN, SEED
from wallet.api import create_app

IQ, MSA, EN = language.IRAQI, language.MSA, language.ENGLISH


# ---------------------------------------------------------------- the detector

@pytest.mark.parametrize("text,expected", [
    ("دز لأحمد خمسين", IQ),
    ("شلونك، شكد عندي هسه؟", IQ),
    ("اكو فاتورة مولدة؟", IQ),
    ("گلي شكد الفاتورة", IQ),
    ("dizli 50 alf l ahmad", IQ),
    ("shgad 3indi", IQ),
    ("أريد أن أدفع فاتورة الكهرباء", MSA),
    ("هل يمكنك تحويل خمسين ألف دينار إلى أحمد؟", MSA),
    ("كم رصيدي؟", MSA),
    ("ابغى اشحن رصيد", MSA),  # Gulf
    ("بدي حوّل لأحمد خمسين ألف", MSA),  # Levantine
    ("عايز أدفع النت", MSA),  # Egyptian
    ("بغيت نخلص الضو", MSA),  # Maghrebi
    ("send 50000 to Ahmed", EN),
    ("pay the electricity bill please", EN),
    ("what is my balance", EN),
])
def test_detect_from_iraqi(text, expected):
    assert language.detect(text, IQ) == expected


@pytest.mark.parametrize("text", ["50000", "إي", "ادفع المولدة", "Ahmed", "ok", "1234", "👍"])
def test_no_markers_keep_the_language(text):
    for previous in (MSA, EN):
        if previous == EN and text in ("إي", "ادفع المولدة"):
            continue  # Arabic script after English: back to Arabic (below)
        assert language.detect(text, previous) == previous


def test_arabic_script_without_markers_after_english_goes_back_to_iraqi():
    assert language.detect("ادفع المولدة", EN) == IQ


def test_one_mixed_message_never_flips():
    assert language.detect("اريد ادفع هسه بس كم الفاتورة", IQ) == IQ  # Iraqi + MSA markers
    assert language.detect("هل يمكن تدزلي شكد عندي", MSA) == MSA
    assert language.detect("send لأحمد خمسين هسه", EN) == EN


def test_normalize_hints():
    assert language.normalize("ar-IQ") == IQ and language.normalize("fusha") == MSA
    assert language.normalize("English") == EN and language.normalize("fr") is None
    assert language.normalize(None) is None


# ---------------------------------------------------------------- every string in three languages

_F = string.Formatter()


def _slots(entry) -> set[tuple[str, ...]]:
    texts = entry.values() if isinstance(entry, dict) else [entry]
    return {tuple(sorted({name for _, name, _, _ in _F.parse(t) if name})) for t in texts}


def test_every_message_exists_in_every_language_with_the_same_slots():
    for lang, table in CATALOGS.items():
        assert set(table) == set(MESSAGES), lang
        for key, entry in table.items():
            if isinstance(entry, dict):
                assert set(entry) == {"f", "m"}, (lang, key)
            assert _slots(entry) | _slots(MESSAGES[key]) == _slots(MESSAGES[key]) | _slots(entry)
            assert {s for t in _slots(entry) for s in t} == {s for t in _slots(MESSAGES[key]) for s in t}, (lang, key)


def test_no_persian_che_and_no_iraqi_words_in_msa():
    iraqi_only = ("هسه", "شكد", "ماكو", "اكو", "گ", "چ", "دز", "بطّلت", "إلج", "منج")
    for key, entry in CATALOGS[MSA].items():
        for text in entry.values() if isinstance(entry, dict) else [entry]:
            assert "چ" not in text
            assert not any(word in text for word in iraqi_only), (key, text)
    for table in CATALOGS.values():
        for entry in table.values():
            for text in entry.values() if isinstance(entry, dict) else [entry]:
                assert "چ" not in text


def test_english_messages_are_english():
    for key, entry in CATALOGS[EN].items():
        assert isinstance(entry, str), key
        letters = [c for c in entry if c.isalpha()]
        assert sum(c.isascii() for c in letters) / len(letters) > 0.95, key


def test_render_in_each_language():
    assert render("balance", "f", IQ, balance=50_000) == "رصيدج 50,000 دينار."
    assert render("balance", "f", MSA, balance=50_000) == "رصيدك 50,000 دينار."
    assert render("balance", "f", EN, balance=50_000) == "Your balance is 50,000 dinars."
    assert "September 2026" in render("already_paid", "m", EN, biller="X", period="2026-09")


# ---------------------------------------------------------------- amount in words and the card

@pytest.mark.parametrize("n,msa,en", [
    (1_000, "ألف دينار", "one thousand dinars"),
    (2_000, "ألفا دينار", "two thousand dinars"),  # the dual construct right before دينار
    (5_000, "خمسة آلاف دينار", "five thousand dinars"),
    (11_000, "أحد عشر ألف دينار", "eleven thousand dinars"),
    (25_000, "خمسة وعشرون ألف دينار", "twenty-five thousand dinars"),
    (50_000, "خمسون ألف دينار", "fifty thousand dinars"),
    (200_000, "مئتا ألف دينار", "two hundred thousand dinars"),
    (250_000, "مئتان وخمسون ألف دينار", "two hundred fifty thousand dinars"),
    (1_500_000, "مليون وخمسمئة ألف دينار", "one million five hundred thousand dinars"),
    (50_250, "خمسون ألف ومئتان وخمسون دينار", "fifty thousand two hundred fifty dinars"),
])
def test_amount_words(n, msa, en):
    assert amount_in_words(n, MSA) == msa
    assert amount_in_words(n, EN) == en


def test_msa_words_round_trip_through_the_parser():
    for n in list(range(1_000, 1_000_001, 1_000)) + list(range(1_000_000, 10_000_001, 50_000)):
        assert parse(amount_in_words(n, MSA)).value_iqd == n, n


def test_msa_amounts_the_user_types_are_parsed():
    assert parse("أحد عشر ألف دينار").value_iqd == 11_000
    assert parse("مئتا ألف").value_iqd == 200_000
    assert parse("خمسون ألفاً").value_iqd == 50_000
    assert parse("ماكو احد").value_iqd is None  # «أحد» alone is «someone», never 1,000


def test_buttons_per_language_fit_whatsapp_and_keep_ids():
    for lang, buttons in BUTTONS_BY_LANG.items():
        assert [b.id for b in buttons] == ["confirm", "change", "cancel"], lang
        assert all(len(b.label) <= 20 for b in buttons), lang
    assert [b.label for b in BUTTONS_BY_LANG[MSA]] == ["نعم، ادفع", "تعديل", "لا، إلغاء"]
    assert [b.label for b in BUTTONS_BY_LANG[EN]] == ["Yes, pay", "Change", "No, cancel"]


TRANSFER = QuoteDetails("q1", "transfer", 50_000, fee=250, payee_id="c1", payee_name="أحمد علي",
                        phone="07700000101")
BILL = QuoteDetails("q2", "bill", 35_000, payee_id="a1", payee_name="زينب حسن", biller_name="Hajji Jasim Generator",
                    period="2026-09", account_no="G-1029")


@pytest.mark.parametrize("lang", [IQ, MSA, EN])
def test_card_shows_recipient_amount_fee_total_balance_expiry_in_the_language(lang):
    card = render_card(TRANSFER, WalletView(200_000, 1_000_000, gender="m"), lang=lang)
    text = card.text
    assert card.title == CARD_TITLES[lang]["transfer"] and card.lang == lang
    assert "أحمد علي" in text and "0101" in text and "07700000101" not in text
    assert "50,000" in text and amount_in_words(50_000, lang) in text
    assert "250" in text and "50,250" in text and "149,750" in text
    assert "5" in card.lines[-1]
    assert [b.label for b in card.buttons] == [b.label for b in BUTTONS_BY_LANG[lang]]
    # the voice summary: who, how much in words, the fee, which button; no digits of the phone
    assert "أحمد علي" in card.spoken and amount_in_words(50_000, lang) in card.spoken
    assert "0101" not in card.spoken


def test_english_bill_card():
    card = render_card(BILL, WalletView(100_000, 1_000_000, gender="f"), lang=EN)
    assert card.title == "Confirm bill payment"
    assert card.lines[0] == "You're paying the Hajji Jasim Generator bill"
    assert "Account holder: زينب حسن, account no. G-1029" in card.text
    assert "For: September 2026" in card.text
    assert "No fee, total: 35,000 dinars." in card.text
    assert card.spoken.endswith("tap Yes, pay to confirm.")


def test_msa_card_is_clean_fusha():
    card = render_card(TRANSFER, WalletView(200_000, 1_000_000, gender="f"), lang=MSA)
    assert card.lines[0] == "ستحوّلين 50,000 دينار (خمسون ألف دينار)"
    assert card.lines[-1] == "البطاقة صالحة لمدة 5 دقائق. هل أنتِ متأكدة؟"
    assert not any(w in card.text + card.spoken for w in ("راح", "تدزين", "دقايق", "رصيدج", "دوسي"))


# ---------------------------------------------------------------- the orchestrator follows the user

@pytest.fixture
def make():
    clients = []

    def build(core=None):
        client = TestClient(create_app(":memory:", seed=copy.deepcopy(SEED)))
        clients.append(client)
        return Session(WalletClient(client), core or StubCore())

    yield build
    for c in clients:
        c.close()


def test_english_transfer_end_to_end(make):
    s = make()
    r = s.handle_text("u1", "send 50000 to Hadi")
    assert r.lang == EN and r.kind == "card"
    assert r.card.title == "Confirm transfer"
    assert "You're sending 50,000 dinars (fifty thousand dinars)" in r.card.text
    assert "To: هادي, number ending in 2222" in r.card.text
    assert [b.label for b in r.card.buttons] == ["Yes, pay", "Change", "No, cancel"]
    r = s.handle_button("u1", r.quote_id, "Yes, pay")  # the English title is a button too
    assert r.lines == ["Enter your PIN in the PIN box with the “Yes, pay” button. Don't type it in the chat."]
    r = s.handle_button("u1", r.quote_id, "confirm", pin=PIN)
    assert r.kind == "done" and r.lines[0].startswith("Done. You sent 50,000 dinars to هادي.")


def test_msa_question_and_card(make):
    s = make()
    r = s.handle_text("u1", "أريد أن أحوّل إلى أحمد خمسين ألف دينار")
    assert r.lang == MSA and r.kind == "ask"
    assert r.text.startswith("لديك أكثر من شخص بهذا الاسم: 1) أحمد (الرقم المنتهي بـ ")
    r = s.handle_text("u1", "الثاني")  # no markers: the conversation stays in MSA
    assert r.lang == MSA and r.kind == "card"
    assert r.card.lines[0] == "ستحوّلين 50,000 دينار (خمسون ألف دينار)"
    assert [b.label for b in r.card.buttons] == ["نعم، ادفع", "تعديل", "لا، إلغاء"]


def test_language_switches_back_per_message(make):
    s = make()
    assert s.handle_text("u1", "what is my balance").lines == ["Your balance is 500,000 dinars."]
    assert s.handle_text("u1", "شكد رصيدي هسه").lines == ["رصيدج 500,000 دينار."]
    assert s.handle_text("u1", "كم رصيدي؟").lines == ["رصيدك 500,000 دينار."]
    assert s.lang_of("u1") == MSA


def test_dollars_in_english_ask_for_dinars(make):
    s = make()
    r = s.handle_text("u1", "send $50 to Hadi")
    assert r.kind == "ask" and r.lines == ["How much is that in dinars? We only send Iraqi dinars."]


def test_english_bill_uses_the_billers_english_name(make):
    s = make()
    r = s.handle_text("u1", "pay the generator bill")
    assert r.kind == "card" and r.card.lines[0] == "You're paying the Hajji Jasim Generator bill"


class HintCore(StubCore):
    """The stand-in, plus a confident language hint (what the production core may add)."""

    def __init__(self, hint):
        self.hint = hint

    def understand(self, text, context=None):
        intent = super().understand(text, context)
        intent.language = self.hint
        return intent


def test_a_confident_core_hint_overrides_the_detector(make):
    s = make(HintCore("en"))
    r = s.handle_text("u1", "شكد رصيدي")  # Iraqi markers, but the core is sure it is English
    assert r.lines == ["Your balance is 500,000 dinars."]


def test_phrase_gets_the_language(make):
    seen = []

    class Recording(StubCore):
        def phrase(self, key, slots):
            seen.append(slots.get("lang"))
            return super().phrase(key, slots)

    s = make(Recording())
    s.handle_text("u1", "send money to Hadi")
    assert seen == [EN]


def test_core_context_has_the_language(make):
    contexts = []

    class Recording(StubCore):
        def understand(self, text, context=None):
            contexts.append(dict(context or {}))
            return Intent("balance")

    s = make(Recording())
    s.handle_text("u1", "what is my balance")
    assert contexts[0]["language"] == EN
