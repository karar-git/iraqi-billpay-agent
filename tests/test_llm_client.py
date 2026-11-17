"""The model client: route order, failover, resting keys, the time budget, JSON modes, stats, secrecy.

Every request here goes to an httpx.MockTransport. Nothing reaches a network.
"""

import json
import logging
import threading
import traceback

import httpx
import pytest

from llm import LLM, LLMError, Provider, Registry, Route
from llm.testing import Endpoint, FakeClock, chat, fake_llm

KEY_1 = "k1-test-aaaa-1111-not-real"
KEY_2 = "k2-test-bbbb-2222-not-real"
KEY_B = "kb-test-cccc-3333-not-real"
ENV = {"ALPHA_KEY_1": KEY_1, "ALPHA_KEY_2": KEY_2, "BETA_KEY": KEY_B}

SCHEMA = {
    "type": "object",
    "properties": {"label": {"type": "string", "enum": ["a", "b"]}},
    "required": ["label"],
    "additionalProperties": False,
}
GOOD = {"label": "a"}
MESSAGES = [{"role": "system", "content": "Read the message."}, {"role": "user", "content": "hello"}]


def providers(**changes):
    found = {
        "alpha": Provider("alpha", "https://alpha.test/v1", ("ALPHA_KEY_1", "ALPHA_KEY_2")),
        "beta": Provider("beta", "https://beta.test/v1", ("BETA_KEY",), json_mode="object"),
        "local": Provider("local", "http://local.test/v1", json_mode="prompt", local=True),
    }
    found.update(changes)
    return found


def build(answers, chain, *, env=None, role="decide", provs=None):
    """An LLM over fake hosts. `answers` maps a host's first label (alpha, beta, local) to a function."""
    def answer(body, request):
        return answers[request.url.host.split(".")[0]](body, request)

    endpoint = Endpoint(answer)
    clock = FakeClock()
    registry = Registry(provs or providers(), {role: chain})
    llm = LLM(registry, transport=endpoint.transport, clock=clock, sleep=clock.sleep,
              env=ENV if env is None else env)
    return llm, endpoint, clock


def good(body, request):
    return GOOD


def key_of(request):
    return request.headers.get("authorization", "").removeprefix("Bearer ")


def slow(clock):
    """A host that never answers: the wait the client allowed passes, then the read times out."""
    def answer(body, request):
        clock.advance(request.extensions["timeout"]["read"])
        raise httpx.ReadTimeout("too slow", request=request)
    return answer


CHAIN = [Route("alpha", "big"), Route("beta", "small"), Route("local", "tiny")]


# ---- route order and failover -------------------------------------------------------------------

def test_the_primary_answers_and_the_backups_are_not_called():
    llm, endpoint, _ = build({"alpha": good}, CHAIN)
    assert llm.json("decide", MESSAGES, SCHEMA) == GOOD
    assert endpoint.hosts == ["alpha.test"]
    assert endpoint.requests[0].url.path == "/v1/chat/completions"
    assert key_of(endpoint.requests[0]) == KEY_1
    assert endpoint.bodies[0]["model"] == "big"
    assert endpoint.bodies[0]["messages"] == MESSAGES


def test_a_5xx_gets_one_retry_then_the_next_route():
    llm, endpoint, clock = build({"alpha": lambda b, r: 503, "beta": good}, CHAIN)
    assert llm.json("decide", MESSAGES, SCHEMA) == GOOD
    assert endpoint.hosts == ["alpha.test", "alpha.test", "beta.test"]
    assert [key_of(r) for r in endpoint.requests] == [KEY_1, KEY_1, KEY_B]   # a server fault is not the key's fault
    assert clock.sleeps == [LLM.retry_pause_s]
    rows = llm.stats()["decide"]
    assert (rows[0]["calls"], rows[0]["requests"], rows[0]["failures"], rows[0]["fallbacks"]) == (1, 2, 1, 1)
    assert (rows[1]["calls"], rows[1]["requests"], rows[1]["failures"], rows[1]["fallbacks"]) == (1, 1, 0, 0)
    assert rows[2]["calls"] == 0


def test_the_retry_can_succeed():
    seen = []

    def flaky(body, request):
        seen.append(1)
        return 500 if len(seen) == 1 else GOOD

    llm, endpoint, _ = build({"alpha": flaky}, CHAIN)
    assert llm.json("decide", MESSAGES, SCHEMA) == GOOD
    assert endpoint.hosts == ["alpha.test", "alpha.test"]
    assert llm.stats()["decide"][0]["failures"] == 0


def test_a_network_error_gets_one_retry_then_the_next_route():
    def down(body, request):
        raise httpx.ConnectError("connection refused", request=request)

    llm, endpoint, _ = build({"alpha": down, "beta": good}, CHAIN)
    assert llm.json("decide", MESSAGES, SCHEMA) == GOOD
    assert endpoint.hosts == ["alpha.test", "alpha.test", "beta.test"]


@pytest.mark.parametrize("status", [400, 404, 413, 422])
def test_a_refused_request_goes_straight_to_the_next_route(status):
    llm, endpoint, clock = build({"alpha": lambda b, r: status, "beta": lambda b, r: "hi"}, CHAIN)
    assert llm.text("decide", MESSAGES) == "hi"
    assert endpoint.hosts == ["alpha.test", "beta.test"]
    assert clock.sleeps == []


