"""One-sentence brand-motion authoring, hosted by the Local Executor.

The public entry symbols stay lazy on purpose.  Lightweight consumers such as
the smart-edit worker import authoring workspace helpers from this package, and
must not pay for (or freeze) the browser-backed motion authoring agent unless a
motion-authoring entry point is actually requested.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "MAX_REQUEST_BYTES",
    "SCHEMA_VERSION",
    "MotionAuthoringEntryRejected",
    "run_motion_authoring_entry",
    "serve_one_motion_authoring_request",
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)
    entry = import_module("automation_tool.executor.motion_authoring.entry")
    return getattr(entry, name)
