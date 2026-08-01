"""App-session protected latest-timeline reads and immutable revision saves."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt

from automation_tool.control_plane.api.editing_errors import translate_editing_error
from automation_tool.control_plane.api.errors import (
    AppError,
    ErrorEnvelope,
    TimelineRevisionConflictDetails,
)
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.timelines import (
    InvalidTimelineQuery,
    TimelineRevisionConflict,
    TimelineService,
)
from automation_tool.control_plane.domain import (
    MAX_MATERIAL_DURATION_MS,
    InstallationId,
    InvalidResourceId,
    InvalidTimelineModel,
    MaterialId,
    OriginalAudioMode,
    Timeline,
    TimelineClip,
    TimelineTrack,
    TimelineTrackKind,
    TimelineTransition,
    TransitionKind,
)
from automation_tool.control_plane.domain.timeline import (
    MAX_CLIP_TEXT_CHARACTERS,
    MAX_CLIPS_PER_TRACK,
    MAX_GAIN_DB,
    MAX_TIMELINE_DURATION_MS,
    MAX_TRANSITION_DURATION_MS,
    MIN_GAIN_DB,
    MIN_TIMELINE_DURATION_MS,
)

router = APIRouter(
    prefix="/api/v1/editing-projects/{project_id}/timeline",
    tags=["editing-timelines"],
)


class EditingTimelineTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: TransitionKind
    duration_ms: StrictInt = Field(
        alias="durationMs",
        ge=1,
        le=MAX_TRANSITION_DURATION_MS,
    )

    def to_domain(self) -> TimelineTransition:
        return TimelineTransition(kind=self.kind, duration_ms=self.duration_ms)


class EditingTimelineClip(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clip_id: str = Field(alias="clipId", min_length=1, max_length=64, strict=True)
    start_ms: StrictInt = Field(alias="startMs", ge=0, le=MAX_TIMELINE_DURATION_MS)
    duration_ms: StrictInt = Field(
        alias="durationMs",
        ge=1,
        le=MAX_TIMELINE_DURATION_MS,
    )
    source_material_id: str | None = Field(
        alias="sourceMaterialId",
        strict=True,
    )
    source_in_ms: StrictInt | None = Field(
        alias="sourceInMs",
        ge=0,
        le=MAX_MATERIAL_DURATION_MS,
    )
    source_out_ms: StrictInt | None = Field(
        alias="sourceOutMs",
        ge=0,
        le=MAX_MATERIAL_DURATION_MS,
    )
    text: str | None = Field(
        max_length=MAX_CLIP_TEXT_CHARACTERS,
        strict=True,
    )
    gain_db: StrictFloat | None = Field(
        alias="gainDb",
        ge=MIN_GAIN_DB,
        le=MAX_GAIN_DB,
    )
    transition_in: EditingTimelineTransition | None = Field(alias="transitionIn")
    original_audio_mode: OriginalAudioMode | None = Field(
        alias="originalAudioMode",
        default=None,
    )

    def to_domain(self) -> TimelineClip:
        return TimelineClip(
            clip_id=self.clip_id,
            start_ms=self.start_ms,
            duration_ms=self.duration_ms,
            source_material_id=(
                None
                if self.source_material_id is None
                else MaterialId.parse(self.source_material_id)
            ),
            source_in_ms=self.source_in_ms,
            source_out_ms=self.source_out_ms,
            text=self.text,
            gain_db=self.gain_db,
            transition_in=(None if self.transition_in is None else self.transition_in.to_domain()),
            original_audio_mode=self.original_audio_mode,
        )


class EditingTimelineTrack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: str = Field(alias="trackId", min_length=1, max_length=64, strict=True)
    kind: TimelineTrackKind
    clips: list[EditingTimelineClip] = Field(
        min_length=1,
        max_length=MAX_CLIPS_PER_TRACK,
    )

    def to_domain(self) -> TimelineTrack:
        return TimelineTrack(
            track_id=self.track_id,
            kind=self.kind,
            clips=tuple(clip.to_domain() for clip in self.clips),
        )


class EditingTimelineSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_ms: StrictInt = Field(
        alias="durationMs",
        ge=MIN_TIMELINE_DURATION_MS,
        le=MAX_TIMELINE_DURATION_MS,
    )
    tracks: list[EditingTimelineTrack] = Field(
        min_length=1,
        max_length=len(TimelineTrackKind),
    )
    expected_revision: StrictInt | None = Field(
        alias="expectedRevision",
        default=None,
        ge=0,
    )


class EditingTimelineResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeline_id: str = Field(alias="timelineId")
    project_id: str = Field(alias="projectId")
    revision: int
    duration_ms: int = Field(alias="durationMs")
    tracks: list[EditingTimelineTrack]
    created_at: datetime = Field(alias="createdAt")


def _service(request: Request) -> TimelineService:
    service = request.app.state.timeline_service
    if not isinstance(service, TimelineService):
        raise AppError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="editing_timelines_unavailable",
            message="Editing timeline service is unavailable",
            retryable=True,
        )
    return service


def _validation_error() -> AppError:
    return AppError(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="validation",
        message="Request validation failed",
    )


def _transition_response(
    transition: TimelineTransition | None,
) -> EditingTimelineTransition | None:
    if transition is None:
        return None
    return EditingTimelineTransition(
        kind=transition.kind,
        durationMs=transition.duration_ms,
    )


def _clip_response(clip: TimelineClip) -> EditingTimelineClip:
    return EditingTimelineClip(
        clipId=clip.clip_id,
        startMs=clip.start_ms,
        durationMs=clip.duration_ms,
        sourceMaterialId=(
            None if clip.source_material_id is None else str(clip.source_material_id)
        ),
        sourceInMs=clip.source_in_ms,
        sourceOutMs=clip.source_out_ms,
        text=clip.text,
        gainDb=clip.gain_db,
        transitionIn=_transition_response(clip.transition_in),
        originalAudioMode=clip.original_audio_mode,
    )


def _track_response(track: TimelineTrack) -> EditingTimelineTrack:
    return EditingTimelineTrack(
        trackId=track.track_id,
        kind=track.kind,
        clips=[_clip_response(clip) for clip in track.clips],
    )


def _timeline_response(timeline: Timeline) -> EditingTimelineResponse:
    return EditingTimelineResponse(
        timelineId=str(timeline.timeline_id),
        projectId=str(timeline.project_id),
        revision=timeline.revision,
        durationMs=timeline.duration_ms,
        tracks=[_track_response(track) for track in timeline.tracks],
        createdAt=timeline.created_at,
    )


@router.get(
    "",
    response_model=EditingTimelineResponse,
    operation_id="getEditingProjectTimeline",
)
async def get_editing_project_timeline(
    project_id: str,
    response: Response,
    installation_id: Annotated[
        InstallationId,
        Depends(require_current_installation_access),
    ],
    service: Annotated[TimelineService, Depends(_service)],
) -> EditingTimelineResponse:
    response.headers["cache-control"] = "no-store"
    try:
        timeline = await service.get(
            project_id=project_id,
            installation_id=installation_id,
        )
    except InvalidTimelineQuery:
        raise _validation_error() from None
    except Exception as error:
        raise translate_editing_error(error) from None
    return _timeline_response(timeline)


@router.put(
    "",
    response_model=EditingTimelineResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="saveEditingProjectTimeline",
    responses={
        status.HTTP_409_CONFLICT: {
            "description": "Timeline revision conflict",
            "model": ErrorEnvelope,
        }
    },
)
async def save_editing_project_timeline(
    project_id: str,
    payload: EditingTimelineSaveRequest,
    response: Response,
    installation_id: Annotated[
        InstallationId,
        Depends(require_current_installation_access),
    ],
    service: Annotated[TimelineService, Depends(_service)],
) -> EditingTimelineResponse:
    response.headers["cache-control"] = "no-store"
    try:
        tracks = tuple(track.to_domain() for track in payload.tracks)
    except (InvalidResourceId, InvalidTimelineModel):
        raise _validation_error() from None
    try:
        timeline = await service.save(
            project_id=project_id,
            installation_id=installation_id,
            duration_ms=payload.duration_ms,
            tracks=tracks,
            expected_revision=payload.expected_revision,
        )
    except InvalidTimelineQuery:
        raise _validation_error() from None
    except TimelineRevisionConflict as error:
        raise AppError(
            status_code=status.HTTP_409_CONFLICT,
            code="timeline_revision_conflict",
            message=str(error),
            details=TimelineRevisionConflictDetails(
                kind="timeline_revision_conflict.v1",
                currentRevision=error.current_revision,
            ),
        ) from None
    except Exception as error:
        raise translate_editing_error(error) from None
    return _timeline_response(timeline)


__all__ = ["router"]
