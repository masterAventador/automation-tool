"""LE-19 T3: smart-edit analyses and narrations on one real PostgreSQL transaction."""

from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime

import pytest
from conftest import AlembicRunner
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, select, text

from automation_tool.control_plane import create_app
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.materials import (
    MaterialDescriptionProtected,
    MaterialNotFound,
    MaterialPersistenceUnavailable,
    SmartEditMaterialAnalysisWriteback,
    SmartEditMaterialWriteback,
)
from automation_tool.control_plane.domain import (
    DescriptionSource,
    InstallationId,
    Material,
    MaterialId,
    MaterialKind,
)
from automation_tool.control_plane.infrastructure.database import (
    Database,
    installations,
    materials,
)
from automation_tool.control_plane.infrastructure.database.material_repository import (
    SqlAlchemyMaterialRepository,
)

OWNER = InstallationId.parse("00000000-0000-4000-8000-000000000101")
VIDEO_ID = MaterialId.parse("00000000-0000-4000-8000-000000000102")
NARRATION_ID = MaterialId.parse("00000000-0000-4000-8000-000000000103")
VIDEO_DIGEST = "a1" * 32
NARRATION_DIGEST = "b2" * 32
ORIGINAL_TIME = datetime(2026, 8, 1, 1, 2, 3, tzinfo=UTC)
UPDATED_TIME = datetime(2026, 8, 1, 4, 5, 6, tzinfo=UTC)


def _video(*, user_description: bool = False) -> Material:
    return Material.register(
        material_id=VIDEO_ID,
        kind=MaterialKind.VIDEO,
        duration_ms=12_000,
        width=1920,
        height=1080,
        content_digest=VIDEO_DIGEST,
        has_audio=True,
        audio_loudness_lufs=-14.0,
        has_speech=False,
        speech_segments_ms=(),
        speech_transcript=None,
        shot_boundaries_ms=(0, 6_000),
        ai_description=("用户描述" if user_description else "原始理解"),
        ai_tags=(() if user_description else ("原始",)),
        description_source=(DescriptionSource.USER if user_description else DescriptionSource.AI),
        described_at=None if user_description else ORIGINAL_TIME,
    )


def _narration() -> Material:
    return Material.register(
        material_id=NARRATION_ID,
        kind=MaterialKind.AUDIO,
        duration_ms=1_200,
        width=None,
        height=None,
        content_digest=NARRATION_DIGEST,
        has_audio=True,
        audio_loudness_lufs=None,
        has_speech=True,
        speech_segments_ms=((0, 1_200),),
        speech_transcript="生成的旁白内容",
        shot_boundaries_ms=(),
        ai_description=None,
        ai_tags=(),
        description_source=DescriptionSource.AI,
        described_at=None,
    )


def _writeback() -> SmartEditMaterialWriteback:
    return SmartEditMaterialWriteback(
        analyses=(
            SmartEditMaterialAnalysisWriteback(
                material_id=VIDEO_ID,
                content_digest=VIDEO_DIGEST,
                has_speech=True,
                speech_segments_ms=((400, 2_000),),
                speech_transcript="更新后的原声内容",
                shot_boundaries_ms=(0, 4_000, 8_000),
                ai_description="更新后的理解",
                ai_tags=("更新",),
                description_source=DescriptionSource.AI,
                described_at=UPDATED_TIME,
            ),
        ),
        narrations=(_narration(),),
    )


async def _prepare(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(materials).where(materials.c.installation_id == OWNER.uuid))
        exists = await session.scalar(
            select(installations.c.id).where(installations.c.id == OWNER.uuid)
        )
        if exists is None:
            await session.execute(
                insert(installations).values(
                    id=OWNER.uuid,
                    device_public_key=secrets.token_bytes(32),
                )
            )


async def _cleanup(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(materials).where(materials.c.installation_id == OWNER.uuid))
        await session.execute(delete(installations).where(installations.c.id == OWNER.uuid))


def _http_writeback() -> dict[str, object]:
    return {
        "analyses": [
            {
                "materialId": str(VIDEO_ID),
                "contentDigest": VIDEO_DIGEST,
                "hasSpeech": True,
                "speechSegmentsMs": [[400, 2_000]],
                "speechTranscript": "更新后的原声内容",
                "shotBoundariesMs": [0, 4_000, 8_000],
                "aiDescription": "更新后的理解",
                "aiTags": ["更新"],
                "descriptionSource": "ai",
                "describedAt": "2026-08-01T04:05:06Z",
            }
        ],
        "narrations": [
            {
                "materialId": str(NARRATION_ID),
                "contentDigest": NARRATION_DIGEST,
                "durationMs": 1_200,
                "speechTranscript": "生成的旁白内容",
            }
        ],
    }


