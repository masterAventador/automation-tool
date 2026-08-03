"""Turning a storyboard into a list of renders — the step that was missing.

`catalog_parts` has been on every beat since PC-04 and nothing read it: the
composition was still drawn from the four built-in layouts, so the 134 parts
were validated and then ignored. Everything else route A needs was built around
that hole — the slot table (PC-03), the packaged catalog (PC-16), the per-part
render (PC-05), the join (PC-06), the timeline (PC-08). This is the piece that
connects them.

A beat becomes one shot. A beat that named a part is rendered from that part's
working copy on the part's own stage; a beat that named none falls back to the
template composition on the template's stage, which is what "本机 4 layout 模板段
与零件段可混排" means in practice.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest

from automation_tool.executor.motion_authoring.film_assembly import (
    AssembledFilm,
    AssemblyRejected,
    BeatPlan,
    assemble_film,
)
from automation_tool.executor.motion_authoring.film_timeline import FilmOverBudget

TEMPLATE_CANVAS = {"width": 640, "height": 360, "deviceScaleFactor": 2}


def _catalog(tmp_path: Path) -> Path:
    """A catalog with one part, laid out the way the release tree is."""
    root = tmp_path / "motion-catalog"
    part = root / "items" / "lt-bold-block"
    part.mkdir(parents=True)
    (part / "lt-bold-block.html").write_text(
        "<!doctype html><html><head><style>.n{font-size:58px}</style></head><body>"
        '<div class="n">Maya Chen</div></body></html>',
        encoding="utf-8",
    )
    return root


class _Workspace:
    def __init__(self) -> None:
        self.written: dict[str, bytes] = {}

    def write_text(self, relative: str, text: str) -> Path:
        self.written[relative] = text.encode("utf-8")
        return Path(relative)

    def write_bytes(self, relative: str, payload: bytes) -> Path:
        self.written[relative] = bytes(payload)
        return Path(relative)


SLOTS = {
    "parts": [
        {
            "name": "lt-bold-block",
            "slots": [{"index": 1, "original": "Maya Chen", "parentTag": "div"}],
        }
    ]
}
BUDGET = {
    "parts": [
        {
            "name": "lt-bold-block",
            "slots": [
                {
                    "index": 1,
                    "original": "Maya Chen",
                    "usableWidthPx": 366,
                    "fontSizePx": 58,
                    "baselineOverflowsX": False,
                    "baselineOverflowsY": True,
                }
            ],
        }
    ]
}
DURATIONS = {"lt-bold-block": 4.8}
DIMENSIONS = {"lt-bold-block": (1920, 1080)}


def assemble(
    beats: Sequence[BeatPlan],
    tmp_path: Path,
    *,
    workspace: object | None = None,
    slot_table: Mapping[str, object] = SLOTS,
    part_durations: Mapping[str, float] = DURATIONS,
    font_css_for: Callable[[str], str] = lambda _text: "@font-face{}",
    catalog_root: Path | None = None,
) -> AssembledFilm:
    return assemble_film(
        beats=beats,
        workspace=_Workspace() if workspace is None else workspace,
        catalog_root=_catalog(tmp_path) if catalog_root is None else catalog_root,
        slot_table=slot_table,
        slot_budget=BUDGET,
        part_durations=part_durations,
        part_dimensions=DIMENSIONS,
        part_types={"lt-bold-block": "block"},
        template_canvas=TEMPLATE_CANVAS,
        frames_per_second=30,
        segment_frames_maximum=600,
        font_css_for=font_css_for,
    )


def test_a_beat_that_named_a_part_renders_on_that_parts_stage(tmp_path: Path) -> None:
    film = assemble(
        [BeatPlan(beat_id="b1", part="lt-bold-block", copy={1: "张三"}, voice_seconds=None)],
        tmp_path,
    )

    assert len(film.segments) == 1
    segment = film.segments[0]
    assert segment.canvas == {"width": 1920, "height": 1080, "deviceScaleFactor": 1}
    assert segment.entry_html.endswith("lt-bold-block.html")
    assert segment.frames == 144


def test_a_beat_that_named_no_part_falls_back_to_the_template(tmp_path: Path) -> None:
    """Mixing is the point: the template still carries beats no part fits."""
    film = assemble(
        [BeatPlan(beat_id="b1", part=None, copy={}, voice_seconds=5.0)],
        tmp_path,
    )

    assert film.segments[0].canvas == TEMPLATE_CANVAS
    assert film.segments[0].part is None
    assert film.segments[0].frames == 150


def test_a_film_mixes_part_segments_and_template_segments(tmp_path: Path) -> None:
    film = assemble(
        [
            BeatPlan(beat_id="b1", part="lt-bold-block", copy={1: "张三"}, voice_seconds=None),
            BeatPlan(beat_id="b2", part=None, copy={}, voice_seconds=3.0),
        ],
        tmp_path,
    )

    assert [segment.part for segment in film.segments] == ["lt-bold-block", None]
    assert film.total_frames == 144 + 90


def test_each_template_shot_samples_its_own_stretch_of_the_composition(tmp_path: Path) -> None:
    """The defect that made the first real film play the same footage twice.

    A template segment loads the whole composition — one document that draws
    every beat along one timeline. The Worker spreads *the page's whole seekable
    duration* over the frames it is asked for, so two template segments both
    rendered the entire film, each squeezed into its own half. Measured on the
    kept artifact of 2026-07-28: 12 seconds, two identical 6 second halves at
    double speed, and every mechanical check — codec, canvas, frame count,
    duration, the still-image gate — green over it.

    So a segment has to say which stretch of its source it is. For a template
    beat that is the window the storyboard gave it.
    """
    film = assemble(
        [
            BeatPlan(
                beat_id="b1",
                part=None,
                copy={},
                voice_seconds=None,
                declared_seconds=6.0,
                start_seconds=0.0,
            ),
            BeatPlan(
                beat_id="b2",
                part=None,
                copy={},
                voice_seconds=None,
                declared_seconds=6.0,
                start_seconds=6.0,
            ),
        ],
        tmp_path,
    )

    first, second = film.segments
    # 毫秒整数：命令签名绑定的规范化 JSON 要在三门语言里逐字节一致，浮点过不了这一关。
    assert (first.source_start_millis, first.source_end_millis) == (0, 6000)
    assert (second.source_start_millis, second.source_end_millis) == (6000, 12000)
    # Same document, different stretch of it — that is what "one render per
    # shot" has to mean for beats the template carries.
    assert first.entry_html == second.entry_html


def test_a_template_window_stays_inside_the_beat_even_when_the_line_runs_long(
    tmp_path: Path,
) -> None:
    """镜头变长不等于这一镜在 composition 上占的地方变大。

    `start_seconds` 与 `declared_seconds` 说的是 composition 自己那条时间轴——
    `_compose` 就是照它把每张卡画上去的。而镜头长度是 max(旁白, 动效)，是另一条
    时间轴上的数。两者相加就会越界：第一镜声明 6 秒、旁白 8 秒，窗口若取
    [0, 8000)，而第二镜的卡在 6 秒处就已经出现——于是第一镜的最后四分之一
    先把第二镜的卡放一遍，切过去再放一遍。

    帧数、时长、编码、静帧门禁全都不会响，和本项修的那个缺陷同一个signature。
    今天旁白还没接上（`voice_seconds` 恒为 None），所以这条现在守的是将来。
    """
    film = assemble(
        [
            BeatPlan(
                beat_id="b1",
                part=None,
                copy={},
                voice_seconds=8.0,
                declared_seconds=6.0,
                start_seconds=0.0,
            ),
            BeatPlan(
                beat_id="b2",
                part=None,
                copy={},
                voice_seconds=None,
                declared_seconds=6.0,
                start_seconds=6.0,
            ),
        ],
        tmp_path,
    )

    first, second = film.segments
    # 镜头真的变长了：8 秒 × 30fps。
    assert first.frames == 240
    # 但它在 composition 上仍然只占自己声明的那 6 秒，多出来的帧是把这 6 秒
    # 重新采样铺满——零件段本来就是这个规则。
    assert (first.source_start_millis, first.source_end_millis) == (0, 6000)
    assert (second.source_start_millis, second.source_end_millis) == (6000, 12000)


def test_a_part_segment_samples_its_own_document_from_the_start(tmp_path: Path) -> None:
    """A part is its own composition, so its window is its own timeline."""
    film = assemble(
        [BeatPlan(beat_id="b1", part="lt-bold-block", copy={1: "张三"}, voice_seconds=None)],
        tmp_path,
    )

    segment = film.segments[0]
    assert segment.source_start_millis == 0
    assert segment.source_end_millis == round(DURATIONS["lt-bold-block"] * 1000)


def test_narration_longer_than_the_motion_lengthens_the_shot(tmp_path: Path) -> None:
    """PC-08's rule, reaching the render request that is actually sent."""
    film = assemble(
        [BeatPlan(beat_id="b1", part="lt-bold-block", copy={1: "张三"}, voice_seconds=8.0)],
        tmp_path,
    )

    assert film.segments[0].frames == 240


