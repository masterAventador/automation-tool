from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime, timedelta
from typing import TypedDict

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, insert, select

from automation_tool.control_plane.application.tasks import TaskPersistenceRejected
from automation_tool.control_plane.domain import (
    DouyinSearchExposureAction,
    DouyinSearchExposureDefinition,
    InstallationId,
    InvalidTaskTransition,
    TaskId,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    device_credentials,
    device_sessions,
    douyin_search_exposure_definitions,
    installation_registration_challenges,
    installations,
    platform_session_gates,
    tasks,
)
from automation_tool.control_plane.infrastructure.database.task_repository import (
    SqlAlchemyTaskRepository,
)

NOW = datetime(2026, 7, 18, 15, 0, tzinfo=UTC)
DEFINITION = DouyinSearchExposureDefinition(
    search_keyword="新能源汽车",
    action=DouyinSearchExposureAction.BROWSE,
    message_template=None,
    target_limit=10,
    minimum_interval_seconds=30,
    maximum_interval_seconds=90,
    preview_required=True,
    final_confirmation_required=True,
)


class TransitionValues(TypedDict):
    installation_id: InstallationId
    expected_revision: int
    target_status: TaskStatus
    updated_at: datetime


async def reset_data(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(douyin_search_exposure_definitions))
        await session.execute(delete(tasks))
        await session.execute(delete(installation_registration_challenges))
        await session.execute(delete(device_sessions))
        await session.execute(delete(device_credentials))
        await session.execute(delete(platform_session_gates))
        await session.execute(delete(installations))


async def seed_installation(database: Database, *, revoked: bool = False) -> InstallationId:
    installation_id = InstallationId.new()
    values: dict[str, object] = {
        "id": installation_id.uuid,
        "device_public_key": secrets.token_bytes(32),
        "created_at": NOW,
        "updated_at": NOW,
    }
    if revoked:
        values.update(
            {
                "status": "revoked",
                "revision": 2,
                "revoked_at": NOW,
            }
        )
    async with database.session() as session:
        await session.execute(insert(installations).values(values))
    return installation_id