@pytest.mark.asyncio
async def test_real_postgres_writeback_is_atomic_and_exact_retry_is_idempotent(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyMaterialRepository(database)
    try:
        await _prepare(database)
        await repository.save(_video(), OWNER)

        first = await repository.apply_smart_edit_writeback(_writeback(), OWNER)
        retry = await repository.apply_smart_edit_writeback(_writeback(), OWNER)

        assert first == retry
        assert len(first) == 2
        stored_video = await repository.get(VIDEO_ID, OWNER)
        assert stored_video.ai_description == "更新后的理解"
        assert stored_video.speech_transcript == "更新后的原声内容"
        assert stored_video.shot_boundaries_ms == (0, 4_000, 8_000)
        assert await repository.get(NARRATION_ID, OWNER) == _narration()
        async with database.session() as session:
            count = await session.scalar(
                select(text("count(*)"))
                .select_from(materials)
                .where(materials.c.installation_id == OWNER.uuid)
            )
        assert count == 2
    finally:
        await _cleanup(database)
        await database.close()


@pytest.mark.asyncio
async def test_real_postgres_accepts_narration_only_after_analysis_is_current(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyMaterialRepository(database)
    writeback = SmartEditMaterialWriteback(analyses=(), narrations=(_narration(),))
    try:
        await _prepare(database)

        assert await repository.apply_smart_edit_writeback(writeback, OWNER) == (_narration(),)
        assert await repository.get(NARRATION_ID, OWNER) == _narration()
    finally:
        await _cleanup(database)
        await database.close()


@pytest.mark.asyncio
async def test_real_postgres_user_takeover_rejects_the_whole_writeback(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyMaterialRepository(database)
    original = _video(user_description=True)
    try:
        await _prepare(database)
        await repository.save(original, OWNER)

        with pytest.raises(MaterialDescriptionProtected):
            await repository.apply_smart_edit_writeback(_writeback(), OWNER)

        assert await repository.get(VIDEO_ID, OWNER) == original
        with pytest.raises(MaterialNotFound):
            await repository.get(NARRATION_ID, OWNER)
    finally:
        await _cleanup(database)
        await database.close()


@pytest.mark.asyncio
async def test_real_postgres_insert_failure_rolls_back_prior_analysis_update(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    repository = SqlAlchemyMaterialRepository(database)
    original = _video()
    trigger_name = "le19_reject_narration_insert"
    function_name = "le19_reject_narration_insert_fn"
    try:
        await _prepare(database)
        await repository.save(original, OWNER)
        async with database.session() as session:
            await session.execute(
                text(
                    f"create function {function_name}() returns trigger language plpgsql "
                    "as $$ begin "
                    f"if new.material_id = '{NARRATION_ID.uuid}'::uuid then "
                    "raise exception 'injected narration failure'; end if; "
                    "return new; end $$"
                )
            )
            await session.execute(
                text(
                    f"create trigger {trigger_name} before insert on materials "
                    f"for each row execute function {function_name}()"
                )
            )

        with pytest.raises(MaterialPersistenceUnavailable):
            await repository.apply_smart_edit_writeback(_writeback(), OWNER)

        assert await repository.get(VIDEO_ID, OWNER) == original
        with pytest.raises(MaterialNotFound):
            await repository.get(NARRATION_ID, OWNER)
    finally:
        async with database.session() as session:
            await session.execute(text(f"drop trigger if exists {trigger_name} on materials"))
            await session.execute(text(f"drop function if exists {function_name}()"))
        await _cleanup(database)
        await database.close()


def test_formal_http_api_persists_the_same_batch_on_real_postgres(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")

    async def seed() -> None:
        database = Database.from_url(postgresql_url)
        try:
            await _prepare(database)
            await SqlAlchemyMaterialRepository(database).save(_video(), OWNER)
        finally:
            await database.close()

    asyncio.run(seed())
    app_database = Database.from_url(postgresql_url)
    app = create_app(database=app_database)
    app.dependency_overrides[require_current_installation_access] = lambda: OWNER
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/editing-materials/smart-edit-writebacks",
            json=_http_writeback(),
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert [value["materialId"] for value in response.json()["materials"]] == [
        str(VIDEO_ID),
        str(NARRATION_ID),
    ]
    assert "relativePath" not in response.text

    async def verify_and_clean() -> None:
        database = Database.from_url(postgresql_url)
        repository = SqlAlchemyMaterialRepository(database)
        try:
            assert (await repository.get(VIDEO_ID, OWNER)).ai_description == "更新后的理解"
            assert await repository.get(NARRATION_ID, OWNER) == _narration()
            await _cleanup(database)
        finally:
            await database.close()

    asyncio.run(verify_and_clean())
