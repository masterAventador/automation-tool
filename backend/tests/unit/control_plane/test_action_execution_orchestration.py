"""Application boundary for durable one-at-a-time action orchestration."""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from uuid import UUID, uuid1

import pytest

from automation_tool.control_plane.application.action_execution_orchestration import (
    ActionExecutionAdvanceKind,
    ActionExecutionAdvanceResult,
    ActionExecutionLimits,
    ActionExecutionOrchestrationRejected,
    ActionExecutionOrchestrationService,
    ActionExecutionOrchestrationUnavailable,
    PendingActionExecutionAdvance,
    SystemActionExecutionOrchestrationClock,
)
from automation_tool.control_plane.domain import (
    MAX_ACTION_RISK_LIMIT,
    MAX_TASK_INTERVAL_SECONDS,
    MAX_TASK_TARGET_LIMIT,
    ExecutorId,
    InstallationId,
)
from automation_tool.protocol import ACTION_AUTHORIZATION_MAX_LIFETIME

NOW = datetime(2026, 7, 21, 6, 30, tzinfo=UTC)
INSTALLATION_ID = InstallationId.parse("123e4567-e89b-42d3-a456-426614174001")
EXECUTOR_ID = ExecutorId.parse("223e4567-e89b-42d3-a456-426614174001")
IDS = tuple(
    UUID(value)
    for value in (
        "323e4567-e89b-42d3-a456-426614174001",
        "423e4567-e89b-42d3-a456-426614174001",
        "523e4567-e89b-42d3-a456-426614174001",
        "623e4567-e89b-42d3-a456-426614174001",
    )
)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class FixedIds:
    def __init__(self) -> None:
        self._values = iter(IDS)

    def __call__(self) -> UUID:
        return next(self._values)


class RecordingRepository:
    def __init__(self, result: ActionExecutionAdvanceResult | Exception) -> None:
        self.result = result
        self.pending: PendingActionExecutionAdvance | None = None

    async def advance(
        self,
        pending: PendingActionExecutionAdvance,
    ) -> ActionExecutionAdvanceResult:
        self.pending = pending
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def limits() -> ActionExecutionLimits:
    return ActionExecutionLimits(
        minimum_interval_seconds=5,
        task_action_limit=20,
        daily_action_limit=100,
        consecutive_failure_threshold=3,
    )


def pending() -> PendingActionExecutionAdvance:
    return PendingActionExecutionAdvance(
        installation_id=INSTALLATION_ID,
        executor_id=EXECUTOR_ID,
        execution_attempt_id=IDS[0],
        action_id=IDS[1],
        message_id=IDS[2],
        correlation_id=IDS[3],
        limits=limits(),
        requested_at=NOW,
        command_deadline_at=NOW + timedelta(minutes=5),
    )


@pytest.mark.asyncio
async def test_service_builds_one_bounded_advance_request_from_server_time() -> None:
    expected = ActionExecutionAdvanceResult(kind=ActionExecutionAdvanceKind.IDLE)
    repository = RecordingRepository(expected)
    service = ActionExecutionOrchestrationService(
        repository=repository,
        limits=limits(),
        clock=FixedClock(),
        id_source=FixedIds(),
        command_lifetime=timedelta(minutes=5),
    )

    assert await service.advance(INSTALLATION_ID, EXECUTOR_ID) == expected
    assert repository.pending == pending()


@pytest.mark.asyncio
async def test_service_collapses_repository_failure_without_reflecting_it() -> None:
    service = ActionExecutionOrchestrationService(
        repository=RecordingRepository(RuntimeError("private database detail")),
        limits=limits(),
        clock=FixedClock(),
        id_source=FixedIds(),
    )

    with pytest.raises(ActionExecutionOrchestrationUnavailable) as captured:
        await service.advance(INSTALLATION_ID, EXECUTOR_ID)
    assert "private" not in str(captured.value)


def test_limits_and_service_configuration_fail_closed() -> None:
    valid_limits = {
        field.name: getattr(limits(), field.name) for field in fields(ActionExecutionLimits)
    }
    invalid_limits: tuple[dict[str, object], ...] = (
        {"minimum_interval_seconds": True},
        {"minimum_interval_seconds": 0},
        {"minimum_interval_seconds": MAX_TASK_INTERVAL_SECONDS + 1},
        {"task_action_limit": 0},
        {"task_action_limit": MAX_TASK_TARGET_LIMIT + 1},
        {"daily_action_limit": 0},
        {"daily_action_limit": MAX_ACTION_RISK_LIMIT + 1},
        {"consecutive_failure_threshold": 0},
        {"consecutive_failure_threshold": MAX_ACTION_RISK_LIMIT + 1},
    )
    for overrides in invalid_limits:
        with pytest.raises(ActionExecutionOrchestrationRejected):
            ActionExecutionLimits(**{**valid_limits, **overrides})

    valid_repository = RecordingRepository(
        ActionExecutionAdvanceResult(kind=ActionExecutionAdvanceKind.IDLE)
    )
    invalid_service_arguments: tuple[dict[str, object], ...] = (
        {"repository": object()},
        {"limits": object()},
        {"id_source": object()},
        {"command_lifetime": "five minutes"},
        {"command_lifetime": timedelta(microseconds=1)},
        {"command_lifetime": timedelta(0)},
        {"command_lifetime": ACTION_AUTHORIZATION_MAX_LIFETIME + timedelta(seconds=1)},
    )
    for overrides in invalid_service_arguments:
        arguments: dict[str, object] = {
            "repository": valid_repository,
            "limits": limits(),
        }
        arguments.update(overrides)
        with pytest.raises(ActionExecutionOrchestrationRejected):
            ActionExecutionOrchestrationService(**arguments)  # type: ignore[arg-type]


