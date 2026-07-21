"""Fail-closed deployment configuration for server-side action execution."""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from typing import Final

from pydantic import SecretStr, ValidationError
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
from automation_tool.control_plane.infrastructure.security import (
    Ed25519ActionAuthorizationIssuer,
)
from automation_tool.protocol import ACTION_AUTHORIZATION_MAX_LIFETIME

_BASE64URL_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]+")


class ActionExecutionConfigurationError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Action execution configuration is invalid")


class _ActionExecutionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOMATION_TOOL_", extra="ignore")

    action_authorization_private_key: SecretStr | None = None
    action_minimum_interval_seconds: int | None = None
    action_task_limit: int | None = None
    action_daily_limit: int | None = None
    action_consecutive_failure_threshold: int | None = None


@dataclass(frozen=True, slots=True, repr=False)
class ActionExecutionRuntime:
    service: ActionExecutionOrchestrationService
    issuer: Ed25519ActionAuthorizationIssuer

    def __repr__(self) -> str:
        return "ActionExecutionRuntime(<redacted>)"


def _private_key(value: SecretStr) -> bytes:
    source = value.get_secret_value()
    if _BASE64URL_PATTERN.fullmatch(source) is None:
        raise ActionExecutionConfigurationError
    try:
        decoded = base64.urlsafe_b64decode(source + ("=" * (-len(source) % 4)))
    except (ValueError, binascii.Error):
        raise ActionExecutionConfigurationError from None
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if canonical != source or len(decoded) != 32 or decoded == bytes(32):
        raise ActionExecutionConfigurationError
    return decoded


def action_execution_runtime_from_environment(
    database: Database,
) -> ActionExecutionRuntime | None:
    """Build action execution only when the complete server-owned policy is present."""

    try:
        settings = _ActionExecutionSettings()
    except ValidationError:
        raise ActionExecutionConfigurationError from None
    values = (
        settings.action_authorization_private_key,
        settings.action_minimum_interval_seconds,
        settings.action_task_limit,
        settings.action_daily_limit,
        settings.action_consecutive_failure_threshold,
    )
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise ActionExecutionConfigurationError
    try:
        limits = ActionExecutionLimits(
            minimum_interval_seconds=settings.action_minimum_interval_seconds,  # type: ignore[arg-type]
            task_action_limit=settings.action_task_limit,  # type: ignore[arg-type]
            daily_action_limit=settings.action_daily_limit,  # type: ignore[arg-type]
            consecutive_failure_threshold=(
                settings.action_consecutive_failure_threshold  # type: ignore[arg-type]
            ),
        )
        clock = SystemActionExecutionOrchestrationClock()
        issuer = Ed25519ActionAuthorizationIssuer(
            private_key=_private_key(settings.action_authorization_private_key),  # type: ignore[arg-type]
            clock=clock,
            authorization_lifetime=ACTION_AUTHORIZATION_MAX_LIFETIME,
        )
        service = ActionExecutionOrchestrationService(
            repository=SqlAlchemyActionExecutionOrchestrationRepository(database),
            limits=limits,
            clock=clock,
            command_lifetime=ACTION_AUTHORIZATION_MAX_LIFETIME,
        )
    except Exception:
        raise ActionExecutionConfigurationError from None
    return ActionExecutionRuntime(service=service, issuer=issuer)


__all__ = [
    "ActionExecutionConfigurationError",
    "ActionExecutionRuntime",
    "action_execution_runtime_from_environment",
]
