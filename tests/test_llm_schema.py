"""The schema checker: what the code accepts from a model, and what it turns away."""

import pytest

from llm.schema import describe, is_strict, is_valid, parse_json, strip_think, validate

INTENT = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["pay_bill", "transfer", "balance"]},
        "payee": {"type": ["string", "null"], "maxLength": 40},
        "count": {"type": "integer", "minimum": 1, "maximum": 3},
        "tags": {"type": "array", "items": {"type": "string", "minLength": 1}, "maxItems": 2},
    },
    "required": ["action", "payee"],
    "additionalProperties": False,
}


# ---- types ------------------------------------------------------------------------------------

@pytest.mark.parametrize("type_name,value,ok", [
    ("string", "x", True), ("string", 1, False), ("string", None, False),
    ("integer", 5, True), ("integer", 5.0, False), ("integer", True, False), ("integer", "5", False),
    ("number", 5, True), ("number", 5.5, True), ("number", False, False), ("number", float("nan"), False),
    ("number", float("inf"), False),
    ("boolean", True, True), ("boolean", 0, False), ("boolean", "true", False),
    ("null", None, True), ("null", 0, False), ("null", "", False),
    ("array", [], True), ("array", {}, False),
    ("object", {}, True), ("object", [], False),
])
def test_type(type_name, value, ok):
    assert is_valid(value, {"type": type_name}) is ok


def test_an_unknown_type_name_matches_nothing():
    assert validate("x", {"type": "strnig"}) != []


def test_a_type_list_makes_a_field_nullable():
    schema = {"type": ["string", "null"]}
    assert is_valid(None, schema) and is_valid("x", schema)
    assert not is_valid(3, schema)


# ---- objects ----------------------------------------------------------------------------------

def test_a_good_answer_passes():
    assert validate({"action": "transfer", "payee": "هادي", "count": 2, "tags": ["a"]}, INTENT) == []
    assert validate({"action": "balance", "payee": None}, INTENT) == []


def test_a_missing_required_field_is_named():
    errors = validate({"action": "balance"}, INTENT)
    assert errors == ["$.payee: required, missing"]


def test_a_value_outside_the_enum_is_refused():
    assert validate({"action": "pay_everything", "payee": None}, INTENT) == ["$.action: not one of the allowed values"]


def test_an_extra_property_is_refused_when_the_schema_closes_the_list():
    errors = validate({"action": "balance", "payee": None, "pin": "1234"}, INTENT)
    assert len(errors) == 1 and "does not allow" in errors[0]


def test_an_extra_property_is_fine_when_the_schema_leaves_the_list_open():
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    assert validate({"a": "x", "b": 1}, schema) == []


def test_additional_properties_can_be_a_schema():
    schema = {"type": "object", "properties": {}, "additionalProperties": {"type": "integer"}}
    assert validate({"a": 1, "b": 2}, schema) == []
    assert validate({"a": 1, "b": "two"}, schema) != []


def test_nested_errors_carry_their_place():
    schema = {"type": "object", "properties": {"rows": {"type": "array", "items": INTENT}}}
    errors = validate({"rows": [{"action": "balance", "payee": None}, {"action": "balance", "payee": 7}]}, schema)
    assert errors == ["$.rows[1].payee: expected string or null"]


# ---- limits -----------------------------------------------------------------------------------

@pytest.mark.parametrize("count,ok", [(0, False), (1, True), (3, True), (4, False)])
def test_minimum_and_maximum(count, ok):
    assert is_valid({"action": "balance", "payee": None, "count": count}, INTENT) is ok


def test_exclusive_limits():
    schema = {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1}
    assert is_valid(0.5, schema) and not is_valid(0, schema) and not is_valid(1, schema)


def test_string_lengths():
    assert not is_valid({"action": "balance", "payee": "x" * 41}, INTENT)
    assert not is_valid({"action": "balance", "payee": None, "tags": [""]}, INTENT)
    assert is_valid({"action": "balance", "payee": "x" * 40}, INTENT)


def test_array_sizes():
    assert not is_valid({"action": "balance", "payee": None, "tags": ["a", "b", "c"]}, INTENT)
    assert not is_valid([], {"type": "array", "minItems": 1})


def test_enum_compares_the_json_way():
    assert is_valid(None, {"enum": ["a", None]})
    assert not is_valid(True, {"enum": [1]})
    assert not is_valid("1", {"enum": [1]})
    assert is_valid(1.0, {"enum": [1]})


def test_const_and_any_of():
    assert is_valid("v1", {"const": "v1"}) and not is_valid("v2", {"const": "v1"})
    schema = {"anyOf": [{"type": "string"}, {"type": "integer", "minimum": 0}]}
    assert is_valid("x", schema) and is_valid(3, schema)
    assert not is_valid(-1, schema) and not is_valid(None, schema)


def test_errors_never_repeat_the_answer():
    secret = "the-model-wrote-this-0770-000-0000"
    answer = {"action": secret, "payee": {"oops": secret}, secret: secret, "count": 99, "tags": [secret, 5]}
    errors = validate(answer, INTENT)
    assert errors
    assert all(secret not in error for error in errors)


# ---- strict schemas ---------------------------------------------------------------------------

def test_is_strict():
    assert not is_strict(INTENT)                       # count and tags are optional
    closed = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"],
              "additionalProperties": False}
    assert is_strict(closed)
    assert not is_strict({**closed, "additionalProperties": True})
    assert not is_strict({"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]})
    assert is_strict({"type": "array", "items": closed})
    assert not is_strict({"type": "array", "items": INTENT})
    assert not is_strict({"type": "object", "properties": {"inner": INTENT}, "required": ["inner"],
                          "additionalProperties": False})


def test_describe_is_compact_and_keeps_arabic():
    text = describe({"enum": ["دفع"], "type": "string"})
    assert " " not in text and "دفع" in text


# ---- reading JSON out of an answer --------------------------------------------------------------

@pytest.mark.parametrize("text", [
    '{"a": 1}',
    '  {"a": 1}\n',
    '```json\n{"a": 1}\n```',
    '```\n{"a": 1}\n```',
    'Here is the answer:\n```json\n{"a": 1}\n```\nHope it helps.',
    'Sure. {"a": 1} is what you asked for.',
    '<think>the user wants {"b": 2}</think>{"a": 1}',
    'x } { broken {"a": 1}',
])
def test_parse_json_finds_the_object(text):
    assert parse_json(text) == {"a": 1}


def test_parse_json_reads_an_array_and_arabic():
    assert parse_json('[1, 2]') == [1, 2]
    assert parse_json('{"name": "هادي"}') == {"name": "هادي"}


@pytest.mark.parametrize("text", ["", "no json here", "{broken", '{"a": NaN}', '{"a": Infinity}', None, 5])
def test_parse_json_refuses_what_is_not_json(text):
    with pytest.raises(ValueError) as caught:
        parse_json(text)
    assert str(caught.value) == "no JSON in the answer"


def test_strip_think():
    assert strip_think("<think>hmm\nhmm</think>\nالجواب") == "\nالجواب"
    assert strip_think("plain") == "plain"
