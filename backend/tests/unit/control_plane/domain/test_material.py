"""Material domain invariants for the local editing library."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from automation_tool.control_plane.domain.material import (
    MAX_MATERIAL_DIMENSION,
    MAX_MATERIAL_DURATION_MS,
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


@pytest.mark.parametrize(
    "digest",
    ["", "A" * 64, "a" * 63, "a" * 65, "g" * 64],
    ids=["empty", "uppercase", "too-short", "too-long", "non-hex"],
)
def test_content_digest_must_be_lowercase_sha256(digest: str) -> None:
    with pytest.raises(InvalidMaterialModel):
        _video(content_digest=digest)


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
