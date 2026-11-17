"""eval/run_decision.py: the tune report, and the rules of the one blind run on the locked set.

Every locked run here happens in a temporary folder on made-up "locked" lines. The real locked file is never
scored by a test.
"""

import hashlib
import json
import shutil

import pytest

from decision import IntentModel
from decision.data import build as B
from decision.intent import MODEL_PATH
from eval import run_decision as R

TRAIN = B.load_part("train")


@pytest.fixture()
def sandbox(tmp_path):
    """A data folder with the real tune file and a small made-up locked file with its own lock."""
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(B.DATA_DIR / "tune.jsonl", data / "tune.jsonl")
    rows = TRAIN[:60] + [
        {"text": "شكد رصيدي", "label": "transfer", "style": "wrong_on_purpose", "source": "w1"},
        {"text": "الغي | cancel", "label": "pay_bill", "style": "wrong_on_purpose", "source": "w4"},
    ]
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    (data / "locked.jsonl").write_text(text, encoding="utf-8", newline="\n")
    lock = {"file": "locked.jsonl", "sha256": B.text_sha256(text), "lines": len(rows), "frozen": "2026-01-01",
            "note": "made up for a test"}
    (data / B.LOCK_NAME).write_text(json.dumps(lock), encoding="utf-8")
    return data, tmp_path / "RESULTS.md", rows


def run(sandbox, *argv, today="2026-01-02"):
    data, results, _ = sandbox
    return R.main(list(argv), data_dir=data, results_path=results, today=today)


def test_the_default_paths_are_the_ones_in_the_repo():
    assert R.RESULTS_PATH == R.ROOT / "decision" / "RESULTS.md"
    assert R.DATA_DIR == B.DATA_DIR and R.MODEL_PATH == MODEL_PATH


def test_the_tune_run_prints_every_number_and_writes_nothing(sandbox, capsys):
    assert run(sandbox) == 0
    out = capsys.readouterr().out
    model = IntentModel.load()
    stored = model.meta["tune"]
    assert f"- Accuracy: {stored['correct']} / {stored['lines']}" in out
    for label in model.labels:
        assert f"| {label} |" in out and f"| **{label}** |" in out  # precision / recall and the confusion matrix
    for needle in ("Per label:", "Confusion matrix", "Per style slice:", "Direct path (`Gate.before`",
                   "Cross-check (`Gate.after`", "Fallback (`Gate.fallback`", "Precision of an overrule:",
                   "Missed lines (", "optimistic"):
        assert needle in out, needle
    assert not sandbox[1].exists()
    assert run(sandbox, "--set", "tune") == 0


def test_the_locked_set_runs_once_and_every_missed_line_is_written_down(sandbox, capsys):
    data, results, rows = sandbox
    assert run(sandbox, "--set", "locked", "--dry") == 0
    assert "blind run" in capsys.readouterr().out and not results.exists()

    assert run(sandbox, "--set", "locked") == 0
    text = results.read_text(encoding="utf-8")
    assert text.startswith(R.RESULTS_HEADER)
    assert "## 2026-01-02 | intent_v1 | blind run" in text
    lock = B.read_lock(data)
    assert f"sha256 `{lock['sha256'][:16]}`" in text and f"{len(rows)} lines" in text
    assert hashlib.sha256(MODEL_PATH.read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:16] in text

    model = IntentModel.load()
    missed = [row for row in rows if model.predict(row["text"]).label != row["label"]]
    assert len(missed) >= 2
    assert f"- Accuracy: {len(rows) - len(missed)} / {len(rows)}" in text
    assert f"Missed lines ({len(missed)}, all of them):" in text
    for row in missed:
        assert f"| {R.cell(row['text'])} |" in text, row["text"]
    assert "| الغي / cancel |" in text  # a bar in a message cannot break the table
    assert R.past_runs(results) == [{"model": "intent_v1", "model_sha256": R.model_sha256(model)[:16], "blind": True,
                                     "correct": len(rows) - len(missed), "lines": len(rows)}]

    capsys.readouterr()
    assert run(sandbox, "--set", "locked") == 4  # a second blind run is refused
    assert "REFUSED" in capsys.readouterr().err
    assert results.read_text(encoding="utf-8") == text

    assert run(sandbox, "--set", "locked", "--regression", today="2026-01-03") == 0
    after = results.read_text(encoding="utf-8")
    assert after.startswith(text)
    assert "## 2026-01-03 | intent_v1 | regression check, not blind" in after
    assert [run_["blind"] for run_ in R.past_runs(results)] == [True, False]
    assert run(sandbox, "--set", "locked") == 4
    assert run(sandbox, "--set", "locked", "--dry", "--regression") == 0
    assert results.read_text(encoding="utf-8") == after


def test_a_regression_run_needs_a_blind_run_before_it(sandbox, capsys):
    assert run(sandbox, "--set", "locked", "--regression") == 4
    assert "no blind run yet" in capsys.readouterr().err
    assert not sandbox[1].exists()


def test_a_changed_locked_file_is_never_run(sandbox, capsys):
    data, results, _ = sandbox
    path = data / "locked.jsonl"
    path.write_text(path.read_text(encoding="utf-8").replace("شكد رصيدي", "شكد رصيدي هسه"), encoding="utf-8",
                    newline="\n")
    for argv in (("--set", "locked"), ("--set", "locked", "--dry"), ("--set", "locked", "--regression")):
        assert run(sandbox, *argv) == 3
        assert "REFUSED" in capsys.readouterr().err
    assert not results.exists()
    (data / B.LOCK_NAME).unlink()
    assert run(sandbox, "--set", "locked") == 3
    assert run(sandbox) == 0  # the tune run does not need the lock


def test_a_missing_model_file_is_reported(sandbox, tmp_path, capsys):
    data, results, _ = sandbox
    assert R.main(["--set", "locked"], data_dir=data, results_path=results, model_path=tmp_path / "none.json") == 2
    assert "decision.train" in capsys.readouterr().err
    assert not results.exists()


def test_the_recorded_runs_in_the_repo_follow_the_rules():
    """The locked set had its blind run on 2026-11-13, so decision/RESULTS.md must be there (without it the
    script would record a second "blind" run): one blind run, first, on the frozen file; the newest run is of
    the model file that is shipped (after a retrain, record a regression run)."""
    assert R.RESULTS_PATH.is_file(), "decision/RESULTS.md is the record of the one blind run: restore it"
    runs = R.past_runs(R.RESULTS_PATH)
    assert runs and runs[0]["blind"] and not any(run_["blind"] for run_ in runs[1:])
    text = R.RESULTS_PATH.read_text(encoding="utf-8")
    lock = B.read_lock()
    assert text.count(f"sha256 `{lock['sha256'][:16]}`") == len(runs)
    assert all(run_["lines"] == lock["lines"] for run_ in runs)
    assert runs[-1]["model_sha256"] == R.model_sha256(IntentModel.load())[:16]
    assert chr(0x2014) not in text and B.CHE not in text
