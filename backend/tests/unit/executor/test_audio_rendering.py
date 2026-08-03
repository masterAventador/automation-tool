"""LE-11 T2: deterministic path-free audio filter graph compilation."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest

from automation_tool.executor.audio_rendering import (
    AudioRenderBindingRejected,
    AudioRenderBindingRejection,
    AudioRenderCompilationRejected,
    AudioRenderSourceBinding,
    bind_audio_render_inputs,
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


def test_compiler_does_not_launder_a_mutated_outer_clip_list() -> None:
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
    object.__setattr__(
        plan,
        "clips",
        cast(tuple[LocalEditingAudioRenderClip, ...], list(plan.clips)),
    )

    with pytest.raises(AudioRenderCompilationRejected):
        compile_audio_filter_graph(plan, first_input_index=0)


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


def _source(
    directory: Path,
    material_id: UUID,
    name: str,
    *,
    has_audio: bool,
    create: bool = True,
) -> AudioRenderSourceBinding:
    path = directory / name
    if create:
        path.write_bytes(b"source")
    return AudioRenderSourceBinding(
        material_id=material_id,
        source_path=path,
        has_audio=has_audio,
    )


def test_binding_omits_silent_ambient_and_muted_sources_but_preserves_input_order(
    tmp_path: Path,
) -> None:
    narration_id = uuid4()
    silent_ambient_id = uuid4()
    fixed_id = uuid4()
    muted_id = uuid4()
    music_id = uuid4()
    plan = _plan(
        (
            _clip(
                1,
                LocalEditingAudioTrackKind.NARRATION,
                start_ms=0,
                duration_ms=500,
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
                material_id=silent_ambient_id,
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
    narration = _source(tmp_path, narration_id, "旁白.wav", has_audio=True)
    silent = _source(tmp_path, silent_ambient_id, "无声画面.mp4", has_audio=False)
    fixed = _source(tmp_path, fixed_id, "固定原声.wav", has_audio=True)
    muted = _source(
        tmp_path,
        muted_id,
        "不存在也不应触碰.wav",
        has_audio=False,
        create=False,
    )
    music = _source(tmp_path, music_id, "音乐.wav", has_audio=True)

    bound = bind_audio_render_inputs(
        plan,
        (music, muted, silent, narration, fixed),
        first_input_index=2,
    )

    assert bound.input_material_ids == (narration_id, fixed_id, music_id)
    assert bound.input_argv == (
        "-i",
        os.fspath(narration.source_path),
        "-i",
        os.fspath(fixed.source_path),
        "-i",
        os.fspath(music.source_path),
    )
    assert os.fspath(silent.source_path) not in bound.input_argv
    assert os.fspath(muted.source_path) not in bound.input_argv
    assert "[2:a]" in bound.filter_graph
    assert "[3:a]" in bound.filter_graph
    assert "[4:a]" in bound.filter_graph
    assert "[5:a]" not in bound.filter_graph
    assert bound.filter_graph.count("sidechaincompress=") == 1
    assert "private" not in repr(bound)


def test_silent_ambient_only_plan_compiles_to_no_audio_inputs(tmp_path: Path) -> None:
    material_id = uuid4()
    plan = _plan(
        (
            _clip(
                1,
                LocalEditingAudioTrackKind.AMBIENT,
                start_ms=0,
                duration_ms=1000,
                gain_db=-12.0,
                mode=LocalEditingOriginalAudioMode.AUTO_DUCK,
                material_id=material_id,
            ),
        )
    )

    bound = bind_audio_render_inputs(
        plan,
        (_source(tmp_path, material_id, "silent.mp4", has_audio=False),),
        first_input_index=1,
    )

    assert bound.input_material_ids == ()
    assert bound.input_argv == ()
    assert bound.filter_graph == ""
    assert bound.output_label is None


@pytest.mark.parametrize(
    "kind",
    [LocalEditingAudioTrackKind.NARRATION, LocalEditingAudioTrackKind.MUSIC],
)
def test_required_audio_lane_rejects_material_without_audio(
    tmp_path: Path,
    kind: LocalEditingAudioTrackKind,
) -> None:
    material_id = uuid4()
    source = _source(tmp_path, material_id, "private-user-file.wav", has_audio=False)
    plan = _plan(
        (
            _clip(
                1,
                kind,
                start_ms=0,
                duration_ms=1000,
                gain_db=0.0,
                material_id=material_id,
            ),
        )
    )

    with pytest.raises(AudioRenderBindingRejected) as error:
        bind_audio_render_inputs(plan, (source,), first_input_index=0)

    assert error.value.code is AudioRenderBindingRejection.SOURCE_HAS_NO_AUDIO
    assert error.value.material_id == material_id
    assert str(error.value) == "audio render binding rejected"
    assert "private-user-file" not in str(error.value)
    assert error.value.__cause__ is None


@pytest.mark.parametrize(
    "bindings",
    [
        lambda root, first, second: (),
        lambda root, first, second: (first,),
        lambda root, first, second: (
            first,
            second,
            _source(root, uuid4(), "extra.wav", has_audio=True),
        ),
        lambda root, first, second: (first, second, second),
        lambda root, first, second: cast(
            tuple[AudioRenderSourceBinding, ...],
            [first, second],
        ),
    ],
)
def test_audio_bindings_must_be_a_unique_exact_tuple(
    tmp_path: Path,
    bindings: Callable[
        [Path, AudioRenderSourceBinding, AudioRenderSourceBinding],
        tuple[AudioRenderSourceBinding, ...],
    ],
) -> None:
    first_id = uuid4()
    second_id = uuid4()
    plan = _plan(
        (
            _clip(
                1,
                LocalEditingAudioTrackKind.NARRATION,
                start_ms=0,
                duration_ms=1000,
                gain_db=0.0,
                material_id=first_id,
            ),
            _clip(
                2,
                LocalEditingAudioTrackKind.MUSIC,
                start_ms=0,
                duration_ms=1000,
                gain_db=-12.0,
                material_id=second_id,
            ),
        )
    )
    first = _source(tmp_path, first_id, "first.wav", has_audio=True)
    second = _source(tmp_path, second_id, "second.wav", has_audio=True)

    with pytest.raises(AudioRenderBindingRejected) as error:
        bind_audio_render_inputs(
            plan,
            bindings(tmp_path, first, second),
            first_input_index=0,
        )

    assert error.value.code is AudioRenderBindingRejection.INVALID_BINDINGS
    assert error.value.material_id is None


@pytest.mark.parametrize(
    "construct",
    [
        lambda root: AudioRenderSourceBinding(
            material_id=UUID(int=0),
            source_path=root / "audio.wav",
            has_audio=True,
        ),
        lambda root: AudioRenderSourceBinding(
            material_id=uuid4(),
            source_path=Path("relative.wav"),
            has_audio=True,
        ),
        lambda root: AudioRenderSourceBinding(
            material_id=uuid4(),
            source_path=root / "unsafe\u202ewav",
            has_audio=True,
        ),
        lambda root: AudioRenderSourceBinding(
            material_id=uuid4(),
            source_path=Path("/") / ("a" * 4096),
            has_audio=True,
        ),
        lambda root: AudioRenderSourceBinding(
            material_id=uuid4(),
            source_path=root / "audio.wav",
            has_audio=cast(bool, 1),
        ),
    ],
)
def test_audio_source_binding_shape_fails_closed(
    tmp_path: Path,
    construct: Callable[[Path], AudioRenderSourceBinding],
) -> None:
    with pytest.raises(AudioRenderBindingRejected) as error:
        construct(tmp_path)

    assert error.value.code is AudioRenderBindingRejection.INVALID_BINDINGS
    assert error.value.material_id is None


def test_binding_revalidates_mutated_plan_and_source_without_laundering(
    tmp_path: Path,
) -> None:
    material_id = uuid4()
    plan = _plan(
        (
            _clip(
                1,
                LocalEditingAudioTrackKind.NARRATION,
                start_ms=0,
                duration_ms=1000,
                gain_db=0.0,
                material_id=material_id,
            ),
        )
    )
    source = _source(tmp_path, material_id, "audio.wav", has_audio=True)
    object.__setattr__(source, "has_audio", 1)

    with pytest.raises(AudioRenderBindingRejected) as source_error:
        bind_audio_render_inputs(plan, (source,), first_input_index=0)
    assert source_error.value.code is AudioRenderBindingRejection.INVALID_BINDINGS

    object.__setattr__(
        plan,
        "clips",
        cast(tuple[LocalEditingAudioRenderClip, ...], list(plan.clips)),
    )
    with pytest.raises(AudioRenderBindingRejected) as plan_error:
        bind_audio_render_inputs(plan, (source,), first_input_index=0)
    assert plan_error.value.code is AudioRenderBindingRejection.INVALID_PLAN


@pytest.mark.parametrize(
    ("plan_value", "first_input_index"),
    [
        (object(), 0),
        (_plan(()), True),
        (_plan(()), -1),
    ],
)
def test_binding_rejects_invalid_plan_or_input_index(
    plan_value: object,
    first_input_index: object,
) -> None:
    with pytest.raises(AudioRenderBindingRejected) as error:
        bind_audio_render_inputs(
            cast(LocalEditingAudioRenderPlan, plan_value),
            (),
            first_input_index=cast(int, first_input_index),
        )

    assert error.value.code is AudioRenderBindingRejection.INVALID_PLAN


def test_audio_binding_repr_is_path_free(tmp_path: Path) -> None:
    binding = _source(tmp_path, uuid4(), "private-name.wav", has_audio=True)

    assert repr(binding) == "AudioRenderSourceBinding(<redacted>)"
    assert "private-name" not in repr(binding)
