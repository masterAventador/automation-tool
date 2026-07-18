from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from automation_tool.control_plane.application.task_command_delivery import (
    TaskCommandDeliveryRejected,
    TaskCommandRecord,
)
from automation_tool.control_plane.application.task_controls import (
    PendingTaskControl,
    TaskControlConflict,
    TaskControlNotFound,
    TaskControlService,
)
from automation_tool.control_plane.application.task_event_convergence import (
    TaskEventConvergenceRejected,
    TaskEventConvergenceService,
)
from automation_tool.control_plane.domain import (
    ExecutionAttemptId,
    ExecutionAttemptStatus,
    InstallationId,
    TaskCommandResponseType,
    TaskCommandStatus,
    TaskCommandType,
    TaskId,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyTaskCommandRepository,
    SqlAlchemyTaskEventConvergenceRepository,
    device_credentials,
    device_sessions,
    execution_attempts,
    installation_registration_challenges,
    installations,
    task_actions,
    task_commands,
    task_events,
    tasks,
)
from automation_tool.control_plane.infrastructure.database import (
    task_command_repository as repository_module,
)
from automation_tool.protocol import (
    TaskCommandResultEnvelope,
    TaskEventEnvelope,
    parse_executor_message,
)

NOW = datetime(2026, 7, 18, 19, 0, tzinfo=UTC)
EXECUTOR_ID = "123e4567-e89b-42d3-a456-426614174004"


@dataclass
class FixedClock:
    value: datetime

    def now(self) -> datetime:
        return self.value


async def reset_data(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(task_events))
        await session.execute(delete(task_commands))
        await session.execute(delete(task_actions))
        await session.execute(update(tasks).values(current_attempt_id=None))
        await session.execute(delete(execution_attempts))
        await session.execute(delete(tasks))
        await session.execute(delete(installation_registration_challenges))
        await session.execute(delete(device_sessions))
        await session.execute(delete(device_credentials))
        await session.execute(delete(installations))


