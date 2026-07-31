"""Compile a path-free local audio plan into one deterministic FFmpeg graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Never, cast
from uuid import UUID

from automation_tool.protocol.local_rendering import (
    LocalEditingAudioRenderClip,
    LocalEditingAudioRenderPlan,
    LocalEditingAudioTrackKind,
    LocalEditingOriginalAudioMode,
)

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


@dataclass(frozen=True, slots=True, repr=False)
class CompiledAudioFilterGraph:
    input_material_ids: tuple[UUID, ...]
    filter_graph: str
    output_label: str | None

    def __repr__(self) -> str:
        return "CompiledAudioFilterGraph(<redacted>)"


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
            clips=tuple(
                LocalEditingAudioRenderClip(
                    sequence=clip.sequence,
                    track_kind=clip.track_kind,
                    material_id=clip.material_id,
                    start_ms=clip.start_ms,
                    duration_ms=clip.duration_ms,
                    source_in_ms=clip.source_in_ms,
                    source_out_ms=clip.source_out_ms,
                    gain_db=clip.gain_db,
                    original_audio_mode=clip.original_audio_mode,
                )
                for clip in plan.clips
            ),
        )
    except Exception:
        _reject()


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
    validated = _validated_plan(plan)
    parts: list[str] = []
    material_ids: list[UUID] = []
    narration_labels: list[str] = []
    duckable_labels: list[str] = []
    fixed_labels: list[str] = []
    total_seconds = _seconds(validated.duration_ms)

    for clip in validated.clips:
        if clip.original_audio_mode is LocalEditingOriginalAudioMode.MUTED:
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


__all__ = [
    "AUDIO_DUCK_ATTACK_MS",
    "AUDIO_DUCK_RATIO",
    "AUDIO_DUCK_RELEASE_MS",
    "AUDIO_DUCK_THRESHOLD",
    "AUDIO_SAMPLE_RATE",
    "AudioRenderCompilationRejected",
    "CompiledAudioFilterGraph",
    "compile_audio_filter_graph",
]