def test_the_retries_option_turns_the_retry_off():
    chain = [Route("alpha", "big", {"retries": 0}), Route("beta", "small")]
    llm, endpoint, _ = build({"alpha": lambda b, r: 500, "beta": good}, chain)
    assert llm.json("decide", MESSAGES, SCHEMA) == GOOD
    assert endpoint.hosts == ["alpha.test", "beta.test"]


def test_every_route_failing_gives_none():
    llm, endpoint, _ = build({"alpha": lambda b, r: 500, "beta": lambda b, r: 404, "local": lambda b, r: 500}, CHAIN)
    assert llm.json("decide", MESSAGES, SCHEMA) is None
    assert llm.text("decide", MESSAGES) is None
    rows = llm.stats()["decide"]
    assert [row["failures"] for row in rows] == [2, 2, 2]
    assert [row["fallbacks"] for row in rows] == [0, 0, 0]


def test_a_route_with_no_key_or_an_unknown_provider_is_skipped():
    chain = [Route("ghost", "x"), Route("alpha", "big"), Route("local", "tiny")]
    llm, endpoint, _ = build({"local": good}, chain, env={})
    assert llm.json("decide", MESSAGES, SCHEMA) == GOOD
    assert endpoint.hosts == ["local.test"]
    assert "authorization" not in endpoint.requests[0].headers        # a local route sends no key
    rows = llm.stats()["decide"]
    assert [row["skipped"] for row in rows] == [1, 1, 0]
    assert [row["calls"] for row in rows] == [0, 0, 1]
    assert rows[2]["fallbacks"] == 0 and rows[0]["fallbacks"] == 0   # a skipped route did not fail


def test_a_role_with_no_chain_gives_none_without_a_request():
    llm, endpoint, _ = build({}, CHAIN)
    assert llm.json("phrase", MESSAGES, SCHEMA) is None
    assert llm.text("no-such-role", MESSAGES) is None
    assert llm.choose("phrase", "x", ["a"]) is None
    assert endpoint.requests == []


def test_a_local_provider_with_a_key_sends_it():
    provs = providers(local=Provider("local", "http://local.test/v1", ("LOCAL_KEY",), local=True))
    llm, endpoint, _ = build({"local": good}, [Route("local", "tiny")], env={"LOCAL_KEY": "lk-test"}, provs=provs)
    assert llm.json("decide", MESSAGES, SCHEMA) == GOOD
    assert key_of(endpoint.requests[0]) == "lk-test"


def test_provider_headers_are_sent():
    provs = providers(local=Provider("local", "http://local.test/v1", local=True, headers={"X-Title": "billpay"}))
    llm, endpoint, _ = build({"local": good}, [Route("local", "tiny")], provs=provs)
    llm.json("decide", MESSAGES, SCHEMA)
    assert endpoint.requests[0].headers["x-title"] == "billpay"


# ---- keys that rest -----------------------------------------------------------------------------

@pytest.mark.parametrize("status", [401, 402, 403])
def test_a_refused_key_rests_and_the_next_key_is_tried(status):
    def alpha(body, request):
        return status if key_of(request) == KEY_1 else GOOD

    llm, endpoint, clock = build({"alpha": alpha}, CHAIN)
    assert llm.json("decide", MESSAGES, SCHEMA) == GOOD
    assert [key_of(r) for r in endpoint.requests] == [KEY_1, KEY_2]

    assert llm.json("decide", MESSAGES, SCHEMA) == GOOD                 # key 1 is resting: straight to key 2
    assert [key_of(r) for r in endpoint.requests] == [KEY_1, KEY_2, KEY_2]

    clock.advance(LLM.key_rest_s[status] + 1)                           # the rest is over: key 1 is tried again
    assert llm.json("decide", MESSAGES, SCHEMA) == GOOD
    assert [key_of(r) for r in endpoint.requests][3:] == [KEY_1, KEY_2]
    assert llm.stats()["decide"][0]["failures"] == 0                    # the route answered every time


def test_a_rate_limited_key_rests_for_as_long_as_the_provider_asks():
    def alpha(body, request):
        if key_of(request) == KEY_1:
            return httpx.Response(429, headers={"retry-after": "7"}, json={"error": "slow down"})
        return GOOD

    llm, endpoint, clock = build({"alpha": alpha}, CHAIN)
    llm.json("decide", MESSAGES, SCHEMA)
    clock.advance(6)
    llm.json("decide", MESSAGES, SCHEMA)
    assert [key_of(r) for r in endpoint.requests] == [KEY_1, KEY_2, KEY_2]
    clock.advance(2)
    llm.json("decide", MESSAGES, SCHEMA)
    assert [key_of(r) for r in endpoint.requests][3:] == [KEY_1, KEY_2]


