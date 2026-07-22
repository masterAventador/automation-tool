from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, insert, text, update

from automation_tool.control_plane.application.task_target_results import (
    TaskTargetResultEvidence,
    TaskTargetResultService,
    TaskTargetResultStatus,
)
from automation_tool.control_plane.domain import (
    ActionId,
    ExecutionAttemptId,
    InstallationId,
    TaskId,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    action_risk_authorizations,
    douyin_search_exposure_definitions,
    execution_attempts,
    installations,
    task_actions,
    task_target_exclusions,
    task_targets,
    tasks,
)
from automation_tool.control_plane.infrastructure.database.task_target_repository import (
    SqlAlchemyTaskTargetRepository,
)
from automation_tool.control_plane.infrastructure.database.task_target_result_repository import (
    SqlAlchemyTaskTargetResultRepository,
)
from automation_tool.protocol import (
    ACTION_RESULT_EVIDENCE_VERSION,
    DouyinCandidate,
    DouyinCandidateSource,
    DouyinCandidateSummary,
)

BASE = datetime(2026, 7, 21, 8, 0, tzinfo=UTC)


async def reset_data(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(action_risk_authorizations))
        await session.execute(delete(task_actions))
        await session.execute(delete(task_target_exclusions))
        await session.execute(delete(task_targets))
        await session.execute(update(tasks).values(current_attempt_id=None))
        await session.execute(delete(execution_attempts))
        await session.execute(delete(douyin_search_exposure_definitions))
        await session.execute(delete(tasks))
        await session.execute(delete(installations))


def candidate(value: str, *, display_name: str | None = None) -> DouyinCandidate:
    return DouyinCandidate(
        platform_target_id=value,
        summary=DouyinCandidateSummary(
            display_name=display_name or f"目标 {value}",
            public_handle=f"handle_{value}",
        ),
        source=DouyinCandidateSource.GENERAL_SEARCH_AUTHOR,
        page_revision=1,
    )


