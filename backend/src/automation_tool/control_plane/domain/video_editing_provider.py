"""Provider-neutral VideoEditingProvider port and fail-closed registry.

This module defines the only editing-provider surface the domain layer knows:
`capabilities` / `validate` / `submit` / `get` / `cancel` / `fetch_artifacts`,
a closed error taxonomy, statuses expressed exclusively as `EditingJobStatus`,
and a bounded idempotency key derived from the editing job identity. Vendor
DTOs, job IDs, regions, credentials, and raw payloads must stay inside each
adapter (Aliyun IMS/ICE first; any future provider) and never appear here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Never, Protocol, final, runtime_checkable

from automation_tool.control_plane.domain.resource_ids import ArtifactId
from automation_tool.control_plane.domain.video_creation import (
    MAX_ARTIFACT_REFERENCES,
    MAX_TRACKS,
    MAX_VIDEO_DURATION_MS,
    TimelineTrackKind,
    TransitionKind,
)
from automation_tool.control_plane.domain.video_editing import (
    EditingFailureCode,
    EditingJobId,
    EditingJobStatus,
    EditingProjectId,
    EditingTimeline,
)

MAX_EDITING_IDEMPOTENCY_KEY_CHARACTERS: Final = 128

_PROVIDER_ID_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{2,63}")
_IDEMPOTENCY_KEY_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")


class InvalidEditingProviderModel(ValueError):
    """A provider-neutral editing provider value is invalid."""

    def __init__(self) -> None:
        super().__init__("Video editing provider model is invalid")


def _reject() -> Never:
    raise InvalidEditingProviderModel


@final
class EditingProviderId(str):
    """Stable, closed identifier for one registered editing provider."""

    __slots__ = ()

    def __new__(cls, value: str) -> EditingProviderId:
        if type(value) is not str or _PROVIDER_ID_PATTERN.fullmatch(value) is None:
            _reject()
        return str.__new__(cls, value)


@final
class EditingIdempotencyKey(str):
    """Bounded canonical key deduplicating one editing submission intent."""

    __slots__ = ()

    def __new__(cls, value: str) -> EditingIdempotencyKey:
        if type(value) is not str or _IDEMPOTENCY_KEY_PATTERN.fullmatch(value) is None:
            _reject()
        return str.__new__(cls, value)


def editing_submission_idempotency_key(editing_job_id: EditingJobId) -> EditingIdempotencyKey:
    """Derive the canonical idempotency key for one editing job submission."""
    if not isinstance(editing_job_id, EditingJobId):
        _reject()
    return EditingIdempotencyKey(f"editing-job:{editing_job_id}")


class EditingProviderErrorCode(StrEnum):
    """Closed, provider-neutral failure taxonomy for the provider port."""

    INVALID_INPUT = "invalid_input"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    PROVIDER_ERROR = "provider_error"


_FAILURE_MESSAGES: Final[Mapping[EditingProviderErrorCode, str]] = MappingProxyType(
    {
        EditingProviderErrorCode.INVALID_INPUT: "Editing provider rejected invalid input",
        EditingProviderErrorCode.UNSUPPORTED_CAPABILITY: (
            "Editing provider does not support a requested capability"
        ),
        EditingProviderErrorCode.NOT_FOUND: "Editing provider resource was not found",
        EditingProviderErrorCode.CONFLICT: (
            "Editing provider request conflicts with existing state"
        ),
        EditingProviderErrorCode.DEPENDENCY_UNAVAILABLE: (
            "Editing provider dependency is unavailable"
        ),
        EditingProviderErrorCode.PROVIDER_ERROR: "Editing provider failed internally",
    }
)


@final
class EditingProviderFailure(Exception):
    """A closed provider failure carrying only a fixed, pre-sanitized message."""

    def __init__(self, code: EditingProviderErrorCode) -> None:
        if not isinstance(code, EditingProviderErrorCode):
            _reject()
        super().__init__(_FAILURE_MESSAGES[code])
        self.code: Final[EditingProviderErrorCode] = code


@dataclass(frozen=True, slots=True)
class EditingProviderCapabilities:
    """Declared, provider-neutral limits one editing provider supports."""

    provider_id: EditingProviderId
    supported_track_kinds: frozenset[TimelineTrackKind]
    supported_transition_kinds: frozenset[TransitionKind]
    max_timeline_duration_ms: int
    max_tracks: int

    def __post_init__(self) -> None:
        if (
            type(self.provider_id) is not EditingProviderId
            or type(self.supported_track_kinds) is not frozenset
            or not self.supported_track_kinds
            or any(not isinstance(kind, TimelineTrackKind) for kind in self.supported_track_kinds)
            or TimelineTrackKind.VISUAL not in self.supported_track_kinds
            or type(self.supported_transition_kinds) is not frozenset
            or any(not isinstance(kind, TransitionKind) for kind in self.supported_transition_kinds)
            or type(self.max_timeline_duration_ms) is not int
            or not 100 <= self.max_timeline_duration_ms <= MAX_VIDEO_DURATION_MS
            or type(self.max_tracks) is not int
            or not 1 <= self.max_tracks <= MAX_TRACKS
        ):
            _reject()

    def supports(self, timeline: EditingTimeline) -> bool:
        """Report whether the timeline fits inside the declared capabilities."""
        if not isinstance(timeline, EditingTimeline):
            _reject()
        if (
            timeline.duration_ms > self.max_timeline_duration_ms
            or len(timeline.tracks) > self.max_tracks
        ):
            return False
        for track in timeline.tracks:
            if track.kind not in self.supported_track_kinds:
                return False
            for clip in track.clips:
                if (
                    clip.transition_in is not None
                    and clip.transition_in.kind not in self.supported_transition_kinds
                ):
                    return False
        return True


@dataclass(frozen=True, slots=True)
class EditingSubmission:
    """One idempotent request to execute a frozen editing timeline revision."""

    editing_job_id: EditingJobId
    project_id: EditingProjectId
    timeline: EditingTimeline
    idempotency_key: EditingIdempotencyKey

    def __post_init__(self) -> None:
        if (
            not isinstance(self.editing_job_id, EditingJobId)
            or not isinstance(self.project_id, EditingProjectId)
            or not isinstance(self.timeline, EditingTimeline)
            or self.timeline.project_id != self.project_id
            or type(self.idempotency_key) is not EditingIdempotencyKey
            or self.idempotency_key != editing_submission_idempotency_key(self.editing_job_id)
        ):
            _reject()


@dataclass(frozen=True, slots=True)
class EditingProviderJobSnapshot:
    """Provider-neutral view of one editing job as reported by an adapter."""

    provider_id: EditingProviderId
    editing_job_id: EditingJobId
    status: EditingJobStatus
    failure_code: EditingFailureCode | None
    output_artifact_ids: tuple[ArtifactId, ...]

    def __post_init__(self) -> None:
        if (
            type(self.provider_id) is not EditingProviderId
            or not isinstance(self.editing_job_id, EditingJobId)
            or not isinstance(self.status, EditingJobStatus)
            or (
                self.failure_code is not None
                and not isinstance(self.failure_code, EditingFailureCode)
            )
            or not isinstance(self.output_artifact_ids, tuple)
            or len(self.output_artifact_ids) > MAX_ARTIFACT_REFERENCES
            or any(
                not isinstance(artifact_id, ArtifactId) for artifact_id in self.output_artifact_ids
            )
            or len(set(self.output_artifact_ids)) != len(self.output_artifact_ids)
        ):
            _reject()

        if self.status is EditingJobStatus.SUCCEEDED:
            valid_facts = bool(self.output_artifact_ids) and self.failure_code is None
        elif self.status is EditingJobStatus.FAILED:
            valid_facts = not self.output_artifact_ids and self.failure_code is not None
        else:
            valid_facts = not self.output_artifact_ids and self.failure_code is None
        if not valid_facts:
            _reject()


@runtime_checkable
class VideoEditingProvider(Protocol):
    """The only editing-provider capability surface the domain layer knows."""

    async def capabilities(self) -> EditingProviderCapabilities:
        """Return the provider's declared, provider-neutral capabilities."""
        ...

    async def validate(self, timeline: EditingTimeline) -> None:
        """Raise `unsupported_capability` instead of silently degrading."""
        ...

    async def submit(self, submission: EditingSubmission) -> EditingProviderJobSnapshot:
        """Submit once per idempotency key; replays return the original result."""
        ...

    async def get(self, editing_job_id: EditingJobId) -> EditingProviderJobSnapshot:
        """Return the current snapshot; unknown jobs raise `not_found`."""
        ...

    async def cancel(self, editing_job_id: EditingJobId) -> EditingProviderJobSnapshot:
        """Request cooperative cancellation; terminal outcomes stay unchanged."""
        ...

    async def fetch_artifacts(self, editing_job_id: EditingJobId) -> tuple[ArtifactId, ...]:
        """Return outputs of a confirmed success; otherwise raise `conflict`."""
        ...


