"""Reading the model configuration: presets, the environment, the JSON file, and what counts as usable."""

import json

import pytest

from llm import LLM, LLMError, Provider, Registry, Route, ROLES
from llm.registry import parse_options, presets

FAKE_KEY = "k-test-3f9a-not-a-real-key"


def test_roles_are_the_seven_jobs():
    assert ROLES == ("decide", "understand", "phrase", "vision", "hear", "stt", "tts")


# ---- presets ------------------------------------------------------------------------------------

def test_local_presets_need_no_key():
    found = presets()
    assert found["ollama"].base_url == "http://127.0.0.1:11434/v1"
    assert found["lmstudio"].base_url == "http://127.0.0.1:1234/v1"
    assert found["llamacpp"].base_url == "http://127.0.0.1:8080/v1"
    for name in ("ollama", "lmstudio", "llamacpp"):
        assert found[name].local and found[name].key_envs == ()


def test_hosted_presets_read_their_key_from_name_api_key():
    found = presets()
    for name in ("groq", "cerebras", "mistral", "together"):
        assert not found[name].local
        assert found[name].key_envs == (f"{name.upper()}_API_KEY",)
        assert found[name].base_url.startswith("https://")


def test_from_env_with_nothing_set_has_the_presets_and_no_roles():
    registry = Registry.from_env({})
    assert set(presets()) <= set(registry.providers)
    assert registry.roles == {}
    assert not LLM(registry, env={}).has("decide")


# ---- roles from the environment -----------------------------------------------------------------

def test_a_role_is_a_chain_of_provider_model():
    registry = Registry.from_env({"LLM_DECIDE": "groq:llama-3.1-8b-instant, ollama:qwen2.5:7b"})
    chain = registry.chain("decide")
    assert [(r.provider, r.model) for r in chain] == [("groq", "llama-3.1-8b-instant"), ("ollama", "qwen2.5:7b")]
    assert chain[0].spec == "groq:llama-3.1-8b-instant"


def test_a_model_name_keeps_its_colons_and_slashes():
    registry = Registry.from_env({"LLM_VISION": "ollama:hf.co/org/model-GGUF:Q4_K_M"})
    assert registry.chain("vision")[0].model == "hf.co/org/model-GGUF:Q4_K_M"


def test_route_options_after_a_question_mark():
    registry = Registry.from_env({"LLM_PHRASE": "ollama:gemma3:4b?timeout_s=8&effort=low&temperature=0.4&strict=false"})
    route = registry.chain("phrase")[0]
    assert route.model == "gemma3:4b"
    assert route.options == {"timeout_s": 8, "effort": "low", "temperature": 0.4, "strict": False}


def test_parse_options_keeps_text_as_text():
    assert parse_options("a=1&b=x y&c=true&d=&e") == {"a": 1, "b": "x y", "c": True, "d": ""}


@pytest.mark.parametrize("value", ["off", "none", "OFF", "-"])
def test_off_empties_a_chain(value):
    defaults = Registry({}, {"tts": [Route("ollama", "x")]})
    assert Registry.from_env({"LLM_TTS": value}, defaults).chain("tts") == []


def test_an_empty_variable_changes_nothing():
    defaults = Registry({}, {"tts": [Route("ollama", "x")]})
    assert Registry.from_env({"LLM_TTS": "  "}, defaults).chain("tts") == [Route("ollama", "x")]


def test_a_route_without_a_provider_is_a_loud_error_that_does_not_repeat_the_value():
    with pytest.raises(LLMError) as caught:
        Registry.from_env({"LLM_DECIDE": FAKE_KEY})
    assert "LLM_DECIDE" in str(caught.value)
    assert FAKE_KEY not in str(caught.value)


def test_variables_that_are_not_roles_are_ignored():
    registry = Registry.from_env({"LLM_SOMETHING": "ollama:x", "LLM_CONFIG": ""})
    assert registry.roles == {}


# ---- providers from the environment -------------------------------------------------------------

def test_a_new_provider_from_the_environment():
    registry = Registry.from_env({
        "LLM_PROVIDER_MYHOST_BASE": "https://models.example.test/v1/",
        "LLM_PROVIDER_MYHOST_KEYS": "MYHOST_KEY_A, MYHOST_KEY_B",
        "LLM_PROVIDER_MYHOST_JSON": "prompt",
        "LLM_PROVIDER_MYHOST_OPTIONS": "transcribe_body=multipart&timeout_s=9",
    })
    provider = registry.providers["myhost"]
    assert provider.base_url == "https://models.example.test/v1"
    assert provider.key_envs == ("MYHOST_KEY_A", "MYHOST_KEY_B")
    assert provider.json_mode == "prompt" and not provider.local
    assert provider.options == {"transcribe_body": "multipart", "timeout_s": 9}
    assert provider.host == "models.example.test"


