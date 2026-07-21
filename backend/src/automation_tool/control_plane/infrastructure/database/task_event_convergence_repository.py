"""Atomic PostgreSQL convergence for Executor Task events and snapshots."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import desc, insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from automation_tool.control_plane.application.task_event_convergence import (
    PendingTaskEvent,
    TaskEventConvergenceRejected,
    TaskEventConvergenceResult,
    TaskEventConvergenceUnavailable,
)
from automation_tool.control_plane.domain import (
    MAX_ACTION_RISK_LIMIT,
    TERMINAL_EXECUTION_ATTEMPT_STATUSES,
    ActionOutcome,
    ActionStatus,
    ExecutionAttemptStatus,
    InvalidTaskTransition,
    TaskCommandResponseType,
    TaskCommandStatus,
    TaskCommandType,
    TaskEventType,
    TaskEventVersion,
    TaskId,
    TaskSnapshotProjection,
    TaskStateMachine,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database.schema import (
    action_failure_circuits,
    action_risk_authorizations,
    action_risk_results,
    douyin_search_exposure_definitions,
    execution_attempts,
    installations,
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


async def _record_action_risk_result(
    session: AsyncSession,
    *,
    pending: PendingTaskEvent,
) -> bool:
    """Persist one authorized final result and return whether it opened the circuit."""

    action_id = pending.action_id
    outcome = pending.target_action_outcome
    if (
        action_id is None
        or pending.message.message_type not in {"step.completed", "step.failed"}
        or outcome not in {ActionOutcome.SUCCEEDED, ActionOutcome.FAILED}
    ):
        return False
    authorization = (
        (
            await session.execute(
                select(
                    action_risk_authorizations.c.installation_id,
                    action_risk_authorizations.c.platform,
                    action_risk_authorizations.c.action,
                    action_risk_authorizations.c.consecutive_failure_threshold,
                )
                .where(action_risk_authorizations.c.action_id == action_id.uuid)
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if authorization is None:
        return False
    if (
        await session.scalar(
            select(action_risk_results.c.action_id).where(
                action_risk_results.c.action_id == action_id.uuid
            )
        )
        is not None
    ):
        raise TaskEventConvergenceRejected

    scope = (
        action_failure_circuits.c.installation_id == authorization["installation_id"],
        action_failure_circuits.c.platform == authorization["platform"],
        action_failure_circuits.c.action == authorization["action"],
    )
    circuit = (
        (await session.execute(select(action_failure_circuits).where(*scope).with_for_update()))
        .mappings()
        .one_or_none()
    )
    prior_failures = 0 if circuit is None else cast(int, circuit["consecutive_failures"])
    already_open = False if circuit is None else cast(bool, circuit["circuit_open"])
    if circuit is not None and pending.received_at < cast(datetime, circuit["updated_at"]):
        raise TaskEventConvergenceRejected

    if outcome is ActionOutcome.SUCCEEDED:
        failures_after = prior_failures if already_open else 0
    else:
        if prior_failures >= MAX_ACTION_RISK_LIMIT:
            raise TaskEventConvergenceRejected
        failures_after = prior_failures + 1
    threshold = cast(int, authorization["consecutive_failure_threshold"])
    opened_now = (
        outcome is ActionOutcome.FAILED and not already_open and failures_after >= threshold
    )
    open_after = already_open or opened_now

    await session.execute(
        insert(action_risk_results).values(
            action_id=action_id.uuid,
            installation_id=authorization["installation_id"],
            platform=authorization["platform"],
            action=authorization["action"],
            outcome=outcome.value,
            consecutive_failures_after=failures_after,
            consecutive_failure_threshold=threshold,
            circuit_open_after=open_after,
            triggered_handoff=opened_now,
            observed_at=pending.received_at,
            created_at=pending.received_at,
        )
    )
    if circuit is None:
        await session.execute(
            insert(action_failure_circuits).values(
                installation_id=authorization["installation_id"],
                platform=authorization["platform"],
                action=authorization["action"],
                consecutive_failures=failures_after,
                circuit_open=open_after,
                revision=1,
                last_action_id=action_id.uuid,
                opened_by_action_id=action_id.uuid if opened_now else None,
                opened_at=pending.received_at if opened_now else None,
                created_at=pending.received_at,
                updated_at=pending.received_at,
            )
        )
    else:
        values: dict[str, object] = {
            "consecutive_failures": failures_after,
            "circuit_open": open_after,
            "revision": cast(int, circuit["revision"]) + 1,
            "last_action_id": action_id.uuid,
            "updated_at": pending.received_at,
        }
        if opened_now:
            values["opened_by_action_id"] = action_id.uuid
            values["opened_at"] = pending.received_at
        updated = await session.execute(
            update(action_failure_circuits)
            .where(*scope, action_failure_circuits.c.revision == circuit["revision"])
            .values(**values)
            .returning(action_failure_circuits.c.revision)
        )
        updated.scalar_one()
    return opened_now


async def _clear_owned_failure_circuit(
    session: AsyncSession,
    *,
    pending: PendingTaskEvent,
) -> None:
    if pending.message.message_type != "task.resumed":
        return
    installation_id = UUID(str(pending.message.installation_id))
    task_id = UUID(str(pending.message.task_id))
    action = await session.scalar(
        select(douyin_search_exposure_definitions.c.action).where(
            douyin_search_exposure_definitions.c.task_id == task_id,
            douyin_search_exposure_definitions.c.installation_id == installation_id,
        )
    )
    if action is None:
        return
    circuit = (
        (
            await session.execute(
                select(action_failure_circuits)
                .where(
                    action_failure_circuits.c.installation_id == installation_id,
                    action_failure_circuits.c.platform == "douyin",
                    action_failure_circuits.c.action == action,
                    action_failure_circuits.c.circuit_open.is_(True),
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if circuit is None:
        return
    opened_task_id = await session.scalar(
        select(action_risk_authorizations.c.task_id).where(
            action_risk_authorizations.c.action_id == circuit["opened_by_action_id"]
        )
    )
    if opened_task_id != task_id:
        return
    if pending.received_at < cast(datetime, circuit["updated_at"]):
        raise TaskEventConvergenceRejected
    updated = await session.execute(
        update(action_failure_circuits)
        .where(
            action_failure_circuits.c.installation_id == installation_id,
            action_failure_circuits.c.platform == "douyin",
            action_failure_circuits.c.action == action,
            action_failure_circuits.c.revision == circuit["revision"],
        )
        .values(
            consecutive_failures=0,
            circuit_open=False,
            revision=cast(int, circuit["revision"]) + 1,
            opened_by_action_id=None,
            opened_at=None,
            updated_at=pending.received_at,
        )
        .returning(action_failure_circuits.c.revision)
    )
    updated.scalar_one()


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
                installation_id = UUID(str(message.installation_id))
                installation = await session.scalar(
                    select(installations.c.id)
                    .where(installations.c.id == installation_id)
                    .with_for_update()
                )
                if installation is None:
                    raise TaskEventConvergenceRejected
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

                opened_failure_circuit = await _record_action_risk_result(
                    session,
                    pending=pending,
                )
                persisted_event_type = pending.event_type
                if opened_failure_circuit and current_task_status is TaskStatus.RUNNING:
                    next_task_status = TaskStateMachine.transition(
                        current_task_status,
                        TaskStatus.AWAITING_HUMAN,
                    )
                    next_attempt_status = ExecutionAttemptStatus.AWAITING_HUMAN
                    persisted_event_type = TaskEventType.TASK_AWAITING_HUMAN
                else:
                    next_task_status = _next_task_status(current_task_status, pending)
                    next_attempt_status = _next_attempt_status(current_attempt_status, pending)
                await _clear_owned_failure_circuit(session, pending=pending)

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
                        event_type=persisted_event_type.value,
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