def test_a_long_visual_timeline_is_compressed_into_one_complete_capture(tmp_path: Path) -> None:
    """Keep the full 24s source window without asking the Worker for 720 frames."""
    film = assemble(
        [BeatPlan(beat_id="b1", part="lt-bold-block", copy={}, voice_seconds=None)],
        tmp_path,
        part_durations={"lt-bold-block": 24.0},
        slot_table={"parts": []},
    )

    segment = film.segments[0]
    assert segment.frames == 600
    assert segment.seconds == 20.0
    assert segment.source_end_millis == 24_000


def test_a_part_the_catalog_does_not_carry_is_refused(tmp_path: Path) -> None:
    with pytest.raises(AssemblyRejected):
        assemble(
            [BeatPlan(beat_id="b1", part="not-a-part", copy={}, voice_seconds=2.0)],
            tmp_path,
        )


def test_a_visual_only_part_with_no_frozen_slots_is_rendered_unchanged(tmp_path: Path) -> None:
    """A missing copy slot must not make an otherwise renderable part unusable."""
    film = assemble(
        [BeatPlan(beat_id="b1", part="lt-bold-block", copy={}, voice_seconds=None)],
        tmp_path,
        slot_table={"parts": []},
    )

    assert film.segments[0].part == "lt-bold-block"
    assert film.segments[0].slot_budgets == ()


