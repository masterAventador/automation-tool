"""Local Executor components that depend only on the shared protocol."""

from importlib import import_module
from typing import Any

# The FakeExecutor doubles are deliberately absent from this package's exports.
# `automation-tool-executor.spec` declares `excludes=[]`, so what ships is decided
# by the import graph alone: re-exporting them here would let any entry point that
# imports the package — rather than the module it needs — carry a
# protocol-replaying stand-in into a customer's installation. They remain
# importable as `automation_tool.executor.fake` and `...fake_client`, which is how
# the tests that need them already import them. Guarded by
# `tests/unit/executor/test_shipped_package_boundary.py`.
_PUBLIC_MODULES = ("authentication", "bootstrap", "command_processor", "runtime")


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    for module_name in _PUBLIC_MODULES:
        module = import_module(f"{__name__}.{module_name}")
        if name in vars(module):
            value = vars(module)[name]
            globals()[name] = value
            return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "ExecutorBootstrap",
    "ExecutorBootstrapRejected",
    "ExecutorCommandProcessor",
    "ExecutorCommandRejected",
    "ExecutorProcessRejected",
    "ExecutorProcessReporter",
    "LocalExecutorProcess",
    "LocalSessionAuthenticationRejected",
    "LocalSessionAuthenticator",
    "RuntimeMetadata",
    "read_executor_bootstrap",
    "require_local_session_token",
]
