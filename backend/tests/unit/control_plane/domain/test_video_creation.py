from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime

import pytest

from automation_tool.control_plane.domain import ArtifactId
from automation_tool.control_plane.domain.video_creation import (
    Artifact,
    ArtifactRole,
    ContentBrief,
    ContentBriefId,
    InvalidVideoDomainModel,
    RenderFailureCode,
    RenderJob,
    RenderJobId,
    RenderJobStatus,
    Storyboard,
    StoryboardId,
    StoryboardScene,
    Timeline,
    TimelineClip,
    TimelineId,
    TimelineTrack,
    TimelineTrackKind,
    TimelineTransition,
    TransitionKind,
    VideoAspectRatio,
    VideoCreationMethod,
)

NOW = datetime(2026, 7, 23, 0, 0, tzinfo=UTC)
SHA256 = "a" * 64


def _artifact(role: ArtifactRole = ArtifactRole.SOURCE_IMAGE) -> Artifact:
    return Artifact(
        artifact_id=ArtifactId.new(),
        role=role,
        media_type="image/png",
        byte_size=1024,
        sha256=SHA256,
        source_artifact_ids=(),
        created_at=NOW,
    )


def _brief(source: Artifact) -> ContentBrief:
    return ContentBrief(
        brief_id=ContentBriefId.new(),
        prompt="把新品的三个核心卖点做成一段简洁的竖屏视频",
        language="zh-CN",
        target_duration_ms=30_000,
        aspect_ratio=VideoAspectRatio.PORTRAIT_9_16,
        source_artifact_ids=(source.artifact_id,),
        created_at=NOW,
    )


def _storyboard(brief: ContentBrief) -> Storyboard:
    return Storyboard(
        storyboard_id=StoryboardId.new(),
        brief_id=brief.brief_id,
        revision=1,
        scenes=(
            StoryboardScene(
                sequence=1,
                duration_ms=12_000,
                visual_direction="产品在纯色背景中进入画面并展示卖点一",
                narration="先看第一个核心卖点。",
                on_screen_text="核心卖点一",
            ),
            StoryboardScene(
                sequence=2,
                duration_ms=18_000,
                visual_direction="切换到功能细节和行动提示",
                narration="再看另外两个卖点。",
                on_screen_text="立即了解",
            ),
        ),
        created_at=NOW,
    )


def _timeline(storyboard: Storyboard, source: Artifact) -> Timeline:
    return Timeline(
        timeline_id=TimelineId.new(),
        storyboard_id=storyboard.storyboard_id,
        revision=1,
        duration_ms=30_000,
        tracks=(
            TimelineTrack(
                track_id="visual-main",
                kind=TimelineTrackKind.VISUAL,
                clips=(
                    TimelineClip(
                        clip_id="visual-1",
                        start_ms=0,
                        duration_ms=30_000,
                        source_artifact_id=source.artifact_id,
                        text=None,
                        transition_in=TimelineTransition(
                            kind=TransitionKind.FADE,
                            duration_ms=300,
                        ),
                    ),
                ),
            ),
            TimelineTrack(
                track_id="caption-main",
                kind=TimelineTrackKind.CAPTION,
                clips=(
                    TimelineClip(
                        clip_id="caption-1",
                        start_ms=0,
                        duration_ms=12_000,
                        source_artifact_id=None,
                        text="核心卖点一",
                        transition_in=None,
                    ),
                ),
            ),
        ),
        created_at=NOW,
    )


