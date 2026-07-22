"""Durable server-side progression for one confirmed action at a time."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import RFC_4122, UUID, uuid4

from automation_tool.control_plane.domain import (
    MAX_ACTION_RISK_LIMIT,
    MAX_TASK_INTERVAL_SECONDS,
    MAX_TASK_TARGET_LIMIT,
    ExecutorId,
    InstallationId,
)
from automation_tool.protocol import ACTION_AUTHORIZATION_MAX_LIFETIME


class ActionExecutionOrchestrationRejected(ValueError):
    def __init__(self) -> None:
        super().__init__("Action execution orchestration is rejected")


class ActionExecutionOrchestrationUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Action execution orchestration is unavailable")


class ActionExecutionAdvanceKind(StrEnum):
    IDLE = "idle"
    TASK_OFFERED = "task_offered"
    ACTION_ENQUEUED = "action_enqueued"
    TASK_FINALIZED = "task_finalized"
    RATE_LIMITED = "rate_limited"


@dataclass(frozen=True, slots=True)
class ActionExecutionLimits:
    minimum_interval_seconds: int
    task_action_limit: int
    daily_action_limit: int
    consecutive_failure_threshold: int

    def __post_init__(self) -> None:
        if (
            not _bounded_positive_integer(
                self.minimum_interval_seconds,
                MAX_TASK_INTERVAL_SECONDS,
            )
            or not _bounded_positive_integer(self.task_action_limit, MAX_TASK_TARGET_LIMIT)
            or not _bounded_positive_integer(self.daily_action_limit, MAX_ACTION_RISK_LIMIT)
            or not _bounded_positive_integer(
                self.consecutive_failure_threshold,
                MAX_ACTION_RISK_LIMIT,
            )
        ):
            raise ActionExecutionOrchestrationRejected


@dataclass(frozen=True, slots=True)
class PendingActionExecutionAdvance:
    installation_id: InstallationId
    executor_id: ExecutorId
    execution_attempt_id: UUID
    action_id: UUID
    message_id: UUID
    correlation_id: UUID
    limits: ActionExecutionLimits
    requested_at: datetime
    command_deadline_at: datetime

    def __post_init__(self) -> None:
        requested_at = _canonical_utc(self.requested_at)
        deadline_at = _canonical_utc(self.command_deadline_at)
        if (
            type(self.installation_id) is not InstallationId
            or type(self.executor_id) is not ExecutorId
            or not all(
                _uuid_v4(value)
                for value in (
                    self.execution_attempt_id,
                    self.action_id,
                    self.message_id,
                    self.correlation_id,
                )
            )
            or not isinstance(self.limits, ActionExecutionLimits)
            or requested_at is None
            or deadline_at is None
            or not requested_at < deadline_at
            or deadline_at - requested_at > ACTION_AUTHORIZATION_MAX_LIFETIME
        ):
            raise ActionExecutionOrchestrationRejected
        object.__setattr__(self, "requested_at", requested_at)
        object.__setattr__(self, "command_deadline_at", deadline_at)


@dataclass(frozen=True, slots=True)
class ActionExecutionAdvanceResult:
    kind: ActionExecutionAdvanceKind

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ActionExecutionAdvanceKind):
            raise ActionExecutionOrchestrationRejected


@runtime_checkable
class ActionExecutionOrchestrationRepository(Protocol):
    async def advance(
        self,
        pending: PendingActionExecutionAdvance,
    ) -> ActionExecutionAdvanceResult: ...


class ActionExecutionOrchestrationClock(Protocol):
    def now(self) -> datetime: ...


class SystemActionExecutionOrchestrationClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class ActionExecutionOrchestrationService:
    def __init__(
        self,
        *,
        repository: ActionExecutionOrchestrationRepository,
        limits: ActionExecutionLimits,
        clock: ActionExecutionOrchestrationClock | None = None,
        id_source: Callable[[], object] = uuid4,
        command_lifetime: timedelta = ACTION_AUTHORIZATION_MAX_LIFETIME,
    ) -> None:
        if (
            not isinstance(repository, ActionExecutionOrchestrationRepository)
            or not isinstance(limits, ActionExecutionLimits)
            or not callable(id_source)
            or type(command_lifetime) is not timedelta
            or command_lifetime.microseconds != 0
            or not timedelta(seconds=1) <= command_lifetime <= ACTION_AUTHORIZATION_MAX_LIFETIME
        ):
            raise ActionExecutionOrchestrationRejected
        self._repository = repository
        self._limits = limits
        self._clock = clock or SystemActionExecutionOrchestrationClock()
        self._id_source = id_source
        self._command_lifetime = command_lifetime

    def _now(self) -> datetime:
        try:
            value = _canonical_utc(self._clock.now())
        except Exception:
            value = None
        if value is None:
            raise ActionExecutionOrchestrationUnavailable
        return value

    def _new_id(self) -> UUID:
        try:
            value = self._id_source()
        except Exception:
            raise ActionExecutionOrchestrationUnavailable from None
        if not isinstance(value, UUID) or not _uuid_v4(value):
            raise ActionExecutionOrchestrationUnavailable
        return value

    async def advance(
        self,
        installation_id: InstallationId,
        executor_id: ExecutorId,
    ) -> ActionExecutionAdvanceResult:
        if type(installation_id) is not InstallationId or type(executor_id) is not ExecutorId:
            raise ActionExecutionOrchestrationRejected
        now = self._now()
        pending = PendingActionExecutionAdvance(
            installation_id=installation_id,
            executor_id=executor_id,
            execution_attempt_id=self._new_id(),
            action_id=self._new_id(),
            message_id=self._new_id(),
            correlation_id=self._new_id(),
            limits=self._limits,
            requested_at=now,
            command_deadline_at=now + self._command_lifetime,
        )
        try:
            result = await self._repository.advance(pending)
        except ActionExecutionOrchestrationRejected:
            raise
        except ActionExecutionOrchestrationUnavailable:
            raise
        except Exception:
            raise ActionExecutionOrchestrationUnavailable from None
        if not isinstance(result, ActionExecutionAdvanceResult):
            raise ActionExecutionOrchestrationRejected
        return result


def _bounded_positive_integer(value: object, maximum: int) -> bool:
    return type(value) is int and 1 <= value <= maximum


def _canonical_utc(value: object) -> datetime | None:
    if type(value) is not datetime or value.tzinfo is None:
        return None
    try:
        if value.utcoffset() != timedelta(0):
            return None
        return value.astimezone(UTC)
    except Exception:
        return None


def _uuid_v4(value: object) -> bool:
    return isinstance(value, UUID) and value.version == 4 and value.variant == RFC_4122


__all__ = [
    "ActionExecutionAdvanceKind",
    "ActionExecutionAdvanceResult",
    "ActionExecutionLimits",
    "ActionExecutionOrchestrationRejected",
    "ActionExecutionOrchestrationRepository",
    "ActionExecutionOrchestrationService",
    "ActionExecutionOrchestrationUnavailable",
    "PendingActionExecutionAdvance",
    "SystemActionExecutionOrchestrationClock",
]
