from __future__ import annotations

from datetime import UTC, datetime

import pytest

from automation_tool.control_plane.application.task_event_stream import (
    InvalidTaskEventStream,
    TaskEventRecord,
    TaskEventStreamBatch,
    TaskEventStreamNotFound,
    TaskEventStreamService,
    TaskEventStreamUnavailable,
)
from automation_tool.control_plane.domain import (
    ActionId,
    ExecutionAttemptId,
    InstallationId,
    TaskEventType,
    TaskEventVersion,
    TaskId,
    TaskStatus,
)

NOW = datetime(2026, 7, 18, 18, 0, tzinfo=UTC)
INSTALLATION_ID = InstallationId.new()
TASK_ID = TaskId.new()
ATTEMPT_ID = ExecutionAttemptId.new()
ACTION_ID = ActionId.new()


def event(
    sequence: int,
    *,
    event_type: TaskEventType = TaskEventType.TASK_STARTED,
    task_status: TaskStatus = TaskStatus.RUNNING,
    action_id: ActionId | None = None,
    progress_percent: int | None = None,
) -> TaskEventRecord:
    return TaskEventRecord(
        task_id=TASK_ID,
        sequence=sequence,
        event_version=TaskEventVersion.V1,
        event_type=event_type,
        task_revision=sequence + 1,
        task_status=task_status,
        execution_attempt_id=ATTEMPT_ID,
        action_id=action_id,
        progress_percent=progress_percent,
        occurred_at=NOW,
        recorded_at=NOW,
        safe_message=None,
    )


class MemoryRepository:
    def __init__(self, result: TaskEventStreamBatch | None) -> None:
        self.result = result
        self.calls: list[tuple[InstallationId, TaskId, int, int]] = []
        self.error: Exception | None = None

    async def read_batch(
        self,
        *,
        installation_id: InstallationId,
        task_id: TaskId,
        after_sequence: int,
        limit: int,
    ) -> TaskEventStreamBatch | None:
        self.calls.append((installation_id, task_id, after_sequence, limit))
        if self.error is not None:
            raise self.error
        return self.result


def batch(
    *events: TaskEventRecord,
    watermark: int | None = None,
    status: TaskStatus = TaskStatus.RUNNING,
    after_sequence: int | None = None,
) -> TaskEventStreamBatch:
    return TaskEventStreamBatch(
        events=events,
        after_sequence=(events[0].sequence - 1 if events else 0)
        if after_sequence is None
        else after_sequence,
        task_last_event_sequence=(events[-1].sequence if watermark is None and events else 0)
        if watermark is None
        else watermark,
        task_status=status,
    )


@pytest.mark.asyncio
async def test_service_parses_standard_last_event_id_and_reads_the_next_bounded_batch() -> None:
    second = event(2, action_id=ACTION_ID)
    repository = MemoryRepository(batch(second, watermark=2))
    service = TaskEventStreamService(repository=repository)

    result = await service.read(
        installation_id=INSTALLATION_ID,
        task_id=str(TASK_ID),
        last_event_id="1",
        limit=25,
    )

    assert result == repository.result
    assert repository.calls == [(INSTALLATION_ID, TASK_ID, 1, 25)]
    assert result is not None
    assert result.next_sequence == 2
    assert result.caught_up is True
    assert result.close_after_batch is False


@pytest.mark.asyncio
async def test_service_starts_at_zero_and_closes_only_after_catching_up_a_terminal_task() -> None:
    terminal = event(
        2,
        event_type=TaskEventType.TASK_COMPLETED,
        task_status=TaskStatus.SUCCEEDED,
    )
    repository = MemoryRepository(batch(event(1), terminal, status=TaskStatus.SUCCEEDED))
    service = TaskEventStreamService(repository=repository)

    result = await service.read(
        installation_id=INSTALLATION_ID,
        task_id=str(TASK_ID),
        last_event_id=None,
    )

    assert repository.calls == [(INSTALLATION_ID, TASK_ID, 0, 100)]
    assert result.close_after_batch is True

    repository.result = batch(event(1), watermark=2, status=TaskStatus.SUCCEEDED)
    partial = await service.read(
        installation_id=INSTALLATION_ID,
        task_id=str(TASK_ID),
        last_event_id=None,
        limit=1,
    )
    assert partial.caught_up is False
    assert partial.close_after_batch is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "last_event_id",
    ["", "00", "01", "+1", "-1", " 1", "1 ", "1.0", "private", str(2**53)],
)
async def test_service_rejects_noncanonical_or_out_of_range_last_event_ids(
    last_event_id: str,
) -> None:
    service = TaskEventStreamService(repository=MemoryRepository(batch()))

    with pytest.raises(InvalidTaskEventStream, match="Task event stream is invalid"):
        await service.read(
            installation_id=INSTALLATION_ID,
            task_id=str(TASK_ID),
            last_event_id=last_event_id,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("installation_id", "task_id", "limit"),
    [
        ("invalid", str(TASK_ID), 100),
        (INSTALLATION_ID, "invalid", 100),
        (INSTALLATION_ID, str(TASK_ID), True),
        (INSTALLATION_ID, str(TASK_ID), 0),
        (INSTALLATION_ID, str(TASK_ID), 101),
    ],
)
async def test_service_rejects_invalid_scope_task_or_limit_without_querying(
    installation_id: object,
    task_id: str,
    limit: object,
) -> None:
    repository = MemoryRepository(batch())
    service = TaskEventStreamService(repository=repository)

    expected_error = TaskEventStreamNotFound if task_id == "invalid" else InvalidTaskEventStream
    with pytest.raises(expected_error):
        await service.read(
            installation_id=installation_id,  # type: ignore[arg-type]
            task_id=task_id,
            last_event_id=None,
            limit=limit,  # type: ignore[arg-type]
        )
    assert repository.calls == []


