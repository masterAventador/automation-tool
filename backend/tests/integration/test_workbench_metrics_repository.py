from __future__ import annotations

import secrets
from datetime import UTC, datetime

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, insert

from automation_tool.control_plane.domain import (
    ActionId,
    ActionOutcome,
    ActionStatus,
    ExecutionAttemptId,
    ExecutionAttemptStatus,
    InstallationId,
    TaskId,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    douyin_search_exposure_definitions,
    execution_attempts,
    installations,
    task_actions,
    tasks,
)
from automation_tool.control_plane.infrastructure.database.workbench_metrics_repository import (
    SqlAlchemyWorkbenchMetricsRepository,
)
from automation_tool.protocol import ActionResultEvidence

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


async def reset_data(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(task_actions))
        await session.execute(delete(execution_attempts))
        await session.execute(delete(douyin_search_exposure_definitions))
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
    return installation_id


async def seed_metrics(
    database: Database,
    installation_id: InstallationId,
    *,
    prefix: str,
) -> None:
    statuses = (
        TaskStatus.SUCCEEDED,
        TaskStatus.PARTIALLY_SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.AWAITING_HUMAN,
        TaskStatus.OUTCOME_UNCERTAIN,
        TaskStatus.RUNNING,
        TaskStatus.CANCELLED,
    )
    task_ids = tuple(TaskId.new() for _ in statuses)
    running_task = task_ids[5]
    attempt_id = ExecutionAttemptId.new()
    async with database.session() as session:
        await session.execute(
            insert(tasks),
            [
                {
                    "id": task_id.uuid,
                    "installation_id": installation_id.uuid,
                    "creation_idempotency_key": f"task:metrics:{prefix}:{index}",
                    "status": status.value,
                    "revision": 1,
                    "created_at": NOW,
                    "updated_at": NOW,
                }
                for index, (task_id, status) in enumerate(zip(task_ids, statuses, strict=True))
            ],
        )
        await session.execute(
            insert(execution_attempts).values(
                id=attempt_id.uuid,
                task_id=running_task.uuid,
                installation_id=installation_id.uuid,
                attempt_number=1,
                status=ExecutionAttemptStatus.RUNNING.value,
                revision=1,
                created_at=NOW,
                updated_at=NOW,
                started_at=NOW,
            )
        )
        action_facts = (
            (
                ActionStatus.VERIFIED,
                ActionOutcome.SUCCEEDED,
                ActionResultEvidence.COMMENT_CONFIRMED,
                NOW,
            ),
            (
                ActionStatus.VERIFIED,
                ActionOutcome.FAILED,
                ActionResultEvidence.LOGIN_REQUIRED,
                NOW,
            ),
            (
                ActionStatus.OUTCOME_UNCERTAIN,
                ActionOutcome.OUTCOME_UNCERTAIN,
                ActionResultEvidence.FINAL_STATE_UNCONFIRMED,
                NOW,
            ),
            (
                ActionStatus.CANCELLED,
                ActionOutcome.CANCELLED,
                ActionResultEvidence.ACTION_CANCELLED,
                NOW,
            ),
            (ActionStatus.PLANNED, ActionOutcome.PENDING, None, None),
        )
        await session.execute(
            insert(task_actions),
            [
                {
                    "id": ActionId.new().uuid,
                    "execution_attempt_id": attempt_id.uuid,
                    "task_id": running_task.uuid,
                    "installation_id": installation_id.uuid,
                    "ordinal": ordinal,
                    "status": status.value,
                    "outcome": outcome.value,
                    "evidence_code": None if evidence is None else evidence.value,
                    "revision": 1,
                    "created_at": NOW,
                    "updated_at": NOW,
                    "finished_at": finished_at,
                }
                for ordinal, (status, outcome, evidence, finished_at) in enumerate(
                    action_facts, start=1
                )
            ],
        )


@pytest.mark.asyncio
async def test_repository_projects_current_task_and_action_facts_in_installation_scope(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyWorkbenchMetricsRepository(database)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        other_installation = await seed_installation(database)
        await seed_metrics(database, installation_id, prefix="current")
        await seed_metrics(database, other_installation, prefix="other")

        metrics = await repository.get(installation_id=installation_id)
        empty_installation = await seed_installation(database)
        empty = await repository.get(installation_id=empty_installation)

        assert (
            metrics.task_total,
            metrics.task_succeeded,
            metrics.task_failed,
            metrics.task_handoff_required,
            metrics.task_outcome_uncertain,
        ) == (7, 2, 1, 1, 1)
        assert (
            metrics.action_total,
            metrics.action_succeeded,
            metrics.action_failed,
            metrics.action_outcome_uncertain,
        ) == (5, 1, 1, 1)
        assert empty.task_total == 0
        assert empty.action_total == 0
    finally:
        await reset_data(database)
        await database.close()
