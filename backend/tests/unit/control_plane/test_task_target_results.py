from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from typing import cast

import pytest

from automation_tool.control_plane.application.task_target_results import (
    InvalidTaskTargetResult,
    TaskTargetResult,
    TaskTargetResultEvidence,
    TaskTargetResultNotFound,
    TaskTargetResultService,
    TaskTargetResultSnapshot,
    TaskTargetResultStatus,
    TaskTargetResultUnavailable,
)
from automation_tool.control_plane.application.tasks import TaskRecord
from automation_tool.control_plane.domain import (
    ActionId,
    InstallationId,
    TargetId,
    TaskId,
    TaskStatus,
)
from automation_tool.protocol import MAX_TASK_TARGET_LIMIT

NOW = datetime(2026, 7, 21, 8, 0, tzinfo=UTC)
TARGET_ID = TargetId.new()
ACTION_ID = ActionId.new()
INSTALLATION_ID = InstallationId.new()
TASK_ID = TaskId.new()


def result(
    *,
    ordinal: int = 1,
    target_id: TargetId = TARGET_ID,
    status: TaskTargetResultStatus = TaskTargetResultStatus.SUCCEEDED,
    evidence: TaskTargetResultEvidence = TaskTargetResultEvidence.COMMENT_CONFIRMED,
    action_id: ActionId | None = ACTION_ID,
) -> TaskTargetResult:
    return TaskTargetResult(
        target_id=target_id,
        ordinal=ordinal,
        display_name="目标一",
        public_handle="target.one",
        status=status,
        evidence=evidence,
        action_id=action_id,
        updated_at=NOW,
    )


def task() -> TaskRecord:
    return TaskRecord(
        task_id=TASK_ID,
        installation_id=INSTALLATION_ID,
        status=TaskStatus.RUNNING,
        revision=2,
        last_event_sequence=1,
        created_at=NOW,
        updated_at=NOW,
    )


