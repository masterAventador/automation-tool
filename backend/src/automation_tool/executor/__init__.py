"""Local Executor components that depend only on the shared protocol."""

from automation_tool.executor.authentication import (
    LocalSessionAuthenticationRejected,
    LocalSessionAuthenticator,
    require_local_session_token,
)
from automation_tool.executor.bootstrap import (
    ExecutorBootstrap,
    ExecutorBootstrapRejected,
    read_executor_bootstrap,
)
from automation_tool.executor.command_processor import (
    ExecutorCommandProcessor,
    ExecutorCommandRejected,
)

# The FakeExecutor doubles are deliberately absent from this package's exports.
# `automation-tool-executor.spec` declares `excludes=[]`, so what ships is decided
# by the import graph alone: re-exporting them here would let any entry point that
# imports the package — rather than the module it needs — carry a
# protocol-replaying stand-in into a customer's installation. They remain
# importable as `automation_tool.executor.fake` and `...fake_client`, which is how
# the tests that need them already import them. Guarded by
# `tests/unit/executor/test_shipped_package_boundary.py`.
from automation_tool.executor.runtime import (
    ExecutorProcessRejected,
    ExecutorProcessReporter,
    LocalExecutorProcess,
    RuntimeMetadata,
)

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