@final
class VideoEditingProviderRegistry:
    """Explicit, fail-closed provider registry without runtime discovery."""

    __slots__ = ("_providers",)

    def __init__(self) -> None:
        self._providers: dict[EditingProviderId, VideoEditingProvider] = {}

    def register(self, provider_id: EditingProviderId, provider: VideoEditingProvider) -> None:
        if type(provider_id) is not EditingProviderId or not isinstance(
            provider, VideoEditingProvider
        ):
            _reject()
        if provider_id in self._providers:
            raise EditingProviderFailure(EditingProviderErrorCode.CONFLICT)
        self._providers[provider_id] = provider

    def resolve(self, provider_id: EditingProviderId) -> VideoEditingProvider:
        if type(provider_id) is not EditingProviderId:
            _reject()
        provider = self._providers.get(provider_id)
        if provider is None:
            raise EditingProviderFailure(EditingProviderErrorCode.NOT_FOUND)
        return provider

    def registered_provider_ids(self) -> tuple[EditingProviderId, ...]:
        return tuple(sorted(self._providers))


__all__ = [
    "MAX_EDITING_IDEMPOTENCY_KEY_CHARACTERS",
    "EditingIdempotencyKey",
    "EditingProviderCapabilities",
    "EditingProviderErrorCode",
    "EditingProviderFailure",
    "EditingProviderId",
    "EditingProviderJobSnapshot",
    "EditingSubmission",
    "InvalidEditingProviderModel",
    "VideoEditingProvider",
    "VideoEditingProviderRegistry",
    "editing_submission_idempotency_key",
]
