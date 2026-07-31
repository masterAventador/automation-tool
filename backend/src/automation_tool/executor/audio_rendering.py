"""Compile a path-free local audio plan into one deterministic FFmpeg graph."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Never, cast
from uuid import RFC_4122, UUID

from automation_tool.executor.material_probe import MAX_PATH_CHARACTERS
from automation_tool.protocol.local_rendering import (
    LocalEditingAudioRenderPlan,
    LocalEditingAudioTrackKind,
    LocalEditingOriginalAudioMode,
)
from automation_tool.protocol.safe_text import contains_control_or_bidi

AUDIO_SAMPLE_RATE = 48_000
AUDIO_DUCK_THRESHOLD = "0.05"
AUDIO_DUCK_RATIO = 8
AUDIO_DUCK_ATTACK_MS = 20
AUDIO_DUCK_RELEASE_MS = 350


class AudioRenderCompilationRejected(ValueError):
    """The audio wire value cannot form a bounded filter graph."""

    def __init__(self) -> None:
        super().__init__("audio render compilation rejected")


def _reject() -> Never:
    raise AudioRenderCompilationRejected from None


class AudioRenderBindingRejection(StrEnum):
    INVALID_PLAN = "invalid_plan"
    INVALID_BINDINGS = "invalid_bindings"
    SOURCE_HAS_NO_AUDIO = "source_has_no_audio"


class AudioRenderBindingRejected(ValueError):
    """Local material facts cannot safely satisfy one audio plan."""

    def __init__(
        self,
        code: AudioRenderBindingRejection,
        *,
        material_id: UUID | None = None,
    ) -> None:
        self.code = code
        self.material_id = material_id
        super().__init__("audio render binding rejected")


def _reject_binding(
    code: AudioRenderBindingRejection,
    *,
    material_id: UUID | None = None,
) -> Never:
    raise AudioRenderBindingRejected(code, material_id=material_id) from None


def _is_uuid4(value: object) -> bool:
    return (
        isinstance(value, UUID)
        and value.variant == RFC_4122
        and value.version == 4
        and value.int != 0
    )


def _valid_local_path(value: object) -> bool:
    if not isinstance(value, Path) or not value.is_absolute():
        return False
    text = os.fspath(value)
    return 1 <= len(text) <= MAX_PATH_CHARACTERS and not contains_control_or_bidi(text)


@dataclass(frozen=True, slots=True, repr=False)
class AudioRenderSourceBinding:
    """One Executor-local material path and its already-probed audio fact."""

    material_id: UUID
    source_path: Path = field(repr=False)
    has_audio: bool

    def __post_init__(self) -> None:
        if (
            not _is_uuid4(self.material_id)
            or not _valid_local_path(self.source_path)
            or type(self.has_audio) is not bool
        ):
            _reject_binding(AudioRenderBindingRejection.INVALID_BINDINGS)

    def __repr__(self) -> str:
        return "AudioRenderSourceBinding(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class CompiledAudioFilterGraph:
    input_material_ids: tuple[UUID, ...]
    filter_graph: str
    output_label: str | None

    def __repr__(self) -> str:
        return "CompiledAudioFilterGraph(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class BoundAudioRenderInputs:
    """Local input argv paired with the still path-free compiled filter graph."""

    input_argv: tuple[str, ...] = field(repr=False)
    input_material_ids: tuple[UUID, ...]
    filter_graph: str
    output_label: str | None

    def __repr__(self) -> str:
        return "BoundAudioRenderInputs(<redacted>)"


def _seconds(milliseconds: int) -> str:
    return f"{milliseconds / 1000:.6f}"


def _mix(parts: list[str], labels: list[str], output: str) -> str | None:
    if not labels:
        return None
    inputs = "".join(f"[{label}]" for label in labels)
    if len(labels) == 1:
        parts.append(f"{inputs}anull[{output}]")
    else:
        parts.append(
            f"{inputs}amix=inputs={len(labels)}:duration=longest:"
            f"dropout_transition=0:normalize=0[{output}]"
        )
    return output


def _validated_plan(plan: LocalEditingAudioRenderPlan) -> LocalEditingAudioRenderPlan:
    try:
        return LocalEditingAudioRenderPlan(
            project_id=plan.project_id,
            timeline_id=plan.timeline_id,
            timeline_revision=plan.timeline_revision,
            duration_ms=plan.duration_ms,
            clips=plan.clips,
        )
    except Exception:
        _reject()


def _compile_validated_audio_filter_graph(
    validated: LocalEditingAudioRenderPlan,
    *,
    first_input_index: int,
    excluded_sequences: frozenset[int],
) -> CompiledAudioFilterGraph:
    parts: list[str] = []
    material_ids: list[UUID] = []
    narration_labels: list[str] = []
    duckable_labels: list[str] = []
    fixed_labels: list[str] = []
    total_seconds = _seconds(validated.duration_ms)

    for clip in validated.clips:
        if (
            clip.sequence in excluded_sequences
            or clip.original_audio_mode is LocalEditingOriginalAudioMode.MUTED
        ):
            continue
        input_index = first_input_index + len(material_ids)
        material_ids.append(clip.material_id)
        label = f"audio_clip_{clip.sequence}"
        parts.append(
            f"[{input_index}:a]atrim=start={_seconds(clip.source_in_ms)}:"
            f"end={_seconds(clip.source_out_ms)},asetpts=PTS-STARTPTS,"
            f"volume={clip.gain_db:.6f}dB,aresample={AUDIO_SAMPLE_RATE},"
            "aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"adelay=delays={clip.start_ms}:all=1,apad=whole_dur={total_seconds},"
            f"atrim=end={total_seconds}[{label}]"
        )
        if clip.track_kind is LocalEditingAudioTrackKind.NARRATION:
            narration_labels.append(label)
        elif (
            clip.track_kind is LocalEditingAudioTrackKind.MUSIC
            or clip.original_audio_mode is LocalEditingOriginalAudioMode.AUTO_DUCK
        ):
            duckable_labels.append(label)
        else:
            fixed_labels.append(label)

    if not material_ids:
        return CompiledAudioFilterGraph((), "", None)

    narration = _mix(parts, narration_labels, "narration")
    duckable = _mix(parts, duckable_labels, "duck_bed")
    fixed = _mix(parts, fixed_labels, "ambient_fixed")
    final_labels: list[str] = []
    if narration is not None and duckable is not None:
        parts.append(
            f"[{narration}]asplit=2[narration_mix][narration_sidechain]"
        )
        parts.append(
            f"[{duckable}][narration_sidechain]sidechaincompress="
            f"threshold={AUDIO_DUCK_THRESHOLD}:ratio={AUDIO_DUCK_RATIO}:"
            f"attack={AUDIO_DUCK_ATTACK_MS}:release={AUDIO_DUCK_RELEASE_MS}[ducked]"
        )
        final_labels.extend(("narration_mix", "ducked"))
    else:
        if narration is not None:
            final_labels.append(narration)
        if duckable is not None:
            final_labels.append(duckable)
    if fixed is not None:
        final_labels.append(fixed)

    final_mix = cast(str, _mix(parts, final_labels, "audio_mix"))
    parts.append(
        f"[{final_mix}]apad=whole_dur={total_seconds},"
        f"atrim=end={total_seconds}[audio_out]"
    )
    return CompiledAudioFilterGraph(tuple(material_ids), ";".join(parts), "audio_out")


def compile_audio_filter_graph(
    plan: LocalEditingAudioRenderPlan,
    *,
    first_input_index: int,
) -> CompiledAudioFilterGraph:
    """Assign inputs and compile ducked/fixed branches without local paths."""

    if (
        not isinstance(plan, LocalEditingAudioRenderPlan)
        or type(first_input_index) is not int
        or first_input_index < 0
    ):
        _reject()
    return _compile_validated_audio_filter_graph(
        _validated_plan(plan),
        first_input_index=first_input_index,
        excluded_sequences=frozenset(),
    )


def _validated_bindings(
    sources: tuple[AudioRenderSourceBinding, ...],
) -> tuple[AudioRenderSourceBinding, ...]:
    if not isinstance(sources, tuple) or not all(
        isinstance(source, AudioRenderSourceBinding) for source in sources
    ):
        _reject_binding(AudioRenderBindingRejection.INVALID_BINDINGS)
    try:
        rebuilt = tuple(
            AudioRenderSourceBinding(
                material_id=source.material_id,
                source_path=source.source_path,
                has_audio=source.has_audio,
            )
            for source in sources
        )
    except Exception:
        _reject_binding(AudioRenderBindingRejection.INVALID_BINDINGS)
    if len({source.material_id for source in rebuilt}) != len(rebuilt):
        _reject_binding(AudioRenderBindingRejection.INVALID_BINDINGS)
    return rebuilt


def bind_audio_render_inputs(
    plan: LocalEditingAudioRenderPlan,
    sources: tuple[AudioRenderSourceBinding, ...],
    *,
    first_input_index: int,
) -> BoundAudioRenderInputs:
    """Apply local audio facts and bind exact source paths in compiler input order."""

    if (
        not isinstance(plan, LocalEditingAudioRenderPlan)
        or type(first_input_index) is not int
        or first_input_index < 0
    ):
        _reject_binding(AudioRenderBindingRejection.INVALID_PLAN)
    try:
        validated_plan = _validated_plan(plan)
    except AudioRenderCompilationRejected:
        _reject_binding(AudioRenderBindingRejection.INVALID_PLAN)
    validated_sources = _validated_bindings(sources)
    source_by_id = {source.material_id: source for source in validated_sources}
    expected_ids = {clip.material_id for clip in validated_plan.clips}
    if set(source_by_id) != expected_ids:
        _reject_binding(AudioRenderBindingRejection.INVALID_BINDINGS)

    excluded_sequences: set[int] = set()
    for clip in validated_plan.clips:
        source = source_by_id[clip.material_id]
        if source.has_audio or clip.original_audio_mode is LocalEditingOriginalAudioMode.MUTED:
            continue
        if clip.track_kind is LocalEditingAudioTrackKind.AMBIENT:
            excluded_sequences.add(clip.sequence)
            continue
        _reject_binding(
            AudioRenderBindingRejection.SOURCE_HAS_NO_AUDIO,
            material_id=clip.material_id,
        )

    compiled = _compile_validated_audio_filter_graph(
        validated_plan,
        first_input_index=first_input_index,
        excluded_sequences=frozenset(excluded_sequences),
    )
    input_argv: list[str] = []
    for material_id in compiled.input_material_ids:
        input_argv.extend(("-i", os.fspath(source_by_id[material_id].source_path)))
    return BoundAudioRenderInputs(
        input_argv=tuple(input_argv),
        input_material_ids=compiled.input_material_ids,
        filter_graph=compiled.filter_graph,
        output_label=compiled.output_label,
    )


__all__ = [
    "AUDIO_DUCK_ATTACK_MS",
    "AUDIO_DUCK_RATIO",
    "AUDIO_DUCK_RELEASE_MS",
    "AUDIO_DUCK_THRESHOLD",
    "AUDIO_SAMPLE_RATE",
    "AudioRenderBindingRejected",
    "AudioRenderBindingRejection",
    "AudioRenderCompilationRejected",
    "AudioRenderSourceBinding",
    "BoundAudioRenderInputs",
    "CompiledAudioFilterGraph",
    "bind_audio_render_inputs",
    "compile_audio_filter_graph",
]
