"""Build the decision-model data: clean the raw labelled messages, then split them once into train / tune / locked.

Run from the repo root, BEFORE any training:
    python -m decision.data.build            # writes train.jsonl, tune.jsonl, locked.jsonl, LOCK.json, BUILD.json
    python -m decision.data.build --check    # rebuild in memory and compare with the files, write nothing

Input: `decision/data/raw/*.jsonl`, one JSON object per line: {"text", "label", "style"}.

Cleaning, in this order:
1. drop a line that is not an object with a non-empty text and a label in core.interface.ACTIONS;
2. drop a line whose text has the letter U+0686 (Iraqi text in this repo writes ج);
3. drop exact duplicates: the same text after `payments.text.fold`, with white space collapsed and punctuation
   and emoji removed (the first one read is kept; files are read in name order). When the copies carry
   different labels, every copy is dropped: the label is not clear;
4. drop near duplicates: a line whose character trigrams overlap an earlier kept line by 80% or more (Jaccard).

Split: SHA-256 of the cleaned key decides the part, so a line always lands in the same part whatever else is in
the files: 70% train, 15% tune, 15% locked. The locked file is frozen by LOCK.json (its SHA-256, taken with CRLF
read as LF). Once LOCK.json exists this script never changes the locked file:
- it refuses (exit 3, nothing written) when a fresh build would give a different locked file, and
- it refuses (exit 3, nothing written) when locked.jsonl on disk no longer has the hash in LOCK.json;
- otherwise it leaves locked.jsonl alone and rewrites only train.jsonl, tune.jsonl and BUILD.json.
New data for honest testing goes into a new, separately locked file, never into this one. Removing a line from
the raw files is allowed as long as it was a train or tune line (the locked hash then stays the same).

Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # allow `python decision/data/build.py` as well as `python -m decision.data.build`
    sys.path.insert(0, str(ROOT))

from core.interface import ACTIONS  # noqa: E402
from payments.text import fold  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent
RAW_DIR = DATA_DIR / "raw"
PARTS = ("train", "tune", "locked")
LOCK_NAME = "LOCK.json"
BUILD_NAME = "BUILD.json"
FROZEN = "2026-11-13"
LOCK_NOTE = "frozen before the first training run; never used for training or tuning"
NEAR_DUPLICATE_JACCARD = 0.8
TRAIN_SHARE, TUNE_SHARE = 70, 15  # percent; the rest is locked
CHE = chr(0x0686)  # the letter this repo never writes in Iraqi text
# The four raw files, each written to a different brief (all by AI, none by a real user).
SLICES = {
    "w1": "everyday Baghdadi: short, casual, typos, digits, emoji, voice-note style",
    "w2": "other Iraqi regions (south, Mosul, Anbar), slang, Arabizi, very short and very long messages",
    "w3": "English, broken English, Modern Standard Arabic, other Arabic dialects",
    "w4": "hard cases: negation, take-backs, two requests, questions about the past, sarcasm, scams, out of scope",
}

_NOT_WORD = re.compile(r"[^\w\s]|_")


def exact_key(text: str) -> str:
    """The folded text with white space collapsed."""
    return " ".join(fold(text).split())


def clean_key(text: str) -> str:
    """The key two copies of one message share: folded, no punctuation or emoji, single spaces.

    A message that is only punctuation or emoji keeps its folded text as the key."""
    folded = exact_key(text)
    return " ".join(_NOT_WORD.sub(" ", folded).split()) or folded


def _trigrams(key: str) -> frozenset[str]:
    padded = f" {key} "
    return frozenset(padded[i:i + 3] for i in range(len(padded) - 2))


def part_of(key: str) -> str:
    """train / tune / locked from a stable hash of the cleaned key."""
    bucket = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) % 100
    if bucket < TRAIN_SHARE:
        return "train"
    return "tune" if bucket < TRAIN_SHARE + TUNE_SHARE else "locked"


def read_raw(raw_dir: Path = RAW_DIR) -> tuple[list[dict], Counter]:
    """Every usable raw line as {"text", "label", "style", "source"}, plus the count of lines dropped and why."""
    rows: list[dict] = []
    dropped: Counter = Counter()
    for path in sorted(raw_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                dropped["not_json"] += 1
                continue
            text = item.get("text") if isinstance(item, dict) else None
            if not isinstance(text, str) or not text.strip():
                dropped["no_text"] += 1
            elif item.get("label") not in ACTIONS:
                dropped["unknown_label"] += 1
            elif CHE in text:
                dropped["has_che"] += 1
            else:
                rows.append({"text": " ".join(text.split()), "label": item["label"],
                             "style": str(item.get("style") or ""), "source": path.stem})
    return rows, dropped


def dedupe(rows: list[dict], dropped: Counter) -> list[tuple[str, dict]]:
    """(key, row) for the lines that stay, in reading order."""
    by_key: dict[str, list[dict]] = {}
    for row in rows:
        by_key.setdefault(clean_key(row["text"]), []).append(row)
    kept: list[tuple[str, dict, frozenset[str]]] = []
    for key, copies in by_key.items():
        if len({row["label"] for row in copies}) > 1:
            dropped["same_text_two_labels"] += len(copies)
            continue
        dropped["exact_duplicate"] += len(copies) - 1
        grams = _trigrams(key)
        near = False
        for _, _, other in kept:
            shared = len(grams & other)
            if shared and shared / (len(grams) + len(other) - shared) >= NEAR_DUPLICATE_JACCARD:
                near = True
                break
        if near:
            dropped["near_duplicate"] += 1
        else:
            kept.append((key, copies[0], grams))
    return [(key, row) for key, row, _ in kept]


def build(raw_dir: Path = RAW_DIR) -> tuple[dict[str, str], dict]:
    """({part: file text}, the build report). Pure: reads the raw files, writes nothing."""
    rows, dropped = read_raw(raw_dir)
    raw_lines = len(rows) + sum(dropped.values())
    kept = dedupe(rows, dropped)
    parts: dict[str, list[tuple[str, dict]]] = {name: [] for name in PARTS}
    for key, row in kept:
        parts[part_of(key)].append((hashlib.sha256(key.encode("utf-8")).hexdigest(), row))
    texts: dict[str, str] = {}
    report: dict = {"raw_lines": raw_lines, "dropped": dict(sorted(dropped.items())), "kept": len(kept), "parts": {}}
    for name in PARTS:
        ordered = [row for _, row in sorted(parts[name], key=lambda pair: pair[0])]
        texts[name] = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in ordered)
        report["parts"][name] = {
            "lines": len(ordered),
            "labels": dict(sorted(Counter(row["label"] for row in ordered).items())),
            "sources": dict(sorted(Counter(row["source"] for row in ordered).items())),
        }
    return texts, report


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    """SHA-256 of a file with CRLF read as LF, so a Windows checkout gives the same hash as a Unix one."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def read_lock(data_dir: Path = DATA_DIR) -> dict | None:
    path = data_dir / LOCK_NAME
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def load_part(name: str, data_dir: Path = DATA_DIR) -> list[dict]:
    """The rows of train / tune / locked as written by this script."""
    path = data_dir / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def refusal(texts: dict[str, str], data_dir: Path = DATA_DIR) -> str | None:
    """Why this build must not be written, or None. With a LOCK.json, the locked file never changes: neither
    through a fresh build that differs from the lock, nor by writing over a locked.jsonl that was edited."""
    lock = read_lock(data_dir)
    if lock is None:
        return None
    fresh = text_sha256(texts["locked"])
    if lock["sha256"] != fresh:
        return (f"{LOCK_NAME} freezes locked.jsonl at {lock['sha256'][:16]}..., a fresh build gives {fresh[:16]}... "
                "The raw files or the rules changed. The locked set is never rebuilt: put new test data in a new, "
                "separately locked file.")
    path = data_dir / "locked.jsonl"
    if path.is_file() and file_sha256(path) != lock["sha256"]:
        return (f"locked.jsonl on disk has the hash {file_sha256(path)[:16]}..., {LOCK_NAME} says "
                f"{lock['sha256'][:16]}... The locked file was edited. It is not overwritten here: restore it from "
                "version control.")
    return None


