"""The model layer: every model call of the application goes through `LLM`, and nowhere else.

    from llm import LLM, Registry
    llm = LLM(Registry.from_env())
    if llm.has("decide"):
        label = llm.choose("decide", "ادفع المولدة", ["pay_bill", "transfer", "balance"])

Which model answers which role is configuration (see docs/MODELS.md): swap a model in one line, keep
a backup provider, or run everything on a local open-weight model. Standard library and httpx only.
"""

from llm.client import LLM
from llm.errors import LLMError
from llm.registry import JSON_MODES, ROLES, Provider, Registry, Route
from llm.schema import parse_json, validate

__all__ = ["LLM", "LLMError", "Registry", "Route", "Provider", "ROLES", "JSON_MODES", "validate", "parse_json"]
