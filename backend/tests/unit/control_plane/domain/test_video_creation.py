from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime

import pytest

from automation_tool.control_plane.domain import ArtifactId, TimelineId
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
    VideoAspectRatio,
    VideoCreationMethod,
)

NOW = datetime(2026, 7, 23, 0, 0, tzinfo=UTC)
SHA256 = "a" * 64

_BANNED_FIELD_NAME_FRAGMENTS = ("provider", "model", "vendor", "api_key", "base_url", "voice_id")


def assert_field_names_carry_no_banned_fragment(field_names: tuple[str, ...]) -> None:
    """Every field name must not *contain* a banned provider-coupling fragment.

    This has to be substring containment, not `set(...).intersection(...)`:
    intersection only matches whole elements, so a set containing "provider"
    would let a field literally named "provider_job_id" straight through.
    These dataclass fields carry no default, so a *required* provider field
    already breaks every call site at construction time — this guard exists
    for the *optional* one (`str | None = None`), which would otherwise pass
    silently and be the actual shape violation project rule §6 forbids.
    """
    for field_name in field_names:
        assert not any(fragment in field_name for fragment in _BANNED_FIELD_NAME_FRAGMENTS), (
            f"{field_name!r} carries a banned provider-coupling fragment"
        )


def test_the_creation_line_no_longer_defines_its_own_timeline() -> None:
    """One Timeline, in one module. Two would drift apart."""
    import automation_tool.control_plane.domain.video_creation as creation

    leftovers = [
        name
        for name in (
            "Timeline",
            "TimelineClip",
            "TimelineTrack",
            "TimelineTrackKind",
            "TimelineTransition",
            "TransitionKind",
        )
        if name in vars(creation)
    ]
    assert leftovers == []


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


def test_video_domain_happy_path_reuses_artifact_id_and_has_no_provider_payload() -> None:
    source = _artifact()
    brief = _brief(source)
    storyboard = _storyboard(brief)
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
        timeline_id=TimelineId.new(),
        method=VideoCreationMethod.MATERIAL_MONTAGE_V1,
        status=RenderJobStatus.SUCCEEDED,
        revision=3,
        input_artifact_ids=(source.artifact_id,),
        output_artifact_ids=(output.artifact_id,),
        failure_code=None,
        created_at=NOW,
        updated_at=NOW,
    )

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
    for model, field_names in expected.items():
        assert tuple(field.name for field in fields(model)) == field_names
        assert_field_names_carry_no_banned_fragment(field_names)


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


def test_language_rejects_a_trailing_newline() -> None:
    """`_LANGUAGE_PATTERN.fullmatch` requires consuming the whole string, so
    this is already rejected today — but only because the call site uses
    `fullmatch` rather than `match`. Pinned separately so the guard does not
    quietly start depending on that choice of verb.
    """
    source = _artifact()
    brief = _brief(source)
    with pytest.raises(InvalidVideoDomainModel):
        ContentBrief(
            brief.brief_id,
            brief.prompt,
            "zh-CN\n",
            brief.target_duration_ms,
            brief.aspect_ratio,
            brief.source_artifact_ids,
            brief.created_at,
        )


def test_artifact_sha256_rejects_a_trailing_newline() -> None:
    """`_SHA256_PATTERN.fullmatch` requires consuming the whole string, so
    this is already rejected today — but only because the call site uses
    `fullmatch` rather than `match`. Pinned separately so the guard does not
    quietly start depending on that choice of verb.
    """
    with pytest.raises(InvalidVideoDomainModel):
        Artifact(
            ArtifactId.new(),
            ArtifactRole.SOURCE_IMAGE,
            "image/png",
            1,
            SHA256 + "\n",
            (),
            NOW,
        )


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


def test_scene_structural_bounds_fail_closed() -> None:
    with pytest.raises(InvalidVideoDomainModel):
        StoryboardScene(0, 1_000, "镜头", None, None)


def test_storyboard_rejects_non_contiguous_scenes() -> None:
    brief = _brief(_artifact())
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
