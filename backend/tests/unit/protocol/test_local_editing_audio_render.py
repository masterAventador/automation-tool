"""LE-11 T2: path-free audio render values shared across processes."""

from __future__ import annotations

from dataclasses import fields
from typing import cast
from uuid import uuid4

import pytest

from automation_tool.protocol.local_rendering import (
    LOCAL_EDITING_AUDIO_RENDER_VERSION,
    MAX_LOCAL_EDITING_AUDIO_CLIPS_PER_TRACK,
    MAX_LOCAL_EDITING_AUDIO_RENDER_CLIPS,
    LocalEditingAudioRenderClip,
    LocalEditingAudioRenderPlan,
    LocalEditingAudioRenderRejected,
    LocalEditingAudioTrackKind,
    LocalEditingOriginalAudioMode,
)


def _clip(
    sequence: int,
    track_kind: LocalEditingAudioTrackKind,
    *,
    start_ms: int = 0,
    duration_ms: int = 500,
    mode: LocalEditingOriginalAudioMode | None = None,
) -> LocalEditingAudioRenderClip:
    return LocalEditingAudioRenderClip(
        sequence=sequence,
        track_kind=track_kind,
        material_id=uuid4(),
        start_ms=start_ms,
        duration_ms=duration_ms,
        source_in_ms=100,
        source_out_ms=100 + duration_ms,
        gain_db=-12.0,
        original_audio_mode=mode,
    )


def _plan(
    clips: tuple[LocalEditingAudioRenderClip, ...],
) -> LocalEditingAudioRenderPlan:
    return LocalEditingAudioRenderPlan(
        project_id=uuid4(),
        timeline_id=uuid4(),
        timeline_revision=3,
        duration_ms=1000,
        clips=clips,
    )


def test_audio_plan_is_versioned_path_free_and_allows_no_audio() -> None:
    plan = _plan(())

    assert plan.version == LOCAL_EDITING_AUDIO_RENDER_VERSION
    assert plan.clips == ()
    assert repr(plan) == "LocalEditingAudioRenderPlan(<redacted>)"
    names = {
        field.name
        for model in (LocalEditingAudioRenderClip, LocalEditingAudioRenderPlan)
        for field in fields(model)
    }
    assert not any(
        token in name for name in names for token in ("path", "argv", "codec", "has_audio")
    )


def test_three_tracks_and_ambient_modes_are_exact_closed_enums() -> None:
    assert {kind.value for kind in LocalEditingAudioTrackKind} == {
        "narration",
        "ambient",
        "music",
    }


def test_audio_clip_repr_is_redacted_and_outer_container_is_strict() -> None:
    clip = _clip(1, LocalEditingAudioTrackKind.NARRATION)
    assert repr(clip) == "LocalEditingAudioRenderClip(<redacted>)"
    with pytest.raises(LocalEditingAudioRenderRejected):
        LocalEditingAudioRenderPlan(
            project_id=uuid4(),
            timeline_id=uuid4(),
            timeline_revision=1,
            duration_ms=1000,
            clips=cast(tuple[LocalEditingAudioRenderClip, ...], [clip]),
        )
    assert {mode.value for mode in LocalEditingOriginalAudioMode} == {
        "auto_duck",
        "fixed_volume",
        "muted",
    }


@pytest.mark.parametrize("mode", list(LocalEditingOriginalAudioMode))
def test_only_ambient_requires_one_strongly_typed_mode(
    mode: LocalEditingOriginalAudioMode,
) -> None:
    assert _clip(1, LocalEditingAudioTrackKind.AMBIENT, mode=mode).original_audio_mode is mode
    with pytest.raises(LocalEditingAudioRenderRejected):
        _clip(
            1,
            LocalEditingAudioTrackKind.AMBIENT,
            mode=cast(LocalEditingOriginalAudioMode, mode.value),
        )
    for kind in (LocalEditingAudioTrackKind.NARRATION, LocalEditingAudioTrackKind.MUSIC):
        with pytest.raises(LocalEditingAudioRenderRejected):
            _clip(1, kind, mode=mode)


def test_plan_revalidates_sequence_track_order_and_per_track_layout() -> None:
    narration = _clip(1, LocalEditingAudioTrackKind.NARRATION)
    ambient = _clip(
        2,
        LocalEditingAudioTrackKind.AMBIENT,
        mode=LocalEditingOriginalAudioMode.AUTO_DUCK,
    )
    music = _clip(3, LocalEditingAudioTrackKind.MUSIC)
    assert len(_plan((narration, ambient, music)).clips) == 3

    for clips in (
        (ambient, narration),
        (_clip(2, LocalEditingAudioTrackKind.NARRATION),),
        (
            narration,
            _clip(2, LocalEditingAudioTrackKind.NARRATION, start_ms=400),
        ),
    ):
        with pytest.raises(LocalEditingAudioRenderRejected):
            _plan(clips)


def test_plan_rejects_mutated_nested_values_without_leaking_them() -> None:
    clip = _clip(1, LocalEditingAudioTrackKind.NARRATION)
    object.__setattr__(clip, "source_in_ms", "/Users/private/voice.wav")

    with pytest.raises(LocalEditingAudioRenderRejected) as error:
        _plan((clip,))

    assert str(error.value) == "local audio render plan rejected"
    assert "private" not in str(error.value)
    assert error.value.__cause__ is None


def test_audio_plan_total_clip_limit_matches_three_full_domain_tracks() -> None:
    assert MAX_LOCAL_EDITING_AUDIO_CLIPS_PER_TRACK == 512
    assert MAX_LOCAL_EDITING_AUDIO_RENDER_CLIPS == 1536

    narration = tuple(
        _clip(
            sequence,
            LocalEditingAudioTrackKind.NARRATION,
            start_ms=sequence - 1,
            duration_ms=1,
        )
        for sequence in range(1, 513)
    )
    assert len(_plan(narration).clips) == 512
    extra = _clip(
        513,
        LocalEditingAudioTrackKind.NARRATION,
        start_ms=512,
        duration_ms=1,
    )
    with pytest.raises(LocalEditingAudioRenderRejected):
        _plan((*narration, extra))
