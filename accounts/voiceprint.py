"""Voiceprints: who is speaking in a voice note, kept as a speaker embedding (a short vector of floats), never audio.

Voice approval (VOICE_APPROVAL=1, channels/whatsapp/README.md "Voice approval") lets a voice note from the account's
owner approve a payment without the PIN. This module only turns a voice note into a vector and compares vectors;
what may be paid that way is decided in the wallet (wallet.api.Wallet.grant_voice / execute with factor="voice").

- The vector comes from a speaker model run on this server: sherpa-onnx's SpeakerEmbeddingExtractor with the
  3D-Speaker CAM++ model (VOICEPRINT_MODEL, the .onnx file; k2-fsa/sherpa-onnx release "speaker-recongition-models").
  The audio is decoded to 16 kHz mono with ffmpeg in a subprocess, read in memory and dropped. Nothing leaves the
  server, and only the vector is stored (by the wallet), never the audio.
- Two vectors are compared by cosine similarity; an enrolled voice is the normalised mean of up to MAX_SAMPLES
  vectors.
- A note with less than MIN_SECONDS of speech gives no vector: too little to tell one speaker from another.
- Anything missing or failing (the package, the model file, ffmpeg, a decode error, a timeout) gives no vector, and
  no vector means the PIN is asked for, as without the feature. load() returns None when the embedder cannot work
  here, with one log line naming what is missing.

Embedder is the interface the channel takes: tests pass a fake one, so they need neither the package nor a model.
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
import threading
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol, Sequence

log = logging.getLogger("accounts.voiceprint")

MODEL_FILE = "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
MODEL_NAME = "campplus_sv_zh_en_16k_advanced"  # stored with every vector: vectors of two models never compare
SAMPLE_RATE = 16_000
MIN_SECONDS = 1.5  # of speech (frames above the energy floor), not of the file
MAX_SECONDS = 30.0  # what is decoded of one note
MAX_AUDIO_BYTES = 16_000_000  # WhatsApp's own cap for audio
DECODE_TIMEOUT = 20.0
MAX_SAMPLES = 3  # vectors kept per account
DEFAULT_THRESHOLD = 0.70  # VOICE_MATCH_THRESHOLD
DEFAULT_MAX_IQD = 100_000  # VOICE_APPROVAL_MAX_IQD
FRAME = 320  # 20 ms at 16 kHz
SILENCE_FLOOR = 0.003  # RMS below this is silence, however quiet the note
SILENCE_RATIO = 0.10  # and so is a frame below a tenth of the note's loudest frames


@dataclass(frozen=True)
class Voiceprint:
    """One voice note's speaker vector (unit length), how much speech it held, and the model that made it."""

    vector: tuple[float, ...]
    seconds: float
    model: str = MODEL_NAME

    def __repr__(self) -> str:  # a log line never carries the vector
        return f"Voiceprint(dims={len(self.vector)}, seconds={self.seconds:.1f}, model={self.model})"


class Embedder(Protocol):
    """What the channel needs: a voice note (its bytes and mime) to a Voiceprint, or None when it cannot tell."""

    model: str

    def embed(self, audio: bytes, mime: str) -> Voiceprint | None: ...


# ---------------------------------------------------------------- vectors


