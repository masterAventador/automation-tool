"""Deployment configuration for server-side action execution."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from automation_tool.control_plane.application.action_execution_orchestration import (
    ActionExecutionLimits,
    ActionExecutionOrchestrationService,
    SystemActionExecutionOrchestrationClock,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyActionExecutionOrchestrationRepository,
)
from automation_tool.protocol import ACTION_AUTHORIZATION_MAX_LIFETIME


class ActionExecutionConfigurationError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Action execution configuration is invalid")


class _ActionExecutionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOMATION_TOOL_", extra="ignore")

    action_minimum_interval_seconds: int = 5
    action_task_limit: int = 20
    action_daily_limit: int = 100
    action_consecutive_failure_threshold: int = 3


@dataclass(frozen=True, slots=True, repr=False)
class ActionExecutionRuntime:
    service: ActionExecutionOrchestrationService

    def __repr__(self) -> str:
        return "ActionExecutionRuntime(<redacted>)"


def action_execution_runtime_from_environment(
    database: Database,
) -> ActionExecutionRuntime:
    """Build action execution with environment-tunable limits (defaults apply)."""

    try:
        settings = _ActionExecutionSettings()
        limits = ActionExecutionLimits(
            minimum_interval_seconds=settings.action_minimum_interval_seconds,
            task_action_limit=settings.action_task_limit,
            daily_action_limit=settings.action_daily_limit,
            consecutive_failure_threshold=settings.action_consecutive_failure_threshold,
        )
        clock = SystemActionExecutionOrchestrationClock()
        service = ActionExecutionOrchestrationService(
            repository=SqlAlchemyActionExecutionOrchestrationRepository(database),
            limits=limits,
            clock=clock,
            command_lifetime=ACTION_AUTHORIZATION_MAX_LIFETIME,
        )
    except (ValidationError, Exception):
        raise ActionExecutionConfigurationError from None
    return ActionExecutionRuntime(service=service)


__all__ = [
    "ActionExecutionConfigurationError",
    "ActionExecutionRuntime",
    "action_execution_runtime_from_environment",
]
