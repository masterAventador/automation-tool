"""Local Executor components that depend only on the shared protocol."""

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

__all__ = [
    "FakeExecutorClient",
    "FakeExecutorClientConfiguration",
    "FakeExecutorEngine",
    "FakeExecutorRejected",
    "FakeExecutorScenario",
    "FakeExecutorTransportRejected",
]
