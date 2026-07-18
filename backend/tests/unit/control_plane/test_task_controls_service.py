from __future__ import annotations

from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from automation_tool.control_plane.application import task_controls as controls_module
from automation_tool.control_plane.application.task_command_delivery import TaskCommandRecord
from automation_tool.control_plane.application.task_controls import (
    InvalidTaskControl,
    PendingTaskControl,
    TaskControlConflict,
    TaskControlEnqueueResult,
    TaskControlNotFound,
    TaskControlService,
    TaskControlUnavailable,
)
from automation_tool.control_plane.domain import (
    ExecutionAttemptId,
    InstallationId,
    TaskCommandStatus,
    TaskCommandType,
    TaskId,
)

NOW = datetime(2026, 7, 18, 18, 0, tzinfo=UTC)
INSTALLATION_ID = InstallationId.parse("123e4567-e89b-42d3-a456-426614174003")
TASK_ID = TaskId.parse("123e4567-e89b-42d3-a456-426614174005")
ATTEMPT_ID = ExecutionAttemptId.parse("123e4567-e89b-42d3-a456-426614174006")
MESSAGE_ID = UUID("323e4567-e89b-42d3-a456-426614174001")
CORRELATION_ID = UUID("323e4567-e89b-42d3-a456-426614174002")


@dataclass
class MutableClock:
    value: datetime = NOW

    def now(self) -> datetime:
        return self.value


def record(control: PendingTaskControl, *, created: bool = True) -> TaskControlEnqueueResult:
    command = TaskCommandRecord(
        message_id=control.message_id,
        correlation_id=control.correlation_id,
        installation_id=control.installation_id,
        task_id=control.task_id,
        execution_attempt_id=ATTEMPT_ID,
        sequence=2,
        command_type=control.command_type,
        status=TaskCommandStatus.PENDING,
        idempotency_key=control.idempotency_key,
        revision=1,
        delivery_attempts=0,
        next_delivery_at=control.created_at,
        lease_expires_at=None,
        delivered_at=None,
        acknowledged_at=None,
        response_message_id=None,
        response_type=None,
        deadline_at=control.deadline_at,
        created_at=control.created_at,
        updated_at=control.created_at,
    )
    return TaskControlEnqueueResult(command=command, created=created)


class MemoryTaskControlRepository:
    def __init__(self) -> None:
        self.controls: list[PendingTaskControl] = []
        self.failure: Exception | None = None

    async def enqueue_control(self, control: PendingTaskControl) -> TaskControlEnqueueResult:
        if self.failure is not None:
            raise self.failure
        self.controls.append(control)
        return record(control)


@pytest.mark.asyncio
async def test_pause_and_resume_create_only_persistent_control_intents() -> None:
    repository = MemoryTaskControlRepository()
    identifiers = iter((MESSAGE_ID, CORRELATION_ID, MESSAGE_ID, CORRELATION_ID))
    service = TaskControlService(
        repository=repository,
        clock=MutableClock(),
        id_source=lambda: next(identifiers),
        command_lifetime=timedelta(seconds=45),
    )

    paused = await service.pause(
        installation_id=INSTALLATION_ID,
        task_id=str(TASK_ID),
        idempotency_key="task:pause:demo-1",
    )
    resumed = await service.resume(
        installation_id=INSTALLATION_ID,
        task_id=str(TASK_ID),
        idempotency_key="task:resume:demo-1",
    )

    assert paused.command.command_type is TaskCommandType.TASK_PAUSE
    assert resumed.command.command_type is TaskCommandType.TASK_RESUME
    assert paused.command.status is resumed.command.status is TaskCommandStatus.PENDING
    assert [control.deadline_at for control in repository.controls] == [
        NOW + timedelta(seconds=45),
        NOW + timedelta(seconds=45),
    ]
    assert all(control.task_id == TASK_ID for control in repository.controls)


