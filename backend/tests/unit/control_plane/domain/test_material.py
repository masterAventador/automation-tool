"""Material domain invariants for the local editing library."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from automation_tool.control_plane.domain.material import (
    MAX_MATERIAL_DIMENSION,
    MAX_MATERIAL_DURATION_MS,
    MAX_TAG_CHARACTERS,
    DescriptionSource,
    InvalidMaterialModel,
    Material,
    MaterialId,
    MaterialKind,
)
from automation_tool.control_plane.domain.resource_ids import InvalidResourceId


def test_material_id_is_a_uuid4_resource_id() -> None:
    identifier = MaterialId.new()
    assert MaterialId.parse(str(identifier)) == identifier


def test_material_id_rejects_a_foreign_identifier_type() -> None:
    from automation_tool.control_plane.domain.resource_ids import ArtifactId

    with pytest.raises(InvalidResourceId):
        MaterialId.parse(ArtifactId.new())


def test_material_kinds_are_exactly_the_three_supported() -> None:
    assert {kind.value for kind in MaterialKind} == {"video", "image", "audio"}


def test_description_sources_distinguish_ai_from_user() -> None:
    assert {source.value for source in DescriptionSource} == {"ai", "user"}


def test_invalid_material_model_is_a_value_error() -> None:
    assert issubclass(InvalidMaterialModel, ValueError)


def _video(**overrides: object) -> Material:
    """A valid video material, with named fields overridable per test."""
    defaults: dict[str, object] = {
        "material_id": MaterialId.new(),
        "kind": MaterialKind.VIDEO,
        "duration_ms": 15_000,
        "width": 1920,
        "height": 1080,
        "content_digest": "a" * 64,
        "has_audio": False,
        "audio_loudness_lufs": None,
        "has_speech": False,
        "speech_segments_ms": (),
        "speech_transcript": None,
        "shot_boundaries_ms": (),
        "ai_description": None,
        "ai_tags": (),
        "description_source": DescriptionSource.AI,
        "described_at": None,
    }
    defaults.update(overrides)
    return Material(**defaults)  # type: ignore[arg-type]


def _audio(**overrides: object) -> Material:
    """A valid audio material, with named fields overridable per test."""
    defaults: dict[str, object] = {
        "material_id": MaterialId.new(),
        "kind": MaterialKind.AUDIO,
        "duration_ms": 30_000,
        "width": None,
        "height": None,
        "content_digest": "b" * 64,
        "has_audio": True,
        "audio_loudness_lufs": -18.0,
        "has_speech": False,
        "speech_segments_ms": (),
        "speech_transcript": None,
        "shot_boundaries_ms": (),
        "ai_description": None,
        "ai_tags": (),
        "description_source": DescriptionSource.AI,
        "described_at": None,
    }
    defaults.update(overrides)
    return Material(**defaults)  # type: ignore[arg-type]


def test_a_valid_video_material_is_accepted() -> None:
    material = _video()
    assert material.kind is MaterialKind.VIDEO
    assert material.duration_ms == 15_000


def test_video_without_duration_is_rejected() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(duration_ms=None)


def test_image_must_not_carry_a_duration() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(kind=MaterialKind.IMAGE, duration_ms=15_000)


def test_image_without_duration_is_accepted() -> None:
    material = _video(kind=MaterialKind.IMAGE, duration_ms=None)
    assert material.duration_ms is None


@pytest.mark.parametrize("duration", [0, -1, MAX_MATERIAL_DURATION_MS + 1])
def test_duration_outside_the_supported_range_is_rejected(duration: int) -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(duration_ms=duration)


@pytest.mark.parametrize("dimension", [0, -1, MAX_MATERIAL_DIMENSION + 1])
def test_dimensions_outside_the_supported_range_are_rejected(dimension: int) -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(width=dimension)
    with pytest.raises(InvalidMaterialModel):
        _video(height=dimension)


def test_audio_material_carries_no_frame_size() -> None:
    assert _audio().width is None
    assert _audio().height is None


@pytest.mark.parametrize(
    ("field", "value"),
    [("width", 1920), ("height", 1080), ("width", 0), ("height", -1)],
)
def test_audio_material_rejects_any_frame_size(field: str, value: object) -> None:
    with pytest.raises(InvalidMaterialModel):
        _audio(**{field: value})


@pytest.mark.parametrize("kind", [MaterialKind.VIDEO, MaterialKind.IMAGE])
@pytest.mark.parametrize("field", ["width", "height"])
def test_visual_material_requires_a_frame_size(kind: MaterialKind, field: str) -> None:
    overrides: dict[str, object] = {"kind": kind, field: None}
    if kind is MaterialKind.IMAGE:
        overrides["duration_ms"] = None
    with pytest.raises(InvalidMaterialModel):
        _video(**overrides)


@pytest.mark.parametrize("value", [0, MAX_MATERIAL_DIMENSION + 1, 1080.0, True])
def test_visual_material_rejects_out_of_range_frame_size(value: object) -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(width=value)


@pytest.mark.parametrize(
    "digest",
    ["", "A" * 64, "a" * 63, "a" * 65, "g" * 64],
    ids=["empty", "uppercase", "too-short", "too-long", "non-hex"],
)
def test_content_digest_must_be_lowercase_sha256(digest: str) -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(content_digest=digest)


def test_content_digest_rejects_a_trailing_newline() -> None:
    """`_SHA256_PATTERN.fullmatch` requires consuming the whole string, so this
    is already rejected today — but only because the call site uses
    `fullmatch` rather than `match`. Pinned separately so the guard does not
    quietly start depending on that choice of verb.
    """
    with pytest.raises(InvalidMaterialModel):
        _video(content_digest="a" * 64 + "\n")


def test_material_is_immutable() -> None:
    material = _video()
    with pytest.raises(FrozenInstanceError):
        material.width = 640  # type: ignore[misc]


def test_material_without_audio_must_not_carry_loudness() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(has_audio=False, audio_loudness_lufs=-18.0)


def test_material_without_audio_cannot_have_speech() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(has_audio=False, has_speech=True)


def test_material_without_speech_must_not_carry_segments() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(has_audio=True, has_speech=False, speech_segments_ms=((0, 1_000),))


def test_material_without_speech_must_not_carry_a_transcript() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(has_audio=True, has_speech=False, speech_transcript="讲了点什么")


def test_speech_material_carries_segments_and_transcript() -> None:
    material = _video(
        has_audio=True,
        has_speech=True,
        speech_segments_ms=((500, 3_000), (4_000, 9_000)),
        speech_transcript="第一句。第二句。",
    )
    assert material.speech_segments_ms == ((500, 3_000), (4_000, 9_000))


def test_speech_material_requires_a_non_empty_transcript() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(
            has_audio=True,
            has_speech=True,
            speech_segments_ms=((500, 3_000),),
            speech_transcript=None,
        )
    with pytest.raises(InvalidMaterialModel):
        _video(
            has_audio=True,
            has_speech=True,
            speech_segments_ms=((500, 3_000),),
            speech_transcript="",
        )


def test_speech_analysis_moves_as_one_triplet_and_preserves_every_other_fact() -> None:
    material = _video(
        has_audio=True,
        audio_loudness_lufs=-14.5,
        ai_description="用户能看懂的素材描述",
        ai_tags=("户外", "露营"),
        shot_boundaries_ms=(0, 3_200, 8_000),
        described_at=datetime(2026, 7, 31, tzinfo=UTC),
    )

    changed = material.with_speech_analysis(
        has_speech=True,
        speech_segments_ms=((500, 3_000), (4_000, 9_000)),
        speech_transcript="第一句。\n第二句。",
    )

    assert changed.has_speech is True
    assert changed.speech_segments_ms == ((500, 3_000), (4_000, 9_000))
    assert changed.speech_transcript == "第一句。\n第二句。"
    for field in (
        "material_id",
        "kind",
        "duration_ms",
        "width",
        "height",
        "content_digest",
        "has_audio",
        "audio_loudness_lufs",
        "shot_boundaries_ms",
        "ai_description",
        "ai_tags",
        "description_source",
        "described_at",
    ):
        assert getattr(changed, field) == getattr(material, field)

    cleared = changed.with_speech_analysis(
        has_speech=False,
        speech_segments_ms=(),
        speech_transcript=None,
    )
    assert cleared.has_speech is False
    assert cleared.speech_segments_ms == ()
    assert cleared.speech_transcript is None


def test_speech_segments_must_be_ordered_and_disjoint() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(has_audio=True, has_speech=True, speech_segments_ms=((4_000, 9_000), (500, 3_000)))
    with pytest.raises(InvalidMaterialModel):
        _video(has_audio=True, has_speech=True, speech_segments_ms=((0, 5_000), (3_000, 8_000)))


def test_speech_segment_must_not_be_empty_or_reversed() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(has_audio=True, has_speech=True, speech_segments_ms=((1_000, 1_000),))
    with pytest.raises(InvalidMaterialModel):
        _video(has_audio=True, has_speech=True, speech_segments_ms=((3_000, 1_000),))


def test_speech_segment_must_not_exceed_the_material_duration() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(
            duration_ms=5_000,
            has_audio=True,
            has_speech=True,
            speech_segments_ms=((0, 6_000),),
        )


def test_image_with_audio_is_rejected() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(kind=MaterialKind.IMAGE, duration_ms=None, has_audio=True)


def test_image_without_audio_is_accepted() -> None:
    material = _video(kind=MaterialKind.IMAGE, duration_ms=None, has_audio=False)
    assert material.has_audio is False


def test_image_cannot_smuggle_an_unbounded_speech_segment_via_missing_duration() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(
            kind=MaterialKind.IMAGE,
            duration_ms=None,
            has_audio=True,
            has_speech=True,
            speech_segments_ms=((0, 999_999_999),),
            speech_transcript="hello",
        )


def test_material_with_audio_accepts_a_valid_loudness_value() -> None:
    material = _video(has_audio=True, audio_loudness_lufs=-23.0)
    assert material.audio_loudness_lufs == -23.0


def test_shot_boundaries_are_strictly_increasing() -> None:
    material = _video(duration_ms=20_000, shot_boundaries_ms=(0, 4_000, 12_000))
    assert material.shot_boundaries_ms == (0, 4_000, 12_000)
    with pytest.raises(InvalidMaterialModel):
        _video(duration_ms=20_000, shot_boundaries_ms=(4_000, 4_000))
    with pytest.raises(InvalidMaterialModel):
        _video(duration_ms=20_000, shot_boundaries_ms=(12_000, 4_000))


def test_shot_boundary_must_fall_inside_the_material() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(duration_ms=5_000, shot_boundaries_ms=(0, 6_000))
    with pytest.raises(InvalidMaterialModel):
        _video(duration_ms=5_000, shot_boundaries_ms=(-1,))


def test_image_carries_no_shot_boundaries() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(kind=MaterialKind.IMAGE, duration_ms=None, shot_boundaries_ms=(0,))


def test_audio_material_with_audio_is_accepted() -> None:
    material = _audio()
    assert material.kind is MaterialKind.AUDIO
    assert material.has_audio is True


def test_audio_without_audio_is_rejected() -> None:
    with pytest.raises(InvalidMaterialModel):
        _audio(has_audio=False, audio_loudness_lufs=None)


def test_audio_carries_no_shot_boundaries() -> None:
    with pytest.raises(InvalidMaterialModel):
        _audio(shot_boundaries_ms=(0, 4_000))


def test_a_fresh_material_has_no_description() -> None:
    material = _video()
    assert material.ai_description is None
    assert material.described_at is None
    assert material.description_source is DescriptionSource.AI


def test_ai_description_is_written_onto_an_undescribed_material() -> None:
    stamped = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    material = _video().with_ai_understanding(
        "室内一个人在喝水",
        ("室内", "人物"),
        (),
        stamped,
    )
    assert material.ai_description == "室内一个人在喝水"
    assert material.ai_tags == ("室内", "人物")
    assert material.described_at == stamped
    assert material.description_source is DescriptionSource.AI


def test_ai_understanding_moves_description_and_shot_boundaries_together() -> None:
    stamped = datetime(2026, 7, 28, 10, 30, tzinfo=UTC)

    material = _video(shot_boundaries_ms=(0, 4_000)).with_ai_understanding(
        "模型重新理解后的说明",
        ("室内", "转场"),
        (0, 6_000, 12_000),
        stamped,
    )

    assert material.ai_description == "模型重新理解后的说明"
    assert material.ai_tags == ("室内", "转场")
    assert material.shot_boundaries_ms == (0, 6_000, 12_000)
    assert material.described_at == stamped
    assert material.description_source is DescriptionSource.AI


def test_user_owned_description_rejects_the_entire_ai_understanding() -> None:
    stamped = datetime(2026, 7, 28, 10, 30, tzinfo=UTC)
    edited = _video(shot_boundaries_ms=(0, 4_000)).with_user_description("用户写的说明")

    unchanged = edited.with_ai_understanding(
        "模型试图覆盖",
        ("不应写入",),
        (0, 8_000),
        stamped,
    )

    assert unchanged is edited
    assert unchanged.ai_description == "用户写的说明"
    assert unchanged.shot_boundaries_ms == (0, 4_000)


def test_ai_may_redescribe_material_it_described_itself() -> None:
    first = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    second = datetime(2026, 7, 28, 11, 0, tzinfo=UTC)
    material = _video().with_ai_understanding("第一版", ("旧",), (), first)
    updated = material.with_ai_understanding("第二版", ("新",), (), second)
    assert updated.ai_description == "第二版"
    assert updated.described_at == second


def test_user_description_switches_the_source() -> None:
    material = _video().with_user_description("我自己写的说明")
    assert material.ai_description == "我自己写的说明"
    assert material.description_source is DescriptionSource.USER


def test_ai_cannot_overwrite_a_user_written_description() -> None:
    stamped = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    edited = _video().with_user_description("我自己写的说明")
    unchanged = edited.with_ai_understanding("AI 想改成这样", ("模型",), (0,), stamped)
    assert unchanged is edited
    assert unchanged.ai_description == "我自己写的说明"
    assert unchanged.description_source is DescriptionSource.USER


def test_user_may_rewrite_their_own_description() -> None:
    edited = _video().with_user_description("第一次写的")
    rewritten = edited.with_user_description("改了一版")
    assert rewritten.ai_description == "改了一版"
    assert rewritten.description_source is DescriptionSource.USER


def test_described_at_must_be_timezone_aware() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video().with_ai_understanding("说明", (), (), datetime(2026, 7, 28, 10, 0))


@pytest.mark.parametrize("tags", [("",), (" 前后有空格 ",), ("x" * (MAX_TAG_CHARACTERS + 1),)])
def test_tags_are_validated(tags: tuple[str, ...]) -> None:
    stamped = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    with pytest.raises(InvalidMaterialModel):
        _video().with_ai_understanding("说明", tags, (), stamped)


def test_tags_must_be_unique() -> None:
    stamped = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    with pytest.raises(InvalidMaterialModel):
        _video().with_ai_understanding("说明", ("室内", "室内"), (), stamped)


def test_ai_description_without_a_timestamp_is_rejected() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(ai_description="没有时间戳的描述", description_source=DescriptionSource.AI)


def test_user_description_must_not_carry_an_ai_timestamp() -> None:
    stamped = datetime(2026, 7, 28, 13, 0, tzinfo=UTC)
    with pytest.raises(InvalidMaterialModel):
        _video(
            ai_description="看起来像用户写的",
            description_source=DescriptionSource.USER,
            described_at=stamped,
        )


def test_user_description_clears_any_existing_ai_tags() -> None:
    stamped = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    described = _video().with_ai_understanding("说明", ("室内", "人物"), (), stamped)
    edited = described.with_user_description("我自己写的说明")
    assert edited.ai_tags == ()


def test_user_description_must_not_be_empty() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(ai_description=None, description_source=DescriptionSource.USER)


def test_user_description_must_not_carry_ai_tags() -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(
            ai_description="我自己写的说明",
            ai_tags=("室内",),
            description_source=DescriptionSource.USER,
        )


def test_the_guard_does_not_depend_on_the_calling_verb() -> None:
    """`\\Z` is why, and this is the only shape that pins it.

    Under `fullmatch` a trailing `$` behaves identically, so every
    behavioural case in this file passes either way -- reverting the
    anchor is invisible to them. Calling `match` instead states the
    property the anchor actually buys: the guard holds even if the
    verb is ever weakened.
    """
    from automation_tool.control_plane.domain.material import (
        _SHA256_PATTERN,
    )

    digest = "a" * 64
    assert _SHA256_PATTERN.match(digest + "\n") is None
