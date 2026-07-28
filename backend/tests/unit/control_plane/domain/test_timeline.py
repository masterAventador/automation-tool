"""Local editing timeline invariants: what plays when, from where, how loud."""

from __future__ import annotations

import pytest

from automation_tool.control_plane.domain.material import MaterialId
from automation_tool.control_plane.domain.resource_ids import InvalidResourceId
from automation_tool.control_plane.domain.timeline import (
    MAX_CLIP_TEXT_CHARACTERS,
    MAX_GAIN_DB,
    MAX_TIMELINE_DURATION_MS,
    MAX_TRANSITION_DURATION_MS,
    MIN_GAIN_DB,
    InvalidTimelineModel,
    TimelineClip,
    TimelineId,
    TimelineTrackKind,
    TimelineTransition,
    TransitionKind,
)


def test_timeline_id_is_a_uuid4_resource_id() -> None:
    identifier = TimelineId.new()
    assert TimelineId.parse(str(identifier)) == identifier


def test_timeline_id_rejects_a_foreign_identifier_type() -> None:
    with pytest.raises(InvalidResourceId):
        TimelineId.parse(MaterialId.new())


def test_invalid_timeline_model_is_a_value_error() -> None:
    assert issubclass(InvalidTimelineModel, ValueError)


def test_track_kinds_split_one_audio_lane_into_three() -> None:
    assert {kind.value for kind in TimelineTrackKind} == {
        "visual",
        "narration",
        "ambient",
        "music",
        "caption",
    }


def test_a_hard_cut_is_the_absence_of_a_transition_not_a_kind_of_one() -> None:
    assert {kind.value for kind in TransitionKind} == {"fade", "dissolve", "wipe"}


@pytest.mark.parametrize("duration_ms", [0, -1, MAX_TRANSITION_DURATION_MS + 1, 1.0, True])
def test_transition_rejects_an_unusable_duration(duration_ms: object) -> None:
    with pytest.raises(InvalidTimelineModel):
        TimelineTransition(TransitionKind.FADE, duration_ms)  # type: ignore[arg-type]


def _media_clip(**overrides: object) -> TimelineClip:
    """A valid clip that plays a slice of one material."""
    defaults: dict[str, object] = {
        "clip_id": "clip-1",
        "start_ms": 0,
        "duration_ms": 3_000,
        "source_material_id": MaterialId.new(),
        "source_in_ms": 5_000,
        "source_out_ms": 8_000,
        "text": None,
        "gain_db": None,
        "transition_in": None,
    }
    defaults.update(overrides)
    return TimelineClip(**defaults)  # type: ignore[arg-type]


def _caption_clip(**overrides: object) -> TimelineClip:
    """A valid clip that draws text and plays nothing."""
    defaults: dict[str, object] = {
        "clip_id": "caption-1",
        "start_ms": 0,
        "duration_ms": 3_000,
        "source_material_id": None,
        "source_in_ms": None,
        "source_out_ms": None,
        "text": "第一句字幕",
        "gain_db": None,
        "transition_in": None,
    }
    defaults.update(overrides)
    return TimelineClip(**defaults)  # type: ignore[arg-type]


def test_a_media_clip_states_where_on_the_film_and_where_in_the_source() -> None:
    clip = _media_clip()
    assert clip.end_ms == 3_000
    assert clip.source_out_ms is not None and clip.source_in_ms is not None
    assert clip.source_out_ms - clip.source_in_ms == clip.duration_ms


def test_first_release_takes_the_slice_at_its_own_speed() -> None:
    """No speed change: the slice length must equal the length it occupies."""
    with pytest.raises(InvalidTimelineModel):
        _media_clip(source_in_ms=0, source_out_ms=6_000)  # 2x fast-forward
    with pytest.raises(InvalidTimelineModel):
        _media_clip(source_in_ms=0, source_out_ms=1_500)  # slow motion


@pytest.mark.parametrize(
    ("source_in_ms", "source_out_ms"),
    [(5_000, None), (None, 8_000)],
)
def test_a_source_window_is_stated_at_both_ends_or_neither(
    source_in_ms: object, source_out_ms: object
) -> None:
    with pytest.raises(InvalidTimelineModel):
        _media_clip(source_in_ms=source_in_ms, source_out_ms=source_out_ms)


def test_a_still_source_may_omit_the_window_it_has_no_time_axis() -> None:
    assert _media_clip(source_in_ms=None, source_out_ms=None).source_in_ms is None


def test_a_source_window_cannot_start_before_the_source_does() -> None:
    with pytest.raises(InvalidTimelineModel):
        _media_clip(source_in_ms=-1, source_out_ms=2_999)


def test_text_has_nothing_to_slice() -> None:
    with pytest.raises(InvalidTimelineModel):
        _caption_clip(source_in_ms=0, source_out_ms=3_000)


def test_a_clip_either_plays_a_source_or_draws_text_never_both_nor_neither() -> None:
    with pytest.raises(InvalidTimelineModel):
        _media_clip(text="同时带文字")
    with pytest.raises(InvalidTimelineModel):
        _caption_clip(text=None)


@pytest.mark.parametrize("gain_db", [MIN_GAIN_DB - 0.1, MAX_GAIN_DB + 0.1, 0, True, "0.0"])
def test_gain_is_a_float_inside_the_usable_range(gain_db: object) -> None:
    with pytest.raises(InvalidTimelineModel):
        _media_clip(gain_db=gain_db)


def test_gain_accepts_the_range_ends() -> None:
    assert _media_clip(gain_db=MIN_GAIN_DB).gain_db == MIN_GAIN_DB
    assert _media_clip(gain_db=MAX_GAIN_DB).gain_db == MAX_GAIN_DB


def test_gain_requires_something_audible_to_adjust() -> None:
    """A caption plays nothing, and a still source has no time axis to carry sound."""
    with pytest.raises(InvalidTimelineModel):
        _caption_clip(gain_db=-3.0)
    with pytest.raises(InvalidTimelineModel):
        _media_clip(source_in_ms=None, source_out_ms=None, gain_db=-3.0)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("clip_id", "Bad_ID"),
        ("clip_id", ""),
        ("start_ms", -1),
        ("start_ms", 1.0),
        ("duration_ms", 0),
        ("duration_ms", MAX_TIMELINE_DURATION_MS + 1),
        ("duration_ms", True),
        ("source_material_id", "not-an-id"),
        ("transition_in", "fade"),
    ],
)
def test_clip_structural_bounds_fail_closed(field: str, value: object) -> None:
    with pytest.raises(InvalidTimelineModel):
        _media_clip(**{field: value})


def test_a_transition_cannot_be_as_long_as_or_longer_than_its_own_clip() -> None:
    """A transition covering the whole clip means the clip never plays on its own."""
    with pytest.raises(InvalidTimelineModel):
        _media_clip(
            duration_ms=3_000,
            transition_in=TimelineTransition(TransitionKind.FADE, 3_000),
        )
    with pytest.raises(InvalidTimelineModel):
        _media_clip(
            duration_ms=3_000,
            transition_in=TimelineTransition(TransitionKind.FADE, 10_000),
        )


def test_caption_text_is_bounded_and_free_of_control_characters() -> None:
    assert _caption_clip(text="第一行\n第二行").text == "第一行\n第二行"
    with pytest.raises(InvalidTimelineModel):
        _caption_clip(text="x" * (MAX_CLIP_TEXT_CHARACTERS + 1))
    with pytest.raises(InvalidTimelineModel):
        _caption_clip(text="带\x00空字符")
    with pytest.raises(InvalidTimelineModel):
        _caption_clip(text="  前后有空白  ")
