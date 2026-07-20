from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError

from automation_tool.control_plane.application.action_risk_authorizations import (
    ActionRiskAuthorization,
    ActionRiskAuthorizationLimited,
    ActionRiskAuthorizationRejected,
    ActionRiskAuthorizationUnavailable,
    ActionRiskLimitReason,
)
from automation_tool.control_plane.domain import (
    DOUYIN_CANDIDATE_POLICY_VERSION,
    DOUYIN_SEARCH_EXPOSURE_TEMPLATE,
    ActionId,
    ActionRiskPlatform,
    ActionRiskPolicy,
    ActionRiskScope,
    DouyinCandidateDisposition,
    DouyinSearchExposureAction,
    ExecutionAttemptId,
    ExecutionAttemptStatus,
    InstallationId,
    TargetId,
    TaskId,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    SqlAlchemyActionRiskAuthorizationRepository,
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
from automation_tool.protocol import DouyinCandidateSource, PlatformSessionState

PREVIOUS_REVISION = "20260720_0019"
HEAD_REVISION = "20260720_0020"
NOW = datetime(2026, 7, 20, 1, 0, tzinfo=UTC)
EXPECTED_COLUMNS = {
    "action_id",
    "target_id",
    "execution_attempt_id",
    "task_id",
    "installation_id",
    "ordinal",
    "platform",
    "action",
    "policy_version",
    "effective_minimum_interval_seconds",
    "task_action_limit",
    "daily_action_limit",
    "consecutive_failure_threshold",
    "task_count_after",
    "daily_count_after",
    "authorized_day",
    "authorized_at",
    "created_at",
}
EXPECTED_CONSTRAINTS = {
    "pk_action_risk_authorizations",
    "fk_action_risk_authorizations_action_binding",
    "fk_action_risk_authorizations_target_binding",
    "ck_action_risk_authorizations_platform",
    "ck_action_risk_authorizations_action",
    "ck_action_risk_authorizations_policy_version",
    "ck_action_risk_authorizations_interval",
    "ck_action_risk_authorizations_limits",
    "ck_action_risk_authorizations_counts",
    "ck_action_risk_authorizations_utc_day",
    "ck_action_risk_authorizations_time_order",
    "uq_action_risk_authorizations_task_count",
    "uq_action_risk_authorizations_daily_count",
}


@dataclass(frozen=True, slots=True)
class RunnableTarget:
    task_id: TaskId
    attempt_id: ExecutionAttemptId
    target_id: TargetId


async def reset_data(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(action_risk_authorizations))
        await session.execute(delete(task_actions))
        await session.execute(update(tasks).values(current_attempt_id=None))
        await session.execute(delete(execution_attempts))
        await session.execute(delete(task_target_confirmations))
        await session.execute(delete(task_target_exclusions))
        await session.execute(delete(task_targets))
        await session.execute(delete(douyin_search_exposure_definitions))
        await session.execute(delete(platform_session_gates))
        await session.execute(delete(platform_session_health))
        await session.execute(delete(tasks))
        await session.execute(delete(installations))


async def seed_installation(database: Database) -> InstallationId:
    installation_id = InstallationId.new()
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
            insert(platform_session_health).values(
                installation_id=installation_id.uuid,
                platform="douyin",
                state=PlatformSessionState.HEALTHY.value,
                session_revision=1,
                observed_at=NOW,
                updated_at=NOW,
            )
        )
    return installation_id


