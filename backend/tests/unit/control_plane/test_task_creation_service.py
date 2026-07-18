from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from automation_tool.control_plane.application.tasks import (
    InvalidTaskCreation,
    TaskCreationResult,
    TaskCreationService,
    TaskRecord,
)
from automation_tool.control_plane.bootstrap.tasks import _SystemClock
from automation_tool.control_plane.domain import (
    DouyinSearchExposureAction,
    DouyinSearchExposureDefinition,
    InstallationId,
    TaskId,
    TaskStatus,
)

NOW = datetime(2026, 7, 18, 16, 0, tzinfo=UTC)
DEFINITION = DouyinSearchExposureDefinition(
    search_keyword="新能源汽车",
    action=DouyinSearchExposureAction.BROWSE,
    message_template=None,
    target_limit=10,
    minimum_interval_seconds=30,
    maximum_interval_seconds=90,
    preview_required=True,
    final_confirmation_required=True,
)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class RecordingRepository:
    def __init__(self) -> None:
        self.created: (
            tuple[
                TaskId,
                InstallationId,
                str,
                DouyinSearchExposureDefinition,
                datetime,
            ]
            | None
        ) = None

    async def create(
        self,
        *,
        task_id: TaskId,
        installation_id: InstallationId,
        idempotency_key: str,
        definition: DouyinSearchExposureDefinition,
        created_at: datetime,
    ) -> TaskCreationResult:
        self.created = (task_id, installation_id, idempotency_key, definition, created_at)
        task = TaskRecord(
            task_id=task_id,
            installation_id=installation_id,
            status=TaskStatus.DRAFT,
            revision=1,
            last_event_sequence=0,
            created_at=created_at,
            updated_at=created_at,
        )
        return TaskCreationResult(task=task, created=True)

    async def get(
        self,
        *,
        task_id: TaskId,
        installation_id: InstallationId,
    ) -> TaskRecord | None:
        raise AssertionError("not used")

    async def transition(
        self,
        *,
        task_id: TaskId,
        installation_id: InstallationId,
        expected_revision: int,
        target_status: TaskStatus,
        updated_at: datetime,
    ) -> TaskRecord:
        raise AssertionError("not used")


@pytest.mark.asyncio
async def test_service_creates_one_scoped_draft_through_the_repository_contract() -> None:
    repository = RecordingRepository()
    service = TaskCreationService(repository=repository, clock=FixedClock())
    installation_id = InstallationId.new()

    result = await service.create(
        installation_id=installation_id,
        idempotency_key="task:create:service",
        definition=DEFINITION,
    )

    assert result.created is True
    assert result.task.installation_id == installation_id
    assert result.task.status is TaskStatus.DRAFT
    assert repository.created == (
        result.task.task_id,
        installation_id,
        "task:create:service",
        DEFINITION,
        NOW,
    )


@pytest.mark.asyncio
async def test_service_rejects_untyped_scope_and_invalid_keys_without_repository_io() -> None:
    repository = RecordingRepository()
    service = TaskCreationService(repository=repository, clock=FixedClock())

    for installation_id, key in [
        (cast(InstallationId, "private-installation"), "task:create:valid"),
        (InstallationId.new(), "contains space"),
        (InstallationId.new(), cast(str, object())),
    ]:
        with pytest.raises(InvalidTaskCreation) as captured:
            await service.create(
                installation_id=installation_id,
                idempotency_key=key,
                definition=DEFINITION,
            )
        assert str(captured.value) == "Task creation request is invalid"
        assert "private" not in repr(captured.value)
        assert captured.value.__cause__ is None
        assert captured.value.__context__ is None
    assert repository.created is None


def test_system_clock_returns_a_current_utc_timestamp() -> None:
    before = datetime.now(UTC)
    observed = _SystemClock().now()
    after = datetime.now(UTC)

    assert before <= observed <= after
    assert observed.tzinfo is UTC
