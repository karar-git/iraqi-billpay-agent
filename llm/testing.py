"""Fakes for tests: an LLM that never touches the network.

    from llm.testing import fake_llm

    llm = fake_llm(lambda body, request: {"label": "balance"})     # every role answers through the function
    assert llm.choose("decide", "شكد رصيدي", ["balance", "transfer"]) == "balance"

The function gets the decoded JSON body of the request (None for a multipart upload) and the httpx
request, and returns one of: a dict (sent back as the model's JSON answer), a str (the model's text),
bytes (raw audio), an int (an HTTP error status), or a ready httpx.Response. It may also raise an
httpx exception such as httpx.ReadTimeout.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

import httpx

from llm.client import LLM
from llm.registry import ROLES, Provider, Registry, Route

Answer = Callable[[Any, httpx.Request], Any]


class FakeClock:
    """A clock that moves only when told to. Pass it as `clock`, and its `sleep` as `sleep`."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


def chat(content: Any, *, tokens_in: int = 0, tokens_out: int = 0, cost: float | None = None,
         status: int = 200) -> httpx.Response:
    """A chat-completions answer whose text is `content` (a dict is written as JSON)."""
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    usage: dict[str, Any] = {"prompt_tokens": tokens_in, "completion_tokens": tokens_out}
    if cost is not None:
        usage["cost"] = cost
    return httpx.Response(status, json={"choices": [{"message": {"role": "assistant", "content": text}}],
                                        "usage": usage})


def _body(request: httpx.Request) -> Any:
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            return json.loads(request.content)
        except ValueError:
            return None
    return None


class Endpoint:
    """An httpx transport handler that records every request and answers through `answer`."""

    def __init__(self, answer: Answer) -> None:
        self.answer = answer
        self.requests: list[httpx.Request] = []
        self.bodies: list[Any] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = _body(request)
        self.requests.append(request)
        self.bodies.append(body)
        result = self.answer(body, request)
        if isinstance(result, httpx.Response):
            return result
        if isinstance(result, bool) or result is None:
            return httpx.Response(500)
        if isinstance(result, int):
            return httpx.Response(result, json={"error": {"message": "refused"}})
        if isinstance(result, (bytes, bytearray)):
            return httpx.Response(200, content=bytes(result), headers={"content-type": "audio/ogg"})
        if request.url.path.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": result if isinstance(result, str) else ""})
        return chat(result)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    @property
    def hosts(self) -> list[str]:
        return [request.url.host for request in self.requests]


def fake_llm(answer: Answer, *, roles: tuple[str, ...] = ROLES, json_mode: str = "schema",
             env: Mapping[str, str] | None = None, clock: FakeClock | None = None) -> LLM:
    """An LLM whose every role has one keyless route ("fake:<role>") answered by `answer`.

    The recording endpoint is at `llm.endpoint` (requests, bodies).
    """
    endpoint = Endpoint(answer)
    registry = Registry(
        {"fake": Provider("fake", "http://fake.test/v1", json_mode=json_mode, local=True)},
        {role: [Route("fake", role)] for role in roles},
    )
    clock = clock or FakeClock()
    llm = LLM(registry, transport=endpoint.transport, clock=clock, sleep=clock.sleep, env=env or {})
    llm.endpoint = endpoint  # type: ignore[attr-defined]
    return llm
