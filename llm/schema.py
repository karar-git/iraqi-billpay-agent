"""A small JSON Schema checker: the model answers, this code decides whether the answer counts.

Supported keywords: type (a name or a list of names, so ["string", "null"] is a nullable string),
properties, required, additionalProperties (false, true or a schema), enum, const, items, anyOf,
minimum / maximum, exclusiveMinimum / exclusiveMaximum, minLength / maxLength, minItems / maxItems.
Any other keyword is ignored. An unknown type name matches nothing, so a typo in a schema fails
closed instead of letting everything through.

Stricter than the standard in two places, on purpose:
  - "integer" accepts a whole number written as an integer only (5, never 5.0 and never true);
  - "number" refuses NaN and Infinity.

Error texts name a place in the schema and the rule that broke. They never repeat the answer.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

_FENCE = re.compile(r"```[A-Za-z0-9_-]*[ \t]*\r?\n?(.*?)```", re.DOTALL)
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _is_type(value: Any, name: Any) -> bool:
    if name == "null":
        return value is None
    if name == "boolean":
        return isinstance(value, bool)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        return isinstance(value, int) or math.isfinite(value)
    if name == "string":
        return isinstance(value, str)
    if name == "array":
        return isinstance(value, list)
    if name == "object":
        return isinstance(value, dict)
    return False


def _same(a: Any, b: Any) -> bool:
    """Equality the way JSON means it: true is not 1, and "1" is not 1."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    return type(a) is type(b) and a == b


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate(value: Any, schema: Any, path: str = "$") -> list[str]:
    """Every way `value` breaks `schema`. An empty list means the value is valid."""
    errors: list[str] = []
    _check(value, schema, path, errors)
    return errors


def is_valid(value: Any, schema: Any) -> bool:
    return not validate(value, schema)


def _check(value: Any, schema: Any, path: str, errors: list[str]) -> None:
    if schema is True:
        return
    if schema is False:
        errors.append(f"{path}: not allowed")
        return
    if not isinstance(schema, dict):
        errors.append(f"{path}: the schema is not an object")
        return

    wanted = schema.get("type")
    if wanted is not None:
        names = wanted if isinstance(wanted, list) else [wanted]
        if not any(_is_type(value, name) for name in names):
            errors.append(f"{path}: expected {' or '.join(str(n) for n in names)}")
            return

    if "enum" in schema:
        options = schema["enum"]
        if not isinstance(options, list) or not any(_same(value, option) for option in options):
            errors.append(f"{path}: not one of the allowed values")
    if "const" in schema and not _same(value, schema["const"]):
        errors.append(f"{path}: not the fixed value")
    if "anyOf" in schema:
        branches = schema["anyOf"]
        if not isinstance(branches, list) or not any(not validate(value, b, path) for b in branches):
            errors.append(f"{path}: fits none of the anyOf branches")

    if isinstance(value, str):
        low, high = schema.get("minLength"), schema.get("maxLength")
        if _is_number(low) and len(value) < low:
            errors.append(f"{path}: shorter than minLength {low}")
        if _is_number(high) and len(value) > high:
            errors.append(f"{path}: longer than maxLength {high}")

    elif _is_number(value):
        low, high = schema.get("minimum"), schema.get("maximum")
        if _is_number(low) and value < low:
            errors.append(f"{path}: below minimum {low}")
        if _is_number(high) and value > high:
            errors.append(f"{path}: above maximum {high}")
        low, high = schema.get("exclusiveMinimum"), schema.get("exclusiveMaximum")
        if _is_number(low) and value <= low:
            errors.append(f"{path}: not above exclusiveMinimum {low}")
        if _is_number(high) and value >= high:
            errors.append(f"{path}: not below exclusiveMaximum {high}")

    elif isinstance(value, list):
        low, high = schema.get("minItems"), schema.get("maxItems")
        if _is_number(low) and len(value) < low:
            errors.append(f"{path}: fewer than minItems {low}")
        if _is_number(high) and len(value) > high:
            errors.append(f"{path}: more than maxItems {high}")
        if "items" in schema:
            for index, item in enumerate(value):
                _check(item, schema["items"], f"{path}[{index}]", errors)

    elif isinstance(value, dict):
        properties = schema.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        required = schema.get("required")
        for name in required if isinstance(required, list) else []:
            if name not in value:
                errors.append(f"{path}.{name}: required, missing")
        for name, sub in properties.items():
            if name in value:
                _check(value[name], sub, f"{path}.{name}", errors)
        extra = schema.get("additionalProperties", True)
        if extra is not True:
            unknown = [name for name in value if name not in properties]
            if extra is False:
                if unknown:
                    # The name of an unknown property is the model's own text: count it, do not repeat it.
                    errors.append(f"{path}: {len(unknown)} property(ies) the schema does not allow")
            else:
                for position, name in enumerate(unknown):
                    _check(value[name], extra, f"{path}.<extra {position}>", errors)


def is_strict(schema: Any) -> bool:
    """True when every object in the schema closes its property list and requires all of it.

    Hosted structured output in "strict" mode accepts only such schemas, so the flag is sent only when
    this holds. Either way the answer is checked again here, in code.
    """
    if not isinstance(schema, dict):
        return True
    wanted = schema.get("type")
    names = wanted if isinstance(wanted, list) else [wanted]
    properties = schema.get("properties")
    if "object" in names or isinstance(properties, dict):
        properties = properties if isinstance(properties, dict) else {}
        if schema.get("additionalProperties", True) is not False:
            return False
        required = schema.get("required")
        if set(required if isinstance(required, list) else []) != set(properties):
            return False
        if not all(is_strict(sub) for sub in properties.values()):
            return False
    if "items" in schema and not is_strict(schema["items"]):
        return False
    branches = schema.get("anyOf")
    if isinstance(branches, list) and not all(is_strict(b) for b in branches):
        return False
    return True


def describe(schema: Any) -> str:
    """The schema as compact JSON text, for a provider that takes it in the prompt."""
    return json.dumps(schema, ensure_ascii=False, separators=(",", ":"))


def strip_think(text: str) -> str:
    """Drop the <think>...</think> block some open-weight reasoning models print before their answer."""
    return _THINK.sub("", text) if "<" in text else text


def _no_constant(_name: str) -> Any:
    raise ValueError("not JSON")


def _loads(text: str) -> Any:
    return json.loads(text, parse_constant=_no_constant)


def parse_json(text: str) -> Any:
    """The JSON value inside a model's answer.

    Reads plain JSON, JSON inside a code fence, and JSON with words around it (the first value that
    parses, scanning for a { or a [). Raises ValueError with a fixed message that never repeats the text.
    """
    if not isinstance(text, str):
        raise ValueError("no JSON in the answer")
    cleaned = strip_think(text).strip()
    candidates = [cleaned]
    candidates.extend(match.strip() for match in _FENCE.findall(cleaned))
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return _loads(candidate)
        except ValueError:
            pass
    decoder = json.JSONDecoder(parse_constant=_no_constant)
    for index, char in enumerate(cleaned):
        if char not in "{[":
            continue
        try:
            value, _end = decoder.raw_decode(cleaned, index)
        except ValueError:
            continue
        return value
    raise ValueError("no JSON in the answer")
