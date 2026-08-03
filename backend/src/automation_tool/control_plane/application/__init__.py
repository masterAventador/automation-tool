"""Control Plane use cases and dependency ports."""

from importlib import import_module
from typing import Any


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(f"{__name__}.registration")
    value = vars(module)[name]
    globals()[name] = value
    return value


__all__ = ["CHALLENGE_LIFETIME", "InstallationRegistrationService"]
