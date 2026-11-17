"""The one place every model call goes through.

    llm = LLM(Registry.from_env())
    answer = llm.json("understand", messages, schema)      # a dict that passed the schema, or None

What a call does:
  - the role's chain is tried in order: the primary route, then the backups;
  - inside one provider its keys are tried in order. A key the provider refuses (401, 402, 403, 429)
    rests for a while and the next key is tried;
  - a timeout, a network error or a 5xx gets one more try on the same route, then the next route;
  - any other refusal of the request (400, 404 and the like) goes straight to the next route;
  - a JSON answer is parsed and checked against the schema here, in code. An answer that does not
    pass counts as a failure of that route, and the next route is tried;
  - the whole call lives inside one time budget. No try starts with less than half a second left.

Errors and log lines name the provider, the key number and the status code only. A key, a prompt or
a response body never appears in them.
"""

from __future__ import annotations

import base64
import logging
import math
import os
import threading
import time
from typing import Any, Callable, Mapping, Sequence

import httpx

from llm.errors import LLMError
from llm.registry import JSON_MODES, Provider, Registry, Route, keys_of
from llm.schema import describe, is_strict, parse_json, strip_think, validate

log = logging.getLogger("llm")

_KEY_STATUSES = (401, 402, 403, 429)
_AGAIN_STATUSES = (408, 425)
_NEXT_MODE = {"schema": "object", "object": "prompt"}
_AUDIO_FORMATS = {"mpeg": "mp3", "mpga": "mp3", "x-wav": "wav", "wave": "wav", "x-m4a": "m4a", "mp4": "m4a",
                  "oga": "ogg", "opus": "ogg", "x-flac": "flac"}
_AUDIO_MIMES = {"mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg", "m4a": "audio/mp4",
                "flac": "audio/flac", "webm": "audio/webm", "aac": "audio/aac"}

_SCHEMA_NOTE = (
    "Answer with one JSON object only: no words before or after it, no code fence. "
    "The object must match this JSON Schema:\n"
)


class _Fail(Exception):
    """One try did not produce an answer. `what` is safe to log: a status code or a fixed word."""

    def __init__(self, kind: str, what: str, *, status: int | None = None, rest_s: float | None = None,
                 pause: bool = False) -> None:
        super().__init__(what)
        self.kind = kind          # "key" | "again" | "request" | "invalid"
        self.what = what
        self.status = status
        self.rest_s = rest_s
        self.pause = pause


class _Ok:
    __slots__ = ("value",)

    def __init__(self, value: Any) -> None:
        self.value = value


_FAILED = object()
_SKIPPED = object()
_OUT_OF_TIME = object()

Build = Callable[[Route, dict, str], tuple[str, dict]]
Parse = Callable[[httpx.Response, str, Callable[[Any], None]], Any]


def audio_format(fmt: str) -> str:
    """"audio/ogg; codecs=opus" -> "ogg", "MP3" -> "mp3": the short name the wire format wants."""
    name = str(fmt or "").split(";")[0].strip().lower()
    name = name.rsplit("/", 1)[-1].lstrip(".")
    return _AUDIO_FORMATS.get(name, name) or "ogg"


def _merge(body: dict, extra: Any) -> dict:
    """`extra` laid over `body`; an object inside an object is merged, anything else is replaced."""
    if not isinstance(extra, dict):
        return body
    for name, value in extra.items():
        if isinstance(value, dict) and isinstance(body.get(name), dict):
            body[name] = _merge(dict(body[name]), value)
        else:
            body[name] = value
    return body


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value) if math.isfinite(value) and value > 0 else 0.0


def _with_schema_note(messages: Sequence[dict], schema: Any) -> list[dict]:
    """A copy of the messages whose system message ends with the schema (for object and prompt modes)."""
    note = _SCHEMA_NOTE + describe(schema)
    out = [dict(message) for message in messages]
    for message in out:
        if message.get("role") == "system" and isinstance(message.get("content"), str):
            message["content"] = message["content"].rstrip() + "\n\n" + note
            return out
    return [{"role": "system", "content": note}] + out