def test_a_rate_limit_without_a_hint_rests_for_the_default_and_a_huge_hint_is_capped():
    hints = iter([None, "99999"])

    def alpha(body, request):
        hint = next(hints)
        return httpx.Response(429, headers={"retry-after": hint} if hint else {})

    llm, endpoint, clock = build({"alpha": alpha, "beta": good}, [Route("alpha", "big"), Route("beta", "small")],
                                 env={"ALPHA_KEY_1": KEY_1, "BETA_KEY": KEY_B})
    llm.json("decide", MESSAGES, SCHEMA)
    clock.advance(LLM.key_rest_s[429] + 1)
    llm.json("decide", MESSAGES, SCHEMA)                 # tried again, refused again with a huge hint
    clock.advance(LLM.max_rate_rest_s - 1)
    llm.json("decide", MESSAGES, SCHEMA)                 # still resting
    assert endpoint.hosts.count("alpha.test") == 2
    clock.advance(2)
    llm.json("decide", MESSAGES, SCHEMA)
    assert endpoint.hosts.count("alpha.test") == 3


def test_when_every_key_is_refused_the_next_route_answers_and_the_provider_is_skipped_next_time():
    llm, endpoint, _ = build({"alpha": lambda b, r: 401, "beta": good}, CHAIN)
    assert llm.json("decide", MESSAGES, SCHEMA) == GOOD
    assert [key_of(r) for r in endpoint.requests] == [KEY_1, KEY_2, KEY_B]
    assert llm.json("decide", MESSAGES, SCHEMA) == GOOD
    assert endpoint.hosts == ["alpha.test", "alpha.test", "beta.test", "beta.test"]
    rows = llm.stats()["decide"]
    assert (rows[0]["calls"], rows[0]["failures"], rows[0]["fallbacks"], rows[0]["skipped"]) == (1, 1, 1, 1)
    assert llm.has("decide")       # has() is about configuration, not about a resting key


def test_resting_is_per_provider():
    provs = providers(beta=Provider("beta", "https://beta.test/v1", ("ALPHA_KEY_1",), json_mode="object"))
    llm, endpoint, _ = build({"alpha": lambda b, r: 401, "beta": good}, CHAIN, provs=provs)
    assert llm.json("decide", MESSAGES, SCHEMA) == GOOD      # the same variable still serves the other provider
    assert endpoint.hosts[-1] == "beta.test"


# ---- the time budget ----------------------------------------------------------------------------

def test_a_timeout_gets_one_retry_then_the_next_route_inside_one_budget():
    chain = [Route("alpha", "big"), Route("beta", "small")]
    llm, endpoint, clock = _with_slow_alpha(chain)
    start = clock.now
    assert llm.json("decide", MESSAGES, SCHEMA, timeout_s=10) == GOOD
    waits = [r.extensions["timeout"]["read"] for r in endpoint.requests]
    assert endpoint.hosts == ["alpha.test", "alpha.test", "beta.test"]
    # with a backup waiting, one try takes at most 60% of what is left; the last route may use the rest
    assert waits == pytest.approx([6.0, 2.4, 1.6])
    assert clock.now - start <= 10
    assert clock.sleeps == []                      # no extra pause after a timeout: the wait was the pause


def _with_slow_alpha(chain, beta=good, **kwargs):
    holder = {}

    def alpha(body, request):
        return slow(holder["clock"])(body, request)

    llm, endpoint, clock = build({"alpha": alpha, "beta": beta, "local": beta}, chain, **kwargs)
    holder["clock"] = clock
    return llm, endpoint, clock


def test_no_try_starts_with_less_than_half_a_second_left():
    chain = [Route("alpha", "big"), Route("beta", "small")]
    llm, endpoint, clock = _with_slow_alpha(chain)
    start = clock.now
    assert llm.json("decide", MESSAGES, SCHEMA, timeout_s=1.0) is None
    assert endpoint.hosts == ["alpha.test"]                      # 0.6 s spent, 0.4 s left: nothing more starts
    assert clock.now - start == pytest.approx(0.6)
    rows = llm.stats()["decide"]
    assert rows[0]["failures"] == 1 and rows[1]["calls"] == 0


def test_a_budget_under_half_a_second_makes_no_request():
    llm, endpoint, _ = build({"alpha": good}, CHAIN)
    assert llm.json("decide", MESSAGES, SCHEMA, timeout_s=0.3) is None
    assert endpoint.requests == []


def test_a_route_timeout_option_caps_one_try():
    chain = [Route("alpha", "big", {"timeout_s": 3}), Route("beta", "small")]
    llm, endpoint, clock = _with_slow_alpha(chain)
    assert llm.json("decide", MESSAGES, SCHEMA, timeout_s=20) == GOOD
    waits = [r.extensions["timeout"]["read"] for r in endpoint.requests]
    assert waits == pytest.approx([3.0, 3.0, 14.0])


def test_a_route_timeout_never_exceeds_what_is_left():
    chain = [Route("alpha", "big", {"timeout_s": 30, "retries": 0})]
    llm, endpoint, clock = _with_slow_alpha(chain)
    assert llm.json("decide", MESSAGES, SCHEMA, timeout_s=4) is None
    assert [r.extensions["timeout"]["read"] for r in endpoint.requests] == pytest.approx([4.0])


