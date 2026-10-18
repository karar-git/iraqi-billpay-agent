from dataclasses import dataclass

import pytest

from payments.names import (
    THRESHOLD,
    Candidate,
    is_relation_word,
    needs_question,
    normalize,
    pick,
    resolve,
)

# Synthetic contacts (fictional names and numbers).
CONTACTS = [
    {"contact_id": "c1", "display_name": "أحمد علي", "phone": "07700000101"},
    {"contact_id": "c2", "display_name": "أحمد كريم", "phone": "07800000102"},
    {"contact_id": "c3", "display_name": "محمد جاسم", "phone": "07500000103"},
    {"contact_id": "c4", "display_name": "علي حسن", "phone": "07700000104"},
    {"contact_id": "c5", "display_name": "زينب", "phone": "07700000105", "alias": "مرتي"},
    {"contact_id": "c6", "display_name": "قاسم", "phone": "07700000106"},
    {"contact_id": "c7", "display_name": "عبد الله فاضل", "phone": "07700000107"},
    {"contact_id": "c8", "display_name": "لمى", "phone": "07700000108"},
    {"contact_id": "c9", "display_name": "مي", "phone": "07700000109"},
    {"contact_id": "c10", "display_name": "حجي كاظم", "phone": "07700000110"},
    {"contact_id": "c11", "display_name": "عباس", "phone": "07700000111"},
    {"contact_id": "c12", "display_name": "فاطمة", "phone": "07700000112"},
]


def ids(cands):
    return [c.contact_id for c in cands]


def sure(cands):
    return [c.contact_id for c in cands if c.score >= THRESHOLD]


@pytest.mark.parametrize("a,b", [
    ("أحمد", "احمد"),
    ("إبراهيم", "ابراهيم"),
    ("آمنة", "امنه"),
    ("فاطمة", "فاطمه"),
    ("مصطفى", "مصطفي"),
    ("قاسم", "كاسم"),
    ("گاسم", "كاسم"),
    ("چاي", "جاي"),
    ("عبد الله", "عبدالله"),
    ("عبد  الرحمن", "عبدالرحمن"),
    ("مُحَمَّد", "محمد"),
    ("محـــمد", "محمد"),
    ("  علي   حسن ", "علي حسن"),
])
def test_normalize_makes_variants_equal(a, b):
    assert normalize(a) == normalize(b)


def test_exact_full_name_wins():
    cands = resolve("أحمد علي", CONTACTS)
    assert cands[0].contact_id == "c1"
    assert cands[0].score == 1.0
    assert not needs_question(cands)


def test_two_same_first_name_asks():
    cands = resolve("احمد", CONTACTS)
    assert sorted(sure(cands)) == ["c1", "c2"]
    assert needs_question(cands)
    assert pick(cands) is None


def test_dative_prefix_is_stripped():
    cands = resolve("لأحمد علي", CONTACTS)
    assert pick(cands).contact_id == "c1"


def test_name_starting_with_lam_is_kept():
    # "لمى" is a name: it must not be read as "ل + مى" and matched to "مي".
    cands = resolve("لمى", CONTACTS)
    assert pick(cands).contact_id == "c8"


def test_q_k_g_spellings_match():
    for spelling in ("قاسم", "كاسم", "گاسم"):
        assert pick(resolve(spelling, CONTACTS)).contact_id == "c6"


def test_abd_spacing():
    assert pick(resolve("عبدالله", CONTACTS)).contact_id == "c7"
    assert pick(resolve("عبد الله", CONTACTS)).contact_id == "c7"


def test_titles_are_ignored():
    assert pick(resolve("الحجي كاظم", CONTACTS)).contact_id == "c10"
    assert pick(resolve("كاظم", CONTACTS)).contact_id == "c10"


def test_nickname_one_to_many_asks():
    cands = resolve("حمودي", CONTACTS)
    assert sorted(sure(cands)) == ["c1", "c2", "c3"]
    assert all(c.how == "nickname" for c in cands if c.contact_id in ("c1", "c2", "c3"))
    assert needs_question(cands)


