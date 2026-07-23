"""PB-03: PostgreSQL attempt store for Bilibili publishing (single admission)."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, insert

from automation_tool.control_plane.application.bilibili_archive_publishing import (
    BilibiliArchiveFields,
    BilibiliArchivePublishRejected,
    BilibiliArchivePublishUnavailable,
    BilibiliPublishAttemptRecord,
    BilibiliPublishMaterial,
    BilibiliPublishPhase,
    BilibiliUploadType,
)
from automation_tool.control_plane.domain.video_publishing import (
    PublishFailureCode,
    PublishJobId,
)
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.database.bilibili_publish_repository import (
    SqlAlchemyBilibiliArchivePublishStore,
)
from automation_tool.control_plane.infrastructure.database.schema import (
    bilibili_publish_attempts,
    bilibili_upload_parts,
)

NOW = datetime(2026, 7, 23, 2, 0, tzinfo=UTC)


def make_record(
    publish_job_id: PublishJobId,
    *,
    upload_type: BilibiliUploadType = BilibiliUploadType.CHUNKED,
    title: str = "契约样例一分钟看懂分片上传",
) -> BilibiliPublishAttemptRecord:
    chunked = upload_type is BilibiliUploadType.CHUNKED
    return BilibiliPublishAttemptRecord(
        publish_job_id=publish_job_id,
        phase=BilibiliPublishPhase.PREPARED,
        request_digest=hashlib.sha256(title.encode("utf-8")).hexdigest(),
        material=BilibiliPublishMaterial(
            file_name="demo.mp4",
            size_bytes=113_246_213 if chunked else 1_024,
            duration_seconds=1_800,
            sha256="cd" * 32,
        ),
        fields=BilibiliArchiveFields(
            title=title,
            tid=21,
            tag="科技,教程",
            copyright=1,
            description="样例描述",
            source=None,
            no_reprint=0,
        ),
        upload_type=upload_type,
        part_size_bytes=8_388_608 if chunked else 0,
        part_count=14 if chunked else 0,
        has_cover=True,
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
        await session.execute(delete(bilibili_upload_parts))
        await session.execute(delete(bilibili_publish_attempts))


async def uploaded_record(
    store: SqlAlchemyBilibiliArchivePublishStore, publish_job_id: PublishJobId
) -> None:
    await store.create_prepared(make_record(publish_job_id))
    await store.record_upload_token(
        publish_job_id, "fixture-upload-token-000000000000", NOW + timedelta(seconds=1)
    )
    await store.record_cover_url(
        publish_job_id,
        "https://archive.biliimg.com/bfs/archive/fixture.jpg",
        NOW + timedelta(seconds=2),
    )
    await store.record_video_uploaded(publish_job_id, NOW + timedelta(seconds=3))


@pytest.mark.asyncio
async def test_prepared_attempt_roundtrips_and_is_idempotent(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    store = SqlAlchemyBilibiliArchivePublishStore(database)
    try:
        await reset_data(database)
        publish_job_id = PublishJobId.new()
        record = make_record(publish_job_id)
        first = await store.create_prepared(record)
        assert first.replayed is False
        assert first.record == record

        loaded = await store.load(publish_job_id)
        assert loaded == record

        replay = await store.create_prepared(record)
        assert replay.replayed is True
        assert replay.record == record

        with pytest.raises(BilibiliArchivePublishRejected):
            await store.create_prepared(make_record(publish_job_id, title="换了标题的第二次准备"))
        assert await store.load(PublishJobId.new()) is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_part_progress_is_durable_and_duplicate_free(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    store = SqlAlchemyBilibiliArchivePublishStore(database)
    try:
        await reset_data(database)
        publish_job_id = PublishJobId.new()
        await store.create_prepared(make_record(publish_job_id))
        await store.record_upload_token(publish_job_id, "fixture-upload-token-000000000000", NOW)
        assert await store.record_part_completed(publish_job_id, 1, 8_388_608, NOW) is True
        assert await store.record_part_completed(publish_job_id, 3, 8_388_608, NOW) is True
        assert await store.record_part_completed(publish_job_id, 1, 8_388_608, NOW) is False
        assert await store.completed_part_numbers(publish_job_id) == frozenset({1, 3})

        restarted = SqlAlchemyBilibiliArchivePublishStore(Database.from_url(postgresql_url))
        assert await restarted.completed_part_numbers(publish_job_id) == frozenset({1, 3})
        loaded = await restarted.load(publish_job_id)
        assert loaded is not None
        assert loaded.upload_token == "fixture-upload-token-000000000000"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_video_uploaded_requires_prepared_phase_with_upload_token(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    store = SqlAlchemyBilibiliArchivePublishStore(database)
    try:
        await reset_data(database)
        publish_job_id = PublishJobId.new()
        await store.create_prepared(make_record(publish_job_id))
        with pytest.raises(BilibiliArchivePublishRejected):
            await store.record_video_uploaded(publish_job_id, NOW)
        with pytest.raises(BilibiliArchivePublishRejected):
            await store.record_video_uploaded(PublishJobId.new(), NOW)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_archive_creation_admission_is_granted_exactly_once(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    store = SqlAlchemyBilibiliArchivePublishStore(database)
    try:
        await reset_data(database)
        publish_job_id = PublishJobId.new()
        await uploaded_record(store, publish_job_id)

        premature = PublishJobId.new()
        await store.create_prepared(make_record(premature))
        assert await store.begin_archive_creation(premature, NOW) is False

        outcomes = await asyncio.gather(
            store.begin_archive_creation(publish_job_id, NOW + timedelta(seconds=4)),
            store.begin_archive_creation(publish_job_id, NOW + timedelta(seconds=4)),
        )
        assert sorted(outcomes) == [False, True]
        assert await store.begin_archive_creation(publish_job_id, NOW) is False

        loaded = await store.load(publish_job_id)
        assert loaded is not None
        assert loaded.phase is BilibiliPublishPhase.DISPATCHED
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_settlements_are_terminal_and_exclusive(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    store = SqlAlchemyBilibiliArchivePublishStore(database)
    try:
        await reset_data(database)

        submitted = PublishJobId.new()
        await uploaded_record(store, submitted)
        assert await store.begin_archive_creation(submitted, NOW) is True
        await store.record_submitted(submitted, "BV17B4y1s7R1", NOW + timedelta(seconds=5))
        loaded = await store.load(submitted)
        assert loaded is not None
        assert loaded.phase is BilibiliPublishPhase.SUBMITTED
        assert loaded.resource_id == "BV17B4y1s7R1"
        with pytest.raises(BilibiliArchivePublishRejected):
            await store.record_outcome_uncertain(submitted, NOW + timedelta(seconds=6))

        failed = PublishJobId.new()
        await uploaded_record(store, failed)
        assert await store.begin_archive_creation(failed, NOW) is True
        await store.record_failed(
            failed,
            PublishFailureCode.INVALID_INPUT,
            123013,
            NOW + timedelta(seconds=5),
        )
        loaded_failed = await store.load(failed)
        assert loaded_failed is not None
        assert loaded_failed.phase is BilibiliPublishPhase.FAILED
        assert loaded_failed.failure_code is PublishFailureCode.INVALID_INPUT
        assert loaded_failed.platform_error_code == 123013

        uncertain = PublishJobId.new()
        await uploaded_record(store, uncertain)
        assert await store.begin_archive_creation(uncertain, NOW) is True
        await store.record_outcome_uncertain(uncertain, NOW + timedelta(seconds=5))
        loaded_uncertain = await store.load(uncertain)
        assert loaded_uncertain is not None
        assert loaded_uncertain.phase is BilibiliPublishPhase.OUTCOME_UNCERTAIN
        with pytest.raises(BilibiliArchivePublishRejected):
            await store.record_submitted(uncertain, "BV17B4y1s7R1", NOW)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_database_constraints_reject_inconsistent_rows(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await reset_data(database)
        base: dict[str, object] = {
            "publish_job_id": PublishJobId.new().uuid,
            "phase": "submitted",
            "request_digest": "ab" * 32,
            "material_file_name": "demo.mp4",
            "material_size_bytes": 1024,
            "material_duration_seconds": 90,
            "material_sha256": "cd" * 32,
            "title": "标题",
            "tid": 21,
            "tag": "科技",
            "copyright": 1,
            "description": None,
            "source": None,
            "no_reprint": 0,
            "upload_type": "0",
            "part_size_bytes": 0,
            "part_count": 0,
            "has_cover": False,
            "upload_token": "fixture-upload-token-000000000000",
            "cover_url": None,
            "video_uploaded_at": NOW,
            "dispatched_at": NOW,
            "settled_at": NOW,
            "resource_id": None,
            "failure_code": None,
            "platform_error_code": None,
            "created_at": NOW,
            "updated_at": NOW,
        }
        with pytest.raises(Exception, match=r"ck_|Integrity"):
            async with database.session() as session:
                await session.execute(insert(bilibili_publish_attempts).values(**base))
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_database_error_mapping_is_closed(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    store = SqlAlchemyBilibiliArchivePublishStore(database)
    try:
        await reset_data(database)

        oversized = make_record(PublishJobId.new(), title="超" * 100)
        with pytest.raises(BilibiliArchivePublishUnavailable):
            await store.create_prepared(oversized)

        publish_job_id = PublishJobId.new()
        await store.create_prepared(make_record(publish_job_id))
        with pytest.raises(BilibiliArchivePublishRejected):
            await store.record_upload_token(
                publish_job_id,
                "fixture-upload-token-000000000000",
                NOW - timedelta(days=1),
            )

        first = PublishJobId.new()
        outcomes = await asyncio.gather(
            store.create_prepared(make_record(first, title="并发准备一")),
            store.create_prepared(make_record(first, title="并发准备二")),
            return_exceptions=True,
        )
        successes = [o for o in outcomes if not isinstance(o, BaseException)]
        failures = [o for o in outcomes if isinstance(o, BaseException)]
        assert len(successes) == 1
        assert len(failures) == 1
        assert isinstance(failures[0], BilibiliArchivePublishRejected)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_progress_guards_reject_missing_jobs_and_wrong_phases(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    store = SqlAlchemyBilibiliArchivePublishStore(database)
    try:
        await reset_data(database)
        missing = PublishJobId.new()
        with pytest.raises(BilibiliArchivePublishRejected):
            await store.record_upload_token(missing, "fixture-upload-token", NOW)
        with pytest.raises(BilibiliArchivePublishRejected):
            await store.record_part_completed(missing, 1, 8_388_608, NOW)
        with pytest.raises(BilibiliArchivePublishRejected):
            await store.completed_part_numbers(missing)
        with pytest.raises(BilibiliArchivePublishRejected):
            await store.record_cover_url(missing, "https://example.com/cover.jpg", NOW)
        with pytest.raises(BilibiliArchivePublishRejected):
            await store.begin_archive_creation(missing, NOW)
        with pytest.raises(BilibiliArchivePublishRejected):
            await store.record_failed(missing, PublishFailureCode.PLATFORM_ERROR, 4010, NOW)
        with pytest.raises(BilibiliArchivePublishRejected):
            await store.record_submitted(missing, "BV17B4y1s7R1", NOW)
        with pytest.raises(BilibiliArchivePublishRejected):
            await store.record_outcome_uncertain(missing, NOW)

        small = PublishJobId.new()
        await store.create_prepared(
            make_record(small, upload_type=BilibiliUploadType.SMALL, title="小文件")
        )
        with pytest.raises(BilibiliArchivePublishRejected):
            await store.record_part_completed(small, 1, 1_024, NOW)

        chunked = PublishJobId.new()
        await store.create_prepared(make_record(chunked, title="超界分片"))
        with pytest.raises(BilibiliArchivePublishRejected):
            await store.record_part_completed(chunked, 15, 8_388_608, NOW)
    finally:
        await database.close()
