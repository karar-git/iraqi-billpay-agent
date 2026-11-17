"""eval/compare_models.py: the estimate comes first, a hosted candidate needs --allow-paid, the table is honest.

Every "model" here is a function behind llm.testing's recording endpoint. Nothing reaches a network, and a
test that expects a refusal also checks that no model layer was even built.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from core.open_model import ASKING_NOTES
from core.stub import StubCore
from eval import compare_models as C
from eval import run_eval as E
from llm import LLM, Registry
from llm.testing import Endpoint, FakeClock, chat

SLOTS = ("payee_mention", "biller_mention", "amount_text", "currency", "operator", "period")
STUB = StubCore()


def as_request(intent) -> dict:
    return {"action": intent.action, **{slot: getattr(intent, slot) for slot in SLOTS}}


def good(body, request):
    """A model that reads as well as the stand-in does (it is the stand-in, answering in the model's format)."""
    system, text = body["messages"][0]["content"], body["messages"][-1]["content"]
    intent = STUB.understand(text, {"asking": "operator"} if ASKING_NOTES["operator"] in system else {})
    return {"requests": [as_request(intent)] + [as_request(extra) for extra in intent.extra],
            "language": "iq", "language_sure": False}


def weak(body, request):
    """A model that understands nothing: every message is small talk."""
    return {"requests": [{"action": "chat", **{slot: None for slot in SLOTS}}], "language": "iq", "language_sure": False}


def hostile(body, request):
    """A model that always wants a million sent to somebody the user never named."""
    return {"requests": [{"action": "transfer", "payee_mention": "حساب غريب", "biller_mention": None,
                          "amount_text": "مليون", "currency": "IQD", "operator": "korek", "period": "previous"}],
            "language": "en", "language_sure": True}


def priced(body, request):
    return chat(good(body, request), tokens_in=500, tokens_out=50, cost=0.001)


MODELS = {"good": good, "good-too": good, "weak": weak, "hostile": hostile, "priced": priced, "down": lambda b, r: 503}


class Factory:
    """Builds the model layer for a candidate, on a fake endpoint that answers by model name."""

    def __init__(self):
        self.registries: list[Registry] = []
        self.endpoints: list[Endpoint] = []

    def __call__(self, registry: Registry) -> LLM:
        endpoint = Endpoint(lambda body, request: MODELS[body["model"]](body, request))
        clock = FakeClock()
        self.registries.append(registry)
        self.endpoints.append(endpoint)
        return LLM(registry, transport=endpoint.transport, clock=clock, sleep=clock.sleep, env={"TOGETHER_API_KEY": "k"})


def run(*argv: str, env: dict | None = None) -> tuple[int, str, Factory]:
    out, factory = io.StringIO(), Factory()
    code = C.main(list(argv), make_llm=factory, env=env or {}, out=out)
    return code, out.getvalue(), factory


def table_row(text: str, name: str) -> list[str]:
    line = next(line for line in text.splitlines() if line.startswith(name + " "))
    return line.split()


# ------------------------------------------------------------------ the input is the development set, only


def test_it_reads_the_development_set_and_has_no_way_to_read_another():
    assert C.DEV_CASES == E.CASES_DIR / "dev.jsonl" and C.DEV_CASES.is_file()
    for forbidden in ("--cases", "--set", "--locked"):
        code, _, factory = run("--candidate", "a=ollama:good", forbidden, "eval/locked/locked_v1.jsonl")
        assert code == 2 and factory.registries == []
    source = Path(C.__file__).read_text(encoding="utf-8")
    assert "locked_v1" not in source and "run_locked" not in source


def test_a_sample_is_spread_over_the_set_and_always_the_same():
    cases = E.load_cases([C.DEV_CASES])
    picked = C.sample(cases, 12)
    assert len(picked) == 12 and picked == C.sample(cases, 12)
    assert len({case["id"] for case in picked}) == 12
    assert len({case["type"] for case in picked}) >= 8  # not the first twelve of one type
    assert C.sample(cases, None) == cases and C.sample(cases, 10_000) == cases


def test_the_estimate_counts_messages_a_model_could_be_asked_about():
    cases = [
        {"messages": ["دز لهادي", "50000"], "expected": {"outcome": "EXECUTE", "fields": {"kind": "transfer"}}},
        {"messages": ["ادفع المولدة"], "expected": {"outcome": "EXECUTE", "fields": {"kind": "bill"}}},
        {"messages": ["شلونك"], "expected": {"outcome": "REFUSE"}},
    ]
    assert C.estimate_calls(cases) == 4  # three messages with words, and the yes a bill case may add
    assert 400 < C.tokens_per_call() < 3000


# ------------------------------------------------------------------ refusals: nothing is called


def test_a_hosted_candidate_is_refused_without_allow_paid():
    code, text, factory = run("--candidate", "host=together:priced", "--limit", "6")
    assert code == 2 and factory.registries == []  # no model layer was built, so no call was made
    assert "Refused, nothing was called" in text and "--allow-paid" in text and "host (together:priced)" in text
    assert text.index("Estimated model calls") < text.index("Refused")  # the estimate comes first
    assert "passed" not in text  # no table


def test_one_hosted_candidate_stops_the_whole_run():
    code, text, factory = run("--candidate", "local=ollama:good", "--candidate", "host=groq:priced", "--limit", "6")
    assert code == 2 and factory.registries == [] and "host (groq:priced)" in text and "local (" not in text.split("Refused")[1]


def test_a_hosted_candidate_without_a_key_cannot_run_and_no_key_is_printed():
    code, text, factory = run("--candidate", "host=together:priced", "--allow-paid", "--limit", "6",
                              env={"OTHER_API_KEY": "sk-very-secret"})
    assert code == 2 and factory.registries == []
    assert "Cannot run host (together:priced): no key set" in text and "sk-very-secret" not in text


def test_an_unknown_provider_cannot_run():
    code, text, factory = run("--candidate", "x=nowhere:model", "--allow-paid", "--limit", "6")
    assert code == 2 and factory.registries == [] and "unknown provider" in text


@pytest.mark.parametrize("argv, message", [
    ((), "at least one --candidate"),
    (("--candidate", "no-equals-sign"), "NAME=provider:model"),
    (("--candidate", "a=modelonly"), "candidate a"),
    (("--candidate", "=ollama:good"), "NAME=provider:model"),
    (("--candidate", "a=ollama:good", "--candidate", "a=ollama:weak"), "same name"),
    (("--candidate", "a=ollama:good", "--bar", "1.5"), "--bar"),
    (("--candidate", "a=ollama:good", "--bar", "0"), "--bar"),
    (("--candidate", "a=ollama:good", "--limit", "0"), "--limit"),
])
def test_bad_arguments_run_nothing(argv, message):
    code, text, factory = run(*argv)
    assert code == 2 and message in text and factory.registries == []


def test_a_broken_model_configuration_is_reported():
    code, text, factory = run("--candidate", "a=ollama:good", env={"LLM_UNDERSTAND": "no-provider"})
    assert code == 2 and "model configuration" in text and factory.registries == []


# ------------------------------------------------------------------ a run


@pytest.fixture(scope="module")
def compared(tmp_path_factory) -> tuple[int, str, Factory, dict]:
    """One run of three candidates on a sample, shared by the tests below."""
    path = tmp_path_factory.mktemp("compare") / "out.json"
    code, text, factory = run("--candidate", "tiny=ollama:weak", "--candidate", "mid=ollama:good",
                              "--candidate", "big=ollama:good-too", "--limit", "14", "--json", str(path))
    return code, text, factory, json.loads(path.read_text(encoding="utf-8"))


def test_the_plan_is_printed_before_anything_runs(compared):
    code, text, _, _ = compared
    assert code == 0
    cases = C.sample(E.load_cases([C.DEV_CASES]), 14)
    per = C.estimate_calls(cases)
    assert f"Estimated model calls: at most {per} per candidate, {per * 3} in total for 3 candidate(s)" in text
    assert text.index("Estimated model calls") < text.index("Running")
    assert "tiny: ollama:weak  (local, no key)" in text


def test_each_candidate_runs_alone_on_its_one_route(compared):
    _, _, factory, _ = compared
    assert [{role: [route.spec for route in chain] for role, chain in registry.roles.items()}
            for registry in factory.registries] == [{"understand": ["ollama:weak"]}, {"understand": ["ollama:good"]},
                                                    {"understand": ["ollama:good-too"]}]
    for endpoint, name in zip(factory.endpoints, ("weak", "good", "good-too")):
        assert endpoint.bodies and all(body["model"] == name for body in endpoint.bodies)
        assert set(endpoint.hosts) == {"127.0.0.1"}


def test_the_table_has_the_numbers_of_each_candidate(compared):
    _, text, factory, report = compared
    assert all(column in text for column in ("passed", "wrong cards", "no-tap moves", "s/case", "cost USD", "calls"))
    baseline, rows = report["baseline"], {row["name"]: row for row in report["candidates"]}
    assert baseline["passed"] == baseline["cases"] == 14 and report["cases"] == 14 and report["of"] >= 90
    assert table_row(text, "stand-in")[-5:-3] == ["0", "0"] and f"{baseline['passed']}/14" in table_row(text, "stand-in")
    assert rows["mid"]["passed"] == rows["big"]["passed"] == baseline["passed"]  # as good as the stand-in it wraps
    assert rows["tiny"]["passed"] < report["needed"]
    for name, endpoint in zip(("tiny", "mid", "big"), factory.endpoints):
        row = rows[name]
        assert row["calls"] == len(endpoint.bodies) > 0
        assert row["wrong_cards"] == 0 and row["executed_without_tap"] == 0 and row["audit_failures"] == 0
        assert row["seconds_per_case"] >= 0 and row["usd"] == 0.0 and row["local"] is True
        assert f"{row['passed']}/14" in table_row(text, name) and table_row(text, name)[-1] == ("yes" if row["meets_bar"] else "no")
        assert len(row["results"]) == 14
    assert rows["tiny"]["calls"] <= C.estimate_calls(C.sample(E.load_cases([C.DEV_CASES]), 14))


def test_the_smallest_candidate_that_meets_the_bar_is_the_first_in_the_order_given(compared):
    _, text, _, report = compared
    assert [row["meets_bar"] for row in report["candidates"]] == [False, True, True]
    assert report["smallest_that_meets_the_bar"] == "mid"
    assert "Smallest candidate that meets the bar: mid (ollama:good)" in text
    assert report["needed"] == C.needed_passes(0.95, 14) == 14


def test_a_hostile_candidate_makes_no_wrong_card_and_meets_no_bar():
    code, text, factory = run("--candidate", "bad=ollama:hostile", "--limit", "14")
    assert code == 0  # nothing unsafe happened
    row = table_row(text, "bad")
    assert row[-6:-4] == ["0", "0"] and row[-1] == "no"  # wrong cards, moves without a tap, the bar
    assert "No candidate meets the bar" in text
    assert "pointers dropped" in text and " 0 pointers dropped" not in text


def test_a_model_that_is_down_falls_back_and_is_counted(caplog):
    import logging

    logger = logging.getLogger("llm")
    before = logger.level
    with caplog.at_level(logging.WARNING):
        code, text, factory = run("--candidate", "off=ollama:down", "--limit", "8")
    assert code == 0 and table_row(text, "off")[-1] == "no"
    assert "0 readings fell back" not in text and "No candidate meets the bar" in text
    assert "(0 failed calls)" not in text
    assert logger.level == before  # the model layer's log is quiet during the run and back as it was after
    assert not [record for record in caplog.records if record.name == "llm"]


def test_a_paid_candidate_runs_with_allow_paid_and_its_cost_is_shown():
    code, text, factory = run("--candidate", "host=together:priced", "--allow-paid", "--limit", "6",
                              env={"TOGETHER_API_KEY": "k"})
    assert code == 0 and "host: together:priced  (hosted, api.together.xyz)" in text
    calls = len(factory.endpoints[0].bodies)
    row = table_row(text, "host")
    assert row[-2] == str(calls) and float(row[-3]) == pytest.approx(0.001 * calls)
    assert "Bearer" not in text and " k " not in text


# ------------------------------------------------------------------ the bar and the exit code


@pytest.mark.parametrize("bar, baseline, needed", [(0.95, 146, 139), (0.95, 100, 95), (1.0, 146, 146),
                                                   (0.5, 7, 4), (0.95, 0, 0)])
def test_needed_passes(bar, baseline, needed):
    assert C.needed_passes(bar, baseline) == needed


@pytest.mark.parametrize("change, ok", [({}, True), ({"passed": 138}, False), ({"wrong_cards": 1}, False),
                                        ({"executed_without_tap": 1}, False), ({"audit_failures": 1}, False),
                                        ({"passed": 139}, True)])
def test_meeting_the_bar_needs_every_safety_number_at_zero(change, ok):
    row = {"passed": 146, "wrong_cards": 0, "executed_without_tap": 0, "audit_failures": 0, **change}
    assert C.meets_bar(row, 139) is ok


def test_money_moved_without_a_tap_is_exit_code_one(monkeypatch):
    real = C.run_candidate

    def moved(*args, **kwargs):
        row = real(*args, **kwargs)
        row["executed_without_tap"] = 1
        return row

    monkeypatch.setattr(C, "run_candidate", moved)
    code, text, _ = run("--candidate", "a=ollama:good", "--limit", "4")
    assert code == 1 and table_row(text, "a")[-1] == "no"


def test_the_script_follows_the_text_rules():
    text = Path(C.__file__).read_text(encoding="utf-8")
    assert chr(0x0686) not in text and chr(0x2014) not in text and chr(0x2013) not in text
