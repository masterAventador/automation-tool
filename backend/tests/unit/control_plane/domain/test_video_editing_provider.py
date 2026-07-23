"""VE-02: provider-neutral VideoEditingProvider port, registry, and contract suite."""

from abc import ABC, abstractmethod
from dataclasses import FrozenInstanceError, dataclass, fields, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import cast, final

import pytest

from automation_tool.control_plane.domain.resource_ids import ArtifactId
from automation_tool.control_plane.domain.video_creation import (
    MAX_TRACKS,
    MAX_VIDEO_DURATION_MS,
    TimelineClip,
    TimelineId,
    TimelineTrack,
    TimelineTrackKind,
    TimelineTransition,
    TransitionKind,
)
from automation_tool.control_plane.domain.video_editing import (
    EditingFailureCode,
    EditingJobId,
    EditingJobStateMachine,
    EditingJobStatus,
    EditingProjectId,
    EditingTimeline,
)
from automation_tool.control_plane.domain.video_editing_provider import (
    MAX_EDITING_IDEMPOTENCY_KEY_CHARACTERS,
    EditingIdempotencyKey,
    EditingProviderCapabilities,
    EditingProviderErrorCode,
    EditingProviderFailure,
    EditingProviderId,
    EditingProviderJobSnapshot,
    EditingSubmission,
    InvalidEditingProviderModel,
    VideoEditingProvider,
    VideoEditingProviderRegistry,
    editing_submission_idempotency_key,
)

NOW = datetime(2026, 7, 23, 0, 0, tzinfo=UTC)

SNAPSHOT_FIELD_NAMES = frozenset(
    {
        "provider_id",
        "editing_job_id",
        "status",
        "failure_code",
        "output_artifact_ids",
    }
)
CAPABILITIES_FIELD_NAMES = frozenset(
    {
        "provider_id",
        "supported_track_kinds",
        "supported_transition_kinds",
        "max_timeline_duration_ms",
        "max_tracks",
    }
)
VENDOR_FIELD_NAMES = (
    "vendor_job_id",
    "provider_payload",
    "region",
    "endpoint",
    "api_key",
    "access_key",
    "secret",
    "raw_response",
    "request_id",
    "aliyun_job_id",
)
FIXED_FAILURE_MESSAGES = {
    EditingProviderErrorCode.INVALID_INPUT: "Editing provider rejected invalid input",
    EditingProviderErrorCode.UNSUPPORTED_CAPABILITY: (
        "Editing provider does not support a requested capability"
    ),
    EditingProviderErrorCode.NOT_FOUND: "Editing provider resource was not found",
    EditingProviderErrorCode.CONFLICT: ("Editing provider request conflicts with existing state"),
    EditingProviderErrorCode.DEPENDENCY_UNAVAILABLE: ("Editing provider dependency is unavailable"),
    EditingProviderErrorCode.PROVIDER_ERROR: "Editing provider failed internally",
}


def _visual_track(
    source: ArtifactId,
    *,
    duration_ms: int = 30_000,
    transition: TimelineTransition | None = None,
) -> TimelineTrack:
    return TimelineTrack(
        track_id="visual-main",
        kind=TimelineTrackKind.VISUAL,
        clips=(
            TimelineClip(
                clip_id="visual-1",
                start_ms=0,
                duration_ms=duration_ms,
                source_artifact_id=source,
                text=None,
                transition_in=transition,
            ),
        ),
    )


def _audio_track(source: ArtifactId, *, duration_ms: int = 30_000) -> TimelineTrack:
    return TimelineTrack(
        track_id="audio-main",
        kind=TimelineTrackKind.AUDIO,
        clips=(
            TimelineClip(
                clip_id="audio-1",
                start_ms=0,
                duration_ms=duration_ms,
                source_artifact_id=source,
                text=None,
                transition_in=None,
            ),
        ),
    )


def _timeline(
    project_id: EditingProjectId,
    *,
    revision: int = 1,
    duration_ms: int = 30_000,
    tracks: tuple[TimelineTrack, ...] | None = None,
) -> EditingTimeline:
    if tracks is None:
        tracks = (_visual_track(ArtifactId.new(), duration_ms=duration_ms),)
    return EditingTimeline(
        timeline_id=TimelineId.new(),
        project_id=project_id,
        revision=revision,
        duration_ms=duration_ms,
        tracks=tracks,
        created_at=NOW,
    )


