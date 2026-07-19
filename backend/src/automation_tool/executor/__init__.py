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
from automation_tool.executor.fake import (
    FakeExecutorEngine,
    FakeExecutorRejected,
    FakeExecutorScenario,
)
from automation_tool.executor.fake_client import (
    FakeExecutorClient,
    FakeExecutorClientConfiguration,
    FakeExecutorTransportRejected,
)
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
    "FakeExecutorClient",
    "FakeExecutorClientConfiguration",
    "FakeExecutorEngine",
    "FakeExecutorRejected",
    "FakeExecutorScenario",
    "FakeExecutorTransportRejected",
    "LocalExecutorProcess",
    "LocalSessionAuthenticationRejected",
    "LocalSessionAuthenticator",
    "RuntimeMetadata",
    "read_executor_bootstrap",
    "require_local_session_token",
]
