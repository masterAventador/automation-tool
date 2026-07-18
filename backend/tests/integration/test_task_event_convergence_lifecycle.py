from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, insert, select, update

from automation_tool.control_plane.application.task_event_convergence import (
    PendingTaskEvent,
    TaskEventConvergenceRejected,
    TaskEventConvergenceService,
    TaskEventConvergenceUnavailable,
)
from automation_tool.control_plane.domain import (
    ActionId,
    ActionOutcome,
    ActionStatus,
    ExecutionAttemptId,
    ExecutionAttemptStatus,
    InstallationId,
    TaskCommandResponseType,
    TaskCommandStatus,
    TaskCommandType,
    TaskEventType,
    TaskId,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
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
from automation_tool.protocol import TaskEventEnvelope, parse_executor_message

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
EXECUTOR_ID = "123e4567-e89b-42d3-a456-426614174004"


@dataclass
class MutableClock:
    value: datetime = NOW

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


async def seed_chain(
    database: Database,
    *,
    installation_id: InstallationId | None = None,
    task_status: TaskStatus = TaskStatus.QUEUED,
    attempt_status: ExecutionAttemptStatus = ExecutionAttemptStatus.ACCEPTED,
    action_status: ActionStatus = ActionStatus.DISPATCHED,
) -> tuple[InstallationId, TaskId, ExecutionAttemptId, ActionId]:
    target_installation_id = installation_id or InstallationId.new()
    task_id = TaskId.new()
    attempt_id = ExecutionAttemptId.new()
    action_id = ActionId.new()
    async with database.session() as session:
        if installation_id is None:
            await session.execute(
                insert(installations).values(
                    id=target_installation_id.uuid,
                    device_public_key=secrets.token_bytes(32),
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        await session.execute(
            insert(tasks).values(
                id=task_id.uuid,
                installation_id=target_installation_id.uuid,
                creation_idempotency_key=f"task:t311:{task_id}",
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
                installation_id=target_installation_id.uuid,
                attempt_number=1,
                status=attempt_status.value,
                revision=2,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.execute(
            insert(task_actions).values(
                id=action_id.uuid,
                execution_attempt_id=attempt_id.uuid,
                task_id=task_id.uuid,
                installation_id=target_installation_id.uuid,
                ordinal=1,
                status=action_status.value,
                outcome=ActionOutcome.PENDING.value,
                revision=3,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.execute(
            update(tasks)
            .where(tasks.c.id == task_id.uuid)
            .values(current_attempt_id=attempt_id.uuid)
        )
    return target_installation_id, task_id, attempt_id, action_id


async def seed_acknowledged_control(
    database: Database,
    *,
    installation_id: InstallationId,
    task_id: TaskId,
    attempt_id: ExecutionAttemptId,
    message_type: str,
) -> None:
    command_type = {
        "task.paused": TaskCommandType.TASK_PAUSE,
        "task.resumed": TaskCommandType.TASK_RESUME,
        "task.cancelled": TaskCommandType.TASK_CANCEL,
    }[message_type]
    async with database.session() as session:
        await session.execute(
            insert(task_commands).values(
                message_id=TaskId.new().uuid,
                correlation_id=UUID("323e4567-e89b-42d3-a456-426614174002"),
                installation_id=installation_id.uuid,
                task_id=task_id.uuid,
                execution_attempt_id=attempt_id.uuid,
                sequence=1,
                command_type=command_type.value,
                status=TaskCommandStatus.ACKNOWLEDGED.value,
                idempotency_key=f"task:t311:control:{task_id}",
                revision=4,
                delivery_attempts=1,
                next_delivery_at=None,
                lease_expires_at=None,
                delivered_at=NOW,
                acknowledged_at=NOW,
                response_message_id=TaskId.new().uuid,
                response_type=TaskCommandResponseType.TASK_CONTROL_ACK.value,
                deadline_at=NOW + timedelta(minutes=5),
                created_at=NOW,
                updated_at=NOW,
            )
        )


def event(
    installation_id: InstallationId,
    task_id: TaskId,
    attempt_id: ExecutionAttemptId,
    message_type: str,
    *,
    sequence: int,
    message_id: str | None = None,
    idempotency_key: str | None = None,
    payload: object | None = None,
    correlation_id: str = "323e4567-e89b-42d3-a456-426614174002",
) -> TaskEventEnvelope:
    parsed = parse_executor_message(
        json.dumps(
            {
                "protocol_version": "1.0",
                "message_id": message_id or f"423e4567-e89b-42d3-a456-{sequence:012d}",
                "message_type": message_type,
                "sent_at": NOW.isoformat().replace("+00:00", "Z"),
                "deadline_at": (NOW + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
                "installation_id": str(installation_id),
                "executor_id": EXECUTOR_ID,
                "correlation_id": correlation_id,
                "idempotency_key": idempotency_key or f"task:t311:{message_type}:{sequence}",
                "sequence": sequence,
                "payload": {} if payload is None else payload,
                "task_id": str(task_id),
                "execution_attempt_id": str(attempt_id),
            },
            separators=(",", ":"),
        )
    )
    assert isinstance(parsed, TaskEventEnvelope)
    return parsed


def service(database: Database) -> TaskEventConvergenceService:
    return TaskEventConvergenceService(
        repository=SqlAlchemyTaskEventConvergenceRepository(database),
        clock=MutableClock(),
    )


@pytest.mark.asyncio
async def test_ordered_events_atomically_project_task_attempt_action_and_timeline(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id, task_id, attempt_id, action_id = await seed_chain(database)
        convergence = service(database)
        messages = (
            event(installation_id, task_id, attempt_id, "task.started", sequence=1),
            event(
                installation_id,
                task_id,
                attempt_id,
                "step.started",
                sequence=2,
                payload={"action_id": str(action_id)},
            ),
            event(
                installation_id,
                task_id,
                attempt_id,
                "step.progress",
                sequence=3,
                payload={"action_id": str(action_id), "progress_percent": 50},
            ),
            event(
                installation_id,
                task_id,
                attempt_id,
                "step.completed",
                sequence=4,
                payload={"action_id": str(action_id)},
            ),
            event(installation_id, task_id, attempt_id, "task.completed", sequence=5),
        )

        results = [await convergence.receive(message) for message in messages]

        assert [result.snapshot.revision for result in results] == [4, 5, 6, 7, 8]
        assert [result.snapshot.last_event_sequence for result in results] == [1, 2, 3, 4, 5]
        assert all(result.duplicate is False for result in results)
        async with database.session() as session:
            task_row = (
                (await session.execute(select(tasks).where(tasks.c.id == task_id.uuid)))
                .mappings()
                .one()
            )
            attempt_row = (
                (
                    await session.execute(
                        select(execution_attempts).where(execution_attempts.c.id == attempt_id.uuid)
                    )
                )
                .mappings()
                .one()
            )
            action_row = (
                (
                    await session.execute(
                        select(task_actions).where(task_actions.c.id == action_id.uuid)
                    )
                )
                .mappings()
                .one()
            )
            event_rows = (
                (
                    await session.execute(
                        select(task_events)
                        .where(task_events.c.task_id == task_id.uuid)
                        .order_by(task_events.c.sequence)
                    )
                )
                .mappings()
                .all()
            )
        assert task_row["status"] == TaskStatus.SUCCEEDED.value
        assert task_row["revision"] == 8
        assert task_row["last_event_sequence"] == 5
        assert attempt_row["status"] == ExecutionAttemptStatus.SUCCEEDED.value
        assert attempt_row["revision"] == 4
        assert attempt_row["started_at"] == NOW
        assert attempt_row["finished_at"] == NOW
        assert action_row["status"] == ActionStatus.VERIFIED.value
        assert action_row["outcome"] == ActionOutcome.SUCCEEDED.value
        assert action_row["revision"] == 4
        assert action_row["finished_at"] == NOW
        assert [row["event_type"] for row in event_rows] == [
            TaskEventType.TASK_STARTED.value,
            TaskEventType.STEP_STARTED.value,
            TaskEventType.STEP_PROGRESS.value,
            TaskEventType.STEP_COMPLETED.value,
            TaskEventType.TASK_COMPLETED.value,
        ]
        assert [row["task_revision"] for row in event_rows] == [4, 5, 6, 7, 8]
        assert [row["progress_percent"] for row in event_rows] == [None, None, 50, None, None]
        assert all(len(row["source_fingerprint"]) == 32 for row in event_rows)
        assert all(row["source_idempotency_key"] for row in event_rows)
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_exact_replays_are_idempotent_but_conflicts_gaps_and_late_events_reject(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id, task_id, attempt_id, _ = await seed_chain(database)
        convergence = service(database)
        started = event(installation_id, task_id, attempt_id, "task.started", sequence=1)
        progress = event(
            installation_id,
            task_id,
            attempt_id,
            "step.progress",
            sequence=2,
            payload={"progress_percent": 25},
        )
        await convergence.receive(started)
        original = await convergence.receive(progress)

        exact = await convergence.receive(progress)
        same_intent_new_message = await convergence.receive(
            event(
                installation_id,
                task_id,
                attempt_id,
                "step.progress",
                sequence=2,
                message_id="523e4567-e89b-42d3-a456-426614174002",
                idempotency_key=str(progress.idempotency_key),
                payload={"progress_percent": 25},
            )
        )
        assert exact.duplicate is True
        assert same_intent_new_message.duplicate is True
        assert exact.snapshot == original.snapshot

        conflicts = (
            event(
                installation_id,
                task_id,
                attempt_id,
                "step.progress",
                sequence=2,
                message_id=str(progress.message_id),
                idempotency_key="task:t311:changed-message-intent",
                payload={"progress_percent": 26},
            ),
            event(
                installation_id,
                task_id,
                attempt_id,
                "step.progress",
                sequence=2,
                message_id="623e4567-e89b-42d3-a456-426614174002",
                idempotency_key=str(progress.idempotency_key),
                payload={"progress_percent": 26},
            ),
            event(
                installation_id,
                task_id,
                attempt_id,
                "step.progress",
                sequence=2,
                message_id="723e4567-e89b-42d3-a456-426614174002",
                idempotency_key="task:t311:sequence-conflict",
                payload={"progress_percent": 25},
            ),
            event(
                installation_id,
                task_id,
                attempt_id,
                "step.progress",
                sequence=4,
                payload={"progress_percent": 50},
            ),
            event(
                installation_id,
                task_id,
                attempt_id,
                "task.started",
                sequence=1,
                message_id="823e4567-e89b-42d3-a456-426614174001",
                idempotency_key="task:t311:late",
            ),
        )
        for conflicting in conflicts:
            with pytest.raises(TaskEventConvergenceRejected):
                await convergence.receive(conflicting)

        async with database.session() as session:
            snapshot = (
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
            count = len(
                (
                    await session.execute(
                        select(task_events.c.sequence).where(task_events.c.task_id == task_id.uuid)
                    )
                ).all()
            )
        assert snapshot == {
            "status": TaskStatus.RUNNING.value,
            "revision": 5,
            "last_event_sequence": 2,
        }
        assert count == 2
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_scope_state_and_action_conflicts_roll_back_the_entire_event(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id, task_id, attempt_id, _ = await seed_chain(
            database,
            action_status=ActionStatus.PLANNED,
        )
        _, other_task, other_attempt, other_action = await seed_chain(database)
        convergence = service(database)
        invalid = (
            event(installation_id, other_task, other_attempt, "task.started", sequence=1),
            event(installation_id, task_id, other_attempt, "task.started", sequence=1),
            event(
                installation_id,
                task_id,
                attempt_id,
                "step.completed",
                sequence=1,
                payload={"action_id": str(other_action)},
            ),
        )
        for message in invalid:
            with pytest.raises(TaskEventConvergenceRejected):
                await convergence.receive(message)

        async with database.session() as session:
            task_row = (
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
            own_events = (
                await session.execute(
                    select(task_events.c.sequence).where(task_events.c.task_id == task_id.uuid)
                )
            ).all()
        assert task_row == {
            "status": TaskStatus.QUEUED.value,
            "revision": 3,
            "last_event_sequence": 0,
        }
        assert own_events == []
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_concurrent_next_events_have_one_atomic_revision_winner(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id, task_id, attempt_id, _ = await seed_chain(database)
        convergence = service(database)
        await convergence.receive(
            event(installation_id, task_id, attempt_id, "task.started", sequence=1)
        )
        competitors = (
            event(
                installation_id,
                task_id,
                attempt_id,
                "step.progress",
                sequence=2,
                message_id="923e4567-e89b-42d3-a456-426614174001",
                idempotency_key="task:t311:concurrent:a",
                payload={"progress_percent": 25},
            ),
            event(
                installation_id,
                task_id,
                attempt_id,
                "step.progress",
                sequence=2,
                message_id="a23e4567-e89b-42d3-a456-426614174001",
                idempotency_key="task:t311:concurrent:b",
                payload={"progress_percent": 50},
            ),
        )

        outcomes = await asyncio.gather(
            *(convergence.receive(message) for message in competitors),
            return_exceptions=True,
        )

        assert sum(not isinstance(outcome, Exception) for outcome in outcomes) == 1
        assert sum(isinstance(outcome, TaskEventConvergenceRejected) for outcome in outcomes) == 1
        async with database.session() as session:
            snapshot = (
                (
                    await session.execute(
                        select(tasks.c.revision, tasks.c.last_event_sequence).where(
                            tasks.c.id == task_id.uuid
                        )
                    )
                )
                .mappings()
                .one()
            )
            sequences = list(
                await session.scalars(
                    select(task_events.c.sequence)
                    .where(task_events.c.task_id == task_id.uuid)
                    .order_by(task_events.c.sequence)
                )
            )
        assert snapshot == {"revision": 5, "last_event_sequence": 2}
        assert sequences == [1, 2]
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("task_status", "attempt_status", "message_type", "target_task", "target_attempt"),
    (
        (
            TaskStatus.AWAITING_DEVICE,
            ExecutionAttemptStatus.ACCEPTED,
            "session.login_required",
            TaskStatus.AWAITING_PLATFORM_LOGIN,
            ExecutionAttemptStatus.AWAITING_HUMAN,
        ),
        (
            TaskStatus.RUNNING,
            ExecutionAttemptStatus.RUNNING,
            "handoff.requested",
            TaskStatus.AWAITING_HUMAN,
            ExecutionAttemptStatus.AWAITING_HUMAN,
        ),
        (
            TaskStatus.RUNNING,
            ExecutionAttemptStatus.RUNNING,
            "task.paused",
            TaskStatus.PAUSED,
            ExecutionAttemptStatus.PAUSED,
        ),
        (
            TaskStatus.PAUSED,
            ExecutionAttemptStatus.PAUSED,
            "task.resumed",
            TaskStatus.RUNNING,
            ExecutionAttemptStatus.RUNNING,
        ),
        (
            TaskStatus.CANCELLING,
            ExecutionAttemptStatus.CANCELLING,
            "task.cancelled",
            TaskStatus.CANCELLED,
            ExecutionAttemptStatus.CANCELLED,
        ),
        (
            TaskStatus.RUNNING,
            ExecutionAttemptStatus.RUNNING,
            "task.partially_completed",
            TaskStatus.PARTIALLY_SUCCEEDED,
            ExecutionAttemptStatus.PARTIALLY_SUCCEEDED,
        ),
        (
            TaskStatus.RUNNING,
            ExecutionAttemptStatus.RUNNING,
            "task.failed",
            TaskStatus.FAILED,
            ExecutionAttemptStatus.FAILED,
        ),
        (
            TaskStatus.RUNNING,
            ExecutionAttemptStatus.RUNNING,
            "task.outcome_uncertain",
            TaskStatus.OUTCOME_UNCERTAIN,
            ExecutionAttemptStatus.OUTCOME_UNCERTAIN,
        ),
    ),
)
async def test_each_status_event_projects_only_an_explicit_legal_transition(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
    task_status: TaskStatus,
    attempt_status: ExecutionAttemptStatus,
    message_type: str,
    target_task: TaskStatus,
    target_attempt: ExecutionAttemptStatus,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id, task_id, attempt_id, _ = await seed_chain(
            database,
            task_status=task_status,
            attempt_status=attempt_status,
        )
        if message_type in {"task.paused", "task.resumed", "task.cancelled"}:
            await seed_acknowledged_control(
                database,
                installation_id=installation_id,
                task_id=task_id,
                attempt_id=attempt_id,
                message_type=message_type,
            )

        result = await service(database).receive(
            event(installation_id, task_id, attempt_id, message_type, sequence=1)
        )

        assert result.snapshot.status is target_task
        async with database.session() as session:
            persisted_attempt = await session.scalar(
                select(execution_attempts.c.status).where(
                    execution_attempts.c.id == attempt_id.uuid
                )
            )
        assert persisted_attempt == target_attempt.value
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_illegal_task_attempt_and_action_states_are_rejected_before_append(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        cases: tuple[
            tuple[
                TaskStatus,
                ExecutionAttemptStatus,
                ActionStatus,
                str,
                dict[str, object],
            ],
            ...,
        ] = (
            (
                TaskStatus.DRAFT,
                ExecutionAttemptStatus.ACCEPTED,
                ActionStatus.DISPATCHED,
                "task.started",
                {},
            ),
            (
                TaskStatus.RUNNING,
                ExecutionAttemptStatus.ACCEPTED,
                ActionStatus.DISPATCHED,
                "step.progress",
                {"progress_percent": 25},
            ),
            (
                TaskStatus.RUNNING,
                ExecutionAttemptStatus.ACCEPTED,
                ActionStatus.DISPATCHED,
                "task.paused",
                {},
            ),
            (
                TaskStatus.RUNNING,
                ExecutionAttemptStatus.RUNNING,
                ActionStatus.PLANNED,
                "step.progress",
                {"progress_percent": 25},
            ),
            (
                TaskStatus.RUNNING,
                ExecutionAttemptStatus.RUNNING,
                ActionStatus.PLANNED,
                "step.started",
                {},
            ),
            (
                TaskStatus.RUNNING,
                ExecutionAttemptStatus.RUNNING,
                ActionStatus.PLANNED,
                "step.completed",
                {},
            ),
        )
        for task_status, attempt_status, action_status, message_type, base_payload in cases:
            await reset_data(database)
            installation_id, task_id, attempt_id, action_id = await seed_chain(
                database,
                task_status=task_status,
                attempt_status=attempt_status,
                action_status=action_status,
            )
            if message_type == "task.paused":
                await seed_acknowledged_control(
                    database,
                    installation_id=installation_id,
                    task_id=task_id,
                    attempt_id=attempt_id,
                    message_type=message_type,
                )
            payload = dict(base_payload)
            if message_type.startswith("step."):
                payload["action_id"] = str(action_id)
            with pytest.raises(TaskEventConvergenceRejected):
                await service(database).receive(
                    event(
                        installation_id,
                        task_id,
                        attempt_id,
                        message_type,
                        sequence=1,
                        payload=payload,
                    )
                )

        await reset_data(database)
        installation_id, task_id, attempt_id, action_id = await seed_chain(
            database,
            task_status=TaskStatus.RUNNING,
            attempt_status=ExecutionAttemptStatus.RUNNING,
            action_status=ActionStatus.PREPARED,
        )
        transitioned = await service(database).receive(
            event(
                installation_id,
                task_id,
                attempt_id,
                "step.started",
                sequence=1,
                payload={"action_id": str(action_id)},
            )
        )
        assert transitioned.snapshot.last_event_sequence == 1
        async with database.session() as session:
            action_row = (
                (
                    await session.execute(
                        select(task_actions.c.status, task_actions.c.revision).where(
                            task_actions.c.id == action_id.uuid
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert action_row == {"status": ActionStatus.DISPATCHED.value, "revision": 4}
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_stale_server_times_and_malicious_cross_key_replay_are_rejected(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        for stale_table in (tasks, execution_attempts, task_actions):
            await reset_data(database)
            installation_id, task_id, attempt_id, action_id = await seed_chain(
                database,
                task_status=(
                    TaskStatus.RUNNING if stale_table is task_actions else TaskStatus.QUEUED
                ),
                attempt_status=(
                    ExecutionAttemptStatus.RUNNING
                    if stale_table is task_actions
                    else ExecutionAttemptStatus.ACCEPTED
                ),
            )
            async with database.session() as session:
                identifier = {
                    tasks: tasks.c.id == task_id.uuid,
                    execution_attempts: execution_attempts.c.id == attempt_id.uuid,
                    task_actions: task_actions.c.id == action_id.uuid,
                }[stale_table]
                await session.execute(
                    update(stale_table)
                    .where(identifier)
                    .values(updated_at=NOW + timedelta(seconds=1))
                )
            message_type = "task.started" if stale_table is not task_actions else "step.progress"
            payload = (
                {}
                if stale_table is not task_actions
                else {"action_id": str(action_id), "progress_percent": 25}
            )
            with pytest.raises(TaskEventConvergenceRejected):
                await service(database).receive(
                    event(
                        installation_id,
                        task_id,
                        attempt_id,
                        message_type,
                        sequence=1,
                        payload=payload,
                    )
                )

        await reset_data(database)
        installation_id, task_id, attempt_id, _ = await seed_chain(database)
        convergence = service(database)
        started = event(installation_id, task_id, attempt_id, "task.started", sequence=1)
        progress = event(
            installation_id,
            task_id,
            attempt_id,
            "step.progress",
            sequence=2,
            payload={"progress_percent": 25},
        )
        await convergence.receive(started)
        await convergence.receive(progress)
        async with database.session() as session:
            second_fingerprint = await session.scalar(
                select(task_events.c.source_fingerprint).where(
                    task_events.c.task_id == task_id.uuid,
                    task_events.c.sequence == 2,
                )
            )
            await session.execute(
                update(task_events)
                .where(
                    task_events.c.task_id == task_id.uuid,
                    task_events.c.sequence == 1,
                )
                .values(source_fingerprint=second_fingerprint)
            )
        crossed = event(
            installation_id,
            task_id,
            attempt_id,
            "step.progress",
            sequence=2,
            message_id=str(started.message_id),
            idempotency_key=str(progress.idempotency_key),
            payload={"progress_percent": 25},
        )
        with pytest.raises(TaskEventConvergenceRejected):
            await convergence.receive(crossed)

        await reset_data(database)
        installation_id, task_id, attempt_id, action_id = await seed_chain(
            database,
            task_status=TaskStatus.RUNNING,
            attempt_status=ExecutionAttemptStatus.RUNNING,
        )
        source = event(
            installation_id,
            task_id,
            attempt_id,
            "step.started",
            sequence=1,
            payload={"action_id": str(action_id)},
        )
        with pytest.raises(TaskEventConvergenceRejected):
            await SqlAlchemyTaskEventConvergenceRepository(database).converge(
                PendingTaskEvent(
                    message=source,
                    event_type=TaskEventType.STEP_STARTED,
                    target_task_status=None,
                    target_attempt_status=None,
                    action_id=action_id,
                    target_action_status=None,
                    target_action_outcome=None,
                    progress_percent=None,
                    source_fingerprint=b"x" * 32,
                    received_at=NOW,
                )
            )
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_cross_task_idempotency_race_has_one_winner_and_database_failure_is_safe(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        installation_id, first_task, first_attempt, _ = await seed_chain(database)
        _, second_task, second_attempt, _ = await seed_chain(
            database,
            installation_id=installation_id,
        )
        shared_key = "task:t311:cross-task-race"
        competitors = (
            event(
                installation_id,
                first_task,
                first_attempt,
                "task.started",
                sequence=1,
                message_id="b23e4567-e89b-42d3-a456-426614174001",
                idempotency_key=shared_key,
            ),
            event(
                installation_id,
                second_task,
                second_attempt,
                "task.started",
                sequence=1,
                message_id="c23e4567-e89b-42d3-a456-426614174001",
                idempotency_key=shared_key,
            ),
        )

        outcomes = await asyncio.gather(
            *(service(database).receive(message) for message in competitors),
            return_exceptions=True,
        )

        assert sum(not isinstance(outcome, Exception) for outcome in outcomes) == 1
        assert sum(isinstance(outcome, TaskEventConvergenceRejected) for outcome in outcomes) == 1
    finally:
        await reset_data(database)
        await database.close()

    unavailable_database = Database.from_url(
        "postgresql+asyncpg://unused:unused@127.0.0.1:9/unused",
        connect_timeout_seconds=0.05,
    )
    try:
        repository = SqlAlchemyTaskEventConvergenceRepository(unavailable_database)
        with pytest.raises(TaskEventConvergenceRejected):
            await repository.converge(cast(PendingTaskEvent, object()))
        with pytest.raises(TaskEventConvergenceRejected):
            SqlAlchemyTaskEventConvergenceRepository(cast(Database, object()))

        installation_id = InstallationId.new()
        task_id = TaskId.new()
        attempt_id = ExecutionAttemptId.new()
        with pytest.raises(TaskEventConvergenceUnavailable) as captured:
            await service(unavailable_database).receive(
                event(
                    installation_id,
                    task_id,
                    attempt_id,
                    "task.started",
                    sequence=1,
                )
            )
        assert type(captured.value).__name__ == "TaskEventConvergenceUnavailable"
        assert "unused" not in str(captured.value)
    finally:
        await unavailable_database.close()
