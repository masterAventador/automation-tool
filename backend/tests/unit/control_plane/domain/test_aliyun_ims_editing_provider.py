"""VE-05: Aliyun IMS timeline compilation and single-dispatch job submission."""

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import final
from uuid import UUID

import pytest
import test_video_editing_provider as ve02

from automation_tool.control_plane.domain.aliyun_ims_editing_provider import (
    ALIYUN_IMS_EDITING_PROVIDER_ID,
    OUTPUT_OBJECT_KEY_PREFIX,
    AliyunEditingIntent,
    AliyunEditingIntentState,
    AliyunEditingOutputConfig,
    AliyunImsEditingProvider,
    AliyunImsTransportFailure,
    AliyunImsTransportFailureKind,
    AliyunOssBucketName,
    AliyunSubmitAcknowledgement,
    AliyunSubmitMediaProducingRequest,
    InMemoryAliyunEditingIntentStore,
    InvalidAliyunImsEditingProviderModel,
    acs3_signed_headers,
    aliyun_editing_capabilities,
    build_output_media_config,
    build_submit_request,
    compile_aliyun_timeline,
    editing_submit_request_hash,
)
from automation_tool.control_plane.domain.aliyun_ims_editing_staging import (
    AliyunImsEditingStagingContract,
    AliyunImsRegion,
    EditingServicePreflight,
    MediaStagingPlan,
    PreflightCheckStatus,
    StagingAsset,
    build_media_staging_plan,
    load_aliyun_ims_editing_staging_contract,
)
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
    EditingJobId,
    EditingJobStatus,
    EditingProjectId,
    EditingTimeline,
)
from automation_tool.control_plane.domain.video_editing_provider import (
    EditingProviderErrorCode,
    EditingProviderFailure,
    EditingSubmission,
    VideoEditingProvider,
    editing_submission_idempotency_key,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts/video/aliyun-ims-editing-staging.v1.json"
FIXTURE_PATH = (
    Path(__file__).resolve().parent / "fixtures/aliyun-ims-compiled-timeline-sample.v1.json"
)

NOW = datetime(2026, 7, 23, 0, 0, tzinfo=UTC)
BUCKET = AliyunOssBucketName("automation-tool-video-staging")
REGION = AliyunImsRegion.CN_BEIJING
FIXED_MODEL_MESSAGE = "Aliyun IMS editing provider value is invalid"

ARTIFACT_1 = ArtifactId(UUID("00000000-0000-4000-8000-000000000001"))
ARTIFACT_2 = ArtifactId(UUID("00000000-0000-4000-8000-000000000002"))
ARTIFACT_3 = ArtifactId(UUID("00000000-0000-4000-8000-000000000003"))
PROJECT_ID = EditingProjectId(UUID("00000000-0000-4000-8000-0000000000aa"))
TIMELINE_UUID = UUID("00000000-0000-4000-8000-0000000000bb")
JOB_UUID = UUID("00000000-0000-4000-8000-0000000000cc")


def _digest(artifact_id: ArtifactId) -> str:
    return hashlib.sha256(str(artifact_id).encode("ascii")).hexdigest()


@pytest.fixture(scope="module")
def contract() -> AliyunImsEditingStagingContract:
    return load_aliyun_ims_editing_staging_contract(CONTRACT_PATH)


def _clip(
    clip_id: str,
    *,
    start_ms: int,
    duration_ms: int,
    artifact: ArtifactId | None = None,
    text: str | None = None,
    transition: TimelineTransition | None = None,
) -> TimelineClip:
    return TimelineClip(
        clip_id=clip_id,
        start_ms=start_ms,
        duration_ms=duration_ms,
        source_artifact_id=artifact,
        text=text,
        transition_in=transition,
    )


def _visual_track(
    clips: Sequence[TimelineClip] | None = None, *, track_id: str = "visual-main"
) -> TimelineTrack:
    if clips is None:
        clips = (_clip("visual-1", start_ms=0, duration_ms=10_000, artifact=ARTIFACT_1),)
    return TimelineTrack(track_id=track_id, kind=TimelineTrackKind.VISUAL, clips=tuple(clips))


def _audio_track(clips: Sequence[TimelineClip]) -> TimelineTrack:
    return TimelineTrack(track_id="audio-main", kind=TimelineTrackKind.AUDIO, clips=tuple(clips))


def _caption_track(clips: Sequence[TimelineClip]) -> TimelineTrack:
    return TimelineTrack(
        track_id="caption-main", kind=TimelineTrackKind.CAPTION, clips=tuple(clips)
    )


def _timeline(
    tracks: Sequence[TimelineTrack] | None = None,
    *,
    duration_ms: int = 10_000,
    revision: int = 1,
    project_id: EditingProjectId = PROJECT_ID,
) -> EditingTimeline:
    if tracks is None:
        tracks = (_visual_track(),)
    return EditingTimeline(
        timeline_id=TimelineId(TIMELINE_UUID),
        project_id=project_id,
        revision=revision,
        duration_ms=duration_ms,
        tracks=tuple(tracks),
        created_at=NOW,
    )


def _plan_for(
    contract: AliyunImsEditingStagingContract,
    timeline: EditingTimeline,
    *,
    extensions: dict[str, str] | None = None,
) -> MediaStagingPlan:
    default_extensions = {
        TimelineTrackKind.VISUAL: ".mp4",
        TimelineTrackKind.AUDIO: ".mp3",
    }
    assets: dict[str, StagingAsset] = {}
    for track in timeline.tracks:
        for clip in track.clips:
            if clip.source_artifact_id is None:
                continue
            logical_id = str(clip.source_artifact_id)
            extension = (extensions or {}).get(logical_id) or default_extensions[track.kind]
            assets[logical_id] = StagingAsset(
                logical_id=logical_id,
                sha256_hex=_digest(clip.source_artifact_id),
                size_bytes=1024,
                extension=extension,
            )
    return build_media_staging_plan(
        contract=contract,
        service_region=REGION,
        bucket_region=REGION,
        assets=tuple(assets.values()),
    )


def _media_url(artifact: ArtifactId, extension: str) -> str:
    return (
        f"https://{BUCKET}.oss-{REGION.value}.aliyuncs.com/"
        f"editing-staging/v1/{_digest(artifact)}{extension}"
    )


def _compiled(
    contract: AliyunImsEditingStagingContract,
    timeline: EditingTimeline,
    *,
    extensions: dict[str, str] | None = None,
) -> dict[str, object]:
    plan = _plan_for(contract, timeline, extensions=extensions)
    document = compile_aliyun_timeline(timeline=timeline, staging_plan=plan, bucket=BUCKET)
    parsed: dict[str, object] = json.loads(document)
    return parsed


def _submission(timeline: EditingTimeline | None = None) -> EditingSubmission:
    resolved = timeline if timeline is not None else _timeline()
    job_id = EditingJobId(JOB_UUID)
    return EditingSubmission(
        editing_job_id=job_id,
        project_id=resolved.project_id,
        timeline=resolved,
        idempotency_key=editing_submission_idempotency_key(job_id),
    )


def _preflight(
    status: PreflightCheckStatus = PreflightCheckStatus.PASSED,
) -> EditingServicePreflight:
    return EditingServicePreflight(
        region=REGION,
        region_check=status,
        permission_check=status,
        quota_check=status,
    )


def _assert_failure(error: EditingProviderFailure, code: EditingProviderErrorCode) -> None:
    assert type(error) is EditingProviderFailure
    assert error.code is code
    message = str(error)
    assert str(BUCKET) not in message
    assert "editing-staging" not in message
    assert "sha256" not in message


@final
class _StaticPreflightSource:
    def __init__(self, preflight: EditingServicePreflight) -> None:
        self._preflight = preflight

    async def current(self) -> EditingServicePreflight:
        return self._preflight


@final
class _StaticPlanner:
    def __init__(self, contract: AliyunImsEditingStagingContract) -> None:
        self._contract = contract

    async def plan(self, timeline: EditingTimeline) -> MediaStagingPlan:
        return _plan_for(self._contract, timeline)


@final
class _ScriptedTransport:
    def __init__(
        self, outcomes: Sequence[AliyunSubmitAcknowledgement | AliyunImsTransportFailure] = ()
    ) -> None:
        self.requests: list[AliyunSubmitMediaProducingRequest] = []
        self._outcomes = list(outcomes)

    async def submit_media_producing_job(
        self, request: AliyunSubmitMediaProducingRequest
    ) -> AliyunSubmitAcknowledgement:
        self.requests.append(request)
        if self._outcomes:
            outcome = self._outcomes.pop(0)
            if isinstance(outcome, AliyunImsTransportFailure):
                raise outcome
            return outcome
        return AliyunSubmitAcknowledgement(
            vendor_job_id=f"job-{len(self.requests):032d}",
            request_id=f"request-{len(self.requests):016d}",
        )


def _provider(
    contract: AliyunImsEditingStagingContract,
    *,
    transport: _ScriptedTransport | None = None,
    preflight: EditingServicePreflight | None = None,
    store: InMemoryAliyunEditingIntentStore | None = None,
) -> tuple[AliyunImsEditingProvider, _ScriptedTransport, InMemoryAliyunEditingIntentStore]:
    resolved_transport = transport if transport is not None else _ScriptedTransport()
    resolved_store = store if store is not None else InMemoryAliyunEditingIntentStore()
    provider = AliyunImsEditingProvider(
        contract=contract,
        region=REGION,
        staging_bucket=BUCKET,
        output=AliyunEditingOutputConfig(width=1080, height=1920),
        preflight_source=_StaticPreflightSource(
            preflight if preflight is not None else _preflight()
        ),
        staging_planner=_StaticPlanner(contract),
        transport=resolved_transport,
        intent_store=resolved_store,
    )
    return provider, resolved_transport, resolved_store


class TestAliyunEditingCapabilities:
    def test_provider_id_is_stable(self) -> None:
        assert ALIYUN_IMS_EDITING_PROVIDER_ID == "aliyun_ims"
        assert aliyun_editing_capabilities().provider_id == ALIYUN_IMS_EDITING_PROVIDER_ID

    def test_all_internal_track_kinds_are_supported(self) -> None:
        assert aliyun_editing_capabilities().supported_track_kinds == frozenset(
            {TimelineTrackKind.VISUAL, TimelineTrackKind.AUDIO, TimelineTrackKind.CAPTION}
        )

    def test_dissolve_is_declared_unsupported(self) -> None:
        capabilities = aliyun_editing_capabilities()
        assert capabilities.supported_transition_kinds == frozenset(
            {TransitionKind.CUT, TransitionKind.FADE, TransitionKind.WIPE}
        )
        assert TransitionKind.DISSOLVE not in capabilities.supported_transition_kinds

    def test_limits_match_internal_timeline_bounds(self) -> None:
        capabilities = aliyun_editing_capabilities()
        assert capabilities.max_timeline_duration_ms == MAX_VIDEO_DURATION_MS
        assert capabilities.max_tracks == MAX_TRACKS


class TestAliyunOssBucketName:
    @pytest.mark.parametrize("value", ["automation-tool-video-staging", "abc", "a-1-b", "0" * 63])
    def test_accepts_official_bucket_names(self, value: str) -> None:
        assert AliyunOssBucketName(value) == value

    @pytest.mark.parametrize(
        "value",
        ["", "ab", "a" * 64, "-abc", "abc-", "UPPER", "under_score", "dot.name", "空"],
    )
    def test_rejects_invalid_bucket_names(self, value: str) -> None:
        with pytest.raises(InvalidAliyunImsEditingProviderModel) as excinfo:
            AliyunOssBucketName(value)
        assert str(excinfo.value) == FIXED_MODEL_MESSAGE


class TestAliyunEditingOutputConfig:
    @pytest.mark.parametrize(("width", "height"), [(128, 128), (1080, 1920), (3840, 2160)])
    def test_accepts_official_resolution_bounds(self, width: int, height: int) -> None:
        output = AliyunEditingOutputConfig(width=width, height=height)
        assert (output.width, output.height) == (width, height)

    @pytest.mark.parametrize(
        ("width", "height"),
        [(127, 128), (128, 127), (4097, 128), (128, 4097), (2161, 2161), (4096, 4096)],
    )
    def test_rejects_out_of_bound_resolutions(self, width: int, height: int) -> None:
        with pytest.raises(InvalidAliyunImsEditingProviderModel):
            AliyunEditingOutputConfig(width=width, height=height)

    def test_rejects_non_integer_values(self) -> None:
        with pytest.raises(InvalidAliyunImsEditingProviderModel):
            AliyunEditingOutputConfig(width=True, height=1920)


class TestCompileAliyunTimeline:
    def test_compile_is_deterministic(self, contract: AliyunImsEditingStagingContract) -> None:
        timeline = _timeline()
        plan = _plan_for(contract, timeline)
        first = compile_aliyun_timeline(timeline=timeline, staging_plan=plan, bucket=BUCKET)
        second = compile_aliyun_timeline(timeline=timeline, staging_plan=plan, bucket=BUCKET)
        assert first == second

    def test_visual_clip_maps_to_video_track_clip_in_seconds(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        parsed = _compiled(contract, _timeline())
        assert parsed == {
            "VideoTracks": [
                {
                    "VideoTrackClips": [
                        {
                            "MediaURL": _media_url(ARTIFACT_1, ".mp4"),
                            "TimelineIn": 0.0,
                            "TimelineOut": 10.0,
                        }
                    ]
                }
            ]
        }

    def test_fade_transition_lands_on_previous_clip_effects(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        timeline = _timeline(
            (
                _visual_track(
                    (
                        _clip("visual-1", start_ms=0, duration_ms=6_000, artifact=ARTIFACT_1),
                        _clip(
                            "visual-2",
                            start_ms=6_000,
                            duration_ms=4_000,
                            artifact=ARTIFACT_2,
                            transition=TimelineTransition(
                                kind=TransitionKind.FADE, duration_ms=300
                            ),
                        ),
                    )
                ),
            )
        )
        parsed = _compiled(contract, timeline)
        video_tracks = parsed["VideoTracks"]
        assert isinstance(video_tracks, list)
        clips = video_tracks[0]["VideoTrackClips"]
        assert clips[0]["Effects"] == [{"Type": "Transition", "SubType": "fade", "Duration": 0.3}]
        assert "Effects" not in clips[1]

    def test_wipe_transition_maps_to_official_wiperight(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        timeline = _timeline(
            (
                _visual_track(
                    (
                        _clip("visual-1", start_ms=0, duration_ms=6_000, artifact=ARTIFACT_1),
                        _clip(
                            "visual-2",
                            start_ms=6_000,
                            duration_ms=4_000,
                            artifact=ARTIFACT_2,
                            transition=TimelineTransition(
                                kind=TransitionKind.WIPE, duration_ms=1_000
                            ),
                        ),
                    )
                ),
            )
        )
        parsed = _compiled(contract, timeline)
        video_tracks = parsed["VideoTracks"]
        assert isinstance(video_tracks, list)
        effects = video_tracks[0]["VideoTrackClips"][0]["Effects"]
        assert effects == [{"Type": "Transition", "SubType": "wiperight", "Duration": 1.0}]

    def test_cut_transition_emits_no_effect(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        timeline = _timeline(
            (
                _visual_track(
                    (
                        _clip("visual-1", start_ms=0, duration_ms=6_000, artifact=ARTIFACT_1),
                        _clip(
                            "visual-2",
                            start_ms=6_000,
                            duration_ms=4_000,
                            artifact=ARTIFACT_2,
                            transition=TimelineTransition(kind=TransitionKind.CUT, duration_ms=1),
                        ),
                    )
                ),
            )
        )
        parsed = _compiled(contract, timeline)
        video_tracks = parsed["VideoTracks"]
        assert isinstance(video_tracks, list)
        for clip in video_tracks[0]["VideoTrackClips"]:
            assert "Effects" not in clip

    def test_audio_track_maps_to_audio_track_clips(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        timeline = _timeline(
            (
                _visual_track(),
                _audio_track(
                    (_clip("audio-1", start_ms=0, duration_ms=10_000, artifact=ARTIFACT_3),)
                ),
            )
        )
        parsed = _compiled(contract, timeline)
        assert parsed["AudioTracks"] == [
            {
                "AudioTrackClips": [
                    {
                        "MediaURL": _media_url(ARTIFACT_3, ".mp3"),
                        "TimelineIn": 0.0,
                        "TimelineOut": 10.0,
                    }
                ]
            }
        ]

    def test_caption_track_maps_to_subtitle_text_clips(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        timeline = _timeline(
            (
                _visual_track(),
                _caption_track(
                    (_clip("caption-1", start_ms=1_000, duration_ms=8_000, text="你好世界"),)
                ),
            )
        )
        parsed = _compiled(contract, timeline)
        assert parsed["SubtitleTracks"] == [
            {
                "SubtitleTrackClips": [
                    {
                        "Type": "Text",
                        "Content": "你好世界",
                        "TimelineIn": 1.0,
                        "TimelineOut": 9.0,
                    }
                ]
            }
        ]

    def test_image_material_emits_image_type_with_duration(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        timeline = _timeline(
            (
                _visual_track(
                    (_clip("visual-1", start_ms=0, duration_ms=10_000, artifact=ARTIFACT_1),)
                ),
            )
        )
        parsed = _compiled(contract, timeline, extensions={str(ARTIFACT_1): ".png"})
        video_tracks = parsed["VideoTracks"]
        assert isinstance(video_tracks, list)
        clip = video_tracks[0]["VideoTrackClips"][0]
        assert clip["Type"] == "Image"
        assert clip["Duration"] == 10.0
        assert clip["MediaURL"] == _media_url(ARTIFACT_1, ".png")

    def test_multiple_visual_tracks_preserve_declaration_order(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        timeline = _timeline(
            (
                _visual_track(
                    (_clip("visual-1", start_ms=0, duration_ms=10_000, artifact=ARTIFACT_1),),
                    track_id="visual-a",
                ),
                _visual_track(
                    (_clip("visual-2", start_ms=0, duration_ms=10_000, artifact=ARTIFACT_2),),
                    track_id="visual-b",
                ),
            )
        )
        parsed = _compiled(contract, timeline)
        video_tracks = parsed["VideoTracks"]
        assert isinstance(video_tracks, list)
        assert [track["VideoTrackClips"][0]["MediaURL"] for track in video_tracks] == [
            _media_url(ARTIFACT_1, ".mp4"),
            _media_url(ARTIFACT_2, ".mp4"),
        ]

    def test_dissolve_transition_is_rejected_before_submission(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        timeline = _timeline(
            (
                _visual_track(
                    (
                        _clip("visual-1", start_ms=0, duration_ms=6_000, artifact=ARTIFACT_1),
                        _clip(
                            "visual-2",
                            start_ms=6_000,
                            duration_ms=4_000,
                            artifact=ARTIFACT_2,
                            transition=TimelineTransition(
                                kind=TransitionKind.DISSOLVE, duration_ms=300
                            ),
                        ),
                    )
                ),
            )
        )
        with pytest.raises(EditingProviderFailure) as excinfo:
            _compiled(contract, timeline)
        _assert_failure(excinfo.value, EditingProviderErrorCode.UNSUPPORTED_CAPABILITY)

    def test_transition_into_first_clip_is_rejected(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        timeline = _timeline(
            (
                _visual_track(
                    (
                        _clip(
                            "visual-1",
                            start_ms=0,
                            duration_ms=10_000,
                            artifact=ARTIFACT_1,
                            transition=TimelineTransition(
                                kind=TransitionKind.FADE, duration_ms=300
                            ),
                        ),
                    )
                ),
            )
        )
        with pytest.raises(EditingProviderFailure) as excinfo:
            _compiled(contract, timeline)
        _assert_failure(excinfo.value, EditingProviderErrorCode.UNSUPPORTED_CAPABILITY)

    def test_transition_on_audio_clip_is_rejected(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        timeline = _timeline(
            (
                _visual_track(),
                _audio_track(
                    (
                        _clip("audio-1", start_ms=0, duration_ms=5_000, artifact=ARTIFACT_2),
                        _clip(
                            "audio-2",
                            start_ms=5_000,
                            duration_ms=5_000,
                            artifact=ARTIFACT_3,
                            transition=TimelineTransition(
                                kind=TransitionKind.FADE, duration_ms=300
                            ),
                        ),
                    )
                ),
            )
        )
        with pytest.raises(EditingProviderFailure) as excinfo:
            _compiled(contract, timeline)
        _assert_failure(excinfo.value, EditingProviderErrorCode.UNSUPPORTED_CAPABILITY)

    def test_transition_on_caption_clip_is_rejected(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        timeline = _timeline(
            (
                _visual_track(),
                _caption_track(
                    (
                        _clip("caption-1", start_ms=0, duration_ms=4_000, text="第一段"),
                        _clip(
                            "caption-2",
                            start_ms=4_000,
                            duration_ms=4_000,
                            text="第二段",
                            transition=TimelineTransition(
                                kind=TransitionKind.FADE, duration_ms=300
                            ),
                        ),
                    )
                ),
            )
        )
        with pytest.raises(EditingProviderFailure) as excinfo:
            _compiled(contract, timeline)
        _assert_failure(excinfo.value, EditingProviderErrorCode.UNSUPPORTED_CAPABILITY)

    def test_missing_staging_key_is_invalid_input(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        timeline = _timeline(
            (
                _visual_track(
                    (
                        _clip("visual-1", start_ms=0, duration_ms=6_000, artifact=ARTIFACT_1),
                        _clip("visual-2", start_ms=6_000, duration_ms=4_000, artifact=ARTIFACT_2),
                    )
                ),
            )
        )
        partial_plan = build_media_staging_plan(
            contract=contract,
            service_region=REGION,
            bucket_region=REGION,
            assets=(
                StagingAsset(
                    logical_id=str(ARTIFACT_1),
                    sha256_hex=_digest(ARTIFACT_1),
                    size_bytes=1024,
                    extension=".mp4",
                ),
            ),
        )
        with pytest.raises(EditingProviderFailure) as excinfo:
            compile_aliyun_timeline(timeline=timeline, staging_plan=partial_plan, bucket=BUCKET)
        _assert_failure(excinfo.value, EditingProviderErrorCode.INVALID_INPUT)

    def test_audio_extension_on_visual_track_is_invalid_input(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        timeline = _timeline()
        with pytest.raises(EditingProviderFailure) as excinfo:
            _compiled(contract, timeline, extensions={str(ARTIFACT_1): ".mp3"})
        _assert_failure(excinfo.value, EditingProviderErrorCode.INVALID_INPUT)

    def test_visual_extension_on_audio_track_is_invalid_input(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        timeline = _timeline(
            (
                _visual_track(),
                _audio_track(
                    (_clip("audio-1", start_ms=0, duration_ms=10_000, artifact=ARTIFACT_3),)
                ),
            )
        )
        with pytest.raises(EditingProviderFailure) as excinfo:
            _compiled(contract, timeline, extensions={str(ARTIFACT_3): ".png"})
        _assert_failure(excinfo.value, EditingProviderErrorCode.INVALID_INPUT)


class TestOfficialSampleFixtureReplay:
    def test_compiled_document_matches_committed_official_sample(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        timeline = _timeline(
            (
                _visual_track(
                    (
                        _clip("visual-1", start_ms=0, duration_ms=6_000, artifact=ARTIFACT_1),
                        _clip(
                            "visual-2",
                            start_ms=6_000,
                            duration_ms=4_000,
                            artifact=ARTIFACT_2,
                            transition=TimelineTransition(
                                kind=TransitionKind.FADE, duration_ms=500
                            ),
                        ),
                    )
                ),
                _audio_track(
                    (_clip("audio-1", start_ms=0, duration_ms=10_000, artifact=ARTIFACT_3),)
                ),
                _caption_track(
                    (_clip("caption-1", start_ms=1_000, duration_ms=8_000, text="你好世界"),)
                ),
            )
        )
        parsed = _compiled(contract, timeline, extensions={str(ARTIFACT_2): ".png"})
        expected = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        assert parsed == expected


class TestOutputMediaConfig:
    def test_output_media_config_targets_versioned_output_prefix(self) -> None:
        job_id = EditingJobId(JOB_UUID)
        document = build_output_media_config(
            bucket=BUCKET,
            region=REGION,
            editing_job_id=job_id,
            output=AliyunEditingOutputConfig(width=1080, height=1920),
        )
        assert json.loads(document) == {
            "MediaURL": (
                f"https://{BUCKET}.oss-{REGION.value}.aliyuncs.com/"
                f"{OUTPUT_OBJECT_KEY_PREFIX}{job_id}.mp4"
            ),
            "Width": 1080,
            "Height": 1920,
        }
        assert OUTPUT_OBJECT_KEY_PREFIX == "editing-output/v1/"


class TestSubmitRequestAndHash:
    def _request(
        self, contract: AliyunImsEditingStagingContract, submission: EditingSubmission
    ) -> AliyunSubmitMediaProducingRequest:
        plan = _plan_for(contract, submission.timeline)
        return build_submit_request(
            contract=contract,
            region=REGION,
            bucket=BUCKET,
            output=AliyunEditingOutputConfig(width=1080, height=1920),
            submission=submission,
            staging_plan=plan,
        )

    def test_request_uses_locked_service_facts(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        submission = _submission()
        request = self._request(contract, submission)
        assert request.action == "SubmitMediaProducingJob"
        assert request.api_version == "2020-11-09"
        assert request.endpoint == f"ice.{REGION.value}.aliyuncs.com"
        assert request.output_media_target == "oss-object"
        assert request.client_token == submission.idempotency_key

    def test_request_hash_is_deterministic_sha256(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        submission = _submission()
        first = editing_submit_request_hash(
            submission=submission, request=self._request(contract, submission)
        )
        second = editing_submit_request_hash(
            submission=submission, request=self._request(contract, submission)
        )
        assert first == second
        assert len(first) == 64
        assert set(first) <= set("0123456789abcdef")

    def test_request_hash_changes_with_timeline_content(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        base = _submission()
        changed_timeline = _timeline(
            (
                _visual_track(
                    (_clip("visual-1", start_ms=0, duration_ms=10_000, artifact=ARTIFACT_2),)
                ),
            )
        )
        changed = EditingSubmission(
            editing_job_id=base.editing_job_id,
            project_id=base.project_id,
            timeline=changed_timeline,
            idempotency_key=base.idempotency_key,
        )
        assert editing_submit_request_hash(
            submission=base, request=self._request(contract, base)
        ) != editing_submit_request_hash(
            submission=changed, request=self._request(contract, changed)
        )

    def test_request_hash_changes_with_timeline_revision(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        base = _submission()
        revised = EditingSubmission(
            editing_job_id=base.editing_job_id,
            project_id=base.project_id,
            timeline=_timeline(revision=2),
            idempotency_key=base.idempotency_key,
        )
        assert editing_submit_request_hash(
            submission=base, request=self._request(contract, base)
        ) != editing_submit_request_hash(
            submission=revised, request=self._request(contract, revised)
        )


class TestAcs3SignedHeaders:
    def _headers(self, *, secret: str = "test-secret") -> dict[str, str]:
        return dict(
            acs3_signed_headers(
                access_key_id="test-access-key-id",
                access_key_secret=secret,
                method="POST",
                host="ice.cn-beijing.aliyuncs.com",
                path="/",
                query={"Timeline": "{}", "OutputMediaTarget": "oss-object"},
                action="SubmitMediaProducingJob",
                api_version="2020-11-09",
                body=b"",
                timestamp="2026-07-23T00:00:00Z",
                nonce="0" * 32,
            )
        )

    def test_headers_are_deterministic_and_complete(self) -> None:
        first = self._headers()
        second = self._headers()
        assert first == second
        assert first["x-acs-action"] == "SubmitMediaProducingJob"
        assert first["x-acs-version"] == "2020-11-09"
        assert first["x-acs-date"] == "2026-07-23T00:00:00Z"
        assert first["x-acs-signature-nonce"] == "0" * 32
        assert first["x-acs-content-sha256"] == hashlib.sha256(b"").hexdigest()
        assert first["host"] == "ice.cn-beijing.aliyuncs.com"

    def test_authorization_follows_official_acs3_shape(self) -> None:
        authorization = self._headers()["Authorization"]
        assert authorization.startswith(
            "ACS3-HMAC-SHA256 Credential=test-access-key-id,SignedHeaders="
        )
        signed_headers = authorization.split("SignedHeaders=")[1].split(",Signature=")[0]
        assert signed_headers == (
            "host;x-acs-action;x-acs-content-sha256;x-acs-date;x-acs-signature-nonce;x-acs-version"
        )
        signature = authorization.split("Signature=")[1]
        assert len(signature) == 64
        assert set(signature) <= set("0123456789abcdef")

    def test_signature_depends_on_secret_and_secret_never_leaks(self) -> None:
        first = self._headers()
        other = self._headers(secret="another-secret")
        assert first["Authorization"] != other["Authorization"]
        for value in first.values():
            assert "test-secret" not in value


class TestAliyunEditingIntent:
    def _intent(self, **overrides: object) -> AliyunEditingIntent:
        values: dict[str, object] = {
            "editing_job_id": EditingJobId(JOB_UUID),
            "request_hash": "a" * 64,
            "state": AliyunEditingIntentState.PREPARED,
            "vendor_job_id": None,
            "status": EditingJobStatus.QUEUED,
            "failure_code": None,
            "output_artifact_ids": (),
        }
        values.update(overrides)
        return AliyunEditingIntent(**values)  # type: ignore[arg-type]

    def test_prepared_intent_holds_request_hash_only(self) -> None:
        intent = self._intent()
        assert intent.state is AliyunEditingIntentState.PREPARED
        assert intent.vendor_job_id is None

    def test_dispatched_intent_requires_vendor_job_id(self) -> None:
        intent = self._intent(
            state=AliyunEditingIntentState.DISPATCHED, vendor_job_id="job-12345678"
        )
        assert intent.vendor_job_id == "job-12345678"

    @pytest.mark.parametrize(
        "overrides",
        [
            {"request_hash": "A" * 64},
            {"request_hash": "a" * 63},
            {"state": AliyunEditingIntentState.PREPARED, "vendor_job_id": "job-12345678"},
            {"state": AliyunEditingIntentState.PREPARED, "status": EditingJobStatus.RUNNING},
            {"state": AliyunEditingIntentState.DISPATCHED, "vendor_job_id": None},
            {
                "state": AliyunEditingIntentState.UNCERTAIN,
                "vendor_job_id": None,
                "status": EditingJobStatus.QUEUED,
            },
            {
                "state": AliyunEditingIntentState.DISPATCHED,
                "vendor_job_id": "job-12345678",
                "status": EditingJobStatus.SUCCEEDED,
                "output_artifact_ids": (),
            },
            {
                "state": AliyunEditingIntentState.DISPATCHED,
                "vendor_job_id": "job-12345678",
                "status": EditingJobStatus.FAILED,
                "failure_code": None,
            },
        ],
    )
    def test_invalid_intents_fail_closed(self, overrides: dict[str, object]) -> None:
        with pytest.raises(InvalidAliyunImsEditingProviderModel):
            self._intent(**overrides)

    def test_uncertain_intent_reports_outcome_uncertain(self) -> None:
        intent = self._intent(
            state=AliyunEditingIntentState.UNCERTAIN,
            status=EditingJobStatus.OUTCOME_UNCERTAIN,
        )
        assert intent.status is EditingJobStatus.OUTCOME_UNCERTAIN


class TestInMemoryAliyunEditingIntentStore:
    @pytest.mark.asyncio
    async def test_round_trip_and_overwrite(self) -> None:
        store = InMemoryAliyunEditingIntentStore()
        job_id = EditingJobId(JOB_UUID)
        assert await store.load(job_id) is None
        prepared = AliyunEditingIntent(
            editing_job_id=job_id,
            request_hash="a" * 64,
            state=AliyunEditingIntentState.PREPARED,
            vendor_job_id=None,
            status=EditingJobStatus.QUEUED,
            failure_code=None,
            output_artifact_ids=(),
        )
        await store.save(prepared)
        assert await store.load(job_id) == prepared
        dispatched = AliyunEditingIntent(
            editing_job_id=job_id,
            request_hash="a" * 64,
            state=AliyunEditingIntentState.DISPATCHED,
            vendor_job_id="job-12345678",
            status=EditingJobStatus.QUEUED,
            failure_code=None,
            output_artifact_ids=(),
        )
        await store.save(dispatched)
        assert await store.load(job_id) == dispatched


class TestSubmitOrchestration:
    @pytest.mark.asyncio
    async def test_happy_path_dispatches_once_and_persists_job_id(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        provider, transport, store = _provider(contract)
        submission = _submission()
        snapshot = await provider.submit(submission)
        assert snapshot.status is EditingJobStatus.QUEUED
        assert snapshot.provider_id == ALIYUN_IMS_EDITING_PROVIDER_ID
        assert snapshot.failure_code is None
        assert snapshot.output_artifact_ids == ()
        assert len(transport.requests) == 1
        assert transport.requests[0].client_token == submission.idempotency_key
        intent = await store.load(submission.editing_job_id)
        assert intent is not None
        assert intent.state is AliyunEditingIntentState.DISPATCHED
        assert intent.vendor_job_id == "job-" + "1".zfill(32)
        assert len(intent.request_hash) == 64

    @pytest.mark.asyncio
    async def test_replay_returns_original_result_without_second_dispatch(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        provider, transport, _ = _provider(contract)
        submission = _submission()
        first = await provider.submit(submission)
        replay = await provider.submit(submission)
        assert replay == first
        assert len(transport.requests) == 1

    @pytest.mark.asyncio
    async def test_same_key_different_content_is_conflict_without_dispatch(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        provider, transport, _ = _provider(contract)
        submission = _submission()
        await provider.submit(submission)
        changed = EditingSubmission(
            editing_job_id=submission.editing_job_id,
            project_id=submission.project_id,
            timeline=_timeline(
                (
                    _visual_track(
                        (_clip("visual-1", start_ms=0, duration_ms=10_000, artifact=ARTIFACT_2),)
                    ),
                )
            ),
            idempotency_key=submission.idempotency_key,
        )
        with pytest.raises(EditingProviderFailure) as excinfo:
            await provider.submit(changed)
        _assert_failure(excinfo.value, EditingProviderErrorCode.CONFLICT)
        assert len(transport.requests) == 1

    @pytest.mark.asyncio
    async def test_preflight_not_ready_blocks_submission_without_side_effects(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        for status in (PreflightCheckStatus.FAILED, PreflightCheckStatus.UNVERIFIED):
            provider, transport, store = _provider(contract, preflight=_preflight(status))
            submission = _submission()
            with pytest.raises(EditingProviderFailure) as excinfo:
                await provider.submit(submission)
            _assert_failure(excinfo.value, EditingProviderErrorCode.DEPENDENCY_UNAVAILABLE)
            assert transport.requests == []
            assert await store.load(submission.editing_job_id) is None

    @pytest.mark.asyncio
    async def test_unsupported_timeline_is_rejected_without_side_effects(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        provider, transport, store = _provider(contract)
        timeline = _timeline(
            (
                _visual_track(
                    (
                        _clip("visual-1", start_ms=0, duration_ms=6_000, artifact=ARTIFACT_1),
                        _clip(
                            "visual-2",
                            start_ms=6_000,
                            duration_ms=4_000,
                            artifact=ARTIFACT_2,
                            transition=TimelineTransition(
                                kind=TransitionKind.DISSOLVE, duration_ms=300
                            ),
                        ),
                    )
                ),
            )
        )
        submission = _submission(timeline)
        with pytest.raises(EditingProviderFailure) as excinfo:
            await provider.submit(submission)
        _assert_failure(excinfo.value, EditingProviderErrorCode.UNSUPPORTED_CAPABILITY)
        assert transport.requests == []
        assert await store.load(submission.editing_job_id) is None

    @pytest.mark.asyncio
    async def test_not_dispatched_failure_keeps_prepared_intent_and_allows_retry(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        transport = _ScriptedTransport(
            outcomes=(
                AliyunImsTransportFailure(AliyunImsTransportFailureKind.NOT_DISPATCHED),
                AliyunSubmitAcknowledgement(
                    vendor_job_id="job-retry-ok-12345678", request_id="request-retry-000001"
                ),
            )
        )
        provider, _, store = _provider(contract, transport=transport)
        submission = _submission()
        with pytest.raises(EditingProviderFailure) as excinfo:
            await provider.submit(submission)
        _assert_failure(excinfo.value, EditingProviderErrorCode.DEPENDENCY_UNAVAILABLE)
        intent = await store.load(submission.editing_job_id)
        assert intent is not None
        assert intent.state is AliyunEditingIntentState.PREPARED
        with pytest.raises(EditingProviderFailure):
            await provider.get(submission.editing_job_id)
        snapshot = await provider.submit(submission)
        assert snapshot.status is EditingJobStatus.QUEUED
        assert len(transport.requests) == 2
        retried = await store.load(submission.editing_job_id)
        assert retried is not None
        assert retried.vendor_job_id == "job-retry-ok-12345678"

    @pytest.mark.asyncio
    async def test_lost_response_becomes_outcome_uncertain_without_replay(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        transport = _ScriptedTransport(
            outcomes=(AliyunImsTransportFailure(AliyunImsTransportFailureKind.RESPONSE_LOST),)
        )
        provider, _, store = _provider(contract, transport=transport)
        submission = _submission()
        snapshot = await provider.submit(submission)
        assert snapshot.status is EditingJobStatus.OUTCOME_UNCERTAIN
        assert snapshot.failure_code is None
        assert snapshot.output_artifact_ids == ()
        intent = await store.load(submission.editing_job_id)
        assert intent is not None
        assert intent.state is AliyunEditingIntentState.UNCERTAIN
        replay = await provider.submit(submission)
        assert replay.status is EditingJobStatus.OUTCOME_UNCERTAIN
        assert len(transport.requests) == 1
        assert (await provider.get(submission.editing_job_id)).status is (
            EditingJobStatus.OUTCOME_UNCERTAIN
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            (
                AliyunImsTransportFailureKind.REJECTED_INVALID,
                EditingProviderErrorCode.INVALID_INPUT,
            ),
            (
                AliyunImsTransportFailureKind.REJECTED_PERMISSION,
                EditingProviderErrorCode.DEPENDENCY_UNAVAILABLE,
            ),
            (
                AliyunImsTransportFailureKind.REJECTED_THROTTLED,
                EditingProviderErrorCode.DEPENDENCY_UNAVAILABLE,
            ),
        ],
    )
    async def test_definitive_rejections_map_to_closed_taxonomy(
        self,
        contract: AliyunImsEditingStagingContract,
        kind: AliyunImsTransportFailureKind,
        expected: EditingProviderErrorCode,
    ) -> None:
        transport = _ScriptedTransport(outcomes=(AliyunImsTransportFailure(kind),))
        provider, _, store = _provider(contract, transport=transport)
        submission = _submission()
        with pytest.raises(EditingProviderFailure) as excinfo:
            await provider.submit(submission)
        _assert_failure(excinfo.value, expected)
        intent = await store.load(submission.editing_job_id)
        assert intent is not None
        assert intent.state is AliyunEditingIntentState.PREPARED

    @pytest.mark.asyncio
    async def test_snapshot_carries_no_vendor_fields(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        provider, _, _ = _provider(contract)
        snapshot = await provider.submit(_submission())
        for vendor_field in ve02.VENDOR_FIELD_NAMES:
            assert not hasattr(snapshot, vendor_field)

    @pytest.mark.asyncio
    async def test_reconciliation_entry_points_follow_state_machine(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        provider, _, _ = _provider(contract)
        submission = _submission()
        await provider.submit(submission)
        await provider.record_running(submission.editing_job_id)
        assert (await provider.get(submission.editing_job_id)).status is (EditingJobStatus.RUNNING)
        output = ArtifactId.new()
        await provider.record_succeeded(submission.editing_job_id, (output,))
        snapshot = await provider.get(submission.editing_job_id)
        assert snapshot.status is EditingJobStatus.SUCCEEDED
        assert snapshot.output_artifact_ids == (output,)
        assert await provider.fetch_artifacts(submission.editing_job_id) == (output,)

    @pytest.mark.asyncio
    async def test_illegal_reconciliation_transition_is_conflict(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        provider, _, _ = _provider(contract)
        submission = _submission()
        await provider.submit(submission)
        with pytest.raises(EditingProviderFailure) as excinfo:
            await provider.record_succeeded(submission.editing_job_id, (ArtifactId.new(),))
        _assert_failure(excinfo.value, EditingProviderErrorCode.CONFLICT)


class TestDomainZeroVendorLeak:
    def test_domain_modules_never_import_the_aliyun_adapter(self) -> None:
        domain_root = REPOSITORY_ROOT / "backend/src/automation_tool/control_plane/domain"
        for module_name in ("video_editing.py", "video_editing_provider.py"):
            source = (domain_root / module_name).read_text(encoding="utf-8")
            assert "aliyun_ims_editing_provider" not in source
            assert "aliyun_ims_editing_staging" not in source

    def test_domain_exports_contain_no_vendor_names(self) -> None:
        from automation_tool.control_plane.domain import video_editing, video_editing_provider

        for module in (video_editing, video_editing_provider):
            for name in module.__all__:
                assert "aliyun" not in name.lower()
                assert "oss" not in name.lower()


@final
class TestAliyunImsEditingProviderContract(ve02.VideoEditingProviderContract):
    """VE-02 provider consistency suite against the Aliyun adapter (mock network)."""

    def make_provider(self) -> VideoEditingProvider:
        contract = load_aliyun_ims_editing_staging_contract(CONTRACT_PATH)
        provider, _, _ = _provider(contract)
        return provider

    def make_supported_timeline(self, project_id: EditingProjectId) -> EditingTimeline:
        return _timeline(
            (
                _visual_track(
                    (
                        _clip(
                            "visual-1",
                            start_ms=0,
                            duration_ms=15_000,
                            artifact=ArtifactId.new(),
                        ),
                        _clip(
                            "visual-2",
                            start_ms=15_000,
                            duration_ms=15_000,
                            artifact=ArtifactId.new(),
                            transition=TimelineTransition(
                                kind=TransitionKind.FADE, duration_ms=300
                            ),
                        ),
                    )
                ),
            ),
            duration_ms=30_000,
            project_id=project_id,
        )

    def make_unsupported_timeline(self, project_id: EditingProjectId) -> EditingTimeline:
        return _timeline(
            (
                _visual_track(
                    (
                        _clip(
                            "visual-1",
                            start_ms=0,
                            duration_ms=15_000,
                            artifact=ArtifactId.new(),
                        ),
                        _clip(
                            "visual-2",
                            start_ms=15_000,
                            duration_ms=15_000,
                            artifact=ArtifactId.new(),
                            transition=TimelineTransition(
                                kind=TransitionKind.DISSOLVE, duration_ms=300
                            ),
                        ),
                    )
                ),
            ),
            duration_ms=30_000,
            project_id=project_id,
        )

    async def drive_to_running(
        self, provider: VideoEditingProvider, editing_job_id: EditingJobId
    ) -> None:
        assert isinstance(provider, AliyunImsEditingProvider)
        await provider.record_running(editing_job_id)

    async def drive_to_success(
        self, provider: VideoEditingProvider, editing_job_id: EditingJobId
    ) -> None:
        assert isinstance(provider, AliyunImsEditingProvider)
        await provider.record_running(editing_job_id)
        await provider.record_succeeded(editing_job_id, (ArtifactId.new(),))
