"""LE-16 T5: project resolved Executor paragraphs onto the shared wire."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest

from automation_tool.executor.segment_selection import FittingMaterialSegment
from automation_tool.executor.speech_paragraph_draft import (
    NarrationMaterialBinding,
    OriginalSpeechParagraphDraft,
    ResolvedSpeechAwareParagraphDraft,
    SelectedNarratedParagraphDraft,
    SpeechParagraphDraftRejected,
    project_local_editing_timeline_draft,
)
from automation_tool.protocol.local_editing import LocalEditingTimelineParagraphKind


def _original(
    material_id: UUID,
    *,
    duration_ms: int = 500,
    caption_text: str = "完整原声",
) -> OriginalSpeechParagraphDraft:
    return OriginalSpeechParagraphDraft(
        material_id=material_id,
        source_in_ms=100,
        source_out_ms=100 + duration_ms,
        visual_start_ms=0,
        visual_duration_ms=duration_ms,
        ambient_start_ms=0,
        ambient_duration_ms=duration_ms,
        caption_start_ms=0,
        caption_duration_ms=duration_ms,
        caption_text=caption_text,
    )


def _selected(
    sequence: int,
    material_id: UUID,
    *,
    duration_ms: int = 500,
    is_image: bool = False,
    caption_text: str | None = None,
) -> SelectedNarratedParagraphDraft:
    return SelectedNarratedParagraphDraft(
        sequence=sequence,
        caption_text=caption_text or f"第{sequence}句",
        narration_relative_path=f"voiceover/sentence-{sequence:04d}.wav",
        duration_ms=duration_ms,
        segment=FittingMaterialSegment(
            material_id=material_id,
            score=90,
            duration_ms=duration_ms,
            source_in_ms=None if is_image else 200,
            source_out_ms=None if is_image else 200 + duration_ms,
        ),
    )


def _binding(sequence: int, material_id: UUID | None = None) -> NarrationMaterialBinding:
    return NarrationMaterialBinding(
        sequence=sequence,
        narration_relative_path=f"voiceover/sentence-{sequence:04d}.wav",
        material_id=material_id or uuid4(),
    )


def test_mixed_resolved_draft_projects_originals_then_narrated_without_paths() -> None:
    voiced = uuid4()
    video = uuid4()
    image = uuid4()
    first_voiceover = uuid4()
    second_voiceover = uuid4()
    resolved = ResolvedSpeechAwareParagraphDraft(
        original_speech_paragraphs=(_original(voiced),),
        narrated_paragraphs=(
            _selected(1, video, duration_ms=600),
            _selected(2, image, duration_ms=700, is_image=True),
        ),
    )

    result = project_local_editing_timeline_draft(
        resolved,
        narration_materials=(
            _binding(1, first_voiceover),
            _binding(2, second_voiceover),
        ),
    )

    assert tuple(paragraph.sequence for paragraph in result.paragraphs) == (1, 2, 3)
    assert tuple(paragraph.kind for paragraph in result.paragraphs) == (
        LocalEditingTimelineParagraphKind.ORIGINAL_SPEECH,
        LocalEditingTimelineParagraphKind.NARRATED,
        LocalEditingTimelineParagraphKind.NARRATED,
    )
    assert result.paragraphs[0].audio_material_id == voiced
    assert result.paragraphs[1].visual_material_id == video
    assert result.paragraphs[1].audio_material_id == first_voiceover
    assert (result.paragraphs[1].visual_source_in_ms, result.paragraphs[1].duration_ms) == (
        200,
        600,
    )
    assert result.paragraphs[2].visual_material_id == image
    assert result.paragraphs[2].audio_material_id == second_voiceover
    assert result.paragraphs[2].visual_source_in_ms is None
    assert "voiceover/" not in repr(result)


def test_all_original_speech_needs_no_narration_material_binding() -> None:
    first = uuid4()
    second = uuid4()
    resolved = ResolvedSpeechAwareParagraphDraft(
        original_speech_paragraphs=(_original(first), _original(second)),
        narrated_paragraphs=(),
    )

    result = project_local_editing_timeline_draft(resolved, narration_materials=())

    assert tuple(paragraph.visual_material_id for paragraph in result.paragraphs) == (
        first,
        second,
    )
    assert all(
        paragraph.kind is LocalEditingTimelineParagraphKind.ORIGINAL_SPEECH
        for paragraph in result.paragraphs
    )


def test_narration_binding_is_canonical_local_metadata() -> None:
    binding = _binding(1)

    assert binding.narration_relative_path == "voiceover/sentence-0001.wav"
    assert "/Users" not in repr(binding)


def test_projection_rejects_missing_extra_or_wrong_binding_order() -> None:
    first_visual = uuid4()
    second_visual = uuid4()
    resolved = ResolvedSpeechAwareParagraphDraft(
        original_speech_paragraphs=(),
        narrated_paragraphs=(_selected(1, first_visual), _selected(2, second_visual)),
    )
    constructors: tuple[Callable[[], object], ...] = (
        lambda: project_local_editing_timeline_draft(
            resolved,
            narration_materials=(_binding(1),),
        ),
        lambda: project_local_editing_timeline_draft(
            resolved,
            narration_materials=(_binding(1), _binding(2), _binding(3)),
        ),
        lambda: project_local_editing_timeline_draft(
            resolved,
            narration_materials=(_binding(2), _binding(1)),
        ),
        lambda: project_local_editing_timeline_draft(
            resolved,
            narration_materials=cast(tuple[NarrationMaterialBinding, ...], [_binding(1)]),
        ),
    )

    for construct in constructors:
        with pytest.raises(SpeechParagraphDraftRejected):
            construct()


def test_projection_rejects_audio_identity_collision_and_mutated_bindings() -> None:
    first_visual = uuid4()
    second_visual = uuid4()
    resolved = ResolvedSpeechAwareParagraphDraft(
        original_speech_paragraphs=(),
        narrated_paragraphs=(_selected(1, first_visual), _selected(2, second_visual)),
    )
    shared_audio = uuid4()
    mutated = _binding(1)
    object.__setattr__(mutated, "material_id", UUID(int=0))
    constructors: tuple[Callable[[], object], ...] = (
        lambda: project_local_editing_timeline_draft(
            resolved,
            narration_materials=(_binding(1, first_visual), _binding(2)),
        ),
        lambda: project_local_editing_timeline_draft(
            resolved,
            narration_materials=(_binding(1, shared_audio), _binding(2, shared_audio)),
        ),
        lambda: project_local_editing_timeline_draft(
            resolved,
            narration_materials=(mutated, _binding(2)),
        ),
    )

    for construct in constructors:
        with pytest.raises(SpeechParagraphDraftRejected) as error:
            construct()
        assert str(error.value) == "speech paragraph draft rejected"
        assert error.value.__cause__ is None


def test_projection_keeps_domain_caption_limit_instead_of_bypassing_it() -> None:
    too_long = "字" * 2_001
    resolved = ResolvedSpeechAwareParagraphDraft(
        original_speech_paragraphs=(_original(uuid4(), caption_text=too_long),),
        narrated_paragraphs=(),
    )

    with pytest.raises(SpeechParagraphDraftRejected):
        project_local_editing_timeline_draft(resolved, narration_materials=())


def test_projection_revalidates_resolved_values_at_its_public_boundary() -> None:
    original = _original(uuid4())
    resolved = ResolvedSpeechAwareParagraphDraft(
        original_speech_paragraphs=(original,),
        narrated_paragraphs=(),
    )
    object.__setattr__(original, "source_in_ms", "/Users/private/movie.mp4")

    with pytest.raises(SpeechParagraphDraftRejected) as error:
        project_local_editing_timeline_draft(resolved, narration_materials=())

    assert "private" not in str(error.value)
    assert error.value.__cause__ is None


def test_executor_projection_keeps_control_plane_implementation_out_of_import_graph() -> None:
    source = (
        Path(__file__).parents[3]
        / "src"
        / "automation_tool"
        / "executor"
        / "speech_paragraph_draft.py"
    ).read_text(encoding="utf-8")

    assert "automation_tool.control_plane" not in source
