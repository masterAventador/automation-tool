"""Atomic PostgreSQL counting and authorization for platform side effects."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import func, insert, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from automation_tool.control_plane.application.action_risk_authorizations import (
    ActionRiskAuthorization,
    ActionRiskAuthorizationLimited,
    ActionRiskAuthorizationRejected,
    ActionRiskAuthorizationUnavailable,
    ActionRiskLimitReason,
)
from automation_tool.control_plane.domain import (
    ActionId,
    ActionRiskPlatform,
    ActionRiskPolicy,
    ActionStatus,
    DouyinCandidateDisposition,
    DouyinSearchExposureAction,
    ExecutionAttemptId,
    ExecutionAttemptStatus,
    InstallationId,
    InstallationStatus,
    TargetId,
    TaskId,
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
    task_target_confirmations,
    task_target_exclusions,
    task_targets,
    tasks,
)
from automation_tool.control_plane.infrastructure.database.session import Database
from automation_tool.protocol import PlatformSessionState


def _canonical_utc(value: object) -> datetime | None:
    if type(value) is not datetime or value.tzinfo is None:
        return None
    try:
        if value.utcoffset() != timedelta(0):
            return None
        return value.astimezone(UTC)
    except Exception:
        return None


def _record(row: RowMapping) -> ActionRiskAuthorization:
    try:
        return ActionRiskAuthorization(
            action_id=ActionId.parse(row["action_id"]),
            target_id=TargetId.parse(row["target_id"]),
            execution_attempt_id=ExecutionAttemptId.parse(row["execution_attempt_id"]),
            task_id=TaskId.parse(row["task_id"]),
            installation_id=InstallationId.parse(row["installation_id"]),
            ordinal=cast(int, row["ordinal"]),
            platform=ActionRiskPlatform(cast(str, row["platform"])),
            action=DouyinSearchExposureAction(cast(str, row["action"])),
            policy_version=cast(str, row["policy_version"]),
            effective_minimum_interval_seconds=cast(
                int,
                row["effective_minimum_interval_seconds"],
            ),
            task_action_limit=cast(int, row["task_action_limit"]),
            daily_action_limit=cast(int, row["daily_action_limit"]),
            consecutive_failure_threshold=cast(
                int,
                row["consecutive_failure_threshold"],
            ),
            task_count_after=cast(int, row["task_count_after"]),
            daily_count_after=cast(int, row["daily_count_after"]),
            authorized_day=row["authorized_day"],
            authorized_at=cast(datetime, row["authorized_at"]),
            created_at=cast(datetime, row["created_at"]),
        )
    except (KeyError, TypeError, ValueError):
        raise ActionRiskAuthorizationRejected from None


def _same_intent(
    existing: ActionRiskAuthorization,
    *,
    action_id: ActionId,
    target_id: TargetId,
    execution_attempt_id: ExecutionAttemptId,
    task_id: TaskId,
    installation_id: InstallationId,
    action: DouyinSearchExposureAction,
) -> bool:
    return (
        existing.action_id == action_id
        and existing.target_id == target_id
        and existing.execution_attempt_id == execution_attempt_id
        and existing.task_id == task_id
        and existing.installation_id == installation_id
        and existing.action is action
    )


class SqlAlchemyActionRiskAuthorizationRepository:
    """Serialize each Installation and persist one fact per granted Action ID."""

    def __init__(self, database: Database) -> None:
        if not isinstance(database, Database):
            raise ActionRiskAuthorizationUnavailable
        self._database = database

    async def authorize(
        self,
        *,
        action_id: ActionId,
        target_id: TargetId,
        execution_attempt_id: ExecutionAttemptId,
        task_id: TaskId,
        installation_id: InstallationId,
        policy: ActionRiskPolicy,
        authorized_at: datetime,
    ) -> ActionRiskAuthorization:
        timestamp = _canonical_utc(authorized_at)
        if (
            not isinstance(action_id, ActionId)
            or not isinstance(target_id, TargetId)
            or not isinstance(execution_attempt_id, ExecutionAttemptId)
            or not isinstance(task_id, TaskId)
            or not isinstance(installation_id, InstallationId)
            or not isinstance(policy, ActionRiskPolicy)
            or policy.scope.installation_id != installation_id
            or timestamp is None
        ):
            raise ActionRiskAuthorizationRejected
        try:
            async with self._database.session() as session:
                installation_status = await session.scalar(
                    select(installations.c.status)
                    .where(installations.c.id == installation_id.uuid)
                    .with_for_update()
                )
                if installation_status != InstallationStatus.ACTIVE.value:
                    raise ActionRiskAuthorizationRejected

                existing_row = (
                    (
                        await session.execute(
                            select(action_risk_authorizations).where(
                                action_risk_authorizations.c.action_id == action_id.uuid
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
                        action_id=action_id,
                        target_id=target_id,
                        execution_attempt_id=execution_attempt_id,
                        task_id=task_id,
                        installation_id=installation_id,
                        action=policy.scope.action,
                    ):
                        raise ActionRiskAuthorizationRejected
                    return existing

                task_row = (
                    (
                        await session.execute(
                            select(
                                tasks.c.status,
                                tasks.c.revision,
                                tasks.c.current_attempt_id,
                                tasks.c.updated_at,
                            )
                            .where(
                                tasks.c.id == task_id.uuid,
                                tasks.c.installation_id == installation_id.uuid,
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
                            select(execution_attempts.c.status, execution_attempts.c.updated_at)
                            .where(
                                execution_attempts.c.id == execution_attempt_id.uuid,
                                execution_attempts.c.task_id == task_id.uuid,
                                execution_attempts.c.installation_id == installation_id.uuid,
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
                    or task_row["current_attempt_id"] != execution_attempt_id.uuid
                    or attempt_row is None
                    or attempt_row["status"] != ExecutionAttemptStatus.RUNNING.value
                    or timestamp < cast(datetime, task_row["updated_at"])
                    or timestamp < cast(datetime, attempt_row["updated_at"])
                ):
                    raise ActionRiskAuthorizationRejected

                definition = (
                    (
                        await session.execute(
                            select(
                                douyin_search_exposure_definitions.c.action,
                                douyin_search_exposure_definitions.c.minimum_interval_seconds,
                            ).where(
                                douyin_search_exposure_definitions.c.task_id == task_id.uuid,
                                douyin_search_exposure_definitions.c.installation_id
                                == installation_id.uuid,
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if definition is None or definition["action"] != policy.scope.action.value:
                    raise ActionRiskAuthorizationRejected

                session_state = await session.scalar(
                    select(platform_session_health.c.state).where(
                        platform_session_health.c.installation_id == installation_id.uuid,
                        platform_session_health.c.platform == policy.scope.platform.value,
                    )
                )
                blocked = await session.scalar(
                    select(platform_session_gates.c.session_revision).where(
                        platform_session_gates.c.installation_id == installation_id.uuid,
                        platform_session_gates.c.platform == policy.scope.platform.value,
                    )
                )
                if session_state != PlatformSessionState.HEALTHY.value or blocked is not None:
                    raise ActionRiskAuthorizationRejected

                confirmation = (
                    (
                        await session.execute(
                            select(
                                task_target_confirmations.c.page_revision,
                                task_target_confirmations.c.confirmed_task_revision,
                                task_target_confirmations.c.confirmed_at,
                            )
                            .where(
                                task_target_confirmations.c.task_id == task_id.uuid,
                                task_target_confirmations.c.installation_id == installation_id.uuid,
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                target = (
                    (
                        await session.execute(
                            select(
                                task_targets.c.ordinal,
                                task_targets.c.page_revision,
                                task_targets.c.disposition,
                            )
                            .where(
                                task_targets.c.id == target_id.uuid,
                                task_targets.c.task_id == task_id.uuid,
                                task_targets.c.installation_id == installation_id.uuid,
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                excluded = await session.scalar(
                    select(task_target_exclusions.c.target_id).where(
                        task_target_exclusions.c.target_id == target_id.uuid,
                        task_target_exclusions.c.task_id == task_id.uuid,
                        task_target_exclusions.c.installation_id == installation_id.uuid,
                    )
                )
                if (
                    confirmation is None
                    or target is None
                    or target["page_revision"] != confirmation["page_revision"]
                    or target["disposition"] != DouyinCandidateDisposition.ELIGIBLE.value
                    or excluded is not None
                    or cast(int, task_row["revision"])
                    < cast(int, confirmation["confirmed_task_revision"])
                    or timestamp < cast(datetime, confirmation["confirmed_at"])
                ):
                    raise ActionRiskAuthorizationRejected

                platform = policy.scope.platform.value
                action = policy.scope.action.value
                task_count = cast(
                    int,
                    await session.scalar(
                        select(func.count())
                        .select_from(action_risk_authorizations)
                        .where(
                            action_risk_authorizations.c.task_id == task_id.uuid,
                            action_risk_authorizations.c.platform == platform,
                            action_risk_authorizations.c.action == action,
                        )
                    ),
                )
                daily_count = cast(
                    int,
                    await session.scalar(
                        select(func.count())
                        .select_from(action_risk_authorizations)
                        .where(
                            action_risk_authorizations.c.installation_id == installation_id.uuid,
                            action_risk_authorizations.c.platform == platform,
                            action_risk_authorizations.c.action == action,
                            action_risk_authorizations.c.authorized_day == timestamp.date(),
                        )
                    ),
                )
                last_authorized_at = await session.scalar(
                    select(func.max(action_risk_authorizations.c.authorized_at)).where(
                        action_risk_authorizations.c.installation_id == installation_id.uuid,
                        action_risk_authorizations.c.platform == platform,
                        action_risk_authorizations.c.action == action,
                    )
                )
                if task_count >= policy.task_action_limit:
                    raise ActionRiskAuthorizationLimited(ActionRiskLimitReason.TASK_ACTION_LIMIT)
                if daily_count >= policy.daily_action_limit:
                    raise ActionRiskAuthorizationLimited(ActionRiskLimitReason.DAILY_ACTION_LIMIT)

                effective_interval = max(
                    policy.minimum_interval_seconds,
                    cast(int, definition["minimum_interval_seconds"]),
                )
                if last_authorized_at is not None:
                    previous = cast(datetime, last_authorized_at)
                    if timestamp < previous:
                        raise ActionRiskAuthorizationRejected
                    if timestamp < previous + timedelta(seconds=effective_interval):
                        raise ActionRiskAuthorizationLimited(ActionRiskLimitReason.MINIMUM_INTERVAL)

                ordinal = cast(int, target["ordinal"])
                await session.execute(
                    insert(task_actions).values(
                        id=action_id.uuid,
                        execution_attempt_id=execution_attempt_id.uuid,
                        task_id=task_id.uuid,
                        installation_id=installation_id.uuid,
                        ordinal=ordinal,
                        status=ActionStatus.AUTHORIZED.value,
                        revision=1,
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                )
                created = (
                    (
                        await session.execute(
                            insert(action_risk_authorizations)
                            .values(
                                action_id=action_id.uuid,
                                target_id=target_id.uuid,
                                execution_attempt_id=execution_attempt_id.uuid,
                                task_id=task_id.uuid,
                                installation_id=installation_id.uuid,
                                ordinal=ordinal,
                                platform=platform,
                                action=action,
                                policy_version=policy.version,
                                effective_minimum_interval_seconds=effective_interval,
                                task_action_limit=policy.task_action_limit,
                                daily_action_limit=policy.daily_action_limit,
                                consecutive_failure_threshold=(
                                    policy.consecutive_failure_threshold
                                ),
                                task_count_after=task_count + 1,
                                daily_count_after=daily_count + 1,
                                authorized_day=timestamp.date(),
                                authorized_at=timestamp,
                                created_at=timestamp,
                            )
                            .returning(*action_risk_authorizations.c)
                        )
                    )
                    .mappings()
                    .one()
                )
                return _record(created)
        except ActionRiskAuthorizationLimited:
            raise
        except ActionRiskAuthorizationRejected:
            raise
        except IntegrityError:
            raise ActionRiskAuthorizationRejected from None
        except (OSError, SQLAlchemyError, TimeoutError):
            raise ActionRiskAuthorizationUnavailable from None


__all__ = ["SqlAlchemyActionRiskAuthorizationRepository"]