def test_the_default_budget_and_the_default_try_limit():
    llm, endpoint, clock = _with_slow_alpha([Route("alpha", "big")])
    start = clock.now
    assert llm.json("decide", MESSAGES, SCHEMA) is None
    waits = [r.extensions["timeout"]["read"] for r in endpoint.requests]
    assert waits == pytest.approx([LLM.default_try_timeout_s, LLM.default_timeout_s - LLM.default_try_timeout_s])
    assert clock.now - start == pytest.approx(LLM.default_timeout_s)
    assert endpoint.requests[0].extensions["timeout"]["connect"] == LLM.connect_timeout_s


def test_seconds_are_counted_per_route():
    chain = [Route("alpha", "big", {"timeout_s": 2, "retries": 0}), Route("beta", "small")]
    llm, _, _ = _with_slow_alpha(chain)
    llm.json("decide", MESSAGES, SCHEMA, timeout_s=10)
    rows = llm.stats()["decide"]
    assert rows[0]["seconds"] == pytest.approx(2.0) and rows[1]["seconds"] == 0.0


# ---- the answer is checked in code ----------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "I think the label is a.",                 # not JSON
    '{"label": "c"}',                          # not in the enum
    '{"label": "a", "confidence": 1}',         # a property the schema does not allow
    '{}',                                      # the required field is missing
    '["a"]',                                   # not an object
    '{"label": 1}',                            # the wrong type
    "",                                        # nothing
])
def test_an_invalid_answer_fails_the_route_and_the_next_one_is_tried(bad):
    llm, endpoint, _ = build({"alpha": lambda b, r: chat(bad), "beta": good}, CHAIN)
    assert llm.json("decide", MESSAGES, SCHEMA) == GOOD
    assert endpoint.hosts == ["alpha.test", "beta.test"]          # no retry for an invalid answer
    rows = llm.stats()["decide"]
    assert rows[0]["failures"] == 1 and rows[0]["fallbacks"] == 1


def test_invalid_answers_everywhere_give_none():
    bad = lambda b, r: chat('{"label": "zzz"}')   # noqa: E731
    llm, _, _ = build({"alpha": bad, "beta": bad, "local": bad}, CHAIN)
    assert llm.json("decide", MESSAGES, SCHEMA) is None


@pytest.mark.parametrize("text", [
    '```json\n{"label": "a"}\n```',
    'Sure, here it is: {"label": "a"}',
    '<think>a or b? a.</think>\n{"label": "a"}',
])
def test_a_fence_or_words_around_the_json_are_tolerated(text):
    llm, _, _ = build({"alpha": lambda b, r: chat(text)}, CHAIN)
    assert llm.json("decide", MESSAGES, SCHEMA) == GOOD


def test_an_answer_given_as_content_parts_is_read():
    answer = httpx.Response(200, json={"choices": [{"message": {"content": [
        {"type": "text", "text": '{"label":'}, {"type": "text", "text": ' "b"}'}]}}]})
    llm, _, _ = build({"alpha": lambda b, r: answer}, CHAIN)
    assert llm.json("decide", MESSAGES, SCHEMA) == {"label": "b"}


@pytest.mark.parametrize("payload", [
    {"choices": []}, {"choices": [{"message": {"content": None}}]}, {"choices": [{}]}, {"oops": 1}, [1], "text",
])
def test_a_malformed_reply_is_a_failure_not_a_crash(payload):
    llm, endpoint, _ = build({"alpha": lambda b, r: httpx.Response(200, json=payload), "beta": lambda b, r: "ok"}, CHAIN)
    assert llm.text("decide", MESSAGES) == "ok"
    assert endpoint.hosts == ["alpha.test", "beta.test"]


def test_a_reply_that_is_not_json_at_all_is_a_failure():
    html = httpx.Response(200, content=b"<html>gateway</html>", headers={"content-type": "text/html"})
    llm, _, _ = build({"alpha": lambda b, r: html, "beta": lambda b, r: "ok"}, CHAIN)
    assert llm.text("decide", MESSAGES) == "ok"


def test_an_error_reported_inside_a_200_reply_is_treated_by_its_code():
    refused = httpx.Response(200, json={"error": {"code": 429, "message": "quota"}})
    llm, endpoint, _ = build({"alpha": lambda b, r: refused if key_of(r) == KEY_1 else GOOD}, CHAIN)
    assert llm.json("decide", MESSAGES, SCHEMA) == GOOD
    assert [key_of(r) for r in endpoint.requests] == [KEY_1, KEY_2]


def test_text_strips_the_answer_and_a_think_block():
    llm, _, _ = build({"alpha": lambda b, r: chat("<think>plan</think>\n  هلا بيك  \n")}, CHAIN)
    assert llm.text("decide", MESSAGES) == "هلا بيك"


def test_the_callers_messages_are_never_changed():
    messages = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    before = json.dumps(messages)
    llm, _, _ = build({"local": good}, [Route("local", "tiny")])
    llm.json("decide", messages, SCHEMA)
    assert json.dumps(messages) == before


# ---- JSON modes -----------------------------------------------------------------------------------

def test_schema_mode_sends_the_schema_as_the_response_format():
    llm, endpoint, _ = build({"alpha": good}, [Route("alpha", "big")])
    llm.json("decide", MESSAGES, SCHEMA)
    body = endpoint.bodies[0]
    assert body["response_format"] == {"type": "json_schema",
                                       "json_schema": {"name": "answer", "schema": SCHEMA, "strict": True}}
    assert body["messages"] == MESSAGES                            # the prompt is left alone
    assert body["max_tokens"] == 1200 and body["temperature"] == 0.0