@pytest.mark.asyncio
async def test_target_results_project_success_skip_failure_and_uncertain_from_postgresql(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    installation_id = InstallationId.new()
    task_id = TaskId.new()
    attempt_id = ExecutionAttemptId.new()
    candidates = (
        candidate("target-success", display_name="成功目标"),
        candidate("target-failed", display_name="失败目标"),
        candidate("target-uncertain", display_name="不确定目标"),
        candidate("target-excluded", display_name="用户排除目标"),
        candidate("target-success", display_name="任务内重复目标"),
        candidate("target-blacklisted", display_name="黑名单目标"),
    )
    try:
        await reset_data(database)
        async with database.session() as session:
            await session.execute(
                insert(installations).values(
                    id=installation_id.uuid,
                    device_public_key=secrets.token_bytes(32),
                    created_at=BASE,
                    updated_at=BASE,
                )
            )
            await session.execute(
                insert(tasks).values(
                    id=task_id.uuid,
                    installation_id=installation_id.uuid,
                    creation_idempotency_key=f"task:target-results:{task_id}",
                    status=TaskStatus.RUNNING.value,
                    revision=9,
                    last_event_sequence=8,
                    created_at=BASE,
                    updated_at=BASE + timedelta(seconds=8),
                )
            )
            await session.execute(
                insert(douyin_search_exposure_definitions).values(
                    task_id=task_id.uuid,
                    installation_id=installation_id.uuid,
                    search_keyword="新能源汽车",
                    action="comment",
                    message_template="您好 {{target_display_name}} 期待您的分享",
                    target_limit=10,
                    minimum_interval_seconds=30,
                    maximum_interval_seconds=90,
                )
            )
        targets = await SqlAlchemyTaskTargetRepository(database).evaluate_and_replace(
            task_id=task_id,
            installation_id=installation_id,
            candidates=candidates,
            blacklist=(candidates[-1].dedupe_key,),
            evaluated_at=BASE,
        )
        async with database.session() as session:
            await session.execute(
                insert(execution_attempts).values(
                    id=attempt_id.uuid,
                    task_id=task_id.uuid,
                    installation_id=installation_id.uuid,
                    attempt_number=1,
                    status="running",
                    revision=2,
                    created_at=BASE,
                    updated_at=BASE + timedelta(seconds=1),
                    started_at=BASE + timedelta(seconds=1),
                )
            )
            await session.execute(
                update(tasks)
                .where(tasks.c.id == task_id.uuid)
                .values(current_attempt_id=attempt_id.uuid)
            )
            await session.execute(
                insert(task_target_exclusions).values(
                    target_id=targets[3].target_id.uuid,
                    task_id=task_id.uuid,
                    installation_id=installation_id.uuid,
                    page_revision=1,
                    excluded_at=BASE + timedelta(seconds=2),
                )
            )
            action_facts = (
                (targets[0], "succeeded", "comment_confirmed", 3),
                (targets[1], "failed", "login_required", 4),
                (targets[2], "outcome_uncertain", "final_state_unconfirmed", 5),
            )
            for count, (target, outcome, evidence, second) in enumerate(
                action_facts,
                start=1,
            ):
                action_id = ActionId.new()
                status = "outcome_uncertain" if outcome == "outcome_uncertain" else "verified"
                timestamp = BASE + timedelta(seconds=second)
                await session.execute(
                    insert(task_actions).values(
                        id=action_id.uuid,
                        execution_attempt_id=attempt_id.uuid,
                        task_id=task_id.uuid,
                        installation_id=installation_id.uuid,
                        ordinal=target.ordinal,
                        status=status,
                        outcome=outcome,
                        evidence_code=evidence,
                        revision=3,
                        created_at=BASE + timedelta(seconds=1),
                        updated_at=timestamp,
                        finished_at=timestamp,
                    )
                )
                await session.execute(
                    insert(action_risk_authorizations).values(
                        action_id=action_id.uuid,
                        target_id=target.target_id.uuid,
                        execution_attempt_id=attempt_id.uuid,
                        task_id=task_id.uuid,
                        installation_id=installation_id.uuid,
                        ordinal=target.ordinal,
                        platform="douyin",
                        action="comment",
                        policy_version="action-risk-policy.v1",
                        effective_minimum_interval_seconds=30,
                        task_action_limit=10,
                        daily_action_limit=100,
                        consecutive_failure_threshold=3,
                        task_count_after=count,
                        daily_count_after=count,
                        authorized_day=BASE.date(),
                        authorized_at=BASE + timedelta(seconds=count),
                        created_at=BASE + timedelta(seconds=count),
                    )
                )

        snapshot = await TaskTargetResultService(
            repository=SqlAlchemyTaskTargetResultRepository(database)
        ).get(installation_id=installation_id, task_id=task_id)
        repository = SqlAlchemyTaskTargetResultRepository(database)

        assert snapshot.task.status is TaskStatus.RUNNING
        assert [item.status for item in snapshot.items] == [
            TaskTargetResultStatus.SUCCEEDED,
            TaskTargetResultStatus.FAILED,
            TaskTargetResultStatus.OUTCOME_UNCERTAIN,
            TaskTargetResultStatus.SKIPPED,
            TaskTargetResultStatus.SKIPPED,
            TaskTargetResultStatus.SKIPPED,
        ]
        assert [item.evidence for item in snapshot.items] == [
            TaskTargetResultEvidence.COMMENT_CONFIRMED,
            TaskTargetResultEvidence.LOGIN_REQUIRED,
            TaskTargetResultEvidence.FINAL_STATE_UNCONFIRMED,
            TaskTargetResultEvidence.USER_EXCLUDED,
            TaskTargetResultEvidence.DUPLICATE_IN_TASK,
            TaskTargetResultEvidence.BLACKLISTED,
        ]
        assert [item.display_name for item in snapshot.items[:4]] == [
            "成功目标",
            "失败目标",
            "不确定目标",
            "用户排除目标",
        ]
        assert all(item.action_id is not None for item in snapshot.items[:3])
        assert all(item.action_id is None for item in snapshot.items[3:])
        assert (
            await repository.get(
                installation_id=InstallationId.new(),
                task_id=task_id,
            )
            is None
        )
        assert (
            await repository.get(
                installation_id=installation_id,
                task_id=TaskId.new(),
            )
            is None
        )
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_task_action_evidence_migration_is_exact_and_reversible(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        async with database.session() as session:
            revision = await session.scalar(text("select version_num from alembic_version"))
            columns = set(
                await session.scalars(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_schema = 'public' and table_name = 'task_actions'"
                    )
                )
            )
            checks = set(
                await session.scalars(
                    text(
                        "select conname from pg_constraint where conrelid = "
                        "'public.task_actions'::regclass"
                    )
                )
            )
        assert revision == "20260723_0030"
        assert "evidence_code" in columns
        assert "ck_task_actions_evidence_coherence" in checks
        assert ACTION_RESULT_EVIDENCE_VERSION == "action-result-evidence.v1"
    finally:
        await database.close()

    alembic_runner(postgresql_url, "downgrade", "20260721_0023")
    database = Database.from_url(postgresql_url)
    try:
        async with database.session() as session:
            columns = set(
                await session.scalars(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_schema = 'public' and table_name = 'task_actions'"
                    )
                )
            )
        assert "evidence_code" not in columns
    finally:
        await database.close()
    alembic_runner(postgresql_url, "upgrade", "head")