def test_a_visual_only_part_does_not_request_copy_replacement_fonts(tmp_path: Path) -> None:
    """No frozen copy means the part keeps its visual text and its font fallback."""

    def unexpected_font_request(_name: str) -> str:
        raise AssertionError("visual-only part requested copy replacement fonts")

    film = assemble(
        [BeatPlan(beat_id="b1", part="lt-bold-block", copy={}, voice_seconds=None)],
        tmp_path,
        slot_table={"parts": []},
        font_css_for=unexpected_font_request,
    )

    assert film.segments[0].part == "lt-bold-block"


def test_reusing_one_part_keeps_each_beats_own_working_copy(tmp_path: Path) -> None:
    workspace = _Workspace()
    film = assemble(
        [
            BeatPlan(
                beat_id="b1",
                part="lt-bold-block",
                copy={1: "第一镜"},
                voice_seconds=None,
            ),
            BeatPlan(
                beat_id="b2",
                part="lt-bold-block",
                copy={1: "第二镜"},
                voice_seconds=None,
            ),
        ],
        tmp_path,
        workspace=workspace,
    )

    first, second = film.segments
    assert first.entry_html != second.entry_html
    assert "第一镜" in workspace.written[first.entry_html].decode("utf-8")
    assert "第二镜" in workspace.written[second.entry_html].decode("utf-8")


