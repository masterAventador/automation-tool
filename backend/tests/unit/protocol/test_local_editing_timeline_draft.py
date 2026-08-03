"""LE-16 T5: versioned, path-free final paragraph plan."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields
from typing import cast
from uuid import UUID, uuid4

import pytest

from automation_tool.protocol.local_editing import (
    LOCAL_EDITING_TIMELINE_DRAFT_VERSION,
    MAX_LOCAL_EDITING_MATERIAL_DURATION_MS,
    MAX_LOCAL_EDITING_TIMELINE_CAPTION_CHARACTERS,
    MAX_LOCAL_EDITING_TIMELINE_DURATION_MS,
    MIN_LOCAL_EDITING_TIMELINE_DURATION_MS,
    LocalEditingProtocolRejected,
    LocalEditingTimelineDraft,
    LocalEditingTimelineParagraph,
    LocalEditingTimelineParagraphKind,
)


def _original(
    *,
    sequence: int = 1,
    material_id: UUID | None = None,
    duration_ms: int = 100,
) -> LocalEditingTimelineParagraph:
    visual_id = material_id or uuid4()
    return LocalEditingTimelineParagraph(
        sequence=sequence,
        kind=LocalEditingTimelineParagraphKind.ORIGINAL_SPEECH,
        visual_material_id=visual_id,
        audio_material_id=visual_id,
        duration_ms=duration_ms,
        visual_source_in_ms=50,
        visual_source_out_ms=50 + duration_ms,
        caption_text=f"原声段 {sequence}",
    )


def _narrated(
    *,
    sequence: int = 1,
    visual_material_id: UUID | None = None,
    audio_material_id: UUID | None = None,
    duration_ms: int = 100,
    is_image: bool = False,
) -> LocalEditingTimelineParagraph:
    visual_id = visual_material_id or uuid4()
    audio_id = audio_material_id or uuid4()
    return LocalEditingTimelineParagraph(
        sequence=sequence,
        kind=LocalEditingTimelineParagraphKind.NARRATED,
        visual_material_id=visual_id,
        audio_material_id=audio_id,
        duration_ms=duration_ms,
        visual_source_in_ms=None if is_image else 20,
        visual_source_out_ms=None if is_image else 20 + duration_ms,
        caption_text=f"旁白段 {sequence}",
    )


def test_mixed_timeline_draft_is_versioned_ordered_and_path_free() -> None:
    draft = LocalEditingTimelineDraft(
        paragraphs=(
            _original(sequence=1, duration_ms=100),
            _narrated(sequence=2, duration_ms=200),
            _narrated(sequence=3, duration_ms=300, is_image=True),
        )
    )

    assert draft.version == LOCAL_EDITING_TIMELINE_DRAFT_VERSION
    assert draft.duration_ms == 600
    assert tuple(paragraph.sequence for paragraph in draft.paragraphs) == (1, 2, 3)
    assert draft.paragraphs[0].visual_source_in_ms == 50
    assert draft.paragraphs[2].visual_source_in_ms is None
    assert all("path" not in field.name for field in fields(LocalEditingTimelineParagraph))
    assert all("path" not in field.name for field in fields(LocalEditingTimelineDraft))


def test_timeline_draft_protocol_limits_match_its_versioned_surface() -> None:
    assert LOCAL_EDITING_TIMELINE_DRAFT_VERSION == "local-editing.timeline-draft.v1"
    assert MIN_LOCAL_EDITING_TIMELINE_DURATION_MS == 100
    assert MAX_LOCAL_EDITING_TIMELINE_DURATION_MS == 600_000
    assert MAX_LOCAL_EDITING_TIMELINE_CAPTION_CHARACTERS == 2_000


def test_paragraph_kinds_lock_audio_and_visual_identity_rules() -> None:
    visual_id = uuid4()
    different_audio_id = uuid4()
    constructors: tuple[Callable[[], object], ...] = (
        lambda: LocalEditingTimelineParagraph(
            sequence=0,
            kind=LocalEditingTimelineParagraphKind.NARRATED,
            visual_material_id=visual_id,
            audio_material_id=different_audio_id,
            duration_ms=100,
            visual_source_in_ms=0,
            visual_source_out_ms=100,
            caption_text="旁白",
        ),
        lambda: LocalEditingTimelineParagraph(
            sequence=1,
            kind=cast(LocalEditingTimelineParagraphKind, "narrated"),
            visual_material_id=visual_id,
            audio_material_id=different_audio_id,
            duration_ms=100,
            visual_source_in_ms=0,
            visual_source_out_ms=100,
            caption_text="旁白",
        ),
        lambda: LocalEditingTimelineParagraph(
            sequence=1,
            kind=LocalEditingTimelineParagraphKind.NARRATED,
            visual_material_id=UUID(int=0),
            audio_material_id=different_audio_id,
            duration_ms=100,
            visual_source_in_ms=0,
            visual_source_out_ms=100,
            caption_text="旁白",
        ),
        lambda: LocalEditingTimelineParagraph(
            sequence=1,
            kind=LocalEditingTimelineParagraphKind.NARRATED,
            visual_material_id=visual_id,
            audio_material_id=visual_id,
            duration_ms=100,
            visual_source_in_ms=0,
            visual_source_out_ms=100,
            caption_text="旁白",
        ),
        lambda: LocalEditingTimelineParagraph(
            sequence=1,
            kind=LocalEditingTimelineParagraphKind.ORIGINAL_SPEECH,
            visual_material_id=visual_id,
            audio_material_id=different_audio_id,
            duration_ms=100,
            visual_source_in_ms=0,
            visual_source_out_ms=100,
            caption_text="原声",
        ),
    )

    for construct in constructors:
        with pytest.raises(LocalEditingProtocolRejected):
            construct()


@pytest.mark.parametrize(
    "construct",
    [
        lambda: LocalEditingTimelineParagraph(
            sequence=1,
            kind=LocalEditingTimelineParagraphKind.ORIGINAL_SPEECH,
            visual_material_id=uuid4(),
            audio_material_id=uuid4(),
            duration_ms=100,
            visual_source_in_ms=None,
            visual_source_out_ms=None,
            caption_text="原声",
        ),
        lambda: LocalEditingTimelineParagraph(
            sequence=1,
            kind=LocalEditingTimelineParagraphKind.NARRATED,
            visual_material_id=uuid4(),
            audio_material_id=uuid4(),
            duration_ms=100,
            visual_source_in_ms=0,
            visual_source_out_ms=None,
            caption_text="旁白",
        ),
        lambda: LocalEditingTimelineParagraph(
            sequence=1,
            kind=LocalEditingTimelineParagraphKind.NARRATED,
            visual_material_id=uuid4(),
            audio_material_id=uuid4(),
            duration_ms=100,
            visual_source_in_ms=0,
            visual_source_out_ms=99,
            caption_text="旁白",
        ),
        lambda: LocalEditingTimelineParagraph(
            sequence=1,
            kind=LocalEditingTimelineParagraphKind.NARRATED,
            visual_material_id=uuid4(),
            audio_material_id=uuid4(),
            duration_ms=100,
            visual_source_in_ms=MAX_LOCAL_EDITING_MATERIAL_DURATION_MS - 50,
            visual_source_out_ms=MAX_LOCAL_EDITING_MATERIAL_DURATION_MS + 50,
            caption_text="旁白",
        ),
        lambda: LocalEditingTimelineParagraph(
            sequence=1,
            kind=LocalEditingTimelineParagraphKind.NARRATED,
            visual_material_id=uuid4(),
            audio_material_id=uuid4(),
            duration_ms=cast(int, True),
            visual_source_in_ms=None,
            visual_source_out_ms=None,
            caption_text="旁白",
        ),
        lambda: LocalEditingTimelineParagraph(
            sequence=1,
            kind=LocalEditingTimelineParagraphKind.NARRATED,
            visual_material_id=uuid4(),
            audio_material_id=uuid4(),
            duration_ms=100,
            visual_source_in_ms=None,
            visual_source_out_ms=None,
            caption_text="x" * (MAX_LOCAL_EDITING_TIMELINE_CAPTION_CHARACTERS + 1),
        ),
    ],
)
def test_paragraph_source_window_duration_and_caption_fail_closed(
    construct: Callable[[], object],
) -> None:
    with pytest.raises(LocalEditingProtocolRejected) as error:
        construct()

    assert str(error.value) == "local editing protocol value is invalid"
    assert error.value.__cause__ is None


def test_draft_rejects_sequence_identity_and_duration_inconsistency() -> None:
    shared_visual = uuid4()
    shared_audio = uuid4()
    constructors: tuple[Callable[[], object], ...] = (
        lambda: LocalEditingTimelineDraft(paragraphs=()),
        lambda: LocalEditingTimelineDraft(
            paragraphs=(_original(sequence=1), _narrated(sequence=3)),
        ),
        lambda: LocalEditingTimelineDraft(
            paragraphs=(
                _original(sequence=1, material_id=shared_visual),
                _narrated(sequence=2, visual_material_id=shared_visual),
            ),
        ),
        lambda: LocalEditingTimelineDraft(
            paragraphs=(
                _narrated(sequence=1, audio_material_id=shared_audio),
                _narrated(sequence=2, audio_material_id=shared_audio),
            ),
        ),
        lambda: LocalEditingTimelineDraft(
            paragraphs=(
                _original(sequence=1, material_id=shared_audio),
                _narrated(sequence=2, audio_material_id=shared_audio),
            ),
        ),
        lambda: LocalEditingTimelineDraft(
            paragraphs=(_narrated(duration_ms=MIN_LOCAL_EDITING_TIMELINE_DURATION_MS - 1),),
        ),
        lambda: LocalEditingTimelineDraft(
            paragraphs=(
                _narrated(sequence=1, duration_ms=300_001),
                _narrated(sequence=2, duration_ms=300_001),
            ),
        ),
        lambda: LocalEditingTimelineDraft(
            paragraphs=cast(tuple[LocalEditingTimelineParagraph, ...], [_narrated()]),
        ),
    )

    for construct in constructors:
        with pytest.raises(LocalEditingProtocolRejected):
            construct()


def test_draft_revalidates_mutated_nested_paragraphs() -> None:
    paragraph = _narrated()
    object.__setattr__(paragraph, "caption_text", "/Users/private/voice.wav\0")

    with pytest.raises(LocalEditingProtocolRejected) as error:
        LocalEditingTimelineDraft(paragraphs=(paragraph,))

    assert str(error.value) == "local editing protocol value is invalid"
    assert "private" not in str(error.value)
    assert error.value.__cause__ is None