def test_video_domain_happy_path_reuses_artifact_id_and_has_no_provider_payload() -> None:
    source = _artifact()
    brief = _brief(source)
    storyboard = _storyboard(brief)
    timeline = _timeline(storyboard, source)
    output = Artifact(
        artifact_id=ArtifactId.new(),
        role=ArtifactRole.OUTPUT_VIDEO,
        media_type="video/mp4",
        byte_size=4_096,
        sha256="b" * 64,
        source_artifact_ids=(source.artifact_id,),
        created_at=NOW,
    )
    job = RenderJob(
        render_job_id=RenderJobId.new(),
        brief_id=brief.brief_id,
        storyboard_id=storyboard.storyboard_id,
        timeline_id=timeline.timeline_id,
        method=VideoCreationMethod.MATERIAL_MONTAGE_V1,
        status=RenderJobStatus.SUCCEEDED,
        revision=3,
        input_artifact_ids=(source.artifact_id,),
        output_artifact_ids=(output.artifact_id,),
        failure_code=None,
        created_at=NOW,
        updated_at=NOW,
    )

    assert sum(scene.duration_ms for scene in storyboard.scenes) == timeline.duration_ms
    assert job.input_artifact_ids == brief.source_artifact_ids
    assert tuple(field.name for field in fields(RenderJob)) == (
        "render_job_id",
        "brief_id",
        "storyboard_id",
        "timeline_id",
        "method",
        "status",
        "revision",
        "input_artifact_ids",
        "output_artifact_ids",
        "failure_code",
        "created_at",
        "updated_at",
    )
    assert not any("provider" in field.name for field in fields(RenderJob))
    with pytest.raises(FrozenInstanceError):
        job.revision = 4  # type: ignore[misc]


def test_public_models_have_exact_provider_neutral_fields() -> None:
    expected = {
        ContentBrief: (
            "brief_id",
            "prompt",
            "language",
            "target_duration_ms",
            "aspect_ratio",
            "source_artifact_ids",
            "created_at",
        ),
        StoryboardScene: (
            "sequence",
            "duration_ms",
            "visual_direction",
            "narration",
            "on_screen_text",
        ),
        Storyboard: ("storyboard_id", "brief_id", "revision", "scenes", "created_at"),
        TimelineTransition: ("kind", "duration_ms"),
        TimelineClip: (
            "clip_id",
            "start_ms",
            "duration_ms",
            "source_artifact_id",
            "text",
            "transition_in",
        ),
        TimelineTrack: ("track_id", "kind", "clips"),
        Timeline: (
            "timeline_id",
            "storyboard_id",
            "revision",
            "duration_ms",
            "tracks",
            "created_at",
        ),
        Artifact: (
            "artifact_id",
            "role",
            "media_type",
            "byte_size",
            "sha256",
            "source_artifact_ids",
            "created_at",
        ),
        RenderJob: (
            "render_job_id",
            "brief_id",
            "storyboard_id",
            "timeline_id",
            "method",
            "status",
            "revision",
            "input_artifact_ids",
            "output_artifact_ids",
            "failure_code",
            "created_at",
            "updated_at",
        ),
    }
    banned_fragments = {"provider", "model", "vendor", "api_key", "base_url", "voice_id"}
    for model, field_names in expected.items():
        assert tuple(field.name for field in fields(model)) == field_names
        assert not banned_fragments.intersection(field_names)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("prompt", " "),
        ("language", "zh_CN"),
        ("target_duration_ms", 0),
        ("target_duration_ms", 600_001),
        ("aspect_ratio", "9:16"),
        ("source_artifact_ids", (ArtifactId.new(), ArtifactId.new()) * 33),
        ("created_at", datetime(2026, 7, 23)),
    ),
)
def test_content_brief_rejects_invalid_or_unbounded_fields(field: str, value: object) -> None:
    source = _artifact()
    values: dict[str, object] = {
        "brief_id": ContentBriefId.new(),
        "prompt": "有效视频需求",
        "language": "zh-CN",
        "target_duration_ms": 30_000,
        "aspect_ratio": VideoAspectRatio.PORTRAIT_9_16,
        "source_artifact_ids": (source.artifact_id,),
        "created_at": NOW,
    }
    values[field] = value
    with pytest.raises(InvalidVideoDomainModel):
        ContentBrief(**values)  # type: ignore[arg-type]


