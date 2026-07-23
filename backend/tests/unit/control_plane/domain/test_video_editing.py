"""VE-01: provider-neutral standalone video editing domain contracts."""

from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

import automation_tool.control_plane.domain.video_editing as video_editing_module
from automation_tool.control_plane.domain import ArtifactId, TaskId
from automation_tool.control_plane.domain.resource_ids import InvalidResourceId, ResourceId
from automation_tool.control_plane.domain.video_creation import (
    MAX_VIDEO_DURATION_MS,
    TimelineClip,
    TimelineId,
    TimelineTrack,
    TimelineTrackKind,
    TimelineTransition,
    TransitionKind,
)
from automation_tool.control_plane.domain.video_editing import (
    EDITING_JOB_TERMINAL_STATUSES,
    MAX_EDITING_PROJECT_TITLE_CHARACTERS,
    MAX_EDITING_SOURCE_ARTIFACTS,
    EditingFailureCode,
    EditingJob,
    EditingJobId,
    EditingJobStateMachine,
    EditingJobStatus,
    EditingProject,
    EditingProjectId,
    EditingTimeline,
    InvalidEditingJobTransition,
    InvalidVideoEditingModel,
)

NOW = datetime(2026, 7, 23, 0, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=5)

_EXPECTED_TRANSITIONS: dict[EditingJobStatus, frozenset[EditingJobStatus]] = {
    EditingJobStatus.QUEUED: frozenset(
        {
            EditingJobStatus.RUNNING,
            EditingJobStatus.CANCELLING,
            EditingJobStatus.FAILED,
        }
    ),
    EditingJobStatus.RUNNING: frozenset(
        {
            EditingJobStatus.PAUSED,
            EditingJobStatus.CANCELLING,
            EditingJobStatus.SUCCEEDED,
            EditingJobStatus.FAILED,
            EditingJobStatus.OUTCOME_UNCERTAIN,
        }
    ),
    EditingJobStatus.PAUSED: frozenset(
        {
            EditingJobStatus.RUNNING,
            EditingJobStatus.CANCELLING,
        }
    ),
    EditingJobStatus.CANCELLING: frozenset(
        {
            EditingJobStatus.SUCCEEDED,
            EditingJobStatus.FAILED,
            EditingJobStatus.CANCELLED,
            EditingJobStatus.OUTCOME_UNCERTAIN,
        }
    ),
    EditingJobStatus.SUCCEEDED: frozenset(),
    EditingJobStatus.FAILED: frozenset(),
    EditingJobStatus.CANCELLED: frozenset(),
    EditingJobStatus.OUTCOME_UNCERTAIN: frozenset(),
}


def _source_artifact_ids(count: int = 1) -> tuple[ArtifactId, ...]:
    return tuple(ArtifactId.new() for _ in range(count))


def _project(**overrides: object) -> EditingProject:
    values: dict[str, object] = {
        "project_id": EditingProjectId.new(),
        "title": "新品发布会开场视频剪辑",
        "source_artifact_ids": _source_artifact_ids(),
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return EditingProject(**values)  # type: ignore[arg-type]


def _visual_track(source: ArtifactId, *, duration_ms: int = 30_000) -> TimelineTrack:
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
                transition_in=TimelineTransition(kind=TransitionKind.FADE, duration_ms=300),
            ),
        ),
    )


def _caption_track() -> TimelineTrack:
    return TimelineTrack(
        track_id="caption-main",
        kind=TimelineTrackKind.CAPTION,
        clips=(
            TimelineClip(
                clip_id="caption-1",
                start_ms=0,
                duration_ms=8_000,
                source_artifact_id=None,
                text="欢迎来到发布会",
                transition_in=None,
            ),
        ),
    )


def _audio_track(source: ArtifactId) -> TimelineTrack:
    return TimelineTrack(
        track_id="audio-main",
        kind=TimelineTrackKind.AUDIO,
        clips=(
            TimelineClip(
                clip_id="audio-1",
                start_ms=0,
                duration_ms=30_000,
                source_artifact_id=source,
                text=None,
                transition_in=None,
            ),
        ),
    )