async def seed_runnable_target(
    database: Database,
    installation_id: InstallationId,
    *,
    action: DouyinSearchExposureAction = DouyinSearchExposureAction.COMMENT,
    definition_interval_seconds: int = 10,
    target_count: int = 1,
) -> tuple[RunnableTarget, ...]:
    task_id = TaskId.new()
    attempt_id = ExecutionAttemptId.new()
    target_ids = tuple(TargetId.new() for _ in range(target_count))
    async with database.session() as session:
        await session.execute(
            insert(tasks).values(
                id=task_id.uuid,
                installation_id=installation_id.uuid,
                creation_idempotency_key=f"task:a702:{task_id}",
                status=TaskStatus.RUNNING.value,
                revision=6,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.execute(
            insert(douyin_search_exposure_definitions).values(
                task_id=task_id.uuid,
                installation_id=installation_id.uuid,
                template=DOUYIN_SEARCH_EXPOSURE_TEMPLATE,
                search_keyword="新能源汽车",
                action=action.value,
                message_template=None if action is DouyinSearchExposureAction.BROWSE else "您好",
                target_limit=target_count,
                minimum_interval_seconds=definition_interval_seconds,
                maximum_interval_seconds=max(definition_interval_seconds, 30),
                preview_required=True,
                final_confirmation_required=True,
            )
        )
        for ordinal, target_id in enumerate(target_ids, start=1):
            await session.execute(
                insert(task_targets).values(
                    id=target_id.uuid,
                    task_id=task_id.uuid,
                    installation_id=installation_id.uuid,
                    ordinal=ordinal,
                    platform_target_id=f"target-{ordinal}",
                    dedupe_key="atdck1_" + f"{ordinal:043d}",
                    display_name=f"目标 {ordinal}",
                    public_handle=None,
                    source=DouyinCandidateSource.GENERAL_SEARCH_AUTHOR.value,
                    page_revision=1,
                    disposition=DouyinCandidateDisposition.ELIGIBLE.value,
                    policy_version=DOUYIN_CANDIDATE_POLICY_VERSION,
                    evaluated_at=NOW,
                    created_at=NOW,
                )
            )
        await session.execute(
            insert(task_target_confirmations).values(
                task_id=task_id.uuid,
                installation_id=installation_id.uuid,
                page_revision=1,
                selection_task_revision=4,
                confirmed_task_revision=5,
                selected_target_count=target_count,
                source_message_id=TaskId.new().uuid,
                source_idempotency_key=f"target:confirm:a702:{task_id}",
                source_fingerprint=secrets.token_bytes(32),
                confirmed_at=NOW,
                created_at=NOW,
            )
        )
        await session.execute(
            insert(execution_attempts).values(
                id=attempt_id.uuid,
                task_id=task_id.uuid,
                installation_id=installation_id.uuid,
                attempt_number=1,
                status=ExecutionAttemptStatus.RUNNING.value,
                revision=1,
                created_at=NOW,
                updated_at=NOW,
                started_at=NOW,
            )
        )
        await session.execute(
            update(tasks)
            .where(tasks.c.id == task_id.uuid)
            .values(current_attempt_id=attempt_id.uuid)
        )
    return tuple(
        RunnableTarget(task_id=task_id, attempt_id=attempt_id, target_id=target_id)
        for target_id in target_ids
    )


def policy(
    installation_id: InstallationId,
    *,
    minimum_interval_seconds: int = 5,
    task_action_limit: int = 2,
    daily_action_limit: int = 3,
) -> ActionRiskPolicy:
    return ActionRiskPolicy(
        scope=ActionRiskScope(
            installation_id=installation_id,
            platform=ActionRiskPlatform.DOUYIN,
            action=DouyinSearchExposureAction.COMMENT,
        ),
        minimum_interval=timedelta(seconds=minimum_interval_seconds),
        task_action_limit=task_action_limit,
        daily_action_limit=daily_action_limit,
        consecutive_failure_threshold=3,
    )


async def authorize(
    repository: SqlAlchemyActionRiskAuthorizationRepository,
    target: RunnableTarget,
    installation_id: InstallationId,
    *,
    action_id: ActionId | None = None,
    authorized_at: datetime = NOW,
    risk_policy: ActionRiskPolicy | None = None,
) -> ActionRiskAuthorization:
    return await repository.authorize(
        action_id=action_id or ActionId.new(),
        target_id=target.target_id,
        execution_attempt_id=target.attempt_id,
        task_id=target.task_id,
        installation_id=installation_id,
        policy=risk_policy or policy(installation_id),
        authorized_at=authorized_at,
    )


@pytest.mark.asyncio
async def test_migration_creates_exact_authorization_table_and_downgrades_cleanly(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    alembic_runner(postgresql_url, "check")
    database = Database.from_url(postgresql_url)
    try:
        async with database.session() as session:
            revision = await session.scalar(text("select version_num from alembic_version"))
            columns = set(
                await session.scalars(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_schema = 'public' "
                        "and table_name = 'action_risk_authorizations'"
                    )
                )
            )
            constraints = set(
                await session.scalars(
                    text(
                        "select conname from pg_constraint where conrelid = "
                        "'public.action_risk_authorizations'::regclass"
                    )
                )
            )
            indexes = set(
                await session.scalars(
                    text(
                        "select indexname from pg_indexes where schemaname = 'public' "
                        "and tablename = 'action_risk_authorizations'"
                    )
                )
            )
        assert revision == HEAD_REVISION
        assert columns == EXPECTED_COLUMNS
        assert constraints >= EXPECTED_CONSTRAINTS
        assert {
            "ix_action_risk_authorizations_scope_time",
            "ix_action_risk_authorizations_task_scope",
        } <= indexes

        alembic_runner(postgresql_url, "downgrade", PREVIOUS_REVISION)
        async with database.session() as session:
            assert await session.scalar(text("select version_num from alembic_version")) == (
                PREVIOUS_REVISION
            )
            assert (
                await session.scalar(
                    text("select to_regclass('public.action_risk_authorizations')")
                )
                is None
            )
    finally:
        await database.close()
        alembic_runner(postgresql_url, "upgrade", "head")


@pytest.mark.asyncio
async def test_authorization_persists_action_policy_snapshot_and_exact_replay(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyActionRiskAuthorizationRepository(database)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        target = (await seed_runnable_target(database, installation_id))[0]
        action_id = ActionId.new()

        first = await authorize(repository, target, installation_id, action_id=action_id)
        replay = await authorize(
            repository,
            target,
            installation_id,
            action_id=action_id,
            authorized_at=NOW + timedelta(minutes=1),
        )

        assert replay == first
        assert first.effective_minimum_interval_seconds == 10
        assert first.task_count_after == first.daily_count_after == 1
        assert first.remaining_task_actions == 1
        assert first.remaining_daily_actions == 2
        async with database.session() as session:
            assert await session.scalar(select(func.count()).select_from(task_actions)) == 1
            assert (
                await session.scalar(select(func.count()).select_from(action_risk_authorizations))
                == 1
            )
        with pytest.raises(ActionRiskAuthorizationRejected):
            await repository.authorize(
                action_id=action_id,
                target_id=TargetId.new(),
                execution_attempt_id=target.attempt_id,
                task_id=target.task_id,
                installation_id=installation_id,
                policy=policy(installation_id),
                authorized_at=NOW,
            )
        with pytest.raises(ActionRiskAuthorizationRejected):
            await repository.authorize(
                action_id=action_id,
                target_id=target.target_id,
                execution_attempt_id=target.attempt_id,
                task_id=target.task_id,
                installation_id=installation_id,
                policy=ActionRiskPolicy(
                    scope=ActionRiskScope(
                        installation_id=installation_id,
                        platform=ActionRiskPlatform.DOUYIN,
                        action=DouyinSearchExposureAction.BROWSE,
                    ),
                    minimum_interval=timedelta(seconds=5),
                    task_action_limit=2,
                    daily_action_limit=3,
                    consecutive_failure_threshold=3,
                ),
                authorized_at=NOW,
            )
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_minimum_interval_task_and_utc_daily_limits_are_all_enforced(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyActionRiskAuthorizationRepository(database)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        first_task = await seed_runnable_target(database, installation_id, target_count=3)
        await authorize(repository, first_task[0], installation_id)
        with pytest.raises(ActionRiskAuthorizationLimited) as interval:
            await authorize(
                repository,
                first_task[1],
                installation_id,
                authorized_at=NOW + timedelta(seconds=9),
            )
        assert interval.value.reason is ActionRiskLimitReason.MINIMUM_INTERVAL

        await authorize(
            repository,
            first_task[1],
            installation_id,
            authorized_at=NOW + timedelta(seconds=10),
        )
        with pytest.raises(ActionRiskAuthorizationLimited) as task_limit:
            await authorize(
                repository,
                first_task[2],
                installation_id,
                authorized_at=NOW + timedelta(seconds=20),
            )
        assert task_limit.value.reason is ActionRiskLimitReason.TASK_ACTION_LIMIT

        second_task = (await seed_runnable_target(database, installation_id))[0]
        await authorize(
            repository,
            second_task,
            installation_id,
            authorized_at=NOW + timedelta(seconds=20),
        )
        third_task = (await seed_runnable_target(database, installation_id))[0]
        with pytest.raises(ActionRiskAuthorizationLimited) as daily_limit:
            await authorize(
                repository,
                third_task,
                installation_id,
                authorized_at=NOW + timedelta(seconds=30),
            )
        assert daily_limit.value.reason is ActionRiskLimitReason.DAILY_ACTION_LIMIT
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_concurrent_authorizations_cannot_cross_any_hard_limit(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyActionRiskAuthorizationRepository(database)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        targets = await seed_runnable_target(database, installation_id, target_count=5)

        results = await asyncio.gather(
            *(authorize(repository, target, installation_id) for target in targets),
            return_exceptions=True,
        )

        assert sum(not isinstance(result, BaseException) for result in results) == 1
        assert all(
            not isinstance(result, BaseException)
            or isinstance(result, ActionRiskAuthorizationLimited)
            for result in results
        )
        async with database.session() as session:
            assert (
                await session.scalar(select(func.count()).select_from(action_risk_authorizations))
                == 1
            )
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_authorization_rejects_unhealthy_unconfirmed_or_cross_scope_inputs(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyActionRiskAuthorizationRepository(database)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        target = (await seed_runnable_target(database, installation_id))[0]

        with pytest.raises(ActionRiskAuthorizationRejected):
            await authorize(
                repository,
                target,
                installation_id,
                risk_policy=ActionRiskPolicy(
                    scope=ActionRiskScope(
                        installation_id=InstallationId.new(),
                        platform=ActionRiskPlatform.DOUYIN,
                        action=DouyinSearchExposureAction.COMMENT,
                    ),
                    minimum_interval=timedelta(seconds=5),
                    task_action_limit=2,
                    daily_action_limit=3,
                    consecutive_failure_threshold=3,
                ),
            )
        async with database.session() as session:
            await session.execute(
                update(platform_session_health)
                .where(platform_session_health.c.installation_id == installation_id.uuid)
                .values(state=PlatformSessionState.RISK.value)
            )
        with pytest.raises(ActionRiskAuthorizationRejected):
            await authorize(repository, target, installation_id)

        async with database.session() as session:
            await session.execute(
                update(platform_session_health)
                .where(platform_session_health.c.installation_id == installation_id.uuid)
                .values(state=PlatformSessionState.HEALTHY.value)
            )
            await session.execute(
                insert(task_target_exclusions).values(
                    target_id=target.target_id.uuid,
                    task_id=target.task_id.uuid,
                    installation_id=installation_id.uuid,
                    page_revision=1,
                    excluded_at=NOW,
                )
            )
        with pytest.raises(ActionRiskAuthorizationRejected):
            await authorize(repository, target, installation_id)
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_authorization_fails_closed_for_invalid_state_time_conflict_and_database(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyActionRiskAuthorizationRepository(database)
    unavailable = Database.from_url(
        "postgresql+asyncpg://automation_tool_test:private@127.0.0.1:1/automation_tool_test",
        connect_timeout_seconds=0.05,
    )
    unavailable_repository = SqlAlchemyActionRiskAuthorizationRepository(unavailable)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        targets = await seed_runnable_target(database, installation_id, target_count=2)

        async with database.session() as session:
            await session.execute(
                update(installations)
                .where(installations.c.id == installation_id.uuid)
                .values(status="revoked", revoked_at=NOW)
            )
        with pytest.raises(ActionRiskAuthorizationRejected):
            await authorize(repository, targets[0], installation_id)

        async with database.session() as session:
            await session.execute(
                update(installations)
                .where(installations.c.id == installation_id.uuid)
                .values(status="active", revoked_at=None)
            )
        with pytest.raises(ActionRiskAuthorizationRejected):
            await repository.authorize(
                action_id=ActionId.new(),
                target_id=targets[0].target_id,
                execution_attempt_id=targets[0].attempt_id,
                task_id=TaskId.new(),
                installation_id=installation_id,
                policy=policy(installation_id),
                authorized_at=NOW,
            )
        with pytest.raises(ActionRiskAuthorizationRejected):
            await authorize(
                repository,
                targets[0],
                installation_id,
                risk_policy=ActionRiskPolicy(
                    scope=ActionRiskScope(
                        installation_id=installation_id,
                        platform=ActionRiskPlatform.DOUYIN,
                        action=DouyinSearchExposureAction.BROWSE,
                    ),
                    minimum_interval=timedelta(seconds=5),
                    task_action_limit=2,
                    daily_action_limit=3,
                    consecutive_failure_threshold=3,
                ),
            )

        await authorize(
            repository,
            targets[0],
            installation_id,
            authorized_at=NOW + timedelta(seconds=20),
        )
        with pytest.raises(ActionRiskAuthorizationRejected):
            await authorize(
                repository,
                targets[1],
                installation_id,
                authorized_at=NOW + timedelta(seconds=10),
            )
        with pytest.raises(ActionRiskAuthorizationRejected):
            await authorize(
                repository,
                targets[0],
                installation_id,
                authorized_at=NOW + timedelta(seconds=40),
            )

        with pytest.raises(ActionRiskAuthorizationUnavailable) as captured:
            await unavailable_repository.authorize(
                action_id=ActionId.new(),
                target_id=targets[0].target_id,
                execution_attempt_id=targets[0].attempt_id,
                task_id=targets[0].task_id,
                installation_id=installation_id,
                policy=policy(installation_id),
                authorized_at=NOW,
            )
        assert captured.value.__cause__ is None
        assert "private" not in str(captured.value)
    finally:
        await unavailable.close()
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_database_constraints_reject_incoherent_authorization_rows(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyActionRiskAuthorizationRepository(database)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        target = (await seed_runnable_target(database, installation_id))[0]
        created = await authorize(repository, target, installation_id)
        async with database.session() as session:
            row = (
                (
                    await session.execute(
                        select(action_risk_authorizations).where(
                            action_risk_authorizations.c.action_id == created.action_id.uuid
                        )
                    )
                )
                .mappings()
                .one()
            )
        for overrides in (
            {"authorized_day": NOW.date() - timedelta(days=1)},
            {"platform": "private"},
            {"task_count_after": 3},
            {"daily_count_after": 4},
            {"policy_version": "latest"},
        ):
            values = dict(row)
            values["action_id"] = ActionId.new().uuid
            values.update(overrides)
            with pytest.raises(IntegrityError):
                async with database.session() as session:
                    await session.execute(insert(action_risk_authorizations).values(values))
    finally:
        await reset_data(database)
        await database.close()