class LLM:
    """Routes every model call of the application. Thread-safe: one instance serves all worker threads."""

    default_timeout_s = 30.0       # the budget of a call when the caller names none
    default_try_timeout_s = 20.0   # the longest one try may wait when the route names no timeout_s
    connect_timeout_s = 5.0
    min_try_s = 0.5                # never start a try with less than this left
    retry_pause_s = 0.3            # the short wait before the one retry after a 5xx or a network error
    backup_share = 0.6             # with a backup route waiting, one try takes at most this share of what is left
    key_rest_s = {401: 900.0, 402: 900.0, 403: 900.0, 429: 30.0}   # how long a refused key rests
    max_rate_rest_s = 300.0        # the longest rest a Retry-After header can ask for

    def __init__(self, registry: Registry, transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 env: Mapping[str, str] | None = None) -> None:
        self._registry = registry
        self._transport = transport
        self._clock = clock
        self._sleep = sleep
        self._env: Mapping[str, str] = os.environ if env is None else env
        self._lock = threading.Lock()
        self._client: httpx.Client | None = None
        self._resting: dict[tuple[str, str], float] = {}     # (provider, key variable) -> rests until
        self._modes: dict[tuple[str, str], str] = {}         # (provider, model) -> json mode that worked
        self._counters: dict[tuple[str, int], dict[str, float]] = {}
        self._extra_usd = 0.0
        self._local = threading.local()

    # ---- what is configured ---------------------------------------------------------------------

    @property
    def registry(self) -> Registry:
        return self._registry

    def has(self, role: str) -> bool:
        """True when the role has a usable route: a known provider that is local or has a key set."""
        return self._registry.usable(role, self._env)

    # ---- the calls ------------------------------------------------------------------------------

    def json(self, role: str, messages: Sequence[dict], schema: dict, *, timeout_s: float | None = None,
             max_tokens: int = 1200, temperature: float = 0.0) -> dict | None:
        """A JSON object that passed `schema`, from the first route that gave one. None when none did."""
        messages = [dict(message) for message in messages]

        def build(route: Route, opts: dict, mode: str) -> tuple[str, dict]:
            in_prompt = mode in ("object", "prompt")
            body = self._chat_body(route, opts, _with_schema_note(messages, schema) if in_prompt else messages,
                                   max_tokens, temperature)
            if mode == "schema":
                spec: dict[str, Any] = {"name": "answer", "schema": schema}
                if opts.get("strict", is_strict(schema)):
                    spec["strict"] = True
                body["response_format"] = {"type": "json_schema", "json_schema": spec}
            elif mode == "object":
                body["response_format"] = {"type": "json_object"}
            return "/chat/completions", {"json": _merge(body, opts.get("extra_body"))}

        def parse(response: httpx.Response, mode: str, account: Callable[[Any], None]) -> dict:
            text = self._content_text(self._decoded(response, account))
            try:
                value = parse_json(text)
            except ValueError:
                raise _Fail("invalid", "answer is not JSON") from None
            if not isinstance(value, dict) or validate(value, schema):
                raise _Fail("invalid", "answer does not match the schema")
            return value

        ok, value = self._run(role, timeout_s, build, parse, structured=True)
        return value if ok else None

    def choose(self, role: str, text: str, labels: Sequence[str], *, instructions: str = "",
               timeout_s: float | None = None) -> str | None:
        """One of `labels` for `text`, or None. The answer is never anything outside the list."""
        allowed = list(dict.fromkeys(str(label) for label in labels))
        if not allowed:
            return None
        schema = {
            "type": "object",
            "properties": {"label": {"type": "string", "enum": allowed}},
            "required": ["label"],
            "additionalProperties": False,
        }
        system = (instructions.strip() + "\n\n") if instructions and instructions.strip() else ""
        system += ("Pick the one label that fits the user's message. Allowed labels: "
                   + ", ".join(allowed) + '. Answer as JSON: {"label": "<one allowed label>"}.')
        answer = self.json(role, [{"role": "system", "content": system}, {"role": "user", "content": str(text)}],
                           schema, timeout_s=timeout_s, max_tokens=200, temperature=0.0)
        label = answer.get("label") if answer else None
        return label if label in allowed else None

    def text(self, role: str, messages: Sequence[dict], *, timeout_s: float | None = None,
             max_tokens: int = 600, temperature: float = 0.2) -> str | None:
        """Plain text from the first route that answers. None when none did."""
        messages = [dict(message) for message in messages]

        def build(route: Route, opts: dict, mode: str) -> tuple[str, dict]:
            body = self._chat_body(route, opts, messages, max_tokens, temperature)
            return "/chat/completions", {"json": _merge(body, opts.get("extra_body"))}

        def parse(response: httpx.Response, mode: str, account: Callable[[Any], None]) -> str:
            return self._content_text(self._decoded(response, account))

        ok, value = self._run(role, timeout_s, build, parse)
        return value if ok else None

    def vision_json(self, role: str, image: bytes, mime: str, prompt: str, schema: dict, *,
                    timeout_s: float | None = None) -> dict | None:
        """A JSON object read from an image (an ID card, a bill), checked against `schema`."""
        data_url = f"data:{mime or 'image/jpeg'};base64,{base64.b64encode(image).decode('ascii')}"
        messages = [{"role": "user", "content": [
            {"type": "text", "text": str(prompt)},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]}]
        return self.json(role, messages, schema, timeout_s=timeout_s)

    def hear_json(self, role: str, audio: bytes, fmt: str, messages: Sequence[dict], schema: dict, *,
                  timeout_s: float | None = None) -> dict | None:
        """A JSON object from a model that listens to the audio itself. The audio joins the last user message."""
        part = {"type": "input_audio",
                "input_audio": {"data": base64.b64encode(audio).decode("ascii"), "format": audio_format(fmt)}}
        out = [dict(message) for message in messages]
        if out and out[-1].get("role") == "user":
            content = out[-1].get("content")
            if isinstance(content, str):
                parts: list[Any] = [{"type": "text", "text": content}] if content.strip() else []
            else:
                parts = list(content or [])
            out[-1]["content"] = parts + [part]
        else:
            out.append({"role": "user", "content": [part]})
        return self.json(role, out, schema, timeout_s=timeout_s)

    def transcribe(self, role: str, audio: bytes, fmt: str, *, language: str | None = None,
                   timeout_s: float | None = None) -> str:
        """The words in the audio. "" when the model heard nothing. Raises LLMError when no route answered."""
        short = audio_format(fmt)

        def build(route: Route, opts: dict, mode: str) -> tuple[str, dict]:
            extra = opts.get("extra_body")
            if opts.get("transcribe_body") == "multipart":
                fields = {"model": route.model}
                if language:
                    fields["language"] = language
                if isinstance(extra, dict):
                    fields.update({str(k): str(v) for k, v in extra.items() if isinstance(v, (str, int, float))})
                upload = (f"audio.{short}", audio, _AUDIO_MIMES.get(short, "application/octet-stream"))
                return "/audio/transcriptions", {"data": fields, "files": {"file": upload}}
            body: dict[str, Any] = {
                "model": route.model,
                "input_audio": {"data": base64.b64encode(audio).decode("ascii"), "format": short},
            }
            if language:
                body["language"] = language
            return "/audio/transcriptions", {"json": _merge(body, extra)}

        def parse(response: httpx.Response, mode: str, account: Callable[[Any], None]) -> str:
            try:
                data = response.json()
            except ValueError:
                if response.headers.get("content-type", "").startswith("text/plain"):
                    return response.text.strip()
                raise _Fail("invalid", "invalid answer") from None
            if isinstance(data, dict):
                account(data.get("usage"))
                if isinstance(data.get("text"), str):
                    return data["text"].strip()
            raise _Fail("invalid", "invalid answer")

        ok, value = self._run(role, timeout_s, build, parse)
        if not ok:
            raise LLMError(f"{role}: no route answered ({'; '.join(value) or 'no route configured'})")
        return value

    def speech(self, role: str, text: str, *, voice: str, style: str = "",
               timeout_s: float | None = None) -> bytes | None:
        """The raw audio the route returns for `text` (its format is the route's own). None when none did.

        Speech is often billed by the second or the character, which no usage field reports: record it
        with `add_cost`.
        """
        def build(route: Route, opts: dict, mode: str) -> tuple[str, dict]:
            body: dict[str, Any] = {"model": route.model, "input": str(text), "voice": voice}
            if style:
                body["instructions"] = style
            return "/audio/speech", {"json": _merge(body, opts.get("extra_body"))}

        def parse(response: httpx.Response, mode: str, account: Callable[[Any], None]) -> bytes:
            kind = response.headers.get("content-type", "").lower()
            if not response.content or kind.startswith("application/json") or kind.startswith("text/"):
                raise _Fail("invalid", "no audio in the answer")
            return response.content

        ok, value = self._run(role, timeout_s, build, parse)
        return value if ok else None

    # ---- what it cost ---------------------------------------------------------------------------

    def stats(self) -> dict:
        """Per role, one entry per route of its chain, in order:

        provider, model, calls (times the route was tried), requests (HTTP requests, retries included),
        failures (tries that gave no valid answer), fallbacks (of those failures, how many times a later
        route answered instead), skipped (no key, unknown provider or every key resting), seconds (total),
        tokens_in, tokens_out, usd.
        """
        with self._lock:
            out: dict[str, list[dict]] = {}
            for role, chain in self._registry.roles.items():
                rows = []
                for index, route in enumerate(chain):
                    c = self._counters.get((role, index), {})
                    rows.append({
                        "provider": route.provider, "model": route.model,
                        "calls": int(c.get("calls", 0)), "requests": int(c.get("requests", 0)),
                        "failures": int(c.get("failures", 0)), "fallbacks": int(c.get("fallbacks", 0)),
                        "skipped": int(c.get("skipped", 0)), "seconds": round(c.get("seconds", 0.0), 3),
                        "tokens_in": int(c.get("tokens_in", 0)), "tokens_out": int(c.get("tokens_out", 0)),
                        "usd": round(c.get("usd", 0.0), 6),
                    })
                out[role] = rows
            return out

    @property
    def spent_usd(self) -> float:
        """Everything spent so far: the routes' cost plus what `add_cost` recorded."""
        with self._lock:
            return self._extra_usd + sum(c.get("usd", 0.0) for c in self._counters.values())

    def add_cost(self, usd: float) -> None:
        """Record a cost that is billed another way (speech by the second, a fixed fee per image)."""
        amount = _number(usd)
        if amount:
            with self._lock:
                self._extra_usd += amount

    @property
    def last(self) -> dict:
        """How this thread's most recent call went: {"role", "ok", "provider", "model", "route", "tried"}.

        `route` is the place in the chain (1 is the primary), `tried` lists what failed before the answer,
        in the same safe words the log uses. For showing where an answer came from. {} before any call.
        """
        return dict(getattr(self._local, "last", {}))

    def close(self) -> None:
        with self._lock:
            client, self._client = self._client, None
        if client is not None:
            client.close()

    # ---- inside ---------------------------------------------------------------------------------

    def _http(self) -> httpx.Client:
        with self._lock:
            if self._client is None:
                self._client = httpx.Client(transport=self._transport) if self._transport else httpx.Client()
            return self._client

    def _bump(self, role: str, index: int, **amounts: float) -> None:
        with self._lock:
            counters = self._counters.setdefault((role, index), {})
            for name, amount in amounts.items():
                counters[name] = counters.get(name, 0) + amount

    def _account(self, role: str, index: int, opts: dict, usage: Any) -> None:
        """Tokens and cost of one answer. The provider's own cost wins; else tokens times the route's prices."""
        if not isinstance(usage, dict):
            return
        tokens_in = int(_number(usage.get("prompt_tokens")) or _number(usage.get("input_tokens")))
        tokens_out = int(_number(usage.get("completion_tokens")) or _number(usage.get("output_tokens")))
        cost = usage.get("cost")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0:
            usd = float(cost)
        else:
            usd = (tokens_in * _number(opts.get("usd_per_mtok_in"))
                   + tokens_out * _number(opts.get("usd_per_mtok_out"))) / 1_000_000
        self._bump(role, index, tokens_in=tokens_in, tokens_out=tokens_out, usd=usd)

    def _decoded(self, response: httpx.Response, account: Callable[[Any], None]) -> Any:
        try:
            data = response.json()
        except ValueError:
            raise _Fail("invalid", "invalid answer") from None
        if isinstance(data, dict):
            account(data.get("usage"))   # an answer that fails the schema was still paid for
        return data

    def _content_text(self, data: Any) -> str:
        """The text of the first choice of a chat-completions answer. Raises _Fail when there is none."""
        if not isinstance(data, dict):
            raise _Fail("invalid", "invalid answer")
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            error = data.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            if isinstance(code, int) and not isinstance(code, bool) and 400 <= code <= 599:
                raise self._classify(code, None)  # a failure reported inside a 200 answer
            raise _Fail("invalid", "invalid answer")
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first.get("message"), dict) else {}
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        if not isinstance(content, str):
            raise _Fail("invalid", "empty answer")
        content = strip_think(content).strip()
        if not content:
            raise _Fail("invalid", "empty answer")
        return content

    @staticmethod
    def _chat_body(route: Route, opts: dict, messages: Sequence[dict], max_tokens: int, temperature: float) -> dict:
        body: dict[str, Any] = {
            "model": route.model,
            "messages": list(messages),
            "max_tokens": opts.get("max_tokens", max_tokens),
            "temperature": opts.get("temperature", temperature),
        }
        if opts.get("effort"):
            body["reasoning"] = {"effort": opts["effort"]}
        return body

    def _classify(self, status: int, headers: Any) -> _Fail:
        what = f"status {status}"
        if status in _KEY_STATUSES:
            rest = self.key_rest_s.get(status, 60.0)
            if status == 429 and headers is not None:
                try:
                    asked = float(headers.get("retry-after", ""))
                except (TypeError, ValueError):
                    asked = 0.0
                if asked > 0 and math.isfinite(asked):
                    rest = min(asked, self.max_rate_rest_s)
            return _Fail("key", what, status=status, rest_s=rest)
        if status >= 500 or status in _AGAIN_STATUSES:
            return _Fail("again", what, status=status, pause=True)
        return _Fail("request", what, status=status)

    def _send(self, provider: Provider, key: str | None, path: str, payload: dict, timeout: float) -> httpx.Response:
        headers = dict(provider.headers or {})
        if key:
            headers["Authorization"] = "Bearer " + key
        limits = httpx.Timeout(timeout, connect=min(timeout, self.connect_timeout_s))
        # Every exception is replaced by a fixed word: the original may quote a header or the address.
        try:
            response = self._http().post(provider.base_url + path, headers=headers, timeout=limits, **payload)
        except httpx.TimeoutException:
            raise _Fail("again", "timeout") from None
        except httpx.TransportError:
            raise _Fail("again", "network error", pause=True) from None
        except Exception:
            raise _Fail("request", "request could not be sent") from None
        if 200 <= response.status_code < 300:
            return response
        raise self._classify(response.status_code, response.headers)

    def _mode(self, provider: Provider, route: Route, opts: dict) -> str:
        mode = str(opts.get("json") or "").lower()
        if mode not in JSON_MODES:
            with self._lock:
                mode = self._modes.get((provider.name, route.model), provider.json_mode)
        return mode

    def _try_timeout(self, opts: dict, remaining: float, has_later: bool) -> float:
        own = _number(opts.get("timeout_s"))
        if own:
            return min(own, remaining)
        limit = min(self.default_try_timeout_s, remaining)
        if has_later:
            limit = min(limit, max(remaining * self.backup_share, self.min_try_s))
        return limit

    def _is_resting(self, provider: str, env_name: str) -> bool:
        with self._lock:
            until = self._resting.get((provider, env_name))
            if until is None:
                return False
            if self._clock() >= until:
                del self._resting[(provider, env_name)]
                return False
            return True

    def _run(self, role: str, timeout_s: float | None, build: Build, parse: Parse, *,
             structured: bool = False) -> tuple[bool, Any]:
        """(True, the answer) from the first route that gives one, else (False, the list of what failed)."""
        role = str(role).strip().lower()
        budget = self.default_timeout_s if timeout_s is None else max(float(timeout_s), 0.0)
        deadline = self._clock() + budget
        plan = []
        for index, route in enumerate(self._registry.chain(role)):
            provider = self._registry.providers.get(route.provider)
            keys = keys_of(provider, self._env) if provider is not None else []
            plan.append((index, route, provider, keys, provider is not None and (provider.local or bool(keys))))

        notes: list[str] = []
        failed: list[int] = []
        for position, (index, route, provider, keys, usable) in enumerate(plan):
            if not usable:
                notes.append(f"{route.provider} unknown provider" if provider is None else f"{route.provider} no key")
                self._bump(role, index, skipped=1)
                continue
            if deadline - self._clock() < self.min_try_s:
                notes.append("time budget used up")
                break
            has_later = any(entry[4] for entry in plan[position + 1:])
            opts = {**(provider.options or {}), **route.options}
            outcome = self._try_route(role, index, route, provider, keys, opts, deadline, has_later,
                                      build, parse, structured, notes)
            if isinstance(outcome, _Ok):
                if failed:
                    for earlier in failed:
                        self._bump(role, earlier, fallbacks=1)
                    log.info("llm %s: route %d (%s) answered after %d earlier route(s) failed",
                             role, index + 1, provider.name, len(failed))
                self._local.last = {"role": role, "ok": True, "provider": provider.name, "model": route.model,
                                    "route": index + 1, "tried": list(notes)}
                return True, outcome.value
            if outcome is _SKIPPED:
                continue
            failed.append(index)
            if outcome is _OUT_OF_TIME:
                notes.append("time budget used up")
                break
        if plan:
            log.warning("llm %s: no route answered (%s)", role, "; ".join(notes))
        self._local.last = {"role": role, "ok": False, "provider": None, "model": None, "route": None,
                            "tried": list(notes)}
        return False, notes

    def _try_route(self, role: str, index: int, route: Route, provider: Provider,
                   keys: list[tuple[int, str, str]], opts: dict, deadline: float, has_later: bool,
                   build: Build, parse: Parse, structured: bool, notes: list[str]) -> Any:
        """One route: its keys in order, one retry. Returns _Ok, _FAILED, _SKIPPED or _OUT_OF_TIME."""
        if keys:
            live = [entry for entry in keys if not self._is_resting(provider.name, entry[1])]
            if not live:
                notes.append(f"{provider.name} every key resting")
                self._bump(role, index, skipped=1)
                return _SKIPPED
        else:
            live = [(0, "", "")]   # a local endpoint with no key

        def account(usage: Any) -> None:
            self._account(role, index, opts, usage)

        started = self._clock()
        retries_left = max(int(_number(opts.get("retries", 1))), 0) if "retries" in opts else 1
        mode_override: str | None = None
        outcome: Any = _FAILED
        requests = 0
        for number, env_name, key in live:
            who = f"{provider.name} key {number}" if number else provider.name
            next_key = False
            while True:
                remaining = deadline - self._clock()
                if remaining < self.min_try_s:
                    outcome = _OUT_OF_TIME
                    break
                mode = mode_override or self._mode(provider, route, opts)
                try:
                    path, payload = build(route, opts, mode)
                    requests += 1
                    response = self._send(provider, key or None, path, payload,
                                          self._try_timeout(opts, remaining, has_later))
                    outcome = _Ok(parse(response, mode, account))
                    if mode_override:
                        with self._lock:
                            self._modes[(provider.name, route.model)] = mode_override
                    break
                except _Fail as fail:
                    notes.append(f"{who} {fail.what}")
                    log.warning("llm %s: route %d: %s %s", role, index + 1, who, fail.what)
                    if fail.kind == "key":
                        if number:
                            with self._lock:
                                self._resting[(provider.name, env_name)] = self._clock() + (fail.rest_s or 60.0)
                        next_key = True
                        break
                    if fail.kind == "again" and retries_left > 0:
                        retries_left -= 1
                        if fail.pause:
                            pause = min(self.retry_pause_s, deadline - self._clock() - self.min_try_s)
                            if pause > 0:
                                self._sleep(pause)
                        continue
                    if (fail.kind == "request" and structured and fail.status in (400, 422)
                            and "json" not in opts and mode in _NEXT_MODE):
                        # The endpoint may not know this way of asking for JSON: ask the plainer way.
                        # The plainer way is remembered only once it has worked.
                        mode_override = _NEXT_MODE[mode]
                        continue
                    break
            if not next_key:
                break
        ok = isinstance(outcome, _Ok)
        self._bump(role, index, calls=1, requests=requests, failures=0 if ok else 1,
                   seconds=max(self._clock() - started, 0.0))
        return outcome