def test_nickname_scores_below_exact():
    nick = resolve("علاوي", CONTACTS)
    assert pick(nick).contact_id == "c4"
    exact = resolve("علي حسن", CONTACTS)
    assert nick[0].score < exact[0].score


@pytest.mark.parametrize("nick,cid", [("عبوسي", "c11"), ("كظومي", "c10"), ("فطومة", "c12")])
def test_more_nicknames(nick, cid):
    assert pick(resolve(nick, CONTACTS)).contact_id == cid


def test_fuzzy_never_auto_selects():
    cands = resolve("احمج", CONTACTS)  # typo
    assert {"c1", "c2"} <= set(ids(cands))
    assert all(c.score < THRESHOLD for c in cands)
    assert all(c.how == "fuzzy" for c in cands)
    assert needs_question(cands)


def test_relation_word_without_alias_asks():
    for word in ("ابوي", "امي", "ابني", "بنتي", "اخوي", "اختي", "زوجي"):
        cands = resolve(word, CONTACTS)
        assert cands == [], word
        assert needs_question(cands)
        assert is_relation_word(word)


def test_relation_word_with_contact_alias():
    cands = resolve("مرتي", CONTACTS)
    assert pick(cands).contact_id == "c5"
    assert cands[0].how == "alias"
    assert pick(resolve("لمرتي", CONTACTS)).contact_id == "c5"


def test_relation_word_with_saved_alias_map():
    cands = resolve("ابوي", CONTACTS, aliases={"أبوي": "c3"})
    assert pick(cands).contact_id == "c3"
    assert cands[0].score == 1.0


def test_relation_plus_name_uses_the_name():
    cands = resolve("اخوي علي حسن", CONTACTS)
    assert pick(cands).contact_id == "c4"


def test_phone_suffix():
    assert pick(resolve("0104", CONTACTS)).contact_id == "c4"
    assert resolve("9999", CONTACTS) == []


def test_unknown_name_gives_no_candidates():
    cands = resolve("سيف", CONTACTS)
    assert sure(cands) == []
    assert needs_question(cands)


def test_scores_in_range_and_sorted():
    for mention in ("احمد", "حمودي", "علي", "احمج", "زينب", "عباس", "x"):
        cands = resolve(mention, CONTACTS)
        scores = [c.score for c in cands]
        assert all(0 < s <= 1 for s in scores)
        assert scores == sorted(scores, reverse=True)


def test_accepts_objects_too():
    @dataclass
    class Contact:
        contact_id: str
        display_name: str
        phone: str
        alias: str | None = None

    contacts = [Contact(c["contact_id"], c["display_name"], c["phone"], c.get("alias")) for c in CONTACTS]
    assert pick(resolve("مرتي", contacts)).contact_id == "c5"
    assert isinstance(resolve("زينب", contacts)[0], Candidate)


def test_needs_question_counts_only_confident():
    a = Candidate("x", "x", "", 0.9, "first_name")
    b = Candidate("y", "y", "", 0.4, "fuzzy")
    assert not needs_question([a, b])
    assert needs_question([b])
    assert needs_question([])
    assert needs_question([a, Candidate("z", "z", "", 0.7, "nickname")])


def test_a_later_word_of_the_saved_name_only_asks():
    """A mention of a later word ("علي" in "حسن علي", Hassan son of Ali) is asked about, never picked."""
    contacts = [
        {"contact_id": "h", "display_name": "حسن علي", "phone": "07700000201"},
        {"contact_id": "m", "display_name": "مصطفى كريم", "phone": "07700000202"},
    ]
    for mention, cid in (("لعلي", "h"), ("كريم", "m")):
        cands = resolve(mention, contacts)
        assert pick(cands) is None
        assert cands[0].contact_id == cid and cands[0].how == "name_part"
        assert 0.3 <= cands[0].score < THRESHOLD