def test_schema_mode_leaves_strict_out_for_a_schema_with_optional_fields():
    loose = {"type": "object", "properties": {"label": {"type": "string"}, "note": {"type": "string"}},
             "required": ["label"], "additionalProperties": False}
    llm, endpoint, _ = build({"alpha": lambda b, r: {"label": "x"}}, [Route("alpha", "big")])
    assert llm.json("decide", MESSAGES, loose) == {"label": "x"}
    assert "strict" not in endpoint.bodies[0]["response_format"]["json_schema"]


def test_the_strict_option_wins():
    llm, endpoint, _ = build({"alpha": good}, [Route("alpha", "big", {"strict": False})])
    llm.json("decide", MESSAGES, SCHEMA)
    assert "strict" not in endpoint.bodies[0]["response_format"]["json_schema"]


def test_object_mode_asks_for_a_json_object_and_puts_the_schema_in_the_system_message():
    llm, endpoint, _ = build({"beta": good}, [Route("beta", "small")])
    assert llm.json("decide", MESSAGES, SCHEMA) == GOOD
    body = endpoint.bodies[0]
    assert body["response_format"] == {"type": "json_object"}
    system = body["messages"][0]
    assert system["role"] == "system" and system["content"].startswith("Read the message.")
    assert json.dumps(SCHEMA, separators=(",", ":")) in system["content"]
    assert body["messages"][1:] == MESSAGES[1:]


def test_prompt_mode_sends_no_response_format_and_reads_the_json_out_of_the_text():
    llm, endpoint, _ = build({"local": lambda b, r: 'Here you go:\n```json\n{"label": "b"}\n```'},
                             [Route("local", "tiny")])
    assert llm.json("decide", MESSAGES, SCHEMA) == {"label": "b"}
    body = endpoint.bodies[0]
    assert "response_format" not in body
    assert json.dumps(SCHEMA, separators=(",", ":")) in body["messages"][0]["content"]


def test_prompt_mode_adds_a_system_message_when_there_is_none():
    llm, endpoint, _ = build({"local": good}, [Route("local", "tiny")])
    llm.json("decide", [{"role": "user", "content": "hello"}], SCHEMA)
    sent = endpoint.bodies[0]["messages"]
    assert [m["role"] for m in sent] == ["system", "user"]
    assert "JSON Schema" in sent[0]["content"]


def test_a_route_can_name_its_own_json_mode():
    llm, endpoint, _ = build({"alpha": good}, [Route("alpha", "big", {"json": "object"})])
    llm.json("decide", MESSAGES, SCHEMA)
    assert endpoint.bodies[0]["response_format"] == {"type": "json_object"}


def _refuses_schema_mode(body, request):
    return 400 if (body.get("response_format") or {}).get("type") == "json_schema" else GOOD


def test_an_endpoint_that_refuses_schema_mode_is_asked_the_plainer_way_and_that_is_remembered():
    llm, endpoint, _ = build({"alpha": _refuses_schema_mode}, [Route("alpha", "big")])
    assert llm.json("decide", MESSAGES, SCHEMA) == GOOD
    kinds = [(b.get("response_format") or {}).get("type") for b in endpoint.bodies]
    assert kinds == ["json_schema", "json_object"]
    assert llm.json("decide", MESSAGES, SCHEMA) == GOOD
    kinds = [(b.get("response_format") or {}).get("type") for b in endpoint.bodies]
    assert kinds == ["json_schema", "json_object", "json_object"]       # no second refusal
    assert llm.stats()["decide"][0]["failures"] == 0


def test_the_plainer_way_is_not_remembered_when_it_fails_too():
    llm, endpoint, _ = build({"alpha": lambda b, r: 400, "beta": good}, [Route("alpha", "big"), Route("beta", "small")])
    assert llm.json("decide", MESSAGES, SCHEMA) == GOOD
    kinds = [(b.get("response_format") or {}).get("type") for b in endpoint.bodies[:3]]
    assert kinds == ["json_schema", "json_object", None]
    endpoint.bodies.clear()
    llm.json("decide", MESSAGES, SCHEMA)
    assert endpoint.bodies[0]["response_format"]["type"] == "json_schema"   # it starts from the configured mode


def test_a_refused_text_call_is_not_asked_again():
    llm, endpoint, _ = build({"alpha": lambda b, r: 400}, [Route("alpha", "big")])
    assert llm.text("decide", MESSAGES) is None
    assert len(endpoint.requests) == 1


# ---- route options ----------------------------------------------------------------------------------

def test_effort_is_sent_only_when_set():
    llm, endpoint, _ = build({"alpha": good}, [Route("alpha", "big")])
    llm.json("decide", MESSAGES, SCHEMA)
    assert "reasoning" not in endpoint.bodies[0]

    llm, endpoint, _ = build({"alpha": good}, [Route("alpha", "big", {"effort": "low"})])
    llm.json("decide", MESSAGES, SCHEMA)
    assert endpoint.bodies[0]["reasoning"] == {"effort": "low"}


