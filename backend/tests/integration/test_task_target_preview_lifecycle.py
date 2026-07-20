from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, func, insert, select, update

from automation_tool.control_plane.application.task_target_previews import (
    PendingTaskTargetConfirmation,
    PendingTaskTargetExclusions,
    TaskTargetPreviewConflict,
    TaskTargetPreviewNotFound,
    TaskTargetPreviewService,
)
from automation_tool.control_plane.domain import (
    InstallationId,
    TargetId,
    TaskEventType,
    TaskId,
    TaskStatus,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    installations,
    task_events,
    task_target_confirmations,
    task_target_exclusions,
    task_targets,
    tasks,
)
from automation_tool.control_plane.infrastructure.database.task_target_preview_repository import (
    SqlAlchemyTaskTargetPreviewRepository,
)
from automation_tool.control_plane.infrastructure.database.task_target_repository import (
    SqlAlchemyTaskTargetRepository,
)
from automation_tool.protocol import (
    DouyinCandidate,
    DouyinCandidateSource,
    DouyinCandidateSummary,
)

BASE = datetime(2026, 7, 20, 2, 0, tzinfo=UTC)


@dataclass
class MutableClock:
    value: datetime

    def now(self) -> datetime:
        return self.value


def candidate(target_id: str, *, page_revision: int) -> DouyinCandidate:
    return DouyinCandidate(
        platform_target_id=target_id,
        summary=DouyinCandidateSummary(
            display_name=f"目标 {target_id}",
            public_handle=f"handle_{target_id}",
        ),
        source=DouyinCandidateSource.GENERAL_SEARCH_AUTHOR,
        page_revision=page_revision,
    )


async def reset_data(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(task_target_confirmations))
        await session.execute(delete(task_target_exclusions))
        await session.execute(delete(task_targets))
        await session.execute(delete(task_events))
        await session.execute(delete(tasks))
        await session.execute(delete(installations))


async def seed_preview(
    database: Database,
    *,
    installation_id: InstallationId | None = None,
) -> tuple[InstallationId, TaskId, tuple[TargetId, ...]]:
    scoped_installation = installation_id or InstallationId.new()
    task_id = TaskId.new()
    async with database.session() as session:
        if installation_id is None:
            await session.execute(
                insert(installations).values(
                    id=scoped_installation.uuid,
                    device_public_key=secrets.token_bytes(32),
                    created_at=BASE,
                    updated_at=BASE,
                )
            )
        await session.execute(
            insert(tasks).values(
                id=task_id.uuid,
                installation_id=scoped_installation.uuid,
                creation_idempotency_key=f"task:preview:{task_id}",
                status=TaskStatus.AWAITING_CONFIRMATION.value,
                revision=4,
                last_event_sequence=3,
                created_at=BASE,
                updated_at=BASE,
            )
        )
    records = await SqlAlchemyTaskTargetRepository(database).evaluate_and_replace(
        task_id=task_id,
        installation_id=scoped_installation,
        candidates=(
            candidate("author-001", page_revision=7),
            candidate("author-002", page_revision=7),
            candidate("author-blocked", page_revision=7),
        ),
        blacklist=(candidate("author-blocked", page_revision=7).dedupe_key,),
        evaluated_at=BASE,
    )
    return scoped_installation, task_id, tuple(record.target_id for record in records)


def pending_exclusions(
    installation_id: InstallationId,
    task_id: TaskId,
    *,
    page_revision: int = 7,
    expected_task_revision: int = 4,
    excluded_target_ids: tuple[TargetId, ...] = (),
    idempotency_key: str = "task:preview:direct-exclude",
    requested_at: datetime = BASE + timedelta(seconds=1),
) -> PendingTaskTargetExclusions:
    return PendingTaskTargetExclusions(
        source_message_id=uuid4(),
        installation_id=installation_id,
        task_id=task_id,
        page_revision=page_revision,
        expected_task_revision=expected_task_revision,
        excluded_target_ids=excluded_target_ids,
        idempotency_key=idempotency_key,
        requested_at=requested_at,
    )


