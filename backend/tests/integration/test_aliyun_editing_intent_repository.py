"""VE-06: durable PostgreSQL intent store for Aliyun editing reconciliation."""

from uuid import UUID

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete

from automation_tool.control_plane.domain.aliyun_ims_editing_provider import (
    AliyunEditingIntent,
    AliyunEditingIntentState,
)
from automation_tool.control_plane.domain.resource_ids import ArtifactId
from automation_tool.control_plane.domain.video_editing import (
    EditingFailureCode,
    EditingJobId,
    EditingJobStatus,
)
from automation_tool.control_plane.domain.video_editing_provider import (
    EditingProviderFailure,
)
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.database.aliyun_editing_intent_repository import (
    SqlAlchemyAliyunEditingIntentStore,
)
from automation_tool.control_plane.infrastructure.database.schema import aliyun_editing_intents

JOB_ID = EditingJobId(UUID("00000000-0000-4000-8000-0000000000cc"))
VENDOR_JOB_ID = "46c446e2420348e0950e4d7876acc6fb"
REQUEST_HASH = "ab" * 32
OUTPUT_ARTIFACT = ArtifactId(UUID("00000000-0000-4000-8000-000000000009"))


def _intent(
    *,
    editing_job_id: EditingJobId = JOB_ID,
    state: AliyunEditingIntentState = AliyunEditingIntentState.DISPATCHED,
    vendor_job_id: str | None = VENDOR_JOB_ID,
    status: EditingJobStatus = EditingJobStatus.QUEUED,
    failure_code: EditingFailureCode | None = None,
    output_artifact_ids: tuple[ArtifactId, ...] = (),
) -> AliyunEditingIntent:
    return AliyunEditingIntent(
        editing_job_id=editing_job_id,
        request_hash=REQUEST_HASH,
        state=state,
        vendor_job_id=vendor_job_id,
        status=status,
        failure_code=failure_code,
        output_artifact_ids=output_artifact_ids,
    )


async def _reset(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(aliyun_editing_intents))


@pytest.mark.asyncio
async def test_intent_roundtrip_upsert_and_lookup(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    store = SqlAlchemyAliyunEditingIntentStore(database)
    try:
        await _reset(database)
        assert await store.load(JOB_ID) is None
        assert await store.load_all() == ()
        assert await store.load_by_vendor_job_id(VENDOR_JOB_ID) is None

        prepared = _intent(
            state=AliyunEditingIntentState.PREPARED,
            vendor_job_id=None,
            status=EditingJobStatus.QUEUED,
        )
        await store.save(prepared)
        assert await store.load(JOB_ID) == prepared

        dispatched = _intent()
        await store.save(dispatched)
        assert await store.load(JOB_ID) == dispatched
        assert await store.load_by_vendor_job_id(VENDOR_JOB_ID) == dispatched

        succeeded = _intent(
            status=EditingJobStatus.SUCCEEDED,
            output_artifact_ids=(OUTPUT_ARTIFACT,),
        )
        await store.save(succeeded)
        loaded = await store.load(JOB_ID)
        assert loaded == succeeded
        assert loaded is not None and loaded.output_artifact_ids == (OUTPUT_ARTIFACT,)

        # One acknowledged vendor JobId can never belong to two editing jobs.
        with pytest.raises(EditingProviderFailure):
            await store.save(
                _intent(
                    editing_job_id=EditingJobId(
                        UUID("00000000-0000-4000-8000-0000000000ff")
                    )
                )
            )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_intents_survive_process_restart(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await _reset(database)
        store = SqlAlchemyAliyunEditingIntentStore(database)
        running_id = EditingJobId(UUID("00000000-0000-4000-8000-000000000011"))
        failed_id = EditingJobId(UUID("00000000-0000-4000-8000-000000000012"))
        uncertain_id = EditingJobId(UUID("00000000-0000-4000-8000-000000000013"))
        await store.save(
            _intent(editing_job_id=running_id, status=EditingJobStatus.RUNNING)
        )
        await store.save(
            _intent(
                editing_job_id=failed_id,
                vendor_job_id="1f470a3a89d94f41a43bc6419ba6a144",
                status=EditingJobStatus.FAILED,
                failure_code=EditingFailureCode.EDITING_FAILED,
            )
        )
        await store.save(
            _intent(
                editing_job_id=uncertain_id,
                state=AliyunEditingIntentState.UNCERTAIN,
                vendor_job_id=None,
                status=EditingJobStatus.OUTCOME_UNCERTAIN,
            )
        )
    finally:
        await database.close()

    # A fresh engine over the same PostgreSQL simulates the App restart.
    restarted_database = Database.from_url(postgresql_url)
    restarted = SqlAlchemyAliyunEditingIntentStore(restarted_database)
    try:
        intents = await restarted.load_all()
        by_job = {intent.editing_job_id: intent for intent in intents}
        assert set(by_job) == {
            EditingJobId(UUID("00000000-0000-4000-8000-000000000011")),
            EditingJobId(UUID("00000000-0000-4000-8000-000000000012")),
            EditingJobId(UUID("00000000-0000-4000-8000-000000000013")),
        }
        running = by_job[EditingJobId(UUID("00000000-0000-4000-8000-000000000011"))]
        assert running.state is AliyunEditingIntentState.DISPATCHED
        assert running.status is EditingJobStatus.RUNNING
        uncertain = by_job[EditingJobId(UUID("00000000-0000-4000-8000-000000000013"))]
        assert uncertain.state is AliyunEditingIntentState.UNCERTAIN
    finally:
        await restarted_database.close()


@pytest.mark.asyncio
async def test_schema_rejects_invariant_breaking_rows(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    try:
        await _reset(database)
        from sqlalchemy import insert
        from sqlalchemy.exc import IntegrityError

        base = {
            "editing_job_id": JOB_ID.uuid,
            "request_hash": REQUEST_HASH,
            "state": "prepared",
            "vendor_job_id": None,
            "status": "queued",
            "failure_code": None,
            "output_artifact_ids": [],
        }
        invalid_rows = [
            # PREPARED must not carry a vendor JobId.
            {**base, "vendor_job_id": VENDOR_JOB_ID},
            # UNCERTAIN must persist the outcome_uncertain status.
            {**base, "state": "uncertain", "status": "running"},
            # DISPATCHED must carry the acknowledged vendor JobId.
            {**base, "state": "dispatched", "vendor_job_id": None},
            # SUCCEEDED must reference at least one confirmed output.
            {
                **base,
                "state": "dispatched",
                "vendor_job_id": VENDOR_JOB_ID,
                "status": "succeeded",
                "output_artifact_ids": [],
            },
            # FAILED must persist a failure code.
            {
                **base,
                "state": "dispatched",
                "vendor_job_id": VENDOR_JOB_ID,
                "status": "failed",
                "failure_code": None,
            },
            # Unknown lifecycle tokens are rejected by the closed vocabulary.
            {**base, "status": "migrating"},
        ]
        for row in invalid_rows:
            with pytest.raises(IntegrityError):
                async with database.session() as session:
                    await session.execute(insert(aliyun_editing_intents).values(**row))
    finally:
        await database.close()