@pytest.mark.asyncio
async def test_service_hides_unknown_and_cross_scope_tasks_behind_not_found() -> None:
    service = TaskEventStreamService(repository=MemoryRepository(None))

    with pytest.raises(TaskEventStreamNotFound, match="Task event stream is unavailable"):
        await service.read(
            installation_id=INSTALLATION_ID,
            task_id=str(TASK_ID),
            last_event_id=None,
        )


@pytest.mark.asyncio
async def test_service_rejects_a_cursor_ahead_of_the_durable_watermark() -> None:
    service = TaskEventStreamService(
        repository=MemoryRepository(batch(watermark=1, status=TaskStatus.RUNNING))
    )

    with pytest.raises(InvalidTaskEventStream):
        await service.read(
            installation_id=INSTALLATION_ID,
            task_id=str(TASK_ID),
            last_event_id="2",
        )


@pytest.mark.asyncio
async def test_service_fails_closed_on_gaps_wrong_tasks_and_inconsistent_empty_batches() -> None:
    wrong_task = TaskEventRecord(
        task_id=TaskId.new(),
        sequence=2,
        event_version=TaskEventVersion.V1,
        event_type=TaskEventType.TASK_STARTED,
        task_revision=2,
        task_status=TaskStatus.RUNNING,
        execution_attempt_id=ATTEMPT_ID,
        action_id=None,
        progress_percent=None,
        occurred_at=NOW,
        recorded_at=NOW,
        safe_message=None,
    )
    repository = MemoryRepository(batch(wrong_task, watermark=2))
    service = TaskEventStreamService(repository=repository)

    for result in (
        batch(event(3), watermark=3),
        batch(wrong_task, watermark=2),
        batch(watermark=2, after_sequence=1),
    ):
        repository.result = result
        with pytest.raises(TaskEventStreamUnavailable):
            await service.read(
                installation_id=INSTALLATION_ID,
                task_id=str(TASK_ID),
                last_event_id="1",
            )


@pytest.mark.asyncio
async def test_service_preserves_known_unavailable_and_wraps_unexpected_repository_errors() -> None:
    repository = MemoryRepository(batch())
    service = TaskEventStreamService(repository=repository)

    for error in (TaskEventStreamUnavailable(), RuntimeError("password=private")):
        repository.error = error
        with pytest.raises(TaskEventStreamUnavailable) as raised:
            await service.read(
                installation_id=INSTALLATION_ID,
                task_id=str(TASK_ID),
                last_event_id=None,
            )
        assert raised.value.__cause__ is None
        assert "private" not in str(raised.value)


def test_event_and_batch_models_reject_invalid_values() -> None:
    valid = event(1)
    invalid_event_values = (
        {"sequence": 0},
        {"task_revision": 0},
        {"progress_percent": True},
        {"progress_percent": 101},
        {"recorded_at": datetime(2026, 7, 18, 18, 0)},
    )
    values = {
        "task_id": valid.task_id,
        "sequence": valid.sequence,
        "event_version": valid.event_version,
        "event_type": valid.event_type,
        "task_revision": valid.task_revision,
        "task_status": valid.task_status,
        "execution_attempt_id": valid.execution_attempt_id,
        "action_id": valid.action_id,
        "progress_percent": valid.progress_percent,
        "occurred_at": valid.occurred_at,
        "recorded_at": valid.recorded_at,
        "safe_message": valid.safe_message,
    }
    for overrides in invalid_event_values:
        with pytest.raises(InvalidTaskEventStream):
            TaskEventRecord(**(values | overrides))  # type: ignore[arg-type]

    with pytest.raises(InvalidTaskEventStream):
        TaskEventStreamBatch(
            events=(event(2), event(1)),
            after_sequence=0,
            task_last_event_sequence=2,
            task_status=TaskStatus.RUNNING,
        )


def test_service_rejects_a_repository_without_the_runtime_protocol() -> None:
    with pytest.raises(InvalidTaskEventStream):
        TaskEventStreamService(repository=object())  # type: ignore[arg-type]
