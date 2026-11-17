"""The one error type of the model layer."""

from __future__ import annotations


class LLMError(RuntimeError):
    """A configuration problem, or a call no route could answer.

    The message names providers, key numbers and status codes only. It never carries a key, a prompt
    or a response body, so it is safe to log and to show on an admin page.
    """
