"""VE-07: durable PostgreSQL ledger for finished editing-output lineages."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, text

from automation_tool.control_plane.domain.resource_ids import ArtifactId
from automation_tool.control_plane.domain.video_creation import TimelineId
from automation_tool.control_plane.domain.video_editing import EditingJobId, EditingProjectId
from automation_tool.control_plane.domain.video_editing_outputs import (
    EditingOutputArtifactRecord,
    EditingOutputCost,
    EditingOutputCostSource,
    EditingOutputKind,
    EditingOutputLedgerConflict,
    EditingOutputLineage,
)
from automation_tool.control_plane.domain.video_editing_provider import EditingProviderId
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.database.editing_output_ledger_repository import (
    SqlAlchemyEditingOutputLedger,
)
from automation_tool.control_plane.infrastructure.database.schema import (
    editing_output_artifacts,
    editing_output_lineages,
)

JOB_ID = EditingJobId(UUID("00000000-0000-4000-8000-0000000000dd"))
PROJECT_ID = EditingProjectId(UUID("00000000-0000-4000-8000-0000000000d1"))
TIMELINE_ID = TimelineId(UUID("00000000-0000-4000-8000-0000000000d2"))
INPUT_ARTIFACT = ArtifactId(UUID("00000000-0000-4000-8000-0000000000a1"))
VIDEO_ARTIFACT = ArtifactId(UUID("00000000-0000-4000-8000-0000000000b1"))
COVER_ARTIFACT = ArtifactId(UUID("00000000-0000-4000-8000-0000000000b2"))
CREATED_AT = datetime(2026, 7, 23, 12, 0, 0, tzinfo=UTC)


def _record(
    *,
    artifact_id: ArtifactId = VIDEO_ARTIFACT,
    kind: EditingOutputKind = EditingOutputKind.VIDEO,
    media_type: str = "video/mp4",
) -> EditingOutputArtifactRecord:
    return EditingOutputArtifactRecord(
        artifact_id=artifact_id,
        kind=kind,
        media_type=media_type,
        byte_size=2048,
        sha256_hex="ab" * 32,
        created_at=CREATED_AT,
    )


def _lineage(
    *, outputs: tuple[EditingOutputArtifactRecord, ...] | None = None
) -> EditingOutputLineage:
    return EditingOutputLineage(
        editing_job_id=JOB_ID,
        project_id=PROJECT_ID,
        timeline_id=TIMELINE_ID,
        timeline_revision=2,
        provider_id=EditingProviderId("aliyun_ims"),
        provider_contract_verified_at="2026-07-23",
        input_artifact_ids=(INPUT_ARTIFACT,),
        outputs=outputs
        if outputs is not None
        else (
            _record(),
            _record(
                artifact_id=COVER_ARTIFACT,
                kind=EditingOutputKind.COVER,
                media_type="image/png",
            ),
        ),
        cost=EditingOutputCost(
            source=EditingOutputCostSource.ESTIMATED,
            currency="CNY",
            billed_minutes=1,
            tier_id="up_to_1080p",
            unit_price_cny=Decimal("0.07"),
            total_cny=Decimal("0.07"),
        ),
        created_at=CREATED_AT,
    )


async def _reset(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(editing_output_artifacts))
        await session.execute(delete(editing_output_lineages))


@pytest.mark.asyncio
async def test_lineage_roundtrip_write_once_and_conflict(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    ledger = SqlAlchemyEditingOutputLedger(database)
    try:
        await _reset(database)
        assert await ledger.load(JOB_ID) is None

        await ledger.save(_lineage())
        loaded = await ledger.load(JOB_ID)
        assert loaded == _lineage()

        # Identical replay is idempotent; a different lineage is rejected.
        await ledger.save(_lineage())
        with pytest.raises(EditingOutputLedgerConflict):
            await ledger.save(_lineage(outputs=(_record(),)))
        assert await ledger.load(JOB_ID) == _lineage()
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_lineage_survives_process_restart(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    first = Database.from_url(postgresql_url)
    try:
        await _reset(first)
        await SqlAlchemyEditingOutputLedger(first).save(_lineage())
    finally:
        await first.close()

    second = Database.from_url(postgresql_url)
    try:
        assert await SqlAlchemyEditingOutputLedger(second).load(JOB_ID) == _lineage()
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_database_rejects_second_video_and_bad_media_type(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await _reset(database)
        await SqlAlchemyEditingOutputLedger(database).save(_lineage())

        async with database.session() as session:
            with pytest.raises(Exception, match="ux_editing_output_artifacts_one_video"):
                await session.execute(
                    editing_output_artifacts.insert().values(
                        artifact_id=UUID("00000000-0000-4000-8000-0000000000b9"),
                        editing_job_id=JOB_ID.uuid,
                        position=9,
                        kind="video",
                        media_type="video/mp4",
                        byte_size=1,
                        sha256_hex="cd" * 32,
                        created_at=CREATED_AT,
                    )
                )
        async with database.session() as session:
            with pytest.raises(Exception, match="ck_editing_output_artifacts_media"):
                await session.execute(
                    editing_output_artifacts.insert().values(
                        artifact_id=UUID("00000000-0000-4000-8000-0000000000ba"),
                        editing_job_id=JOB_ID.uuid,
                        position=9,
                        kind="cover",
                        media_type="video/mp4",
                        byte_size=1,
                        sha256_hex="cd" * 32,
                        created_at=CREATED_AT,
                    )
                )
        async with database.session() as session:
            with pytest.raises(Exception, match="ck_editing_output_lineages_cost_total"):
                await session.execute(
                    text(
                        "update editing_output_lineages"
                        " set cost_total_cny = cost_total_cny + 1"
                        " where editing_job_id = :job"
                    ),
                    {"job": JOB_ID.uuid},
                )
    finally:
        await database.close()
