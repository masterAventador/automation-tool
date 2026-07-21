"""Async PostgreSQL infrastructure."""

from .action_risk_authorization_repository import (
    SqlAlchemyActionRiskAuthorizationRepository,
)
from .installation_revocation_repository import SqlAlchemyInstallationRevocationRepository
from .platform_session_health_repository import SqlAlchemyPlatformSessionHealthRepository
from .schema import (
    action_failure_circuits,
    action_risk_authorizations,
    action_risk_results,
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
    task_target_confirmations,
    task_target_exclusions,
    task_targets,
    tasks,
)
from .session import Database
from .task_command_repository import SqlAlchemyTaskCommandRepository
from .task_discovery_repository import SqlAlchemyTaskDiscoveryRepository
from .task_event_convergence_repository import SqlAlchemyTaskEventConvergenceRepository
from .task_event_stream_repository import SqlAlchemyTaskEventStreamRepository
from .task_target_repository import SqlAlchemyTaskTargetRepository
from .task_target_result_repository import SqlAlchemyTaskTargetResultRepository

__all__ = [
    "Database",
    "SqlAlchemyActionRiskAuthorizationRepository",
    "SqlAlchemyInstallationRevocationRepository",
    "SqlAlchemyPlatformSessionHealthRepository",
    "SqlAlchemyTaskCommandRepository",
    "SqlAlchemyTaskDiscoveryRepository",
    "SqlAlchemyTaskEventConvergenceRepository",
    "SqlAlchemyTaskEventStreamRepository",
    "SqlAlchemyTaskTargetRepository",
    "SqlAlchemyTaskTargetResultRepository",
    "action_failure_circuits",
    "action_risk_authorizations",
    "action_risk_results",
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
    "task_target_confirmations",
    "task_target_exclusions",
    "task_targets",
    "tasks",
]
