"""Control Plane domain contracts and errors."""

from automation_tool.control_plane.domain.demo_bootstrap import (
    MAX_DEMO_BOOTSTRAP_LIFETIME,
    BootstrapAuthorizationDenied,
    BootstrapDenialReason,
    BootstrapPurpose,
    DemoBootstrapGrant,
    DemoEnvironmentId,
    InvalidDemoBootstrap,
    InvalidDemoEnvironmentId,
)
from automation_tool.control_plane.domain.errors import DependencyUnavailable
from automation_tool.control_plane.domain.installations import InstallationStatus
from automation_tool.control_plane.domain.ports import DatabaseLifecycle
from automation_tool.control_plane.domain.resource_ids import (
    ActionId,
    ArtifactId,
    ExecutionAttemptId,
    ExecutorConnectionId,
    ExecutorId,
    InstallationId,
    InvalidResourceId,
    ResourceId,
    TaskId,
)
from automation_tool.control_plane.domain.task_state_machine import (
    InvalidTaskTransition,
    TaskStateMachine,
    TaskStatus,
)

__all__ = [
    "MAX_DEMO_BOOTSTRAP_LIFETIME",
    "ActionId",
    "ArtifactId",
    "BootstrapAuthorizationDenied",
    "BootstrapDenialReason",
    "BootstrapPurpose",
    "DatabaseLifecycle",
    "DemoBootstrapGrant",
    "DemoEnvironmentId",
    "DependencyUnavailable",
    "ExecutionAttemptId",
    "ExecutorConnectionId",
    "ExecutorId",
    "InstallationId",
    "InstallationStatus",
    "InvalidDemoBootstrap",
    "InvalidDemoEnvironmentId",
    "InvalidResourceId",
    "InvalidTaskTransition",
    "ResourceId",
    "TaskId",
    "TaskStateMachine",
    "TaskStatus",
]