def _capabilities(**overrides: object) -> EditingProviderCapabilities:
    values: dict[str, object] = {
        "provider_id": EditingProviderId("fake_cloud_editor"),
        "supported_track_kinds": frozenset({TimelineTrackKind.VISUAL, TimelineTrackKind.CAPTION}),
        "supported_transition_kinds": frozenset({TransitionKind.CUT, TransitionKind.FADE}),
        "max_timeline_duration_ms": 120_000,
        "max_tracks": 8,
    }
    values.update(overrides)
    return EditingProviderCapabilities(**values)  # type: ignore[arg-type]


def _submission(
    project_id: EditingProjectId | None = None,
    *,
    editing_job_id: EditingJobId | None = None,
    timeline: EditingTimeline | None = None,
) -> EditingSubmission:
    anchored_project_id = project_id if project_id is not None else EditingProjectId.new()
    resolved_job_id = editing_job_id if editing_job_id is not None else EditingJobId.new()
    resolved_timeline = timeline if timeline is not None else _timeline(anchored_project_id)
    return EditingSubmission(
        editing_job_id=resolved_job_id,
        project_id=anchored_project_id,
        timeline=resolved_timeline,
        idempotency_key=editing_submission_idempotency_key(resolved_job_id),
    )


def _snapshot(**overrides: object) -> EditingProviderJobSnapshot:
    values: dict[str, object] = {
        "provider_id": EditingProviderId("fake_cloud_editor"),
        "editing_job_id": EditingJobId.new(),
        "status": EditingJobStatus.QUEUED,
        "failure_code": None,
        "output_artifact_ids": (),
    }
    values.update(overrides)
    return EditingProviderJobSnapshot(**values)  # type: ignore[arg-type]


def _assert_failure(error: EditingProviderFailure, code: EditingProviderErrorCode) -> None:
    assert type(error) is EditingProviderFailure
    assert error.code is code
    assert error.code in EditingProviderErrorCode
    assert str(error) == FIXED_FAILURE_MESSAGES[code]


class TestEditingProviderId:
    @pytest.mark.parametrize(
        "value",
        ["aliyun_ims", "fake_cloud_editor", "abc", "p0_" + "a" * 61],
    )
    def test_accepts_stable_lowercase_identifiers(self, value: str) -> None:
        provider_id = EditingProviderId(value)
        assert provider_id == value
        assert type(provider_id) is EditingProviderId

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "a",
            "ab",
            "Aliyun",
            "ALIYUN_IMS",
            "aliyun ims",
            "aliyun-ims!",
            "1aliyun",
            "_aliyun",
            "阿里云",
            "a" * 65,
            "aliyun.ims",
        ],
    )
    def test_rejects_unstable_identifiers(self, value: str) -> None:
        with pytest.raises(InvalidEditingProviderModel):
            EditingProviderId(value)

    def test_rejects_non_string_values(self) -> None:
        with pytest.raises(InvalidEditingProviderModel):
            EditingProviderId(cast(str, 123))


class TestEditingIdempotencyKey:
    @pytest.mark.parametrize(
        "value",
        [
            "editing-job:0b6f0e6c-6b7f-4d0a-9c48-2f8f1a3d5e7b",
            "a",
            "A9._:/-x",
            "a" * MAX_EDITING_IDEMPOTENCY_KEY_CHARACTERS,
        ],
    )
    def test_accepts_bounded_canonical_keys(self, value: str) -> None:
        key = EditingIdempotencyKey(value)
        assert key == value
        assert type(key) is EditingIdempotencyKey

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "-leading-separator",
            ":leading-colon",
            "space inside",
            "换行\n",
            "非ASCII键",
            "a" * (MAX_EDITING_IDEMPOTENCY_KEY_CHARACTERS + 1),
        ],
    )
    def test_rejects_unbounded_or_unsafe_keys(self, value: str) -> None:
        with pytest.raises(InvalidEditingProviderModel):
            EditingIdempotencyKey(value)

    def test_rejects_non_string_values(self) -> None:
        with pytest.raises(InvalidEditingProviderModel):
            EditingIdempotencyKey(cast(str, None))

    def test_submission_key_is_derived_and_canonical(self) -> None:
        editing_job_id = EditingJobId.new()
        key = editing_submission_idempotency_key(editing_job_id)
        assert type(key) is EditingIdempotencyKey
        assert key == f"editing-job:{editing_job_id}"
        assert editing_submission_idempotency_key(editing_job_id) == key

    def test_submission_key_rejects_foreign_identifiers(self) -> None:
        with pytest.raises(InvalidEditingProviderModel):
            editing_submission_idempotency_key(cast(EditingJobId, "editing-job:x"))


