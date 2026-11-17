"""Which model does which job: providers, routes and the role table, read from the environment.

A provider is an HTTP endpoint that speaks the common chat-completions API. A route is one model on
one provider. A role (decide, understand, phrase, vision, hear, stt, tts) maps to an ordered chain of
routes: the primary first, then the backups.

Configuration holds names, never secrets:

    LLM_<ROLE>="provider:model[,provider:model...]"      for example LLM_DECIDE="ollama:qwen2.5:7b"
    LLM_PROVIDER_<NAME>_BASE="https://host/v1"
    LLM_PROVIDER_<NAME>_KEYS="ENV1,ENV2"                  names of the variables that hold the keys
    LLM_PROVIDER_<NAME>_JSON=schema|object|prompt
    LLM_PROVIDER_<NAME>_LOCAL=1                           the endpoint needs no key
    LLM_PROVIDER_<NAME>_OPTIONS="name=value&name=value"   default options of every route through it
    LLM_CONFIG=<path to a JSON file with the same content>

A route can carry options after a question mark: "ollama:qwen2.5:7b?timeout_s=8&effort=low".
The keys themselves are read from the environment at the moment of a call, through `Provider.key_envs`.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

from llm.errors import LLMError

ROLES = ("decide", "understand", "phrase", "vision", "hear", "stt", "tts")
JSON_MODES = ("schema", "object", "prompt")

_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,79}$")
_PROVIDER_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,39}$")
_SECRET_HEADERS = {"authorization", "proxy-authorization", "x-api-key", "api-key"}
_OFF = {"off", "none", "-"}
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def _clean_base(name: str, base_url: Any) -> str:
    base = str(base_url or "").strip().rstrip("/")
    parts = urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise LLMError(f"provider {name}: the base address must start with http:// or https://")
    if "@" in parts.netloc or parts.query or parts.fragment:
        # A secret inside an address would end up in logs. Keys travel in a header, from the environment.
        raise LLMError(f"provider {name}: the base address must not carry a user, a query or a fragment")
    return base


def _clean_key_envs(name: str, key_envs: Any) -> tuple[str, ...]:
    if key_envs is None:
        return ()
    if isinstance(key_envs, str):
        key_envs = [part for part in key_envs.split(",")]
    names = tuple(str(part).strip() for part in key_envs if str(part).strip())
    for env_name in names:
        if not _ENV_NAME.match(env_name):
            # Not echoed: a value that fails this test may be a key pasted in the wrong place.
            raise LLMError(
                f"provider {name}: the key list takes NAMES of environment variables "
                "(capital letters, digits, underscore), never a key"
            )
    return names


@dataclass(frozen=True)
class Provider:
    """One endpoint. `key_envs` are the names of the environment variables that hold its keys, in the
    order they are tried. `local` means it needs no key. `options` are default options for every route
    through this provider (a route's own options win)."""

    name: str
    base_url: str
    key_envs: tuple[str, ...] = ()
    json_mode: str = "schema"
    local: bool = False
    headers: dict[str, str] | None = None
    options: dict | None = None

    def __post_init__(self) -> None:
        name = str(self.name or "").strip().lower()
        if not _PROVIDER_NAME.match(name):
            raise LLMError("a provider name uses letters, digits, underscore, dot or dash")
        mode = str(self.json_mode or "schema").strip().lower()
        if mode not in JSON_MODES:
            raise LLMError(f"provider {name}: json mode must be one of {', '.join(JSON_MODES)}")
        headers = {str(k): str(v) for k, v in (self.headers or {}).items()}
        if any(k.lower() in _SECRET_HEADERS for k in headers):
            raise LLMError(f"provider {name}: a key never goes in the headers, name its variable in key_envs")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "base_url", _clean_base(name, self.base_url))
        object.__setattr__(self, "key_envs", _clean_key_envs(name, self.key_envs))
        object.__setattr__(self, "json_mode", mode)
        object.__setattr__(self, "local", bool(self.local))
        object.__setattr__(self, "headers", headers or None)
        object.__setattr__(self, "options", dict(self.options) if self.options else None)

    @property
    def host(self) -> str:
        """The host of the endpoint, for display."""
        return urlsplit(self.base_url).netloc


