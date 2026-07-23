"""PB-04: PostgreSQL reconciliation store — monotonic outcomes, restart recovery."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, update

from automation_tool.control_plane.application.bilibili_archive_publishing import (
    BilibiliArchiveFields,
    BilibiliArchivePublishRejected,
    BilibiliPublishAttemptRecord,
    BilibiliPublishMaterial,
    BilibiliPublishPhase,
    BilibiliUploadType,
)
from automation_tool.control_plane.application.bilibili_archive_reconciliation import (
    BilibiliReconciliationOutcome,
)
from automation_tool.control_plane.domain.video_publishing import (
    PublishFailureCode,
    PublishJobId,
)
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.database.bilibili_publish_repository import (
    SqlAlchemyBilibiliArchivePublishStore,
    SqlAlchemyBilibiliReconciliationStore,
)
from automation_tool.control_plane.infrastructure.database.schema import (
    bilibili_publish_attempts,
    bilibili_publish_reconciliations,
    bilibili_upload_parts,
)

NOW = datetime(2026, 7, 23, 2, 0, tzinfo=UTC)
RESOURCE_ID = "BV17B4y1s7R1"


def make_record(publish_job_id: PublishJobId) -> BilibiliPublishAttemptRecord:
    return BilibiliPublishAttemptRecord(
        publish_job_id=publish_job_id,
        phase=BilibiliPublishPhase.PREPARED,
        request_digest=hashlib.sha256(str(publish_job_id).encode("utf-8")).hexdigest(),
        material=BilibiliPublishMaterial(
            file_name="demo.mp4",
            size_bytes=1_024,
            duration_seconds=90,
            sha256="cd" * 32,
        ),
        fields=BilibiliArchiveFields(
            title="契约样例一分钟看懂分片上传",
            tid=21,
            tag="科技,教程",
            copyright=1,
            description="样例描述",
            source=None,
            no_reprint=0,
        ),
        upload_type=BilibiliUploadType.SMALL,
        part_size_bytes=0,
        part_count=0,
        has_cover=False,
        upload_token=None,
        cover_url=None,
        video_uploaded_at=None,
        dispatched_at=None,
        settled_at=None,
        resource_id=None,
        failure_code=None,
        platform_error_code=None,
        created_at=NOW,
        updated_at=NOW,
    )


async def reset_data(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(bilibili_publish_reconciliations))
        await session.execute(delete(bilibili_upload_parts))
        await session.execute(delete(bilibili_publish_attempts))


async def attempt_in_phase(
    store: SqlAlchemyBilibiliArchivePublishStore,
    phase: BilibiliPublishPhase,
    *,
    resource_id: str | None = None,
) -> PublishJobId:
    publish_job_id = PublishJobId.new()
    await store.create_prepared(make_record(publish_job_id))
    if phase is BilibiliPublishPhase.PREPARED:
        return publish_job_id
    await store.record_upload_token(
        publish_job_id, "fixture-upload-token-000000000000", NOW + timedelta(seconds=1)
    )
    await store.record_video_uploaded(publish_job_id, NOW + timedelta(seconds=2))
    if phase is BilibiliPublishPhase.VIDEO_UPLOADED:
        return publish_job_id
    assert await store.begin_archive_creation(publish_job_id, NOW + timedelta(seconds=3))
    if phase is BilibiliPublishPhase.DISPATCHED:
        return publish_job_id
    if phase is BilibiliPublishPhase.SUBMITTED:
        await store.record_submitted(
            publish_job_id, resource_id or RESOURCE_ID, NOW + timedelta(seconds=4)
        )
        return publish_job_id
    if phase is BilibiliPublishPhase.OUTCOME_UNCERTAIN:
        await store.record_outcome_uncertain(publish_job_id, NOW + timedelta(seconds=4))
        return publish_job_id
    await store.record_failed(
        publish_job_id,
        PublishFailureCode.INVALID_INPUT,
        123024,
        NOW + timedelta(seconds=4),
    )
    return publish_job_id


@pytest.mark.asyncio
async def test_attempt_store_lists_only_reconcilable_phases(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    attempts = SqlAlchemyBilibiliArchivePublishStore(database)
    try:
        await reset_data(database)
        submitted = await attempt_in_phase(attempts, BilibiliPublishPhase.SUBMITTED)
        dispatched = await attempt_in_phase(attempts, BilibiliPublishPhase.DISPATCHED)
        uncertain = await attempt_in_phase(attempts, BilibiliPublishPhase.OUTCOME_UNCERTAIN)
        await attempt_in_phase(attempts, BilibiliPublishPhase.PREPARED)
        await attempt_in_phase(attempts, BilibiliPublishPhase.FAILED)

        reconcilable = await attempts.list_reconcilable()

        assert {record.publish_job_id for record in reconcilable} == {
            submitted,
            dispatched,
            uncertain,
        }
        for record in reconcilable:
            assert record.phase in {
                BilibiliPublishPhase.SUBMITTED,
                BilibiliPublishPhase.DISPATCHED,
                BilibiliPublishPhase.OUTCOME_UNCERTAIN,
            }
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_pending_row_is_idempotent_and_survives_restart(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    attempts = SqlAlchemyBilibiliArchivePublishStore(database)
    store = SqlAlchemyBilibiliReconciliationStore(database)
    try:
        await reset_data(database)
        publish_job_id = await attempt_in_phase(attempts, BilibiliPublishPhase.SUBMITTED)

        first = await store.ensure_pending(publish_job_id, RESOURCE_ID, NOW)
        assert first.outcome is BilibiliReconciliationOutcome.PENDING
        assert first.resource_id == RESOURCE_ID

        replay = await store.ensure_pending(publish_job_id, None, NOW + timedelta(minutes=1))
        assert replay == first

        restarted = SqlAlchemyBilibiliReconciliationStore(database)
        loaded = await restarted.load(publish_job_id)
        assert loaded == first
        assert await restarted.find_by_resource_id(RESOURCE_ID) == first
        assert await restarted.find_by_resource_id("BV1MW421X7gM") is None
        assert [record.publish_job_id for record in await restarted.list_unsettled()] == [
            publish_job_id
        ]
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_settlement_is_monotonic_and_never_regresses(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    attempts = SqlAlchemyBilibiliArchivePublishStore(database)
    store = SqlAlchemyBilibiliReconciliationStore(database)
    try:
        await reset_data(database)
        publish_job_id = await attempt_in_phase(attempts, BilibiliPublishPhase.SUBMITTED)
        await store.ensure_pending(publish_job_id, RESOURCE_ID, NOW)
        await store.record_checked(publish_job_id, -30, NOW + timedelta(minutes=1))

        settled = await store.settle(
            publish_job_id,
            BilibiliReconciliationOutcome.PUBLISHED,
            0,
            None,
            NOW + timedelta(minutes=2),
        )
        assert settled is True

        regression = await store.settle(
            publish_job_id,
            BilibiliReconciliationOutcome.REJECTED,
            -2,
            None,
            NOW + timedelta(minutes=3),
        )
        assert regression is False

        record = await store.load(publish_job_id)
        assert record is not None
        assert record.outcome is BilibiliReconciliationOutcome.PUBLISHED
        assert record.archive_state == 0
        assert record.settled_at is not None
        assert await store.list_unsettled() == ()

        with pytest.raises(BilibiliArchivePublishRejected):
            await store.record_checked(publish_job_id, -30, NOW + timedelta(minutes=4))
        with pytest.raises(BilibiliArchivePublishRejected):
            await store.adopt_resource_id(
                publish_job_id, "BV1MW421X7gM", NOW + timedelta(minutes=4)
            )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_failed_settlement_requires_failure_code(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    attempts = SqlAlchemyBilibiliArchivePublishStore(database)
    store = SqlAlchemyBilibiliReconciliationStore(database)
    try:
        await reset_data(database)
        publish_job_id = await attempt_in_phase(attempts, BilibiliPublishPhase.OUTCOME_UNCERTAIN)
        await store.ensure_pending(publish_job_id, None, NOW)

        with pytest.raises(BilibiliArchivePublishRejected):
            await store.settle(
                publish_job_id,
                BilibiliReconciliationOutcome.FAILED,
                None,
                None,
                NOW + timedelta(minutes=1),
            )

        settled = await store.settle(
            publish_job_id,
            BilibiliReconciliationOutcome.FAILED,
            None,
            PublishFailureCode.PLATFORM_ERROR,
            NOW + timedelta(minutes=2),
        )
        assert settled is True
        record = await store.load(publish_job_id)
        assert record is not None
        assert record.outcome is BilibiliReconciliationOutcome.FAILED
        assert record.failure_code is PublishFailureCode.PLATFORM_ERROR
        assert record.resource_id is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_adoption_binds_the_recovered_resource_exactly_once(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    attempts = SqlAlchemyBilibiliArchivePublishStore(database)
    store = SqlAlchemyBilibiliReconciliationStore(database)
    try:
        await reset_data(database)
        publish_job_id = await attempt_in_phase(attempts, BilibiliPublishPhase.OUTCOME_UNCERTAIN)
        await store.ensure_pending(publish_job_id, None, NOW)

        await store.adopt_resource_id(publish_job_id, RESOURCE_ID, NOW + timedelta(minutes=1))
        record = await store.load(publish_job_id)
        assert record is not None
        assert record.resource_id == RESOURCE_ID
        assert record.outcome is BilibiliReconciliationOutcome.PENDING

        with pytest.raises(BilibiliArchivePublishRejected):
            await store.adopt_resource_id(
                publish_job_id, "BV1MW421X7gM", NOW + timedelta(minutes=2)
            )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_reconciliation_requires_an_existing_attempt(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    store = SqlAlchemyBilibiliReconciliationStore(database)
    try:
        await reset_data(database)
        with pytest.raises(BilibiliArchivePublishRejected):
            await store.ensure_pending(PublishJobId.new(), None, NOW)
        with pytest.raises(BilibiliArchivePublishRejected):
            await store.settle(
                PublishJobId.new(),
                BilibiliReconciliationOutcome.PUBLISHED,
                0,
                None,
                NOW,
            )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_database_constraints_reject_inconsistent_reconciliation_rows(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    attempts = SqlAlchemyBilibiliArchivePublishStore(database)
    store = SqlAlchemyBilibiliReconciliationStore(database)
    try:
        await reset_data(database)
        publish_job_id = await attempt_in_phase(attempts, BilibiliPublishPhase.SUBMITTED)
        await store.ensure_pending(publish_job_id, RESOURCE_ID, NOW)

        inconsistent_rows: tuple[dict[str, object], ...] = (
            {"outcome": "exploded"},
            {"outcome": "published"},
            {"settled_at": NOW},
            {"failure_code": "platform_error"},
            {"outcome": "failed", "settled_at": NOW},
            {"updated_at": NOW - timedelta(minutes=5)},
        )
        for values in inconsistent_rows:
            with pytest.raises(Exception, match=r"ck_|Integrity"):
                async with database.session() as session:
                    await session.execute(
                        update(bilibili_publish_reconciliations)
                        .where(
                            bilibili_publish_reconciliations.c.publish_job_id == publish_job_id.uuid
                        )
                        .values(**values)
                    )
    finally:
        await database.close()
