from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import cast

import pytest

from automation_tool.control_plane.application.task_queries import (
    InvalidTaskQuery,
    TaskNotFound,
    TaskQueryService,
)
from automation_tool.control_plane.application.tasks import TaskRecord
from automation_tool.control_plane.domain import InstallationId, TaskId, TaskStatus


class EmptyRepository:
    async def get(
        self,
        *,
        task_id: TaskId,
        installation_id: InstallationId,
    ) -> TaskRecord | None:
        return None

    async def list_page(
        self,
        *,
        installation_id: InstallationId,
        before_updated_at: datetime | None,
        before_task_id: TaskId | None,
        limit: int,
    ) -> tuple[TaskRecord, ...]:
        return ()


class StaticRepository(EmptyRepository):
    def __init__(self, records: tuple[TaskRecord, ...]) -> None:
        self._records = records

    async def list_page(
        self,
        *,
        installation_id: InstallationId,
        before_updated_at: datetime | None,
        before_task_id: TaskId | None,
        limit: int,
    ) -> tuple[TaskRecord, ...]:
        return self._records


def _cursor(payload: str) -> str:
    return base64.urlsafe_b64encode(payload.encode("ascii")).rstrip(b"=").decode("ascii")


@pytest.mark.asyncio
async def test_query_service_rejects_invalid_scope_id_cursor_and_limit_without_details() -> None:
    service = TaskQueryService(repository=EmptyRepository())
    installation_id = InstallationId.new()

    for operation, expected in (
        (
            service.get(
                installation_id=cast(InstallationId, "private-installation"),
                task_id=str(TaskId.new()),
            ),
            InvalidTaskQuery,
        ),
        (
            service.get(installation_id=installation_id, task_id="private-task"),
            TaskNotFound,
        ),
        (
            service.list(installation_id=installation_id, cursor="private-cursor", limit=20),
            InvalidTaskQuery,
        ),
        (
            service.list(installation_id=installation_id, cursor=None, limit=cast(int, True)),
            InvalidTaskQuery,
        ),
    ):
        with pytest.raises(expected) as captured:
            await operation
        assert "private" not in repr(captured.value)
        assert captured.value.__cause__ is None
        assert captured.value.__context__ is None


@pytest.mark.asyncio
async def test_query_service_rejects_every_noncanonical_cursor_shape() -> None:
    service = TaskQueryService(repository=EmptyRepository())
    installation_id = InstallationId.new()
    task_id = str(TaskId.new())
    invalid_cursors = (
        cast(str, 7),
        "",
        "A",
        "Zh",
        _cursor(
            f'{{"taskId":"{task_id}","taskId":"{task_id}",'
            '"updatedAt":"2026-07-18T17:00:00.000000Z"}'
        ),
        _cursor("[]"),
        _cursor(f'{{"taskId":"{task_id}","unexpected":true}}'),
        _cursor('{"taskId":"not-a-task","updatedAt":"2026-07-18T17:00:00.000000Z"}'),
        _cursor(f'{{"taskId":"{task_id}","updatedAt":7}}'),
        _cursor(f'{{"taskId":"{task_id}","updatedAt":"2026-07-18T17:00:00+00:00"}}'),
        _cursor(f'{{"taskId":"{task_id}","updatedAt":"2026-07-18T17:00:00Z"}}'),
    )

    for cursor in invalid_cursors:
        with pytest.raises(InvalidTaskQuery) as captured:
            await service.list(installation_id=installation_id, cursor=cursor, limit=20)
        assert captured.value.__cause__ is None
        assert captured.value.__context__ is None


@pytest.mark.asyncio
async def test_query_service_rejects_naive_page_boundary_timestamp() -> None:
    installation_id = InstallationId.new()
    naive = datetime(2026, 7, 18, 17, 0)
    record = TaskRecord(
        task_id=TaskId.new(),
        installation_id=installation_id,
        status=TaskStatus.DRAFT,
        revision=1,
        created_at=naive.replace(tzinfo=UTC),
        updated_at=naive,
    )
    service = TaskQueryService(repository=StaticRepository((record, record)))

    with pytest.raises(InvalidTaskQuery):
        await service.list(installation_id=installation_id, cursor=None, limit=1)