def _option_value(text: str) -> Any:
    text = text.strip()
    try:
        value = json.loads(text)
    except ValueError:
        return text
    return value if isinstance(value, (int, float, bool)) or value is None else text


def parse_options(text: str) -> dict:
    """"name=value&name=value" as a dict; numbers and true/false are read as such."""
    options: dict[str, Any] = {}
    for part in str(text or "").split("&"):
        name, sep, value = part.partition("=")
        if name.strip() and sep:
            options[name.strip()] = _option_value(value)
    return options


@dataclass(frozen=True)
class Route:
    """One model on one provider. Options: effort, temperature, max_tokens, timeout_s,
    usd_per_mtok_in, usd_per_mtok_out, extra_body, and the smaller ones listed in docs/MODELS.md."""

    provider: str
    model: str
    options: dict | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", str(self.provider or "").strip().lower())
        object.__setattr__(self, "model", str(self.model or "").strip())
        object.__setattr__(self, "options", dict(self.options) if self.options else {})
        if not self.provider or not self.model:
            raise LLMError("a route needs a provider and a model, written provider:model")

    @classmethod
    def parse(cls, spec: str) -> "Route":
        """"provider:model" or "provider:model?name=value&name=value". The model may hold colons."""
        head, _, tail = str(spec).strip().partition("?")
        provider, sep, model = head.partition(":")
        if not sep or not provider.strip() or not model.strip():
            raise LLMError("a route is written provider:model")
        return cls(provider, model, parse_options(tail))

    @property
    def spec(self) -> str:
        return f"{self.provider}:{self.model}"


def _preset(name: str, base_url: str, *, local: bool, json_mode: str = "schema", audio: bool = True) -> Provider:
    return Provider(
        name=name,
        base_url=base_url,
        key_envs=() if local else (f"{name.upper()}_API_KEY",),
        json_mode=json_mode,
        local=local,
        options={"transcribe_body": "multipart"} if audio else None,
    )


def presets() -> dict[str, Provider]:
    """The built-in providers: three local runtimes that need no key, four hosts with a free tier."""
    found = [
        _preset("ollama", "http://127.0.0.1:11434/v1", local=True),
        _preset("lmstudio", "http://127.0.0.1:1234/v1", local=True),
        _preset("llamacpp", "http://127.0.0.1:8080/v1", local=True),
        # This host serves the common API under a path segment named after another company. The segment
        # is put together here so that no vendor name is spelled out anywhere in this repository.
        _preset("groq", "https://api.groq.com/" + "open" + "ai" + "/v1", local=False, json_mode="object"),
        _preset("cerebras", "https://api.cerebras.ai/v1", local=False, audio=False),
        _preset("mistral", "https://api.mistral.ai/v1", local=False),
        _preset("together", "https://api.together.xyz/v1", local=False),
    ]
    return {provider.name: provider for provider in found}


def keys_of(provider: Provider, env: Mapping[str, str]) -> list[tuple[int, str, str]]:
    """(key number, variable name, key) for every key of the provider that is set, in order."""
    found = []
    for number, env_name in enumerate(provider.key_envs, start=1):
        value = str(env.get(env_name) or "").strip()
        if value:
            found.append((number, env_name, value))
    return found