class BrokenTimezone(tzinfo):
    def utcoffset(self, dt: datetime | None) -> timedelta:
        raise RuntimeError("private timezone detail")

    def dst(self, dt: datetime | None) -> timedelta:
        return timedelta(0)

    def tzname(self, dt: datetime | None) -> str:
        return "broken"


def test_pending_advance_and_result_reject_every_invalid_boundary() -> None:
    valid = pending()
    invalid_pending: tuple[dict[str, object], ...] = (
        {"installation_id": str(INSTALLATION_ID)},
        {"executor_id": str(EXECUTOR_ID)},
        {"execution_attempt_id": uuid1()},
        {"action_id": object()},
        {"message_id": uuid1()},
        {"correlation_id": uuid1()},
        {"limits": object()},
        {"requested_at": "now"},
        {"requested_at": NOW.replace(tzinfo=None)},
        {"requested_at": NOW.astimezone(timezone(timedelta(hours=8)))},
        {"requested_at": NOW.replace(tzinfo=BrokenTimezone())},
        {"command_deadline_at": NOW},
        {"command_deadline_at": NOW + ACTION_AUTHORIZATION_MAX_LIFETIME + timedelta(seconds=1)},
    )
    values = {field.name: getattr(valid, field.name) for field in fields(valid)}
    for overrides in invalid_pending:
        with pytest.raises(ActionExecutionOrchestrationRejected):
            PendingActionExecutionAdvance(**{**values, **overrides})

    with pytest.raises(ActionExecutionOrchestrationRejected):
        ActionExecutionAdvanceResult(kind="idle")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_service_maps_invalid_clock_ids_results_and_repository_errors() -> None:
    expected = ActionExecutionAdvanceResult(kind=ActionExecutionAdvanceKind.IDLE)

    class RaisingClock:
        def now(self) -> datetime:
            raise RuntimeError("private clock detail")

    class InvalidClock:
        def now(self) -> datetime:
            return NOW.replace(tzinfo=None)

    def raising_id() -> UUID:
        raise RuntimeError("private id detail")

    invalid_services = (
        ActionExecutionOrchestrationService(
            repository=RecordingRepository(expected), limits=limits(), clock=RaisingClock()
        ),
        ActionExecutionOrchestrationService(
            repository=RecordingRepository(expected), limits=limits(), clock=InvalidClock()
        ),
        ActionExecutionOrchestrationService(
            repository=RecordingRepository(expected),
            limits=limits(),
            clock=FixedClock(),
            id_source=raising_id,
        ),
        ActionExecutionOrchestrationService(
            repository=RecordingRepository(expected),
            limits=limits(),
            clock=FixedClock(),
            id_source=lambda: uuid1(),
        ),
    )
    for service in invalid_services:
        with pytest.raises(ActionExecutionOrchestrationUnavailable):
            await service.advance(INSTALLATION_ID, EXECUTOR_ID)

    for invalid_installation, invalid_executor in (
        (str(INSTALLATION_ID), EXECUTOR_ID),
        (INSTALLATION_ID, str(EXECUTOR_ID)),
    ):
        with pytest.raises(ActionExecutionOrchestrationRejected):
            await ActionExecutionOrchestrationService(
                repository=RecordingRepository(expected), limits=limits()
            ).advance(invalid_installation, invalid_executor)

    for repository_result, expected_error in (
        (ActionExecutionOrchestrationRejected(), ActionExecutionOrchestrationRejected),
        (ActionExecutionOrchestrationUnavailable(), ActionExecutionOrchestrationUnavailable),
        (object(), ActionExecutionOrchestrationRejected),
    ):
        service = ActionExecutionOrchestrationService(
            repository=RecordingRepository(repository_result),  # type: ignore[arg-type]
            limits=limits(),
            clock=FixedClock(),
            id_source=FixedIds(),
        )
        with pytest.raises(expected_error):
            await service.advance(INSTALLATION_ID, EXECUTOR_ID)

    assert SystemActionExecutionOrchestrationClock().now().utcoffset() == timedelta(0)
