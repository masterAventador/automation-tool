"""Atomic PostgreSQL convergence for Executor Task events and snapshots."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import desc, insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from automation_tool.control_plane.application.task_event_convergence import (
    PendingTaskEvent,
    TaskEventConvergenceRejected,
    TaskEventConvergenceResult,
    TaskEventConvergenceUnavailable,
)
from automation_tool.control_plane.domain import (
    TERMINAL_EXECUTION_ATTEMPT_STATUSES,
    ActionOutcome,
    ActionStatus,
    ExecutionAttemptStatus,
    InvalidTaskTransition,
    TaskCommandResponseType,
    TaskCommandStatus,
    TaskCommandType,
    TaskEventVersion,
    TaskId,
    TaskSnapshotProjection,
    TaskStateMachine,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database.schema import (
    execution_attempts,
    task_actions,
    task_commands,
    task_events,
    tasks,
)
from automation_tool.control_plane.infrastructure.database.session import Database

_CONTROL_EVENT_COMMANDS = {
    "task.paused": frozenset({TaskCommandType.TASK_PAUSE}),
    "task.resumed": frozenset({TaskCommandType.TASK_RESUME}),
}
_PAUSE_RESUME_COMMANDS = frozenset({TaskCommandType.TASK_PAUSE, TaskCommandType.TASK_RESUME})
_TERMINATION_COMMANDS = frozenset(
    {TaskCommandType.TASK_CANCEL, TaskCommandType.TASK_EMERGENCY_STOP}
)

_STEP_EVENT_TYPES = frozenset(
    {
        "step.started",
        "step.progress",
        "step.completed",
        "step.failed",
    }
)
_STEP_TASK_STATUSES = frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLING})
_STEP_ATTEMPT_STATUSES = frozenset(
    {ExecutionAttemptStatus.RUNNING, ExecutionAttemptStatus.CANCELLING}
)
_ATTEMPT_TRANSITIONS: dict[ExecutionAttemptStatus, frozenset[ExecutionAttemptStatus]] = {
    ExecutionAttemptStatus.PENDING: frozenset(
        {ExecutionAttemptStatus.RUNNING, ExecutionAttemptStatus.AWAITING_HUMAN}
    ),
    ExecutionAttemptStatus.OFFERED: frozenset(
        {ExecutionAttemptStatus.RUNNING, ExecutionAttemptStatus.AWAITING_HUMAN}
    ),
    ExecutionAttemptStatus.ACCEPTED: frozenset(
        {ExecutionAttemptStatus.RUNNING, ExecutionAttemptStatus.AWAITING_HUMAN}
    ),
    ExecutionAttemptStatus.RUNNING: frozenset(
        {
            ExecutionAttemptStatus.PAUSED,
            ExecutionAttemptStatus.AWAITING_HUMAN,
            ExecutionAttemptStatus.SUCCEEDED,
            ExecutionAttemptStatus.PARTIALLY_SUCCEEDED,
            ExecutionAttemptStatus.FAILED,
            ExecutionAttemptStatus.CANCELLED,
            ExecutionAttemptStatus.OUTCOME_UNCERTAIN,
        }
    ),
    ExecutionAttemptStatus.PAUSED: frozenset(
        {
            ExecutionAttemptStatus.RUNNING,
            ExecutionAttemptStatus.AWAITING_HUMAN,
            ExecutionAttemptStatus.CANCELLED,
            ExecutionAttemptStatus.OUTCOME_UNCERTAIN,
        }
    ),
    ExecutionAttemptStatus.AWAITING_HUMAN: frozenset(
        {
            ExecutionAttemptStatus.RUNNING,
            ExecutionAttemptStatus.FAILED,
            ExecutionAttemptStatus.CANCELLED,
            ExecutionAttemptStatus.OUTCOME_UNCERTAIN,
        }
    ),
    ExecutionAttemptStatus.CANCELLING: frozenset(
        {
            ExecutionAttemptStatus.SUCCEEDED,
            ExecutionAttemptStatus.PARTIALLY_SUCCEEDED,
            ExecutionAttemptStatus.FAILED,
            ExecutionAttemptStatus.CANCELLED,
            ExecutionAttemptStatus.OUTCOME_UNCERTAIN,
        }
    ),
    ExecutionAttemptStatus.SUCCEEDED: frozenset(),
    ExecutionAttemptStatus.PARTIALLY_SUCCEEDED: frozenset(),
    ExecutionAttemptStatus.FAILED: frozenset(),
    ExecutionAttemptStatus.CANCELLED: frozenset(),
    ExecutionAttemptStatus.REJECTED: frozenset(),
    ExecutionAttemptStatus.EXPIRED: frozenset(),
    ExecutionAttemptStatus.OUTCOME_UNCERTAIN: frozenset(),
}


def _snapshot(row: RowMapping) -> TaskSnapshotProjection:
    return TaskSnapshotProjection(
        task_id=TaskId.parse(row["id"]),
        status=TaskStatus(cast(str, row["status"])),
        revision=cast(int, row["revision"]),
        last_event_sequence=cast(int, row["last_event_sequence"]),
        updated_at=cast(datetime, row["updated_at"]),
    )


def _event_identity(row: RowMapping) -> tuple[object, object]:
    return row["task_id"], row["sequence"]


def _next_task_status(current: TaskStatus, pending: PendingTaskEvent) -> TaskStatus:
    target = pending.target_task_status
    if target is None:
        if (
            pending.message.message_type not in _STEP_EVENT_TYPES
            or current not in _STEP_TASK_STATUSES
        ):
            raise TaskEventConvergenceRejected
        return current
    try:
        return TaskStateMachine.transition(current, target)
    except InvalidTaskTransition:
        raise TaskEventConvergenceRejected from None


def _next_attempt_status(
    current: ExecutionAttemptStatus,
    pending: PendingTaskEvent,
) -> ExecutionAttemptStatus:
    target = pending.target_attempt_status
    if target is None:
        if (
            pending.message.message_type not in _STEP_EVENT_TYPES
            or current not in _STEP_ATTEMPT_STATUSES
        ):
            raise TaskEventConvergenceRejected
        return current
    if target not in _ATTEMPT_TRANSITIONS[current]:
        raise TaskEventConvergenceRejected
    return target


def _validate_action(
    row: RowMapping, pending: PendingTaskEvent
) -> tuple[ActionStatus, ActionOutcome]:
    current_status = ActionStatus(cast(str, row["status"]))
    current_outcome = ActionOutcome(cast(str, row["outcome"]))
    target_status = pending.target_action_status
    target_outcome = pending.target_action_outcome
    if pending.message.message_type == "step.progress":
        if (
            current_status is not ActionStatus.DISPATCHED
            or current_outcome is not ActionOutcome.PENDING
        ):
            raise TaskEventConvergenceRejected
        return current_status, current_outcome
    if target_status is ActionStatus.DISPATCHED:
        if (
            current_status not in {ActionStatus.PREPARED, ActionStatus.DISPATCHED}
            or current_outcome is not ActionOutcome.PENDING
        ):
            raise TaskEventConvergenceRejected
        return target_status, current_outcome
    if target_status is ActionStatus.VERIFIED and target_outcome in {
        ActionOutcome.SUCCEEDED,
        ActionOutcome.FAILED,
    }:
        if (
            current_status is not ActionStatus.DISPATCHED
            or current_outcome is not ActionOutcome.PENDING
        ):
            raise TaskEventConvergenceRejected
        return target_status, target_outcome
    raise TaskEventConvergenceRejected


class SqlAlchemyTaskEventConvergenceRepository:
    """Lock one Task and atomically append its next event and all bound projections."""

    def __init__(self, database: Database) -> None:
        if not isinstance(database, Database):
            raise TaskEventConvergenceRejected
        self._database = database

    async def converge(self, pending: PendingTaskEvent) -> TaskEventConvergenceResult:
        if not isinstance(pending, PendingTaskEvent):
            raise TaskEventConvergenceRejected
        message = pending.message
        installation_id = UUID(str(message.installation_id))
        task_id = UUID(str(message.task_id))
        attempt_id = UUID(str(message.execution_attempt_id))
        message_id = UUID(str(message.message_id))
        try:
            async with self._database.session() as session:
                task_row = (
                    (
                        await session.execute(
                            select(tasks)
                            .where(
                                tasks.c.id == task_id,
                                tasks.c.installation_id == installation_id,
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if task_row is None or task_row["current_attempt_id"] != attempt_id:
                    raise TaskEventConvergenceRejected

                by_message = (
                    (
                        await session.execute(
                            select(task_events).where(
                                task_events.c.installation_id == installation_id,
                                task_events.c.source_message_id == message_id,
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                by_idempotency = (
                    (
                        await session.execute(
                            select(task_events).where(
                                task_events.c.installation_id == installation_id,
                                task_events.c.source_idempotency_key
                                == str(message.idempotency_key),
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                replay = by_message or by_idempotency
                if replay is not None:
                    if (
                        by_message is not None
                        and bytes(by_message["source_fingerprint"]) != pending.source_fingerprint
                    ):
                        raise TaskEventConvergenceRejected
                    if (
                        by_idempotency is not None
                        and bytes(by_idempotency["source_fingerprint"])
                        != pending.source_fingerprint
                    ):
                        raise TaskEventConvergenceRejected
                    if (
                        by_message is not None
                        and by_idempotency is not None
                        and _event_identity(by_message) != _event_identity(by_idempotency)
                    ):
                        raise TaskEventConvergenceRejected
                    return TaskEventConvergenceResult(
                        snapshot=_snapshot(task_row),
                        duplicate=True,
                    )

                expected_sequence = cast(int, task_row["last_event_sequence"]) + 1
                if message.sequence != expected_sequence:
                    raise TaskEventConvergenceRejected
                if pending.received_at < cast(datetime, task_row["updated_at"]):
                    raise TaskEventConvergenceRejected

                attempt_row = (
                    (
                        await session.execute(
                            select(execution_attempts)
                            .where(
                                execution_attempts.c.id == attempt_id,
                                execution_attempts.c.task_id == task_id,
                                execution_attempts.c.installation_id == installation_id,
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if attempt_row is None or pending.received_at < cast(
                    datetime, attempt_row["updated_at"]
                ):
                    raise TaskEventConvergenceRejected

                current_task_status = TaskStatus(cast(str, task_row["status"]))
                current_attempt_status = ExecutionAttemptStatus(cast(str, attempt_row["status"]))
                expected_controls = _CONTROL_EVENT_COMMANDS.get(message.message_type)
                control_family = _PAUSE_RESUME_COMMANDS
                if message.message_type == "task.cancelled" or (
                    message.message_type == "task.outcome_uncertain"
                    and current_task_status is TaskStatus.CANCELLING
                ):
                    expected_controls = _TERMINATION_COMMANDS
                    control_family = _TERMINATION_COMMANDS
                if expected_controls is not None:
                    latest_control = (
                        (
                            await session.execute(
                                select(task_commands)
                                .where(
                                    task_commands.c.execution_attempt_id == attempt_id,
                                    task_commands.c.task_id == task_id,
                                    task_commands.c.installation_id == installation_id,
                                    task_commands.c.command_type.in_(
                                        tuple(command.value for command in control_family)
                                    ),
                                )
                                .order_by(desc(task_commands.c.sequence))
                                .with_for_update()
                                .limit(1)
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if (
                        latest_control is None
                        or latest_control["command_type"]
                        not in {command.value for command in expected_controls}
                        or latest_control["status"] != TaskCommandStatus.ACKNOWLEDGED.value
                        or latest_control["response_type"]
                        != TaskCommandResponseType.TASK_CONTROL_ACK.value
                        or latest_control["correlation_id"] != UUID(str(message.correlation_id))
                        or latest_control["acknowledged_at"] is None
                        or cast(datetime, latest_control["acknowledged_at"]) > pending.received_at
                    ):
                        raise TaskEventConvergenceRejected

                next_task_status = _next_task_status(current_task_status, pending)
                next_attempt_status = _next_attempt_status(current_attempt_status, pending)

                action_row: RowMapping | None = None
                next_action: tuple[ActionStatus, ActionOutcome] | None = None
                if pending.action_id is not None:
                    action_row = (
                        (
                            await session.execute(
                                select(task_actions)
                                .where(
                                    task_actions.c.id == pending.action_id.uuid,
                                    task_actions.c.execution_attempt_id == attempt_id,
                                    task_actions.c.task_id == task_id,
                                    task_actions.c.installation_id == installation_id,
                                )
                                .with_for_update()
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if action_row is None or pending.received_at < cast(
                        datetime, action_row["updated_at"]
                    ):
                        raise TaskEventConvergenceRejected
                    next_action = _validate_action(action_row, pending)

                if next_attempt_status is not current_attempt_status:
                    attempt_values: dict[str, object] = {
                        "status": next_attempt_status.value,
                        "revision": cast(int, attempt_row["revision"]) + 1,
                        "updated_at": pending.received_at,
                    }
                    if (
                        next_attempt_status is ExecutionAttemptStatus.RUNNING
                        and attempt_row["started_at"] is None
                    ):
                        attempt_values["started_at"] = pending.received_at
                    if next_attempt_status in TERMINAL_EXECUTION_ATTEMPT_STATUSES:
                        attempt_values["finished_at"] = pending.received_at
                    updated_attempt = await session.execute(
                        update(execution_attempts)
                        .where(
                            execution_attempts.c.id == attempt_id,
                            execution_attempts.c.revision == attempt_row["revision"],
                        )
                        .values(**attempt_values)
                        .returning(execution_attempts.c.id)
                    )
                    updated_attempt.scalar_one()

                action_id = pending.action_id
                if action_id is not None and action_row is not None and next_action is not None:
                    current_action = (
                        ActionStatus(cast(str, action_row["status"])),
                        ActionOutcome(cast(str, action_row["outcome"])),
                    )
                    if next_action != current_action:
                        action_values: dict[str, object] = {
                            "status": next_action[0].value,
                            "outcome": next_action[1].value,
                            "revision": cast(int, action_row["revision"]) + 1,
                            "updated_at": pending.received_at,
                        }
                        if next_action[0] is ActionStatus.VERIFIED:
                            action_values["finished_at"] = pending.received_at
                        updated_action = await session.execute(
                            update(task_actions)
                            .where(
                                task_actions.c.id == action_id.uuid,
                                task_actions.c.revision == action_row["revision"],
                            )
                            .values(**action_values)
                            .returning(task_actions.c.id)
                        )
                        updated_action.scalar_one()

                next_revision = cast(int, task_row["revision"]) + 1
                await session.execute(
                    insert(task_events).values(
                        task_id=task_id,
                        installation_id=installation_id,
                        sequence=message.sequence,
                        event_version=TaskEventVersion.V1.value,
                        event_type=pending.event_type.value,
                        task_revision=next_revision,
                        task_status=next_task_status.value,
                        execution_attempt_id=attempt_id,
                        action_id=None if pending.action_id is None else pending.action_id.uuid,
                        source_message_id=message_id,
                        source_idempotency_key=str(message.idempotency_key),
                        source_fingerprint=pending.source_fingerprint,
                        progress_percent=pending.progress_percent,
                        occurred_at=message.sent_at,
                        recorded_at=pending.received_at,
                        safe_message=None,
                    )
                )
                updated_task = (
                    (
                        await session.execute(
                            update(tasks)
                            .where(
                                tasks.c.id == task_id,
                                tasks.c.installation_id == installation_id,
                                tasks.c.revision == task_row["revision"],
                                tasks.c.last_event_sequence == task_row["last_event_sequence"],
                            )
                            .values(
                                status=next_task_status.value,
                                revision=next_revision,
                                last_event_sequence=message.sequence,
                                updated_at=pending.received_at,
                            )
                            .returning(*tasks.c)
                        )
                    )
                    .mappings()
                    .one()
                )
                return TaskEventConvergenceResult(
                    snapshot=_snapshot(updated_task),
                    duplicate=False,
                )
        except TaskEventConvergenceRejected:
            raise
        except IntegrityError:
            raise TaskEventConvergenceRejected from None
        except (OSError, SQLAlchemyError):
            raise TaskEventConvergenceUnavailable from None


__all__ = ["SqlAlchemyTaskEventConvergenceRepository"]