def test_a_visual_only_part_refuses_copy_that_has_no_frozen_anchor(tmp_path: Path) -> None:
    with pytest.raises(AssemblyRejected):
        assemble(
            [BeatPlan(beat_id="b1", part="lt-bold-block", copy={1: "张三"}, voice_seconds=None)],
            tmp_path,
            slot_table={"parts": []},
        )


def test_a_shot_too_long_for_one_render_is_refused_before_anything_is_written(
    tmp_path: Path,
) -> None:
    workspace = _Workspace()
    with pytest.raises(FilmOverBudget):
        assemble(
            [BeatPlan(beat_id="b1", part=None, copy={}, voice_seconds=25.0)],
            tmp_path,
            workspace=workspace,
        )
    assert workspace.written == {}


def test_the_plan_says_how_long_the_render_will_take(tmp_path: Path) -> None:
    film = assemble(
        [
            BeatPlan(beat_id="b1", part="lt-bold-block", copy={1: "张三"}, voice_seconds=None),
            BeatPlan(beat_id="b2", part=None, copy={}, voice_seconds=3.0),
        ],
        tmp_path,
    )

    # (30 + 144*0.4) + (30 + 90*0.4) = 87.6 + 66 = 153.6
    assert film.estimated_render_seconds == pytest.approx(153.6)


def test_every_segment_carries_the_budget_its_copy_has_to_fit(tmp_path: Path) -> None:
    """PC-14 needs the baseline at render time, not at freezing time."""
    film = assemble(
        [BeatPlan(beat_id="b1", part="lt-bold-block", copy={1: "张三"}, voice_seconds=None)],
        tmp_path,
    )

    budgets = film.segments[0].slot_budgets
    assert budgets[0].index == 1
    assert budgets[0].usable_width_px == 366
    assert budgets[0].baseline_overflows_y is True


def test_a_film_with_no_beats_is_refused() -> None:
    """An empty film is a blank stretch every downstream gate happily accepts."""
    with pytest.raises(AssemblyRejected):
        assemble_film(
            beats=(),
            workspace=_Workspace(),
            catalog_root=Path("/nonexistent"),
            slot_table=SLOTS,
            slot_budget=BUDGET,
            part_durations=DURATIONS,
            part_dimensions=DIMENSIONS,
            part_types={"lt-bold-block": "block"},
            template_canvas=TEMPLATE_CANVAS,
            frames_per_second=30,
            segment_frames_maximum=600,
            font_css_for=lambda _text: "",
        )


_MALFORMED_CONTRACTS: list[tuple[str, dict[str, object]]] = [
    ("parts is not a list", {"parts": {}}),
    ("no parts key at all", {}),
    ("a part that is not an object", {"parts": ["lt-bold-block"]}),
    ("a part with no name", {"parts": [{"slots": []}]}),
    ("a part whose slots are not a list", {"parts": [{"name": "lt-bold-block", "slots": {}}]}),
    (
        "the same part twice",
        {
            "parts": [
                {"name": "lt-bold-block", "slots": []},
                {"name": "lt-bold-block", "slots": []},
            ]
        },
    ),
    (
        "a slot that is not an object",
        {"parts": [{"name": "lt-bold-block", "slots": ["1"]}]},
    ),
]


@pytest.mark.parametrize(("label", "contract"), _MALFORMED_CONTRACTS)
def test_a_malformed_slot_table_is_refused(
    label: str, contract: dict[str, object], tmp_path: Path
) -> None:
    """The table is what says which run each piece of copy lands in; a broken one
    cannot be read around."""
    with pytest.raises(AssemblyRejected):
        assemble(
            [BeatPlan(beat_id="b1", part="lt-bold-block", copy={1: "张三"}, voice_seconds=None)],
            tmp_path,
            slot_table=contract,
        )
    assert label


