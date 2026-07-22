"""PostgreSQL-backed progression from confirmation to one action command at a time."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

from sqlalchemy import and_, exists, func, insert, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from automation_tool.control_plane.application.action_execution_orchestration import (
    ActionExecutionAdvanceKind,
    ActionExecutionAdvanceResult,
    ActionExecutionOrchestrationRejected,
    ActionExecutionOrchestrationUnavailable,
    PendingActionExecutionAdvance,
)
from automation_tool.control_plane.application.action_risk_authorizations import (
    ActionRiskAuthorizationLimited,
    ActionRiskAuthorizationRejected,
    ActionRiskAuthorizationUnavailable,
)
from automation_tool.control_plane.domain import (
    MAX_TASK_EVENT_SEQUENCE,
    TERMINAL_ACTION_STATUSES,
    TERMINAL_EXECUTION_ATTEMPT_STATUSES,
    ActionId,
    ActionOutcome,
    ActionRiskPlatform,
    ActionRiskPolicy,
    ActionRiskScope,
    ActionStatus,
    DouyinCandidateDisposition,
    DouyinSearchExposureAction,
    ExecutionAttemptId,
    ExecutionAttemptStatus,
    InstallationStatus,
    TargetId,
    TaskCommandStatus,
    TaskCommandType,
    TaskEventType,
    TaskEventVersion,
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
    platform_session_health,
    task_actions,
    task_commands,
    task_events,
    task_target_confirmations,
    task_target_exclusions,
    task_targets,
    tasks,
)
from automation_tool.control_plane.infrastructure.database.session import Database
from automation_tool.protocol import ACTION_AUTHORIZATION_MAX_LIFETIME, PlatformSessionState

from .action_risk_authorization_repository import (
    SqlAlchemyActionRiskAuthorizationRepository,
)


@dataclass(frozen=True, slots=True)
class _NextAuthorization:
    task_id: TaskId
    attempt_id: ExecutionAttemptId
    target_id: TargetId
    action: DouyinSearchExposureAction


class SqlAlchemyActionExecutionOrchestrationRepository:
    """Serialize Installation progression while keeping authorization facts reusable."""

    def __init__(self, database: Database) -> None:
        if not isinstance(database, Database):
            raise ActionExecutionOrchestrationRejected
        self._database = database
        self._authorization = SqlAlchemyActionRiskAuthorizationRepository(database)

    async def advance(
        self,
        pending: PendingActionExecutionAdvance,
    ) -> ActionExecutionAdvanceResult:
        if not isinstance(pending, PendingActionExecutionAdvance):
            raise ActionExecutionOrchestrationRejected
        try:
            for _ in range(2):
                prepared = await self._prepare(pending)
                if isinstance(prepared, ActionExecutionAdvanceResult):
                    return prepared
                policy = ActionRiskPolicy(
                    scope=ActionRiskScope(
                        installation_id=pending.installation_id,
                        platform=ActionRiskPlatform.DOUYIN,
                        action=prepared.action,
                    ),
                    minimum_interval=timedelta(seconds=pending.limits.minimum_interval_seconds),
                    task_action_limit=pending.limits.task_action_limit,
                    daily_action_limit=pending.limits.daily_action_limit,
                    consecutive_failure_threshold=(pending.limits.consecutive_failure_threshold),
                )
                try:
                    authorization = await self._authorization.authorize(
                        action_id=ActionId.parse(pending.action_id),
                        target_id=prepared.target_id,
                        execution_attempt_id=prepared.attempt_id,
                        task_id=prepared.task_id,
                        installation_id=pending.installation_id,
                        policy=policy,
                        authorized_at=pending.requested_at,
                    )
                except ActionRiskAuthorizationLimited:
                    return ActionExecutionAdvanceResult(
                        kind=ActionExecutionAdvanceKind.RATE_LIMITED
                    )
                except ActionRiskAuthorizationRejected:
                    # A concurrent advance may have authorized the same ordinal first.
                    # Re-read once so that transaction's durable fact receives its Outbox.
                    continue
                return await self._enqueue_authorized(
                    pending,
                    action_id=authorization.action_id,
                )
            return ActionExecutionAdvanceResult(kind=ActionExecutionAdvanceKind.IDLE)
        except (ActionExecutionOrchestrationRejected, ActionExecutionOrchestrationUnavailable):
            raise
        except ActionRiskAuthorizationUnavailable:
            raise ActionExecutionOrchestrationUnavailable from None
        except (IntegrityError, OSError, SQLAlchemyError, TimeoutError):
            raise ActionExecutionOrchestrationUnavailable from None

    async def _prepare(
        self,
        pending: PendingActionExecutionAdvance,
    ) -> ActionExecutionAdvanceResult | _NextAuthorization:
        async with self._database.session() as session:
            status = await session.scalar(
                select(installations.c.status)
                .where(installations.c.id == pending.installation_id.uuid)
                .with_for_update()
            )
            if status != InstallationStatus.ACTIVE.value:
                return ActionExecutionAdvanceResult(kind=ActionExecutionAdvanceKind.IDLE)
            blocked = await session.scalar(
                select(platform_session_gates.c.session_revision).where(
                    platform_session_gates.c.installation_id == pending.installation_id.uuid,
                    platform_session_gates.c.platform == ActionRiskPlatform.DOUYIN.value,
                )
            )
            health = await session.scalar(
                select(platform_session_health.c.state).where(
                    platform_session_health.c.installation_id == pending.installation_id.uuid,
                    platform_session_health.c.platform == ActionRiskPlatform.DOUYIN.value,
                )
            )
            if blocked is not None or health != PlatformSessionState.HEALTHY.value:
                return ActionExecutionAdvanceResult(kind=ActionExecutionAdvanceKind.IDLE)

            uncommanded = await session.scalar(
                select(action_risk_authorizations.c.action_id)
                .select_from(
                    action_risk_authorizations.join(
                        task_actions,
                        task_actions.c.id == action_risk_authorizations.c.action_id,
                    ).outerjoin(
                        task_commands,
                        task_commands.c.action_id == action_risk_authorizations.c.action_id,
                    )
                )
                .where(
                    action_risk_authorizations.c.installation_id == pending.installation_id.uuid,
                    task_actions.c.status == ActionStatus.AUTHORIZED.value,
                    task_commands.c.action_id.is_(None),
                )
                .order_by(
                    action_risk_authorizations.c.authorized_at,
                    action_risk_authorizations.c.action_id,
                )
                .with_for_update(of=task_actions, skip_locked=True)
                .limit(1)
            )
            if uncommanded is not None:
                return await self._enqueue_authorized_locked(
                    session,
                    pending,
                    action_id=ActionId.parse(uncommanded),
                )

            active_attempt = (
                (
                    await session.execute(
                        select(execution_attempts)
                        .where(
                            execution_attempts.c.installation_id == pending.installation_id.uuid,
                            execution_attempts.c.status.not_in(
                                tuple(value.value for value in TERMINAL_EXECUTION_ATTEMPT_STATUSES)
                            ),
                        )
                        .order_by(execution_attempts.c.created_at, execution_attempts.c.id)
                        .with_for_update()
                        .limit(1)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if active_attempt is None:
                return await self._offer_next_confirmed_locked(session, pending)
            if active_attempt["status"] != ExecutionAttemptStatus.RUNNING.value:
                return ActionExecutionAdvanceResult(kind=ActionExecutionAdvanceKind.IDLE)

            task_row = (
                (
                    await session.execute(
                        select(tasks)
                        .where(
                            tasks.c.id == active_attempt["task_id"],
                            tasks.c.installation_id == pending.installation_id.uuid,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                task_row is None
                or task_row["status"] != TaskStatus.RUNNING.value
                or task_row["current_attempt_id"] != active_attempt["id"]
            ):
                return ActionExecutionAdvanceResult(kind=ActionExecutionAdvanceKind.IDLE)

            active_action = await session.scalar(
                select(task_actions.c.id).where(
                    task_actions.c.execution_attempt_id == active_attempt["id"],
                    task_actions.c.status.not_in(
                        tuple(value.value for value in TERMINAL_ACTION_STATUSES)
                    ),
                )
            )
            if active_action is not None:
                return ActionExecutionAdvanceResult(kind=ActionExecutionAdvanceKind.IDLE)

            definition = (
                (
                    await session.execute(
                        select(douyin_search_exposure_definitions.c.action).where(
                            douyin_search_exposure_definitions.c.task_id == task_row["id"],
                            douyin_search_exposure_definitions.c.installation_id
                            == pending.installation_id.uuid,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            confirmation = (
                (
                    await session.execute(
                        select(
                            task_target_confirmations.c.page_revision,
                            task_target_confirmations.c.selected_target_count,
                        ).where(
                            task_target_confirmations.c.task_id == task_row["id"],
                            task_target_confirmations.c.installation_id
                            == pending.installation_id.uuid,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if definition is None or confirmation is None:
                return ActionExecutionAdvanceResult(kind=ActionExecutionAdvanceKind.IDLE)
            target_id = await session.scalar(
                select(task_targets.c.id)
                .where(
                    task_targets.c.task_id == task_row["id"],
                    task_targets.c.installation_id == pending.installation_id.uuid,
                    task_targets.c.page_revision == confirmation["page_revision"],
                    task_targets.c.disposition == DouyinCandidateDisposition.ELIGIBLE.value,
                    ~exists(
                        select(task_target_exclusions.c.target_id).where(
                            task_target_exclusions.c.target_id == task_targets.c.id,
                            task_target_exclusions.c.task_id == task_targets.c.task_id,
                            task_target_exclusions.c.installation_id
                            == task_targets.c.installation_id,
                            task_target_exclusions.c.page_revision == task_targets.c.page_revision,
                        )
                    ),
                    ~exists(
                        select(action_risk_authorizations.c.action_id).where(
                            action_risk_authorizations.c.target_id == task_targets.c.id,
                            action_risk_authorizations.c.task_id == task_targets.c.task_id,
                            action_risk_authorizations.c.installation_id
                            == task_targets.c.installation_id,
                        )
                    ),
                )
                .order_by(task_targets.c.ordinal, task_targets.c.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if target_id is None:
                return await self._finalize_completed_task_locked(
                    session,
                    pending,
                    task_row=task_row,
                    active_attempt=active_attempt,
                    selected_target_count=cast(
                        int,
                        confirmation["selected_target_count"],
                    ),
                )
            return _NextAuthorization(
                task_id=TaskId.parse(task_row["id"]),
                attempt_id=ExecutionAttemptId.parse(active_attempt["id"]),
                target_id=TargetId.parse(target_id),
                action=DouyinSearchExposureAction(cast(str, definition["action"])),
            )

    async def _finalize_completed_task_locked(
        self,
        session: AsyncSession,
        pending: PendingActionExecutionAdvance,
        *,
        task_row: object,
        active_attempt: object,
        selected_target_count: int,
    ) -> ActionExecutionAdvanceResult:
        try:
            task = cast(dict[str, object], task_row)
            attempt = cast(dict[str, object], active_attempt)
            if selected_target_count <= 0:
                raise ValueError
            action_rows = (
                (
                    await session.execute(
                        select(task_actions.c.status, task_actions.c.outcome)
                        .where(
                            task_actions.c.execution_attempt_id == attempt["id"],
                            task_actions.c.task_id == task["id"],
                            task_actions.c.installation_id == pending.installation_id.uuid,
                        )
                        .order_by(task_actions.c.ordinal, task_actions.c.id)
                    )
                )
                .mappings()
                .all()
            )
            if len(action_rows) != selected_target_count or any(
                ActionStatus(cast(str, row["status"])) not in TERMINAL_ACTION_STATUSES
                for row in action_rows
            ):
                return ActionExecutionAdvanceResult(kind=ActionExecutionAdvanceKind.IDLE)
            outcomes = tuple(ActionOutcome(cast(str, row["outcome"])) for row in action_rows)
            succeeded = sum(outcome is ActionOutcome.SUCCEEDED for outcome in outcomes)
            if ActionOutcome.OUTCOME_UNCERTAIN in outcomes:
                task_status = TaskStatus.OUTCOME_UNCERTAIN
                attempt_status = ExecutionAttemptStatus.OUTCOME_UNCERTAIN
                event_type = TaskEventType.TASK_OUTCOME_UNCERTAIN
            elif succeeded == len(outcomes):
                task_status = TaskStatus.SUCCEEDED
                attempt_status = ExecutionAttemptStatus.SUCCEEDED
                event_type = TaskEventType.TASK_COMPLETED
            elif succeeded > 0:
                task_status = TaskStatus.PARTIALLY_SUCCEEDED
                attempt_status = ExecutionAttemptStatus.PARTIALLY_SUCCEEDED
                event_type = TaskEventType.TASK_PARTIALLY_COMPLETED
            else:
                task_status = TaskStatus.FAILED
                attempt_status = ExecutionAttemptStatus.FAILED
                event_type = TaskEventType.TASK_FAILED
            TaskStateMachine.transition(TaskStatus.RUNNING, task_status)
            last_event_sequence = cast(int, task["last_event_sequence"])
            if not 0 <= last_event_sequence < MAX_TASK_EVENT_SEQUENCE:
                raise ValueError
            next_sequence = last_event_sequence + 1
            next_revision = cast(int, task["revision"]) + 1
            source_idempotency_key = f"task:action-finalize:{attempt['id']}"
            fingerprint = hashlib.sha256(
                (
                    "task-action-finalize-v1:"
                    f"{pending.installation_id}:{task['id']}:{attempt['id']}:"
                    f"{next_sequence}:{task_status.value}"
                ).encode("ascii")
            ).digest()
            await session.execute(
                insert(task_events).values(
                    task_id=task["id"],
                    installation_id=pending.installation_id.uuid,
                    sequence=next_sequence,
                    event_version=TaskEventVersion.V1.value,
                    event_type=event_type.value,
                    task_revision=next_revision,
                    task_status=task_status.value,
                    execution_attempt_id=attempt["id"],
                    action_id=None,
                    source_message_id=None,
                    source_idempotency_key=source_idempotency_key,
                    source_fingerprint=fingerprint,
                    progress_percent=None,
                    occurred_at=pending.requested_at,
                    recorded_at=pending.requested_at,
                    safe_message=None,
                )
            )
            updated_attempt = await session.execute(
                update(execution_attempts)
                .where(
                    execution_attempts.c.id == attempt["id"],
                    execution_attempts.c.revision == attempt["revision"],
                    execution_attempts.c.status == ExecutionAttemptStatus.RUNNING.value,
                )
                .values(
                    status=attempt_status.value,
                    revision=cast(int, attempt["revision"]) + 1,
                    finished_at=pending.requested_at,
                    updated_at=pending.requested_at,
                )
                .returning(execution_attempts.c.id)
            )
            updated_attempt.scalar_one()
            updated_task = await session.execute(
                update(tasks)
                .where(
                    tasks.c.id == task["id"],
                    tasks.c.installation_id == pending.installation_id.uuid,
                    tasks.c.current_attempt_id == attempt["id"],
                    tasks.c.revision == task["revision"],
                    tasks.c.last_event_sequence == last_event_sequence,
                    tasks.c.status == TaskStatus.RUNNING.value,
                )
                .values(
                    status=task_status.value,
                    revision=next_revision,
                    last_event_sequence=next_sequence,
                    updated_at=pending.requested_at,
                )
                .returning(tasks.c.id)
            )
            updated_task.scalar_one()
            return ActionExecutionAdvanceResult(
                kind=ActionExecutionAdvanceKind.TASK_FINALIZED
            )
        except (KeyError, TypeError, ValueError):
            raise ActionExecutionOrchestrationRejected from None

    async def _offer_next_confirmed_locked(
        self,
        session: AsyncSession,
        pending: PendingActionExecutionAdvance,
    ) -> ActionExecutionAdvanceResult:
        # Kept local to the locked transaction: a second worker cannot allocate
        # another nonterminal Attempt for this Installation.
        result = await session.execute(
            select(
                tasks,
                task_target_confirmations.c.source_message_id,
            )
            .select_from(
                tasks.join(
                    task_target_confirmations,
                    and_(
                        task_target_confirmations.c.task_id == tasks.c.id,
                        task_target_confirmations.c.installation_id == tasks.c.installation_id,
                    ),
                ).join(
                    douyin_search_exposure_definitions,
                    and_(
                        douyin_search_exposure_definitions.c.task_id == tasks.c.id,
                        douyin_search_exposure_definitions.c.installation_id
                        == tasks.c.installation_id,
                    ),
                )
            )
            .where(
                tasks.c.installation_id == pending.installation_id.uuid,
                tasks.c.status == TaskStatus.QUEUED.value,
                tasks.c.current_attempt_id.is_(None),
                tasks.c.revision == task_target_confirmations.c.confirmed_task_revision,
                task_target_confirmations.c.selected_target_count > 0,
                task_target_confirmations.c.action == douyin_search_exposure_definitions.c.action,
                task_target_confirmations.c.message_template.is_not_distinct_from(
                    douyin_search_exposure_definitions.c.message_template
                ),
            )
            .order_by(tasks.c.updated_at, tasks.c.id)
            .with_for_update(of=tasks, skip_locked=True)
            .limit(1)
        )
        task_row = result.mappings().one_or_none()
        if task_row is None:
            return ActionExecutionAdvanceResult(kind=ActionExecutionAdvanceKind.IDLE)
        last_attempt = cast(
            int,
            await session.scalar(
                select(func.coalesce(func.max(execution_attempts.c.attempt_number), 0)).where(
                    execution_attempts.c.task_id == task_row["id"],
                    execution_attempts.c.installation_id == pending.installation_id.uuid,
                )
            ),
        )
        await session.execute(
            insert(execution_attempts).values(
                id=pending.execution_attempt_id,
                task_id=task_row["id"],
                installation_id=pending.installation_id.uuid,
                attempt_number=last_attempt + 1,
                status=ExecutionAttemptStatus.OFFERED.value,
                revision=1,
                created_at=pending.requested_at,
                updated_at=pending.requested_at,
            )
        )
        await session.execute(
            insert(task_commands).values(
                message_id=pending.message_id,
                correlation_id=pending.correlation_id,
                installation_id=pending.installation_id.uuid,
                task_id=task_row["id"],
                execution_attempt_id=pending.execution_attempt_id,
                sequence=1,
                command_type=TaskCommandType.TASK_OFFER.value,
                target_confirmation_message_id=task_row["source_message_id"],
                action_id=None,
                task_event_sequence_baseline=task_row["last_event_sequence"],
                status=TaskCommandStatus.PENDING.value,
                idempotency_key=f"task:offer:{pending.execution_attempt_id}",
                revision=1,
                delivery_attempts=0,
                next_delivery_at=pending.requested_at,
                deadline_at=pending.command_deadline_at,
                created_at=pending.requested_at,
                updated_at=pending.requested_at,
            )
        )
        updated = await session.execute(
            update(tasks)
            .where(
                tasks.c.id == task_row["id"],
                tasks.c.installation_id == pending.installation_id.uuid,
                tasks.c.current_attempt_id.is_(None),
                tasks.c.revision == task_row["revision"],
            )
            .values(
                current_attempt_id=pending.execution_attempt_id,
                updated_at=pending.requested_at,
            )
            .returning(tasks.c.id)
        )
        updated.scalar_one()
        return ActionExecutionAdvanceResult(kind=ActionExecutionAdvanceKind.TASK_OFFERED)

    async def _enqueue_authorized(
        self,
        pending: PendingActionExecutionAdvance,
        *,
        action_id: ActionId,
    ) -> ActionExecutionAdvanceResult:
        async with self._database.session() as session:
            status = await session.scalar(
                select(installations.c.status)
                .where(installations.c.id == pending.installation_id.uuid)
                .with_for_update()
            )
            if status != InstallationStatus.ACTIVE.value:
                raise ActionExecutionOrchestrationRejected
            return await self._enqueue_authorized_locked(
                session,
                pending,
                action_id=action_id,
            )

    async def _enqueue_authorized_locked(
        self,
        session: AsyncSession,
        pending: PendingActionExecutionAdvance,
        *,
        action_id: ActionId,
    ) -> ActionExecutionAdvanceResult:
        existing = await session.scalar(
            select(task_commands.c.message_id).where(task_commands.c.action_id == action_id.uuid)
        )
        if existing is not None:
            return ActionExecutionAdvanceResult(kind=ActionExecutionAdvanceKind.ACTION_ENQUEUED)
        row = (
            (
                await session.execute(
                    select(
                        action_risk_authorizations.c.task_id,
                        action_risk_authorizations.c.execution_attempt_id,
                        action_risk_authorizations.c.authorized_at,
                        task_target_confirmations.c.source_message_id,
                        tasks.c.status.label("task_status"),
                        tasks.c.current_attempt_id,
                        execution_attempts.c.status.label("attempt_status"),
                    )
                    .select_from(
                        action_risk_authorizations.join(
                            tasks,
                            and_(
                                tasks.c.id == action_risk_authorizations.c.task_id,
                                tasks.c.installation_id
                                == action_risk_authorizations.c.installation_id,
                            ),
                        )
                        .join(
                            execution_attempts,
                            execution_attempts.c.id
                            == action_risk_authorizations.c.execution_attempt_id,
                        )
                        .join(
                            task_target_confirmations,
                            and_(
                                task_target_confirmations.c.task_id
                                == action_risk_authorizations.c.task_id,
                                task_target_confirmations.c.installation_id
                                == action_risk_authorizations.c.installation_id,
                            ),
                        )
                    )
                    .where(
                        action_risk_authorizations.c.action_id == action_id.uuid,
                        action_risk_authorizations.c.installation_id
                        == pending.installation_id.uuid,
                    )
                    .with_for_update(of=action_risk_authorizations)
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            row is None
            or row["task_status"] != TaskStatus.RUNNING.value
            or row["attempt_status"] != ExecutionAttemptStatus.RUNNING.value
            or row["current_attempt_id"] != row["execution_attempt_id"]
        ):
            raise ActionExecutionOrchestrationRejected
        deadline = min(
            pending.command_deadline_at,
            cast(datetime, row["authorized_at"]) + ACTION_AUTHORIZATION_MAX_LIFETIME,
        )
        if deadline <= pending.requested_at:
            raise ActionExecutionOrchestrationRejected
        last_sequence = cast(
            int,
            await session.scalar(
                select(func.coalesce(func.max(task_commands.c.sequence), 0)).where(
                    task_commands.c.execution_attempt_id == row["execution_attempt_id"]
                )
            ),
        )
        await session.execute(
            insert(task_commands).values(
                message_id=pending.message_id,
                correlation_id=pending.correlation_id,
                installation_id=pending.installation_id.uuid,
                task_id=row["task_id"],
                execution_attempt_id=row["execution_attempt_id"],
                sequence=last_sequence + 1,
                command_type=TaskCommandType.ACTION_EXECUTE.value,
                target_confirmation_message_id=row["source_message_id"],
                action_id=action_id.uuid,
                status=TaskCommandStatus.PENDING.value,
                idempotency_key=f"action:{action_id}",
                revision=1,
                delivery_attempts=0,
                next_delivery_at=pending.requested_at,
                deadline_at=deadline,
                created_at=pending.requested_at,
                updated_at=pending.requested_at,
            )
        )
        return ActionExecutionAdvanceResult(kind=ActionExecutionAdvanceKind.ACTION_ENQUEUED)


__all__ = ["SqlAlchemyActionExecutionOrchestrationRepository"]
