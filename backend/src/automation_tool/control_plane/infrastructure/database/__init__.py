"""Async PostgreSQL infrastructure."""

from .installation_revocation_repository import SqlAlchemyInstallationRevocationRepository
from .platform_session_health_repository import SqlAlchemyPlatformSessionHealthRepository
from .schema import (
    device_credentials,
    device_sessions,
    douyin_search_exposure_definitions,
    execution_attempts,
    installation_registration_challenges,
    installations,
    metadata,
    platform_session_gates,
    platform_session_health,
    task_actions,
    task_commands,
    task_events,
    task_targets,
    tasks,
)
from .session import Database
from .task_command_repository import SqlAlchemyTaskCommandRepository
from .task_discovery_repository import SqlAlchemyTaskDiscoveryRepository
from .task_event_convergence_repository import SqlAlchemyTaskEventConvergenceRepository
from .task_event_stream_repository import SqlAlchemyTaskEventStreamRepository
from .task_target_repository import SqlAlchemyTaskTargetRepository

__all__ = [
    "Database",
    "SqlAlchemyInstallationRevocationRepository",
    "SqlAlchemyPlatformSessionHealthRepository",
    "SqlAlchemyTaskCommandRepository",
    "SqlAlchemyTaskDiscoveryRepository",
    "SqlAlchemyTaskEventConvergenceRepository",
    "SqlAlchemyTaskEventStreamRepository",
    "SqlAlchemyTaskTargetRepository",
    "device_credentials",
    "device_sessions",
    "douyin_search_exposure_definitions",
    "execution_attempts",
    "installation_registration_challenges",
    "installations",
    "metadata",
    "platform_session_gates",
    "platform_session_health",
    "task_actions",
    "task_commands",
    "task_events",
    "task_targets",
    "tasks",
]
