"""Language review: gaps the offline language harness found in the non-Iraqi reply languages, now fixed.

English replies never show Arabic the user did not write; English ordinals pick an option; a typed yes
keeps the card in every language; MSA amount words and duals read right; everyday Iraqi switches back
from MSA.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent.session import Session, _is_yes, _ordinal
from agent.wallet_client import WalletClient
from payments import language
from payments.amounts import find_amount
from payments.quote import amount_in_words
from wallet.api import create_app

ROOT = Path(__file__).resolve().parent.parent
ARABIC = re.compile(r"[؀-ۿ]")


@pytest.fixture
def session(tmp_path):
    seed = json.loads((ROOT / "data" / "wallet_seed.json").read_text(encoding="utf-8"))
    app = create_app(str(tmp_path / "w.db"), seed=seed)
    wallet = WalletClient.in_process(app)
    yield Session(wallet)
    wallet.http.close()
    app.state.wallet.db.close()


@pytest.fixture
def small(tmp_path):
    from channels.whatsapp.tests import kit  # one generator account only, no electricity account
    app = create_app(str(tmp_path / "s.db"), seed=kit.seed())
    wallet = WalletClient.in_process(app)
    yield Session(wallet)
    wallet.http.close()
    app.state.wallet.db.close()


def test_english_missing_bill_account_names_the_biller_in_english(small):
    reply = small.handle_text("u1", "pay the electricity bill")
    assert reply.lang == "en" and reply.kind == "refuse"
    assert not ARABIC.search(reply.text), reply.text


def test_english_relation_word_question_stays_english(session):
    reply = session.handle_text("u01", "send money to my brother")
    assert reply.lang == "en"
    assert not ARABIC.search(reply.text), reply.text


@pytest.mark.parametrize("answer", ["second", "the second one", "2nd"])
def test_english_ordinal_picks_an_option(answer):
    assert _ordinal(answer) == 1


@pytest.mark.parametrize("text", ["yes please", "yeah", "sure", "go ahead", "نعم من فضلك", "أؤكد"])
def test_typed_yes_in_english_or_msa_keeps_the_card(text):
    assert _is_yes(text)


@pytest.mark.parametrize("n, words", [
    (103_000, "مئة وثلاثة آلاف دينار"),
    (110_000, "مئة وعشرة آلاف دينار"),
])
def test_msa_thousands_after_three_to_ten_are_plural(n, words):
    assert amount_in_words(n, "msa") == words


def test_msa_dual_construct_two_thousand_is_not_read_as_one_thousand():
    found = find_amount("ألفا دينار")
    assert found is None or found[0].value_iqd == 2_000


@pytest.mark.parametrize("text", ["ادفعلي النت مال هالشهر", "ابي ادفع فاتورة الماي", "ليش ما اندفعت"])
def test_iraqi_after_msa_goes_back_to_iraqi(text):
    assert language.detect(text, language.MSA) == language.IRAQI