def _timeline(**overrides: object) -> EditingTimeline:
    visual_source = ArtifactId.new()
    audio_source = ArtifactId.new()
    values: dict[str, object] = {
        "timeline_id": TimelineId.new(),
        "project_id": EditingProjectId.new(),
        "revision": 1,
        "duration_ms": 30_000,
        "tracks": (
            _visual_track(visual_source),
            _audio_track(audio_source),
            _caption_track(),
        ),
        "created_at": NOW,
    }
    values.update(overrides)
    return EditingTimeline(**values)  # type: ignore[arg-type]


def _job(**overrides: object) -> EditingJob:
    values: dict[str, object] = {
        "editing_job_id": EditingJobId.new(),
        "project_id": EditingProjectId.new(),
        "timeline_id": TimelineId.new(),
        "timeline_revision": 1,
        "status": EditingJobStatus.QUEUED,
        "input_artifact_ids": _source_artifact_ids(),
        "output_artifact_ids": (),
        "failure_code": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return EditingJob(**values)  # type: ignore[arg-type]


class TestEditingIdentifiers:
    def test_ids_reuse_the_single_resource_id_implementation(self) -> None:
        assert issubclass(EditingProjectId, ResourceId)
        assert issubclass(EditingJobId, ResourceId)

    def test_ids_reject_non_uuid4_and_foreign_id_types(self) -> None:
        with pytest.raises(InvalidResourceId):
            EditingProjectId.parse("not-a-uuid")
        with pytest.raises(InvalidResourceId):
            EditingJobId.parse(TaskId.new())
        project_id = EditingProjectId.new()
        assert EditingProjectId.parse(str(project_id)) == project_id

    def test_module_does_not_define_a_second_artifact_or_timeline_id(self) -> None:
        assert not hasattr(video_editing_module, "Artifact")
        assert not hasattr(video_editing_module, "EditingTimelineId")
        assert not hasattr(video_editing_module, "EditingArtifactId")
        assert not hasattr(video_editing_module, "EditingArtifact")
        assert getattr(video_editing_module, "ArtifactId") is ArtifactId  # noqa: B009
        assert getattr(video_editing_module, "TimelineId") is TimelineId  # noqa: B009


class TestEditingProject:
    def test_valid_project_round_trip(self) -> None:
        source = _source_artifact_ids(2)
        project = _project(source_artifact_ids=source, updated_at=LATER)
        assert project.source_artifact_ids == source
        assert project.created_at == NOW
        assert project.updated_at == LATER

    def test_project_is_immutable(self) -> None:
        project = _project()
        with pytest.raises(FrozenInstanceError):
            project.title = "改名"  # type: ignore[misc]

    def test_project_has_exactly_the_provider_neutral_fields(self) -> None:
        assert [field.name for field in fields(EditingProject)] == [
            "project_id",
            "title",
            "source_artifact_ids",
            "created_at",
            "updated_at",
        ]

    def test_project_repr_and_error_do_not_leak_user_title(self) -> None:
        project = _project(title="内部机密品牌宣传片")
        assert "内部机密品牌宣传片" not in repr(project)
        error = InvalidVideoEditingModel()
        assert str(error) == "Video editing domain model is invalid"

    @pytest.mark.parametrize(
        "overrides",
        [
            {"project_id": TaskId.new()},
            {"project_id": str(EditingProjectId.new())},
            {"title": ""},
            {"title": " 前后有空格 "},
            {"title": "坏\x00标题"},
            {"title": "长" * (MAX_EDITING_PROJECT_TITLE_CHARACTERS + 1)},
            {"title": None},
            {"source_artifact_ids": [ArtifactId.new()]},
            {"source_artifact_ids": (str(ArtifactId.new()),)},
            {"source_artifact_ids": _source_artifact_ids(MAX_EDITING_SOURCE_ARTIFACTS + 1)},
            {"created_at": datetime(2026, 7, 23, 0, 0)},
            {"updated_at": NOW - timedelta(seconds=1)},
        ],
    )
    def test_invalid_projects_are_rejected(self, overrides: dict[str, object]) -> None:
        with pytest.raises(InvalidVideoEditingModel):
            _project(**overrides)

    def test_duplicate_source_artifacts_are_rejected(self) -> None:
        artifact_id = ArtifactId.new()
        with pytest.raises(InvalidVideoEditingModel):
            _project(source_artifact_ids=(artifact_id, artifact_id))


class TestEditingTimeline:
    def test_valid_timeline_reuses_shared_track_vocabulary(self) -> None:
        project = _project()
        timeline = _timeline(project_id=project.project_id)
        assert timeline.project_id == project.project_id
        assert isinstance(timeline.timeline_id, TimelineId)
        kinds = {track.kind for track in timeline.tracks}
        assert kinds == {
            TimelineTrackKind.VISUAL,
            TimelineTrackKind.AUDIO,
            TimelineTrackKind.CAPTION,
        }

    def test_timeline_is_immutable(self) -> None:
        timeline = _timeline()
        with pytest.raises(FrozenInstanceError):
            timeline.revision = 2  # type: ignore[misc]

    def test_timeline_has_exactly_the_provider_neutral_fields(self) -> None:
        assert [field.name for field in fields(EditingTimeline)] == [
            "timeline_id",
            "project_id",
            "revision",
            "duration_ms",
            "tracks",
            "created_at",
        ]

    @pytest.mark.parametrize(
        "overrides",
        [
            {"timeline_id": uuid4()},
            {"timeline_id": EditingProjectId.new()},
            {"project_id": TimelineId.new()},
            {"revision": 0},
            {"revision": True},
            {"duration_ms": 99},
            {"duration_ms": MAX_VIDEO_DURATION_MS + 1},
            {"tracks": ()},
            {"tracks": (_caption_track(),)},
            {"created_at": datetime(2026, 7, 23, 0, 0)},
        ],
    )
    def test_invalid_timelines_are_rejected(self, overrides: dict[str, object]) -> None:
        with pytest.raises(InvalidVideoEditingModel):
            _timeline(**overrides)

    def test_duplicate_track_ids_are_rejected(self) -> None:
        source = ArtifactId.new()
        with pytest.raises(InvalidVideoEditingModel):
            _timeline(tracks=(_visual_track(source), _visual_track(ArtifactId.new())))

    def test_clip_beyond_timeline_duration_is_rejected(self) -> None:
        source = ArtifactId.new()
        with pytest.raises(InvalidVideoEditingModel):
            _timeline(duration_ms=10_000, tracks=(_visual_track(source, duration_ms=30_000),))


class TestEditingJob:
    def test_valid_queued_job_round_trip(self) -> None:
        job = _job()
        assert job.status is EditingJobStatus.QUEUED
        assert job.output_artifact_ids == ()
        assert job.failure_code is None

    def test_succeeded_job_requires_outputs(self) -> None:
        succeeded = _job(
            status=EditingJobStatus.SUCCEEDED,
            output_artifact_ids=_source_artifact_ids(),
            updated_at=LATER,
        )
        assert succeeded.status is EditingJobStatus.SUCCEEDED

    def test_failed_job_requires_failure_code(self) -> None:
        failed = _job(
            status=EditingJobStatus.FAILED,
            failure_code=EditingFailureCode.EDITING_FAILED,
            updated_at=LATER,
        )
        assert failed.failure_code is EditingFailureCode.EDITING_FAILED

    def test_job_is_immutable(self) -> None:
        job = _job()
        with pytest.raises(FrozenInstanceError):
            job.status = EditingJobStatus.RUNNING  # type: ignore[misc]

    def test_job_has_exactly_the_provider_neutral_fields(self) -> None:
        assert [field.name for field in fields(EditingJob)] == [
            "editing_job_id",
            "project_id",
            "timeline_id",
            "timeline_revision",
            "status",
            "input_artifact_ids",
            "output_artifact_ids",
            "failure_code",
            "created_at",
            "updated_at",
        ]

    @pytest.mark.parametrize(
        "overrides",
        [
            {"editing_job_id": EditingProjectId.new()},
            {"project_id": EditingJobId.new()},
            {"timeline_id": str(TimelineId.new())},
            {"timeline_revision": 0},
            {"status": "running"},
            {"input_artifact_ids": ()},
            {"input_artifact_ids": _source_artifact_ids(300)},
            {"failure_code": "editing_failed"},
            {"created_at": datetime(2026, 7, 23, 0, 0)},
            {"updated_at": NOW - timedelta(seconds=1)},
            {"status": EditingJobStatus.QUEUED, "output_artifact_ids": _source_artifact_ids()},
            {
                "status": EditingJobStatus.RUNNING,
                "failure_code": EditingFailureCode.EDITING_FAILED,
            },
            {
                "status": EditingJobStatus.PAUSED,
                "output_artifact_ids": _source_artifact_ids(),
            },
            {"status": EditingJobStatus.SUCCEEDED},
            {
                "status": EditingJobStatus.SUCCEEDED,
                "output_artifact_ids": _source_artifact_ids(),
                "failure_code": EditingFailureCode.EDITING_FAILED,
            },
            {"status": EditingJobStatus.FAILED},
            {
                "status": EditingJobStatus.FAILED,
                "failure_code": EditingFailureCode.EDITING_FAILED,
                "output_artifact_ids": _source_artifact_ids(),
            },
            {
                "status": EditingJobStatus.CANCELLED,
                "failure_code": EditingFailureCode.EDITING_FAILED,
            },
            {
                "status": EditingJobStatus.OUTCOME_UNCERTAIN,
                "output_artifact_ids": _source_artifact_ids(),
            },
        ],
    )
    def test_invalid_jobs_are_rejected(self, overrides: dict[str, object]) -> None:
        with pytest.raises(InvalidVideoEditingModel):
            _job(**overrides)

    def test_input_and_output_artifacts_must_be_disjoint(self) -> None:
        shared = ArtifactId.new()
        with pytest.raises(InvalidVideoEditingModel):
            _job(
                status=EditingJobStatus.SUCCEEDED,
                input_artifact_ids=(shared,),
                output_artifact_ids=(shared,),
            )


class TestEditingJobStateMachine:
    def test_status_enum_is_closed(self) -> None:
        assert {status.value for status in EditingJobStatus} == {
            "queued",
            "running",
            "paused",
            "cancelling",
            "succeeded",
            "failed",
            "cancelled",
            "outcome_uncertain",
        }

    def test_terminal_statuses_are_exact(self) -> None:
        assert (
            frozenset(
                {
                    EditingJobStatus.SUCCEEDED,
                    EditingJobStatus.FAILED,
                    EditingJobStatus.CANCELLED,
                    EditingJobStatus.OUTCOME_UNCERTAIN,
                }
            )
            == EDITING_JOB_TERMINAL_STATUSES
        )
        assert EditingJobStateMachine.terminal_statuses() == EDITING_JOB_TERMINAL_STATUSES
        for status in EditingJobStatus:
            assert EditingJobStateMachine.is_terminal(status) is (
                status in EDITING_JOB_TERMINAL_STATUSES
            )
        assert EditingJobStateMachine.is_terminal("succeeded") is False

    def test_full_transition_matrix(self) -> None:
        assert set(_EXPECTED_TRANSITIONS) == set(EditingJobStatus)
        for current in EditingJobStatus:
            assert EditingJobStateMachine.allowed_targets(current) == _EXPECTED_TRANSITIONS[current]
            for target in EditingJobStatus:
                allowed = target in _EXPECTED_TRANSITIONS[current]
                assert EditingJobStateMachine.can_transition(current, target) is allowed
                if allowed:
                    assert EditingJobStateMachine.transition(current, target) is target
                else:
                    with pytest.raises(InvalidEditingJobTransition):
                        EditingJobStateMachine.transition(current, target)

    def test_terminal_statuses_accept_no_transition(self) -> None:
        for terminal in EDITING_JOB_TERMINAL_STATUSES:
            assert EditingJobStateMachine.allowed_targets(terminal) == frozenset()

    def test_state_machine_rejects_foreign_status_types(self) -> None:
        with pytest.raises(InvalidEditingJobTransition):
            EditingJobStateMachine.allowed_targets("queued")
        with pytest.raises(InvalidEditingJobTransition):
            EditingJobStateMachine.transition("queued", EditingJobStatus.RUNNING)
        with pytest.raises(InvalidEditingJobTransition):
            EditingJobStateMachine.transition(EditingJobStatus.QUEUED, "running")
        assert EditingJobStateMachine.can_transition("queued", EditingJobStatus.RUNNING) is False
        error = InvalidEditingJobTransition()
        assert str(error) == "Editing job state transition is invalid"

    def test_replace_cannot_bypass_status_fact_validation(self) -> None:
        job = _job()
        with pytest.raises(InvalidVideoEditingModel):
            replace(job, status=EditingJobStatus.SUCCEEDED)
