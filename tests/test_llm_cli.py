"""`python -m llm check` and `python -m llm ping`, plus the text rules for the files of the model layer.

`ping` is the one command that would reach a real model. Here it only ever talks to an httpx.MockTransport.
"""

import io
import json
import pathlib
import wave

import pytest

from llm import ROLES
from llm.__main__ import _png, _wav, main
from llm.testing import Endpoint, chat

ROOT = pathlib.Path(__file__).resolve().parent.parent
FAKE_KEY = "k-test-9c1d-not-a-real-key"


def run(argv, env, answer=None):
    out = io.StringIO()
    endpoint = Endpoint(answer or (lambda body, request: 500))
    code = main(argv, env=env, transport=endpoint.transport, out=out)
    return code, out.getvalue(), endpoint


# ---- check ----------------------------------------------------------------------------------------

def test_check_lists_every_role_and_its_chain_and_never_a_key():
    env = {
        "LLM_DECIDE": "groq:llama-3.1-8b-instant,ollama:qwen2.5:7b",
        "LLM_UNDERSTAND": "cerebras:llama-3.3-70b",
        "LLM_VISION": "myhots:some-model",
        "LLM_PROVIDER_GROQ_KEYS": "GROQ_API_KEY,GROQ_API_KEY_2",
        "GROQ_API_KEY": FAKE_KEY,
    }
    code, text, endpoint = run(["check"], env)
    assert code == 0
    assert endpoint.requests == []                                   # check is offline
    assert FAKE_KEY not in text
    lines = text.splitlines()
    for role in ROLES:
        assert any(line.startswith(role + ":") for line in lines)
    assert "decide: ready" in lines
    assert "  1. groq:llama-3.1-8b-instant  [primary]  usable: 1 of 2 key(s) set" in lines
    assert "       key 1: $GROQ_API_KEY set" in lines
    assert "       key 2: $GROQ_API_KEY_2 missing" in lines
    assert "  2. ollama:qwen2.5:7b  [backup 1]  usable: local, no key needed" in lines
    assert "understand: no usable route" in lines
    assert "  1. cerebras:llama-3.3-70b  [primary]  NOT usable: no key set" in lines
    assert "vision: no usable route" in lines
    assert "  1. myhots:some-model  [primary]  NOT usable: unknown provider" in lines
    assert "phrase: not configured" in lines
    assert lines[-1] == "1 of 7 role(s) ready."


def test_check_with_nothing_configured():
    code, text, _ = run(["check"], {})
    assert code == 0
    assert text.splitlines()[-1] == "0 of 7 role(s) ready."


def test_check_reports_a_configuration_error_without_repeating_the_value():
    code, text, _ = run(["check"], {"LLM_PROVIDER_GROQ_KEYS": FAKE_KEY})
    assert code == 2
    assert text.startswith("configuration error:") and FAKE_KEY not in text


def test_check_shows_roles_that_come_from_a_file(tmp_path):
    path = tmp_path / "llm.json"
    path.write_text(json.dumps({"roles": {"rerank": ["ollama:some-model"]}}), encoding="utf-8")
    code, text, _ = run(["check"], {"LLM_CONFIG": str(path)})
    assert "rerank: ready" in text.splitlines()
    assert text.splitlines()[-1] == "1 of 8 role(s) ready."


def test_a_command_is_required():
    with pytest.raises(SystemExit):
        main([], env={}, out=io.StringIO())


# ---- ping (against a mock transport only) ---------------------------------------------------------------

ENV = {"LLM_PROVIDER_FAKE_BASE": "http://fake.test/v1", "LLM_PROVIDER_FAKE_LOCAL": "1"}


def answer_anything(body, request):
    path = request.url.path
    if path.endswith("/audio/speech"):
        return b"OggS-specimen"
    if path.endswith("/audio/transcriptions"):
        return ""
    labels = json.dumps(body["messages"])
    if "greeting" in labels:
        return chat({"label": "greeting"}, tokens_in=20, tokens_out=4)
    if "ok" in labels and "response_format" in body:
        return chat({"ok": True}, tokens_in=9, tokens_out=3)
    return chat("ok", tokens_in=5, tokens_out=1)


@pytest.mark.parametrize("role", ROLES)
def test_ping_makes_one_small_call_for_each_role(role):
    env = {**ENV, f"LLM_{role.upper()}": "fake:tiny?usd_per_mtok_in=1"}
    code, text, endpoint = run(["ping", role, "--timeout", "5"], env, answer_anything)
    assert code == 0, text
    assert len(endpoint.requests) == 1
    assert text.splitlines()[-1] == f"{role}: ok"
    assert "1. fake:tiny  answered" in text


