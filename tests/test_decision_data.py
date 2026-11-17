"""The decision-model data: the split is clean, the locked part is frozen, and the build script cannot change it.

These tests read decision/data/locked.jsonl only to check its hash and that none of it leaked into the other
parts. Nothing here scores a model on it.
"""

import json
import random
import shutil
from collections import Counter

import pytest

from core.interface import ACTIONS
from decision.data import build as B

PARTS = {name: B.load_part(name) for name in B.PARTS}


def keys(rows):
    return {B.clean_key(row["text"]) for row in rows}


# ------------------------------------------------------------------ the files in the repo


def test_the_locked_file_has_the_hash_in_the_lock():
    lock = B.read_lock()
    assert lock is not None and lock["file"] == "locked.jsonl"
    assert B.file_sha256(B.DATA_DIR / "locked.jsonl") == lock["sha256"]
    assert lock["lines"] == len(PARTS["locked"])


def test_a_fresh_build_gives_exactly_the_files_on_disk():
    texts, _ = B.build()
    for name in B.PARTS:
        assert B.text_sha256(texts[name]) == B.file_sha256(B.DATA_DIR / f"{name}.jsonl"), name
    assert B.text_sha256(texts["locked"]) == B.read_lock()["sha256"]
    assert B.refusal(texts) is None


def test_locked_texts_are_not_in_train_or_tune():
    train, tune, locked = keys(PARTS["train"]), keys(PARTS["tune"]), keys(PARTS["locked"])
    assert not locked & train
    assert not locked & tune
    assert not train & tune
    exact = {" ".join(row["text"].split()) for row in PARTS["train"] + PARTS["tune"]}
    assert not [row["text"] for row in PARTS["locked"] if " ".join(row["text"].split()) in exact]


def test_no_locked_line_is_a_near_duplicate_of_a_train_or_tune_line():
    others = [B._trigrams(B.clean_key(row["text"])) for row in PARTS["train"] + PARTS["tune"]]
    for row in PARTS["locked"]:
        grams = B._trigrams(B.clean_key(row["text"]))
        for other in others:
            shared = len(grams & other)
            assert not shared or shared / (len(grams) + len(other) - shared) < B.NEAR_DUPLICATE_JACCARD


def test_every_line_is_clean_and_every_part_holds_every_action():
    for name, rows in PARTS.items():
        assert rows, name
        for row in rows:
            assert set(row) == {"text", "label", "style", "source"}
            assert row["label"] in ACTIONS
            assert row["text"].strip() and B.CHE not in row["text"]
            assert row["source"] in B.SLICES
        assert {row["label"] for row in rows} == set(ACTIONS), name
        assert len(keys(rows)) == len(rows), f"{name} holds the same message twice"


def test_the_split_is_about_70_15_15():
    total = sum(len(rows) for rows in PARTS.values())
    assert 0.66 < len(PARTS["train"]) / total < 0.74
    assert 0.12 < len(PARTS["tune"]) / total < 0.18
    assert 0.12 < len(PARTS["locked"]) / total < 0.18


def test_build_json_counts_are_the_files():
    report = json.loads((B.DATA_DIR / B.BUILD_NAME).read_text(encoding="utf-8"))
    assert report["kept"] == sum(len(rows) for rows in PARTS.values())
    assert report["raw_lines"] == report["kept"] + sum(report["dropped"].values())
    for name, rows in PARTS.items():
        assert report["parts"][name]["lines"] == len(rows)
        assert report["parts"][name]["labels"] == dict(sorted(Counter(row["label"] for row in rows).items()))
        assert report["parts"][name]["sources"] == dict(sorted(Counter(row["source"] for row in rows).items()))


def test_the_part_of_a_line_depends_only_on_its_own_text():
    for name, rows in PARTS.items():
        for row in rows[:50]:
            assert B.part_of(B.clean_key(row["text"])) == name


# ------------------------------------------------------------------ cleaning rules


def test_keys_fold_spelling_spaces_punctuation_and_emoji():
    assert B.clean_key("  أدفع   المولدة!! 💡") == B.clean_key("ادفع المولده")
    assert B.clean_key("Pay the BILL.") == B.clean_key("pay the bill")
    assert B.clean_key("😂") == "😂"  # only an emoji: the folded text is the key


