from __future__ import annotations

from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import cast

import pytest

from automation_tool.control_plane.application.tasks import (
    TaskPersistenceRejected,
    TaskRecord,
)
from automation_tool.control_plane.domain import InstallationId, TaskId, TaskStatus
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.database.task_repository import (
    SqlAlchemyTaskRepository,
)

NOW = datetime(2026, 7, 18, 15, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_repository_rejects_untyped_identity_revision_status_and_time_before_io() -> None:
    repository = SqlAlchemyTaskRepository(cast(Database, object()))
    task_id = TaskId.new()
    installation_id = InstallationId.new()
    operations: tuple[Awaitable[TaskRecord | None], ...] = (
        repository.create(
            task_id=cast(TaskId, "private-task"),
            installation_id=installation_id,
            created_at=NOW,
        ),
        repository.create(
            task_id=task_id,
            installation_id=cast(InstallationId, "private-installation"),
            created_at=NOW,
        ),
        repository.create(
            task_id=task_id,
            installation_id=installation_id,
            created_at=cast(datetime, "private-time"),
        ),
        repository.create(
            task_id=task_id,
            installation_id=installation_id,
            created_at=datetime(2026, 7, 18, 15, 0),
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
