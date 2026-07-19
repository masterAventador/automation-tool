from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, insert, select

from automation_tool.control_plane.application.task_targets import (
    TaskTargetPersistenceRejected,
)
from automation_tool.control_plane.domain import (
    DouyinCandidateDisposition,
    InstallationId,
    TargetId,
    TaskId,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    installations,
    task_targets,
    tasks,
)
from automation_tool.control_plane.infrastructure.database.task_target_repository import (
    SqlAlchemyTaskTargetRepository,
)
from automation_tool.protocol import (
    DouyinCandidate,
    DouyinCandidateKey,
    DouyinCandidateSource,
    DouyinCandidateSummary,
)

NOW = datetime(2026, 7, 19, 1, 0, tzinfo=UTC)


def candidate(
    target_id: str,
    *,
    page_revision: int,
    display_name: str | None = None,
) -> DouyinCandidate:
    return DouyinCandidate(
        platform_target_id=target_id,
        summary=DouyinCandidateSummary(
            display_name=target_id if display_name is None else display_name,
            public_handle=f"handle_{target_id}",
        ),
        source=DouyinCandidateSource.GENERAL_SEARCH_AUTHOR,
        page_revision=page_revision,
    )


async def reset_data(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(task_targets))
        await session.execute(delete(tasks))
        await session.execute(delete(installations))