def write_raw(folder, rows):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "w1.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                                     encoding="utf-8", newline="\n")


def test_cleaning_drops_what_it_says(tmp_path):
    rows = [
        {"text": "شكد رصيدي", "label": "balance", "style": "a"},
        {"text": "شكد  رصيدي؟", "label": "balance", "style": "a"},            # the same after cleaning
        {"text": "الغي العملية", "label": "cancel", "style": "a"},
        {"text": "الغي العملية", "label": "chat", "style": "a"},              # same text, two labels: both go
        {"text": "شلونك " + B.CHE + "ا", "label": "chat", "style": "a"},       # the letter this repo never writes
        {"text": "دز لاحمد خمسين", "label": "send_money", "style": "a"},       # unknown label
        {"text": "   ", "label": "chat", "style": "a"},
        {"text": "ادفع فاتورة الكهرباء مال هذا الشهر", "label": "pay_bill", "style": "a"},
        {"text": "ادفع فاتورة الكهرباء مال هذا الشهر هسه", "label": "pay_bill", "style": "a"},  # near duplicate
    ]
    write_raw(tmp_path / "raw", rows)
    with (tmp_path / "raw" / "w1.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("not json\n")
    texts, report = B.build(tmp_path / "raw")
    kept = [json.loads(line)["text"] for text in texts.values() for line in text.splitlines()]
    assert sorted(kept) == sorted(["شكد رصيدي", "ادفع فاتورة الكهرباء مال هذا الشهر"])
    assert report["dropped"] == {"exact_duplicate": 1, "has_che": 1, "near_duplicate": 1, "no_text": 1,
                                 "not_json": 1, "same_text_two_labels": 2, "unknown_label": 1}
    assert report["raw_lines"] == 10 and report["kept"] == 2


# ------------------------------------------------------------------ the lock cannot be broken by the script


@pytest.fixture()
def sandbox(tmp_path):
    """A small data folder of made-up lines, built and locked once."""
    raw, data = tmp_path / "raw", tmp_path / "data"
    data.mkdir()
    rows = made_up_rows(240)
    write_raw(raw, rows)
    assert B.main([], raw_dir=raw, data_dir=data) == 0
    assert B.read_lock(data) is not None
    assert B.load_part("locked", data) and B.load_part("train", data)
    return raw, data, rows


WORDS = ("ادفع دز حول اشحن شكد رصيدي الغي بطلت فاتورة المولدة الكهرباء النت الماي لاحمد لهادي لزينب خمسين ميتين "
         "هسه باجر رجاء ضيف رقم جديد آخر العمليات كشف حساب شلونك هلا شكرا please send pay balance cancel "
         "history topup zain korek bill water internet friend today").split()


def made_up_rows(count):
    """Distinct made-up lines (random words, fixed seed), far enough apart not to be near duplicates."""
    rng = random.Random(7)
    rows, seen = [], set()
    while len(rows) < count:
        text = " ".join(rng.sample(WORDS, rng.randint(4, 7)))
        if text not in seen:
            seen.add(text)
            rows.append({"text": text, "label": rng.choice(ACTIONS), "style": "made_up"})
    return rows


def snapshot(data):
    return {path.name: path.read_bytes() for path in sorted(data.iterdir())}


def test_the_first_build_writes_the_lock_and_a_second_build_changes_nothing(sandbox, capsys):
    raw, data, _ = sandbox
    before = snapshot(data)
    assert B.main([], raw_dir=raw, data_dir=data) == 0
    assert snapshot(data) == before
    assert B.main(["--check"], raw_dir=raw, data_dir=data) == 0
    assert "up to date" in capsys.readouterr().out


def test_removing_a_train_line_is_allowed_and_leaves_the_locked_file_alone(sandbox):
    raw, data, rows = sandbox
    locked_before = (data / "locked.jsonl").read_bytes()
    lock_before = (data / B.LOCK_NAME).read_bytes()
    train_text = B.load_part("train", data)[0]["text"]
    write_raw(raw, [row for row in rows if row["text"] != train_text])
    assert B.main(["--check"], raw_dir=raw, data_dir=data) == 1  # train.jsonl is stale now
    assert B.main([], raw_dir=raw, data_dir=data) == 0
    assert train_text not in [row["text"] for row in B.load_part("train", data)]
    assert (data / "locked.jsonl").read_bytes() == locked_before
    assert (data / B.LOCK_NAME).read_bytes() == lock_before
    assert B.main(["--check"], raw_dir=raw, data_dir=data) == 0


def test_a_build_that_would_change_the_locked_set_is_refused_and_writes_nothing(sandbox, capsys):
    raw, data, rows = sandbox
    locked_text = B.load_part("locked", data)[0]["text"]
    write_raw(raw, [row for row in rows if row["text"] != locked_text])
    before = snapshot(data)
    assert B.main([], raw_dir=raw, data_dir=data) == 3
    assert "REFUSED" in capsys.readouterr().err
    assert snapshot(data) == before
    assert B.main(["--check"], raw_dir=raw, data_dir=data) == 3


def test_a_new_line_that_would_land_in_the_locked_set_is_refused(sandbox):
    raw, data, rows = sandbox
    text = next(f"سطر جديد تماما للاختبار {n} xyz" for n in range(1000)
                if B.part_of(B.clean_key(f"سطر جديد تماما للاختبار {n} xyz")) == "locked")
    write_raw(raw, rows + [{"text": text, "label": "chat", "style": "made_up"}])
    before = snapshot(data)
    assert B.main([], raw_dir=raw, data_dir=data) == 3
    assert snapshot(data) == before


def test_an_edited_locked_file_is_never_overwritten(sandbox, capsys):
    raw, data, _ = sandbox
    path = data / "locked.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[1:]) + "\n", encoding="utf-8", newline="\n")  # someone removed a line
    before = snapshot(data)
    assert B.main([], raw_dir=raw, data_dir=data) == 3
    assert "edited" in capsys.readouterr().err
    assert snapshot(data) == before
    assert B.main(["--check"], raw_dir=raw, data_dir=data) == 3


def test_writing_a_build_directly_cannot_break_the_lock_either(sandbox):
    """write_build without main(): a build the lock turns down raises before a single file is written."""
    raw, data, rows = sandbox
    locked_text = B.load_part("locked", data)[0]["text"]
    write_raw(raw, [row for row in rows if row["text"] != locked_text])
    texts, report = B.build(raw)
    before = snapshot(data)
    with pytest.raises(RuntimeError):
        B.write_build(texts, report, data)
    assert snapshot(data) == before
    (data / "locked.jsonl").unlink()  # even with the file gone, only the frozen text may come back
    with pytest.raises(RuntimeError):
        B.write_build(texts, report, data)
    assert not (data / "locked.jsonl").exists()

    write_raw(raw, rows)  # the original raw lines again, and someone edited the locked file by hand
    texts, report = B.build(raw)
    (data / "locked.jsonl").write_text("{}\n", encoding="utf-8", newline="\n")
    before = snapshot(data)
    with pytest.raises(RuntimeError):
        B.write_build(texts, report, data)
    assert snapshot(data) == before


def test_a_lost_locked_file_is_restored_only_as_the_frozen_one(sandbox):
    raw, data, _ = sandbox
    kept = (data / "locked.jsonl").read_bytes()
    (data / "locked.jsonl").unlink()
    assert B.main(["--check"], raw_dir=raw, data_dir=data) == 1
    assert B.main([], raw_dir=raw, data_dir=data) == 0
    assert (data / "locked.jsonl").read_bytes() == kept
    assert B.file_sha256(data / "locked.jsonl") == B.read_lock(data)["sha256"]


def test_crlf_checkouts_hash_the_same(tmp_path):
    source = B.DATA_DIR / "tune.jsonl"
    copy = tmp_path / "tune.jsonl"
    shutil.copy(source, copy)
    copy.write_bytes(copy.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert B.file_sha256(copy) == B.file_sha256(source)