def test_route_options_override_temperature_and_max_tokens():
    llm, endpoint, _ = build({"alpha": lambda b, r: "ok"}, [Route("alpha", "big", {"temperature": 1, "max_tokens": 4000})])
    llm.text("decide", MESSAGES, max_tokens=50, temperature=0.7)
    assert endpoint.bodies[0]["temperature"] == 1 and endpoint.bodies[0]["max_tokens"] == 4000

    llm, endpoint, _ = build({"alpha": lambda b, r: "ok"}, [Route("alpha", "big")])
    llm.text("decide", MESSAGES, max_tokens=50, temperature=0.7)
    assert endpoint.bodies[0]["temperature"] == 0.7 and endpoint.bodies[0]["max_tokens"] == 50
    llm.text("decide", MESSAGES)
    assert endpoint.bodies[1]["temperature"] == 0.2 and endpoint.bodies[1]["max_tokens"] == 600


def test_extra_body_is_merged_into_the_request():
    options = {"effort": "high", "extra_body": {"seed": 7, "reasoning": {"exclude": True}, "max_tokens": 99}}
    llm, endpoint, _ = build({"alpha": good}, [Route("alpha", "big", options)])
    llm.json("decide", MESSAGES, SCHEMA)
    body = endpoint.bodies[0]
    assert body["seed"] == 7 and body["max_tokens"] == 99
    assert body["reasoning"] == {"effort": "high", "exclude": True}


def test_provider_options_are_defaults_that_a_route_overrides():
    provs = providers(alpha=Provider("alpha", "https://alpha.test/v1", ("ALPHA_KEY_1",),
                                     options={"effort": "low", "temperature": 0.9}))
    chain = [Route("alpha", "big", {"effort": "high"})]
    llm, endpoint, _ = build({"alpha": lambda b, r: "ok"}, chain, provs=provs)
    llm.text("decide", MESSAGES)
    assert endpoint.bodies[0]["reasoning"] == {"effort": "high"} and endpoint.bodies[0]["temperature"] == 0.9


# ---- choose -------------------------------------------------------------------------------------------

