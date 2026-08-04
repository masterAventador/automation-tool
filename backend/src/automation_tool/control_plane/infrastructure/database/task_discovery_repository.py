"""Atomic PostgreSQL start and final convergence for target discovery."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from automation_tool.control_plane.application.task_discovery import (
    PendingTaskDiscovery,
    TaskDiscoveryConvergenceResult,
    TaskDiscoveryInstallationBusy,
    TaskDiscoveryRejected,
    TaskDiscoveryStartResult,
)
from automation_tool.control_plane.application.task_targets import (
    TaskTargetPersistenceRejected,
)
from automation_tool.control_plane.domain import (
    TERMINAL_EXECUTION_ATTEMPT_STATUSES,
    ExecutionAttemptStatus,
    InstallationId,
    InstallationStatus,
    TaskCommandResponseType,
    TaskCommandStatus,
    TaskCommandType,
    TaskEventType,
    TaskEventVersion,
    TaskId,
    TaskStateMachine,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database.schema import (
    douyin_search_exposure_definitions,
    execution_attempts,
    installations,
    platform_session_gates,
    platform_session_health,
    task_commands,
    task_events,
    task_targets,
    tasks,
)
from automation_tool.control_plane.infrastructure.database.session import Database
from automation_tool.control_plane.infrastructure.database.task_command_repository import (
    _record as command_record_from_row,
)
from automation_tool.control_plane.infrastructure.database.task_repository import (
    _record as task_record_from_row,
)
from automation_tool.control_plane.infrastructure.database.task_target_repository import (
    _record as target_record_from_row,
)
from automation_tool.control_plane.infrastructure.database.task_target_repository import (
    evaluate_and_replace_task_targets,
)
from automation_tool.protocol import (
    MAX_DISCOVERY_BATCH_CANDIDATES,
    TaskDiscoveryBatchEnvelope,
    TaskDiscoveryCompletedEnvelope,
)
from automation_tool.protocol.douyin_candidate import DouyinCandidate

_STARTABLE_STATUSES = frozenset(
    {
        TaskStatus.DRAFT,
        TaskStatus.AWAITING_PLATFORM_LOGIN,
        TaskStatus.AWAITING_HUMAN,
        TaskStatus.AWAITING_CONFIRMATION,
    }
)
_ACTIVE_DISCOVERY_ATTEMPT_STATUSES = frozenset(
    {
        ExecutionAttemptStatus.OFFERED,
        ExecutionAttemptStatus.ACCEPTED,
        ExecutionAttemptStatus.RUNNING,
    }
)


def _require_discovery_reachable(status: TaskStatus) -> None:
    if status not in _STARTABLE_STATUSES:
        raise TaskDiscoveryRejected
    if status is TaskStatus.DRAFT:
        current: TaskStatus = status
        for target in (
            TaskStatus.VALIDATING,
            TaskStatus.AWAITING_DEVICE,
            TaskStatus.AWAITING_PLATFORM_LOGIN,
            TaskStatus.DISCOVERING_TARGETS,
        ):
            current = TaskStateMachine.transition(current, target)
        return
    TaskStateMachine.transition(status, TaskStatus.DISCOVERING_TARGETS)


def _start_fingerprint(pending: PendingTaskDiscovery) -> bytes:
    source = (
        f"{pending.installation_id}:{pending.task_id}:"
        f"{pending.execution_attempt_id}:task.discovery_started"
    ).encode("ascii")
    return hashlib.sha256(source).digest()


class SqlAlchemyTaskDiscoveryRepository:
    def __init__(self, database: Database) -> None:
        if not isinstance(database, Database):
            raise TaskDiscoveryRejected
        self._database = database

    async def start(self, pending: PendingTaskDiscovery) -> TaskDiscoveryStartResult:
        if not isinstance(pending, PendingTaskDiscovery):
            raise TaskDiscoveryRejected
        try:
            async with self._database.session() as session:
                installation_status = await session.scalar(
                    select(installations.c.status)
                    .where(installations.c.id == pending.installation_id.uuid)
                    .with_for_update()
                )
                if installation_status != InstallationStatus.ACTIVE.value:
                    raise TaskDiscoveryRejected
                blocked = await session.scalar(
                    select(platform_session_gates.c.session_revision).where(
                        platform_session_gates.c.installation_id == pending.installation_id.uuid,
                        platform_session_gates.c.platform == "douyin",
                    )
                )
                health = await session.scalar(
                    select(platform_session_health.c.state).where(
                        platform_session_health.c.installation_id == pending.installation_id.uuid,
                        platform_session_health.c.platform == "douyin",
                    )
                )
                if blocked is not None or health != "healthy":
                    raise TaskDiscoveryRejected

                existing_command = (
                    (
                        await session.execute(
                            select(task_commands).where(
                                task_commands.c.installation_id == pending.installation_id.uuid,
                                task_commands.c.idempotency_key == pending.idempotency_key,
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing_command is not None:
                    command = command_record_from_row(existing_command)
                    if (
                        command.task_id != pending.task_id
                        or command.command_type is not TaskCommandType.TASK_DISCOVER
                    ):
                        raise TaskDiscoveryRejected
                    existing_task = (
                        (
                            await session.execute(
                                select(tasks).where(
                                    tasks.c.id == pending.task_id.uuid,
                                    tasks.c.installation_id == pending.installation_id.uuid,
                                )
                            )
                        )
                        .mappings()
                        .one()
                    )
                    return TaskDiscoveryStartResult(
                        task=task_record_from_row(existing_task),
                        command=command,
                        created=False,
                    )

                active_attempt_id = await session.scalar(
                    select(execution_attempts.c.id)
                    .where(
                        execution_attempts.c.installation_id == pending.installation_id.uuid,
                        execution_attempts.c.status.not_in(
                            tuple(status.value for status in TERMINAL_EXECUTION_ATTEMPT_STATUSES)
                        ),
                    )
                    .limit(1)
                )
                if active_attempt_id is not None:
                    raise TaskDiscoveryInstallationBusy

                task_row = (
                    (
                        await session.execute(
                            select(tasks)
                            .where(
                                tasks.c.id == pending.task_id.uuid,
                                tasks.c.installation_id == pending.installation_id.uuid,
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if task_row is None or pending.created_at < cast(datetime, task_row["updated_at"]):
                    raise TaskDiscoveryRejected
                current_status = TaskStatus(cast(str, task_row["status"]))
                _require_discovery_reachable(current_status)
                definition_exists = await session.scalar(
                    select(douyin_search_exposure_definitions.c.task_id).where(
                        douyin_search_exposure_definitions.c.task_id == pending.task_id.uuid,
                        douyin_search_exposure_definitions.c.installation_id
                        == pending.installation_id.uuid,
                    )
                )
                if definition_exists is None:
                    raise TaskDiscoveryRejected
                last_attempt_number = cast(
                    int,
                    await session.scalar(
                        select(
                            func.coalesce(func.max(execution_attempts.c.attempt_number), 0)
                        ).where(
                            execution_attempts.c.task_id == pending.task_id.uuid,
                            execution_attempts.c.installation_id == pending.installation_id.uuid,
                        )
                    ),
                )
                attempt_number = last_attempt_number + 1
                await session.execute(
                    insert(execution_attempts).values(
                        id=pending.execution_attempt_id.uuid,
                        task_id=pending.task_id.uuid,
                        installation_id=pending.installation_id.uuid,
                        attempt_number=attempt_number,
                        status=ExecutionAttemptStatus.OFFERED.value,
                        revision=1,
                        created_at=pending.created_at,
                        updated_at=pending.created_at,
                    )
                )
                command_row = (
                    (
                        await session.execute(
                            insert(task_commands)
                            .values(
                                message_id=pending.message_id,
                                correlation_id=pending.correlation_id,
                                installation_id=pending.installation_id.uuid,
                                task_id=pending.task_id.uuid,
                                execution_attempt_id=pending.execution_attempt_id.uuid,
                                sequence=pending.command_sequence,
                                command_type=pending.command_type.value,
                                status=TaskCommandStatus.PENDING.value,
                                idempotency_key=pending.idempotency_key,
                                revision=1,
                                delivery_attempts=0,
                                next_delivery_at=pending.created_at,
                                deadline_at=pending.deadline_at,
                                created_at=pending.created_at,
                                updated_at=pending.created_at,
                            )
                            .returning(*task_commands.c)
                        )
                    )
                    .mappings()
                    .one()
                )
                next_sequence = cast(int, task_row["last_event_sequence"]) + 1
                next_revision = cast(int, task_row["revision"]) + 1
                await session.execute(
                    insert(task_events).values(
                        task_id=pending.task_id.uuid,
                        installation_id=pending.installation_id.uuid,
                        sequence=next_sequence,
                        event_version=TaskEventVersion.V1.value,
                        event_type=TaskEventType.TASK_DISCOVERY_STARTED.value,
                        task_revision=next_revision,
                        task_status=TaskStatus.DISCOVERING_TARGETS.value,
                        execution_attempt_id=pending.execution_attempt_id.uuid,
                        action_id=None,
                        source_message_id=None,
                        source_idempotency_key=(
                            f"task:discovery:start:{pending.execution_attempt_id}"
                        ),
                        source_fingerprint=_start_fingerprint(pending),
                        progress_percent=None,
                        occurred_at=pending.created_at,
                        recorded_at=pending.created_at,
                        safe_message=None,
                    )
                )
                updated_task = (
                    (
                        await session.execute(
                            update(tasks)
                            .where(
                                tasks.c.id == pending.task_id.uuid,
                                tasks.c.installation_id == pending.installation_id.uuid,
                                tasks.c.revision == task_row["revision"],
                                tasks.c.last_event_sequence == task_row["last_event_sequence"],
                            )
                            .values(
                                current_attempt_id=pending.execution_attempt_id.uuid,
                                status=TaskStatus.DISCOVERING_TARGETS.value,
                                revision=next_revision,
                                last_event_sequence=next_sequence,
                                updated_at=pending.created_at,
                            )
                            .returning(*tasks.c)
                        )
                    )
                    .mappings()
                    .one()
                )
                return TaskDiscoveryStartResult(
                    task=task_record_from_row(updated_task),
                    command=command_record_from_row(command_row),
                    created=True,
                )
        except TaskDiscoveryRejected:
            raise
        except (IntegrityError, OSError, SQLAlchemyError, ValueError):
            raise TaskDiscoveryRejected from None

    async def authorize_batch(self, message: TaskDiscoveryBatchEnvelope) -> None:
        if not isinstance(message, TaskDiscoveryBatchEnvelope):
            raise TaskDiscoveryRejected
        async with self._database.session() as session:
            try:
                await self._require_active_attempt(session, message)
            except TaskDiscoveryRejected:
                await self._require_completed_replay(session, message)
                if not await self._batch_matches_persisted(session, message):
                    raise TaskDiscoveryRejected from None

    async def converge(
        self,
        message: TaskDiscoveryCompletedEnvelope,
        *,
        candidates: tuple[DouyinCandidate, ...] | None,
        source_fingerprint: bytes,
        received_at: datetime,
    ) -> TaskDiscoveryConvergenceResult:
        if (
            not isinstance(message, TaskDiscoveryCompletedEnvelope)
            or (candidates is not None and type(candidates) is not tuple)
            or type(source_fingerprint) is not bytes
            or len(source_fingerprint) != 32
            or not isinstance(received_at, datetime)
            or received_at.utcoffset() is None
        ):
            raise TaskDiscoveryRejected
        installation_id = InstallationId.parse(str(message.installation_id))
        task_id = TaskId.parse(str(message.task_id))
        try:
            async with self._database.session() as session:
                duplicate = (
                    (
                        await session.execute(
                            select(task_events).where(
                                task_events.c.installation_id == installation_id.uuid,
                                (task_events.c.source_message_id == UUID(str(message.message_id)))
                                | (
                                    task_events.c.source_idempotency_key
                                    == str(message.idempotency_key)
                                ),
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if duplicate is not None:
                    if bytes(duplicate["source_fingerprint"]) != source_fingerprint:
                        raise TaskDiscoveryRejected
                    await self._require_final_replay(session, message)
                    if message.payload.outcome == "completed":
                        if candidates is None or not await self._candidates_match_persisted(
                            session,
                            task_id=task_id,
                            installation_id=installation_id,
                            candidates=candidates,
                        ):
                            raise TaskDiscoveryRejected
                    elif candidates is not None:
                        raise TaskDiscoveryRejected
                    task_row = (
                        (
                            await session.execute(
                                select(tasks).where(
                                    tasks.c.id == task_id.uuid,
                                    tasks.c.installation_id == installation_id.uuid,
                                )
                            )
                        )
                        .mappings()
                        .one()
                    )
                    return TaskDiscoveryConvergenceResult(
                        task=task_record_from_row(task_row),
                        duplicate=True,
                    )

                task_row, attempt_row = await self._require_active_attempt(session, message)
                if received_at < cast(datetime, task_row["updated_at"]) or received_at < cast(
                    datetime, attempt_row["updated_at"]
                ):
                    raise TaskDiscoveryRejected
                outcome = message.payload.outcome
                if outcome == "completed":
                    if (
                        candidates is None
                        or not candidates
                        or len(candidates) != message.payload.candidate_count
                        or any(
                            not isinstance(candidate, DouyinCandidate)
                            or candidate.page_revision != message.payload.page_revision
                            for candidate in candidates
                        )
                    ):
                        raise TaskDiscoveryRejected
                    await evaluate_and_replace_task_targets(
                        session,
                        task_id=task_id,
                        installation_id=installation_id,
                        candidates=candidates,
                        blacklist=(),
                        evaluated_at=received_at,
                    )
                    next_task_status = TaskStatus.AWAITING_CONFIRMATION
                    next_attempt_status = ExecutionAttemptStatus.SUCCEEDED
                    event_type = TaskEventType.TASK_AWAITING_CONFIRMATION
                elif outcome == "login_required":
                    if candidates is not None:
                        raise TaskDiscoveryRejected
                    next_task_status = TaskStatus.AWAITING_PLATFORM_LOGIN
                    next_attempt_status = ExecutionAttemptStatus.FAILED
                    event_type = TaskEventType.TASK_AWAITING_PLATFORM_LOGIN
                elif outcome == "handoff_required":
                    if candidates is not None:
                        raise TaskDiscoveryRejected
                    next_task_status = TaskStatus.AWAITING_HUMAN
                    next_attempt_status = ExecutionAttemptStatus.FAILED
                    event_type = TaskEventType.TASK_AWAITING_HUMAN
                else:
                    if candidates is not None:
                        raise TaskDiscoveryRejected
                    next_task_status = TaskStatus.FAILED
                    next_attempt_status = ExecutionAttemptStatus.FAILED
                    event_type = TaskEventType.TASK_FAILED
                TaskStateMachine.transition(
                    TaskStatus(cast(str, task_row["status"])),
                    next_task_status,
                )
                attempt_values: dict[str, object] = {
                    "status": next_attempt_status.value,
                    "revision": cast(int, attempt_row["revision"]) + 1,
                    "updated_at": received_at,
                    "finished_at": received_at,
                }
                await session.execute(
                    update(execution_attempts)
                    .where(
                        execution_attempts.c.id == attempt_row["id"],
                        execution_attempts.c.revision == attempt_row["revision"],
                    )
                    .values(**attempt_values)
                )
                next_sequence = cast(int, task_row["last_event_sequence"]) + 1
                next_revision = cast(int, task_row["revision"]) + 1
                await session.execute(
                    insert(task_events).values(
                        task_id=task_id.uuid,
                        installation_id=installation_id.uuid,
                        sequence=next_sequence,
                        event_version=TaskEventVersion.V1.value,
                        event_type=event_type.value,
                        task_revision=next_revision,
                        task_status=next_task_status.value,
                        execution_attempt_id=attempt_row["id"],
                        action_id=None,
                        source_message_id=UUID(str(message.message_id)),
                        source_idempotency_key=str(message.idempotency_key),
                        source_fingerprint=source_fingerprint,
                        progress_percent=None,
                        occurred_at=message.sent_at,
                        recorded_at=received_at,
                        safe_message=None,
                    )
                )
                updated_task = (
                    (
                        await session.execute(
                            update(tasks)
                            .where(
                                tasks.c.id == task_id.uuid,
                                tasks.c.installation_id == installation_id.uuid,
                                tasks.c.revision == task_row["revision"],
                                tasks.c.last_event_sequence == task_row["last_event_sequence"],
                            )
                            .values(
                                status=next_task_status.value,
                                revision=next_revision,
                                last_event_sequence=next_sequence,
                                updated_at=received_at,
                            )
                            .returning(*tasks.c)
                        )
                    )
                    .mappings()
                    .one()
                )
                return TaskDiscoveryConvergenceResult(
                    task=task_record_from_row(updated_task),
                    duplicate=False,
                )
        except TaskDiscoveryRejected:
            raise
        except (
            IntegrityError,
            OSError,
            SQLAlchemyError,
            TaskTargetPersistenceRejected,
            ValueError,
        ):
            raise TaskDiscoveryRejected from None

    @staticmethod
    async def _require_active_attempt(
        session: AsyncSession,
        message: TaskDiscoveryBatchEnvelope | TaskDiscoveryCompletedEnvelope,
    ) -> tuple[RowMapping, RowMapping]:
        installation_id = UUID(str(message.installation_id))
        task_id = UUID(str(message.task_id))
        attempt_id = UUID(str(message.execution_attempt_id))
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
        command = (
            (
                await session.execute(
                    select(task_commands).where(
                        task_commands.c.execution_attempt_id == attempt_id,
                        task_commands.c.task_id == task_id,
                        task_commands.c.installation_id == installation_id,
                        task_commands.c.correlation_id == UUID(str(message.correlation_id)),
                        task_commands.c.command_type == TaskCommandType.TASK_DISCOVER.value,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            task_row is None
            or attempt_row is None
            or task_row["current_attempt_id"] != attempt_id
            or task_row["status"] != TaskStatus.DISCOVERING_TARGETS.value
            or ExecutionAttemptStatus(cast(str, attempt_row["status"]))
            not in _ACTIVE_DISCOVERY_ATTEMPT_STATUSES
            or attempt_row["attempt_number"] != message.payload.page_revision
            or command is None
            or command["status"] != TaskCommandStatus.ACKNOWLEDGED.value
            or command["response_type"] != TaskCommandResponseType.TASK_ACCEPT.value
        ):
            raise TaskDiscoveryRejected
        return task_row, attempt_row

    @staticmethod
    async def _require_completed_replay(
        session: AsyncSession,
        message: TaskDiscoveryBatchEnvelope,
    ) -> None:
        task_row, attempt_row, command_row = await SqlAlchemyTaskDiscoveryRepository._replay_rows(
            session,
            message,
        )
        if (
            task_row["status"] != TaskStatus.AWAITING_CONFIRMATION.value
            or attempt_row["status"] != ExecutionAttemptStatus.SUCCEEDED.value
            or command_row["status"] != TaskCommandStatus.ACKNOWLEDGED.value
            or command_row["response_type"] != TaskCommandResponseType.TASK_ACCEPT.value
        ):
            raise TaskDiscoveryRejected

    @staticmethod
    async def _require_final_replay(
        session: AsyncSession,
        message: TaskDiscoveryCompletedEnvelope,
    ) -> None:
        task_row, attempt_row, command_row = await SqlAlchemyTaskDiscoveryRepository._replay_rows(
            session,
            message,
        )
        expected = {
            "completed": (
                TaskStatus.AWAITING_CONFIRMATION.value,
                ExecutionAttemptStatus.SUCCEEDED.value,
            ),
            "login_required": (
                TaskStatus.AWAITING_PLATFORM_LOGIN.value,
                ExecutionAttemptStatus.FAILED.value,
            ),
            "handoff_required": (
                TaskStatus.AWAITING_HUMAN.value,
                ExecutionAttemptStatus.FAILED.value,
            ),
            "failed": (
                TaskStatus.FAILED.value,
                ExecutionAttemptStatus.FAILED.value,
            ),
        }[message.payload.outcome]
        if (
            (task_row["status"], attempt_row["status"]) != expected
            or command_row["status"] != TaskCommandStatus.ACKNOWLEDGED.value
            or command_row["response_type"] != TaskCommandResponseType.TASK_ACCEPT.value
        ):
            raise TaskDiscoveryRejected

    @staticmethod
    async def _replay_rows(
        session: AsyncSession,
        message: TaskDiscoveryBatchEnvelope | TaskDiscoveryCompletedEnvelope,
    ) -> tuple[RowMapping, RowMapping, RowMapping]:
        installation_id = UUID(str(message.installation_id))
        task_id = UUID(str(message.task_id))
        attempt_id = UUID(str(message.execution_attempt_id))
        task_row = (
            (
                await session.execute(
                    select(tasks).where(
                        tasks.c.id == task_id,
                        tasks.c.installation_id == installation_id,
                        tasks.c.current_attempt_id == attempt_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        attempt_row = (
            (
                await session.execute(
                    select(execution_attempts).where(
                        execution_attempts.c.id == attempt_id,
                        execution_attempts.c.task_id == task_id,
                        execution_attempts.c.installation_id == installation_id,
                        execution_attempts.c.attempt_number == message.payload.page_revision,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        command_row = (
            (
                await session.execute(
                    select(task_commands).where(
                        task_commands.c.execution_attempt_id == attempt_id,
                        task_commands.c.task_id == task_id,
                        task_commands.c.installation_id == installation_id,
                        task_commands.c.correlation_id == UUID(str(message.correlation_id)),
                        task_commands.c.command_type == TaskCommandType.TASK_DISCOVER.value,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if task_row is None or attempt_row is None or command_row is None:
            raise TaskDiscoveryRejected
        return task_row, attempt_row, command_row

    @staticmethod
    async def _batch_matches_persisted(
        session: AsyncSession,
        message: TaskDiscoveryBatchEnvelope,
    ) -> bool:
        start = (message.payload.batch_index - 1) * MAX_DISCOVERY_BATCH_CANDIDATES + 1
        stop = start + len(message.payload.candidates) - 1
        rows = (
            (
                await session.execute(
                    select(task_targets)
                    .where(
                        task_targets.c.task_id == UUID(str(message.task_id)),
                        task_targets.c.installation_id == UUID(str(message.installation_id)),
                        task_targets.c.page_revision == message.payload.page_revision,
                        task_targets.c.ordinal.between(start, stop),
                    )
                    .order_by(task_targets.c.ordinal, task_targets.c.id)
                )
            )
            .mappings()
            .all()
        )
        persisted = tuple(target_record_from_row(row).candidate for row in rows)
        received = tuple(candidate.to_candidate() for candidate in message.payload.candidates)
        return persisted == received

    @staticmethod
    async def _candidates_match_persisted(
        session: AsyncSession,
        *,
        task_id: TaskId,
        installation_id: InstallationId,
        candidates: tuple[DouyinCandidate, ...],
    ) -> bool:
        rows = (
            (
                await session.execute(
                    select(task_targets)
                    .where(
                        task_targets.c.task_id == task_id.uuid,
                        task_targets.c.installation_id == installation_id.uuid,
                        task_targets.c.page_revision == candidates[0].page_revision,
                    )
                    .order_by(task_targets.c.ordinal, task_targets.c.id)
                )
            )
            .mappings()
            .all()
        )
        return tuple(target_record_from_row(row).candidate for row in rows) == candidates


__all__ = ["SqlAlchemyTaskDiscoveryRepository"]
