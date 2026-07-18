from __future__ import annotations

from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import cast

import pytest
from sqlalchemy.engine import RowMapping

from automation_tool.control_plane.application.tasks import (
    TaskCreationResult,
    TaskPersistenceRejected,
    TaskRecord,
)
from automation_tool.control_plane.domain import (
    DouyinSearchExposureAction,
    DouyinSearchExposureDefinition,
    InstallationId,
    TaskId,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.database.task_repository import (
    SqlAlchemyTaskRepository,
    _definition,
)

NOW = datetime(2026, 7, 18, 15, 0, tzinfo=UTC)
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


@pytest.mark.asyncio
async def test_repository_rejects_untyped_identity_revision_status_and_time_before_io() -> None:
    repository = SqlAlchemyTaskRepository(cast(Database, object()))
    task_id = TaskId.new()
    installation_id = InstallationId.new()
    operations: tuple[Awaitable[TaskCreationResult | TaskRecord | None], ...] = (
        repository.create(
            task_id=cast(TaskId, "private-task"),
            installation_id=installation_id,
            idempotency_key="task:validation:1",
            definition=DEFINITION,
            created_at=NOW,
        ),
        repository.create(
            task_id=task_id,
            installation_id=cast(InstallationId, "private-installation"),
            idempotency_key="task:validation:2",
            definition=DEFINITION,
            created_at=NOW,
        ),
        repository.create(
            task_id=task_id,
            installation_id=installation_id,
            idempotency_key="task:validation:3",
            definition=cast(DouyinSearchExposureDefinition, object()),
            created_at=NOW,
        ),
        repository.create(
            task_id=task_id,
            installation_id=installation_id,
            idempotency_key="task:validation:4",
            definition=DEFINITION,
            created_at=cast(datetime, "private-time"),
        ),
        repository.create(
            task_id=task_id,
            installation_id=installation_id,
            idempotency_key="task:validation:5",
            definition=DEFINITION,
            created_at=datetime(2026, 7, 18, 15, 0),
        ),
        repository.create(
            task_id=task_id,
            installation_id=installation_id,
            idempotency_key="contains space",
            definition=DEFINITION,
            created_at=NOW,
        ),
        repository.transition(
            task_id=task_id,
            installation_id=installation_id,
            expected_revision=cast(int, True),
            target_status=TaskStatus.VALIDATING,
            updated_at=NOW,
        ),
        repository.transition(
            task_id=task_id,
            installation_id=installation_id,
            expected_revision=0,
            target_status=TaskStatus.VALIDATING,
            updated_at=NOW,
        ),
        repository.transition(
            task_id=task_id,
            installation_id=installation_id,
            expected_revision=1,
            target_status=cast(TaskStatus, "running"),
            updated_at=NOW,
        ),
    )

    for operation in operations:
        with pytest.raises(TaskPersistenceRejected) as captured:
            await operation
        assert str(captured.value) == "Task persistence operation is rejected"
        assert "private" not in repr(captured.value)
        assert captured.value.__cause__ is None
        assert captured.value.__context__ is None


def test_repository_rejects_malformed_persisted_definition_without_disclosure() -> None:
    row = cast(
        RowMapping,
        {
            "search_keyword": "新能源汽车",
            "action": "private-action",
            "message_template": None,
            "target_limit": 10,
            "minimum_interval_seconds": 30,
            "maximum_interval_seconds": 90,
            "preview_required": True,
            "final_confirmation_required": True,
        },
    )

    with pytest.raises(TaskPersistenceRejected) as captured:
        _definition(row)
    assert "private-action" not in repr(captured.value)
    assert captured.value.__cause__ is None


@pytest.mark.asyncio
async def test_repository_rejects_invalid_page_boundaries_before_io() -> None:
    repository = SqlAlchemyTaskRepository(cast(Database, object()))
    installation_id = InstallationId.new()
    task_id = TaskId.new()
    operations = (
        repository.list_page(
            installation_id=cast(InstallationId, "private-installation"),
            before_updated_at=None,
            before_task_id=None,
            limit=20,
        ),
        repository.list_page(
            installation_id=installation_id,
            before_updated_at=None,
            before_task_id=None,
            limit=cast(int, True),
        ),
        repository.list_page(
            installation_id=installation_id,
            before_updated_at=None,
            before_task_id=None,
            limit=0,
        ),
        repository.list_page(
            installation_id=installation_id,
            before_updated_at=NOW,
            before_task_id=None,
            limit=20,
        ),
        repository.list_page(
            installation_id=installation_id,
            before_updated_at=None,
            before_task_id=task_id,
            limit=20,
        ),
    )

    for operation in operations:
        with pytest.raises(TaskPersistenceRejected) as captured:
            await operation
        assert str(captured.value) == "Task persistence operation is rejected"
        assert captured.value.__cause__ is None
        assert captured.value.__context__ is None