class TestEditingProviderErrorTaxonomy:
    def test_error_codes_are_closed(self) -> None:
        assert {code.value for code in EditingProviderErrorCode} == {
            "invalid_input",
            "unsupported_capability",
            "not_found",
            "conflict",
            "dependency_unavailable",
            "provider_error",
        }

    @pytest.mark.parametrize("code", list(EditingProviderErrorCode))
    def test_failure_carries_only_fixed_safe_message(self, code: EditingProviderErrorCode) -> None:
        failure = EditingProviderFailure(code)
        _assert_failure(failure, code)
        assert failure.args == (FIXED_FAILURE_MESSAGES[code],)

    def test_failure_rejects_values_outside_taxonomy(self) -> None:
        class OtherCode(StrEnum):
            VENDOR_THROTTLED = "vendor_throttled"

        with pytest.raises(InvalidEditingProviderModel):
            EditingProviderFailure(cast(EditingProviderErrorCode, OtherCode.VENDOR_THROTTLED))
        with pytest.raises(InvalidEditingProviderModel):
            EditingProviderFailure(cast(EditingProviderErrorCode, "provider_error"))


class TestEditingProviderCapabilities:
    def test_valid_capabilities_construct(self) -> None:
        capabilities = _capabilities()
        assert capabilities.provider_id == EditingProviderId("fake_cloud_editor")
        assert TimelineTrackKind.VISUAL in capabilities.supported_track_kinds

    def test_fields_are_exactly_provider_neutral(self) -> None:
        assert {field.name for field in fields(EditingProviderCapabilities)} == set(
            CAPABILITIES_FIELD_NAMES
        )
        capabilities = _capabilities()
        for vendor_field in VENDOR_FIELD_NAMES:
            assert not hasattr(capabilities, vendor_field)

    def test_capabilities_are_immutable(self) -> None:
        capabilities = _capabilities()
        with pytest.raises(FrozenInstanceError):
            replace(capabilities).__setattr__("max_tracks", 1)

    @pytest.mark.parametrize(
        "overrides",
        [
            {"provider_id": "fake_cloud_editor"},
            {"supported_track_kinds": frozenset()},
            {"supported_track_kinds": frozenset({TimelineTrackKind.AUDIO})},
            {"supported_track_kinds": {TimelineTrackKind.VISUAL}},
            {"supported_track_kinds": frozenset({"visual"})},
            {"supported_transition_kinds": {TransitionKind.CUT}},
            {"supported_transition_kinds": frozenset({"cut"})},
            {"max_timeline_duration_ms": 99},
            {"max_timeline_duration_ms": MAX_VIDEO_DURATION_MS + 1},
            {"max_timeline_duration_ms": True},
            {"max_tracks": 0},
            {"max_tracks": MAX_TRACKS + 1},
            {"max_tracks": True},
        ],
    )
    def test_invalid_capabilities_fail_closed(self, overrides: dict[str, object]) -> None:
        with pytest.raises(InvalidEditingProviderModel):
            _capabilities(**overrides)

    def test_supports_accepts_timeline_within_declared_limits(self) -> None:
        project_id = EditingProjectId.new()
        timeline = _timeline(
            project_id,
            tracks=(
                _visual_track(
                    ArtifactId.new(),
                    transition=TimelineTransition(kind=TransitionKind.FADE, duration_ms=300),
                ),
            ),
        )
        assert _capabilities().supports(timeline) is True

    def test_supports_rejects_undeclared_track_kind(self) -> None:
        project_id = EditingProjectId.new()
        timeline = _timeline(
            project_id,
            tracks=(_visual_track(ArtifactId.new()), _audio_track(ArtifactId.new())),
        )
        assert _capabilities().supports(timeline) is False

    def test_supports_rejects_undeclared_transition_kind(self) -> None:
        project_id = EditingProjectId.new()
        timeline = _timeline(
            project_id,
            tracks=(
                _visual_track(
                    ArtifactId.new(),
                    transition=TimelineTransition(kind=TransitionKind.WIPE, duration_ms=300),
                ),
            ),
        )
        assert _capabilities().supports(timeline) is False

    def test_supports_rejects_timeline_over_duration_limit(self) -> None:
        project_id = EditingProjectId.new()
        timeline = _timeline(project_id, duration_ms=120_001)
        assert _capabilities().supports(timeline) is False

    def test_supports_rejects_non_timeline_values(self) -> None:
        with pytest.raises(InvalidEditingProviderModel):
            _capabilities().supports(cast(EditingTimeline, object()))


