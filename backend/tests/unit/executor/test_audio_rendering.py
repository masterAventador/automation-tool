"""LE-11 T2: deterministic path-free audio filter graph compilation."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from automation_tool.executor.audio_rendering import (
    AudioRenderCompilationRejected,
    compile_audio_filter_graph,
)
from automation_tool.protocol.local_rendering import (
    LocalEditingAudioRenderClip,
    LocalEditingAudioRenderPlan,
    LocalEditingAudioTrackKind,
    LocalEditingOriginalAudioMode,
)


def _clip(
    sequence: int,
    kind: LocalEditingAudioTrackKind,
    *,
    start_ms: int,
    duration_ms: int,
    gain_db: float,
    mode: LocalEditingOriginalAudioMode | None = None,
    material_id: UUID | None = None,
) -> LocalEditingAudioRenderClip:
    return LocalEditingAudioRenderClip(
        sequence=sequence,
        track_kind=kind,
        material_id=material_id or uuid4(),
        start_ms=start_ms,
        duration_ms=duration_ms,
        source_in_ms=100,
        source_out_ms=100 + duration_ms,
        gain_db=gain_db,
        original_audio_mode=mode,
    )


def _plan(clips: tuple[LocalEditingAudioRenderClip, ...]) -> LocalEditingAudioRenderPlan:
    return LocalEditingAudioRenderPlan(
        project_id=uuid4(),
        timeline_id=uuid4(),
        timeline_revision=1,
        duration_ms=1000,
        clips=clips,
    )


def test_compiler_routes_auto_music_fixed_muted_and_narration_deterministically() -> None:
    narration_id = uuid4()
    auto_id = uuid4()
    fixed_id = uuid4()
    muted_id = uuid4()
    music_id = uuid4()
    plan = _plan(
        (
            _clip(
                1,
                LocalEditingAudioTrackKind.NARRATION,
                start_ms=200,
                duration_ms=300,
                gain_db=0.0,
                material_id=narration_id,
            ),
            _clip(
                2,
                LocalEditingAudioTrackKind.AMBIENT,
                start_ms=0,
                duration_ms=200,
                gain_db=-12.0,
                mode=LocalEditingOriginalAudioMode.AUTO_DUCK,
                material_id=auto_id,
            ),
            _clip(
                3,
                LocalEditingAudioTrackKind.AMBIENT,
                start_ms=200,
                duration_ms=200,
                gain_db=-9.0,
                mode=LocalEditingOriginalAudioMode.FIXED_VOLUME,
                material_id=fixed_id,
            ),
            _clip(
                4,
                LocalEditingAudioTrackKind.AMBIENT,
                start_ms=400,
                duration_ms=200,
                gain_db=-6.0,
                mode=LocalEditingOriginalAudioMode.MUTED,
                material_id=muted_id,
            ),
            _clip(
                5,
                LocalEditingAudioTrackKind.MUSIC,
                start_ms=0,
                duration_ms=1000,
                gain_db=-24.0,
                material_id=music_id,
            ),
        )
    )

    compiled = compile_audio_filter_graph(plan, first_input_index=3)

    assert compiled.input_material_ids == (narration_id, auto_id, fixed_id, music_id)
    assert muted_id not in compiled.input_material_ids
    assert compiled.output_label == "audio_out"
    assert "[3:a]atrim=start=0.100000:end=0.400000" in compiled.filter_graph
    assert "volume=0.000000dB" in compiled.filter_graph
    assert "aresample=48000" in compiled.filter_graph
    assert "aformat=sample_fmts=fltp:channel_layouts=stereo" in compiled.filter_graph
    assert "adelay=delays=200:all=1" in compiled.filter_graph
    assert compiled.filter_graph.count("sidechaincompress=") == 1
    assert "release=350" in compiled.filter_graph
    assert "asplit=2[narration_mix][narration_sidechain]" in compiled.filter_graph
    assert "[ambient_fixed]" in compiled.filter_graph
    assert "[audio_out]" in compiled.filter_graph
    assert "/Users/" not in compiled.filter_graph
    assert "private" not in repr(compiled)


def test_without_narration_keeps_the_duckable_bed_at_baseline() -> None:
    plan = _plan(
        (
            _clip(
                1,
                LocalEditingAudioTrackKind.AMBIENT,
                start_ms=0,
                duration_ms=1000,
                gain_db=-12.0,
                mode=LocalEditingOriginalAudioMode.AUTO_DUCK,
            ),
        )
    )

    compiled = compile_audio_filter_graph(plan, first_input_index=0)

    assert "sidechaincompress" not in compiled.filter_graph
    assert compiled.output_label == "audio_out"


def test_narration_without_a_bed_is_mixed_without_sidechain() -> None:
    plan = _plan(
        (
            _clip(
                1,
                LocalEditingAudioTrackKind.NARRATION,
                start_ms=0,
                duration_ms=1000,
                gain_db=0.0,
            ),
        )
    )

    compiled = compile_audio_filter_graph(plan, first_input_index=0)

    assert "sidechaincompress" not in compiled.filter_graph
    assert "[narration]" in compiled.filter_graph
    assert compiled.output_label == "audio_out"


def test_compiler_revalidates_a_mutated_nested_plan() -> None:
    clip = _clip(
        1,
        LocalEditingAudioTrackKind.NARRATION,
        start_ms=0,
        duration_ms=1000,
        gain_db=0.0,
    )
    plan = _plan((clip,))
    object.__setattr__(plan.clips[0], "gain_db", "/Users/private/audio.wav")

    with pytest.raises(AudioRenderCompilationRejected) as error:
        compile_audio_filter_graph(plan, first_input_index=0)

    assert str(error.value) == "audio render compilation rejected"
    assert "private" not in str(error.value)
    assert error.value.__cause__ is None


def test_empty_or_all_muted_plan_has_no_inputs_and_no_output_label() -> None:
    empty = compile_audio_filter_graph(_plan(()), first_input_index=0)
    muted = compile_audio_filter_graph(
        _plan(
            (
                _clip(
                    1,
                    LocalEditingAudioTrackKind.AMBIENT,
                    start_ms=0,
                    duration_ms=1000,
                    gain_db=-12.0,
                    mode=LocalEditingOriginalAudioMode.MUTED,
                ),
            )
        ),
        first_input_index=0,
    )

    for compiled in (empty, muted):
        assert compiled.input_material_ids == ()
        assert compiled.filter_graph == ""
        assert compiled.output_label is None


@pytest.mark.parametrize("first_input_index", [-1, True, 1.0])
def test_first_input_index_is_a_non_negative_real_integer(
    first_input_index: object,
) -> None:
    with pytest.raises(AudioRenderCompilationRejected):
        compile_audio_filter_graph(_plan(()), first_input_index=first_input_index)  # type: ignore[arg-type]