def test_text_boundaries_allow_layout_but_reject_controls_and_wrong_id_type() -> None:
    source = _artifact()
    brief = _brief(source)
    with_layout = ContentBrief(
        brief.brief_id,
        "第一行\n第二行\t补充",
        brief.language,
        brief.target_duration_ms,
        brief.aspect_ratio,
        brief.source_artifact_ids,
        brief.created_at,
    )
    assert "\n" in with_layout.prompt and "\t" in with_layout.prompt
    with pytest.raises(InvalidVideoDomainModel):
        ContentBrief(
            ArtifactId.new(),  # type: ignore[arg-type]
            brief.prompt,
            brief.language,
            brief.target_duration_ms,
            brief.aspect_ratio,
            brief.source_artifact_ids,
            brief.created_at,
        )
    with pytest.raises(InvalidVideoDomainModel):
        ContentBrief(
            brief.brief_id,
            "不可见方向控制\u202e",
            brief.language,
            brief.target_duration_ms,
            brief.aspect_ratio,
            brief.source_artifact_ids,
            brief.created_at,
        )


def test_scene_transition_clip_and_track_structural_bounds_fail_closed() -> None:
    source = _artifact()
    with pytest.raises(InvalidVideoDomainModel):
        StoryboardScene(0, 1_000, "镜头", None, None)
    with pytest.raises(InvalidVideoDomainModel):
        TimelineTransition(TransitionKind.FADE, 0)
    with pytest.raises(InvalidVideoDomainModel):
        TimelineClip("Bad_ID", 0, 1_000, source.artifact_id, None, None)
    with pytest.raises(InvalidVideoDomainModel):
        TimelineClip("empty-clip", 0, 1_000, None, None, None)
    with pytest.raises(InvalidVideoDomainModel):
        TimelineTrack("Bad_ID", TimelineTrackKind.VISUAL, ())
    with pytest.raises(InvalidVideoDomainModel):
        TimelineTrack(
            "visual-main",
            TimelineTrackKind.VISUAL,
            (TimelineClip("visual-1", 0, 1_000, source.artifact_id, "错误文字", None),),
        )


def test_storyboard_rejects_non_contiguous_scenes_and_timeline_rejects_overlap() -> None:
    source = _artifact()
    brief = _brief(source)
    first = StoryboardScene(
        sequence=1,
        duration_ms=1_000,
        visual_direction="第一个镜头",
        narration=None,
        on_screen_text=None,
    )
    with pytest.raises(InvalidVideoDomainModel):
        Storyboard(
            storyboard_id=StoryboardId.new(),
            brief_id=brief.brief_id,
            revision=1,
            scenes=(first, StoryboardScene(3, 1_000, "第三个镜头", None, None)),
            created_at=NOW,
        )

    with pytest.raises(InvalidVideoDomainModel):
        TimelineTrack(
            track_id="visual-main",
            kind=TimelineTrackKind.VISUAL,
            clips=(
                TimelineClip("clip-1", 0, 800, source.artifact_id, None, None),
                TimelineClip("clip-2", 700, 300, source.artifact_id, None, None),
            ),
        )


def test_timeline_rejects_wrong_track_payload_and_out_of_bounds_clip() -> None:
    source = _artifact()
    brief = _brief(source)
    storyboard = _storyboard(brief)
    with pytest.raises(InvalidVideoDomainModel):
        TimelineTrack(
            track_id="caption-main",
            kind=TimelineTrackKind.CAPTION,
            clips=(TimelineClip("caption-1", 0, 1_000, source.artifact_id, None, None),),
        )

    overflowing = TimelineTrack(
        track_id="visual-main",
        kind=TimelineTrackKind.VISUAL,
        clips=(TimelineClip("visual-1", 29_500, 1_000, source.artifact_id, None, None),),
    )
    with pytest.raises(InvalidVideoDomainModel):
        Timeline(TimelineId.new(), storyboard.storyboard_id, 1, 30_000, (overflowing,), NOW)

    caption_only = TimelineTrack(
        "caption-main",
        TimelineTrackKind.CAPTION,
        (TimelineClip("caption-1", 0, 1_000, None, "字幕", None),),
    )
    with pytest.raises(InvalidVideoDomainModel):
        Timeline(TimelineId.new(), storyboard.storyboard_id, 1, 30_000, (caption_only,), NOW)