def write_build(texts: dict[str, str], report: dict, data_dir: Path = DATA_DIR) -> dict:
    """Write the parts, BUILD.json and, the first time only, LOCK.json. Returns the lock.

    A locked.jsonl that exists is never written again, and a build that `refusal` turns down raises here
    before anything is written (so a caller that forgot to ask cannot break the lock either)."""
    why = refusal(texts, data_dir)
    if why is not None:
        raise RuntimeError(why)
    lock = read_lock(data_dir)
    for name in PARTS:
        path = data_dir / f"{name}.jsonl"
        if name == "locked" and lock is not None and path.is_file():
            continue
        path.write_text(texts[name], encoding="utf-8", newline="\n")
    if lock is None:
        lock = {"file": "locked.jsonl", "sha256": text_sha256(texts["locked"]),
                "lines": report["parts"]["locked"]["lines"], "frozen": FROZEN, "note": LOCK_NOTE}
        (data_dir / LOCK_NAME).write_text(json.dumps(lock, ensure_ascii=False, indent=1) + "\n",
                                          encoding="utf-8", newline="\n")
    (data_dir / BUILD_NAME).write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                                       encoding="utf-8", newline="\n")
    return lock


def main(argv: list[str] | None = None, raw_dir: Path = RAW_DIR, data_dir: Path = DATA_DIR) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="compare a fresh build with the files, write nothing")
    args = parser.parse_args(argv)
    texts, report = build(raw_dir)
    why = refusal(texts, data_dir)
    if why is not None:
        print(f"REFUSED: {why}", file=sys.stderr)
        return 3
    if args.check:
        lock = read_lock(data_dir)
        stale = [name for name in PARTS if not (data_dir / f"{name}.jsonl").is_file()
                 or file_sha256(data_dir / f"{name}.jsonl") != text_sha256(texts[name])]
        print("up to date" if not stale and lock is not None else f"stale or missing: {', '.join(stale) or LOCK_NAME}")
        return 0 if not stale and lock is not None else 1
    lock = write_build(texts, report, data_dir)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"locked.jsonl sha256 {lock['sha256']} ({lock['lines']} lines, frozen {lock['frozen']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