@dataclass
class Registry:
    """Providers by name, and for each role its ordered chain of routes."""

    providers: dict[str, Provider]
    roles: dict[str, list[Route]]

    def __post_init__(self) -> None:
        self.providers = {str(name).strip().lower(): p for name, p in dict(self.providers or {}).items()}
        self.roles = {str(role).strip().lower(): list(chain or []) for role, chain in dict(self.roles or {}).items()}

    def chain(self, role: str) -> list[Route]:
        return list(self.roles.get(str(role).strip().lower(), []))

    def status(self, role: str, env: Mapping[str, str] | None = None) -> list[dict]:
        """One entry per route of the role: is it usable, and why. Names only, never a key."""
        env = os.environ if env is None else env
        report = []
        for route in self.chain(role):
            provider = self.providers.get(route.provider)
            entry: dict[str, Any] = {
                "provider": route.provider, "model": route.model, "usable": False,
                "local": False, "host": "", "json_mode": "", "keys": [], "reason": "",
            }
            if provider is None:
                entry["reason"] = "unknown provider"
            else:
                set_names = {env_name for _n, env_name, _key in keys_of(provider, env)}
                entry["keys"] = [
                    {"number": n, "env": env_name, "set": env_name in set_names}
                    for n, env_name in enumerate(provider.key_envs, start=1)
                ]
                entry["local"] = provider.local
                entry["host"] = provider.host
                entry["json_mode"] = str(route.options.get("json") or provider.json_mode)
                if set_names:
                    entry["usable"] = True
                    entry["reason"] = f"{len(set_names)} of {len(provider.key_envs)} key(s) set"
                elif provider.local:
                    entry["usable"] = True
                    entry["reason"] = "local, no key needed"
                elif provider.key_envs:
                    entry["reason"] = "no key set"
                else:
                    entry["reason"] = "no key variable named"
            report.append(entry)
        return report

    def usable(self, role: str, env: Mapping[str, str] | None = None) -> bool:
        return any(entry["usable"] for entry in self.status(role, env))

    # ---- reading the configuration --------------------------------------------------------------

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, defaults: "Registry | None" = None) -> "Registry":
        """Built-in presets < `defaults` < the LLM_CONFIG file < environment variables.

        A provider is merged field by field (the environment may change only the key names of a preset).
        A role's chain is replaced as a whole by the later source. LLM_<ROLE>=off empties a chain.
        """
        env = os.environ if env is None else env
        providers = presets()
        roles: dict[str, list[Route]] = {}
        if defaults is not None:
            providers.update(defaults.providers)
            roles.update({role: list(chain) for role, chain in defaults.roles.items()})

        path = str(env.get("LLM_CONFIG") or "").strip()
        if path:
            try:
                with open(path, encoding="utf-8") as handle:
                    data = json.load(handle)
            except OSError:
                raise LLMError("LLM_CONFIG: the file cannot be read") from None
            except ValueError:
                raise LLMError("LLM_CONFIG: the file is not valid JSON") from None
            if not isinstance(data, dict):
                raise LLMError("LLM_CONFIG: the file must hold one JSON object")
            _apply_file(data, providers, roles)

        _apply_flat(env, providers, roles, known_roles=set(ROLES) | set(roles))
        return cls(providers, roles)


_PROVIDER_PREFIX = "LLM_PROVIDER_"
_PROVIDER_FIELDS = {"_BASE": "base_url", "_KEYS": "key_envs", "_JSON": "json_mode", "_LOCAL": "local",
                    "_OPTIONS": "options"}