class ResultRepository:
    def __init__(
        self,
        snapshot: TaskTargetResultSnapshot | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.failure = failure

    async def get(
        self,
        *,
        installation_id: InstallationId,
        task_id: TaskId,
    ) -> TaskTargetResultSnapshot | None:
        assert installation_id == INSTALLATION_ID
        assert task_id == TASK_ID
        if self.failure is not None:
            raise self.failure
        return self.snapshot


@pytest.mark.parametrize(
    ("status", "evidence", "action_id"),
    [
        (
            TaskTargetResultStatus.SUCCEEDED,
            TaskTargetResultEvidence.COMMENT_CONFIRMED,
            ACTION_ID,
        ),
        (
            TaskTargetResultStatus.SKIPPED,
            TaskTargetResultEvidence.USER_EXCLUDED,
            None,
        ),
        (
            TaskTargetResultStatus.FAILED,
            TaskTargetResultEvidence.LOGIN_REQUIRED,
            ACTION_ID,
        ),
        (
            TaskTargetResultStatus.OUTCOME_UNCERTAIN,
            TaskTargetResultEvidence.FINAL_STATE_UNCONFIRMED,
            ACTION_ID,
        ),
    ],
)
def test_target_result_accepts_only_coherent_status_evidence_and_action_scope(
    status: TaskTargetResultStatus,
    evidence: TaskTargetResultEvidence,
    action_id: ActionId | None,
) -> None:
    result = TaskTargetResult(
        target_id=TARGET_ID,
        ordinal=1,
        display_name="目标一",
        public_handle="target.one",
        status=status,
        evidence=evidence,
        action_id=action_id,
        updated_at=NOW,
    )

    assert result.status is status
    assert result.evidence is evidence
    assert result.action_id is action_id
    assert "目标一" not in repr(result)


@pytest.mark.parametrize(
    ("status", "evidence", "action_id"),
    [
        (
            TaskTargetResultStatus.SUCCEEDED,
            TaskTargetResultEvidence.USER_EXCLUDED,
            ACTION_ID,
        ),
        (
            TaskTargetResultStatus.SKIPPED,
            TaskTargetResultEvidence.COMMENT_CONFIRMED,
            None,
        ),
        (
            TaskTargetResultStatus.OUTCOME_UNCERTAIN,
            TaskTargetResultEvidence.FINAL_STATE_UNCONFIRMED,
            None,
        ),
    ],
)
def test_target_result_rejects_cross_status_or_missing_action_evidence(
    status: TaskTargetResultStatus,
    evidence: TaskTargetResultEvidence,
    action_id: ActionId | None,
) -> None:
    with pytest.raises(InvalidTaskTargetResult):
        TaskTargetResult(
            target_id=TARGET_ID,
            ordinal=1,
            display_name="目标一",
            public_handle=None,
            status=status,
            evidence=evidence,
            action_id=action_id,
            updated_at=NOW,
        )


@pytest.mark.parametrize(
    ("status", "evidence", "action_id"),
    (
        (
            TaskTargetResultStatus.PENDING,
            TaskTargetResultEvidence.AWAITING_EXECUTION,
            None,
        ),
        (
            TaskTargetResultStatus.PENDING,
            TaskTargetResultEvidence.ACTION_PENDING,
            ACTION_ID,
        ),
        (
            TaskTargetResultStatus.RUNNING,
            TaskTargetResultEvidence.ACTION_IN_PROGRESS,
            ACTION_ID,
        ),
        (
            TaskTargetResultStatus.SKIPPED,
            TaskTargetResultEvidence.ACTION_CANCELLED,
            ACTION_ID,
        ),
    ),
)
def test_target_result_accepts_pending_running_and_cancelled_action_shapes(
    status: TaskTargetResultStatus,
    evidence: TaskTargetResultEvidence,
    action_id: ActionId | None,
) -> None:
    assert result(status=status, evidence=evidence, action_id=action_id).status is status


def test_target_result_rejects_invalid_identity_summary_ordinal_and_time() -> None:
    valid = result()
    invalid: tuple[Callable[[], TaskTargetResult], ...] = (
        lambda: replace(valid, target_id=cast(TargetId, "private-target")),
        lambda: replace(valid, ordinal=cast(int, True)),
        lambda: replace(valid, ordinal=0),
        lambda: replace(valid, ordinal=MAX_TASK_TARGET_LIMIT + 1),
        lambda: replace(valid, display_name=""),
        lambda: replace(valid, status=cast(TaskTargetResultStatus, "succeeded")),
        lambda: replace(valid, evidence=cast(TaskTargetResultEvidence, "comment_confirmed")),
        lambda: replace(valid, updated_at=cast(datetime, object())),
        lambda: replace(valid, updated_at=datetime(2026, 7, 21, 8, 0)),
        lambda: replace(valid, updated_at=NOW.astimezone(timezone(timedelta(hours=8)))),
    )

    for create_invalid in invalid:
        with pytest.raises(InvalidTaskTargetResult):
            create_invalid()


def test_target_result_snapshot_requires_typed_ordered_unique_bounded_items() -> None:
    first = result()
    second = result(ordinal=2, target_id=TargetId.new())
    snapshot = TaskTargetResultSnapshot(task=task(), items=(first, second))
    assert snapshot.items == (first, second)

    invalid: tuple[Callable[[], TaskTargetResultSnapshot], ...] = (
        lambda: TaskTargetResultSnapshot(task=cast(TaskRecord, object()), items=()),
        lambda: TaskTargetResultSnapshot(task=task(), items=cast(tuple[TaskTargetResult, ...], [])),
        lambda: TaskTargetResultSnapshot(
            task=task(),
            items=tuple(first for _ in range(MAX_TASK_TARGET_LIMIT + 1)),
        ),
        lambda: TaskTargetResultSnapshot(
            task=task(),
            items=cast(tuple[TaskTargetResult, ...], (object(),)),
        ),
        lambda: TaskTargetResultSnapshot(task=task(), items=(second, first)),
        lambda: TaskTargetResultSnapshot(
            task=task(),
            items=(first, replace(first, ordinal=2)),
        ),
    )
    for create_invalid in invalid:
        with pytest.raises(InvalidTaskTargetResult):
            create_invalid()


@pytest.mark.asyncio
async def test_target_result_service_preserves_closed_failures_and_wraps_unknown_errors() -> None:
    snapshot = TaskTargetResultSnapshot(task=task(), items=(result(),))
    service = TaskTargetResultService(repository=ResultRepository(snapshot))
    assert await service.get(installation_id=INSTALLATION_ID, task_id=TASK_ID) is snapshot

    with pytest.raises(InvalidTaskTargetResult):
        TaskTargetResultService(repository=cast(ResultRepository, object()))
    with pytest.raises(InvalidTaskTargetResult):
        await service.get(installation_id=cast(InstallationId, object()), task_id=TASK_ID)
    with pytest.raises(InvalidTaskTargetResult):
        await service.get(installation_id=INSTALLATION_ID, task_id=cast(TaskId, object()))

    cases = (
        (ResultRepository(), TaskTargetResultNotFound),
        (ResultRepository(failure=InvalidTaskTargetResult()), InvalidTaskTargetResult),
        (ResultRepository(failure=TaskTargetResultUnavailable()), TaskTargetResultUnavailable),
        (ResultRepository(failure=RuntimeError("private")), TaskTargetResultUnavailable),
    )
    for repository, expected in cases:
        with pytest.raises(expected) as captured:
            await TaskTargetResultService(repository=repository).get(
                installation_id=INSTALLATION_ID,
                task_id=TASK_ID,
            )
        assert "private" not in str(captured.value)