def test_ping_sends_real_looking_media():
    env = {**ENV, "LLM_VISION": "fake:tiny", "LLM_HEAR": "fake:tiny"}
    _, _, endpoint = run(["ping", "vision"], env, answer_anything)
    url = endpoint.bodies[0]["messages"][0]["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    _, _, endpoint = run(["ping", "hear"], env, answer_anything)
    assert endpoint.bodies[0]["messages"][0]["content"][-1]["input_audio"]["format"] == "wav"


def test_ping_reports_a_failure_and_falls_back_like_any_call():
    env = {**ENV, "LLM_PROVIDER_BROKEN_BASE": "http://broken.test/v1", "LLM_PROVIDER_BROKEN_KEYS": "BROKEN_KEY",
           "BROKEN_KEY": FAKE_KEY, "LLM_PHRASE": "broken:big,fake:tiny"}

    def answer(body, request):
        return 401 if request.url.host == "broken.test" else "ok"

    code, text, endpoint = run(["ping", "phrase"], env, answer)
    assert code == 0 and FAKE_KEY not in text
    assert "1. broken:big  failed" in text and "2. fake:tiny  answered" in text


def test_ping_fails_when_no_route_answers():
    code, text, _ = run(["ping", "phrase"], {**ENV, "LLM_PHRASE": "fake:tiny"}, lambda body, request: 500)
    assert code == 1
    assert text.splitlines()[-1] == "phrase: no route answered"
    code, text, _ = run(["ping", "stt"], {**ENV, "LLM_STT": "fake:tiny"}, lambda body, request: 500)
    assert code == 1 and "stt: no route answered (fake status 500; fake status 500)" in text


def test_ping_without_a_usable_route_makes_no_call():
    code, text, endpoint = run(["ping", "decide"], {"LLM_DECIDE": "groq:llama-3.1-8b-instant"})
    assert code == 1 and endpoint.requests == []
    assert "no usable route" in text


def test_the_built_in_sample_media_are_well_formed():
    png = _png()
    assert png.startswith(b"\x89PNG\r\n\x1a\n") and png.endswith(b"IEND\xaeB`\x82")
    with wave.open(io.BytesIO(_wav()), "rb") as sound:
        assert (sound.getnchannels(), sound.getframerate(), sound.getnframes()) == (1, 16000, 8000)


# ---- text rules for the files of the model layer ----------------------------------------------------------

def _own_files():
    files = sorted((ROOT / "llm").glob("*.py")) + sorted((ROOT / "tests").glob("test_llm_*.py"))
    return files + [ROOT / "docs" / "MODELS.md"]


def test_no_em_dash_and_no_persian_che_in_these_files():
    banned = {chr(0x2014): "an em dash", chr(0x0686): "the letter che"}
    for path in _own_files():
        text = path.read_text(encoding="utf-8")
        for char, name in banned.items():
            assert char not in text, f"{path.name} holds {name}"


def test_the_guide_covers_every_role_and_the_commands():
    guide = (ROOT / "docs" / "MODELS.md").read_text(encoding="utf-8")
    for role in ROLES:
        assert f"| `{role}` |" in guide
    for phrase in ("python -m llm check", "python -m llm ping", "LLM_PROVIDER_", "LLM_CONFIG", "ollama pull",
                   "Add a backup provider", "Swap a model in one line"):
        assert phrase in guide


def test_the_guide_shows_check_output_the_command_really_prints():
    env = {
        "LLM_DECIDE": "groq:llama-3.1-8b-instant,ollama:qwen2.5:7b",
        "LLM_UNDERSTAND": "cerebras:llama-3.3-70b",
        "LLM_VISION": "myhots:some-model",
        "LLM_PROVIDER_GROQ_KEYS": "GROQ_API_KEY,GROQ_API_KEY_2",
        "GROQ_API_KEY": FAKE_KEY,
    }
    _, text, _ = run(["check"], env)
    guide = (ROOT / "docs" / "MODELS.md").read_text(encoding="utf-8")
    assert text.strip() in guide


def test_the_package_imports_nothing_beyond_the_standard_library_and_httpx():
    import ast
    import sys

    allowed = set(sys.stdlib_module_names) | {"httpx", "llm", "__future__"}
    for path in sorted((ROOT / "llm").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                assert name.split(".")[0] in allowed, f"{path.name} imports {name}"
