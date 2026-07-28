"""Local editing timeline invariants: what plays when, from where, how loud."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from automation_tool.control_plane.domain.material import MaterialId
from automation_tool.control_plane.domain.resource_ids import InvalidResourceId
from automation_tool.control_plane.domain.timeline import (
    MAX_CLIP_TEXT_CHARACTERS,
    MAX_CLIPS_PER_TRACK,
    MAX_GAIN_DB,
    MAX_TIMELINE_DURATION_MS,
    MAX_TRANSITION_DURATION_MS,
    MIN_GAIN_DB,
    MIN_TIMELINE_DURATION_MS,
    InvalidTimelineModel,
    Timeline,
    TimelineClip,
    TimelineId,
    TimelineTrack,
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


@pytest.mark.parametrize("kind", ["fade", TimelineTrackKind.VISUAL])
def test_transition_rejects_a_kind_that_is_not_a_transition_kind(kind: object) -> None:
    with pytest.raises(InvalidTimelineModel):
        TimelineTransition(kind, 500)  # type: ignore[arg-type]


def test_a_valid_transition_can_be_constructed_and_attached_to_a_clip() -> None:
    transition = TimelineTransition(TransitionKind.FADE, 500)
    assert transition.kind is TransitionKind.FADE
    assert transition.duration_ms == 500
    clip = _media_clip(transition_in=transition)
    assert clip.transition_in == transition


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


@pytest.mark.parametrize(
    ("source_in_ms", "source_out_ms"),
    [(5_000.0, 8_000.0), (True, 3_001)],
)
def test_a_source_window_rejects_non_integer_endpoints(
    source_in_ms: object, source_out_ms: object
) -> None:
    """The span still matches duration_ms — only the endpoint types are wrong."""
    with pytest.raises(InvalidTimelineModel):
        _media_clip(source_in_ms=source_in_ms, source_out_ms=source_out_ms)


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


def test_a_clip_cannot_end_past_the_timeline_upper_bound() -> None:
    """start_ms has no upper bound of its own — only end_ms staying on the axis does."""
    with pytest.raises(InvalidTimelineModel):
        _media_clip(start_ms=MAX_TIMELINE_DURATION_MS)
    with pytest.raises(InvalidTimelineModel):
        _media_clip(start_ms=10**18)


def test_a_clip_may_end_exactly_at_the_timeline_upper_bound() -> None:
    clip = _media_clip(start_ms=MAX_TIMELINE_DURATION_MS - 3_000)
    assert clip.end_ms == MAX_TIMELINE_DURATION_MS


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


def _visual_track(**overrides: object) -> TimelineTrack:
    defaults: dict[str, object] = {
        "track_id": "visual",
        "kind": TimelineTrackKind.VISUAL,
        "clips": (
            _media_clip(clip_id="visual-1", start_ms=0, duration_ms=3_000),
            _media_clip(
                clip_id="visual-2",
                start_ms=3_000,
                duration_ms=4_000,
                source_in_ms=0,
                source_out_ms=4_000,
            ),
        ),
    }
    defaults.update(overrides)
    return TimelineTrack(**defaults)  # type: ignore[arg-type]


def _audible_clip(**overrides: object) -> TimelineClip:
    """A clip fit for an audible lane: it states a level and a stretch."""
    defaults: dict[str, object] = {"gain_db": -12.0}
    defaults.update(overrides)
    return _media_clip(**defaults)


def test_a_visual_track_runs_end_to_end_from_zero() -> None:
    track = _visual_track()
    assert track.clips[0].start_ms == 0
    assert track.clips[1].start_ms == track.clips[0].end_ms


def test_a_still_image_is_welcome_on_the_visual_track() -> None:
    """A still image (a material with no source window) is a first-class
    visual clip — the picture lane's shape rule only cares about level and
    text, not whether a window is present."""
    track = _visual_track(
        clips=(
            _media_clip(
                clip_id="visual-1",
                start_ms=0,
                duration_ms=2_000,
                source_in_ms=None,
                source_out_ms=None,
            ),
        )
    )
    assert track.clips[0].source_in_ms is None


def test_a_visual_track_refuses_a_gap_that_would_render_as_black() -> None:
    with pytest.raises(InvalidTimelineModel):
        _visual_track(
            clips=(
                _media_clip(clip_id="visual-1", start_ms=0, duration_ms=3_000),
                _media_clip(
                    clip_id="visual-2",
                    start_ms=3_500,
                    duration_ms=4_000,
                    source_in_ms=0,
                    source_out_ms=4_000,
                ),
            )
        )


def test_a_visual_track_refuses_to_start_late() -> None:
    with pytest.raises(InvalidTimelineModel):
        _visual_track(clips=(_media_clip(clip_id="visual-1", start_ms=500, duration_ms=3_000),))


def test_a_transition_overlaps_its_two_clips_so_the_film_really_is_that_long() -> None:
    """xfade renders a + b - transition; the timeline must say the same."""
    track = _visual_track(
        clips=(
            _media_clip(clip_id="visual-1", start_ms=0, duration_ms=3_000),
            _media_clip(
                clip_id="visual-2",
                start_ms=2_200,
                duration_ms=4_000,
                source_in_ms=0,
                source_out_ms=4_000,
                transition_in=TimelineTransition(TransitionKind.DISSOLVE, 800),
            ),
        )
    )
    assert track.clips[-1].end_ms == 6_200


def test_a_transition_that_does_not_overlap_is_rejected() -> None:
    with pytest.raises(InvalidTimelineModel):
        _visual_track(
            clips=(
                _media_clip(clip_id="visual-1", start_ms=0, duration_ms=3_000),
                _media_clip(
                    clip_id="visual-2",
                    start_ms=3_000,
                    duration_ms=4_000,
                    source_in_ms=0,
                    source_out_ms=4_000,
                    transition_in=TimelineTransition(TransitionKind.DISSOLVE, 800),
                ),
            )
        )


def test_a_transition_cannot_swallow_either_clip_whole() -> None:
    """The transition (800ms) is exactly as long as the previous clip (800ms).

    `visual-1` needs its own matching source window here — without one it
    would fail `TimelineClip`'s own duration/window check first, and the
    test would pass without ever reaching `TimelineTrack`'s layout rule.
    """
    with pytest.raises(InvalidTimelineModel):
        _visual_track(
            clips=(
                _media_clip(
                    clip_id="visual-1",
                    start_ms=0,
                    duration_ms=800,
                    source_in_ms=0,
                    source_out_ms=800,
                ),
                _media_clip(
                    clip_id="visual-2",
                    start_ms=0,
                    duration_ms=4_000,
                    source_in_ms=0,
                    source_out_ms=4_000,
                    transition_in=TimelineTransition(TransitionKind.DISSOLVE, 800),
                ),
            )
        )


def test_a_transition_chain_cannot_swallow_a_clip_between_two_others() -> None:
    """Two back-to-back transitions can eat a clip whole even when neither
    one alone reaches the *original* duration of its immediate neighbour.

    `visual-2` is 1000ms. The incoming transition from `visual-1` eats 999ms
    of its head; the incoming transition into `visual-3` eats 999ms of its
    tail. Those two eaten stretches overlap almost completely, so `visual-2`
    never plays a single frame on its own — checking each transition only
    against the previous clip's raw `duration_ms` cannot see this, because
    the previous clip's *own* incoming transition already claimed part of
    that duration.
    """
    with pytest.raises(InvalidTimelineModel):
        _visual_track(
            clips=(
                _media_clip(
                    clip_id="visual-1",
                    start_ms=0,
                    duration_ms=1_000,
                    source_in_ms=0,
                    source_out_ms=1_000,
                ),
                _media_clip(
                    clip_id="visual-2",
                    start_ms=1,
                    duration_ms=1_000,
                    source_in_ms=0,
                    source_out_ms=1_000,
                    transition_in=TimelineTransition(TransitionKind.DISSOLVE, 999),
                ),
                _media_clip(
                    clip_id="visual-3",
                    start_ms=2,
                    duration_ms=1_000,
                    source_in_ms=0,
                    source_out_ms=1_000,
                    transition_in=TimelineTransition(TransitionKind.DISSOLVE, 999),
                ),
            )
        )


def test_a_legitimate_transition_chain_is_still_accepted() -> None:
    """The accepting counterpart to the chain-swallow rejection above: two
    chained transitions are fine as long as neither exhausts the tail the
    previous transition already left behind.
    """
    track = _visual_track(
        clips=(
            _media_clip(clip_id="visual-1", start_ms=0, duration_ms=3_000),
            _media_clip(
                clip_id="visual-2",
                start_ms=2_200,
                duration_ms=4_000,
                source_in_ms=0,
                source_out_ms=4_000,
                transition_in=TimelineTransition(TransitionKind.DISSOLVE, 800),
            ),
            _media_clip(
                clip_id="visual-3",
                start_ms=5_400,
                duration_ms=2_000,
                source_in_ms=0,
                source_out_ms=2_000,
                transition_in=TimelineTransition(TransitionKind.DISSOLVE, 800),
            ),
        )
    )
    assert track.clips[-1].end_ms == 7_400


def test_the_first_clip_has_nothing_to_transition_from() -> None:
    with pytest.raises(InvalidTimelineModel):
        _visual_track(
            clips=(
                _media_clip(
                    clip_id="visual-1",
                    start_ms=0,
                    duration_ms=3_000,
                    transition_in=TimelineTransition(TransitionKind.FADE, 500),
                ),
            )
        )


def test_a_visual_clip_carries_no_level_of_its_own() -> None:
    with pytest.raises(InvalidTimelineModel):
        _visual_track(
            clips=(_media_clip(clip_id="visual-1", start_ms=0, duration_ms=3_000, gain_db=0.0),)
        )


@pytest.mark.parametrize(
    "kind",
    [TimelineTrackKind.NARRATION, TimelineTrackKind.AMBIENT, TimelineTrackKind.MUSIC],
)
def test_an_audible_track_states_a_level_for_every_clip(kind: TimelineTrackKind) -> None:
    TimelineTrack("sound", kind, (_audible_clip(clip_id="sound-1"),))
    with pytest.raises(InvalidTimelineModel):
        TimelineTrack("sound", kind, (_media_clip(clip_id="sound-1"),))


@pytest.mark.parametrize(
    "kind",
    [TimelineTrackKind.NARRATION, TimelineTrackKind.AMBIENT, TimelineTrackKind.MUSIC],
)
def test_no_audible_lane_will_take_a_clip_with_no_stretch_to_play(
    kind: TimelineTrackKind,
) -> None:
    """A windowless clip cannot carry a level, and an audible lane demands one.

    The clip itself is legal — a still image occupies time without playing
    any stretch of a source. It is this lane that has no use for it.
    """
    windowless = _media_clip(clip_id="sound-1", source_in_ms=None, source_out_ms=None)
    with pytest.raises(InvalidTimelineModel):
        TimelineTrack("sound", kind, (windowless,))


@pytest.mark.parametrize(
    "kind",
    [TimelineTrackKind.NARRATION, TimelineTrackKind.AMBIENT, TimelineTrackKind.MUSIC],
)
def test_audio_gets_no_transitions_in_the_first_release(kind: TimelineTrackKind) -> None:
    with pytest.raises(InvalidTimelineModel):
        TimelineTrack(
            "sound",
            kind,
            (
                _audible_clip(clip_id="sound-1", start_ms=0, duration_ms=3_000),
                _audible_clip(
                    clip_id="sound-2",
                    start_ms=3_000,
                    duration_ms=3_000,
                    source_in_ms=0,
                    source_out_ms=3_000,
                    transition_in=TimelineTransition(TransitionKind.FADE, 500),
                ),
            ),
        )


def test_a_silent_stretch_between_two_narration_clips_is_fine() -> None:
    track = TimelineTrack(
        "narration",
        TimelineTrackKind.NARRATION,
        (
            _audible_clip(
                clip_id="line-1", start_ms=0, duration_ms=2_000, source_in_ms=0, source_out_ms=2_000
            ),
            _audible_clip(
                clip_id="line-2",
                start_ms=2_600,
                duration_ms=2_000,
                source_in_ms=0,
                source_out_ms=2_000,
            ),
        ),
    )
    assert track.clips[1].start_ms > track.clips[0].end_ms


@pytest.mark.parametrize(
    "kind",
    [
        TimelineTrackKind.NARRATION,
        TimelineTrackKind.AMBIENT,
        TimelineTrackKind.MUSIC,
        TimelineTrackKind.CAPTION,
    ],
)
def test_no_track_lets_two_clips_play_over_each_other(kind: TimelineTrackKind) -> None:
    first = _caption_clip if kind is TimelineTrackKind.CAPTION else _audible_clip
    with pytest.raises(InvalidTimelineModel):
        TimelineTrack(
            "lane",
            kind,
            (
                first(
                    clip_id="lane-1",
                    start_ms=0,
                    duration_ms=2_000,
                    **(
                        {}
                        if kind is TimelineTrackKind.CAPTION
                        else {"source_in_ms": 0, "source_out_ms": 2_000}
                    ),
                ),
                first(
                    clip_id="lane-2",
                    start_ms=1_500,
                    duration_ms=2_000,
                    **(
                        {}
                        if kind is TimelineTrackKind.CAPTION
                        else {"source_in_ms": 0, "source_out_ms": 2_000}
                    ),
                ),
            ),
        )


def test_a_caption_track_only_draws_text() -> None:
    TimelineTrack("caption", TimelineTrackKind.CAPTION, (_caption_clip(),))
    with pytest.raises(InvalidTimelineModel):
        TimelineTrack("caption", TimelineTrackKind.CAPTION, (_media_clip(),))


def test_a_caption_lane_refuses_a_clip_that_wants_to_dissolve() -> None:
    """A caption appears and disappears; only the picture lane dissolves.

    The level half of this rule lives one layer down — a caption clip
    cannot carry `gain_db` at all, so it never reaches a lane. See
    `test_gain_requires_something_audible_to_adjust` in Task 2.
    """
    dissolving = _caption_clip(transition_in=TimelineTransition(TransitionKind.FADE, 300))
    with pytest.raises(InvalidTimelineModel):
        TimelineTrack("caption", TimelineTrackKind.CAPTION, (dissolving,))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("track_id", "Bad_ID"),
        ("kind", "visual"),
        ("clips", ()),
        ("clips", [_media_clip()]),
    ],
)
def test_track_structural_bounds_fail_closed(field: str, value: object) -> None:
    with pytest.raises(InvalidTimelineModel):
        _visual_track(**{field: value})


@pytest.mark.parametrize(
    "kind",
    [
        TimelineTrackKind.VISUAL,
        TimelineTrackKind.NARRATION,
        TimelineTrackKind.AMBIENT,
        TimelineTrackKind.MUSIC,
    ],
)
def test_no_media_lane_will_take_a_caption_shaped_clip(kind: TimelineTrackKind) -> None:
    """A caption-shaped clip (text, no material) only fits the caption lane."""
    with pytest.raises(InvalidTimelineModel):
        TimelineTrack("lane", kind, (_caption_clip(),))


def test_a_track_ends_when_its_last_clip_does() -> None:
    track = _visual_track()
    assert track.end_ms == track.clips[-1].end_ms


def test_a_track_refuses_two_clips_with_the_same_id() -> None:
    with pytest.raises(InvalidTimelineModel):
        _visual_track(
            clips=(
                _media_clip(clip_id="same", start_ms=0, duration_ms=3_000),
                _media_clip(
                    clip_id="same",
                    start_ms=3_000,
                    duration_ms=3_000,
                    source_in_ms=0,
                    source_out_ms=3_000,
                ),
            )
        )


def _caption_chain(count: int) -> tuple[TimelineClip, ...]:
    """`count` non-overlapping caption clips, 2ms apart, for boundary tests."""
    return tuple(
        _caption_clip(clip_id=f"c{i}", start_ms=i * 2, duration_ms=1) for i in range(count)
    )


def test_max_clips_per_track_boundary_is_inclusive() -> None:
    track = TimelineTrack("caption", TimelineTrackKind.CAPTION, _caption_chain(MAX_CLIPS_PER_TRACK))
    assert len(track.clips) == MAX_CLIPS_PER_TRACK


def test_more_than_max_clips_per_track_is_rejected() -> None:
    with pytest.raises(InvalidTimelineModel):
        TimelineTrack("caption", TimelineTrackKind.CAPTION, _caption_chain(MAX_CLIPS_PER_TRACK + 1))


def _timeline(**overrides: object) -> Timeline:
    defaults: dict[str, object] = {
        "timeline_id": TimelineId.new(),
        "revision": 1,
        "duration_ms": 7_000,
        "tracks": (_visual_track(),),
        "created_at": datetime(2026, 7, 29, 10, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    return Timeline(**defaults)  # type: ignore[arg-type]


def test_a_timeline_is_as_long_as_its_picture_lane() -> None:
    assert _timeline().duration_ms == _visual_track().end_ms


def test_a_timeline_refuses_to_claim_a_length_its_picture_does_not_fill() -> None:
    with pytest.raises(InvalidTimelineModel):
        _timeline(duration_ms=9_000)
    with pytest.raises(InvalidTimelineModel):
        _timeline(duration_ms=5_000)


def test_a_timeline_without_a_picture_lane_is_not_a_film() -> None:
    with pytest.raises(InvalidTimelineModel):
        _timeline(
            tracks=(
                TimelineTrack(
                    "narration",
                    TimelineTrackKind.NARRATION,
                    (
                        _audible_clip(
                            clip_id="line-1",
                            start_ms=0,
                            duration_ms=7_000,
                            source_in_ms=0,
                            source_out_ms=7_000,
                        ),
                    ),
                ),
            )
        )


def test_each_lane_appears_at_most_once() -> None:
    with pytest.raises(InvalidTimelineModel):
        _timeline(tracks=(_visual_track(track_id="visual-a"), _visual_track(track_id="visual-b")))


def test_a_full_timeline_carries_picture_narration_ambient_music_and_captions() -> None:
    timeline = _timeline(
        tracks=(
            _visual_track(),
            TimelineTrack(
                "narration",
                TimelineTrackKind.NARRATION,
                (
                    _audible_clip(
                        clip_id="line-1",
                        start_ms=0,
                        duration_ms=6_000,
                        source_in_ms=0,
                        source_out_ms=6_000,
                    ),
                ),
            ),
            TimelineTrack(
                "ambient",
                TimelineTrackKind.AMBIENT,
                (
                    _audible_clip(
                        clip_id="room-1",
                        start_ms=0,
                        duration_ms=7_000,
                        source_in_ms=0,
                        source_out_ms=7_000,
                    ),
                ),
            ),
            TimelineTrack(
                "music",
                TimelineTrackKind.MUSIC,
                (
                    _audible_clip(
                        clip_id="bgm-1",
                        start_ms=0,
                        duration_ms=7_000,
                        source_in_ms=0,
                        source_out_ms=7_000,
                        gain_db=-24.0,
                    ),
                ),
            ),
            TimelineTrack(
                "caption",
                TimelineTrackKind.CAPTION,
                (_caption_clip(clip_id="cap-1", start_ms=0, duration_ms=3_000),),
            ),
        )
    )
    assert len(timeline.tracks) == len(TimelineTrackKind)


def test_nothing_may_run_past_the_end_of_the_film() -> None:
    with pytest.raises(InvalidTimelineModel):
        _timeline(
            tracks=(
                _visual_track(),
                TimelineTrack(
                    "music",
                    TimelineTrackKind.MUSIC,
                    (
                        _audible_clip(
                            clip_id="bgm-1",
                            start_ms=0,
                            duration_ms=9_000,
                            source_in_ms=0,
                            source_out_ms=9_000,
                        ),
                    ),
                ),
            )
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timeline_id", "not-an-id"),
        ("revision", 0),
        ("revision", 1.0),
        ("revision", True),
        ("duration_ms", MIN_TIMELINE_DURATION_MS - 1),
        ("duration_ms", MAX_TIMELINE_DURATION_MS + 1),
        # A float that numerically equals the default picture lane's own end_ms:
        # the range check and the "picture.end_ms == duration_ms" check would both
        # pass it silently if the type check were ever dropped, the same way
        # ("revision", True) isolates revision's own type check above.
        ("duration_ms", 7_000.0),
        ("tracks", ()),
        ("tracks", [_visual_track()]),
        ("created_at", datetime(2026, 7, 29, 10, 0)),
        ("created_at", "2026-07-29T10:00:00Z"),
    ],
)
def test_timeline_structural_bounds_fail_closed(field: str, value: object) -> None:
    with pytest.raises(InvalidTimelineModel):
        _timeline(**{field: value})


def test_a_timeline_refuses_two_lanes_with_the_same_id() -> None:
    with pytest.raises(InvalidTimelineModel):
        _timeline(
            tracks=(
                _visual_track(track_id="same"),
                TimelineTrack(
                    "same",
                    TimelineTrackKind.CAPTION,
                    (_caption_clip(clip_id="cap-1", start_ms=0, duration_ms=3_000),),
                ),
            )
        )
