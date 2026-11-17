"""Make the synthetic bill-pay voice notes in data/voice/billpay/ (text-to-speech, then WhatsApp-like Opus).

    python -m eval.make_voice_notes --allow-paid          # existing notes are kept; only new ones are spoken

Each note is written once in Iraqi Arabic with the details the bot must hear right (a name, an amount, every digit
of an account or phone number), spoken by one of six voices with its own style, and encoded as 16 kHz mono Opus in
Ogg like a WhatsApp voice note. Two notes get extra trouble: crowd noise, and fast speech. These are SYNTHETIC
voices: they show which model hears the words, not how it copes with a real phone in a real street. Real voice
notes from people are the next step.

Speech goes through the model layer's "tts" role (llm/, docs/MODELS.md): set LLM_TTS to a route and give the
six voices the route knows in VOICE_NOTES_VOICES="v1=<voice>,v2=<voice>,...". A route may answer raw 16-bit
PCM (read at --pcm-rate) or any container PyAV can open. Needs `pip install av numpy`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "voice" / "billpay"
RATE = 24000

AMT = r"(?:(?<!\d){d}(?: ?0{{3}})?(?!\d)|{w})"   # 25, 25000, 25 000, ب25000 or the words


def amount(d: str, w: str) -> str:
    return AMT.format(d=d, w=w)


# id, voice, style, text, checks.  Patterns run on folded text (ة->ه, أ/إ/آ->ا, no diacritics).
NOTES = [
    ("bp01_send_name_amount", "v1", "young Baghdadi man, casual",
     "دزلي لحمودي خمسة وعشرين ألف",
     [("name حمودي", "name", r"حمودي"), ("25 الف", "amount", amount("25", r"خمسه و ?عشرين"))]),
    ("bp02_generator_bill", "v2", "Basra mother, calm",
     "خلي ادفع حق المولدة هالشهر، مية وخمسين ألف",
     [("biller مولدة", "biller", r"مولد"), ("150 الف", "amount", amount("150", r"مي?ه و ?خمسين"))]),
    ("bp03_internet_account", "v3", "older man, slow and clear",
     "ادفع الانترنت، رقم الحساب صفر سبعة ثمانية صفر، اربعة اربعة، واحد تسعة، ستة ستة صفر",
     [("biller انترنت", "biller", r"انترنت|نت\b|internet"), ("account 07804419660", "digits", "07804419660")]),
    ("bp04_balance", "v4", "young man, quick question",
     "شكد باقي برصيدي؟",
     [("رصيد", "word", r"رصيد"), ("شكد (any spelling)", "word", r"[اإ]?ش ?[وت]? ?[كق][دت]|كم")]),
    ("bp05_confirm", "v5", "friendly man, short",
     "اي ثبت، دزها",
     [("ثبت", "word", r"ثبت")]),
    ("bp06_cancel", "v6", "annoyed man",
     "لا لا بطلت، الغيها هسه",
     [("الغي", "word", r"الغي|لغي|الغاء")]),
    ("bp07_transfer_phone", "v7", "man reading a number slowly",
     "حول نص مليون لهالرقم، صفر سبعة سبعة صفر، تسعة ثمانية ستة، خمسة اربعة ثلاثة اثنين",
     [("نص مليون", "amount", r"نص ?مليون|نصف مليون|500 ?000|\b500\b"), ("phone 07709865432", "digits", "07709865432")]),
    ("bp08_umm_ali", "v2", "woman, warm",
     "دزي لأم علي عشرة آلاف، هي بالكرادة",
     [("name ام علي", "name", r"ام ?علي"), ("10 الاف", "amount", amount("10", r"عشره ?ال?اف|عشر ?ال?اف"))]),
    ("bp09_water", "v1", "young man, curious",
     "فاتورة الماي شكد طالعة هالشهر؟",
     [("biller ماي", "biller", r"\bماي\b|الماي|ماء|مي\b|المي\b"), ("فاتورة", "word", r"فاتور")]),
    ("bp10_electric_account", "v3", "older man, careful",
     "سدد الكهرباء، رقم المشترك ثلاثة سبعة واحد، خمسة تسعة، اثنين صفر اربعة",
     [("biller كهرباء", "biller", r"كهرب"), ("subscriber 37159204", "digits", "37159204")]),
    ("bp11_mustafa", "v4", "young man, confident",
     "حوّل ثلاثمية ألف لمصطفى",
     [("name مصطفى", "name", r"مصطف"), ("300 الف", "amount", amount("300", r"ثلاث ?ما?ي?ه"))]),
    ("bp12_noisy_market", "v5", "man in a busy market, slightly loud",
     "عيني دزلي لزينب كاظم عشرين ألف هسه",
     [("name زينب كاظم", "name", r"زينب ?كاظم"), ("20 الف", "amount", amount("20", r"عشرين"))]),
    ("bp13_fast", "v1", "talks very fast, in a hurry",
     "هلو عيني دزلي لزهراء خمسطعش ألف بسرعة",
     [("name زهراء", "name", r"زهراء|زهرا"), ("15 الف", "amount", amount("15", r"خمسطعش|خمسه? ?عشر"))]),
    ("bp14_topup_asia", "v6", "young man, casual",
     "اشحنلي آسيا بخمسة آلاف دينار",
     [("asia", "biller", r"اسيا|asia"), ("5 الاف", "amount", amount("5", r"خمس(ه)? ?ال?اف"))]),
    ("bp15_correction", "v7", "man who changes his mind mid-sentence",
     "دز لعلي عشرين... لا لا، ثلاثين ألف",
     [("name علي", "name", r"\bعلي\b|لعلي"), ("30 الف", "amount", amount("30", r"ثلاثين"))]),
    ("bp16_mixed_english", "v4", "young man mixing English",
     "send لأحمد fifty thousand please",
     [("name احمد", "name", r"احمد|ahmed|ahmad"), ("50 الف", "amount", r"fifty|خمسين|\b50\b|50 ?000")]),
]
NOISY, FAST = {"bp12_noisy_market"}, {"bp13_fast"}


def voices() -> dict[str, str]:
    """VOICE_NOTES_VOICES="v1=<voice>,v2=<voice>": the label in notes.jsonl -> the voice the route knows."""
    pairs = (p.split("=", 1) for p in os.environ.get("VOICE_NOTES_VOICES", "").split(",") if "=" in p)
    return {k.strip(): v.strip() for k, v in pairs}


def tts(text: str, voice: str, style: str) -> bytes:
    from llm import LLM, Registry
    llm = LLM(Registry.from_env())
    if not llm.has("tts"):
        raise SystemExit("no usable route for the tts role: set LLM_TTS (docs/MODELS.md)")
    audio = llm.speech("tts", text, voice=voice, style=f"Iraqi Arabic (Baghdadi) WhatsApp voice note. {style}.",
                       timeout_s=180)
    if not audio or len(audio) < 4800:
        raise RuntimeError(f"text-to-speech returned {len(audio or b'')} bytes")
    return audio


def to_samples(audio: bytes, pcm_rate: int) -> np.ndarray:
    """Mono float32 at RATE from whatever the route answered: a container PyAV opens, else raw s16le PCM."""
    if audio[:4] in (b"RIFF", b"OggS", b"fLaC") or audio[:3] == b"ID3" or audio[:1] == bytes([0xFF]):
        import io
        import av
        chunks = []
        with av.open(io.BytesIO(audio)) as c:
            rs = av.AudioResampler(format="s16", layout="mono", rate=RATE)
            for frame in c.decode(audio=0):
                chunks += [f.to_ndarray().reshape(-1) for f in rs.resample(frame)]
            chunks += [f.to_ndarray().reshape(-1) for f in rs.resample(None)]
        return np.concatenate(chunks).astype(np.float32) / 32768
    x = np.frombuffer(audio[: len(audio) // 2 * 2], "<i2").astype(np.float32) / 32768
    if pcm_rate != RATE:
        x = np.interp(np.arange(0, x.size, pcm_rate / RATE), np.arange(x.size), x).astype(np.float32)
    return x


def add_noise(x: np.ndarray, others: list[np.ndarray], seed: int = 7) -> np.ndarray:
    """Crowd babble from the other notes' voices at -11 dB plus pink-ish noise at -22 dB, re the speech RMS."""
    rng = np.random.default_rng(seed)
    n = x.size + RATE // 2
    babble = np.zeros(n, np.float32)
    for o in others:
        pos = int(rng.integers(-o.size // 2, n // 2))
        while pos < n:
            s, e = max(pos, 0), min(pos + o.size, n)
            babble[s:e] += o[s - pos:e - pos] * float(rng.uniform(0.5, 1.0))
            pos += o.size + int(rng.integers(0, RATE))
    pink = np.cumsum(rng.standard_normal(n)).astype(np.float32)
    pink = pink - np.convolve(pink, np.ones(400) / 400, mode="same")   # crude: drop the drift, keep the hiss
    sp = np.pad(np.concatenate([np.zeros(RATE // 2, np.float32), x]), (0, 0))[:n]
    rms = lambda a: float(np.sqrt(np.mean(a.astype(np.float64) ** 2))) or 1e-9  # noqa: E731
    at = lambda a, db: a * (rms(x) * 10 ** (db / 20)) / rms(a)  # noqa: E731
    return np.clip(sp + at(babble, -11) + at(pink, -22), -1, 1).astype(np.float32)


def speed_up(x: np.ndarray, factor: float = 1.12) -> np.ndarray:
    """Plain resample: faster and a little higher, like a voice note sped up."""
    idx = np.arange(0, x.size, factor)
    return np.interp(idx, np.arange(x.size), x).astype(np.float32)


def write_ogg(x: np.ndarray, path: Path) -> None:
    import av
    s16 = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with av.open(str(path), "w", format="ogg") as c:
        st = c.add_stream("libopus", rate=16000, layout="mono")
        st.bit_rate = 24000
        rs = av.AudioResampler(format="s16", layout="mono", rate=16000)
        frame = av.AudioFrame.from_ndarray(s16.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = RATE
        for f in rs.resample(frame) + rs.resample(None):
            for p in st.encode(f):
                c.mux(p)
        for p in st.encode(None):
            c.mux(p)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--allow-paid", action="store_true")
    ap.add_argument("--pcm-rate", type=int, default=RATE, help="sample rate of a raw PCM answer (default 24000)")
    a = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    raw_dir = OUT / ".pcm"                      # each answer of the tts route, as it came (not committed)
    raw_dir.mkdir(exist_ok=True)
    todo = [n for n in NOTES if not (raw_dir / f"{n[0]}.pcm").exists()]
    if todo and not a.allow_paid:
        print(f"refused: {len(todo)} notes need text-to-speech; add --allow-paid", file=sys.stderr)
        return 2
    names = voices()
    for nid, label, style, text, _ in todo:
        if label not in names:
            raise SystemExit(f"VOICE_NOTES_VOICES has no voice for {label}")
        t0 = time.perf_counter()
        (raw_dir / f"{nid}.pcm").write_bytes(tts(text, names[label], "speak very fast" if nid in FAST else style))
        print(f"  tts {nid:<24} {label:<4} {time.perf_counter() - t0:4.1f}s", flush=True)
    pcm = {n[0]: to_samples((raw_dir / f"{n[0]}.pcm").read_bytes(), a.pcm_rate) for n in NOTES}
    lines = []
    for nid, voice, style, text, checks in NOTES:
        x = pcm[nid]
        effect = None
        if nid in NOISY:
            x, effect = add_noise(x, [v for k, v in pcm.items() if k != nid][:6]), "crowd noise, about 10 dB SNR"
        if nid in FAST:
            x, effect = speed_up(x), "spoken fast, then sped up 1.12x"
        write_ogg(x, OUT / f"{nid}.ogg")
        lines.append(json.dumps({
            "id": nid, "text": text, "voice": voice, "style": style, "effect": effect,
            "seconds": round(x.size / RATE, 2),
            "checks": [{"what": w, "kind": k, **({"digits": p} if k == "digits" else {"pattern": p})}
                       for w, k, p in checks]}, ensure_ascii=False))
    (OUT / "notes.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(lines)} notes in {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