@pytest.mark.asyncio
async def test_repository_creates_and_reads_only_within_the_installation_scope(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTaskRepository(database)
    try:
        await reset_data(database)
        first_installation = await seed_installation(database)
        second_installation = await seed_installation(database)
        first_id = TaskId.new()
        second_id = TaskId.new()

        first_result = await repository.create(
            task_id=first_id,
            installation_id=first_installation,
            idempotency_key="task:repository:first",
            definition=DEFINITION,
            created_at=NOW,
        )
        second_result = await repository.create(
            task_id=second_id,
            installation_id=second_installation,
            idempotency_key="task:repository:second",
            definition=DEFINITION,
            created_at=NOW + timedelta(seconds=1),
        )
        first = first_result.task
        second = second_result.task

        assert first.task_id == first_id
        assert first.installation_id == first_installation
        assert first.status is TaskStatus.DRAFT
        assert first.revision == 1
        assert first.created_at == NOW
        assert first.updated_at == NOW
        assert second.installation_id == second_installation
        assert (
            await repository.get(
                task_id=first_id,
                installation_id=first_installation,
            )
            == first
        )
        assert (
            await repository.get(
                task_id=first_id,
                installation_id=second_installation,
            )
            is None
        )
        assert (
            await repository.get(
                task_id=TaskId.new(),
                installation_id=first_installation,
            )
            is None
        )
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_repository_rejects_unknown_revoked_and_duplicate_create_targets_safely(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTaskRepository(database)
    task_id = TaskId.new()
    try:
        await reset_data(database)
        active = await seed_installation(database)
        revoked = await seed_installation(database, revoked=True)
        await repository.create(
            task_id=task_id,
            installation_id=active,
            idempotency_key="task:repository:original",
            definition=DEFINITION,
            created_at=NOW,
        )

        for installation_id, candidate_id in (
            (InstallationId.new(), TaskId.new()),
            (revoked, TaskId.new()),
            (active, task_id),
        ):
            with pytest.raises(TaskPersistenceRejected) as captured:
                await repository.create(
                    task_id=candidate_id,
                    installation_id=installation_id,
                    idempotency_key=f"task:repository:candidate:{candidate_id}",
                    definition=DEFINITION,
                    created_at=NOW,
                )
            assert str(captured.value) == "Task persistence operation is rejected"
            assert str(installation_id) not in str(captured.value)
            assert str(candidate_id) not in str(captured.value)

        async with database.session() as session:
            persisted = list((await session.execute(select(tasks))).mappings())
        assert len(persisted) == 1
        assert persisted[0]["id"] == task_id.uuid
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_persistent_platform_gate_rejects_new_task_creation(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTaskRepository(database)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        async with database.session() as session:
            await session.execute(
                insert(platform_session_gates).values(
                    installation_id=installation_id.uuid,
                    platform="douyin",
                    state="blocked",
                    session_revision=1,
                    updated_at=NOW,
                )
            )

        with pytest.raises(TaskPersistenceRejected):
            await repository.create(
                task_id=TaskId.new(),
                installation_id=installation_id,
                idempotency_key="task:repository:blocked-platform",
                definition=DEFINITION,
                created_at=NOW,
            )
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_repository_applies_state_machine_and_revision_cas_without_scope_leaks(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTaskRepository(database)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        other_installation = await seed_installation(database)
        task_id = TaskId.new()
        await repository.create(
            task_id=task_id,
            installation_id=installation_id,
            idempotency_key="task:repository:transition",
            definition=DEFINITION,
            created_at=NOW,
        )

        validating = await repository.transition(
            task_id=task_id,
            installation_id=installation_id,
            expected_revision=1,
            target_status=TaskStatus.VALIDATING,
            updated_at=NOW + timedelta(seconds=1),
        )
        assert validating.status is TaskStatus.VALIDATING
        assert validating.revision == 2
        assert validating.updated_at == NOW + timedelta(seconds=1)

        rejected_operations: tuple[TransitionValues, ...] = (
            {
                "installation_id": installation_id,
                "expected_revision": 1,
                "target_status": TaskStatus.AWAITING_DEVICE,
                "updated_at": NOW + timedelta(seconds=2),
            },
            {
                "installation_id": other_installation,
                "expected_revision": 2,
                "target_status": TaskStatus.AWAITING_DEVICE,
                "updated_at": NOW + timedelta(seconds=2),
            },
            {
                "installation_id": installation_id,
                "expected_revision": 2,
                "target_status": TaskStatus.AWAITING_DEVICE,
                "updated_at": NOW - timedelta(seconds=1),
            },
        )
        for values in rejected_operations:
            with pytest.raises(TaskPersistenceRejected):
                await repository.transition(task_id=task_id, **values)

        with pytest.raises(InvalidTaskTransition):
            await repository.transition(
                task_id=task_id,
                installation_id=installation_id,
                expected_revision=2,
                target_status=TaskStatus.RUNNING,
                updated_at=NOW + timedelta(seconds=2),
            )

        persisted = await repository.get(
            task_id=task_id,
            installation_id=installation_id,
        )
        assert persisted == validating
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_two_concurrent_transitions_have_one_revision_winner(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTaskRepository(database)
    try:
        await reset_data(database)
        installation_id = await seed_installation(database)
        task_id = TaskId.new()
        await repository.create(
            task_id=task_id,
            installation_id=installation_id,
            idempotency_key="task:repository:concurrent",
            definition=DEFINITION,
            created_at=NOW,
        )

        results = await asyncio.gather(
            *(
                repository.transition(
                    task_id=task_id,
                    installation_id=installation_id,
                    expected_revision=1,
                    target_status=TaskStatus.VALIDATING,
                    updated_at=NOW + timedelta(seconds=1),
                )
                for _ in range(2)
            ),
            return_exceptions=True,
        )

        assert sum(not isinstance(result, BaseException) for result in results) == 1
        assert sum(isinstance(result, TaskPersistenceRejected) for result in results) == 1
        persisted = await repository.get(
            task_id=task_id,
            installation_id=installation_id,
        )
        assert persisted is not None
        assert persisted.status is TaskStatus.VALIDATING
        assert persisted.revision == 2
    finally:
        await reset_data(database)
        await database.close()