def test_a_new_hosted_provider_defaults_to_name_api_key():
    registry = Registry.from_env({"LLM_PROVIDER_MY_HOST_BASE": "https://x.example.test/v1"})
    assert registry.providers["my_host"].key_envs == ("MY_HOST_API_KEY",)


def test_a_new_local_provider_needs_no_key():
    registry = Registry.from_env({"LLM_PROVIDER_WHISPER_BASE": "http://127.0.0.1:8000/v1",
                                  "LLM_PROVIDER_WHISPER_LOCAL": "1", "LLM_STT": "whisper:large-v3"})
    assert registry.providers["whisper"].local and registry.providers["whisper"].key_envs == ()
    assert LLM(registry, env={}).has("stt")


def test_the_environment_changes_one_field_of_a_preset():
    registry = Registry.from_env({"LLM_PROVIDER_GROQ_KEYS": "GROQ_KEY_1,GROQ_KEY_2",
                                  "LLM_PROVIDER_OLLAMA_BASE": "http://10.0.0.5:11434/v1"})
    assert registry.providers["groq"].key_envs == ("GROQ_KEY_1", "GROQ_KEY_2")
    assert registry.providers["groq"].base_url == presets()["groq"].base_url
    assert registry.providers["groq"].json_mode == presets()["groq"].json_mode
    assert registry.providers["ollama"].base_url == "http://10.0.0.5:11434/v1" and registry.providers["ollama"].local


def test_a_provider_without_a_base_address_is_an_error():
    with pytest.raises(LLMError) as caught:
        Registry.from_env({"LLM_PROVIDER_NEWONE_KEYS": "NEWONE_KEY"})
    assert "LLM_PROVIDER_NEWONE_BASE" in str(caught.value)


def test_a_key_pasted_where_a_name_belongs_is_refused_and_not_repeated():
    with pytest.raises(LLMError) as caught:
        Registry.from_env({"LLM_PROVIDER_GROQ_KEYS": FAKE_KEY})
    assert FAKE_KEY not in str(caught.value)
    with pytest.raises(LLMError) as caught:
        Provider("p", "https://x.example.test/v1", key_envs=(FAKE_KEY,))
    assert FAKE_KEY not in str(caught.value)


@pytest.mark.parametrize("base", [
    "models.example.test/v1", "ftp://x.example.test", "", "https://user:pw@x.example.test/v1",
    "https://x.example.test/v1?key=abc",
])
def test_a_bad_base_address_is_refused_and_not_repeated(base):
    with pytest.raises(LLMError) as caught:
        Provider("p", base)
    assert "pw" not in str(caught.value) and "abc" not in str(caught.value)


def test_a_key_never_goes_in_the_headers():
    with pytest.raises(LLMError) as caught:
        Provider("p", "https://x.example.test/v1", headers={"Authorization": "Bearer " + FAKE_KEY})
    assert FAKE_KEY not in str(caught.value)
    assert Provider("p", "https://x.example.test/v1", headers={"X-Title": "billpay"}).headers == {"X-Title": "billpay"}


def test_a_bad_json_mode_is_refused():
    with pytest.raises(LLMError):
        Registry.from_env({"LLM_PROVIDER_GROQ_JSON": "xml"})
    with pytest.raises(LLMError):
        Registry.from_env({"LLM_PROVIDER_GROQ_LOCAL": "maybe"})


# ---- the merge order ----------------------------------------------------------------------------

def _write(tmp_path, data):
    path = tmp_path / "llm.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_merge_order_presets_then_defaults_then_file_then_environment(tmp_path):
    defaults = Registry(
        {"ollama": Provider("ollama", "http://defaults.test/v1", local=True),
         "house": Provider("house", "https://house.example.test/v1", ("HOUSE_KEY",))},
        {"decide": [Route("house", "from-defaults")], "phrase": [Route("house", "from-defaults")],
         "vision": [Route("house", "from-defaults")]},
    )
    path = _write(tmp_path, {
        "providers": {"house": {"json": "object"}, "extra": {"base_url": "http://extra.test/v1", "local": True}},
        "roles": {"phrase": ["house:from-file"], "vision": ["extra:from-file"]},
    })
    registry = Registry.from_env({"LLM_CONFIG": path, "LLM_VISION": "ollama:from-env",
                                  "LLM_PROVIDER_HOUSE_KEYS": "HOUSE_KEY_ENV"}, defaults)

    assert registry.providers["lmstudio"].base_url == "http://127.0.0.1:1234/v1"      # preset, untouched
    assert registry.providers["ollama"].base_url == "http://defaults.test/v1"         # defaults over preset
    assert registry.providers["house"].json_mode == "object"                           # file over defaults
    assert registry.providers["house"].key_envs == ("HOUSE_KEY_ENV",)                  # environment over file
    assert registry.providers["house"].base_url == "https://house.example.test/v1"
    assert registry.chain("decide")[0].model == "from-defaults"
    assert registry.chain("phrase")[0].model == "from-file"
    assert registry.chain("vision")[0].spec == "ollama:from-env"


