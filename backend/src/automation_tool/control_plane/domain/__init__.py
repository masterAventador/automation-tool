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
from automation_tool.control_plane.domain.task_commands import (
    TERMINAL_TASK_COMMAND_STATUSES,
    TaskCommandResponseType,
    TaskCommandStatus,
    TaskCommandType,
)
from automation_tool.control_plane.domain.task_definitions import (
    DOUYIN_SEARCH_EXPOSURE_TEMPLATE,
    MAX_MESSAGE_TEMPLATE_CHARACTERS,
    MAX_SEARCH_KEYWORD_CHARACTERS,
    MAX_TASK_INTERVAL_SECONDS,
    MAX_TASK_TARGET_LIMIT,
    DouyinSearchExposureAction,
    DouyinSearchExposureDefinition,
    InvalidTaskDefinition,
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
    "DOUYIN_SEARCH_EXPOSURE_TEMPLATE",
    "MAX_DEMO_BOOTSTRAP_LIFETIME",
    "MAX_MESSAGE_TEMPLATE_CHARACTERS",
    "MAX_SAFE_TASK_EVENT_MESSAGE_CHARACTERS",
    "MAX_SEARCH_KEYWORD_CHARACTERS",
    "MAX_TASK_EVENT_SEQUENCE",
    "MAX_TASK_INTERVAL_SECONDS",
    "MAX_TASK_TARGET_LIMIT",
    "TERMINAL_ACTION_STATUSES",
    "TERMINAL_EXECUTION_ATTEMPT_STATUSES",
    "TERMINAL_TASK_COMMAND_STATUSES",
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
    "DouyinSearchExposureAction",
    "DouyinSearchExposureDefinition",
    "ExecutionAttemptId",
    "ExecutionAttemptStatus",
    "ExecutorConnectionId",
    "ExecutorId",
    "InstallationId",
    "InstallationStatus",
    "InvalidDemoBootstrap",
    "InvalidDemoEnvironmentId",
    "InvalidResourceId",
    "InvalidTaskDefinition",
    "InvalidTaskEventModel",
    "InvalidTaskTransition",
    "ResourceId",
    "SafeTaskEventMessage",
    "TaskCommandResponseType",
    "TaskCommandStatus",
    "TaskCommandType",
    "TaskEventType",
    "TaskEventVersion",
    "TaskId",
    "TaskSnapshotProjection",
    "TaskStateMachine",
    "TaskStatus",
]
