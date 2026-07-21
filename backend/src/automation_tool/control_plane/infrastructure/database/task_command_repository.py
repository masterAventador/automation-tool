"""Atomic PostgreSQL repository for the persistent Executor command outbox."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import and_, exists, func, insert, or_, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError

from automation_tool.control_plane.application.task_command_delivery import (
    ActionCommandContext,
    PendingTaskCommand,
    TaskCommandDeliveryRejected,
    TaskCommandRecord,
)
from automation_tool.control_plane.application.task_controls import (
    PendingTaskControl,
    TaskControlConflict,
    TaskControlEnqueueResult,
    TaskControlNotFound,
)
from automation_tool.control_plane.domain import (
    ActionId,
    ExecutionAttemptId,
    ExecutionAttemptStatus,
    InstallationId,
    InstallationStatus,
    TaskCommandResponseType,
    TaskCommandStatus,
    TaskCommandType,
    TaskId,
    TaskStateMachine,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database.schema import (
    action_risk_authorizations,
    douyin_search_exposure_definitions,
    execution_attempts,
    installations,
    platform_session_gates,
    task_commands,
    task_target_confirmations,
    task_targets,
    tasks,
)
from automation_tool.control_plane.infrastructure.database.session import Database
from automation_tool.protocol import (
    DOUYIN_DISCOVERY_PROTOCOL_VERSION,
    DouyinCandidate,
    DouyinCandidateSource,
    DouyinCandidateSummary,
    DouyinDiscoveryCommandPayload,
    TaskCommandResultEnvelope,
)

from .action_risk_authorization_repository import (
    _record as action_authorization_record_from_row,
)


def _aware_utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise TaskCommandDeliveryRejected
    return value.astimezone(UTC)


def _record(row: RowMapping) -> TaskCommandRecord:
    try:
        response_type = row["response_type"]
        return TaskCommandRecord(
            message_id=cast(UUID, row["message_id"]),
            correlation_id=cast(UUID, row["correlation_id"]),
            installation_id=InstallationId.parse(row["installation_id"]),
            task_id=TaskId.parse(row["task_id"]),
            execution_attempt_id=ExecutionAttemptId.parse(row["execution_attempt_id"]),
            sequence=cast(int, row["sequence"]),
            command_type=TaskCommandType(cast(str, row["command_type"])),
            target_confirmation_message_id=cast(
                UUID | None,
                row["target_confirmation_message_id"],
            ),
            action_id=(None if row["action_id"] is None else ActionId.parse(row["action_id"])),
            status=TaskCommandStatus(cast(str, row["status"])),
            idempotency_key=cast(str, row["idempotency_key"]),
            revision=cast(int, row["revision"]),
            delivery_attempts=cast(int, row["delivery_attempts"]),
            next_delivery_at=cast(datetime | None, row["next_delivery_at"]),
            lease_expires_at=cast(datetime | None, row["lease_expires_at"]),
            delivered_at=cast(datetime | None, row["delivered_at"]),
            acknowledged_at=cast(datetime | None, row["acknowledged_at"]),
            response_message_id=cast(UUID | None, row["response_message_id"]),
            response_type=(
                None if response_type is None else TaskCommandResponseType(cast(str, response_type))
            ),
            deadline_at=cast(datetime, row["deadline_at"]),
            created_at=cast(datetime, row["created_at"]),
            updated_at=cast(datetime, row["updated_at"]),
        )
    except (KeyError, TypeError, ValueError):
        raise TaskCommandDeliveryRejected from None


def _same_intent(
    existing: TaskCommandRecord,
    pending: PendingTaskCommand,
    *,
    target_confirmation_message_id: UUID | None,
) -> bool:
    return (
        existing.installation_id == pending.installation_id
        and existing.task_id == pending.task_id
        and existing.execution_attempt_id == pending.execution_attempt_id
        and existing.sequence == pending.sequence
        and existing.command_type is pending.command_type
        and existing.target_confirmation_message_id == target_confirmation_message_id
        and existing.idempotency_key == pending.idempotency_key
        and existing.deadline_at == pending.deadline_at
    )


def _response_payload_is_valid(response: TaskCommandResultEnvelope) -> bool:
    if response.message_type in {
        TaskCommandResponseType.TASK_ACCEPT.value,
        TaskCommandResponseType.ACTION_ACCEPT.value,
    }:
        return response.payload == {"accepted": True}
    if response.message_type in {
        TaskCommandResponseType.TASK_REJECT.value,
        TaskCommandResponseType.ACTION_REJECT.value,
    }:
        return response.payload == {"accepted": False}
    return response.payload == {"acknowledged": True}


def _response_matches_command(
    command_type: TaskCommandType,
    response_type: TaskCommandResponseType,
) -> bool:
    if command_type in {TaskCommandType.TASK_OFFER, TaskCommandType.TASK_DISCOVER}:
        return response_type in {
            TaskCommandResponseType.TASK_ACCEPT,
            TaskCommandResponseType.TASK_REJECT,
        }
    if command_type is TaskCommandType.ACTION_EXECUTE:
        return response_type in {
            TaskCommandResponseType.ACTION_ACCEPT,
            TaskCommandResponseType.ACTION_REJECT,
        }
    return response_type is TaskCommandResponseType.TASK_CONTROL_ACK


_TERMINATION_COMMANDS = frozenset(
    {
        TaskCommandType.TASK_CANCEL,
        TaskCommandType.TASK_EMERGENCY_STOP,
    }
)
_CANCELLABLE_ATTEMPT_STATUSES = frozenset(
    {
        ExecutionAttemptStatus.PENDING,
        ExecutionAttemptStatus.OFFERED,
        ExecutionAttemptStatus.ACCEPTED,
        ExecutionAttemptStatus.RUNNING,
        ExecutionAttemptStatus.PAUSED,
        ExecutionAttemptStatus.AWAITING_HUMAN,
    }
)


class SqlAlchemyTaskCommandRepository:
    """Serialize enqueue/claim/delivery/ACK transitions in PostgreSQL."""

    def __init__(self, database: Database) -> None:
        if not isinstance(database, Database):
            raise TaskCommandDeliveryRejected
        self._database = database

    async def enqueue(self, command: PendingTaskCommand) -> TaskCommandRecord:
        if not isinstance(command, PendingTaskCommand):
            raise TaskCommandDeliveryRejected
        try:
            async with self._database.session() as session:
                installation_status = await session.scalar(
                    select(installations.c.status)
                    .where(installations.c.id == command.installation_id.uuid)
                    .with_for_update()
                )
                if installation_status != InstallationStatus.ACTIVE.value:
                    raise TaskCommandDeliveryRejected
                requires_target_confirmation = False
                if command.command_type is TaskCommandType.TASK_OFFER:
                    requires_target_confirmation = (
                        await session.scalar(
                            select(
                                exists().where(
                                    douyin_search_exposure_definitions.c.task_id
                                    == command.task_id.uuid,
                                    douyin_search_exposure_definitions.c.installation_id
                                    == command.installation_id.uuid,
                                )
                            )
                        )
                        is True
                    )
                confirmation_row = None
                if requires_target_confirmation:
                    confirmation_row = (
                        (
                            await session.execute(
                                select(
                                    task_target_confirmations.c.source_message_id,
                                    task_target_confirmations.c.confirmed_task_revision,
                                    task_target_confirmations.c.action.label("confirmation_action"),
                                    task_target_confirmations.c.message_template.label(
                                        "confirmation_message_template"
                                    ),
                                    douyin_search_exposure_definitions.c.action.label(
                                        "definition_action"
                                    ),
                                    douyin_search_exposure_definitions.c.message_template.label(
                                        "definition_message_template"
                                    ),
                                    tasks.c.status,
                                    tasks.c.revision,
                                )
                                .select_from(
                                    task_target_confirmations.join(
                                        tasks,
                                        and_(
                                            tasks.c.id == task_target_confirmations.c.task_id,
                                            tasks.c.installation_id
                                            == task_target_confirmations.c.installation_id,
                                        ),
                                    ).join(
                                        douyin_search_exposure_definitions,
                                        and_(
                                            douyin_search_exposure_definitions.c.task_id
                                            == task_target_confirmations.c.task_id,
                                            douyin_search_exposure_definitions.c.installation_id
                                            == task_target_confirmations.c.installation_id,
                                        ),
                                    )
                                )
                                .where(
                                    task_target_confirmations.c.task_id == command.task_id.uuid,
                                    task_target_confirmations.c.installation_id
                                    == command.installation_id.uuid,
                                )
                                .with_for_update()
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
                confirmation_message_id = (
                    None
                    if confirmation_row is None
                    else cast(UUID, confirmation_row["source_message_id"])
                )
                existing_row = (
                    (
                        await session.execute(
                            select(task_commands).where(
                                task_commands.c.installation_id == command.installation_id.uuid,
                                task_commands.c.idempotency_key == command.idempotency_key,
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing_row is not None:
                    existing = _record(existing_row)
                    if not _same_intent(
                        existing,
                        command,
                        target_confirmation_message_id=confirmation_message_id,
                    ):
                        raise TaskCommandDeliveryRejected
                    return existing
                if requires_target_confirmation and (
                    confirmation_row is None
                    or confirmation_row["status"] != TaskStatus.QUEUED.value
                    or confirmation_row["revision"] != confirmation_row["confirmed_task_revision"]
                    or confirmation_row["confirmation_action"]
                    != confirmation_row["definition_action"]
                    or confirmation_row["confirmation_message_template"]
                    != confirmation_row["definition_message_template"]
                ):
                    raise TaskCommandDeliveryRejected
                blocked = await session.scalar(
                    select(platform_session_gates.c.session_revision).where(
                        platform_session_gates.c.installation_id == command.installation_id.uuid,
                        platform_session_gates.c.platform == "douyin",
                    )
                )
                if blocked is not None:
                    raise TaskCommandDeliveryRejected
                created = (
                    (
                        await session.execute(
                            insert(task_commands)
                            .values(
                                message_id=command.message_id,
                                correlation_id=command.correlation_id,
                                installation_id=command.installation_id.uuid,
                                task_id=command.task_id.uuid,
                                execution_attempt_id=command.execution_attempt_id.uuid,
                                sequence=command.sequence,
                                command_type=command.command_type.value,
                                target_confirmation_message_id=confirmation_message_id,
                                status=TaskCommandStatus.PENDING.value,
                                idempotency_key=command.idempotency_key,
                                revision=1,
                                delivery_attempts=0,
                                next_delivery_at=command.created_at,
                                deadline_at=command.deadline_at,
                                created_at=command.created_at,
                                updated_at=command.created_at,
                            )
                            .returning(*task_commands.c)
                        )
                    )
                    .mappings()
                    .one()
                )
                return _record(created)
        except IntegrityError:
            raise TaskCommandDeliveryRejected from None

    async def enqueue_control(self, control: PendingTaskControl) -> TaskControlEnqueueResult:
        """Allocate one control, projecting only a first termination to CANCELLING."""

        if not isinstance(control, PendingTaskControl):
            raise TaskControlConflict
        is_termination = control.command_type in _TERMINATION_COMMANDS
        expected = (
            None
            if is_termination
            else {
                TaskCommandType.TASK_PAUSE: (
                    TaskStatus.RUNNING,
                    ExecutionAttemptStatus.RUNNING,
                ),
                TaskCommandType.TASK_RESUME: (
                    TaskStatus.PAUSED,
                    ExecutionAttemptStatus.PAUSED,
                ),
            }[control.command_type]
        )
        try:
            async with self._database.session() as session:
                installation_status = await session.scalar(
                    select(installations.c.status)
                    .where(installations.c.id == control.installation_id.uuid)
                    .with_for_update()
                )
                if installation_status != InstallationStatus.ACTIVE.value:
                    raise TaskControlNotFound

                existing_row = (
                    (
                        await session.execute(
                            select(task_commands).where(
                                task_commands.c.installation_id == control.installation_id.uuid,
                                task_commands.c.idempotency_key == control.idempotency_key,
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing_row is not None:
                    existing = _record(existing_row)
                    if (
                        existing.task_id != control.task_id
                        or existing.command_type is not control.command_type
                    ):
                        raise TaskControlConflict
                    return TaskControlEnqueueResult(command=existing, created=False)

                task_row = (
                    (
                        await session.execute(
                            select(tasks)
                            .where(
                                tasks.c.id == control.task_id.uuid,
                                tasks.c.installation_id == control.installation_id.uuid,
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if task_row is None:
                    raise TaskControlNotFound
                attempt_id = task_row["current_attempt_id"]
                if attempt_id is None:
                    raise TaskControlConflict
                attempt_row = (
                    (
                        await session.execute(
                            select(execution_attempts)
                            .where(
                                execution_attempts.c.id == attempt_id,
                                execution_attempts.c.task_id == control.task_id.uuid,
                                execution_attempts.c.installation_id
                                == control.installation_id.uuid,
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one()
                )
                if expected is not None and (
                    task_row["status"] != expected[0].value
                    or attempt_row["status"] != expected[1].value
                ):
                    raise TaskControlConflict
                if is_termination:
                    current_task_status = TaskStatus(cast(str, task_row["status"]))
                    current_attempt_status = ExecutionAttemptStatus(
                        cast(str, attempt_row["status"])
                    )
                    if (
                        not TaskStateMachine.can_transition(
                            current_task_status,
                            TaskStatus.CANCELLING,
                        )
                        or current_attempt_status not in _CANCELLABLE_ATTEMPT_STATUSES
                        or control.created_at < cast(datetime, task_row["updated_at"])
                        or control.created_at < cast(datetime, attempt_row["updated_at"])
                    ):
                        raise TaskControlConflict

                last_sequence = await session.scalar(
                    select(func.coalesce(func.max(task_commands.c.sequence), 0)).where(
                        task_commands.c.execution_attempt_id == attempt_id,
                        task_commands.c.task_id == control.task_id.uuid,
                        task_commands.c.installation_id == control.installation_id.uuid,
                    )
                )
                resolved_last_sequence = cast(int, last_sequence)
                if resolved_last_sequence >= (1 << 53) - 1:
                    raise TaskControlConflict
                created = (
                    (
                        await session.execute(
                            insert(task_commands)
                            .values(
                                message_id=control.message_id,
                                correlation_id=control.correlation_id,
                                installation_id=control.installation_id.uuid,
                                task_id=control.task_id.uuid,
                                execution_attempt_id=attempt_id,
                                sequence=resolved_last_sequence + 1,
                                command_type=control.command_type.value,
                                status=TaskCommandStatus.PENDING.value,
                                idempotency_key=control.idempotency_key,
                                revision=1,
                                delivery_attempts=0,
                                next_delivery_at=control.created_at,
                                deadline_at=control.deadline_at,
                                created_at=control.created_at,
                                updated_at=control.created_at,
                            )
                            .returning(*task_commands.c)
                        )
                    )
                    .mappings()
                    .one()
                )
                if is_termination:
                    updated_task = await session.execute(
                        update(tasks)
                        .where(
                            tasks.c.id == control.task_id.uuid,
                            tasks.c.installation_id == control.installation_id.uuid,
                            tasks.c.revision == task_row["revision"],
                            tasks.c.status == task_row["status"],
                        )
                        .values(
                            status=TaskStatus.CANCELLING.value,
                            revision=tasks.c.revision + 1,
                            updated_at=control.created_at,
                        )
                        .returning(tasks.c.id)
                    )
                    updated_task.scalar_one()
                    updated_attempt = await session.execute(
                        update(execution_attempts)
                        .where(
                            execution_attempts.c.id == attempt_id,
                            execution_attempts.c.revision == attempt_row["revision"],
                            execution_attempts.c.status == attempt_row["status"],
                        )
                        .values(
                            status=ExecutionAttemptStatus.CANCELLING.value,
                            revision=execution_attempts.c.revision + 1,
                            updated_at=control.created_at,
                        )
                        .returning(execution_attempts.c.id)
                    )
                    updated_attempt.scalar_one()
                return TaskControlEnqueueResult(command=_record(created), created=True)
        except (TaskControlNotFound, TaskControlConflict):
            raise
        except IntegrityError:
            raise TaskControlConflict from None

    async def expire_due(
        self,
        *,
        installation_id: InstallationId,
        now: datetime,
    ) -> int:
        if type(installation_id) is not InstallationId:
            raise TaskCommandDeliveryRejected
        timestamp = _aware_utc(now)
        async with self._database.session() as session:
            expired = (
                await session.scalars(
                    update(task_commands)
                    .where(
                        task_commands.c.installation_id == installation_id.uuid,
                        task_commands.c.status.in_(
                            (
                                TaskCommandStatus.PENDING.value,
                                TaskCommandStatus.IN_FLIGHT.value,
                                TaskCommandStatus.DELIVERED.value,
                            )
                        ),
                        task_commands.c.deadline_at <= timestamp,
                        task_commands.c.updated_at <= timestamp,
                    )
                    .values(
                        status=TaskCommandStatus.EXPIRED.value,
                        revision=task_commands.c.revision + 1,
                        next_delivery_at=None,
                        lease_expires_at=None,
                        acknowledged_at=None,
                        response_message_id=None,
                        response_type=None,
                        updated_at=timestamp,
                    )
                    .returning(task_commands.c.message_id)
                )
            ).all()
        return len(expired)

    async def claim_next(
        self,
        *,
        installation_id: InstallationId,
        now: datetime,
        lease_expires_at: datetime,
        retry_delivered_before: datetime,
        recover_delivered: bool,
    ) -> TaskCommandRecord | None:
        if type(installation_id) is not InstallationId or type(recover_delivered) is not bool:
            raise TaskCommandDeliveryRejected
        timestamp = _aware_utc(now)
        requested_lease = _aware_utc(lease_expires_at)
        retry_before = _aware_utc(retry_delivered_before)
        if requested_lease <= timestamp:
            raise TaskCommandDeliveryRejected
        delivered_due = (
            task_commands.c.delivered_at < retry_before
            if recover_delivered
            else task_commands.c.delivered_at <= retry_before
        )
        due = or_(
            and_(
                task_commands.c.status == TaskCommandStatus.PENDING.value,
                task_commands.c.next_delivery_at <= timestamp,
            ),
            and_(
                task_commands.c.status == TaskCommandStatus.IN_FLIGHT.value,
                task_commands.c.lease_expires_at <= timestamp,
            ),
            and_(
                task_commands.c.status == TaskCommandStatus.DELIVERED.value,
                delivered_due,
            ),
        )
        async with self._database.session() as session:
            installation_status = await session.scalar(
                select(installations.c.status)
                .where(installations.c.id == installation_id.uuid)
                .with_for_update()
            )
            if installation_status != InstallationStatus.ACTIVE.value:
                return None
            blocked = await session.scalar(
                select(platform_session_gates.c.session_revision).where(
                    platform_session_gates.c.installation_id == installation_id.uuid,
                    platform_session_gates.c.platform == "douyin",
                )
            )
            query = select(task_commands).where(
                task_commands.c.installation_id == installation_id.uuid,
                task_commands.c.deadline_at > timestamp,
                task_commands.c.updated_at <= timestamp,
                due,
                or_(
                    task_commands.c.command_type.not_in(
                        (
                            TaskCommandType.TASK_OFFER.value,
                            TaskCommandType.ACTION_EXECUTE.value,
                        )
                    ),
                    and_(
                        task_commands.c.target_confirmation_message_id.is_(None),
                        ~exists(
                            select(douyin_search_exposure_definitions.c.task_id).where(
                                douyin_search_exposure_definitions.c.task_id
                                == task_commands.c.task_id,
                                douyin_search_exposure_definitions.c.installation_id
                                == task_commands.c.installation_id,
                            )
                        ),
                    ),
                    and_(
                        task_commands.c.target_confirmation_message_id.is_not(None),
                        exists(
                            select(task_target_confirmations.c.task_id)
                            .select_from(
                                task_target_confirmations.join(
                                    tasks,
                                    and_(
                                        tasks.c.id == task_target_confirmations.c.task_id,
                                        tasks.c.installation_id
                                        == task_target_confirmations.c.installation_id,
                                    ),
                                ).join(
                                    douyin_search_exposure_definitions,
                                    and_(
                                        douyin_search_exposure_definitions.c.task_id
                                        == task_target_confirmations.c.task_id,
                                        douyin_search_exposure_definitions.c.installation_id
                                        == task_target_confirmations.c.installation_id,
                                    ),
                                )
                            )
                            .where(
                                task_target_confirmations.c.task_id == task_commands.c.task_id,
                                task_target_confirmations.c.installation_id
                                == task_commands.c.installation_id,
                                task_target_confirmations.c.source_message_id
                                == task_commands.c.target_confirmation_message_id,
                                tasks.c.status.in_(
                                    (TaskStatus.QUEUED.value, TaskStatus.RUNNING.value)
                                ),
                                tasks.c.revision
                                >= task_target_confirmations.c.confirmed_task_revision,
                                task_target_confirmations.c.action
                                == douyin_search_exposure_definitions.c.action,
                                task_target_confirmations.c.message_template.is_not_distinct_from(
                                    douyin_search_exposure_definitions.c.message_template
                                ),
                            )
                        ),
                    ),
                ),
            )
            if blocked is not None:
                query = query.where(
                    task_commands.c.command_type.in_(
                        command_type.value for command_type in _TERMINATION_COMMANDS
                    )
                )
            current = (
                (
                    await session.execute(
                        query.order_by(
                            task_commands.c.deadline_at,
                            task_commands.c.created_at,
                            task_commands.c.message_id,
                        )
                        .with_for_update(skip_locked=True)
                        .limit(1)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if current is None:
                return None
            deadline = cast(datetime, current["deadline_at"])
            lease_until = min(requested_lease, deadline)
            claimed = (
                (
                    await session.execute(
                        update(task_commands)
                        .where(
                            task_commands.c.message_id == current["message_id"],
                            task_commands.c.revision == current["revision"],
                        )
                        .values(
                            status=TaskCommandStatus.IN_FLIGHT.value,
                            revision=task_commands.c.revision + 1,
                            delivery_attempts=task_commands.c.delivery_attempts + 1,
                            next_delivery_at=None,
                            lease_expires_at=lease_until,
                            delivered_at=None,
                            acknowledged_at=None,
                            response_message_id=None,
                            response_type=None,
                            updated_at=timestamp,
                        )
                        .returning(*task_commands.c)
                    )
                )
                .mappings()
                .one()
            )
            record = _record(claimed)
            if record.command_type is TaskCommandType.TASK_DISCOVER:
                definition = (
                    (
                        await session.execute(
                            select(
                                douyin_search_exposure_definitions.c.search_keyword,
                                douyin_search_exposure_definitions.c.target_limit,
                                execution_attempts.c.attempt_number,
                            )
                            .select_from(
                                douyin_search_exposure_definitions.join(
                                    execution_attempts,
                                    execution_attempts.c.task_id
                                    == douyin_search_exposure_definitions.c.task_id,
                                )
                            )
                            .where(
                                douyin_search_exposure_definitions.c.task_id == record.task_id.uuid,
                                douyin_search_exposure_definitions.c.installation_id
                                == record.installation_id.uuid,
                                execution_attempts.c.id == record.execution_attempt_id.uuid,
                                execution_attempts.c.installation_id == record.installation_id.uuid,
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if definition is None:
                    raise TaskCommandDeliveryRejected
                record = replace(
                    record,
                    discovery_payload=DouyinDiscoveryCommandPayload.model_validate(
                        {
                            "discovery_version": DOUYIN_DISCOVERY_PROTOCOL_VERSION,
                            "keyword": definition["search_keyword"],
                            "target_limit": definition["target_limit"],
                            "page_revision": definition["attempt_number"],
                        }
                    ),
                )
            elif record.command_type is TaskCommandType.ACTION_EXECUTE:
                if record.action_id is None:
                    raise TaskCommandDeliveryRejected
                context_row = (
                    (
                        await session.execute(
                            select(
                                action_risk_authorizations,
                                task_targets.c.platform_target_id,
                                task_targets.c.display_name,
                                task_targets.c.public_handle,
                                task_targets.c.source,
                                task_targets.c.page_revision,
                                douyin_search_exposure_definitions.c.message_template,
                            )
                            .select_from(
                                action_risk_authorizations.join(
                                    task_targets,
                                    and_(
                                        task_targets.c.id == action_risk_authorizations.c.target_id,
                                        task_targets.c.task_id
                                        == action_risk_authorizations.c.task_id,
                                        task_targets.c.installation_id
                                        == action_risk_authorizations.c.installation_id,
                                    ),
                                ).join(
                                    douyin_search_exposure_definitions,
                                    and_(
                                        douyin_search_exposure_definitions.c.task_id
                                        == action_risk_authorizations.c.task_id,
                                        douyin_search_exposure_definitions.c.installation_id
                                        == action_risk_authorizations.c.installation_id,
                                    ),
                                )
                            )
                            .where(
                                action_risk_authorizations.c.action_id == record.action_id.uuid,
                                action_risk_authorizations.c.execution_attempt_id
                                == record.execution_attempt_id.uuid,
                                action_risk_authorizations.c.task_id == record.task_id.uuid,
                                action_risk_authorizations.c.installation_id
                                == record.installation_id.uuid,
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if context_row is None:
                    raise TaskCommandDeliveryRejected
                record = replace(
                    record,
                    action_context=ActionCommandContext(
                        authorization=action_authorization_record_from_row(context_row),
                        candidate=DouyinCandidate(
                            platform_target_id=cast(str, context_row["platform_target_id"]),
                            summary=DouyinCandidateSummary(
                                display_name=cast(str, context_row["display_name"]),
                                public_handle=cast(str | None, context_row["public_handle"]),
                            ),
                            source=DouyinCandidateSource(cast(str, context_row["source"])),
                            page_revision=cast(int, context_row["page_revision"]),
                        ),
                        message_template=cast(str | None, context_row["message_template"]),
                    ),
                )
            return record

    async def mark_delivered(
        self,
        *,
        message_id: UUID,
        expected_revision: int,
        delivered_at: datetime,
    ) -> TaskCommandRecord:
        timestamp = _aware_utc(delivered_at)
        if not isinstance(message_id, UUID) or type(expected_revision) is not int:
            raise TaskCommandDeliveryRejected
        async with self._database.session() as session:
            delivered = (
                (
                    await session.execute(
                        update(task_commands)
                        .where(
                            task_commands.c.message_id == message_id,
                            task_commands.c.revision == expected_revision,
                            task_commands.c.status == TaskCommandStatus.IN_FLIGHT.value,
                            task_commands.c.updated_at <= timestamp,
                            task_commands.c.deadline_at > timestamp,
                        )
                        .values(
                            status=TaskCommandStatus.DELIVERED.value,
                            revision=task_commands.c.revision + 1,
                            lease_expires_at=None,
                            delivered_at=timestamp,
                            updated_at=timestamp,
                        )
                        .returning(*task_commands.c)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if delivered is None:
                raise TaskCommandDeliveryRejected
            return _record(delivered)

    async def release_for_retry(
        self,
        *,
        message_id: UUID,
        expected_revision: int,
        now: datetime,
        retry_at: datetime,
    ) -> TaskCommandRecord:
        timestamp = _aware_utc(now)
        retry_timestamp = _aware_utc(retry_at)
        if (
            not isinstance(message_id, UUID)
            or type(expected_revision) is not int
            or retry_timestamp < timestamp
        ):
            raise TaskCommandDeliveryRejected
        async with self._database.session() as session:
            current = (
                (
                    await session.execute(
                        select(task_commands)
                        .where(
                            task_commands.c.message_id == message_id,
                            task_commands.c.revision == expected_revision,
                            task_commands.c.status == TaskCommandStatus.IN_FLIGHT.value,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if current is None or timestamp < current["updated_at"]:
                raise TaskCommandDeliveryRejected
            expired = (
                timestamp >= current["deadline_at"] or retry_timestamp >= current["deadline_at"]
            )
            released = (
                (
                    await session.execute(
                        update(task_commands)
                        .where(
                            task_commands.c.message_id == message_id,
                            task_commands.c.revision == expected_revision,
                        )
                        .values(
                            status=(
                                TaskCommandStatus.EXPIRED.value
                                if expired
                                else TaskCommandStatus.PENDING.value
                            ),
                            revision=task_commands.c.revision + 1,
                            next_delivery_at=None if expired else retry_timestamp,
                            lease_expires_at=None,
                            delivered_at=None,
                            acknowledged_at=None,
                            response_message_id=None,
                            response_type=None,
                            updated_at=timestamp,
                        )
                        .returning(*task_commands.c)
                    )
                )
                .mappings()
                .one()
            )
            return _record(released)

    async def acknowledge(
        self,
        *,
        response: TaskCommandResultEnvelope,
        received_at: datetime,
    ) -> TaskCommandRecord:
        if not isinstance(response, TaskCommandResultEnvelope) or not _response_payload_is_valid(
            response
        ):
            raise TaskCommandDeliveryRejected
        timestamp = _aware_utc(received_at)
        installation_id = InstallationId.parse(str(response.installation_id))
        task_id = TaskId.parse(str(response.task_id))
        attempt_id = ExecutionAttemptId.parse(str(response.execution_attempt_id))
        correlation_id = UUID(str(response.correlation_id))
        response_message_id = UUID(str(response.message_id))
        response_type = TaskCommandResponseType(response.message_type)
        try:
            async with self._database.session() as session:
                current_row = (
                    (
                        await session.execute(
                            select(task_commands)
                            .where(
                                task_commands.c.installation_id == installation_id.uuid,
                                task_commands.c.task_id == task_id.uuid,
                                task_commands.c.execution_attempt_id == attempt_id.uuid,
                                task_commands.c.correlation_id == correlation_id,
                                task_commands.c.sequence == response.sequence,
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if current_row is None:
                    raise TaskCommandDeliveryRejected
                current = _record(current_row)
                if not _response_matches_command(current.command_type, response_type):
                    raise TaskCommandDeliveryRejected
                if current.status in {
                    TaskCommandStatus.ACKNOWLEDGED,
                    TaskCommandStatus.REJECTED,
                }:
                    if current.response_type is not response_type:
                        raise TaskCommandDeliveryRejected
                    return current
                if current.status is TaskCommandStatus.EXPIRED:
                    return current
                if current.status is not TaskCommandStatus.DELIVERED:
                    raise TaskCommandDeliveryRejected
                if timestamp > current.deadline_at:
                    expired = (
                        (
                            await session.execute(
                                update(task_commands)
                                .where(
                                    task_commands.c.message_id == current.message_id,
                                    task_commands.c.revision == current.revision,
                                )
                                .values(
                                    status=TaskCommandStatus.EXPIRED.value,
                                    revision=task_commands.c.revision + 1,
                                    next_delivery_at=None,
                                    lease_expires_at=None,
                                    acknowledged_at=None,
                                    response_message_id=None,
                                    response_type=None,
                                    updated_at=timestamp,
                                )
                                .returning(*task_commands.c)
                            )
                        )
                        .mappings()
                        .one()
                    )
                    return _record(expired)
                final_status = (
                    TaskCommandStatus.REJECTED
                    if response_type
                    in {
                        TaskCommandResponseType.TASK_REJECT,
                        TaskCommandResponseType.ACTION_REJECT,
                    }
                    else TaskCommandStatus.ACKNOWLEDGED
                )
                acknowledged = (
                    (
                        await session.execute(
                            update(task_commands)
                            .where(
                                task_commands.c.message_id == current.message_id,
                                task_commands.c.revision == current.revision,
                            )
                            .values(
                                status=final_status.value,
                                revision=task_commands.c.revision + 1,
                                acknowledged_at=timestamp,
                                response_message_id=response_message_id,
                                response_type=response_type.value,
                                updated_at=timestamp,
                            )
                            .returning(*task_commands.c)
                        )
                    )
                    .mappings()
                    .one()
                )
                return _record(acknowledged)
        except IntegrityError:
            raise TaskCommandDeliveryRejected from None


__all__ = ["SqlAlchemyTaskCommandRepository"]