def pending_confirmation(
    installation_id: InstallationId,
    task_id: TaskId,
    *,
    page_revision: int = 7,
    expected_task_revision: int = 4,
    idempotency_key: str = "task:preview:direct-confirm",
    requested_at: datetime = BASE + timedelta(seconds=1),
) -> PendingTaskTargetConfirmation:
    return PendingTaskTargetConfirmation(
        source_message_id=uuid4(),
        installation_id=installation_id,
        task_id=task_id,
        page_revision=page_revision,
        expected_task_revision=expected_task_revision,
        idempotency_key=idempotency_key,
        requested_at=requested_at,
    )


@pytest.mark.asyncio
async def test_preview_paginates_excludes_confirms_and_replays_atomically(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    clock = MutableClock(BASE + timedelta(seconds=1))
    service = TaskTargetPreviewService(
        repository=SqlAlchemyTaskTargetPreviewRepository(database),
        clock=clock,
    )
    try:
        await reset_data(database)
        installation_id, task_id, target_ids = await seed_preview(database)

        first = await service.get(
            installation_id=installation_id,
            task_id=task_id,
            cursor=None,
            limit=2,
        )
        assert [item.target.ordinal for item in first.snapshot.items] == [1, 2]
        assert first.snapshot.selected_target_count == 2
        assert first.snapshot.user_excluded_target_count == 0
        assert first.next_cursor is not None
        second = await service.get(
            installation_id=installation_id,
            task_id=task_id,
            cursor=first.next_cursor,
            limit=2,
        )
        assert [item.target.ordinal for item in second.snapshot.items] == [3]
        assert second.next_cursor is None

        excluded = await service.replace_exclusions(
            installation_id=installation_id,
            task_id=task_id,
            page_revision=7,
            expected_task_revision=4,
            excluded_target_ids=(target_ids[1],),
            idempotency_key="task:preview:exclude:1",
        )
        assert excluded.replayed is False
        assert excluded.snapshot.task.revision == 5
        assert excluded.snapshot.task.last_event_sequence == 4
        assert excluded.snapshot.selected_target_count == 1
        assert excluded.snapshot.user_excluded_target_count == 1
        assert [item.selected for item in excluded.snapshot.items] == [True, False, False]

        replay = await service.replace_exclusions(
            installation_id=installation_id,
            task_id=task_id,
            page_revision=7,
            expected_task_revision=4,
            excluded_target_ids=(target_ids[1],),
            idempotency_key="task:preview:exclude:1",
        )
        assert replay.replayed is True
        assert replay.snapshot.task.revision == 5

        with pytest.raises(TaskTargetPreviewConflict):
            await service.get(
                installation_id=installation_id,
                task_id=task_id,
                cursor=first.next_cursor,
                limit=2,
            )

        clock.value += timedelta(seconds=1)
        confirmed = await service.confirm(
            installation_id=installation_id,
            task_id=task_id,
            page_revision=7,
            expected_task_revision=5,
            idempotency_key="task:preview:confirm:1",
        )
        assert confirmed.replayed is False
        assert confirmed.snapshot.task.status is TaskStatus.QUEUED
        assert confirmed.snapshot.task.revision == 6
        assert confirmed.snapshot.confirmed_at == clock.value

        replayed_confirmation = await service.confirm(
            installation_id=installation_id,
            task_id=task_id,
            page_revision=7,
            expected_task_revision=5,
            idempotency_key="task:preview:confirm:1",
        )
        assert replayed_confirmation.replayed is True
        assert replayed_confirmation.snapshot.task.revision == 6

        async with database.session() as session:
            confirmation_rows = list(
                (await session.execute(select(task_target_confirmations))).mappings().all()
            )
            event_types = list(
                await session.scalars(
                    select(task_events.c.event_type).order_by(task_events.c.sequence)
                )
            )
        assert len(confirmation_rows) == 1
        assert confirmation_rows[0]["selected_target_count"] == 1
        assert event_types == [
            TaskEventType.TASK_TARGET_SELECTION_UPDATED.value,
            TaskEventType.TASK_TARGETS_CONFIRMED.value,
        ]
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_replacing_discovered_targets_invalidates_the_previous_confirmation(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    preview_service = TaskTargetPreviewService(
        repository=SqlAlchemyTaskTargetPreviewRepository(database),
        clock=MutableClock(BASE + timedelta(seconds=1)),
    )
    target_repository = SqlAlchemyTaskTargetRepository(database)
    try:
        await reset_data(database)
        installation_id, task_id, _ = await seed_preview(database)
        await preview_service.confirm(
            installation_id=installation_id,
            task_id=task_id,
            page_revision=7,
            expected_task_revision=4,
            idempotency_key="task:preview:confirm-before-rediscovery",
        )

        await target_repository.evaluate_and_replace(
            task_id=task_id,
            installation_id=installation_id,
            candidates=(candidate("author-new", page_revision=8),),
            blacklist=(),
            evaluated_at=BASE + timedelta(seconds=2),
        )

        async with database.session() as session:
            confirmation_count = await session.scalar(
                select(func.count()).select_from(task_target_confirmations)
            )
        assert confirmation_count == 0
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_stale_cross_scope_and_policy_exclusions_are_rejected_without_mutation(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    service = TaskTargetPreviewService(
        repository=SqlAlchemyTaskTargetPreviewRepository(database),
        clock=MutableClock(BASE + timedelta(seconds=1)),
    )
    try:
        await reset_data(database)
        installation_id, task_id, target_ids = await seed_preview(database)
        other_installation, _, _ = await seed_preview(database)
        failures = (
            service.replace_exclusions(
                installation_id=installation_id,
                task_id=task_id,
                page_revision=6,
                expected_task_revision=4,
                excluded_target_ids=(target_ids[0],),
                idempotency_key="task:preview:stale-page",
            ),
            service.replace_exclusions(
                installation_id=installation_id,
                task_id=task_id,
                page_revision=7,
                expected_task_revision=3,
                excluded_target_ids=(target_ids[0],),
                idempotency_key="task:preview:stale-task",
            ),
            service.replace_exclusions(
                installation_id=installation_id,
                task_id=task_id,
                page_revision=7,
                expected_task_revision=4,
                excluded_target_ids=(target_ids[2],),
                idempotency_key="task:preview:policy-target",
            ),
        )
        for operation in failures:
            with pytest.raises(TaskTargetPreviewConflict):
                await operation
        with pytest.raises(TaskTargetPreviewNotFound):
            await service.get(
                installation_id=other_installation,
                task_id=task_id,
                cursor=None,
                limit=20,
            )
        snapshot = await service.get(
            installation_id=installation_id,
            task_id=task_id,
            cursor=None,
            limit=20,
        )
        assert snapshot.snapshot.task.revision == 4
        assert snapshot.snapshot.user_excluded_target_count == 0
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_confirmation_rejects_an_empty_selection_and_concurrent_stale_revision(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    clock = MutableClock(BASE + timedelta(seconds=1))
    service = TaskTargetPreviewService(
        repository=SqlAlchemyTaskTargetPreviewRepository(database),
        clock=clock,
    )
    try:
        await reset_data(database)
        installation_id, task_id, target_ids = await seed_preview(database)
        excluded = await service.replace_exclusions(
            installation_id=installation_id,
            task_id=task_id,
            page_revision=7,
            expected_task_revision=4,
            excluded_target_ids=target_ids[:2],
            idempotency_key="task:preview:exclude-all",
        )
        assert excluded.snapshot.selected_target_count == 0
        clock.value += timedelta(seconds=1)
        with pytest.raises(TaskTargetPreviewConflict):
            await service.confirm(
                installation_id=installation_id,
                task_id=task_id,
                page_revision=7,
                expected_task_revision=5,
                idempotency_key="task:preview:confirm-empty",
            )

        restored = await service.replace_exclusions(
            installation_id=installation_id,
            task_id=task_id,
            page_revision=7,
            expected_task_revision=5,
            excluded_target_ids=(),
            idempotency_key="task:preview:restore",
        )
        assert restored.snapshot.task.revision == 6
        clock.value += timedelta(seconds=1)
        results = await asyncio.gather(
            service.confirm(
                installation_id=installation_id,
                task_id=task_id,
                page_revision=7,
                expected_task_revision=6,
                idempotency_key="task:preview:confirm-race-a",
            ),
            service.confirm(
                installation_id=installation_id,
                task_id=task_id,
                page_revision=7,
                expected_task_revision=6,
                idempotency_key="task:preview:confirm-race-b",
            ),
            return_exceptions=True,
        )
        assert sum(not isinstance(result, Exception) for result in results) == 1
        assert sum(isinstance(result, TaskTargetPreviewConflict) for result in results) == 1
    finally:
        await reset_data(database)
        await database.close()


def test_preview_tables_export_only_minimal_relational_facts() -> None:
    assert set(task_target_exclusions.c.keys()) == {
        "target_id",
        "task_id",
        "installation_id",
        "page_revision",
        "excluded_at",
    }
    assert set(task_target_confirmations.c.keys()) == {
        "task_id",
        "installation_id",
        "page_revision",
        "selection_task_revision",
        "confirmed_task_revision",
        "selected_target_count",
        "source_message_id",
        "source_idempotency_key",
        "source_fingerprint",
        "confirmed_at",
        "created_at",
    }
    forbidden = {"cookie", "html", "profile_path", "raw_text", "url"}
    assert forbidden.isdisjoint(task_target_exclusions.c.keys())
    assert forbidden.isdisjoint(task_target_confirmations.c.keys())


@pytest.mark.asyncio
async def test_repository_defensive_scope_time_replay_and_snapshot_failures(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTaskTargetPreviewRepository(database)
    try:
        await reset_data(database)
        installation_id, task_id, target_ids = await seed_preview(database)
        other_installation, _, _ = await seed_preview(database)

        with pytest.raises(TaskTargetPreviewConflict):
            SqlAlchemyTaskTargetPreviewRepository(cast(Any, object()))
        with pytest.raises(TaskTargetPreviewNotFound):
            await repository.read_page(
                installation_id=cast(InstallationId, task_id),
                task_id=task_id,
                expected_page_revision=None,
                expected_task_revision=None,
                after_ordinal=None,
                after_target_id=None,
                limit=20,
            )
        with pytest.raises(TaskTargetPreviewConflict):
            await repository.replace_exclusions(cast(Any, object()))
        with pytest.raises(TaskTargetPreviewConflict):
            await repository.confirm(cast(Any, object()))
        with pytest.raises(TaskTargetPreviewNotFound):
            await repository.replace_exclusions(pending_exclusions(other_installation, task_id))
        with pytest.raises(TaskTargetPreviewConflict):
            await repository.replace_exclusions(
                pending_exclusions(
                    installation_id,
                    task_id,
                    expected_task_revision=5,
                )
            )
        with pytest.raises(TaskTargetPreviewConflict):
            await repository.replace_exclusions(
                pending_exclusions(
                    installation_id,
                    task_id,
                    idempotency_key="task:preview:old-clock",
                    requested_at=BASE - timedelta(microseconds=1),
                )
            )
        with pytest.raises(TaskTargetPreviewConflict):
            await repository.confirm(
                pending_confirmation(
                    installation_id,
                    task_id,
                    page_revision=6,
                    idempotency_key="task:preview:stale-confirm-page",
                )
            )
        with pytest.raises(TaskTargetPreviewConflict):
            await repository.read_page(
                installation_id=installation_id,
                task_id=task_id,
                expected_page_revision=6,
                expected_task_revision=4,
                after_ordinal=None,
                after_target_id=None,
                limit=20,
            )

        changed = await repository.replace_exclusions(
            pending_exclusions(
                installation_id,
                task_id,
                excluded_target_ids=(target_ids[1],),
                idempotency_key="task:preview:replay-tamper",
            )
        )
        assert changed.snapshot.task.revision == 5
        async with database.session() as session:
            await session.execute(delete(task_target_exclusions))
        with pytest.raises(TaskTargetPreviewConflict):
            await repository.replace_exclusions(
                pending_exclusions(
                    installation_id,
                    task_id,
                    excluded_target_ids=(target_ids[1],),
                    idempotency_key="task:preview:replay-tamper",
                )
            )
        with pytest.raises(TaskTargetPreviewConflict):
            await repository.replace_exclusions(
                pending_exclusions(
                    installation_id,
                    task_id,
                    excluded_target_ids=(),
                    idempotency_key="task:preview:replay-tamper",
                )
            )

        confirmed = await repository.confirm(
            pending_confirmation(
                installation_id,
                task_id,
                expected_task_revision=5,
                idempotency_key="task:preview:missing-confirmation-replay",
                requested_at=BASE + timedelta(seconds=2),
            )
        )
        assert confirmed.snapshot.task.status is TaskStatus.QUEUED
        async with database.session() as session:
            await session.execute(delete(task_target_confirmations))
        with pytest.raises(TaskTargetPreviewConflict):
            await repository.confirm(
                pending_confirmation(
                    installation_id,
                    task_id,
                    expected_task_revision=5,
                    idempotency_key="task:preview:missing-confirmation-replay",
                    requested_at=BASE + timedelta(seconds=2),
                )
            )
        with pytest.raises(TaskTargetPreviewConflict):
            await repository.replace_exclusions(
                pending_exclusions(
                    installation_id,
                    task_id,
                    expected_task_revision=6,
                    idempotency_key="task:preview:after-confirm",
                    requested_at=BASE + timedelta(seconds=3),
                )
            )
        with pytest.raises(TaskTargetPreviewConflict):
            await repository.read_page(
                installation_id=installation_id,
                task_id=task_id,
                expected_page_revision=7,
                expected_task_revision=6,
                after_ordinal=None,
                after_target_id=None,
                limit=20,
            )

        async with database.session() as session:
            await session.execute(
                update(installations)
                .where(installations.c.id == installation_id.uuid)
                .values(
                    status="revoked",
                    revision=2,
                    revoked_at=BASE + timedelta(seconds=4),
                    updated_at=BASE + timedelta(seconds=4),
                )
            )
        with pytest.raises(TaskTargetPreviewNotFound):
            await repository.confirm(
                pending_confirmation(
                    installation_id,
                    task_id,
                    expected_task_revision=6,
                    idempotency_key="task:preview:revoked",
                    requested_at=BASE + timedelta(seconds=5),
                )
            )
    finally:
        await reset_data(database)
        await database.close()


@pytest.mark.asyncio
async def test_repository_rejects_missing_and_mixed_target_snapshots(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyTaskTargetPreviewRepository(database)
    try:
        await reset_data(database)
        installation_id, task_id, _ = await seed_preview(database)
        async with database.session() as session:
            existing = (await session.execute(select(task_targets).limit(1))).mappings().one()
            await session.execute(
                insert(task_targets).values(
                    id=TargetId.new().uuid,
                    task_id=task_id.uuid,
                    installation_id=installation_id.uuid,
                    ordinal=4,
                    platform_target_id="author-mixed",
                    dedupe_key="atdck1_" + ("A" * 43),
                    display_name="混合快照",
                    public_handle="mixed_snapshot",
                    source=existing["source"],
                    page_revision=8,
                    disposition="eligible",
                    policy_version=existing["policy_version"],
                    evaluated_at=BASE,
                    created_at=BASE,
                )
            )
        with pytest.raises(TaskTargetPreviewNotFound):
            await repository.read_page(
                installation_id=installation_id,
                task_id=task_id,
                expected_page_revision=None,
                expected_task_revision=None,
                after_ordinal=None,
                after_target_id=None,
                limit=20,
            )

        await reset_data(database)
        installation_id = InstallationId.new()
        task_id = TaskId.new()
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
                    creation_idempotency_key="task:preview:empty",
                    status=TaskStatus.AWAITING_CONFIRMATION.value,
                    revision=4,
                    last_event_sequence=3,
                    created_at=BASE,
                    updated_at=BASE,
                )
            )
        with pytest.raises(TaskTargetPreviewNotFound):
            await repository.read_page(
                installation_id=installation_id,
                task_id=task_id,
                expected_page_revision=None,
                expected_task_revision=None,
                after_ordinal=None,
                after_target_id=None,
                limit=20,
            )
    finally:
        await reset_data(database)
        await database.close()
