"""The intent decision model at run time: it loads, it is the scikit-learn pipeline to 1e-6, and it needs
nothing but the standard library.

Only one test needs scikit-learn (it skips without it). Every other test runs on the standard library.
"""

import hashlib
import json
import math
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from core.interface import ACTIONS
from decision import Decision, IntentModel
from decision.data.build import DATA_DIR, file_sha256, load_part, read_lock
from decision.features import ngrams, prepare
from decision.intent import FORMAT, MODEL_PATH

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_PATH = MODEL_PATH.with_name("intent_v1.reference.json")
TUNE = load_part("tune")
ODD_TEXTS = ["", "   ", "؟", "😂😂", "asdf qwer", "0770 123 4567", "دز لهاتف_ا ٥٠ الف", "sh7anet 50k",
             "خمسيييين   الف\tدينار", "A" * 400, "ادفع " * 60]


def model_text() -> str:
    return MODEL_PATH.read_text(encoding="utf-8")


# ------------------------------------------------------------------ the file


def test_the_model_file_loads_and_its_labels_are_the_core_actions():
    model = IntentModel.load()
    assert sorted(model.labels) == sorted(ACTIONS)
    assert len(model.labels) == len(set(model.labels)) == len(ACTIONS)
    decision = model.predict("شكد رصيدي")
    assert isinstance(decision, Decision)
    assert set(decision.scores) == set(ACTIONS)
    assert math.isclose(sum(decision.scores.values()), 1.0, abs_tol=1e-9)
    assert decision.label == max(decision.scores, key=decision.scores.get)
    assert decision.confidence == decision.scores[decision.label]


def test_the_model_file_is_small_and_says_what_it_is():
    assert MODEL_PATH.stat().st_size < 2_000_000
    meta = IntentModel.load().meta
    assert meta["format"] == FORMAT and meta["name"] == "intent_v1"
    assert meta["settings"]["analyzer"] == "char_wb" and meta["settings"]["norm"] == "l2"
    assert 0.0 < meta["settings"]["temperature"] < 20.0
    assert not {"vocabulary", "idf", "coef"} & set(meta)  # the weights are not kept twice


def test_the_model_was_trained_on_the_files_in_the_repo_and_never_on_the_locked_one():
    data = IntentModel.load().meta["data"]
    assert data["train_sha256"] == file_sha256(DATA_DIR / "train.jsonl"), "retrain: python -m decision.train"
    assert data["tune_sha256"] == file_sha256(DATA_DIR / "tune.jsonl")
    assert data["locked_sha256"] == read_lock()["sha256"]
    assert data["train_lines"] == len(load_part("train")) and data["tune_lines"] == len(TUNE)


def test_the_training_script_never_opens_the_locked_file():
    source = (ROOT / "decision" / "train.py").read_text(encoding="utf-8")
    assert 'load_part("locked")' not in source and "locked.jsonl\"" not in source
    assert 'load_part("train"), load_part("tune")' in source


def test_a_wrong_or_broken_model_file_is_refused(tmp_path):
    data = json.loads(model_text())
    for change in ({"format": 99}, {"idf": data["idf"][:-1]}, {"intercept": data["intercept"][:-1]},
                   {"settings": {**data["settings"], "analyzer": "word"}}):
        path = tmp_path / "model.json"
        path.write_text(json.dumps({**data, **change}), encoding="utf-8")
        with pytest.raises(ValueError):
            IntentModel(path).predict("هلا")
    with pytest.raises(FileNotFoundError):
        IntentModel.load(tmp_path / "missing.json")


def test_loading_is_lazy_and_the_shipped_model_is_shared(tmp_path):
    assert IntentModel.load() is IntentModel.load()
    lazy = IntentModel(tmp_path / "not-there.json")  # nothing is read until the first prediction
    with pytest.raises(FileNotFoundError):
        lazy.predict("هلا")
    copy = tmp_path / "copy.json"
    copy.write_text(model_text(), encoding="utf-8")
    other = IntentModel.load(copy)
    assert other is not IntentModel.load()
    assert other.predict("دز لاحمد خمسين") == IntentModel.load().predict("دز لاحمد خمسين")


# ------------------------------------------------------------------ the same numbers as scikit-learn


def test_pure_python_gives_the_probabilities_scikit_learn_gave_at_training_time():
    """No scikit-learn needed: decision/train.py stored scikit-learn's own numbers for 200 tune lines."""
    reference = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    assert reference["model_sha256"] == hashlib.sha256(model_text().encode("utf-8")).hexdigest(), \
        "the reference belongs to another model file: retrain"
    model = IntentModel.load()
    assert reference["labels"] == list(model.labels)
    assert len(reference["rows"]) == 200
    assert [row["text"] for row in reference["rows"]] == [row["text"] for row in TUNE[:200]]
    for row in reference["rows"]:
        got = model.probabilities(row["text"])
        assert max(abs(p - q) for p, q in zip(got, row["probabilities"])) < 1e-6, row["text"]


def test_pure_python_equals_the_scikit_learn_pipeline_to_1e_6():
    pytest.importorskip("sklearn")
    pytest.importorskip("numpy")
    from decision.train import sklearn_pipeline

    vectorizer, classifier = sklearn_pipeline(json.loads(model_text()))
    model = IntentModel.load()
    assert [str(label) for label in classifier.classes_] == list(model.labels)
    texts = [row["text"] for row in TUNE[:200]] + ODD_TEXTS
    expected = classifier.predict_proba(vectorizer.transform(texts))
    worst = 0.0
    for text, row in zip(texts, expected):
        got = model.probabilities(text)
        worst = max(worst, max(abs(p - float(q)) for p, q in zip(got, row)))
    assert worst < 1e-6
    assert [model.predict(text).label for text in texts] == [str(label) for label in classifier.predict(
        vectorizer.transform(texts))]


