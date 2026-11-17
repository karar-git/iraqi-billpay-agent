"""decision/README.md says what the files say: the counts, the thresholds, the blind run and every missed line.

The numbers in the README are typed by hand; these tests fail when the data, the model or the recorded run change
and the README does not. They read decision/RESULTS.md (the record of the run), never the locked lines themselves.
"""

import json
import re
from pathlib import Path

from decision import IntentModel
from decision.data import build as B
from decision.intent import MODEL_PATH
from eval import run_decision as R

README = (Path(B.DATA_DIR).parent / "README.md").read_text(encoding="utf-8")
RESULTS = R.RESULTS_PATH.read_text(encoding="utf-8")
BUILD = json.loads((B.DATA_DIR / B.BUILD_NAME).read_text(encoding="utf-8"))
META = IntentModel.load().meta


def thousands(n: int) -> str:
    return f"{n:,}"


def blind_entry() -> str:
    """The text of the blind run in RESULTS.md (the first entry)."""
    entries = re.split(r"^## ", RESULTS, flags=re.MULTILINE)[1:]
    assert entries and "| blind run" in entries[0].splitlines()[0]
    return entries[0]


def missed_rows(entry: str) -> list[list[str]]:
    """The cells of the "Missed lines" table of one entry."""
    tail = entry.split("\nMissed lines (", 1)[1]
    rows = [line for line in tail.splitlines() if line.startswith("| ")]
    return [[cell.strip() for cell in line.strip("|").split("|")] for line in rows[1:]]  # rows[0] is the header


def test_the_text_rules_hold_in_the_docs():
    for text in (README, RESULTS):
        assert chr(0x2014) not in text and chr(0x2013) not in text  # no long dashes
        assert B.CHE not in text


def test_the_readme_counts_are_the_build_report():
    for name in B.SLICES:
        lines = len([line for line in (B.RAW_DIR / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
                     if line.strip()])
        assert f"| `data/raw/{name}.jsonl` | {lines} | {B.SLICES[name]} |" in README, name
    assert f"{thousands(BUILD['raw_lines'])} raw lines" in README
    assert f"{thousands(BUILD['kept'])} lines kept" in README
    assert f"{BUILD['dropped']['exact_duplicate']} exact duplicates" in README
    assert f"{BUILD['dropped']['near_duplicate']} near duplicates" in README
    assert set(BUILD["dropped"]) == {"exact_duplicate", "near_duplicate"}  # the README says nothing else was dropped
    labels = sorted(BUILD["parts"]["train"]["labels"])
    assert "| Part | Lines | " + " | ".join(labels) + " |" in README
    for name in B.PARTS:
        part = BUILD["parts"][name]
        row = f"| `{name}.jsonl` | {thousands(part['lines'])} | " + " | ".join(str(part["labels"][label])
                                                                               for label in labels) + " |"
        assert row in README, name
    assert f"`{B.read_lock()['sha256'][:16]}...`" in README


def test_the_readme_model_facts_are_the_model_file():
    settings, gate = META["settings"], META["gate"]
    low, high = settings["ngram"]
    assert f"character n-grams of {low} to {high} letters" in README
    assert f"the {thousands(settings['max_features'])} most frequent n-grams" in README
    assert f"C = {settings['C']:g}" in README
    assert f"one temperature ({settings['temperature']:.2f})" in README
    size = len(MODEL_PATH.read_bytes().replace(b"\r\n", b"\n"))
    assert f"one {size // 1000} KB JSON file" in README
    assert f"scikit-learn {META['built_with']['scikit-learn']}" in README
    actions = " and ".join(f"`{action}`" for action in gate["direct_actions"])
    assert f"direct {gate['direct']:.2f} for {actions}, cross-check {gate['veto']:.2f}, second reading in parts " \
           f"{gate['part']:.2f}" in " ".join(README.split())
    assert f"({gate['floor']:.2f}) was set by hand" in " ".join(README.split())
    assert f"at least {gate['veto']:.2f} sure" in README and f"confidence {gate['direct']:.2f} or more" in README


def test_the_readme_accuracy_table_is_the_model_file_and_the_blind_run():
    tune, oof, tried = META["tune"], META["train_out_of_fold"], META["data"]["settings_tried_on_tune"]
    assert f"| Tune, {tune['lines']} lines | {tried} settings were tried on it" in README
    assert f"| {tune['correct']} / {tune['lines']} ({100 * tune['correct'] / tune['lines']:.1f}%) |" in README
    assert f"| {thousands(oof['correct'])} / {thousands(oof['lines'])} ({100 * oof['correct'] / oof['lines']:.1f}%) |" \
        in README
    blind = R.past_runs(R.RESULTS_PATH)[0]
    assert blind["blind"]
    score = f"{blind['correct']} / {blind['lines']} ({100 * blind['correct'] / blind['lines']:.1f}%)"
    assert f"**{score}**" in README
    assert f"- Accuracy: {score}" in blind_entry()


def test_the_readme_lists_every_missed_line_of_the_blind_run():
    blind = R.past_runs(R.RESULTS_PATH)[0]
    rows = missed_rows(blind_entry())
    assert len(rows) == blind["lines"] - blind["correct"]
    assert f"### Every missed line of the locked set ({len(rows)})" in README
    for cells in rows:
        assert "| " + " | ".join(cells) + " |" in README, cells[-1]


def test_the_readme_lists_every_missed_line_of_the_tune_set():
    model = IntentModel.load()
    missed = [(row, model.predict(row["text"])) for row in B.load_part("tune")]
    missed = [(row, decision) for row, decision in missed if decision.label != row["label"]]
    assert f"### Every missed line of the tune set ({len(missed)})" in README
    for row, decision in missed:
        line = (f"| {row['label']} | {decision.label} | {decision.confidence:.2f} | {R.cell(row['source'])} | "
                f"{R.cell(row['style'])} | {R.cell(row['text'])} |")
        assert line in README, row["text"]
