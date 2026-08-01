"""Public entry points for the independently deployable Control Plane."""

from importlib import import_module
from typing import Any


def __getattr__(name: str) -> Any:
    if name != "create_app":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = import_module(f"{__name__}.bootstrap.app").create_app
    globals()[name] = value
    return value

__all__ = ["create_app"]