def test_the_file_takes_routes_with_options_and_backups(tmp_path):
    path = _write(tmp_path, {"roles": {"understand": [
        {"provider": "groq", "model": "big", "options": {"timeout_s": 6, "extra_body": {"seed": 7}}},
        "ollama:qwen2.5:7b?effort=low",
    ]}})
    chain = Registry.from_env({"LLM_CONFIG": path}).chain("understand")
    assert chain[0].options == {"timeout_s": 6, "extra_body": {"seed": 7}}
    assert chain[1] == Route("ollama", "qwen2.5:7b", {"effort": "low"})


def test_the_file_may_use_the_same_flat_names_as_the_environment(tmp_path):
    path = _write(tmp_path, {"LLM_STT": "local_stt:whisper-large-v3",
                             "LLM_PROVIDER_LOCAL_STT_BASE": "http://127.0.0.1:9000/v1",
                             "LLM_PROVIDER_LOCAL_STT_LOCAL": True})
    registry = Registry.from_env({"LLM_CONFIG": path})
    assert registry.chain("stt")[0].spec == "local_stt:whisper-large-v3"
    assert registry.providers["local_stt"].local


@pytest.mark.parametrize("content", ["not json", "[1, 2]", '{"roles": []}', '{"providers": {"x": 3}}',
                                     '{"roles": {"decide": ["no-colon"]}}'])
def test_a_broken_file_is_a_loud_error(tmp_path, content):
    path = tmp_path / "llm.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(LLMError) as caught:
        Registry.from_env({"LLM_CONFIG": str(path)})
    assert "LLM_CONFIG" in str(caught.value)


def test_a_missing_file_is_a_loud_error(tmp_path):
    with pytest.raises(LLMError):
        Registry.from_env({"LLM_CONFIG": str(tmp_path / "nowhere.json")})


def test_from_env_reads_the_real_environment_by_default(monkeypatch):
    monkeypatch.delenv("LLM_CONFIG", raising=False)
    monkeypatch.setenv("LLM_HEAR", "ollama:listener")
    assert Registry.from_env().chain("hear")[0].spec == "ollama:listener"


# ---- has() --------------------------------------------------------------------------------------

def _registry():
    return Registry(
        {"hosted": Provider("hosted", "https://hosted.example.test/v1", ("HOSTED_KEY_1", "HOSTED_KEY_2")),
         "nokeys": Provider("nokeys", "https://nokeys.example.test/v1"),
         "local": Provider("local", "http://127.0.0.1:1/v1", local=True)},
        {"decide": [Route("hosted", "m")], "phrase": [Route("local", "m")], "vision": [Route("ghost", "m")],
         "hear": [Route("nokeys", "m")], "understand": [Route("ghost", "m"), Route("hosted", "m"), Route("local", "m")],
         "stt": []},
    )


def test_has_needs_a_key_for_a_hosted_route():
    assert not LLM(_registry(), env={}).has("decide")
    assert not LLM(_registry(), env={"HOSTED_KEY_1": "   "}).has("decide")
    assert LLM(_registry(), env={"HOSTED_KEY_2": FAKE_KEY}).has("decide")


def test_has_is_true_for_a_local_route_and_false_for_an_unknown_provider():
    llm = LLM(_registry(), env={})
    assert llm.has("phrase")
    assert not llm.has("vision")
    assert not llm.has("hear")          # hosted, and no key variable is even named
    assert not llm.has("stt")           # an empty chain
    assert not llm.has("tts")           # no chain at all
    assert not llm.has("no-such-role")
    assert llm.has("understand")        # the third route is usable
    assert llm.has("PHRASE")


def test_keys_are_read_at_call_time_not_when_the_llm_is_built():
    env = {}
    llm = LLM(_registry(), env=env)
    assert not llm.has("decide")
    env["HOSTED_KEY_1"] = FAKE_KEY
    assert llm.has("decide")
    del env["HOSTED_KEY_1"]
    assert not llm.has("decide")


def test_status_names_the_key_variables_and_never_a_key():
    report = _registry().status("understand", {"HOSTED_KEY_2": FAKE_KEY})
    assert [entry["usable"] for entry in report] == [False, True, True]
    assert report[0]["reason"] == "unknown provider"
    assert report[1]["keys"] == [{"number": 1, "env": "HOSTED_KEY_1", "set": False},
                                 {"number": 2, "env": "HOSTED_KEY_2", "set": True}]
    assert report[2]["reason"] == "local, no key needed"
    assert FAKE_KEY not in json.dumps(report)


def test_registry_and_route_normalise_names():
    registry = Registry({"Local": Provider("LOCAL", "http://127.0.0.1:1/v1", local=True)},
                        {"Decide": [Route(" Local ", " m ")]})
    assert "local" in registry.providers and registry.chain("DECIDE") == [Route("local", "m")]
    with pytest.raises(LLMError):
        Route("local", "")
    with pytest.raises(LLMError):
        Route.parse("just-a-model")