def normalize(vector: Iterable[float]) -> tuple[float, ...]:
    values = tuple(float(x) for x in vector)
    norm = math.sqrt(sum(x * x for x in values))
    if not norm or not math.isfinite(norm):
        return ()
    return tuple(x / norm for x in values)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity, -1..1; 0.0 for vectors that cannot be compared (empty, other lengths, zero)."""
    if not a or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    if not na or not nb:
        return 0.0
    return max(-1.0, min(1.0, dot / (na * nb)))


def centroid(vectors: Sequence[Sequence[float]]) -> tuple[float, ...]:
    """The enrolled voice: the normalised mean of the normalised samples (all of one length)."""
    units = [normalize(v) for v in vectors]
    units = [u for u in units if u]
    if not units or len({len(u) for u in units}) != 1:
        return ()
    return normalize(sum(column) / len(units) for column in zip(*units))


def score(samples: Sequence[Sequence[float]], probe: Sequence[float]) -> float:
    return cosine(centroid(samples), probe)


def pack(vector: Sequence[float]) -> bytes:
    """A vector as float32 bytes (what the wallet stores)."""
    return array("f", vector).tobytes()


def unpack(blob: bytes) -> tuple[float, ...]:
    values = array("f")
    values.frombytes(bytes(blob or b"")[: len(blob or b"") // 4 * 4])
    return tuple(values)


# ---------------------------------------------------------------- audio


def decode(audio: bytes, ffmpeg: str, *, timeout: float = DECODE_TIMEOUT) -> array | None:
    """Any audio ffmpeg reads (ogg/opus voice notes, m4a, mp3) to 16 kHz mono float samples, in memory."""
    if not audio or len(audio) > MAX_AUDIO_BYTES:
        return None
    command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-t", str(MAX_SECONDS),
               "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "pipe:1"]
    try:
        done = subprocess.run(command, input=audio, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("voiceprint decode failed: %s", type(exc).__name__)
        return None
    if done.returncode != 0 or not done.stdout:
        log.warning("voiceprint decode failed: ffmpeg exit %s", done.returncode)
        return None
    samples = array("f")
    samples.frombytes(done.stdout[: len(done.stdout) // 4 * 4])
    return samples


def speech_seconds(samples: Sequence[float], rate: int = SAMPLE_RATE) -> float:
    """Seconds of frames loud enough to be speech: above SILENCE_FLOOR and above a tenth of the loud frames (the
    95th percentile, so one click does not set the bar)."""
    levels = []
    for start in range(0, len(samples) - FRAME + 1, FRAME):
        frame = samples[start:start + FRAME]
        levels.append(math.sqrt(sum(x * x for x in frame) / FRAME))
    if not levels:
        return 0.0
    loud = sorted(levels)[int(0.95 * (len(levels) - 1))]
    floor = max(SILENCE_FLOOR, SILENCE_RATIO * loud)
    return sum(1 for level in levels if level >= floor) * FRAME / rate


class SherpaEmbedder:
    """The CAM++ speaker model through sherpa-onnx, on this server's CPU. Loaded once, on the first note."""

    model = MODEL_NAME

    def __init__(self, model_path: str | Path, ffmpeg: str, *, threads: int = 1):
        self.model_path = str(model_path)
        self.ffmpeg = ffmpeg
        self.threads = threads
        self._extractor = None
        self._lock = threading.Lock()

    def _get(self):
        with self._lock:
            if self._extractor is None:
                import sherpa_onnx  # noqa: PLC0415 - optional: only a server with voice approval needs it

                config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=self.model_path, num_threads=self.threads,
                                                                     debug=False, provider="cpu")
                if not config.validate():
                    raise RuntimeError("speaker model config refused")
                self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
            return self._extractor

    def embed(self, audio: bytes, mime: str) -> Voiceprint | None:
        try:
            samples = decode(audio, self.ffmpeg)
            if samples is None:
                return None
            seconds = speech_seconds(samples)
            if seconds < MIN_SECONDS:
                return None
            extractor = self._get()
            with self._lock:  # one stream at a time through the shared extractor
                stream = extractor.create_stream()
                stream.accept_waveform(SAMPLE_RATE, samples.tolist())
                stream.input_finished()
                if not extractor.is_ready(stream):
                    return None
                vector = normalize(extractor.compute(stream))
            return Voiceprint(vector, seconds, self.model) if vector else None
        except Exception as exc:  # never in the way of the turn: no vector means the PIN
            log.warning("voiceprint failed: %s", type(exc).__name__)
            return None


def find_ffmpeg(env: dict[str, str] | None = None) -> str | None:
    """VOICEPRINT_FFMPEG when it names a file, else ffmpeg on the PATH."""
    env = os.environ if env is None else env
    named = (env.get("VOICEPRINT_FFMPEG") or "").strip()
    if named:
        return named if Path(named).is_file() else None
    return shutil.which("ffmpeg")


def load(model_path: str | Path | None, env: dict[str, str] | None = None) -> Embedder | None:
    """The real embedder, or None (with one log line naming what is missing): no model file, no sherpa-onnx, no
    ffmpeg. The model itself loads on the first voice note."""
    missing = []
    path = Path(model_path) if model_path else None
    if path is None or not path.is_file():
        missing.append("VOICEPRINT_MODEL (the speaker model file)")
    try:
        import sherpa_onnx  # noqa: F401, PLC0415
    except Exception:
        missing.append("the sherpa-onnx package")
    ffmpeg = find_ffmpeg(env)
    if not ffmpeg:
        missing.append("ffmpeg")
    if missing:
        log.warning("voice approval is off (every payment needs the PIN): missing %s", ", ".join(missing))
        return None
    return SherpaEmbedder(path, ffmpeg)
