"""Control Plane domain contracts and errors."""

from automation_tool.control_plane.domain.errors import DependencyUnavailable
from automation_tool.control_plane.domain.ports import DatabaseLifecycle
from automation_tool.control_plane.domain.resource_ids import (
    ActionId,
    ArtifactId,
    ExecutionAttemptId,
    ExecutorId,
    InstallationId,
    InvalidResourceId,
    ResourceId,
    TaskId,
)

__all__ = [
    "ActionId",
    "ArtifactId",
    "DatabaseLifecycle",
    "DependencyUnavailable",
    "ExecutionAttemptId",
    "ExecutorId",
    "InstallationId",
    "InvalidResourceId",
    "ResourceId",
    "TaskId",
]
