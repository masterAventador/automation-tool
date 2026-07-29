"""Provider-neutral domain contracts shared by both video creation methods."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Never, final

from automation_tool.control_plane.domain.resource_ids import ArtifactId, ResourceId
from automation_tool.control_plane.domain.timeline import TimelineId

MAX_VIDEO_DURATION_MS: Final = 600_000
MAX_BRIEF_CHARACTERS: Final = 4_000
MAX_SCENES: Final = 128
MAX_ARTIFACT_REFERENCES: Final = 256
MAX_ARTIFACT_BYTES: Final = 16 * 1024 * 1024 * 1024

_LANGUAGE_PATTERN = re.compile(r"^[a-z]{2,3}(?:-[A-Z][a-z]{3})?(?:-(?:[A-Z]{2}|\d{3}))?\Z")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}\Z")


class InvalidVideoDomainModel(ValueError):
    """A provider-neutral video domain value is invalid."""

    def __init__(self) -> None:
        super().__init__("Video domain model is invalid")


@final
class ContentBriefId(ResourceId):
    """Stable identifier for one user video intent."""

    __slots__ = ()
    _resource = "content brief"


@final
class StoryboardId(ResourceId):
    """Stable identifier for one storyboard lineage."""

    __slots__ = ()
    _resource = "storyboard"


@final
class RenderJobId(ResourceId):
    """Stable identifier for one video rendering job."""

    __slots__ = ()
    _resource = "render job"


class VideoCreationMethod(StrEnum):
    """Internal product routes; upstream implementation names are not public IDs."""

    MATERIAL_MONTAGE_V1 = "material_montage_v1"
    MOTION_COMPOSITION_V1 = "motion_composition_v1"


class VideoAspectRatio(StrEnum):
    LANDSCAPE_16_9 = "landscape_16_9"
    PORTRAIT_9_16 = "portrait_9_16"
    SQUARE_1_1 = "square_1_1"


class ArtifactRole(StrEnum):
    SOURCE_IMAGE = "source_image"
    SOURCE_VIDEO = "source_video"
    SOURCE_AUDIO = "source_audio"
    SCRIPT = "script"
    STORYBOARD = "storyboard"
    TIMELINE = "timeline"
    COMPOSITION = "composition"
    PREVIEW_IMAGE = "preview_image"
    OUTPUT_VIDEO = "output_video"
    SUBTITLE = "subtitle"


class RenderJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RenderFailureCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    RENDER_FAILED = "render_failed"


_ROLE_MEDIA_TYPES: Final[dict[ArtifactRole, frozenset[str]]] = {
    ArtifactRole.SOURCE_IMAGE: frozenset({"image/jpeg", "image/png", "image/webp"}),
    ArtifactRole.SOURCE_VIDEO: frozenset({"video/mp4", "video/webm"}),
    ArtifactRole.SOURCE_AUDIO: frozenset({"audio/mpeg", "audio/ogg", "audio/wav"}),
    ArtifactRole.SCRIPT: frozenset({"application/json", "text/plain"}),
    ArtifactRole.STORYBOARD: frozenset({"application/json"}),
    ArtifactRole.TIMELINE: frozenset({"application/json"}),
    ArtifactRole.COMPOSITION: frozenset({"application/json", "text/html"}),
    ArtifactRole.PREVIEW_IMAGE: frozenset({"image/jpeg", "image/png", "image/webp"}),
    ArtifactRole.OUTPUT_VIDEO: frozenset({"video/mp4"}),
    ArtifactRole.SUBTITLE: frozenset({"text/vtt"}),
}


def _reject() -> Never:
    raise InvalidVideoDomainModel


def _validate_text(value: object, *, maximum: int, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
        _reject()
    for character in value:
        if character in {"\n", "\t"}:
            continue
        if unicodedata.category(character).startswith("C"):
            _reject()


def _validate_timestamp(value: object) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        _reject()


def _validate_resource_tuple(
    values: object,
    *,
    maximum: int,
) -> None:
    if not isinstance(values, tuple) or len(values) > maximum:
        _reject()
    if any(not isinstance(value, ArtifactId) for value in values) or len(set(values)) != len(
        values
    ):
        _reject()


@dataclass(frozen=True, slots=True)
class ContentBrief:
    brief_id: ContentBriefId
    prompt: str
    language: str
    target_duration_ms: int
    aspect_ratio: VideoAspectRatio
    source_artifact_ids: tuple[ArtifactId, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.brief_id, ContentBriefId):
            _reject()
        _validate_text(self.prompt, maximum=MAX_BRIEF_CHARACTERS)
        if not isinstance(self.language, str) or _LANGUAGE_PATTERN.fullmatch(self.language) is None:
            _reject()
        if (
            type(self.target_duration_ms) is not int
            or not 1_000 <= self.target_duration_ms <= MAX_VIDEO_DURATION_MS
            or not isinstance(self.aspect_ratio, VideoAspectRatio)
        ):
            _reject()
        _validate_resource_tuple(self.source_artifact_ids, maximum=64)
        _validate_timestamp(self.created_at)


@dataclass(frozen=True, slots=True)
class StoryboardScene:
    sequence: int
    duration_ms: int
    visual_direction: str
    narration: str | None
    on_screen_text: str | None

    def __post_init__(self) -> None:
        if (
            type(self.sequence) is not int
            or not 1 <= self.sequence <= MAX_SCENES
            or type(self.duration_ms) is not int
            or not 100 <= self.duration_ms <= MAX_VIDEO_DURATION_MS
        ):
            _reject()
        _validate_text(self.visual_direction, maximum=4_000)
        _validate_text(self.narration, maximum=4_000, optional=True)
        _validate_text(self.on_screen_text, maximum=1_000, optional=True)


@dataclass(frozen=True, slots=True)
class Storyboard:
    storyboard_id: StoryboardId
    brief_id: ContentBriefId
    revision: int
    scenes: tuple[StoryboardScene, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.storyboard_id, StoryboardId)
            or not isinstance(self.brief_id, ContentBriefId)
            or type(self.revision) is not int
            or self.revision < 1
            or not isinstance(self.scenes, tuple)
            or not 1 <= len(self.scenes) <= MAX_SCENES
            or any(not isinstance(scene, StoryboardScene) for scene in self.scenes)
            or tuple(scene.sequence for scene in self.scenes)
            != tuple(range(1, len(self.scenes) + 1))
            or sum(scene.duration_ms for scene in self.scenes) > MAX_VIDEO_DURATION_MS
        ):
            _reject()
        _validate_timestamp(self.created_at)


@dataclass(frozen=True, slots=True)
class Artifact:
    artifact_id: ArtifactId
    role: ArtifactRole
    media_type: str
    byte_size: int
    sha256: str
    source_artifact_ids: tuple[ArtifactId, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.artifact_id, ArtifactId)
            or not isinstance(self.role, ArtifactRole)
            or not isinstance(self.media_type, str)
            or self.media_type not in _ROLE_MEDIA_TYPES.get(self.role, frozenset())
            or type(self.byte_size) is not int
            or not 1 <= self.byte_size <= MAX_ARTIFACT_BYTES
            or not isinstance(self.sha256, str)
            or _SHA256_PATTERN.fullmatch(self.sha256) is None
        ):
            _reject()
        _validate_resource_tuple(self.source_artifact_ids, maximum=MAX_ARTIFACT_REFERENCES)
        if self.artifact_id in self.source_artifact_ids:
            _reject()
        _validate_timestamp(self.created_at)


@dataclass(frozen=True, slots=True)
class RenderJob:
    render_job_id: RenderJobId
    brief_id: ContentBriefId
    storyboard_id: StoryboardId
    timeline_id: TimelineId
    method: VideoCreationMethod
    status: RenderJobStatus
    revision: int
    input_artifact_ids: tuple[ArtifactId, ...]
    output_artifact_ids: tuple[ArtifactId, ...]
    failure_code: RenderFailureCode | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.render_job_id, RenderJobId)
            or not isinstance(self.brief_id, ContentBriefId)
            or not isinstance(self.storyboard_id, StoryboardId)
            or not isinstance(self.timeline_id, TimelineId)
            or not isinstance(self.method, VideoCreationMethod)
            or not isinstance(self.status, RenderJobStatus)
            or type(self.revision) is not int
            or self.revision < 1
            or (
                self.failure_code is not None
                and not isinstance(self.failure_code, RenderFailureCode)
            )
        ):
            _reject()
        _validate_resource_tuple(self.input_artifact_ids, maximum=MAX_ARTIFACT_REFERENCES)
        _validate_resource_tuple(self.output_artifact_ids, maximum=MAX_ARTIFACT_REFERENCES)
        if set(self.input_artifact_ids) & set(self.output_artifact_ids):
            _reject()
        _validate_timestamp(self.created_at)
        _validate_timestamp(self.updated_at)
        if self.updated_at < self.created_at:
            _reject()

        if self.status in {RenderJobStatus.QUEUED, RenderJobStatus.RUNNING}:
            valid_facts = not self.output_artifact_ids and self.failure_code is None
        elif self.status is RenderJobStatus.SUCCEEDED:
            valid_facts = bool(self.output_artifact_ids) and self.failure_code is None
        elif self.status is RenderJobStatus.FAILED:
            valid_facts = not self.output_artifact_ids and self.failure_code is not None
        else:
            valid_facts = not self.output_artifact_ids and self.failure_code is None
        if not valid_facts:
            _reject()


__all__ = [
    "MAX_ARTIFACT_BYTES",
    "MAX_ARTIFACT_REFERENCES",
    "MAX_BRIEF_CHARACTERS",
    "MAX_SCENES",
    "MAX_VIDEO_DURATION_MS",
    "Artifact",
    "ArtifactRole",
    "ContentBrief",
    "ContentBriefId",
    "InvalidVideoDomainModel",
    "RenderFailureCode",
    "RenderJob",
    "RenderJobId",
    "RenderJobStatus",
    "Storyboard",
    "StoryboardId",
    "StoryboardScene",
    "VideoAspectRatio",
    "VideoCreationMethod",
]
