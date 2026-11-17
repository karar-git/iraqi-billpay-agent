"""Which core the orchestrator talks to, chosen at run time.

    BILLPAY_CORE="package.module:ClassName"   the class to use (instantiated with no arguments)
    BILLPAY_CORE_PATH="/some/folder"           optional: a folder added to sys.path first

With BILLPAY_CORE unset, the open rule-based StubCore is used. A BILLPAY_CORE that cannot be loaded
is an error, never a silent switch to another core: a payment agent must run the core it was told to.
"""

from __future__ import annotations

import importlib
import os
import sys
from typing import Any

from core.interface import Core

ENV_CORE = "BILLPAY_CORE"
ENV_PATH = "BILLPAY_CORE_PATH"


class CoreLoadError(RuntimeError):
    """BILLPAY_CORE is set but does not name a usable core."""


def _class_from(spec: str) -> type:
    module_name, sep, class_name = spec.strip().partition(":")
    if not module_name or not sep or not class_name:
        raise CoreLoadError(f"{ENV_CORE} must look like 'package.module:ClassName'")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise CoreLoadError(f"{ENV_CORE}: cannot import its module ({exc.__class__.__name__})") from exc
    try:
        return getattr(module, class_name)
    except AttributeError as exc:
        raise CoreLoadError(f"{ENV_CORE}: the module has no {class_name}") from exc


def load_core(spec: str | None = None, path: str | None = None) -> Any:
    """A ready core: `spec` (or $BILLPAY_CORE) as module:Class, else core.stub.StubCore."""
    spec = spec if spec is not None else os.environ.get(ENV_CORE, "")
    path = path if path is not None else os.environ.get(ENV_PATH, "")
    if not spec.strip():
        from core.stub import StubCore

        return StubCore()
    if path and path not in sys.path:
        sys.path.insert(0, path)
    cls = _class_from(spec)
    try:
        core = cls()
    except Exception as exc:  # a core that cannot start is a configuration error, reported as one
        raise CoreLoadError(f"{ENV_CORE}: the core could not start ({exc.__class__.__name__}: {exc})") from exc
    if not isinstance(core, Core):
        raise CoreLoadError(f"{ENV_CORE}: the class does not implement understand() and phrase()")
    return core


def core_name(core: Any) -> str:
    """A short label for reports: the class name only."""
    return type(core).__name__
