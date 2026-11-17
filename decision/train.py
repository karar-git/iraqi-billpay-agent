"""Train the intent decision model and export it for pure-Python inference.

This is the ONLY file of the package that needs scikit-learn and numpy (training tools, not runtime):
    pip install scikit-learn
    python -m decision.data.build        # once: clean, split, lock (see decision/data/build.py)
    python -m decision.train             # search on the tune set, calibrate, export, choose the gate thresholds

What it does, in order:
1. reads train.jsonl and tune.jsonl. It never opens locked.jsonl;
2. searches the settings on the TUNE set only: with or without the Arabic reading of Latin words, n-gram
   range, feature cap, rare n-grams, C, class weights
   (character n-gram TF-IDF inside word boundaries on `decision.features.prepare(text)`, sublinear tf,
   multinomial logistic regression);
3. calibrates: one temperature, fitted on out-of-fold predictions of the train set (5 folds: every train line
   is scored by a model that did not see it), so the tune set stays a fair check of reliability. The temperature
   is folded into the weights;
4. rounds the weights (to keep the file small) and puts the rounded numbers back into the scikit-learn objects,
   so the exported model and the scikit-learn model are the same model;
5. writes decision/models/intent_v1.json and intent_v1.reference.json (the scikit-learn probabilities of 200 tune
   lines; a test compares the pure-Python reader with them);
6. chooses the gate thresholds (decision/metrics.py) and writes them into the file: on the tune set, read with
   the exported model by the pure-Python reader, and with the out-of-fold train predictions as a second witness
   that can only make a threshold stricter or drop an action from the direct path.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import math
import sys
from datetime import date
from pathlib import Path

import numpy as np
import sklearn
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.interface import ACTIONS  # noqa: E402
from decision import metrics  # noqa: E402
from decision.data.build import DATA_DIR, file_sha256, load_part, read_lock  # noqa: E402
from decision.features import prepare  # noqa: E402
from decision.gate import DEFAULT_DIRECT, DEFAULT_FLOOR, DEFAULT_VETO  # noqa: E402
from decision.intent import FORMAT, MODEL_PATH, Decision, IntentModel  # noqa: E402

REFERENCE_PATH = MODEL_PATH.with_name("intent_v1.reference.json")
REFERENCE_LINES = 200
WEIGHT_DECIMALS = 4
IDF_DECIMALS = 5
MAX_FILE_BYTES = 2_000_000
FOLDS = 5
SEED = 0

NGRAMS = ((1, 4), (2, 5), (1, 5))
MIN_DF = (1, 2)
FEATURE_CAPS = (5000, 8000, 12000)
ARABIC_READING = (True, False)
C_VALUES = (3.0, 10.0, 30.0, 100.0, 300.0)
CLASS_WEIGHTS = (None, "balanced")
FIRST_C = 10.0
SETTING_KEYS = ("arabic_reading", "ngram", "min_df", "max_features", "C", "class_weight")
SECOND_STAGE = 3  # how many of the best text settings go on to the C / class-weight search


# ------------------------------------------------------------------ the scikit-learn pipeline

def make_vectorizer(settings: dict, vocabulary: dict[str, int] | None = None) -> TfidfVectorizer:
    reader = functools.partial(prepare, reading=bool(settings.get("arabic_reading", False)))
    return TfidfVectorizer(preprocessor=reader, analyzer="char_wb", ngram_range=tuple(settings["ngram"]),
                           sublinear_tf=True, smooth_idf=True, norm="l2", min_df=settings.get("min_df", 1),
                           max_features=None if vocabulary is not None else settings.get("max_features"),
                           vocabulary=vocabulary, dtype=np.float64)


def make_classifier(settings: dict) -> LogisticRegression:
    return LogisticRegression(C=settings["C"], class_weight=settings.get("class_weight"), max_iter=3000, tol=1e-6)


def fit(rows: list[dict], settings: dict) -> tuple[TfidfVectorizer, LogisticRegression]:
    vectorizer = make_vectorizer(settings)
    x = vectorizer.fit_transform([row["text"] for row in rows])
    classifier = make_classifier(settings).fit(x, [row["label"] for row in rows])
    return vectorizer, classifier


def sklearn_pipeline(model: dict) -> tuple[TfidfVectorizer, LogisticRegression]:
    """The scikit-learn objects rebuilt from an exported model file (for the equality test)."""
    vectorizer = make_vectorizer(model["settings"], {gram: i for i, gram in enumerate(model["vocabulary"])})
    vectorizer.idf_ = np.array(model["idf"], dtype=np.float64)
    classifier = LogisticRegression()
    classifier.classes_ = np.array(model["labels"])
    classifier.coef_ = np.array(model["coef"], dtype=np.float64).T.copy()
    classifier.intercept_ = np.array(model["intercept"], dtype=np.float64)
    classifier.n_features_in_ = len(model["vocabulary"])
    return vectorizer, classifier


def softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - logits.max(axis=1, keepdims=True)
    exps = np.exp(shifted)
    return exps / exps.sum(axis=1, keepdims=True)


def as_decisions(probabilities: np.ndarray, labels: list[str]) -> list[Decision]:
    out = []
    for row in probabilities:
        best = int(row.argmax())
        out.append(Decision(labels[best], float(row[best]), {label: float(p) for label, p in zip(labels, row)}))
    return out


# ------------------------------------------------------------------ search on the tune set

def evaluate(train: list[dict], tune: list[dict], settings: dict) -> dict:
    vectorizer, classifier = fit(train, settings)
    labels = list(classifier.classes_)
    probabilities = classifier.predict_proba(vectorizer.transform([row["text"] for row in tune]))
    result = metrics.score(tune, as_decisions(probabilities, labels), labels)
    return {**settings, "features": len(vectorizer.vocabulary_), "accuracy": result["accuracy"],
            "log_loss": result["log_loss"]}


def rank(result: dict) -> tuple:
    """Best first: tune accuracy, then tune log loss, then the smaller model."""
    return (-result["accuracy"], result["log_loss"], result["features"])


def search(train: list[dict], tune: list[dict], say=print) -> tuple[dict, list[dict]]:
    """(the chosen settings, every setting tried with its tune score). The tune set decides, nothing else."""
    tried = []
    for reading in ARABIC_READING:
        for ngram in NGRAMS:
            for min_df in MIN_DF:
                for cap in FEATURE_CAPS:
                    settings = {"arabic_reading": reading, "ngram": list(ngram), "min_df": min_df,
                                "max_features": cap, "C": FIRST_C, "class_weight": None}
                    tried.append(evaluate(train, tune, settings))
                    say("  text settings", json.dumps(tried[-1]))
    seen = {tuple(json.dumps(r[key]) for key in SETTING_KEYS) for r in tried}
    for base in sorted(tried, key=rank)[:SECOND_STAGE]:
        for c in C_VALUES:
            for weight in CLASS_WEIGHTS:
                settings = {**{key: base[key] for key in SETTING_KEYS}, "C": c, "class_weight": weight}
                key = tuple(json.dumps(settings[k]) for k in SETTING_KEYS)
                if key in seen:
                    continue
                seen.add(key)
                tried.append(evaluate(train, tune, settings))
                say("  classifier settings", json.dumps(tried[-1]))
    best = sorted(tried, key=rank)[0]
    return {key: best[key] for key in SETTING_KEYS}, tried


# ------------------------------------------------------------------ calibration

def out_of_fold(train: list[dict], settings: dict) -> tuple[np.ndarray, list[str], np.ndarray, list[tuple]]:
    """Scores for every train line from a model that did not see it (5 folds).

    (logits, labels, the fold of each line, the (vectorizer, classifier) of each fold)."""
    texts = np.array([row["text"] for row in train], dtype=object)
    y = np.array([row["label"] for row in train])
    labels = sorted(set(y))
    logits = np.zeros((len(train), len(labels)))
    fold_of = np.zeros(len(train), dtype=int)
    models = []
    splits = StratifiedKFold(FOLDS, shuffle=True, random_state=SEED).split(texts, y)
    for fold, (fit_idx, held_idx) in enumerate(splits):
        vectorizer = make_vectorizer(settings)
        x = vectorizer.fit_transform(texts[fit_idx])
        classifier = make_classifier(settings).fit(x, y[fit_idx])
        assert list(classifier.classes_) == labels
        logits[held_idx] = classifier.decision_function(vectorizer.transform(texts[held_idx]))
        fold_of[held_idx] = fold
        models.append((vectorizer, classifier))
    return logits, labels, fold_of, models


def out_of_fold_second_readings(train: list[dict], decisions: list[Decision], fold_of: np.ndarray, models: list[tuple],
                                temperature: float, labels: list[str]) -> tuple[list[float], list[float]]:
    """The gate's second reading in parts (decision/metrics.py) for the train lines, each line's parts read by
    the model of its own fold, which never saw that line."""
    others, payment_parts = [0.0] * len(train), [0.0] * len(train)
    for fold, (vectorizer, classifier) in enumerate(models):
        def read_many(texts, vectorizer=vectorizer, classifier=classifier):
            if not texts:
                return []
            scores = classifier.decision_function(vectorizer.transform(list(texts)))
            return as_decisions(softmax(scores / temperature), labels)

        held = [i for i in range(len(train)) if fold_of[i] == fold]
        got = metrics.second_readings(read_many, [train[i] for i in held], [decisions[i] for i in held],
                                      DEFAULT_DIRECT, DEFAULT_VETO)
        for i, other, part in zip(held, *got):
            others[i], payment_parts[i] = other, part
    return others, payment_parts


def fit_temperature(logits: np.ndarray, truth: np.ndarray) -> float:
    """The one number T that makes softmax(logits / T) fit the truth best (golden-section search on log T)."""
    def loss(log_t: float) -> float:
        p = softmax(logits / math.exp(log_t))
        return float(-np.log(np.clip(p[np.arange(len(truth)), truth], 1e-12, None)).mean())

    low, high = math.log(0.05), math.log(20.0)
    ratio = (math.sqrt(5) - 1) / 2
    a, b = high - ratio * (high - low), low + ratio * (high - low)
    for _ in range(80):
        if loss(a) < loss(b):
            high, b = b, a
            a = high - ratio * (high - low)
        else:
            low, a = a, b
            b = low + ratio * (high - low)
    return math.exp((low + high) / 2)


# ------------------------------------------------------------------ export

def freeze(vectorizer: TfidfVectorizer, classifier: LogisticRegression, temperature: float) -> None:
    """Fold the temperature into the weights and round, IN the scikit-learn objects: what is exported is what
    scikit-learn itself now computes."""
    vectorizer.idf_ = np.round(vectorizer.idf_, IDF_DECIMALS)
    classifier.coef_ = np.round(classifier.coef_ / temperature, WEIGHT_DECIMALS)
    classifier.intercept_ = np.round(classifier.intercept_ / temperature, WEIGHT_DECIMALS)


def export(vectorizer: TfidfVectorizer, classifier: LogisticRegression, head: dict) -> dict:
    vocabulary = [gram for gram, _ in sorted(vectorizer.vocabulary_.items(), key=lambda item: item[1])]
    return {**head,
            "labels": [str(label) for label in classifier.classes_],
            "intercept": [float(v) for v in classifier.intercept_],
            "vocabulary": vocabulary,
            "idf": [float(v) for v in vectorizer.idf_],
            "coef": [[float(v) for v in column] for column in classifier.coef_.T]}


def dump(model: dict) -> str:
    """JSON with the readable part on top and the three long arrays on one line each."""
    head = {key: value for key, value in model.items() if key not in ("vocabulary", "idf", "coef")}
    text = json.dumps(head, ensure_ascii=False, indent=1)[:-2]
    for key in ("vocabulary", "idf", "coef"):
        text += f',\n "{key}": ' + json.dumps(model[key], ensure_ascii=False, separators=(",", ":"))
    return text + "\n}\n"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def summary(result: dict) -> dict:
    return {"lines": result["lines"], "correct": result["correct"], "accuracy": round(result["accuracy"], 4),
            "log_loss": round(result["log_loss"], 4), "ece": round(result["calibration"]["ece"], 4)}


def path_summary(result: dict) -> dict:
    return {key: (round(value, 4) if isinstance(value, float) else value)
            for key, value in result.items() if key not in ("wrong", "actions")}


# ------------------------------------------------------------------ main

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--date", default=date.today().isoformat(), help="training date written into the file")
    args = parser.parse_args(argv)

    lock = read_lock()
    if lock is None:
        print("REFUSED: decision/data/LOCK.json is missing. Run python -m decision.data.build first: the locked "
              "set is frozen before any training.", file=sys.stderr)
        return 3
    train, tune = load_part("train"), load_part("tune")
    print(f"train {len(train)} lines, tune {len(tune)} lines; locked.jsonl is not opened")

    print("search on the tune set")
    settings, tried = search(train, tune)
    print("chosen:", json.dumps(settings))

    logits, labels, fold_of, fold_models = out_of_fold(train, settings)
    assert sorted(labels) == sorted(ACTIONS), "the train set must hold every action"
    truth = np.array([labels.index(row["label"]) for row in train])
    temperature = fit_temperature(logits, truth)
    oof_decisions = as_decisions(softmax(logits / temperature), labels)
    oof = metrics.score(train, oof_decisions, labels)
    print(f"temperature {temperature:.4f} (out-of-fold train accuracy {oof['accuracy']:.4f})")

    vectorizer, classifier = fit(train, settings)
    assert list(classifier.classes_) == labels
    tune_texts = [row["text"] for row in tune]
    before = metrics.score(tune, as_decisions(classifier.predict_proba(vectorizer.transform(tune_texts)), labels),
                           labels)
    freeze(vectorizer, classifier, temperature)
    reference = classifier.predict_proba(vectorizer.transform(tune_texts))

    head = {
        "name": "intent_v1", "format": FORMAT,
        "kind": "character n-gram TF-IDF + multinomial logistic regression, read by decision/intent.py",
        "trained": args.date,
        "settings": {"text": "decision.features.prepare", "arabic_reading": settings["arabic_reading"],
                     "analyzer": "char_wb", "ngram": settings["ngram"], "sublinear_tf": True, "smooth_idf": True,
                     "norm": "l2", "min_df": settings["min_df"], "max_features": settings["max_features"],
                     "C": settings["C"], "class_weight": settings["class_weight"],
                     "temperature": round(temperature, 4), "weight_decimals": WEIGHT_DECIMALS,
                     "idf_decimals": IDF_DECIMALS},
        "built_with": {"scikit-learn": sklearn.__version__, "numpy": np.__version__},
        "data": {"train_lines": len(train), "tune_lines": len(tune), "locked_lines_never_read": lock["lines"],
                 "train_sha256": file_sha256(DATA_DIR / "train.jsonl"),
                 "tune_sha256": file_sha256(DATA_DIR / "tune.jsonl"), "locked_sha256": lock["sha256"],
                 "settings_tried_on_tune": len(tried)},
    }
    model = export(vectorizer, classifier, head)
    write(MODEL_PATH, dump(model))

    # From here on the numbers come from the exported file, read by the pure-Python reader.
    reader = IntentModel(MODEL_PATH)
    tune_decisions = [reader.predict(text) for text in tune_texts]
    gap = max(abs(p - q) for row, text in zip(reference, tune_texts) for p, q in zip(row, reader.probabilities(text)))
    assert gap < 1e-9, f"the pure-Python reader differs from scikit-learn by {gap}"
    after = metrics.score(tune, tune_decisions, labels)
    print(f"tune: accuracy {after['accuracy']:.4f}; ECE {before['calibration']['ece']:.4f} before the temperature, "
          f"{after['calibration']['ece']:.4f} after; pure Python vs scikit-learn max gap {gap:.2e}")

    # The gate's thresholds. The second reading in parts uses the exported model for the tune lines and each
    # fold's own model for the train lines.
    tune_others, tune_parts = metrics.second_readings(lambda texts: [reader.predict(text) for text in texts],
                                                      tune, tune_decisions, DEFAULT_DIRECT, DEFAULT_VETO)
    oof_others, oof_parts = out_of_fold_second_readings(train, oof_decisions, fold_of, fold_models, temperature,
                                                        labels)
    direct = metrics.choose_direct([(tune, tune_decisions, tune_others), (train, oof_decisions, oof_others)],
                                   DEFAULT_DIRECT)
    part = direct["part"]
    veto = metrics.choose_veto([(tune, tune_decisions, tune_parts), (train, oof_decisions, oof_parts)], part,
                               DEFAULT_VETO)
    gate = {
        "direct": direct["direct"] if direct["direct"] is not None else metrics.OFF,
        "direct_actions": direct["direct_actions"],
        "veto": veto if veto is not None else metrics.OFF,
        "floor": DEFAULT_FLOOR,
        "part": part,
        "target_precision": metrics.TARGET_PRECISION,
        "chosen_on": "the tune set, with out-of-fold predictions of the train set as a second witness; "
                     "never the locked set. 1.01 means: this path is switched off.",
    }
    gate["tune"] = {
        "direct": path_summary(metrics.direct_path(tune, tune_decisions, gate["direct"], gate["direct_actions"],
                                                   tune_others, part)),
        "veto": path_summary(metrics.veto_path(tune, tune_decisions, gate["veto"], tune_parts, part)),
        "fallback": path_summary(metrics.fallback_path(tune, tune_decisions, gate["floor"])),
    }
    gate["train_out_of_fold"] = {
        "direct": path_summary(metrics.direct_path(train, oof_decisions, gate["direct"], gate["direct_actions"],
                                                   oof_others, part)),
        "veto": path_summary(metrics.veto_path(train, oof_decisions, gate["veto"], oof_parts, part)),
    }
    model = export(vectorizer, classifier, {**head, "tune": {"before_temperature": summary(before), **summary(after)},
                                            "train_out_of_fold": summary(oof), "gate": gate})
    text = dump(model)
    write(MODEL_PATH, text)
    size = len(text.encode("utf-8"))
    assert size <= MAX_FILE_BYTES, f"the model file is {size} bytes, over {MAX_FILE_BYTES}"

    write(REFERENCE_PATH, json.dumps({
        "what": "scikit-learn's own probabilities for the first lines of tune.jsonl, written at training time. "
                "tests/test_decision_intent.py checks the pure-Python reader against them.",
        "model_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "labels": labels,
        "rows": [{"text": row["text"], "probabilities": [float(p) for p in probs]}
                 for row, probs in list(zip(tune, reference))[:REFERENCE_LINES]],
    }, ensure_ascii=False, indent=None, separators=(",", ":")).replace('{"text"', '\n{"text"') + "\n")

    print(f"wrote {MODEL_PATH.relative_to(ROOT)} ({size} bytes, {len(model['vocabulary'])} n-grams) and "
          f"{REFERENCE_PATH.name}")
    print("gate:", json.dumps({key: gate[key] for key in ("direct", "direct_actions", "veto", "floor", "part")}))
    print("gate on tune:", json.dumps(gate["tune"]))
    print("gate on out-of-fold train:", json.dumps(gate["train_out_of_fold"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
