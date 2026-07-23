"""A fake second editing provider proving the neutral port is replaceable.

This adapter deliberately differs from the first-phase cloud adapter in its
capability matrix (it accepts the dissolve transition and rejects the wipe
transition) and in its execution model (a purely in-process job table driven
by an explicit `complete_job` call). It still passes the identical VE-08
conformance suite, which is the executable proof that a future vendor adapter
can be added without touching the domain layer or the pages.

The module speaks only the neutral vocabulary: no vendor names, endpoints,
regions or credentials appear here, and a static test enforces that.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, final

from automation_tool.control_plane.domain.resource_ids import ArtifactId
from automation_tool.control_plane.domain.video_creation import (
    MAX_TRACKS,
    MAX_VIDEO_DURATION_MS,
    TimelineTrackKind,
    TransitionKind,
)
from automation_tool.control_plane.domain.video_editing import (
    EditingFailureCode,
    EditingJobId,
    EditingJobStateMachine,
    EditingJobStatus,
    EditingTimeline,
)
from automation_tool.control_plane.domain.video_editing_provider import (
    EditingProviderCapabilities,
    EditingProviderErrorCode,
    EditingProviderFailure,
    EditingProviderId,
    EditingProviderJobSnapshot,
    EditingSubmission,
    InvalidEditingProviderModel,
)

FAKE_SECOND_EDITING_PROVIDER_ID: Final = EditingProviderId("fake_secondcloud")


def _fail(code: EditingProviderErrorCode) -> None:
    raise EditingProviderFailure(code)


@final
@dataclass(slots=True)
class _JobRecord:
    fingerprint: str
    status: EditingJobStatus
    failure_code: EditingFailureCode | None
    output_artifact_ids: tuple[ArtifactId, ...]


def _timeline_fingerprint(timeline: EditingTimeline) -> str:
    return repr(timeline)


@final
class FakeSecondEditingProvider:
    """In-process second provider satisfying `VideoEditingProvider`."""

    __slots__ = ("_jobs",)

    def __init__(self) -> None:
        self._jobs: dict[EditingJobId, _JobRecord] = {}

    async def capabilities(self) -> EditingProviderCapabilities:
        return EditingProviderCapabilities(
            provider_id=FAKE_SECOND_EDITING_PROVIDER_ID,
            supported_track_kinds=frozenset(
                {TimelineTrackKind.VISUAL, TimelineTrackKind.AUDIO, TimelineTrackKind.CAPTION}
            ),
            supported_transition_kinds=frozenset(
                {TransitionKind.CUT, TransitionKind.FADE, TransitionKind.DISSOLVE}
            ),
            max_timeline_duration_ms=MAX_VIDEO_DURATION_MS,
            max_tracks=MAX_TRACKS,
        )

    async def validate(self, timeline: EditingTimeline) -> None:
        if not isinstance(timeline, EditingTimeline):
            raise InvalidEditingProviderModel
        if not (await self.capabilities()).supports(timeline):
            _fail(EditingProviderErrorCode.UNSUPPORTED_CAPABILITY)

    async def submit(self, submission: EditingSubmission) -> EditingProviderJobSnapshot:
        if not isinstance(submission, EditingSubmission):
            raise InvalidEditingProviderModel
        await self.validate(submission.timeline)
        fingerprint = _timeline_fingerprint(submission.timeline)
        record = self._jobs.get(submission.editing_job_id)
        if record is not None:
            if record.fingerprint != fingerprint:
                _fail(EditingProviderErrorCode.CONFLICT)
            return self._snapshot_of(submission.editing_job_id, record)
        record = _JobRecord(
            fingerprint=fingerprint,
            status=EditingJobStatus.QUEUED,
            failure_code=None,
            output_artifact_ids=(),
        )
        self._jobs[submission.editing_job_id] = record
        return self._snapshot_of(submission.editing_job_id, record)

    async def get(self, editing_job_id: EditingJobId) -> EditingProviderJobSnapshot:
        return self._snapshot_of(editing_job_id, self._require_job(editing_job_id))

    async def cancel(self, editing_job_id: EditingJobId) -> EditingProviderJobSnapshot:
        record = self._require_job(editing_job_id)
        if not EditingJobStateMachine.is_terminal(record.status):
            record.status = EditingJobStateMachine.transition(
                record.status, EditingJobStatus.CANCELLING
            )
        return self._snapshot_of(editing_job_id, record)

    async def fetch_artifacts(self, editing_job_id: EditingJobId) -> tuple[ArtifactId, ...]:
        record = self._require_job(editing_job_id)
        if record.status is not EditingJobStatus.SUCCEEDED:
            _fail(EditingProviderErrorCode.CONFLICT)
        return record.output_artifact_ids

    async def complete_job(self, editing_job_id: EditingJobId) -> None:
        """Drive one queued or running job to its confirmed success terminal."""
        record = self._require_job(editing_job_id)
        if EditingJobStateMachine.is_terminal(record.status):
            return
        if record.status is EditingJobStatus.QUEUED:
            record.status = EditingJobStateMachine.transition(
                record.status, EditingJobStatus.RUNNING
            )
        record.status = EditingJobStateMachine.transition(
            record.status, EditingJobStatus.SUCCEEDED
        )
        record.failure_code = None
        record.output_artifact_ids = (ArtifactId.new(),)

    def _require_job(self, editing_job_id: EditingJobId) -> _JobRecord:
        if not isinstance(editing_job_id, EditingJobId):
            raise EditingProviderFailure(EditingProviderErrorCode.INVALID_INPUT)
        record = self._jobs.get(editing_job_id)
        if record is None:
            raise EditingProviderFailure(EditingProviderErrorCode.NOT_FOUND)
        return record

    def _snapshot_of(
        self, editing_job_id: EditingJobId, record: _JobRecord
    ) -> EditingProviderJobSnapshot:
        return EditingProviderJobSnapshot(
            provider_id=FAKE_SECOND_EDITING_PROVIDER_ID,
            editing_job_id=editing_job_id,
            status=record.status,
            failure_code=record.failure_code,
            output_artifact_ids=record.output_artifact_ids,
        )


__all__ = [
    "FAKE_SECOND_EDITING_PROVIDER_ID",
    "FakeSecondEditingProvider",
]