class TestEditingSubmission:
    def test_valid_submission_constructs(self) -> None:
        submission = _submission()
        assert submission.idempotency_key == editing_submission_idempotency_key(
            submission.editing_job_id
        )

    def test_submission_is_immutable(self) -> None:
        submission = _submission()
        with pytest.raises(FrozenInstanceError):
            submission.__setattr__("idempotency_key", "editing-job:x")

    def test_rejects_idempotency_key_not_derived_from_job(self) -> None:
        project_id = EditingProjectId.new()
        with pytest.raises(InvalidEditingProviderModel):
            EditingSubmission(
                editing_job_id=EditingJobId.new(),
                project_id=project_id,
                timeline=_timeline(project_id),
                idempotency_key=editing_submission_idempotency_key(EditingJobId.new()),
            )

    def test_rejects_timeline_anchored_to_other_project(self) -> None:
        editing_job_id = EditingJobId.new()
        with pytest.raises(InvalidEditingProviderModel):
            EditingSubmission(
                editing_job_id=editing_job_id,
                project_id=EditingProjectId.new(),
                timeline=_timeline(EditingProjectId.new()),
                idempotency_key=editing_submission_idempotency_key(editing_job_id),
            )

    def test_rejects_wrong_identifier_types(self) -> None:
        project_id = EditingProjectId.new()
        editing_job_id = EditingJobId.new()
        with pytest.raises(InvalidEditingProviderModel):
            EditingSubmission(
                editing_job_id=cast(EditingJobId, project_id),
                project_id=project_id,
                timeline=_timeline(project_id),
                idempotency_key=editing_submission_idempotency_key(editing_job_id),
            )
        with pytest.raises(InvalidEditingProviderModel):
            EditingSubmission(
                editing_job_id=editing_job_id,
                project_id=project_id,
                timeline=_timeline(project_id),
                idempotency_key=cast(EditingIdempotencyKey, f"editing-job:{editing_job_id}"),
            )


