"""Render path-free caption cues into local PNG bindings on one frame grid."""

from __future__ import annotations

import os
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Never
from uuid import RFC_4122, UUID

from automation_tool.executor.captions.fonts import CaptionFontRejected
from automation_tool.executor.captions.render import CaptionRenderStyle, render_caption
from automation_tool.executor.material_probe import MAX_PATH_CHARACTERS
from automation_tool.protocol.local_rendering import (
    LocalEditingCaptionRenderCue,
    LocalEditingCaptionRenderPlan,
    LocalEditingCaptionRenderStyle,
)
from automation_tool.protocol.safe_text import contains_control_or_bidi


class CaptionOverlayRejection(StrEnum):
    INVALID_PLAN = "invalid_plan"
    INVALID_WORKSPACE = "invalid_workspace"
    CAPTION_BELOW_ONE_FRAME = "caption_below_one_frame"
    RENDER_FAILED = "render_failed"


class CaptionOverlayRejected(ValueError):
    """Caption PNG preparation failed without exposing text or local paths."""

    def __init__(self, code: CaptionOverlayRejection) -> None:
        self.code = code
        super().__init__("caption overlay preparation rejected")


def _reject(code: CaptionOverlayRejection) -> Never:
    raise CaptionOverlayRejected(code) from None


def _is_uuid4(value: object) -> bool:
    return (
        isinstance(value, UUID)
        and value.variant == RFC_4122
        and value.version == 4
        and value.int != 0
    )


def _valid_path(value: object, *, suffix: str | None = None) -> bool:
    if not isinstance(value, Path) or not value.is_absolute():
        return False
    text = os.fspath(value)
    return (
        1 <= len(text) <= MAX_PATH_CHARACTERS
        and not contains_control_or_bidi(text)
        and (suffix is None or value.suffix == suffix)
    )


@dataclass(frozen=True, slots=True, repr=False)
class VisualCaptionOverlayBinding:
    """One local PNG and the exact inclusive-exclusive output frame window."""

    sequence: int
    start_frame: int
    end_frame: int
    source_path: Path = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.sequence) is not int
            or self.sequence < 1
            or type(self.start_frame) is not int
            or self.start_frame < 0
            or type(self.end_frame) is not int
            or self.end_frame <= self.start_frame
            or not _valid_path(self.source_path, suffix=".png")
        ):
            _reject(CaptionOverlayRejection.INVALID_PLAN)

    def __repr__(self) -> str:
        return "VisualCaptionOverlayBinding(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class VisualCaptionOverlaySet:
    """Local caption files bound to one exact visual render identity."""

    project_id: UUID
    timeline_id: UUID
    timeline_revision: int
    output_width: int
    output_height: int
    output_fps: int
    duration_ms: int
    target_frames: int
    captions: tuple[VisualCaptionOverlayBinding, ...]

    def __post_init__(self) -> None:
        if (
            not _is_uuid4(self.project_id)
            or not _is_uuid4(self.timeline_id)
            or type(self.timeline_revision) is not int
            or self.timeline_revision < 1
            or type(self.output_width) is not int
            or self.output_width < 1
            or type(self.output_height) is not int
            or self.output_height < 1
            or type(self.output_fps) is not int
            or self.output_fps < 1
            or type(self.duration_ms) is not int
            or self.duration_ms < 1
            or type(self.target_frames) is not int
            or self.target_frames < 1
            or not isinstance(self.captions, tuple)
            or not all(isinstance(item, VisualCaptionOverlayBinding) for item in self.captions)
        ):
            _reject(CaptionOverlayRejection.INVALID_PLAN)
        try:
            captions = tuple(
                VisualCaptionOverlayBinding(
                    sequence=item.sequence,
                    start_frame=item.start_frame,
                    end_frame=item.end_frame,
                    source_path=item.source_path,
                )
                for item in self.captions
            )
        except Exception:
            _reject(CaptionOverlayRejection.INVALID_PLAN)
        if tuple(item.sequence for item in captions) != tuple(range(1, len(captions) + 1)):
            _reject(CaptionOverlayRejection.INVALID_PLAN)
        previous_end = 0
        for item in captions:
            if item.start_frame < previous_end or item.end_frame > self.target_frames:
                _reject(CaptionOverlayRejection.INVALID_PLAN)
            previous_end = item.end_frame
        object.__setattr__(self, "captions", captions)

    def __repr__(self) -> str:
        return "VisualCaptionOverlaySet(<redacted>)"