def test_choose_returns_one_of_the_labels():
    llm, endpoint, _ = build({"alpha": lambda b, r: {"label": "transfer"}}, [Route("alpha", "big")])
    assert llm.choose("decide", "دز لهادي خمسين", ["pay_bill", "transfer", "balance"],
                      instructions="Classify the request.") == "transfer"
    body = endpoint.bodies[0]
    schema = body["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["label"]["enum"] == ["pay_bill", "transfer", "balance"]
    assert schema["required"] == ["label"] and schema["additionalProperties"] is False
    assert body["messages"][0]["content"].startswith("Classify the request.")
    assert body["messages"][1] == {"role": "user", "content": "دز لهادي خمسين"}
    assert body["temperature"] == 0.0


@pytest.mark.parametrize("answer", [{"label": "refund"}, {"label": "Transfer"}, {"label": None}, {"choice": "transfer"},
                                    "transfer"])
def test_choose_never_returns_anything_outside_the_labels(answer):
    llm, _, _ = build({"alpha": lambda b, r: chat(answer)}, [Route("alpha", "big")])
    assert llm.choose("decide", "x", ["pay_bill", "transfer"]) is None


def test_choose_with_no_labels_or_no_route():
    llm, endpoint, _ = build({"alpha": good}, [Route("alpha", "big")])
    assert llm.choose("decide", "x", []) is None
    assert endpoint.requests == []
    assert llm.choose("decide", "x", ["a", "a", "b"]) == "a"
    assert endpoint.bodies[0]["response_format"]["json_schema"]["schema"]["properties"]["label"]["enum"] == ["a", "b"]


# ---- stats and cost ---------------------------------------------------------------------------------------

def test_tokens_and_an_estimated_cost_from_the_routes_prices():
    chain = [Route("alpha", "big", {"usd_per_mtok_in": 2.0, "usd_per_mtok_out": 10.0})]
    llm, _, _ = build({"alpha": lambda b, r: chat(GOOD, tokens_in=1000, tokens_out=200)}, chain)
    llm.json("decide", MESSAGES, SCHEMA)
    llm.json("decide", MESSAGES, SCHEMA)
    row = llm.stats()["decide"][0]
    assert (row["calls"], row["tokens_in"], row["tokens_out"]) == (2, 2000, 400)
    assert row["usd"] == pytest.approx(2 * (1000 * 2.0 + 200 * 10.0) / 1_000_000)
    assert llm.spent_usd == pytest.approx(0.008)


def test_the_providers_own_cost_wins_over_the_estimate():
    chain = [Route("alpha", "big", {"usd_per_mtok_in": 2.0, "usd_per_mtok_out": 10.0})]
    llm, _, _ = build({"alpha": lambda b, r: chat(GOOD, tokens_in=1000, tokens_out=200, cost=0.00042)}, chain)
    llm.json("decide", MESSAGES, SCHEMA)
    assert llm.stats()["decide"][0]["usd"] == pytest.approx(0.00042)
    assert llm.spent_usd == pytest.approx(0.00042)


def test_no_prices_and_no_cost_means_zero_dollars_but_tokens_are_counted():
    llm, _, _ = build({"local": lambda b, r: chat(GOOD, tokens_in=30, tokens_out=5)}, [Route("local", "tiny")])
    llm.json("decide", MESSAGES, SCHEMA)
    row = llm.stats()["decide"][0]
    assert (row["tokens_in"], row["tokens_out"], row["usd"]) == (30, 5, 0.0)
    assert llm.spent_usd == 0.0


def test_an_invalid_answer_was_still_paid_for():
    chain = [Route("alpha", "big", {"usd_per_mtok_out": 5.0}), Route("beta", "small")]
    llm, _, _ = build({"alpha": lambda b, r: chat("not json", tokens_in=10, tokens_out=1000), "beta": good}, chain)
    llm.json("decide", MESSAGES, SCHEMA)
    row = llm.stats()["decide"][0]
    assert row["failures"] == 1 and row["tokens_out"] == 1000 and row["usd"] == pytest.approx(0.005)


def test_add_cost_joins_the_total():
    llm, _, _ = build({"alpha": lambda b, r: chat(GOOD, cost=0.001)}, [Route("alpha", "big")])
    llm.json("decide", MESSAGES, SCHEMA)
    llm.add_cost(0.02)
    llm.add_cost(-5)                  # ignored
    llm.add_cost(float("nan"))        # ignored
    llm.add_cost("0.5")               # ignored: not a number
    assert llm.spent_usd == pytest.approx(0.021)


def test_stats_list_every_role_and_route_even_before_any_call():
    registry = Registry(providers(), {"decide": CHAIN, "phrase": [Route("local", "tiny")]})
    stats = LLM(registry, env={}).stats()
    assert [(row["provider"], row["model"]) for row in stats["decide"]] == [("alpha", "big"), ("beta", "small"),
                                                                           ("local", "tiny")]
    assert stats["phrase"][0] == {"provider": "local", "model": "tiny", "calls": 0, "requests": 0, "failures": 0,
                                  "fallbacks": 0, "skipped": 0, "seconds": 0.0, "tokens_in": 0, "tokens_out": 0,
                                  "usd": 0.0}


def test_weird_usage_numbers_do_not_break_the_count():
    usage = {"prompt_tokens": "many", "completion_tokens": -4, "cost": "free"}
    reply = httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": usage})
    llm, _, _ = build({"alpha": lambda b, r: reply}, [Route("alpha", "big")])
    assert llm.text("decide", MESSAGES) == "ok"
    row = llm.stats()["decide"][0]
    assert (row["tokens_in"], row["tokens_out"], row["usd"]) == (0, 0, 0.0)


def test_counters_are_safe_across_threads():
    def alpha(body, request):
        return chat(GOOD, tokens_in=3, tokens_out=1, cost=0.001)

    endpoint = Endpoint(lambda body, request: alpha(body, request))
    registry = Registry(providers(), {"decide": [Route("alpha", "big")]})
    llm = LLM(registry, transport=endpoint.transport, env=ENV)
    wrong = []

    def work():
        for _ in range(25):
            if llm.json("decide", MESSAGES, SCHEMA) != GOOD:
                wrong.append(1)
            llm.add_cost(0.001)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    llm.close()
    row = llm.stats()["decide"][0]
    assert wrong == []
    assert (row["calls"], row["requests"], row["failures"], row["tokens_in"], row["tokens_out"]) == (200, 200, 0, 600, 200)
    assert llm.spent_usd == pytest.approx(0.4)


# ---- secrecy ------------------------------------------------------------------------------------------------

PROMPT_MARK = "PROMPT-MARK-0770-123-4567"
BODY_MARK = "BODY-MARK-the-provider-said-this"


def _everything_logged(caplog):
    lines = []
    for record in caplog.records:
        lines.append(record.getMessage())
        lines.append(repr(record.args))
        if record.exc_info:
            lines.append("".join(traceback.format_exception(*record.exc_info)))
    return "\n".join(lines)


def _assert_clean(text):
    for secret in (KEY_1, KEY_2, KEY_B, PROMPT_MARK, BODY_MARK):
        assert secret not in text


def test_no_key_prompt_or_body_reaches_a_log_or_an_error(caplog):
    caplog.set_level(logging.DEBUG)
    replies = iter([
        httpx.Response(401, json={"error": {"message": BODY_MARK + " bad key " + KEY_1}}),
        httpx.Response(429, json={"error": {"message": BODY_MARK}}),
        httpx.Response(500, text=BODY_MARK),
        httpx.Response(500, text=BODY_MARK),
        chat(BODY_MARK + " not json"),
    ])
    answers = {"alpha": lambda b, r: next(replies), "beta": lambda b, r: next(replies), "local": lambda b, r: next(replies)}
    llm, endpoint, _ = build(answers, CHAIN)
    messages = [{"role": "user", "content": PROMPT_MARK}]
    assert llm.json("decide", messages, SCHEMA) is None
    assert len(endpoint.requests) == 5

    logged = _everything_logged(caplog)
    _assert_clean(logged)
    # what the log does say: the provider, the key number, the status code
    assert "alpha key 1 status 401" in logged
    assert "alpha key 2 status 429" in logged
    assert "beta key 1 status 500" in logged
    assert "no route answered" in logged
    _assert_clean(json.dumps(llm.stats()))


def test_the_error_of_transcribe_names_provider_key_number_and_status_only(caplog):
    caplog.set_level(logging.DEBUG)
    chain = [Route("alpha", "ears"), Route("local", "ears")]
    answers = {"alpha": lambda b, r: httpx.Response(403, text=BODY_MARK),
               "local": lambda b, r: httpx.Response(500, text=BODY_MARK)}
    llm, _, _ = build(answers, chain, role="stt")
    with pytest.raises(LLMError) as caught:
        llm.transcribe("stt", PROMPT_MARK.encode(), "ogg")
    error = caught.value
    text = str(error) + repr(error) + "".join(traceback.format_exception(error))
    _assert_clean(text)
    assert str(error) == ("stt: no route answered (alpha key 1 status 403; alpha key 2 status 403; "
                          "local status 500; local status 500)")
    assert error.__cause__ is None and error.__context__ is None
    _assert_clean(_everything_logged(caplog))


def test_a_key_that_cannot_even_be_sent_stays_out_of_logs(caplog):
    caplog.set_level(logging.DEBUG)

    def refuses_the_header(body, request):
        # what an HTTP library does with a header it will not send: its own error quotes the value
        raise RuntimeError("Illegal header value " + request.headers["authorization"])

    llm, endpoint, _ = build({"alpha": refuses_the_header, "beta": good},
                             [Route("alpha", "big"), Route("beta", "small")])
    assert llm.json("decide", MESSAGES, SCHEMA) == GOOD
    assert endpoint.hosts == ["alpha.test", "beta.test"]          # no retry, no second key: the next route
    logged = _everything_logged(caplog)
    _assert_clean(logged)
    assert "alpha key 1 request could not be sent" in logged


def test_a_key_with_spaces_or_a_line_break_around_it_is_trimmed():
    llm, endpoint, _ = build({"alpha": good}, [Route("alpha", "big")], env={"ALPHA_KEY_1": "  " + KEY_1 + "\n"})
    assert llm.json("decide", MESSAGES, SCHEMA) == GOOD
    assert key_of(endpoint.requests[0]) == KEY_1


def test_timeouts_and_network_errors_are_logged_as_fixed_words(caplog):
    caplog.set_level(logging.DEBUG)

    def down(body, request):
        raise httpx.ConnectError(BODY_MARK + KEY_1, request=request)

    llm, _, _ = build({"alpha": down}, [Route("alpha", "big")])
    assert llm.text("decide", [{"role": "user", "content": PROMPT_MARK}]) is None
    logged = _everything_logged(caplog)
    _assert_clean(logged)
    assert "alpha key 1 network error" in logged


# ---- where an answer came from ------------------------------------------------------------------------------

def test_last_says_which_route_answered_and_what_failed_before_it():
    llm, _, _ = build({"alpha": lambda b, r: 401 if key_of(r) == KEY_1 else 404, "beta": good}, CHAIN)
    assert llm.last == {}
    assert llm.json("decide", MESSAGES, SCHEMA) == GOOD
    assert llm.last == {"role": "decide", "ok": True, "provider": "beta", "model": "small", "route": 2,
                        "tried": ["alpha key 1 status 401", "alpha key 2 status 404"]}
    llm.last["provider"] = "changed"                   # a copy: changing it changes nothing
    assert llm.last["provider"] == "beta"


def test_last_after_a_call_nothing_answered():
    llm, _, _ = build({"alpha": lambda b, r: 404}, [Route("alpha", "big")])
    assert llm.text("decide", MESSAGES) is None
    assert llm.last == {"role": "decide", "ok": False, "provider": None, "model": None, "route": None,
                        "tried": ["alpha key 1 status 404"]}


def test_last_is_kept_per_thread():
    llm, _, _ = build({"alpha": good}, [Route("alpha", "big")])
    llm.json("decide", MESSAGES, SCHEMA)
    seen = []
    thread = threading.Thread(target=lambda: seen.append(llm.last))
    thread.start()
    thread.join()
    assert seen == [{}] and llm.last["ok"] is True


def test_the_backup_share_applies_only_while_a_usable_backup_waits():
    chain = [Route("alpha", "big", {"retries": 0}), Route("beta", "small")]
    llm, endpoint, _ = _with_slow_alpha(chain, env={"ALPHA_KEY_1": KEY_1})      # beta has no key: no backup
    assert llm.json("decide", MESSAGES, SCHEMA, timeout_s=10) is None
    assert [r.extensions["timeout"]["read"] for r in endpoint.requests] == pytest.approx([10.0])


# ---- the fake for other tests ---------------------------------------------------------------------------------

def test_fake_llm_answers_every_role_without_a_network():
    llm = fake_llm(lambda body, request: {"label": "a"} if "response_format" in (body or {}) else "مرحبا")
    assert llm.has("decide") and llm.has("tts")
    assert llm.choose("decide", "x", ["a", "b"]) == "a"
    assert llm.text("phrase", MESSAGES) == "مرحبا"
    assert [body["model"] for body in llm.endpoint.bodies] == ["decide", "phrase"]
