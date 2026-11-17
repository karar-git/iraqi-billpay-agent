"""eval/compare_stt.py: the scoring behind docs/SPEECH_TO_TEXT.md, offline (no model is called)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval import compare_stt as S

ROOT = Path(__file__).resolve().parents[1]
SETS = [ROOT / "data" / "voice" / "notes.jsonl", ROOT / "data" / "voice" / "billpay" / "notes.jsonl"]


@pytest.mark.parametrize("path", SETS, ids=["shop", "billpay"])
def test_every_note_has_audio_and_passes_its_own_text(path):
    notes = S.load_notes(path)
    assert len(notes) >= 8
    for n in notes:
        assert (path.parent / f"{n['id']}.ogg").stat().st_size > 2000, n["id"]
        sc = S.score(n["text"], n["text"], n["checks"])        # what was said must pass every check
        assert sc["checks_ok"] == sc["checks_total"], (n["id"], [c for c in sc["checks"] if not c["ok"]])
        assert sc["sim"] == 1.0


def test_a_doubled_digit_fails_the_phone_check():
    checks = [{"what": "phone", "kind": "phone", "digits": "07701234567"}]
    assert S.score("", "ورقمي 07701234567", checks)["checks_ok"] == 1
    assert S.score("", "ورقمي صفر سبعة سبعة صفر واحد اثنين ثلاثة اربعة خمسة ستة سبعة", checks)["checks_ok"] == 1
    assert S.score("", "ورقمي 0 7 7 0 1 2 3 4  4 5 6 7", checks)["checks_ok"] == 0     # Whisper large-v3, 15 Nov


def test_amounts_glued_to_a_letter_still_count():
    bp = {n["id"]: n for n in S.load_notes(SETS[1])}
    n = bp["bp14_topup_asia"]
    assert S.score(n["text"], "أريد شحن رصيد آسيا ب5000 دينار", n["checks"])["checks_ok"] == 2
    assert S.score(n["text"], "أريد شحن رصيد آسيا ب50000 دينار", n["checks"])["checks_ok"] == 1   # 50 000 is not 5 000


def test_folding():
    assert S.norm_ar("إشكد سعرها؟ ثلاجة چبيرة") == S.norm_ar("اشكد سعرها ثلاجه جبيره")
    assert S.norm_ar("٠٧٧٠") == "0770"


def test_hosted_models_need_allow_paid(capsys):
    assert S.main(["--model", "cohere"]) == 2
    assert "allow-paid" in capsys.readouterr().err


def test_rescore_uses_the_current_checks(tmp_path):
    notes = [{"id": "x", "text": "دزلي لحمودي", "checks": [{"what": "name", "kind": "name", "pattern": "حمودي"}]}]
    saved = {"models": {"m": {"agg": {}, "rows": [
        {"note": "x", "said": "", "heard": "دزلي لحمود", "seconds": 1.0, "cost": 0.0, "error": None}]}}}
    f = tmp_path / "r.json"
    f.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    assert S.rescore(f, notes) == 0
    assert json.loads(f.read_text(encoding="utf-8"))["models"]["m"]["agg"]["checks"] == "0/1"
    notes[0]["checks"][0]["pattern"] = "حمود"
    S.rescore(f, notes)
    assert json.loads(f.read_text(encoding="utf-8"))["models"]["m"]["agg"]["checks"] == "1/1"


@pytest.mark.parametrize("name", ["stt_billpay.json", "stt_shop.json"])
def test_saved_report_matches_a_fresh_rescore(name, tmp_path):
    """The numbers in docs/SPEECH_TO_TEXT.md come from these files; scoring them again changes nothing."""
    src = ROOT / "eval" / "reports" / "2026-11-15" / name
    before = json.loads(src.read_text(encoding="utf-8"))
    f = tmp_path / name
    f.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    S.rescore(f, S.load_notes(SETS[1] if "billpay" in name else SETS[0]))
    after = json.loads(f.read_text(encoding="utf-8"))
    assert {k: v["agg"] for k, v in after["models"].items()} == {k: v["agg"] for k, v in before["models"].items()}