def _validated_plan(plan: LocalEditingCaptionRenderPlan) -> LocalEditingCaptionRenderPlan:
    if not isinstance(plan, LocalEditingCaptionRenderPlan) or not isinstance(plan.cues, tuple):
        _reject(CaptionOverlayRejection.INVALID_PLAN)
    try:
        return LocalEditingCaptionRenderPlan(
            project_id=plan.project_id,
            timeline_id=plan.timeline_id,
            timeline_revision=plan.timeline_revision,
            output_width=plan.output_width,
            output_height=plan.output_height,
            output_fps=plan.output_fps,
            duration_ms=plan.duration_ms,
            style=LocalEditingCaptionRenderStyle(
                font_key=plan.style.font_key,
                font_px=plan.style.font_px,
                stroke_px=plan.style.stroke_px,
                line_spacing=plan.style.line_spacing,
            ),
            cues=tuple(
                LocalEditingCaptionRenderCue(
                    sequence=cue.sequence,
                    start_ms=cue.start_ms,
                    duration_ms=cue.duration_ms,
                    text=cue.text,
                )
                for cue in plan.cues
            ),
        )
    except Exception:
        _reject(CaptionOverlayRejection.INVALID_PLAN)


def _frame_at(milliseconds: int, fps: int) -> int:
    return (milliseconds * fps + 500) // 1000


def _remove(paths: list[Path]) -> None:
    for path in paths:
        with suppress(OSError):
            path.unlink(missing_ok=True)


def render_caption_overlay_set(
    plan: LocalEditingCaptionRenderPlan,
    destination_directory: Path,
) -> VisualCaptionOverlaySet:
    """Render every caption atomically, or reject and remove this call's outputs."""

    validated = _validated_plan(plan)
    if (
        not _valid_path(destination_directory)
        or destination_directory.is_symlink()
        or not destination_directory.is_dir()
    ):
        _reject(CaptionOverlayRejection.INVALID_WORKSPACE)

    target_frames = _frame_at(validated.duration_ms, validated.output_fps)
    frame_windows: list[tuple[int, int]] = []
    destinations: list[Path] = []
    for cue in validated.cues:
        start_frame = _frame_at(cue.start_ms, validated.output_fps)
        end_frame = _frame_at(cue.end_ms, validated.output_fps)
        if end_frame <= start_frame:
            _reject(CaptionOverlayRejection.CAPTION_BELOW_ONE_FRAME)
        destination = destination_directory / f"caption-{cue.sequence:04d}.png"
        if destination.exists():
            _reject(CaptionOverlayRejection.INVALID_WORKSPACE)
        frame_windows.append((start_frame, end_frame))
        destinations.append(destination)

    style = CaptionRenderStyle(
        font_key=validated.style.font_key,
        font_px=validated.style.font_px,
        stroke_px=validated.style.stroke_px,
        line_spacing=validated.style.line_spacing,
    )
    written: list[Path] = []
    try:
        for cue, destination in zip(validated.cues, destinations, strict=True):
            result = render_caption(
                cue.text,
                style,
                frame_width=validated.output_width,
                frame_height=validated.output_height,
                destination=destination,
            )
            if result != destination or not destination.is_file():
                raise CaptionFontRejected("caption render result rejected")
            written.append(destination)
    except Exception:
        _remove(written + destinations)
        _reject(CaptionOverlayRejection.RENDER_FAILED)

    return VisualCaptionOverlaySet(
        project_id=validated.project_id,
        timeline_id=validated.timeline_id,
        timeline_revision=validated.timeline_revision,
        output_width=validated.output_width,
        output_height=validated.output_height,
        output_fps=validated.output_fps,
        duration_ms=validated.duration_ms,
        target_frames=target_frames,
        captions=tuple(
            VisualCaptionOverlayBinding(
                sequence=cue.sequence,
                start_frame=start_frame,
                end_frame=end_frame,
                source_path=destination,
            )
            for cue, (start_frame, end_frame), destination in zip(
                validated.cues,
                frame_windows,
                destinations,
                strict=True,
            )
        ),
    )


__all__ = [
    "CaptionOverlayRejected",
    "CaptionOverlayRejection",
    "VisualCaptionOverlayBinding",
    "VisualCaptionOverlaySet",
    "render_caption_overlay_set",
]