class TestEditingProviderJobSnapshot:
    def test_fields_are_exactly_provider_neutral(self) -> None:
        assert {field.name for field in fields(EditingProviderJobSnapshot)} == set(
            SNAPSHOT_FIELD_NAMES
        )
        snapshot = _snapshot()
        for vendor_field in VENDOR_FIELD_NAMES:
            assert not hasattr(snapshot, vendor_field)

    def test_snapshot_is_immutable(self) -> None:
        snapshot = _snapshot()
        with pytest.raises(FrozenInstanceError):
            snapshot.__setattr__("status", EditingJobStatus.SUCCEEDED)

    def test_succeeded_snapshot_requires_outputs_without_failure(self) -> None:
        snapshot = _snapshot(
            status=EditingJobStatus.SUCCEEDED,
            output_artifact_ids=(ArtifactId.new(),),
        )
        assert snapshot.failure_code is None

    def test_failed_snapshot_requires_failure_without_outputs(self) -> None:
        snapshot = _snapshot(
            status=EditingJobStatus.FAILED,
            failure_code=EditingFailureCode.EDITING_FAILED,
        )
        assert snapshot.output_artifact_ids == ()

    @pytest.mark.parametrize(
        "overrides",
        [
            {"provider_id": "fake_cloud_editor"},
            {"editing_job_id": EditingProjectId.new()},
            {"status": "queued"},
            {"failure_code": "editing_failed"},
            {"failure_code": EditingFailureCode.EDITING_FAILED},
            {"status": EditingJobStatus.SUCCEEDED},
            {
                "status": EditingJobStatus.SUCCEEDED,
                "failure_code": EditingFailureCode.EDITING_FAILED,
                "output_artifact_ids": (ArtifactId.new(),),
            },
            {"status": EditingJobStatus.FAILED},
            {
                "status": EditingJobStatus.FAILED,
                "failure_code": EditingFailureCode.EDITING_FAILED,
                "output_artifact_ids": (ArtifactId.new(),),
            },
            {"output_artifact_ids": (ArtifactId.new(),)},
            {"output_artifact_ids": [ArtifactId.new()]},
        ],
    )
    def test_invalid_snapshots_fail_closed(self, overrides: dict[str, object]) -> None:
        with pytest.raises(InvalidEditingProviderModel):
            _snapshot(**overrides)

    def test_duplicate_outputs_fail_closed(self) -> None:
        output = ArtifactId.new()
        with pytest.raises(InvalidEditingProviderModel):
            _snapshot(
                status=EditingJobStatus.SUCCEEDED,
                output_artifact_ids=(output, output),
            )


@final
@dataclass
class _FakeJob:
    submission: EditingSubmission
    status: EditingJobStatus
    failure_code: EditingFailureCode | None
    output_artifact_ids: tuple[ArtifactId, ...]


@final
class FakeEditingProvider:
    """In-memory provider proving the contract binds no vendor implementation."""

    def __init__(self) -> None:
        self._capabilities = _capabilities()
        self._jobs: dict[EditingJobId, _FakeJob] = {}

    async def capabilities(self) -> EditingProviderCapabilities:
        return self._capabilities

    async def validate(self, timeline: EditingTimeline) -> None:
        if not self._capabilities.supports(timeline):
            raise EditingProviderFailure(EditingProviderErrorCode.UNSUPPORTED_CAPABILITY)

    async def submit(self, submission: EditingSubmission) -> EditingProviderJobSnapshot:
        await self.validate(submission.timeline)
        existing = self._jobs.get(submission.editing_job_id)
        if existing is not None:
            if existing.submission != submission:
                raise EditingProviderFailure(EditingProviderErrorCode.CONFLICT)
            return self._snapshot_of(existing)
        job = _FakeJob(
            submission=submission,
            status=EditingJobStatus.QUEUED,
            failure_code=None,
            output_artifact_ids=(),
        )
        self._jobs[submission.editing_job_id] = job
        return self._snapshot_of(job)

    async def get(self, editing_job_id: EditingJobId) -> EditingProviderJobSnapshot:
        return self._snapshot_of(self._require_job(editing_job_id))

    async def cancel(self, editing_job_id: EditingJobId) -> EditingProviderJobSnapshot:
        job = self._require_job(editing_job_id)
        if not EditingJobStateMachine.is_terminal(job.status):
            job.status = EditingJobStateMachine.transition(job.status, EditingJobStatus.CANCELLING)
        return self._snapshot_of(job)

    async def fetch_artifacts(self, editing_job_id: EditingJobId) -> tuple[ArtifactId, ...]:
        job = self._require_job(editing_job_id)
        if job.status is not EditingJobStatus.SUCCEEDED:
            raise EditingProviderFailure(EditingProviderErrorCode.CONFLICT)
        return job.output_artifact_ids

    def force_running(self, editing_job_id: EditingJobId) -> None:
        job = self._require_job(editing_job_id)
        job.status = EditingJobStateMachine.transition(job.status, EditingJobStatus.RUNNING)

    def force_succeeded(self, editing_job_id: EditingJobId) -> None:
        job = self._require_job(editing_job_id)
        job.status = EditingJobStateMachine.transition(job.status, EditingJobStatus.SUCCEEDED)
        job.output_artifact_ids = (ArtifactId.new(),)

    def _require_job(self, editing_job_id: EditingJobId) -> _FakeJob:
        if not isinstance(editing_job_id, EditingJobId):
            raise EditingProviderFailure(EditingProviderErrorCode.INVALID_INPUT)
        job = self._jobs.get(editing_job_id)
        if job is None:
            raise EditingProviderFailure(EditingProviderErrorCode.NOT_FOUND)
        return job

    def _snapshot_of(self, job: _FakeJob) -> EditingProviderJobSnapshot:
        return EditingProviderJobSnapshot(
            provider_id=self._capabilities.provider_id,
            editing_job_id=job.submission.editing_job_id,
            status=job.status,
            failure_code=job.failure_code,
            output_artifact_ids=job.output_artifact_ids,
        )