async def seed_running_task(
    database: Database,
    *,
    task_status: TaskStatus = TaskStatus.RUNNING,
    attempt_status: ExecutionAttemptStatus = ExecutionAttemptStatus.RUNNING,
) -> tuple[InstallationId, TaskId, ExecutionAttemptId]:
    installation_id = InstallationId.new()
    task_id = TaskId.new()
    attempt_id = ExecutionAttemptId.new()
    async with database.session() as session:
        await session.execute(
            insert(installations).values(
                id=installation_id.uuid,
                device_public_key=secrets.token_bytes(32),
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.execute(
            insert(tasks).values(
                id=task_id.uuid,
                installation_id=installation_id.uuid,
                creation_idempotency_key=f"task:t313:{task_id}",
                status=task_status.value,
                revision=3,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.execute(
            insert(execution_attempts).values(
                id=attempt_id.uuid,
                task_id=task_id.uuid,
                installation_id=installation_id.uuid,
                attempt_number=1,
                status=attempt_status.value,
                revision=2,
                created_at=NOW,
                updated_at=NOW,
                started_at=NOW if attempt_status is ExecutionAttemptStatus.RUNNING else None,
            )
        )
        await session.execute(
            update(tasks)
            .where(tasks.c.id == task_id.uuid)
            .values(current_attempt_id=attempt_id.uuid)
        )
    return installation_id, task_id, attempt_id


def control_ack(command: TaskCommandRecord, *, at: datetime) -> TaskCommandResultEnvelope:
    return TaskCommandResultEnvelope.model_validate(
        {
            "protocol_version": "1.0",
            "message_id": str(TaskId.new()),
            "message_type": "task.control_ack",
            "sent_at": at,
            "deadline_at": at + timedelta(seconds=30),
            "installation_id": str(command.installation_id),
            "executor_id": EXECUTOR_ID,
            "correlation_id": str(command.correlation_id),
            "idempotency_key": f"task:t313:ack:{command.sequence}",
            "sequence": command.sequence,
            "payload": {"acknowledged": True},
            "task_id": str(command.task_id),
            "execution_attempt_id": str(command.execution_attempt_id),
        }
    )


def event(
    installation_id: InstallationId,
    task_id: TaskId,
    attempt_id: ExecutionAttemptId,
    message_type: str,
    *,
    sequence: int,
    at: datetime,
    correlation_id: str,
) -> TaskEventEnvelope:
    parsed = parse_executor_message(
        json.dumps(
            {
                "protocol_version": "1.0",
                "message_id": str(TaskId.new()),
                "message_type": message_type,
                "sent_at": at.isoformat().replace("+00:00", "Z"),
                "deadline_at": (at + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                "installation_id": str(installation_id),
                "executor_id": EXECUTOR_ID,
                "correlation_id": correlation_id,
                "idempotency_key": f"task:t313:{message_type}:{sequence}",
                "sequence": sequence,
                "payload": {},
                "task_id": str(task_id),
                "execution_attempt_id": str(attempt_id),
            },
            separators=(",", ":"),
        )
    )
    assert isinstance(parsed, TaskEventEnvelope)
    return parsed


async def deliver_and_acknowledge(
    repository: SqlAlchemyTaskCommandRepository,
    installation_id: InstallationId,
    *,
    at: datetime,
) -> TaskCommandRecord:
    claimed = await repository.claim_next(
        installation_id=installation_id,
        now=at,
        lease_expires_at=at + timedelta(seconds=5),
        retry_delivered_before=at,
        recover_delivered=False,
    )
    assert claimed is not None
    delivered = await repository.mark_delivered(
        message_id=claimed.message_id,
        expected_revision=claimed.revision,
        delivered_at=at,
    )
    return await repository.acknowledge(
        response=control_ack(delivered, at=at + timedelta(seconds=1)),
        received_at=at + timedelta(seconds=1),
    )


@pytest.mark.asyncio
async def test_control_enqueue_is_atomic_idempotent_scoped_and_projection_free(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id, task_id, attempt_id = await seed_running_task(database)
        repository = SqlAlchemyTaskCommandRepository(database)
        service = TaskControlService(repository=repository, clock=FixedClock(NOW))

        first, replay = await asyncio.gather(
            service.pause(
                installation_id=installation_id,
                task_id=str(task_id),
                idempotency_key="task:t313:pause:atomic",
            ),
            service.pause(
                installation_id=installation_id,
                task_id=str(task_id),
                idempotency_key="task:t313:pause:atomic",
            ),
        )
        assert {first.created, replay.created} == {True, False}
        assert first.command.message_id == replay.command.message_id
        assert first.command.sequence == replay.command.sequence == 1
        assert first.command.command_type is TaskCommandType.TASK_PAUSE

        async with database.session() as session:
            task_row = (
                (
                    await session.execute(
                        select(tasks.c.status, tasks.c.revision).where(tasks.c.id == task_id.uuid)
                    )
                )
                .mappings()
                .one()
            )
            attempt_row = (
                (
                    await session.execute(
                        select(execution_attempts.c.status, execution_attempts.c.revision).where(
                            execution_attempts.c.id == attempt_id.uuid
                        )
                    )
                )
                .mappings()
                .one()
            )
            count = await session.scalar(select(task_commands.c.message_id))
        assert task_row == {"status": "running", "revision": 3}
        assert attempt_row == {"status": "running", "revision": 2}
        assert count == first.command.message_id

        with pytest.raises(TaskControlConflict):
            await service.resume(
                installation_id=installation_id,
                task_id=str(task_id),
                idempotency_key="task:t313:pause:atomic",
            )
        with pytest.raises(TaskControlConflict):
            await service.pause(
                installation_id=installation_id,
                task_id=str(TaskId.new()),
                idempotency_key="task:t313:pause:atomic",
            )
        other_installation, _, _ = await seed_running_task(database)
        with pytest.raises(TaskControlNotFound):
            await service.pause(
                installation_id=other_installation,
                task_id=str(task_id),
                idempotency_key="task:t313:pause:foreign",
            )
        with pytest.raises(TaskControlNotFound):
            await service.pause(
                installation_id=InstallationId.new(),
                task_id=str(task_id),
                idempotency_key="task:t313:pause:missing-installation",
            )

        async with database.session() as session:
            await session.execute(
                update(tasks).where(tasks.c.id == task_id.uuid).values(current_attempt_id=None)
            )
        with pytest.raises(TaskControlConflict):
            await service.pause(
                installation_id=installation_id,
                task_id=str(task_id),
                idempotency_key="task:t313:pause:no-attempt",
            )
        async with database.session() as session:
            await session.execute(
                update(tasks)
                .where(tasks.c.id == task_id.uuid)
                .values(current_attempt_id=attempt_id.uuid)
            )
        with pytest.raises(TaskControlConflict):
            await service.resume(
                installation_id=installation_id,
                task_id=str(task_id),
                idempotency_key="task:t313:resume:wrong-state",
            )

        async with database.session() as session:
            await session.execute(
                update(task_commands)
                .where(task_commands.c.message_id == first.command.message_id)
                .values(sequence=(1 << 53) - 1)
            )
        with pytest.raises(TaskControlConflict):
            await service.pause(
                installation_id=installation_id,
                task_id=str(task_id),
                idempotency_key="task:t313:pause:sequence-exhausted",
            )
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_ack_is_required_but_only_the_following_event_projects_pause_and_resume(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id, task_id, attempt_id = await seed_running_task(database)
        commands = SqlAlchemyTaskCommandRepository(database)
        pause = await TaskControlService(
            repository=commands,
            clock=FixedClock(NOW + timedelta(seconds=1)),
        ).pause(
            installation_id=installation_id,
            task_id=str(task_id),
            idempotency_key="task:t313:pause:confirmed",
        )
        pause_event = event(
            installation_id,
            task_id,
            attempt_id,
            "task.paused",
            sequence=1,
            at=NOW + timedelta(seconds=3),
            correlation_id=str(pause.command.correlation_id),
        )
        convergence = TaskEventConvergenceService(
            repository=SqlAlchemyTaskEventConvergenceRepository(database),
            clock=FixedClock(NOW + timedelta(seconds=3)),
        )

        with pytest.raises(TaskEventConvergenceRejected):
            await convergence.receive(pause_event)
        acknowledged_pause = await deliver_and_acknowledge(
            commands,
            installation_id,
            at=NOW + timedelta(seconds=2),
        )
        assert acknowledged_pause.status is TaskCommandStatus.ACKNOWLEDGED
        assert acknowledged_pause.response_type is TaskCommandResponseType.TASK_CONTROL_ACK
        async with database.session() as session:
            assert (
                await session.scalar(select(tasks.c.status).where(tasks.c.id == task_id.uuid))
                == TaskStatus.RUNNING.value
            )

        paused = await convergence.receive(pause_event)
        assert paused.snapshot.status is TaskStatus.PAUSED

        resume = await TaskControlService(
            repository=commands,
            clock=FixedClock(NOW + timedelta(seconds=4)),
        ).resume(
            installation_id=installation_id,
            task_id=str(task_id),
            idempotency_key="task:t313:resume:confirmed",
        )
        assert resume.command.sequence == pause.command.sequence + 1
        resume_event = event(
            installation_id,
            task_id,
            attempt_id,
            "task.resumed",
            sequence=2,
            at=NOW + timedelta(seconds=6),
            correlation_id=str(resume.command.correlation_id),
        )
        resume_convergence = TaskEventConvergenceService(
            repository=SqlAlchemyTaskEventConvergenceRepository(database),
            clock=FixedClock(NOW + timedelta(seconds=6)),
        )
        with pytest.raises(TaskEventConvergenceRejected):
            await resume_convergence.receive(resume_event)
        acknowledged_resume = await deliver_and_acknowledge(
            commands,
            installation_id,
            at=NOW + timedelta(seconds=5),
        )
        assert acknowledged_resume.status is TaskCommandStatus.ACKNOWLEDGED
        resumed = await resume_convergence.receive(resume_event)
        assert resumed.snapshot.status is TaskStatus.RUNNING

        async with database.session() as session:
            final_task = (
                (
                    await session.execute(
                        select(
                            tasks.c.status,
                            tasks.c.revision,
                            tasks.c.last_event_sequence,
                        ).where(tasks.c.id == task_id.uuid)
                    )
                )
                .mappings()
                .one()
            )
            final_attempt = await session.scalar(
                select(execution_attempts.c.status).where(
                    execution_attempts.c.id == attempt_id.uuid
                )
            )
        assert final_task == {
            "status": TaskStatus.RUNNING.value,
            "revision": 5,
            "last_event_sequence": 2,
        }
        assert final_attempt == ExecutionAttemptStatus.RUNNING.value
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_control_rejects_mismatched_projection_and_repository_inputs(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id, task_id, _ = await seed_running_task(
            database,
            task_status=TaskStatus.RUNNING,
            attempt_status=ExecutionAttemptStatus.PAUSED,
        )
        repository = SqlAlchemyTaskCommandRepository(database)
        service = TaskControlService(repository=repository, clock=FixedClock(NOW))
        with pytest.raises(TaskControlConflict):
            await service.pause(
                installation_id=installation_id,
                task_id=str(task_id),
                idempotency_key="task:t313:pause:mismatch",
            )
        with pytest.raises(TaskControlConflict):
            await repository.enqueue_control(object())  # type: ignore[arg-type]
        with pytest.raises(TaskCommandDeliveryRejected):
            await repository.acknowledge(
                response=object(),  # type: ignore[arg-type]
                received_at=NOW,
            )

        async with database.session() as session:
            await session.execute(
                update(execution_attempts)
                .where(execution_attempts.c.task_id == task_id.uuid)
                .values(status=ExecutionAttemptStatus.RUNNING.value)
            )

        def reject_insert(_table: object) -> object:
            raise IntegrityError("private statement", {}, RuntimeError("private database"))

        monkeypatch.setattr(repository_module, "insert", reject_insert)
        with pytest.raises(TaskControlConflict) as caught:
            await repository.enqueue_control(
                PendingTaskControl(
                    message_id=uuid4(),
                    correlation_id=uuid4(),
                    installation_id=installation_id,
                    task_id=task_id,
                    command_type=TaskCommandType.TASK_PAUSE,
                    idempotency_key="task:t313:pause:integrity",
                    created_at=NOW,
                    deadline_at=NOW + timedelta(minutes=1),
                )
            )
        assert caught.value.__cause__ is None
    finally:
        await reset_data(database)
        await database.close()
