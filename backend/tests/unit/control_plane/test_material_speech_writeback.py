"""LE-14 T3: the application writes one supplier-neutral speech triplet."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from automation_tool.control_plane.application.materials import (
    MaterialNotFound,
    MaterialService,
)
from automation_tool.control_plane.domain import (
    DescriptionSource,
    InstallationId,
    Material,
    MaterialId,
    MaterialKind,
)

OWNER = InstallationId.parse("00000000-0000-4000-8000-000000000001")


def _material() -> Material:
    return Material.register(
        material_id=MaterialId.parse("00000000-0000-4000-8000-000000000002"),
        kind=MaterialKind.VIDEO,
        duration_ms=15_000,
        width=1920,
        height=1080,
        content_digest="a" * 64,
        has_audio=True,
        audio_loudness_lufs=-14.5,
        has_speech=False,
        speech_segments_ms=(),
        speech_transcript=None,
        shot_boundaries_ms=(0, 8_000),
        ai_description="原有素材理解",
        ai_tags=("户外",),
        description_source=DescriptionSource.AI,
        described_at=datetime(2026, 7, 31, tzinfo=UTC),
    )


class RecordingMaterialRepository:
    def __init__(self, material: Material) -> None:
        self.material: Material | None = material
        self.speech_writes: list[tuple[Material, InstallationId]] = []

    async def get(
        self,
        material_id: MaterialId,
        installation_id: InstallationId,
    ) -> Material:
        if (
            self.material is None
            or self.material.material_id != material_id
            or installation_id != OWNER
        ):
            raise MaterialNotFound
        return self.material

    async def update_speech_analysis(
        self,
        material: Material,
        installation_id: InstallationId,
    ) -> None:
        if self.material is None:
            raise MaterialNotFound
        self.speech_writes.append((material, installation_id))
        self.material = material


@pytest.mark.asyncio
async def test_service_writes_the_complete_triplet_once_and_returns_the_stored_row() -> None:
    original = _material()
    repository = RecordingMaterialRepository(original)
    service = MaterialService(repository=repository)  # type: ignore[arg-type]

    stored = await service.update_speech_analysis(
        installation_id=OWNER,
        material_id=str(original.material_id),
        has_speech=True,
        speech_segments_ms=((500, 3_000), (4_000, 9_000)),
        speech_transcript="第一句。\n第二句。",
    )

    assert len(repository.speech_writes) == 1
    written, owner = repository.speech_writes[0]
    assert owner == OWNER
    assert written == stored == repository.material
    assert written.has_speech is True
    assert written.speech_segments_ms == ((500, 3_000), (4_000, 9_000))
    assert written.speech_transcript == "第一句。\n第二句。"
    assert written.content_digest == original.content_digest
    assert written.shot_boundaries_ms == original.shot_boundaries_ms
    assert written.ai_description == original.ai_description


@pytest.mark.asyncio
async def test_service_does_not_turn_a_missing_write_target_into_success() -> None:
    original = _material()
    repository = RecordingMaterialRepository(original)
    service = MaterialService(repository=repository)  # type: ignore[arg-type]
    repository.material = None

    with pytest.raises(MaterialNotFound):
        await service.update_speech_analysis(
            installation_id=OWNER,
            material_id=str(original.material_id),
            has_speech=False,
            speech_segments_ms=(),
            speech_transcript=None,
        )

    assert repository.speech_writes == []