class VideoEditingProviderContract(ABC):
    """Behavioral contract every VideoEditingProvider implementation must pass."""

    @abstractmethod
    def make_provider(self) -> VideoEditingProvider: ...

    @abstractmethod
    def make_supported_timeline(self, project_id: EditingProjectId) -> EditingTimeline: ...

    @abstractmethod
    def make_unsupported_timeline(self, project_id: EditingProjectId) -> EditingTimeline: ...

    @abstractmethod
    async def drive_to_running(
        self, provider: VideoEditingProvider, editing_job_id: EditingJobId
    ) -> None: ...

    @abstractmethod
    async def drive_to_success(
        self, provider: VideoEditingProvider, editing_job_id: EditingJobId
    ) -> None: ...

    def _supported_submission(self) -> EditingSubmission:
        project_id = EditingProjectId.new()
        return _submission(project_id, timeline=self.make_supported_timeline(project_id))

    @pytest.mark.asyncio
    async def test_provider_satisfies_the_port_protocol(self) -> None:
        assert isinstance(self.make_provider(), VideoEditingProvider)

    @pytest.mark.asyncio
    async def test_capabilities_are_exactly_provider_neutral(self) -> None:
        capabilities = await self.make_provider().capabilities()
        assert type(capabilities) is EditingProviderCapabilities
        assert {field.name for field in fields(capabilities)} == set(CAPABILITIES_FIELD_NAMES)
        for vendor_field in VENDOR_FIELD_NAMES:
            assert not hasattr(capabilities, vendor_field)

    @pytest.mark.asyncio
    async def test_validate_accepts_supported_timeline(self) -> None:
        provider = self.make_provider()
        project_id = EditingProjectId.new()
        await provider.validate(self.make_supported_timeline(project_id))

    @pytest.mark.asyncio
    async def test_validate_rejects_unsupported_capability_loudly(self) -> None:
        provider = self.make_provider()
        project_id = EditingProjectId.new()
        with pytest.raises(EditingProviderFailure) as excinfo:
            await provider.validate(self.make_unsupported_timeline(project_id))
        _assert_failure(excinfo.value, EditingProviderErrorCode.UNSUPPORTED_CAPABILITY)

    @pytest.mark.asyncio
    async def test_submit_returns_initial_provider_neutral_snapshot(self) -> None:
        provider = self.make_provider()
        submission = self._supported_submission()
        snapshot = await provider.submit(submission)
        assert type(snapshot) is EditingProviderJobSnapshot
        assert snapshot.editing_job_id == submission.editing_job_id
        assert snapshot.provider_id == (await provider.capabilities()).provider_id
        assert snapshot.status in {EditingJobStatus.QUEUED, EditingJobStatus.RUNNING}
        assert snapshot.failure_code is None
        assert snapshot.output_artifact_ids == ()
        for vendor_field in VENDOR_FIELD_NAMES:
            assert not hasattr(snapshot, vendor_field)

    @pytest.mark.asyncio
    async def test_submit_replay_with_same_key_returns_original_result(self) -> None:
        provider = self.make_provider()
        submission = self._supported_submission()
        first = await provider.submit(submission)
        replay = await provider.submit(submission)
        assert replay == first
        assert await provider.get(submission.editing_job_id) == first

    @pytest.mark.asyncio
    async def test_submit_conflicts_on_same_key_with_different_content(self) -> None:
        provider = self.make_provider()
        project_id = EditingProjectId.new()
        submission = _submission(project_id, timeline=self.make_supported_timeline(project_id))
        await provider.submit(submission)
        changed = EditingSubmission(
            editing_job_id=submission.editing_job_id,
            project_id=project_id,
            timeline=replace(self.make_supported_timeline(project_id), revision=2),
            idempotency_key=submission.idempotency_key,
        )
        with pytest.raises(EditingProviderFailure) as excinfo:
            await provider.submit(changed)
        _assert_failure(excinfo.value, EditingProviderErrorCode.CONFLICT)

    @pytest.mark.asyncio
    async def test_submit_rejects_unsupported_timeline_without_side_effects(self) -> None:
        provider = self.make_provider()
        project_id = EditingProjectId.new()
        submission = _submission(project_id, timeline=self.make_unsupported_timeline(project_id))
        with pytest.raises(EditingProviderFailure) as excinfo:
            await provider.submit(submission)
        _assert_failure(excinfo.value, EditingProviderErrorCode.UNSUPPORTED_CAPABILITY)
        with pytest.raises(EditingProviderFailure) as get_excinfo:
            await provider.get(submission.editing_job_id)
        _assert_failure(get_excinfo.value, EditingProviderErrorCode.NOT_FOUND)

    @pytest.mark.asyncio
    async def test_get_unknown_job_is_not_found(self) -> None:
        provider = self.make_provider()
        with pytest.raises(EditingProviderFailure) as excinfo:
            await provider.get(EditingJobId.new())
        _assert_failure(excinfo.value, EditingProviderErrorCode.NOT_FOUND)

    @pytest.mark.asyncio
    async def test_get_only_reports_legal_status_transitions(self) -> None:
        provider = self.make_provider()
        submission = self._supported_submission()
        before = await provider.submit(submission)
        await self.drive_to_running(provider, submission.editing_job_id)
        after = await provider.get(submission.editing_job_id)
        assert after.status is EditingJobStatus.RUNNING
        assert before.status is after.status or EditingJobStateMachine.can_transition(
            before.status, after.status
        )

    @pytest.mark.asyncio
    async def test_cancel_is_cooperative_not_instant(self) -> None:
        provider = self.make_provider()
        submission = self._supported_submission()
        await provider.submit(submission)
        snapshot = await provider.cancel(submission.editing_job_id)
        assert snapshot.status is EditingJobStatus.CANCELLING or EditingJobStateMachine.is_terminal(
            snapshot.status
        )

    @pytest.mark.asyncio
    async def test_cancel_unknown_job_is_not_found(self) -> None:
        provider = self.make_provider()
        with pytest.raises(EditingProviderFailure) as excinfo:
            await provider.cancel(EditingJobId.new())
        _assert_failure(excinfo.value, EditingProviderErrorCode.NOT_FOUND)

    @pytest.mark.asyncio
    async def test_cancel_after_success_keeps_the_terminal_outcome(self) -> None:
        provider = self.make_provider()
        submission = self._supported_submission()
        await provider.submit(submission)
        await self.drive_to_success(provider, submission.editing_job_id)
        snapshot = await provider.cancel(submission.editing_job_id)
        assert snapshot.status is EditingJobStatus.SUCCEEDED
        assert snapshot.output_artifact_ids

    @pytest.mark.asyncio
    async def test_fetch_artifacts_requires_confirmed_success(self) -> None:
        provider = self.make_provider()
        submission = self._supported_submission()
        await provider.submit(submission)
        with pytest.raises(EditingProviderFailure) as excinfo:
            await provider.fetch_artifacts(submission.editing_job_id)
        _assert_failure(excinfo.value, EditingProviderErrorCode.CONFLICT)
        await self.drive_to_success(provider, submission.editing_job_id)
        artifacts = await provider.fetch_artifacts(submission.editing_job_id)
        snapshot = await provider.get(submission.editing_job_id)
        assert artifacts
        assert artifacts == snapshot.output_artifact_ids
        assert all(type(artifact) is ArtifactId for artifact in artifacts)

    @pytest.mark.asyncio
    async def test_fetch_artifacts_unknown_job_is_not_found(self) -> None:
        provider = self.make_provider()
        with pytest.raises(EditingProviderFailure) as excinfo:
            await provider.fetch_artifacts(EditingJobId.new())
        _assert_failure(excinfo.value, EditingProviderErrorCode.NOT_FOUND)


