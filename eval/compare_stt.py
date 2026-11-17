"""Which speech-to-text model hears Iraqi voice notes best? Same notes, same scoring, one table.

    python -m eval.compare_stt --model NAME=provider:model --allow-paid      # any route of the model layer
    python -m eval.compare_stt --model turbo=local:large-v3-turbo           # faster-whisper on this CPU
    python -m eval.compare_stt --notes data/voice/billpay/notes.jsonl --model ...

    --model [NAME=]ROUTE   provider:model   a route for the "stt" role, exactly as in docs/MODELS.md: the
                                            provider (its address, its key names, multipart or JSON body) is
                                            set in the environment or in the LLM_CONFIG file. Example, Cohere:
                                            {"providers": {"cohere": {"base_url": "https://api.cohere.com/v2",
                                             "keys": ["COHERE_API_KEY"], "options": {"transcribe_body":
                                             "multipart", "extra_body": {"temperature": 0}}}}}
                                            then --model cohere:cohere-transcribe-arabic-07-2026
                           local:<size or folder>  faster-whisper on the CPU (pip install faster-whisper av)
                           NAME is the label in the table (default: the route). Repeatable.
    --notes PATH   a notes.jsonl: id, text (what was said), checks; the .ogg sits next to it (default data/voice)
    --allow-paid   needed for any route that is not local; nothing is sent without it
    --json PATH    write the table and every transcript (default: print only)
    --rescore PATH score the transcripts saved in an earlier --json file again, against the checks in --notes
                   as they are now (no model is called); the file is rewritten in place

Scoring, per note (the same for every model):
    checks   the details the bot must not get wrong, each a pattern on the folded text: a name, a brand,
             an amount, every digit of a phone or account number in order. A note passes a check or not.
    sim      character similarity to what was said after folding (hamza, ta marbuta, diacritics, ج/چ, digits)
    sim-nn   the same with number words and digits removed, so "34" and "اربعة وثلاثين" are judged only
             by the checks

Keys are read from the environment at the moment of each call and never printed.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import statistics
import sys
import time
import unicodedata
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NOTES = ROOT / "data" / "voice" / "notes.jsonl"

# ---------------------------------------------------------------- Arabic text scoring
_DIAC = re.compile(r"[ً-ْٰـ]")
_FOLD = [("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ٱ", "ا"), ("ى", "ي"), ("ة", "ه"), ("گ", "ك"), ("ڭ", "ك"),
         ("چ", "ج"), ("ڤ", "ف"), ("ؤ", "و"), ("ئ", "ي"), ("پ", "ب")]
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def norm_ar(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").translate(_AR_DIGITS).lower()
    s = _DIAC.sub("", s)
    for a, b in _FOLD:
        s = s.replace(a, b)
    s = re.sub(r"(?<=\d)[.,٫](?=\d)", ".", s)
    s = re.sub(r"[^\w\s.]|(?<!\d)\.|\.(?!\d)", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_NUM_WORDS = {
    "صفر", "واحد", "وحده", "اثنين", "اثنان", "ثنين", "ثلاث", "ثلاثه", "ثلاثة", "اربع", "اربعه", "خمس", "خمسه",
    "ست", "سته", "سبع", "سبعه", "ثمان", "ثمانيه", "ثمنيه", "ثمانه", "تسع", "تسعه", "عشر", "عشره",
    "عشرين", "ثلاثين", "اربعين", "خمسين", "ستين", "سبعين", "ثمانين", "تسعين", "ميه", "مئه", "مية",
    "الف", "الاف", "مليون", "طعش", "نص", "نصف", "ربع",
}


def _is_num(tok: str) -> bool:
    if re.fullmatch(r"[\d.]+", tok):
        return True
    t = tok[1:] if tok.startswith("و") and len(tok) > 3 else tok
    return (t in _NUM_WORDS or tok in _NUM_WORDS
            or re.fullmatch(r"(ثلاث|اربع|خمس|ست|سبع|ثمن|تسع)(ميه|مية|طعش)", t) is not None)


def strip_numbers(s: str) -> str:
    return " ".join(t for t in norm_ar(s).split() if not _is_num(t))


def char_sim(a: str, b: str) -> float:
    return round(difflib.SequenceMatcher(None, a, b, autojunk=False).ratio(), 3)


def word_error_rate(ref: str, hyp: str) -> float:
    rw, hw = ref.split(), hyp.split()
    sm = difflib.SequenceMatcher(None, rw, hw, autojunk=False)
    edits = sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal")
    return round(edits / max(1, len(rw)), 3)


_DIGIT_WORDS = {"صفر": "0", "واحد": "1", "اثنين": "2", "اثنان": "2", "ثنين": "2", "ثلاثه": "3", "ثلاث": "3",
                "اربعه": "4", "اربع": "4", "خمسه": "5", "خمس": "5", "سته": "6", "ست": "6", "سبعه": "7",
                "سبع": "7", "ثمانيه": "8", "ثمنيه": "8", "ثمان": "8", "تسعه": "9", "تسع": "9"}


def spoken_digits(s: str) -> str:
    """Every digit the text contains, in order, whether written as digits or as single digit words."""
    out = []
    for tok in norm_ar(s).split():
        if re.fullmatch(r"\d+", tok):
            out.append(tok)
            continue
        t = tok[1:] if tok.startswith("و") and tok[1:] in _DIGIT_WORDS else tok
        if t in _DIGIT_WORDS:
            out.append(_DIGIT_WORDS[t])
    return "".join(out)


def score(intended: str, heard: str, checks: list[dict]) -> dict:
    a, b = norm_ar(intended), norm_ar(heard)
    an, bn = strip_numbers(intended), strip_numbers(heard)
    results = []
    for c in checks:
        if c["kind"] in ("phone", "digits"):
            ok = spoken_digits(heard) == c["digits"] if c.get("exact") else c["digits"] in spoken_digits(heard)
        else:
            ok = re.search(c["pattern"], b) is not None
        results.append({"what": c["what"], "kind": c["kind"], "ok": ok})
    return {"sim": char_sim(a, b), "sim_nn": char_sim(an, bn), "wer_nn": word_error_rate(an, bn),
            "checks": results, "checks_ok": sum(r["ok"] for r in results), "checks_total": len(results)}


# ---------------------------------------------------------------- engines
Engine = Callable[[Path], tuple[str, float]]   # audio file -> (text, cost in USD)


def route_engine(route: str) -> tuple[Engine, bool]:
    """A route of the model layer for the "stt" role. (engine, local?)"""
    from llm import LLM, Registry
    registry = Registry.from_env(dict(os.environ, LLM_STT=route))
    llm = LLM(registry)
    if not llm.has("stt"):
        raise SystemExit(f"{route!r}: no usable route (unknown provider, or its key is not set)")
    local = all(e["local"] for e in registry.status("stt"))

    def run(path: Path) -> tuple[str, float]:
        before = llm.spent_usd
        text = llm.transcribe("stt", path.read_bytes(), path.suffix[1:], language="ar", timeout_s=120)
        return text.strip(), llm.spent_usd - before
    return run, local


def _decode16k(path: Path):
    import av
    import numpy as np
    chunks = []
    with av.open(str(path)) as c:
        rs = av.AudioResampler(format="s16", layout="mono", rate=16000)
        for frame in c.decode(audio=0):
            chunks += [f.to_ndarray().reshape(-1) for f in rs.resample(frame)]
        chunks += [f.to_ndarray().reshape(-1) for f in rs.resample(None)]
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def local_whisper_engine(size_or_dir: str) -> Engine:
    from faster_whisper import WhisperModel
    m = WhisperModel(size_or_dir, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 4)

    def run(path: Path) -> tuple[str, float]:
        segs, _ = m.transcribe(_decode16k(path), language="ar", beam_size=5, vad_filter=False)
        return " ".join(s.text.strip() for s in segs).strip(), 0.0
    return run


def make_engine(route: str) -> tuple[Engine, bool]:
    """(engine, hosted?)"""
    if route.startswith("local:"):
        return local_whisper_engine(route.split(":", 1)[1]), False
    engine, local = route_engine(route)
    return engine, not local


def split_label(arg: str) -> tuple[str, str]:
    """"name=route" -> (name, route); a bare route is its own name."""
    name, eq, route = arg.partition("=")
    return (name, route) if eq and ":" in route else (arg, arg)


# ---------------------------------------------------------------- run
def load_notes(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_model(name: str, engine: Engine, notes: list[dict], folder: Path, out=sys.stdout) -> dict:
    rows = []
    for n in notes:
        t0 = time.perf_counter()
        try:
            text, cost = engine(folder / f"{n['id']}.ogg")
            err = None
        except Exception as exc:  # one failed call is a failed note, not a failed run
            text, cost, err = "", 0.0, f"{type(exc).__name__}: {str(exc)[:160]}"
        dt = time.perf_counter() - t0
        sc = score(n["text"], text, n["checks"])
        rows.append({"note": n["id"], "said": n["text"], "heard": text, "seconds": round(dt, 2), "cost": cost,
                     "error": err, **sc})
        missed = ", ".join(c["what"] for c in sc["checks"] if not c["ok"])
        print(f"  {name:<36} {n['id']:<26} {dt:5.2f}s  checks {sc['checks_ok']}/{sc['checks_total']}"
              f"{'  missed: ' + missed if missed else ''}{'  ERROR ' + err if err else ''}", file=out, flush=True)
    agg = aggregate(rows)
    return {"agg": agg, "rows": rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", action="append", default=[])
    ap.add_argument("--rescore", type=Path)
    ap.add_argument("--notes", type=Path, default=DEFAULT_NOTES)
    ap.add_argument("--allow-paid", action="store_true")
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    notes = load_notes(a.notes)
    if a.rescore:
        return rescore(a.rescore, notes)
    if not a.model:
        ap.error("give at least one --model (or --rescore PATH)")
    models = [split_label(m) for m in a.model]
    if not a.allow_paid:
        hosted = [route for _, route in models if not route.startswith("local:")]
        if hosted:
            print(f"refused: {', '.join(hosted)} may be hosted; add --allow-paid", file=sys.stderr)
            return 2
    print(f"{len(notes)} notes x {len(models)} models = {len(notes) * len(models)} calls")
    result = {"notes": str(a.notes.relative_to(ROOT) if a.notes.is_relative_to(ROOT) else a.notes), "models": {}}
    for name, route in models:
        engine, _ = make_engine(route)
        result["models"][name] = run_model(name, engine, notes, a.notes.parent)
    print_table(result)
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


def aggregate(rows: list[dict]) -> dict:
    secs = [r["seconds"] for r in rows]
    return {"checks": f"{sum(r['checks_ok'] for r in rows)}/{sum(r['checks_total'] for r in rows)}",
            "notes_all_checks": f"{sum(r['checks_ok'] == r['checks_total'] for r in rows)}/{len(rows)}",
            "sim": round(statistics.fmean(r["sim"] for r in rows), 3),
            "sim_nn": round(statistics.fmean(r["sim_nn"] for r in rows), 3),
            "wer_nn": round(statistics.fmean(r["wer_nn"] for r in rows), 3),
            "seconds_p50": round(statistics.median(secs), 2), "seconds_max": round(max(secs), 2),
            "cost_usd": round(sum(r["cost"] for r in rows), 6), "errors": sum(bool(r["error"]) for r in rows)}


def rescore(path: Path, notes: list[dict]) -> int:
    by_id = {n["id"]: n for n in notes}
    result = json.loads(path.read_text(encoding="utf-8"))
    for r in result["models"].values():
        for row in r["rows"]:
            n = by_id[row["note"]]
            row.update(said=n["text"], **score(n["text"], row["heard"], n["checks"]))
        r["agg"] = aggregate(r["rows"])
    result["rescored_against"] = "the checks in the notes file at rescore time"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print_table(result)
    return 0


def print_table(result: dict) -> None:
    print(f"\n{'model':<44} {'checks':>8} {'notes ok':>9} {'sim':>6} {'sim-nn':>7} {'p50 s':>6} {'cost $':>9} {'errors':>6}")
    for name, r in result["models"].items():
        g = r["agg"]
        print(f"{name:<44} {g['checks']:>8} {g['notes_all_checks']:>9} {g['sim']:>6.3f} {g['sim_nn']:>7.3f} "
              f"{g['seconds_p50']:>6.2f} {g['cost_usd']:>9.5f} {g['errors']:>6}")


if __name__ == "__main__":
    sys.exit(main())
