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
from automation_tool.control_plane.domain.execution_models import (
    TERMINAL_ACTION_STATUSES,
    TERMINAL_EXECUTION_ATTEMPT_STATUSES,
    ActionOutcome,
    ActionStatus,
    ExecutionAttemptStatus,
)
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
from automation_tool.control_plane.domain.task_events import (
    MAX_SAFE_TASK_EVENT_MESSAGE_CHARACTERS,
    MAX_TASK_EVENT_SEQUENCE,
    InvalidTaskEventModel,
    SafeTaskEventMessage,
    TaskEventType,
    TaskEventVersion,
    TaskSnapshotProjection,
)
from automation_tool.control_plane.domain.task_state_machine import (
    InvalidTaskTransition,
    TaskStateMachine,
    TaskStatus,
)

__all__ = [
    "MAX_DEMO_BOOTSTRAP_LIFETIME",
    "MAX_SAFE_TASK_EVENT_MESSAGE_CHARACTERS",
    "MAX_TASK_EVENT_SEQUENCE",
    "TERMINAL_ACTION_STATUSES",
    "TERMINAL_EXECUTION_ATTEMPT_STATUSES",
    "ActionId",
    "ActionOutcome",
    "ActionStatus",
    "ArtifactId",
    "BootstrapAuthorizationDenied",
    "BootstrapDenialReason",
    "BootstrapPurpose",
    "DatabaseLifecycle",
    "DemoBootstrapGrant",
    "DemoEnvironmentId",
    "DependencyUnavailable",
    "ExecutionAttemptId",
    "ExecutionAttemptStatus",
    "ExecutorConnectionId",
    "ExecutorId",
    "InstallationId",
    "InstallationStatus",
    "InvalidDemoBootstrap",
    "InvalidDemoEnvironmentId",
    "InvalidResourceId",
    "InvalidTaskEventModel",
    "InvalidTaskTransition",
    "ResourceId",
    "SafeTaskEventMessage",
    "TaskEventType",
    "TaskEventVersion",
    "TaskId",
    "TaskSnapshotProjection",
    "TaskStateMachine",
    "TaskStatus",
]