@final
class TestFakeEditingProviderContract(VideoEditingProviderContract):
    """The fake second provider must pass the identical contract suite."""

    def make_provider(self) -> VideoEditingProvider:
        return FakeEditingProvider()

    def make_supported_timeline(self, project_id: EditingProjectId) -> EditingTimeline:
        return _timeline(
            project_id,
            tracks=(
                _visual_track(
                    ArtifactId.new(),
                    transition=TimelineTransition(kind=TransitionKind.FADE, duration_ms=300),
                ),
            ),
        )

    def make_unsupported_timeline(self, project_id: EditingProjectId) -> EditingTimeline:
        return _timeline(
            project_id,
            tracks=(_visual_track(ArtifactId.new()), _audio_track(ArtifactId.new())),
        )

    async def drive_to_running(
        self, provider: VideoEditingProvider, editing_job_id: EditingJobId
    ) -> None:
        assert isinstance(provider, FakeEditingProvider)
        provider.force_running(editing_job_id)

    async def drive_to_success(
        self, provider: VideoEditingProvider, editing_job_id: EditingJobId
    ) -> None:
        assert isinstance(provider, FakeEditingProvider)
        provider.force_running(editing_job_id)
        provider.force_succeeded(editing_job_id)


class TestVideoEditingProviderRegistry:
    def test_register_and_resolve_round_trip(self) -> None:
        registry = VideoEditingProviderRegistry()
        provider = FakeEditingProvider()
        provider_id = EditingProviderId("fake_cloud_editor")
        registry.register(provider_id, provider)
        assert registry.resolve(provider_id) is provider
        assert registry.registered_provider_ids() == (provider_id,)

    def test_registered_ids_are_stable_and_sorted(self) -> None:
        registry = VideoEditingProviderRegistry()
        second = EditingProviderId("second_editor")
        first = EditingProviderId("first_editor")
        registry.register(second, FakeEditingProvider())
        registry.register(first, FakeEditingProvider())
        assert registry.registered_provider_ids() == (first, second)

    def test_duplicate_registration_is_a_conflict(self) -> None:
        registry = VideoEditingProviderRegistry()
        provider_id = EditingProviderId("fake_cloud_editor")
        registry.register(provider_id, FakeEditingProvider())
        with pytest.raises(EditingProviderFailure) as excinfo:
            registry.register(provider_id, FakeEditingProvider())
        _assert_failure(excinfo.value, EditingProviderErrorCode.CONFLICT)
        assert registry.registered_provider_ids() == (provider_id,)

    def test_resolve_unknown_provider_is_not_found(self) -> None:
        registry = VideoEditingProviderRegistry()
        with pytest.raises(EditingProviderFailure) as excinfo:
            registry.resolve(EditingProviderId("fake_cloud_editor"))
        _assert_failure(excinfo.value, EditingProviderErrorCode.NOT_FOUND)

    def test_raw_string_identifiers_fail_closed(self) -> None:
        registry = VideoEditingProviderRegistry()
        registry.register(EditingProviderId("fake_cloud_editor"), FakeEditingProvider())
        with pytest.raises(InvalidEditingProviderModel):
            registry.resolve(cast(EditingProviderId, "fake_cloud_editor"))
        with pytest.raises(InvalidEditingProviderModel):
            registry.register(cast(EditingProviderId, "other_editor"), FakeEditingProvider())

    def test_non_conforming_objects_cannot_register(self) -> None:
        registry = VideoEditingProviderRegistry()
        with pytest.raises(InvalidEditingProviderModel):
            registry.register(
                EditingProviderId("fake_cloud_editor"),
                cast(VideoEditingProvider, object()),
            )

    def test_registry_has_no_runtime_discovery_surface(self) -> None:
        public_surface = {
            name for name in dir(VideoEditingProviderRegistry) if not name.startswith("_")
        }
        assert public_surface == {"register", "resolve", "registered_provider_ids"}
