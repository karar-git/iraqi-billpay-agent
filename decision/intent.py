"""The intent decision model at run time: pure Python, standard library only, no network.

It reads one message and picks one action from the fixed list `core.interface.ACTIONS`, with a confidence.
The model file (decision/models/intent_v1.json) is written by decision/train.py: character n-gram TF-IDF
weights and a multinomial logistic regression. Inference here repeats that arithmetic exactly:

    counts of the known n-grams  ->  (1 + ln count) * idf  ->  scaled to length 1  ->  weights . x + intercept
    ->  softmax

The file is parsed on first use (a few tens of milliseconds), a prediction takes well under a millisecond.
"""

from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass
from pathlib import Path

from decision.features import ngrams, prepare

MODEL_PATH = Path(__file__).resolve().parent / "models" / "intent_v1.json"
FORMAT = 1


@dataclass(frozen=True)
class Decision:
    """One reading of a message: the chosen label, how sure (0..1), and the score of every label."""

    label: str
    confidence: float
    scores: dict[str, float]


class IntentModel:
    """Pure-Python inference from a model file. `IntentModel.load()` is cheap; the file is read on first use."""

    _default: "IntentModel | None" = None
    _default_lock = threading.Lock()

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else MODEL_PATH
        self._lock = threading.Lock()
        self._ready = False
        self._labels: tuple[str, ...] = ()
        self._low, self._high = 1, 4
        self._reading = True
        self._features: dict[str, tuple[float, tuple[float, ...]]] = {}
        self._intercept: tuple[float, ...] = ()
        self._meta: dict = {}

    @classmethod
    def load(cls, path: str | Path | None = None) -> "IntentModel":
        """The model at `path`, or the shipped one (shared, so it is parsed once per process).

        A missing file is an error here, not at the first prediction."""
        if path is None:
            with cls._default_lock:
                if cls._default is None:
                    cls._default = cls._checked(MODEL_PATH)
                return cls._default
        return cls._checked(Path(path))

    @classmethod
    def _checked(cls, path: Path) -> "IntentModel":
        if not path.is_file():
            raise FileNotFoundError(f"decision model file not found: {path} (python -m decision.train writes it)")
        return cls(path)

    # ------------------------------------------------------------------ the file

    def _ensure(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("format") != FORMAT:
                raise ValueError(f"{self.path.name}: unknown model format {data.get('format')!r}")
            labels = tuple(data["labels"])
            vocabulary, idf, coef = data["vocabulary"], data["idf"], data["coef"]
            if not (len(vocabulary) == len(idf) == len(coef)) or any(len(row) != len(labels) for row in coef) \
                    or len(data["intercept"]) != len(labels):
                raise ValueError(f"{self.path.name}: vocabulary, idf, weights and labels do not line up")
            settings = data["settings"]
            if settings.get("analyzer") != "char_wb" or not settings.get("sublinear_tf") \
                    or settings.get("norm") != "l2":
                raise ValueError(f"{self.path.name}: settings this reader does not implement")
            self._low, self._high = (int(n) for n in settings["ngram"])
            self._reading = bool(settings.get("arabic_reading", False))
            self._features = {gram: (float(weight), tuple(float(v) for v in row))
                              for gram, weight, row in zip(vocabulary, idf, coef)}
            self._intercept = tuple(float(v) for v in data["intercept"])
            self._meta = {key: value for key, value in data.items() if key not in ("vocabulary", "idf", "coef")}
            self._labels = labels
            self._ready = True

    @property
    def labels(self) -> tuple[str, ...]:
        """The labels in the order of the scores."""
        self._ensure()
        return self._labels

    @property
    def meta(self) -> dict:
        """Everything in the model file except the weights: settings, dates, data counts, tuned thresholds."""
        self._ensure()
        return self._meta

    # ------------------------------------------------------------------ inference

    def probabilities(self, text: str) -> list[float]:
        """The probability of each label, in the order of `labels`."""
        self._ensure()
        counts: dict[str, int] = {}
        features = self._features
        for gram in ngrams(prepare(text, self._reading), self._low, self._high):
            if gram in features:
                counts[gram] = counts.get(gram, 0) + 1
        logits = list(self._intercept)
        if counts:
            values = []
            for gram, count in counts.items():
                idf, row = features[gram]
                values.append(((1.0 + math.log(count)) * idf, row))
            norm = math.sqrt(math.fsum(value * value for value, _ in values))
            for k in range(len(logits)):
                logits[k] += math.fsum(value * row[k] for value, row in values) / norm
        top = max(logits)
        exps = [math.exp(v - top) for v in logits]
        total = math.fsum(exps)
        return [e / total for e in exps]

    def predict(self, text: str) -> Decision:
        """The most likely action for one message. Always answers; a low confidence means "do not rely on me"."""
        probs = self.probabilities(text)
        best = max(range(len(probs)), key=probs.__getitem__)
        return Decision(self._labels[best], probs[best], dict(zip(self._labels, probs)))
