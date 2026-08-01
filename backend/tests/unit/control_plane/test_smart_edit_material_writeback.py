"""LE-19 T3: one atomic path-free smart-edit material writeback."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from automation_tool.control_plane.application.materials import (
    InvalidMaterialQuery,
    MaterialService,
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

OWNER = InstallationId.parse("00000000-0000-4000-8000-000000000010")


def _analysis() -> SmartEditMaterialAnalysisWriteback:
    return SmartEditMaterialAnalysisWriteback(
        material_id=MaterialId.parse("00000000-0000-4000-8000-000000000001"),
        content_digest="a" * 64,
        has_speech=True,
        speech_segments_ms=((200, 900),),
        speech_transcript="原声内容",
        shot_boundaries_ms=(0, 1_000),
        ai_description="产品特写",
        ai_tags=("产品",),
        description_source=DescriptionSource.AI,
        described_at=datetime(2026, 8, 1, tzinfo=UTC),
    )


def _narration() -> Material:
    return Material.register(
        material_id=MaterialId.parse("00000000-0000-4000-8000-000000000002"),
        kind=MaterialKind.AUDIO,
        duration_ms=800,
        width=None,
        height=None,
        content_digest="b" * 64,
        has_audio=True,
        audio_loudness_lufs=None,
        has_speech=True,
        speech_segments_ms=((0, 800),),
        speech_transcript="旁白内容",
        shot_boundaries_ms=(),
        ai_description=None,
        ai_tags=(),
        description_source=DescriptionSource.AI,
        described_at=None,
    )


def test_writeback_command_can_hold_only_analysis_facts_and_narration_materials() -> None:
    analysis = _analysis()
    narration = _narration()
    writeback = SmartEditMaterialWriteback(
        analyses=(analysis,),
        narrations=(narration,),
    )

    assert writeback.analyses == (analysis,)
    assert writeback.narrations == (narration,)
    assert "path" not in repr(writeback).lower()
    assert "产品特写" not in repr(analysis)
    assert "旁白内容" not in repr(writeback)


def test_writeback_command_accepts_narration_only_when_analysis_is_already_current() -> None:
    narration = _narration()

    writeback = SmartEditMaterialWriteback(analyses=(), narrations=(narration,))

    assert writeback.analyses == ()
    assert writeback.narrations == (narration,)


@pytest.mark.parametrize(
    "analyses,narrations",
    [
        ((), ()),
        ((_analysis(), _analysis()), (_narration(),)),
        ((_analysis(),), (_narration(), _narration())),
    ],
)
def test_writeback_command_rejects_empty_or_duplicate_batches(
    analyses: tuple[SmartEditMaterialAnalysisWriteback, ...],
    narrations: tuple[Material, ...],
) -> None:
    with pytest.raises(InvalidMaterialQuery):
        SmartEditMaterialWriteback(analyses=analyses, narrations=narrations)


class RecordingWritebackRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[SmartEditMaterialWriteback, InstallationId]] = []

    async def apply_smart_edit_writeback(
        self,
        writeback: SmartEditMaterialWriteback,
        installation_id: InstallationId,
    ) -> tuple[Material, ...]:
        self.calls.append((writeback, installation_id))
        return writeback.narrations


@pytest.mark.asyncio
async def test_service_delegates_the_whole_batch_to_one_repository_transaction() -> None:
    repository = RecordingWritebackRepository()
    service = MaterialService(repository=repository)  # type: ignore[arg-type]
    writeback = SmartEditMaterialWriteback(
        analyses=(_analysis(),),
        narrations=(_narration(),),
    )

    stored = await service.apply_smart_edit_writeback(
        installation_id=OWNER,
        writeback=writeback,
    )

    assert repository.calls == [(writeback, OWNER)]
    assert stored == writeback.narrations


@pytest.mark.asyncio
async def test_service_rejects_foreign_writeback_types_before_the_repository() -> None:
    repository = RecordingWritebackRepository()
    service = MaterialService(repository=repository)  # type: ignore[arg-type]

    with pytest.raises(InvalidMaterialQuery):
        await service.apply_smart_edit_writeback(
            installation_id=OWNER,
            writeback=object(),  # type: ignore[arg-type]
        )

    assert repository.calls == []
