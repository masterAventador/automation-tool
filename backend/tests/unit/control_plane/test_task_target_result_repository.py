from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError

from automation_tool.control_plane.application.task_target_results import (
    InvalidTaskTargetResult,
    TaskTargetResultStatus,
    TaskTargetResultUnavailable,
)
from automation_tool.control_plane.domain import InstallationId, TaskId
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.database.task_target_result_repository import (
    SqlAlchemyTaskTargetResultRepository,
    _persisted_evidence,
    _result,
)
from automation_tool.protocol import (
    SUCCESS_ACTION_RESULT_EVIDENCE,
    ActionResultEvidence,
)

NOW = datetime(2026, 7, 21, 8, 0, tzinfo=UTC)
TARGET_ID = "123e4567-e89b-42d3-a456-426614174006"
ACTION_ID = "123e4567-e89b-42d3-a456-426614174008"


def row(**overrides: object) -> RowMapping:
    values: dict[str, object] = {
        "target_id": TARGET_ID,
        "ordinal": 1,
        "display_name": "目标一",
        "public_handle": "target_one",
        "disposition": "eligible",
        "target_updated_at": NOW,
        "excluded_at": None,
        "action_id": None,
        "action_status": None,
        "action_outcome": None,
        "evidence_code": None,
        "action_updated_at": None,
    }
    values.update(overrides)
    return cast(RowMapping, values)


@pytest.mark.parametrize(
    ("overrides", "status", "evidence", "action_present"),
    (
        ({}, TaskTargetResultStatus.PENDING, "awaiting_execution", False),
        (
            {"excluded_at": NOW},
            TaskTargetResultStatus.SKIPPED,
            "user_excluded",
            False,
        ),
        (
            {"disposition": "duplicate_in_task"},
            TaskTargetResultStatus.SKIPPED,
            "duplicate_in_task",
            False,
        ),
        (
            {"disposition": "duplicate_in_history"},
            TaskTargetResultStatus.SKIPPED,
            "duplicate_in_history",
            False,
        ),
        (
            {"disposition": "blacklisted"},
            TaskTargetResultStatus.SKIPPED,
            "blacklisted",
            False,
        ),
        (
            {
                "action_id": ACTION_ID,
                "action_status": "planned",
                "action_outcome": "pending",
                "action_updated_at": NOW,
            },
            TaskTargetResultStatus.PENDING,
            "action_pending",
            True,
        ),
        (
            {
                "action_id": ACTION_ID,
                "action_status": "dispatched",
                "action_outcome": "pending",
                "action_updated_at": NOW,
            },
            TaskTargetResultStatus.RUNNING,
            "action_in_progress",
            True,
        ),
        (
            {
                "action_id": ACTION_ID,
                "action_status": "cancelled",
                "action_outcome": "cancelled",
                "evidence_code": "action_cancelled",
                "action_updated_at": NOW,
            },
            TaskTargetResultStatus.SKIPPED,
            "action_cancelled",
            True,
        ),
        (
            {
                "action_id": ACTION_ID,
                "action_status": "verified",
                "action_outcome": "succeeded",
                "evidence_code": None,
                "action_updated_at": NOW,
            },
            TaskTargetResultStatus.SUCCEEDED,
            "executor_reported_success",
            True,
        ),
        (
            {
                "action_id": ACTION_ID,
                "action_status": "verified",
                "action_outcome": "failed",
                "evidence_code": "login_required",
                "action_updated_at": NOW,
            },
            TaskTargetResultStatus.FAILED,
            "login_required",
            True,
        ),
        (
            {
                "action_id": ACTION_ID,
                "action_status": "outcome_uncertain",
                "action_outcome": "outcome_uncertain",
                "evidence_code": "dispatch_unavailable",
                "action_updated_at": NOW,
            },
            TaskTargetResultStatus.OUTCOME_UNCERTAIN,
            "dispatch_unavailable",
            True,
        ),
    ),
)
def test_repository_row_projection_covers_every_closed_result_state(
    overrides: dict[str, object],
    status: TaskTargetResultStatus,
    evidence: str,
    action_present: bool,
) -> None:
    projected = _result(row(**overrides))
    assert projected.status is status
    assert projected.evidence.value == evidence
    assert (projected.action_id is not None) is action_present


def test_repository_rejects_invalid_or_cross_outcome_persisted_evidence() -> None:
    for evidence in ("private-evidence", "login_required"):
        with pytest.raises(InvalidTaskTargetResult):
            _persisted_evidence(
                row(evidence_code=evidence),
                allowed=SUCCESS_ACTION_RESULT_EVIDENCE,
                fallback=ActionResultEvidence.EXECUTOR_REPORTED_SUCCESS,
            )
    with pytest.raises(InvalidTaskTargetResult):
        _result(
            row(
                action_id=ACTION_ID,
                action_status="verified",
                action_outcome="pending",
                action_updated_at=NOW,
            )
        )


class FailingSessionScope:
    def __init__(self, failure: Exception) -> None:
        self.failure = failure

    async def __aenter__(self) -> object:
        raise self.failure

    async def __aexit__(
        self,
        _exception_type: object,
        _exception: object,
        _traceback: object,
    ) -> None:
        return None


class FailingSessions:
    def __init__(self, failure: Exception) -> None:
        self.failure = failure

    def begin(self) -> FailingSessionScope:
        return FailingSessionScope(self.failure)


@pytest.mark.asyncio
async def test_repository_rejects_wrong_dependencies_and_wraps_database_errors() -> None:
    with pytest.raises(TaskTargetResultUnavailable):
        SqlAlchemyTaskTargetResultRepository(cast(Database, object()))

    database = Database.from_url(
        "postgresql+asyncpg://unused:unused@127.0.0.1:1/unused",
        connect_timeout_seconds=0.01,
    )
    try:
        repository = SqlAlchemyTaskTargetResultRepository(database)
        with pytest.raises(InvalidTaskTargetResult):
            await repository.get(
                installation_id=cast(InstallationId, object()),
                task_id=TaskId.new(),
            )
        for failure, expected in (
            (InvalidTaskTargetResult(), InvalidTaskTargetResult),
            (TaskTargetResultUnavailable(), TaskTargetResultUnavailable),
            (SQLAlchemyError("private database failure"), TaskTargetResultUnavailable),
        ):
            object.__setattr__(database, "_sessions", FailingSessions(failure))
            with pytest.raises(expected) as captured:
                await repository.get(
                    installation_id=InstallationId.new(),
                    task_id=TaskId.new(),
                )
            assert "private" not in str(captured.value)
            if isinstance(failure, SQLAlchemyError):
                assert captured.value.__cause__ is None
    finally:
        await database.close()