@pytest.mark.asyncio
async def test_input_and_repository_failures_are_closed_and_stable() -> None:
    repository = MemoryTaskControlRepository()
    service = TaskControlService(repository=repository, clock=MutableClock())

    for invalid_task in ("private-invalid", ""):
        with pytest.raises(InvalidTaskControl, match=r"^Task control request is invalid$"):
            await service.pause(
                installation_id=INSTALLATION_ID,
                task_id=invalid_task,
                idempotency_key="task:pause:valid",
            )
    for invalid_key in ("", "private invalid", "x" * 129):
        with pytest.raises(InvalidTaskControl):
            await service.resume(
                installation_id=INSTALLATION_ID,
                task_id=str(TASK_ID),
                idempotency_key=invalid_key,
            )
    with pytest.raises(InvalidTaskControl):
        await service.pause(
            installation_id=str(INSTALLATION_ID),  # type: ignore[arg-type]
            task_id=str(TASK_ID),
            idempotency_key="task:pause:scope",
        )

    for failure in (TaskControlNotFound(), TaskControlConflict()):
        repository.failure = failure
        with pytest.raises(type(failure)):
            await service.pause(
                installation_id=INSTALLATION_ID,
                task_id=str(TASK_ID),
                idempotency_key="task:pause:failure",
            )
    repository.failure = RuntimeError("private database address")
    with pytest.raises(TaskControlUnavailable, match=r"^Task control is unavailable$") as caught:
        await service.pause(
            installation_id=INSTALLATION_ID,
            task_id=str(TASK_ID),
            idempotency_key="task:pause:unavailable",
        )
    assert caught.value.__cause__ is None


def test_control_value_objects_and_configuration_fail_closed() -> None:
    valid = PendingTaskControl(
        message_id=MESSAGE_ID,
        correlation_id=CORRELATION_ID,
        installation_id=INSTALLATION_ID,
        task_id=TASK_ID,
        command_type=TaskCommandType.TASK_PAUSE,
        idempotency_key="task:pause:demo-1",
        deadline_at=NOW + timedelta(seconds=45),
        created_at=NOW,
    )
    values = {field.name: getattr(valid, field.name) for field in fields(valid)}
    invalid_values: tuple[dict[str, object], ...] = (
        {"message_id": UUID("123e4567-e89b-12d3-a456-426614174001")},
        {"correlation_id": UUID("123e4567-e89b-12d3-a456-426614174002")},
        {"installation_id": str(INSTALLATION_ID)},
        {"task_id": str(TASK_ID)},
        {"command_type": TaskCommandType.TASK_OFFER},
        {"idempotency_key": "private invalid"},
        {"created_at": NOW.replace(tzinfo=None)},
        {"deadline_at": NOW},
    )
    for overrides in invalid_values:
        with pytest.raises(InvalidTaskControl):
            PendingTaskControl(**{**values, **overrides})

    pause_result = record(valid)
    with pytest.raises(InvalidTaskControl):
        TaskControlEnqueueResult(command=object(), created=True)  # type: ignore[arg-type]
    with pytest.raises(InvalidTaskControl):
        TaskControlEnqueueResult(command=pause_result.command, created=1)  # type: ignore[arg-type]
    with pytest.raises(InvalidTaskControl):
        TaskControlEnqueueResult(
            command=replace(
                pause_result.command,
                command_type=TaskCommandType.TASK_OFFER,
            ),
            created=True,
        )

    repository = MemoryTaskControlRepository()
    for arguments in (
        {"repository": object()},
        {"repository": repository, "clock": object()},
        {"repository": repository, "id_source": object()},
        {"repository": repository, "command_lifetime": timedelta(0)},
    ):
        with pytest.raises(InvalidTaskControl):
            TaskControlService(**arguments)  # type: ignore[arg-type]

    assert controls_module.SystemTaskControlClock().now().utcoffset() == timedelta(0)
    assert str(TaskControlNotFound()) == "Task is unavailable"
    assert str(TaskControlConflict()) == "Task control was rejected"
    assert str(TaskControlUnavailable()) == "Task control is unavailable"


@pytest.mark.asyncio
async def test_clock_identifier_and_invalid_repository_results_are_rejected() -> None:
    class BadClock:
        def now(self) -> datetime:
            return NOW.replace(tzinfo=None)

    with pytest.raises(TaskControlUnavailable):
        await TaskControlService(
            repository=MemoryTaskControlRepository(),
            clock=BadClock(),
        ).pause(
            installation_id=INSTALLATION_ID,
            task_id=str(TASK_ID),
            idempotency_key="task:pause:clock",
        )

    with pytest.raises(TaskControlUnavailable):
        await TaskControlService(
            repository=MemoryTaskControlRepository(),
            clock=MutableClock(),
            id_source=lambda: UUID("123e4567-e89b-12d3-a456-426614174001"),
        ).pause(
            installation_id=INSTALLATION_ID,
            task_id=str(TASK_ID),
            idempotency_key="task:pause:id",
        )

    class BadRepository(MemoryTaskControlRepository):
        async def enqueue_control(self, control: PendingTaskControl) -> TaskControlEnqueueResult:
            return object()  # type: ignore[return-value]

    with pytest.raises(TaskControlUnavailable):
        await TaskControlService(repository=BadRepository(), clock=MutableClock()).pause(
            installation_id=INSTALLATION_ID,
            task_id=str(TASK_ID),
            idempotency_key="task:pause:result",
        )
