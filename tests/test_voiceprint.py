"""accounts/voiceprint.py: the vector arithmetic, the speech-length gate, and every way the real embedder degrades to
"no vector" (which means the PIN). Offline: no model and no ffmpeg are needed (or used)."""

from __future__ import annotations

import logging
import math

from accounts import voiceprint as vp


def test_cosine_centroid_and_score():
    assert vp.cosine((1, 0), (1, 0)) == 1.0 and vp.cosine((1, 0), (0, 1)) == 0.0
    assert vp.cosine((), ()) == 0.0 and vp.cosine((1, 0), (1, 0, 0)) == 0.0 and vp.cosine((0, 0), (1, 0)) == 0.0
    mean = vp.centroid([(1, 0), (0, 1)])
    assert math.isclose(mean[0], mean[1]) and math.isclose(sum(x * x for x in mean), 1.0)
    assert vp.centroid([]) == () and vp.centroid([(1, 0), (1, 0, 0)]) == ()
    assert math.isclose(vp.score([(1, 0), (0.9, 0.1)], (1, 0.05)), vp.cosine(vp.centroid([(1, 0), (0.9, 0.1)]),
                                                                              (1, 0.05)))


def test_pack_and_unpack_keep_a_float32_vector():
    vector = vp.normalize([0.1 * i for i in range(1, 193)])
    back = vp.unpack(vp.pack(vector))
    assert len(back) == 192 and vp.cosine(vector, back) > 0.999999
    assert vp.unpack(b"") == () and vp.unpack(b"\x00\x00\x80?\x01") == (1.0,)  # a torn tail is ignored


def test_a_voiceprint_never_prints_its_vector():
    print_ = vp.Voiceprint((0.123456, 0.654321), 2.5)
    assert "0.123456" not in repr(print_) and "dims=2" in repr(print_)


def test_speech_seconds_counts_speech_not_silence():
    rate = vp.SAMPLE_RATE
    tone = [0.3 * math.sin(2 * math.pi * 220 * i / rate) for i in range(rate)]  # one second of sound
    silence = [0.0] * (2 * rate)
    assert 0.95 <= vp.speech_seconds(silence + tone + silence) <= 1.05
    assert vp.speech_seconds(silence) == 0.0 and vp.speech_seconds([]) == 0.0
    assert vp.speech_seconds(tone * 2) >= vp.MIN_SECONDS


def test_load_is_none_when_anything_is_missing(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="accounts.voiceprint"):
        assert vp.load(None, env={}) is None
        assert vp.load(tmp_path / "missing.onnx", env={}) is None
    assert "VOICEPRINT_MODEL" in caplog.text and "PIN" in caplog.text
    model = tmp_path / vp.MODEL_FILE
    model.write_bytes(b"not a model")
    assert vp.load(model, env={"VOICEPRINT_FFMPEG": str(tmp_path / "no-ffmpeg.exe")}) is None  # no ffmpeg there


def test_the_embedder_gives_no_vector_when_decoding_fails(tmp_path):
    embedder = vp.SherpaEmbedder(tmp_path / "missing.onnx", str(tmp_path / "no-ffmpeg.exe"))
    assert embedder.embed(b"OggS-not-really", "audio/ogg") is None  # ffmpeg cannot run: no vector, no exception
    assert embedder.embed(b"", "audio/ogg") is None
    assert vp.decode(b"x" * (vp.MAX_AUDIO_BYTES + 1), "ffmpeg") is None  # over WhatsApp's cap: not even tried


def test_too_little_speech_gives_no_vector(tmp_path, monkeypatch):
    embedder = vp.SherpaEmbedder(tmp_path / "missing.onnx", "ffmpeg")
    from array import array

    short = array("f", [0.3 * math.sin(i / 3) for i in range(vp.SAMPLE_RATE)])  # one second
    monkeypatch.setattr(vp, "decode", lambda audio, ffmpeg, **kw: short)
    assert embedder.embed(b"OggS", "audio/ogg") is None  # the model is never even loaded for it
    assert embedder._extractor is None
