"""Execute one App-owned local-editing checkpoint with the shipped render pipeline."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Never
from uuid import RFC_4122, UUID, uuid4

from automation_tool.control_plane.application.local_editing_visual_render import (
    create_local_editing_audio_render_plan,
    create_local_editing_caption_render_plan,
    create_local_editing_visual_render_plan,
)
from automation_tool.control_plane.domain.editing_project import (
    CaptionStyle,
    EditingProject,
    EditingProjectId,
    OutputSpec,
)
from automation_tool.control_plane.domain.material import MaterialId
from automation_tool.control_plane.domain.timeline import (
    OriginalAudioMode,
    Timeline,
    TimelineClip,
    TimelineId,
    TimelineTrack,
    TimelineTrackKind,
    TimelineTransition,
    TransitionKind,
)
from automation_tool.executor.audio_rendering import AudioRenderSourceBinding
from automation_tool.executor.local_editing_worker import (
    LocalEditingStartCommand,
    LocalEditingWorkerBootstrap,
    LocalEditingWorkerFailureCode,
)
from automation_tool.executor.material_probe import (
    MATERIAL_PATH_REGISTRY_FILE_NAME,
    MaterialPathRegistry,
    MaterialPathRegistryRejected,
)
from automation_tool.executor.visual_render_execution import (
    VisualRenderExecutionRejected,
    VisualRenderExecutionRejection,
    execute_audiovisual_render,
    execute_visual_render,
)
from automation_tool.executor.visual_rendering import VisualRenderSourceBinding

_SCHEMA_VERSION = "local-editing-render-request.v1"
_CHECKPOINT_NAME = "local-editing-render-request.checkpoint"
_MAX_CHECKPOINT_BYTES = 1024 * 1024


class LocalEditingRenderRejected(RuntimeError):
    """A fixed, path-free terminal failure for the Worker protocol."""

    def __init__(
        self,
        code: LocalEditingWorkerFailureCode,
        diagnostic: LocalEditingRenderDiagnosticCode | None = None,
    ) -> None:
        self.code = code
        self.diagnostic = diagnostic or LocalEditingRenderDiagnosticCode.REJECTED
        super().__init__("local editing render rejected")

    def __repr__(self) -> str:
        return "LocalEditingRenderRejected(<redacted>)"


class LocalEditingRenderDiagnosticCode(StrEnum):
    """Closed, path-free reason retained only for local operational diagnostics."""

    REJECTED = "rejected"
    REGISTRY_FILE_MISSING = "registry_file_missing"
    BINDING_MISSING = "binding_missing"
    SOURCE_CHANGED = "source_changed"
    UNUSABLE_IDENTIFIER = "unusable_identifier"
    NOT_REGISTERED = "not_registered"
    FILE_MISSING = "file_missing"
    FILE_UNREADABLE = "file_unreadable"
    FILE_CHANGED = "file_changed"
    REGISTRY_UNREADABLE = "registry_unreadable"
    REGISTRY_UNWRITABLE = "registry_unwritable"
    REGISTRY_FULL = "registry_full"


class LocalEditingRenderCancelled(RuntimeError):
    def __init__(self) -> None:
        super().__init__("local editing render cancelled")


def _reject(
    code: LocalEditingWorkerFailureCode,
    diagnostic: LocalEditingRenderDiagnosticCode = LocalEditingRenderDiagnosticCode.REJECTED,
) -> Never:
    raise LocalEditingRenderRejected(code, diagnostic) from None


def _object(value: object, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        _reject(LocalEditingWorkerFailureCode.INVALID_TIMELINE)
    return value


def _uuid(value: object) -> UUID:
    try:
        parsed = UUID(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.version != 4 or parsed.variant != RFC_4122 or str(parsed) != value:
        _reject(LocalEditingWorkerFailureCode.INVALID_TIMELINE)
    return parsed


def _timestamp(value: object) -> datetime:
    try:
        parsed = (
            datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else None
        )
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        _reject(LocalEditingWorkerFailureCode.INVALID_TIMELINE)
    return parsed


def _optional_uuid(value: object) -> MaterialId | None:
    return None if value is None else MaterialId(_uuid(value))


def _transition(value: object) -> TimelineTransition | None:
    if value is None:
        return None
    document = _object(value, {"kind", "durationMs"})
    kind = document["kind"]
    if not isinstance(kind, str):
        _reject(LocalEditingWorkerFailureCode.INVALID_TIMELINE)
    try:
        return TimelineTransition(
            kind=TransitionKind(kind),
            duration_ms=document["durationMs"],  # type: ignore[arg-type]
        )
    except Exception:
        _reject(LocalEditingWorkerFailureCode.INVALID_TIMELINE)


def _clip(value: object) -> TimelineClip:
    document = _object(
        value,
        {
            "clipId",
            "startMs",
            "durationMs",
            "sourceMaterialId",
            "sourceInMs",
            "sourceOutMs",
            "text",
            "gainDb",
            "transitionIn",
            "originalAudioMode",
        },
    )
    original = document["originalAudioMode"]
    try:
        return TimelineClip(
            clip_id=document["clipId"],  # type: ignore[arg-type]
            start_ms=document["startMs"],  # type: ignore[arg-type]
            duration_ms=document["durationMs"],  # type: ignore[arg-type]
            source_material_id=_optional_uuid(document["sourceMaterialId"]),
            source_in_ms=document["sourceInMs"],  # type: ignore[arg-type]
            source_out_ms=document["sourceOutMs"],  # type: ignore[arg-type]
            text=document["text"],  # type: ignore[arg-type]
            gain_db=document["gainDb"],  # type: ignore[arg-type]
            transition_in=_transition(document["transitionIn"]),
            original_audio_mode=(
                None if original is None else OriginalAudioMode(original)  # type: ignore[arg-type]
            ),
        )
    except Exception:
        _reject(LocalEditingWorkerFailureCode.INVALID_TIMELINE)


def _track(value: object) -> TimelineTrack:
    document = _object(value, {"trackId", "kind", "clips"})
    clips = document["clips"]
    if not isinstance(clips, list):
        _reject(LocalEditingWorkerFailureCode.INVALID_TIMELINE)
    try:
        return TimelineTrack(
            track_id=document["trackId"],  # type: ignore[arg-type]
            kind=TimelineTrackKind(document["kind"]),  # type: ignore[arg-type]
            clips=tuple(_clip(item) for item in clips),
        )
    except Exception:
        _reject(LocalEditingWorkerFailureCode.INVALID_TIMELINE)


def _project(value: object) -> EditingProject:
    document = _object(value, {"projectId", "title", "output", "captionStyle", "createdAt"})
    output = _object(document["output"], {"width", "height", "fps"})
    captions = _object(document["captionStyle"], {"fontKey", "fontPx", "strokePx", "lineSpacing"})
    try:
        return EditingProject(
            project_id=EditingProjectId(_uuid(document["projectId"])),
            title=document["title"],  # type: ignore[arg-type]
            output=OutputSpec(
                width=output["width"],  # type: ignore[arg-type]
                height=output["height"],  # type: ignore[arg-type]
                fps=output["fps"],  # type: ignore[arg-type]
            ),
            caption_style=CaptionStyle(
                font_key=captions["fontKey"],  # type: ignore[arg-type]
                font_px=captions["fontPx"],  # type: ignore[arg-type]
                stroke_px=captions["strokePx"],  # type: ignore[arg-type]
                line_spacing=captions["lineSpacing"],  # type: ignore[arg-type]
            ),
            created_at=_timestamp(document["createdAt"]),
        )
    except Exception:
        _reject(LocalEditingWorkerFailureCode.INVALID_TIMELINE)


def _timeline(value: object) -> Timeline:
    document = _object(
        value,
        {
            "timelineId",
            "projectId",
            "revision",
            "durationMs",
            "tracks",
            "createdAt",
        },
    )
    tracks = document["tracks"]
    if not isinstance(tracks, list):
        _reject(LocalEditingWorkerFailureCode.INVALID_TIMELINE)
    try:
        return Timeline(
            timeline_id=TimelineId(_uuid(document["timelineId"])),
            project_id=EditingProjectId(_uuid(document["projectId"])),
            revision=document["revision"],  # type: ignore[arg-type]
            duration_ms=document["durationMs"],  # type: ignore[arg-type]
            tracks=tuple(_track(item) for item in tracks),
            created_at=_timestamp(document["createdAt"]),
        )
    except Exception:
        _reject(LocalEditingWorkerFailureCode.INVALID_TIMELINE)


@dataclass(frozen=True, slots=True)
class _MaterialBinding:
    material_id: UUID
    has_audio: bool


def _materials(value: object) -> tuple[_MaterialBinding, ...]:
    if not isinstance(value, list) or not value:
        _reject(LocalEditingWorkerFailureCode.MATERIAL_UNAVAILABLE)
    result: list[_MaterialBinding] = []
    for item in value:
        document = _object(item, {"materialId", "hasAudio"})
        if type(document["hasAudio"]) is not bool:
            _reject(LocalEditingWorkerFailureCode.MATERIAL_UNAVAILABLE)
        result.append(_MaterialBinding(_uuid(document["materialId"]), document["hasAudio"]))
    if len({item.material_id for item in result}) != len(result):
        _reject(LocalEditingWorkerFailureCode.MATERIAL_UNAVAILABLE)
    return tuple(result)


def _load_request(app_data: Path, job_id: UUID) -> dict[str, object]:
    checkpoint = (
        app_data / "video-workspaces-v1" / "jobs" / str(job_id) / "checkpoints" / _CHECKPOINT_NAME
    )
    try:
        metadata = checkpoint.lstat()
        if (
            checkpoint.is_symlink()
            or not checkpoint.is_file()
            or not 1 <= metadata.st_size <= _MAX_CHECKPOINT_BYTES
        ):
            _reject(LocalEditingWorkerFailureCode.WORKSPACE_UNUSABLE)
        payload = checkpoint.read_bytes()

        def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
            document: dict[str, object] = {}
            for key, value in pairs:
                if key in document:
                    raise ValueError("duplicate key")
                document[key] = value
            return document

        value = json.loads(payload, object_pairs_hook=unique)
    except LocalEditingRenderRejected:
        raise
    except OSError:
        _reject(LocalEditingWorkerFailureCode.WORKSPACE_UNUSABLE)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _reject(LocalEditingWorkerFailureCode.INVALID_TIMELINE)
    return _object(value, {"schemaVersion", "jobId", "project", "timeline", "materials"})


def _render_failure(error: VisualRenderExecutionRejected) -> Never:
    mapping = {
        VisualRenderExecutionRejection.INVALID_REQUEST: (
            LocalEditingWorkerFailureCode.INVALID_TIMELINE
        ),
        VisualRenderExecutionRejection.TOOL_UNAVAILABLE: (
            LocalEditingWorkerFailureCode.RENDER_FAILED
        ),
        VisualRenderExecutionRejection.SOURCE_CHANGED: (
            LocalEditingWorkerFailureCode.MATERIAL_UNAVAILABLE
        ),
        VisualRenderExecutionRejection.WORKSPACE_UNUSABLE: (
            LocalEditingWorkerFailureCode.WORKSPACE_UNUSABLE
        ),
        VisualRenderExecutionRejection.OUTPUT_EXISTS: (
            LocalEditingWorkerFailureCode.WORKSPACE_UNUSABLE
        ),
        VisualRenderExecutionRejection.CAPTION_FAILED: (
            LocalEditingWorkerFailureCode.FONT_UNAVAILABLE
        ),
        VisualRenderExecutionRejection.PROCESS_FAILED: LocalEditingWorkerFailureCode.RENDER_FAILED,
        VisualRenderExecutionRejection.TIMED_OUT: LocalEditingWorkerFailureCode.RENDER_FAILED,
        VisualRenderExecutionRejection.OUTPUT_TOO_LARGE: (
            LocalEditingWorkerFailureCode.RESOURCE_EXHAUSTED
        ),
        VisualRenderExecutionRejection.OUTPUT_INVALID: LocalEditingWorkerFailureCode.RENDER_FAILED,
    }
    if error.code is VisualRenderExecutionRejection.CANCELLED:
        raise LocalEditingRenderCancelled from None
    diagnostic = (
        LocalEditingRenderDiagnosticCode.SOURCE_CHANGED
        if error.code is VisualRenderExecutionRejection.SOURCE_CHANGED
        else LocalEditingRenderDiagnosticCode.REJECTED
    )
    _reject(mapping[error.code], diagnostic)


def execute_local_editing_job(
    bootstrap: LocalEditingWorkerBootstrap,
    command: LocalEditingStartCommand,
    *,
    cancel_requested: Callable[[], bool],
    artifact_id_factory: Callable[[], UUID] = uuid4,
) -> UUID:
    """Render the exact checkpoint named by an authenticated path-free command."""

    document = _load_request(bootstrap.asset_root, command.job_id)
    if document["schemaVersion"] != _SCHEMA_VERSION or _uuid(document["jobId"]) != command.job_id:
        _reject(LocalEditingWorkerFailureCode.INVALID_TIMELINE)
    project = _project(document["project"])
    timeline = _timeline(document["timeline"])
    if (
        project.project_id.uuid != command.project_id
        or timeline.timeline_id.uuid != command.timeline_id
        or timeline.project_id != project.project_id
        or timeline.revision != command.timeline_revision
    ):
        _reject(LocalEditingWorkerFailureCode.INVALID_TIMELINE)
    bindings = _materials(document["materials"])
    binding_by_id = {item.material_id: item for item in bindings}
    registry_directory = bootstrap.asset_root / "local-executor" / "state"
    if not (registry_directory / MATERIAL_PATH_REGISTRY_FILE_NAME).is_file():
        _reject(
            LocalEditingWorkerFailureCode.MATERIAL_UNAVAILABLE,
            LocalEditingRenderDiagnosticCode.REGISTRY_FILE_MISSING,
        )
    try:
        registry = MaterialPathRegistry(state_directory=registry_directory)
        visual_plan = create_local_editing_visual_render_plan(project, timeline)
        audio_plan = create_local_editing_audio_render_plan(project, timeline)
        caption_plan = create_local_editing_caption_render_plan(project, timeline)
        resolved: dict[UUID, tuple[Path, object]] = {}
        for material_id in {
            *(clip.material_id for clip in visual_plan.clips),
            *(clip.material_id for clip in audio_plan.clips),
        }:
            if material_id not in binding_by_id:
                _reject(
                    LocalEditingWorkerFailureCode.MATERIAL_UNAVAILABLE,
                    LocalEditingRenderDiagnosticCode.BINDING_MISSING,
                )
            resolved[material_id] = registry.resolve(material_id)
        visual_sources = tuple(
            VisualRenderSourceBinding(
                material_id=clip.material_id,
                kind=clip.kind,
                source_path=resolved[clip.material_id][0],
            )
            for clip in visual_plan.clips
        )
        visual_approvals = tuple(resolved[clip.material_id][1] for clip in visual_plan.clips)
        audio_material_ids = tuple(dict.fromkeys(clip.material_id for clip in audio_plan.clips))
        audio_sources = tuple(
            AudioRenderSourceBinding(
                material_id=material_id,
                source_path=resolved[material_id][0],
                has_audio=binding_by_id[material_id].has_audio,
            )
            for material_id in audio_material_ids
        )
        audio_approvals = tuple(resolved[material_id][1] for material_id in audio_material_ids)
    except LocalEditingRenderRejected:
        raise
    except MaterialPathRegistryRejected as error:
        _reject(
            LocalEditingWorkerFailureCode.MATERIAL_UNAVAILABLE,
            LocalEditingRenderDiagnosticCode(error.rejection.value),
        )
    except Exception:
        _reject(LocalEditingWorkerFailureCode.INVALID_TIMELINE)

    task_directory = (
        bootstrap.asset_root / "video-workspaces-v1" / "jobs" / str(command.job_id) / "outputs"
    )
    try:
        if audio_plan.clips:
            execute_audiovisual_render(
                bootstrap.media_tools,
                visual_plan,
                visual_sources,
                visual_approvals,  # type: ignore[arg-type]
                audio_plan,
                audio_sources,
                audio_approvals,  # type: ignore[arg-type]
                task_directory,
                caption_plan=caption_plan if caption_plan.cues else None,
                cancel_requested=cancel_requested,
            )
        else:
            execute_visual_render(
                bootstrap.media_tools,
                visual_plan,
                visual_sources,
                visual_approvals,  # type: ignore[arg-type]
                task_directory,
                caption_plan=caption_plan if caption_plan.cues else None,
                cancel_requested=cancel_requested,
            )
    except VisualRenderExecutionRejected as error:
        _render_failure(error)
    artifact_id = artifact_id_factory()
    if (
        not isinstance(artifact_id, UUID)
        or artifact_id.version != 4
        or artifact_id.variant != RFC_4122
    ):
        _reject(LocalEditingWorkerFailureCode.RENDER_FAILED)
    return artifact_id


__all__ = [
    "LocalEditingRenderCancelled",
    "LocalEditingRenderDiagnosticCode",
    "LocalEditingRenderRejected",
    "execute_local_editing_job",
]