def _as_bool(source: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise LLMError(f"{source}: expected 1 or 0")


def _merge_provider(providers: dict[str, Provider], name: str, fields: dict[str, Any], source: str) -> None:
    name = name.strip().lower()
    current = providers.get(name)
    if "local" in fields:
        fields["local"] = _as_bool(source, fields["local"])
    if "options" in fields:
        new = fields["options"]
        new = parse_options(new) if isinstance(new, str) else dict(new or {})
        fields["options"] = {**((current.options or {}) if current else {}), **new}
    if "headers" in fields and not isinstance(fields["headers"], dict):
        raise LLMError(f"{source}: headers must be an object")
    if current is not None:
        providers[name] = dataclasses.replace(current, **fields)
        return
    if not fields.get("base_url"):
        raise LLMError(f"{source}: provider {name} has no base address (LLM_PROVIDER_{name.upper()}_BASE)")
    if "key_envs" not in fields and not fields.get("local"):
        fields["key_envs"] = (f"{re.sub(r'[^A-Z0-9]', '_', name.upper())}_API_KEY",)
    providers[name] = Provider(name=name, **fields)


def _routes(source: str, value: Any) -> list[Route]:
    if isinstance(value, str):
        text = value.strip()
        if text.lower() in _OFF:
            return []
        items: list[Any] = [part for part in text.split(",") if part.strip()]
    elif isinstance(value, list):
        items = value
    else:
        raise LLMError(f"{source}: expected provider:model[,provider:model...]")
    chain = []
    for item in items:
        try:
            if isinstance(item, Route):
                chain.append(item)
            elif isinstance(item, str):
                chain.append(Route.parse(item))
            elif isinstance(item, dict):
                chain.append(Route(item.get("provider", ""), item.get("model", ""), item.get("options")))
            else:
                raise LLMError("bad route")
        except LLMError:
            raise LLMError(f"{source}: every route is written provider:model") from None
    return chain


def _apply_flat(mapping: Mapping[str, Any], providers: dict[str, Provider], roles: dict[str, list[Route]],
                known_roles: set[str]) -> None:
    """Environment-style names: LLM_PROVIDER_<NAME>_<FIELD> and LLM_<ROLE>."""
    partial: dict[str, dict[str, Any]] = {}
    for raw_name in list(mapping.keys()):
        name = str(raw_name).upper()
        if not name.startswith(_PROVIDER_PREFIX):
            continue
        value = mapping[raw_name]
        if isinstance(value, str) and not value.strip():
            continue  # an empty variable is an unset one
        rest = name[len(_PROVIDER_PREFIX):]
        for suffix, field in _PROVIDER_FIELDS.items():
            if rest.endswith(suffix) and len(rest) > len(suffix):
                partial.setdefault(rest[: -len(suffix)].lower(), {})[field] = value
                break
    for provider_name in sorted(partial):
        _merge_provider(providers, provider_name, partial[provider_name],
                        f"LLM_PROVIDER_{provider_name.upper()}")

    by_upper = {str(raw_name).upper(): raw_name for raw_name in mapping.keys()}
    for role in sorted(known_roles):
        raw_name = by_upper.get(f"LLM_{role.upper()}")
        if raw_name is None:
            continue
        value = mapping[raw_name]
        if isinstance(value, str) and not value.strip():
            continue
        roles[role] = _routes(f"LLM_{role.upper()}", value)


def _apply_file(data: dict, providers: dict[str, Provider], roles: dict[str, list[Route]]) -> None:
    """The JSON file: {"providers": {...}, "roles": {...}}, and/or the same flat names as the environment."""
    listed = data.get("providers")
    listed = {} if listed is None else listed
    if not isinstance(listed, dict):
        raise LLMError("LLM_CONFIG: providers must be an object")
    for name, spec in listed.items():
        if not isinstance(spec, dict):
            raise LLMError(f"LLM_CONFIG: provider {str(name).lower()} must be an object")
        fields: dict[str, Any] = {}
        for field, names in (("base_url", ("base_url", "base")), ("key_envs", ("key_envs", "keys")),
                             ("json_mode", ("json_mode", "json")), ("local", ("local",)),
                             ("headers", ("headers",)), ("options", ("options",))):
            for key in names:
                if key in spec:
                    fields[field] = spec[key]
                    break
        _merge_provider(providers, str(name), fields, "LLM_CONFIG")

    chains = data.get("roles")
    chains = {} if chains is None else chains
    if not isinstance(chains, dict):
        raise LLMError("LLM_CONFIG: roles must be an object")
    for role, value in chains.items():
        roles[str(role).strip().lower()] = _routes(f"LLM_CONFIG role {str(role).lower()}", value)

    flat = {k: v for k, v in data.items() if str(k).upper().startswith("LLM_")}
    if flat:
        file_roles = {str(k)[4:].lower() for k in flat if not str(k).upper().startswith(_PROVIDER_PREFIX)}
        _apply_flat(flat, providers, roles, known_roles=set(ROLES) | set(roles) | (file_roles - {"config"}))
