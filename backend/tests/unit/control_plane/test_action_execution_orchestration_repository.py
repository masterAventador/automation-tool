"""Fail-closed branches around the PostgreSQL action orchestrator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from automation_tool.control_plane.application.action_execution_orchestration import (
    ActionExecutionAdvanceKind,
    ActionExecutionLimits,
    ActionExecutionOrchestrationRejected,
    ActionExecutionOrchestrationUnavailable,
    PendingActionExecutionAdvance,
)
from automation_tool.control_plane.application.action_risk_authorizations import (
    ActionRiskAuthorizationRejected,
    ActionRiskAuthorizationUnavailable,
)
from automation_tool.control_plane.domain import (
    MAX_TASK_EVENT_SEQUENCE,
    DouyinSearchExposureAction,
    ExecutionAttemptId,
    ExecutorId,
    InstallationId,
    TargetId,
    TaskId,
)
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.database import (
    action_execution_orchestration_repository as repository_module,
)

NOW = datetime(2026, 7, 21, 8, 0, tzinfo=UTC)


def database_without_connection() -> Database:
    return Database.from_url(
        "postgresql+asyncpg://unused:unused@127.0.0.1:1/unused",
        connect_timeout_seconds=0.01,
    )


def pending() -> PendingActionExecutionAdvance:
    requested_at = NOW
    return PendingActionExecutionAdvance(
        installation_id=InstallationId.new(),
        executor_id=ExecutorId.new(),
        execution_attempt_id=uuid4(),
        action_id=uuid4(),
        message_id=uuid4(),
        correlation_id=uuid4(),
        limits=ActionExecutionLimits(
            minimum_interval_seconds=2,
            task_action_limit=2,
            daily_action_limit=10,
            consecutive_failure_threshold=3,
        ),
        requested_at=requested_at,
        command_deadline_at=requested_at + timedelta(minutes=5),
    )


def next_authorization() -> repository_module._NextAuthorization:
    return repository_module._NextAuthorization(
        task_id=TaskId.new(),
        attempt_id=ExecutionAttemptId.new(),
        target_id=TargetId.new(),
        action=DouyinSearchExposureAction.COMMENT,
    )


@pytest.mark.asyncio
async def test_repository_rejects_invalid_database_and_advance_request() -> None:
    with pytest.raises(ActionExecutionOrchestrationRejected):
        repository_module.SqlAlchemyActionExecutionOrchestrationRepository(object())  # type: ignore[arg-type]

    database = database_without_connection()
    try:
        repository = repository_module.SqlAlchemyActionExecutionOrchestrationRepository(database)
        with pytest.raises(ActionExecutionOrchestrationRejected):
            await repository.advance(object())  # type: ignore[arg-type]
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_terminal_finalization_rejects_invalid_selection_and_exhausted_watermark() -> None:
    class TerminalRows:
        def mappings(self) -> TerminalRows:
            return self

        def all(self) -> list[dict[str, str]]:
            return [{"status": "verified", "outcome": "succeeded"}]

    class TerminalSession:
        @staticmethod
        async def execute(_statement: object) -> TerminalRows:
            return TerminalRows()

    database = database_without_connection()
    try:
        repository = repository_module.SqlAlchemyActionExecutionOrchestrationRepository(database)
        request = pending()
        with pytest.raises(ActionExecutionOrchestrationRejected):
            await repository._finalize_completed_task_locked(
                object(),  # type: ignore[arg-type]
                request,
                task_row={},
                active_attempt={},
                selected_target_count=0,
            )
        with pytest.raises(ActionExecutionOrchestrationRejected):
            await repository._finalize_completed_task_locked(
                TerminalSession(),  # type: ignore[arg-type]
                request,
                task_row={
                    "id": uuid4(),
                    "revision": 1,
                    "last_event_sequence": MAX_TASK_EVENT_SEQUENCE,
                },
                active_attempt={"id": uuid4(), "revision": 1},
                selected_target_count=1,
            )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_concurrent_authorization_rejection_is_re_read_once_then_idles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RejectingAuthorization:
        calls = 0

        async def authorize(self, **values: object) -> None:
            self.calls += 1
            raise ActionRiskAuthorizationRejected

    database = database_without_connection()
    try:
        repository = repository_module.SqlAlchemyActionExecutionOrchestrationRepository(database)
        authorization = RejectingAuthorization()

        async def prepared(
            request: PendingActionExecutionAdvance,
        ) -> repository_module._NextAuthorization:
            return next_authorization()

        monkeypatch.setattr(repository, "_prepare", prepared)
        monkeypatch.setattr(repository, "_authorization", authorization)

        result = await repository.advance(pending())

        assert result.kind is ActionExecutionAdvanceKind.IDLE
        assert authorization.calls == 2
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_repository_collapses_authorization_and_sql_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = database_without_connection()
    try:
        repository = repository_module.SqlAlchemyActionExecutionOrchestrationRepository(database)

        async def prepared(
            request: PendingActionExecutionAdvance,
        ) -> repository_module._NextAuthorization:
            return next_authorization()

        class UnavailableAuthorization:
            @staticmethod
            async def authorize(**values: object) -> None:
                raise ActionRiskAuthorizationUnavailable

        monkeypatch.setattr(repository, "_prepare", prepared)
        monkeypatch.setattr(repository, "_authorization", UnavailableAuthorization())
        with pytest.raises(ActionExecutionOrchestrationUnavailable):
            await repository.advance(pending())

        for failure, expected in (
            (ActionExecutionOrchestrationRejected(), ActionExecutionOrchestrationRejected),
            (ActionExecutionOrchestrationUnavailable(), ActionExecutionOrchestrationUnavailable),
            (SQLAlchemyError("private database detail"), ActionExecutionOrchestrationUnavailable),
        ):

            async def failing_prepare(
                request: PendingActionExecutionAdvance,
                error: Exception = failure,
            ) -> repository_module._NextAuthorization:
                raise error

            monkeypatch.setattr(repository, "_prepare", failing_prepare)
            with pytest.raises(expected):
                await repository.advance(pending())
    finally:
        await database.close()