def test_a_slot_entry_missing_any_of_its_three_anchors_is_refused(tmp_path: Path) -> None:
    complete = {"index": 1, "original": "Maya Chen", "parentTag": "div"}
    catalog = _catalog(tmp_path)

    cases: list[tuple[str, dict[str, object]]] = [
        ("an index that is not an int", {"index": "1"}),
        ("an original that is not text", {"original": 1}),
        ("a parent tag that is not text", {"parentTag": None}),
    ]
    for label, overrides in cases:
        with pytest.raises(AssemblyRejected):
            assemble(
                [
                    BeatPlan(
                        beat_id="b1",
                        part="lt-bold-block",
                        copy={1: "张三"},
                        voice_seconds=None,
                    )
                ],
                tmp_path,
                catalog_root=catalog,
                slot_table={
                    "parts": [{"name": "lt-bold-block", "slots": [{**complete, **overrides}]}]
                },
            )
        assert label


@pytest.mark.parametrize(("label", "contract"), _MALFORMED_CONTRACTS)
def test_a_malformed_slot_budget_is_refused(
    label: str, contract: dict[str, object], tmp_path: Path
) -> None:
    """Without the budget nothing knows whether the copy fits the frame it lands in."""
    with pytest.raises(AssemblyRejected):
        assemble_film(
            beats=[
                BeatPlan(beat_id="b1", part="lt-bold-block", copy={1: "张三"}, voice_seconds=None)
            ],
            workspace=_Workspace(),
            catalog_root=_catalog(tmp_path),
            slot_table=SLOTS,
            slot_budget=contract,
            part_durations=DURATIONS,
            part_dimensions=DIMENSIONS,
            part_types={"lt-bold-block": "block"},
            template_canvas=TEMPLATE_CANVAS,
            frames_per_second=30,
            segment_frames_maximum=600,
            font_css_for=lambda _text: "@font-face{}",
        )
    assert label


def test_a_budget_entry_missing_any_of_its_measurements_is_refused(tmp_path: Path) -> None:
    complete: dict[str, object] = {
        "index": 1,
        "original": "Maya Chen",
        "usableWidthPx": 366,
        "fontSizePx": 58,
        "baselineOverflowsX": False,
        "baselineOverflowsY": True,
    }
    catalog = _catalog(tmp_path)

    cases: list[tuple[str, dict[str, object]]] = [
        ("an index that is not an int", {"index": "1"}),
        ("a width that is not an int", {"usableWidthPx": 366.0}),
        ("a font size that is not an int", {"fontSizePx": "58"}),
        ("an overflow flag that is not a bool", {"baselineOverflowsX": 0}),
        ("the other overflow flag that is not a bool", {"baselineOverflowsY": 1}),
    ]
    for label, overrides in cases:
        with pytest.raises(AssemblyRejected):
            assemble_film(
                beats=[
                    BeatPlan(
                        beat_id="b1",
                        part="lt-bold-block",
                        copy={1: "张三"},
                        voice_seconds=None,
                    )
                ],
                workspace=_Workspace(),
                catalog_root=catalog,
                slot_table=SLOTS,
                slot_budget={
                    "parts": [{"name": "lt-bold-block", "slots": [{**complete, **overrides}]}]
                },
                part_durations=DURATIONS,
                part_dimensions=DIMENSIONS,
                part_types={"lt-bold-block": "block"},
                template_canvas=TEMPLATE_CANVAS,
                frames_per_second=30,
                segment_frames_maximum=600,
                font_css_for=lambda _text: "@font-face{}",
            )
        assert label


def test_a_beat_naming_a_part_with_no_declared_type_is_refused(tmp_path: Path) -> None:
    """The type decides which builder wraps it; without one there is nothing to pick."""
    with pytest.raises(AssemblyRejected):
        assemble_film(
            beats=[
                BeatPlan(beat_id="b1", part="lt-bold-block", copy={1: "张三"}, voice_seconds=None)
            ],
            workspace=_Workspace(),
            catalog_root=_catalog(tmp_path),
            slot_table=SLOTS,
            slot_budget=BUDGET,
            part_durations=DURATIONS,
            part_dimensions=DIMENSIONS,
            part_types={},
            template_canvas=TEMPLATE_CANVAS,
            frames_per_second=30,
            segment_frames_maximum=600,
            font_css_for=lambda _text: "@font-face{}",
        )