@pytest.mark.parametrize(
    "artifact",
    (
        lambda: Artifact(
            ArtifactId.new(), ArtifactRole.SOURCE_IMAGE, "text/html", 1, SHA256, (), NOW
        ),
        lambda: Artifact(
            ArtifactId.new(), ArtifactRole.SOURCE_IMAGE, "image/png", 0, SHA256, (), NOW
        ),
        lambda: Artifact(
            ArtifactId.new(), ArtifactRole.SOURCE_IMAGE, "image/png", 1, "A" * 64, (), NOW
        ),
        lambda: Artifact(
            ArtifactId.new(),
            ArtifactRole.SOURCE_IMAGE,
            "image/png",
            1,
            SHA256,
            (ArtifactId.new(),) * 2,
            NOW,
        ),
    ),
)
def test_artifact_rejects_role_media_mismatch_and_invalid_integrity(artifact: object) -> None:
    with pytest.raises(InvalidVideoDomainModel):
        artifact()  # type: ignore[operator]


def test_artifact_rejects_self_reference() -> None:
    artifact_id = ArtifactId.new()
    with pytest.raises(InvalidVideoDomainModel):
        Artifact(
            artifact_id,
            ArtifactRole.SOURCE_IMAGE,
            "image/png",
            1,
            SHA256,
            (artifact_id,),
            NOW,
        )


@pytest.mark.parametrize(
    ("status", "outputs", "failure"),
    (
        (RenderJobStatus.QUEUED, (ArtifactId.new(),), None),
        (RenderJobStatus.RUNNING, (), RenderFailureCode.RENDER_FAILED),
        (RenderJobStatus.SUCCEEDED, (), None),
        (RenderJobStatus.SUCCEEDED, (ArtifactId.new(),), RenderFailureCode.RENDER_FAILED),
        (RenderJobStatus.FAILED, (), None),
        (RenderJobStatus.CANCELLED, (ArtifactId.new(),), None),
    ),
)
def test_render_job_status_keeps_output_and_failure_facts_consistent(
    status: RenderJobStatus,
    outputs: tuple[ArtifactId, ...],
    failure: RenderFailureCode | None,
) -> None:
    with pytest.raises(InvalidVideoDomainModel):
        RenderJob(
            RenderJobId.new(),
            ContentBriefId.new(),
            StoryboardId.new(),
            TimelineId.new(),
            VideoCreationMethod.MOTION_COMPOSITION_V1,
            status,
            1,
            (),
            outputs,
            failure,
            NOW,
            NOW,
        )


def test_render_job_rejects_wrong_ids_overlap_and_time_regression() -> None:
    shared = ArtifactId.new()
    values: dict[str, object] = {
        "render_job_id": RenderJobId.new(),
        "brief_id": ContentBriefId.new(),
        "storyboard_id": StoryboardId.new(),
        "timeline_id": TimelineId.new(),
        "method": VideoCreationMethod.MATERIAL_MONTAGE_V1,
        "status": RenderJobStatus.QUEUED,
        "revision": 1,
        "input_artifact_ids": (),
        "output_artifact_ids": (),
        "failure_code": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    for changes in (
        {"render_job_id": ArtifactId.new()},
        {"input_artifact_ids": (shared,), "output_artifact_ids": (shared,)},
        {"created_at": NOW, "updated_at": datetime(2026, 7, 22, tzinfo=UTC)},
    ):
        with pytest.raises(InvalidVideoDomainModel):
            RenderJob(**(values | changes))  # type: ignore[arg-type]