def test_the_n_gram_cut_is_the_one_documented():
    assert ngrams("اب", 1, 5) == [" ", "ا", "ب", " ", " ا", "اب", "ب ", " اب", "اب ", " اب "]
    assert ngrams("ab cd", 2, 2) == [" a", "ab", "b ", " c", "cd", "d "]
    assert ngrams("", 1, 5) == [] and ngrams("   ", 1, 5) == []


# ------------------------------------------------------------------ what the model reads


def test_digits_carry_no_amount_and_a_masked_number_reads_like_a_real_one():
    assert prepare("دز لهاتف_ا ٥٠ الف") == prepare("دز ل07701234567 50 الف") == "دز ل00000000000 00 الف"
    model = IntentModel.load()
    assert model.probabilities("دز 50000 لاحمد") == model.probabilities("دز 75321 لاحمد")
    assert model.probabilities("ضيف رقم هاتف_ب باسم سيف") == model.probabilities("ضيف رقم 07809998877 باسم سيف")


def test_spelling_variants_and_arabizi_are_folded_before_reading():
    model = IntentModel.load()
    assert model.probabilities("أدفع المولّدة") == model.probabilities("ادفع المولده")
    assert model.probabilities("PAY THE BILL") == model.probabilities("pay the bill")
    assert prepare("sh7anet 50k") == "sh7anet 00k شحنت 00 الف"  # the Arabic reading follows the Latin words
    assert prepare("ادفع المولدة") == "ادفع المولده"  # no Latin letters: nothing is added


# ------------------------------------------------------------------ fixed examples, no scikit-learn

EXAMPLES = [
    ("شكد رصيدي", "balance"), ("كم رصيدي", "balance"), ("what is my balance", "balance"),
    ("الغي", "cancel"), ("cancel", "cancel"),
    ("دز لاحمد خمسين", "transfer"), ("send 50000 to Hadi", "transfer"), ("dez l ahmed 50", "transfer"),
    ("ارسل إلى محمد مئة ألف دينار", "transfer"),
    ("ادفع المولدة", "pay_bill"), ("pay the electricity bill", "pay_bill"),
    ("اشحن زين خمسة", "topup"), ("اشحنلي رصيد اسيا بعشرة", "topup"),
    ("راويني آخر العمليات", "history"), ("show my last transactions", "history"),
    ("ضيف رقم احمد 07701234567", "add_contact"),
    ("اريد قرض", "unsupported"), ("book me a flight", "unsupported"),
    ("هلا شلونك", "chat"), ("hello", "chat"), ("شكرا", "chat"),
]


@pytest.mark.parametrize("text,label", EXAMPLES)
def test_plain_requests_are_read_right_with_high_confidence(text, label):
    decision = IntentModel.load().predict(text)
    assert decision.label == label
    assert decision.confidence > 0.9


def test_any_text_gets_an_answer_and_never_an_error():
    model = IntentModel.load()
    for text in ODD_TEXTS:
        decision = model.predict(text)
        assert decision.label in ACTIONS and 0.0 < decision.confidence <= 1.0
    # nothing known in the message: the answer is the same weak prior, never a confident request
    assert model.predict("").confidence < 0.6 and model.predict("") == model.predict("   ")


def test_tune_accuracy_is_the_one_written_in_the_model_file():
    model = IntentModel.load()
    correct = sum(model.predict(row["text"]).label == row["label"] for row in TUNE)
    assert correct == model.meta["tune"]["correct"]
    assert correct / len(TUNE) > 0.9


# ------------------------------------------------------------------ cheap to run


def test_a_prediction_takes_well_under_10_ms():
    model = IntentModel.load()
    texts = [row["text"] for row in TUNE[:200]]
    model.predict(texts[0])  # the first call parses the file
    best = math.inf
    for _ in range(3):
        started = time.perf_counter()
        for text in texts:
            model.predict(text)
        best = min(best, (time.perf_counter() - started) / len(texts))
    assert best < 0.010, f"{best * 1000:.2f} ms per prediction"


def test_many_threads_can_share_one_model(tmp_path):
    copy = tmp_path / "copy.json"
    copy.write_text(model_text(), encoding="utf-8")
    model = IntentModel(copy)  # not loaded yet: the threads race on the first use
    texts = [row["text"] for row in TUNE[:40]]
    expected = [IntentModel.load().predict(text) for text in texts]
    results, errors = {}, []

    def work(n):
        try:
            results[n] = [model.predict(text) for text in texts]
        except Exception as error:  # pragma: no cover
            errors.append(error)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert all(results[n] == expected for n in range(8))


def test_the_runtime_imports_no_training_library():
    code = ("import sys\n"
            "from decision import Gate, IntentModel\n"
            "from core.interface import Intent\n"
            "gate = Gate()\n"
            "gate.before('شكد رصيدي', {})\n"
            "gate.after('دز لاحمد خمسين', Intent('transfer'), {})\n"
            "gate.fallback('ادفع المولدة', {})\n"
            "import decision.metrics, decision.data.build, eval.run_decision\n"
            "print(sorted(name for name in ('sklearn', 'numpy', 'scipy', 'pandas') if name in sys.modules))\n")
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120,
                          encoding="utf-8")
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]"