async def seed_task(
    database: Database,
    *,
    installation_id: InstallationId | None = None,
    created_at: datetime = NOW,
) -> tuple[TaskId, InstallationId]:
    task_id = TaskId.new()
    scoped_installation = installation_id or InstallationId.new()
    async with database.session() as session:
        if installation_id is None:
            await session.execute(
                insert(installations).values(
                    id=scoped_installation.uuid,
                    device_public_key=secrets.token_bytes(32),
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
        await session.execute(
            insert(tasks).values(
                id=task_id.uuid,
                installation_id=scoped_installation.uuid,
                creation_idempotency_key=f"task:target-repository:{task_id}",
                created_at=created_at,
                updated_at=created_at,
            )
        )
    return task_id, scoped_installation


async def revoke_installation(database: Database, installation_id: InstallationId) -> None:
    async with database.session() as session:
        await session.execute(
            installations.update()
            .where(installations.c.id == installation_id.uuid)
            .values(status="revoked", revision=2, revoked_at=NOW, updated_at=NOW)
        )


@pytest.mark.asyncio
async def test_repository_evaluates_then_persists_every_decision_in_input_order(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTaskTargetRepository(database)
    try:
        await reset_data(database)
        task_id, installation_id = await seed_task(database)
        first = candidate("creator-001", page_revision=11, display_name="甲")
        repeated = candidate("creator-001", page_revision=11, display_name="甲的新名称")
        blocked = candidate("creator-002", page_revision=11, display_name="乙")

        records = await repository.evaluate_and_replace(
            task_id=task_id,
            installation_id=installation_id,
            candidates=(first, repeated, blocked),
            blacklist=(blocked.dedupe_key,),
            evaluated_at=NOW,
        )

        assert [record.ordinal for record in records] == [1, 2, 3]
        assert [record.candidate for record in records] == [first, repeated, blocked]
        assert [record.disposition for record in records] == [
            DouyinCandidateDisposition.ELIGIBLE,
            DouyinCandidateDisposition.DUPLICATE_IN_TASK,
            DouyinCandidateDisposition.BLACKLISTED,
        ]
        assert all(type(record.target_id) is TargetId for record in records)
        assert all(record.task_id == task_id for record in records)
        assert all(record.installation_id == installation_id for record in records)
        assert all(record.evaluated_at == NOW and record.created_at == NOW for record in records)

        async with database.session() as session:
            persisted = list(
                (await session.execute(select(task_targets).order_by(task_targets.c.ordinal)))
                .mappings()
                .all()
            )
        assert [row["dedupe_key"] for row in persisted] == [
            str(first.dedupe_key),
            str(repeated.dedupe_key),
            str(blocked.dedupe_key),
        ]
        assert [row["disposition"] for row in persisted] == [
            "eligible",
            "duplicate_in_task",
            "blacklisted",
        ]
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_repository_uses_only_same_installation_recent_history(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTaskTargetRepository(database)
    try:
        await reset_data(database)
        current_task, installation_id = await seed_task(database)
        recent_task, _ = await seed_task(
            database,
            installation_id=installation_id,
            created_at=NOW - timedelta(days=30),
        )
        old_task, _ = await seed_task(
            database,
            installation_id=installation_id,
            created_at=NOW - timedelta(days=31),
        )
        other_task, other_installation = await seed_task(
            database,
            created_at=NOW - timedelta(seconds=1),
        )

        recent = candidate("creator-recent", page_revision=1)
        old = candidate("creator-old", page_revision=1)
        foreign = candidate("creator-foreign", page_revision=1)
        await repository.evaluate_and_replace(
            task_id=recent_task,
            installation_id=installation_id,
            candidates=(recent,),
            blacklist=(),
            evaluated_at=NOW - timedelta(days=30),
        )
        await repository.evaluate_and_replace(
            task_id=old_task,
            installation_id=installation_id,
            candidates=(old,),
            blacklist=(),
            evaluated_at=NOW - timedelta(days=30, microseconds=1),
        )
        await repository.evaluate_and_replace(
            task_id=other_task,
            installation_id=other_installation,
            candidates=(foreign,),
            blacklist=(),
            evaluated_at=NOW - timedelta(seconds=1),
        )

        current = await repository.evaluate_and_replace(
            task_id=current_task,
            installation_id=installation_id,
            candidates=(
                candidate("creator-recent", page_revision=2),
                candidate("creator-old", page_revision=2),
                candidate("creator-foreign", page_revision=2),
            ),
            blacklist=(),
            evaluated_at=NOW,
        )

        assert [record.disposition for record in current] == [
            DouyinCandidateDisposition.DUPLICATE_IN_HISTORY,
            DouyinCandidateDisposition.ELIGIBLE,
            DouyinCandidateDisposition.ELIGIBLE,
        ]
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_repository_replaces_only_with_newer_revision_and_rolls_back_rejection(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTaskTargetRepository(database)
    try:
        await reset_data(database)
        task_id, installation_id = await seed_task(database)
        original = await repository.evaluate_and_replace(
            task_id=task_id,
            installation_id=installation_id,
            candidates=(candidate("creator-original", page_revision=5),),
            blacklist=(),
            evaluated_at=NOW + timedelta(seconds=10),
        )

        for rejected_candidates, rejected_at in (
            ((candidate("creator-changed", page_revision=5),), NOW + timedelta(seconds=11)),
            ((candidate("creator-older", page_revision=4),), NOW + timedelta(seconds=11)),
            ((candidate("creator-time", page_revision=6),), NOW + timedelta(seconds=9)),
            ((), NOW + timedelta(seconds=11)),
        ):
            with pytest.raises(TaskTargetPersistenceRejected):
                await repository.evaluate_and_replace(
                    task_id=task_id,
                    installation_id=installation_id,
                    candidates=rejected_candidates,
                    blacklist=(),
                    evaluated_at=rejected_at,
                )

        replacement = await repository.evaluate_and_replace(
            task_id=task_id,
            installation_id=installation_id,
            candidates=(candidate("creator-new", page_revision=6),),
            blacklist=(),
            evaluated_at=NOW + timedelta(seconds=12),
        )

        assert replacement[0].target_id != original[0].target_id
        assert replacement[0].candidate.page_revision == 6
        async with database.session() as session:
            rows = list((await session.execute(select(task_targets))).mappings().all())
        assert len(rows) == 1
        assert rows[0]["platform_target_id"] == "creator-new"
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_concurrent_snapshots_serialize_to_the_highest_revision_without_mixing(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTaskTargetRepository(database)
    try:
        await reset_data(database)
        task_id, installation_id = await seed_task(database)

        results = await asyncio.gather(
            repository.evaluate_and_replace(
                task_id=task_id,
                installation_id=installation_id,
                candidates=(candidate("creator-revision-2", page_revision=2),),
                blacklist=(),
                evaluated_at=NOW + timedelta(seconds=1),
            ),
            repository.evaluate_and_replace(
                task_id=task_id,
                installation_id=installation_id,
                candidates=(candidate("creator-revision-3", page_revision=3),),
                blacklist=(),
                evaluated_at=NOW + timedelta(seconds=2),
            ),
            return_exceptions=True,
        )

        assert isinstance(results[1], tuple)
        assert isinstance(results[0], (tuple, TaskTargetPersistenceRejected))
        async with database.session() as session:
            rows = list((await session.execute(select(task_targets))).mappings().all())
        assert len(rows) == 1
        assert rows[0]["page_revision"] == 3
        assert rows[0]["platform_target_id"] == "creator-revision-3"
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_repository_lists_stable_keyset_pages_and_hides_cross_scope_rows(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTaskTargetRepository(database)
    try:
        await reset_data(database)
        task_id, installation_id = await seed_task(database)
        other_task, other_installation = await seed_task(database)
        created = await repository.evaluate_and_replace(
            task_id=task_id,
            installation_id=installation_id,
            candidates=tuple(
                candidate(f"creator-{index:03d}", page_revision=9) for index in range(5)
            ),
            blacklist=(),
            evaluated_at=NOW,
        )
        await repository.evaluate_and_replace(
            task_id=other_task,
            installation_id=other_installation,
            candidates=(candidate("creator-private", page_revision=9),),
            blacklist=(),
            evaluated_at=NOW,
        )

        first = await repository.list_page(
            task_id=task_id,
            installation_id=installation_id,
            page_revision=9,
            after_ordinal=None,
            after_target_id=None,
            limit=3,
        )
        second = await repository.list_page(
            task_id=task_id,
            installation_id=installation_id,
            page_revision=9,
            after_ordinal=first[-1].ordinal,
            after_target_id=first[-1].target_id,
            limit=3,
        )
        hidden = await repository.list_page(
            task_id=other_task,
            installation_id=installation_id,
            page_revision=9,
            after_ordinal=None,
            after_target_id=None,
            limit=3,
        )
        stale = await repository.list_page(
            task_id=task_id,
            installation_id=installation_id,
            page_revision=8,
            after_ordinal=None,
            after_target_id=None,
            limit=3,
        )

        assert first == created[:3]
        assert second == created[3:]
        assert hidden == ()
        assert stale == ()
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_repository_rejects_invalid_scope_cursor_and_policy_inputs_without_echo(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTaskTargetRepository(database)
    private = "private-target-value"
    try:
        await reset_data(database)
        task_id, installation_id = await seed_task(database)
        _, other_installation = await seed_task(database)
        revoked_task, revoked_installation = await seed_task(database)
        await revoke_installation(database, revoked_installation)

        operations = (
            repository.evaluate_and_replace(
                task_id=cast(TaskId, private),
                installation_id=installation_id,
                candidates=(candidate("creator-identity", page_revision=1),),
                blacklist=(),
                evaluated_at=NOW,
            ),
            repository.evaluate_and_replace(
                task_id=task_id,
                installation_id=other_installation,
                candidates=(candidate("creator-scope", page_revision=1),),
                blacklist=(),
                evaluated_at=NOW,
            ),
            repository.evaluate_and_replace(
                task_id=revoked_task,
                installation_id=revoked_installation,
                candidates=(candidate("creator-revoked", page_revision=1),),
                blacklist=(),
                evaluated_at=NOW,
            ),
            repository.evaluate_and_replace(
                task_id=task_id,
                installation_id=installation_id,
                candidates=cast(tuple[DouyinCandidate, ...], [candidate(private, page_revision=1)]),
                blacklist=(),
                evaluated_at=NOW,
            ),
            repository.evaluate_and_replace(
                task_id=task_id,
                installation_id=installation_id,
                candidates=(candidate("creator-policy", page_revision=1),),
                blacklist=cast(tuple[DouyinCandidateKey, ...], (private,)),
                evaluated_at=NOW,
            ),
            repository.list_page(
                task_id=task_id,
                installation_id=installation_id,
                page_revision=1,
                after_ordinal=1,
                after_target_id=None,
                limit=20,
            ),
            repository.list_page(
                task_id=task_id,
                installation_id=installation_id,
                page_revision=1,
                after_ordinal=None,
                after_target_id=None,
                limit=cast(int, True),
            ),
            repository.list_page(
                task_id=task_id,
                installation_id=installation_id,
                page_revision=1,
                after_ordinal=0,
                after_target_id=TargetId.new(),
                limit=20,
            ),
            repository.list_page(
                task_id=task_id,
                installation_id=installation_id,
                page_revision=1,
                after_ordinal=1,
                after_target_id=cast(TargetId, private),
                limit=20,
            ),
        )
        for operation in operations:
            with pytest.raises(TaskTargetPersistenceRejected) as captured:
                await operation
            assert private not in repr(captured.value)
            assert captured.value.__cause__ is None
            assert captured.value.__context__ is None
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_repository_rejects_corrupt_persisted_candidate_without_echo(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTaskTargetRepository(database)
    try:
        await reset_data(database)
        task_id, installation_id = await seed_task(database)
        await repository.evaluate_and_replace(
            task_id=task_id,
            installation_id=installation_id,
            candidates=(candidate("creator-corrupt", page_revision=1),),
            blacklist=(),
            evaluated_at=NOW,
        )
        async with database.session() as session:
            await session.execute(
                task_targets.update()
                .where(task_targets.c.task_id == task_id.uuid)
                .values(dedupe_key="atdck1_" + ("Z" * 43))
            )

        with pytest.raises(TaskTargetPersistenceRejected) as captured:
            await repository.list_page(
                task_id=task_id,
                installation_id=installation_id,
                page_revision=1,
                after_ordinal=None,
                after_target_id=None,
                limit=20,
            )
        assert "creator-corrupt" not in repr(captured.value)
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_repository_rejects_mixed_persisted_snapshot_facts(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTaskTargetRepository(database)
    try:
        await reset_data(database)
        task_id, installation_id = await seed_task(database)
        await repository.evaluate_and_replace(
            task_id=task_id,
            installation_id=installation_id,
            candidates=(
                candidate("creator-corrupt-a", page_revision=1),
                candidate("creator-corrupt-b", page_revision=1),
            ),
            blacklist=(),
            evaluated_at=NOW,
        )
        async with database.session() as session:
            await session.execute(
                task_targets.update()
                .where(task_targets.c.task_id == task_id.uuid, task_targets.c.ordinal == 2)
                .values(
                    evaluated_at=NOW + timedelta(seconds=1),
                    created_at=NOW + timedelta(seconds=1),
                )
            )

        with pytest.raises(TaskTargetPersistenceRejected):
            await repository.evaluate_and_replace(
                task_id=task_id,
                installation_id=installation_id,
                candidates=(candidate("creator-new", page_revision=2),),
                blacklist=(),
                evaluated_at=NOW + timedelta(seconds=2),
            )

        async with database.session() as session:
            await session.execute(
                task_targets.update()
                .where(task_targets.c.task_id == task_id.uuid, task_targets.c.ordinal == 2)
                .values(page_revision=2, evaluated_at=NOW, created_at=NOW)
            )
        with pytest.raises(TaskTargetPersistenceRejected):
            await repository.evaluate_and_replace(
                task_id=task_id,
                installation_id=installation_id,
                candidates=(candidate("creator-newer", page_revision=3),),
                blacklist=(),
                evaluated_at=NOW + timedelta(seconds=3),
            )
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_repository_rejects_a_snapshot_before_its_parent_task(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTaskTargetRepository(database)
    try:
        await reset_data(database)
        task_id, installation_id = await seed_task(database)

        with pytest.raises(TaskTargetPersistenceRejected):
            await repository.evaluate_and_replace(
                task_id=task_id,
                installation_id=installation_id,
                candidates=(candidate("creator-before-task", page_revision=1),),
                blacklist=(),
                evaluated_at=NOW - timedelta(microseconds=1),
            )
    finally:
        await reset_data(database)
        await database.close()
