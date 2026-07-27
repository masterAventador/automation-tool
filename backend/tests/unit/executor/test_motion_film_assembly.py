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

import json
from pathlib import Path

import pytest

from automation_tool.executor.motion_authoring.film_assembly import (
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


def assemble(beats, tmp_path, **overrides):
    arguments = {
        "beats": beats,
        "workspace": _Workspace(),
        "catalog_root": _catalog(tmp_path),
        "slot_table": SLOTS,
        "slot_budget": BUDGET,
        "part_durations": DURATIONS,
        "part_dimensions": DIMENSIONS,
        "template_canvas": TEMPLATE_CANVAS,
        "frames_per_second": 30,
        "segment_frames_maximum": 600,
        "font_css_for": lambda text: "@font-face{}",
    }
    arguments.update(overrides)
    return assemble_film(**arguments)


def test_a_beat_that_named_a_part_renders_on_that_parts_stage(tmp_path) -> None:
    film = assemble(
        [BeatPlan(beat_id="b1", part="lt-bold-block", copy={1: "张三"}, voice_seconds=None)],
        tmp_path,
    )

    assert len(film.segments) == 1
    segment = film.segments[0]
    assert segment.canvas == {"width": 1920, "height": 1080, "deviceScaleFactor": 1}
    assert segment.entry_html.endswith("lt-bold-block.html")
    assert segment.frames == 144


def test_a_beat_that_named_no_part_falls_back_to_the_template(tmp_path) -> None:
    """Mixing is the point: the template still carries beats no part fits."""
    film = assemble(
        [BeatPlan(beat_id="b1", part=None, copy={}, voice_seconds=5.0)],
        tmp_path,
    )

    assert film.segments[0].canvas == TEMPLATE_CANVAS
    assert film.segments[0].part is None
    assert film.segments[0].frames == 150


def test_a_film_mixes_part_segments_and_template_segments(tmp_path) -> None:
    film = assemble(
        [
            BeatPlan(beat_id="b1", part="lt-bold-block", copy={1: "张三"}, voice_seconds=None),
            BeatPlan(beat_id="b2", part=None, copy={}, voice_seconds=3.0),
        ],
        tmp_path,
    )

    assert [segment.part for segment in film.segments] == ["lt-bold-block", None]
    assert film.total_frames == 144 + 90


def test_narration_longer_than_the_motion_lengthens_the_shot(tmp_path) -> None:
    """PC-08's rule, reaching the render request that is actually sent."""
    film = assemble(
        [BeatPlan(beat_id="b1", part="lt-bold-block", copy={1: "张三"}, voice_seconds=8.0)],
        tmp_path,
    )

    assert film.segments[0].frames == 240


def test_a_part_the_catalog_does_not_carry_is_refused(tmp_path) -> None:
    with pytest.raises(AssemblyRejected):
        assemble(
            [BeatPlan(beat_id="b1", part="not-a-part", copy={}, voice_seconds=2.0)],
            tmp_path,
        )


def test_a_part_with_no_frozen_slots_is_refused(tmp_path) -> None:
    """A part nobody measured cannot take this film's copy."""
    with pytest.raises(AssemblyRejected):
        assemble(
            [BeatPlan(beat_id="b1", part="lt-bold-block", copy={1: "张三"}, voice_seconds=None)],
            tmp_path,
            slot_table={"parts": []},
        )


def test_a_shot_too_long_for_one_render_is_refused_before_anything_is_written(
    tmp_path,
) -> None:
    workspace = _Workspace()
    with pytest.raises(FilmOverBudget):
        assemble(
            [BeatPlan(beat_id="b1", part=None, copy={}, voice_seconds=25.0)],
            tmp_path,
            workspace=workspace,
        )
    assert workspace.written == {}


def test_the_plan_says_how_long_the_render_will_take(tmp_path) -> None:
    film = assemble(
        [
            BeatPlan(beat_id="b1", part="lt-bold-block", copy={1: "张三"}, voice_seconds=None),
            BeatPlan(beat_id="b2", part=None, copy={}, voice_seconds=3.0),
        ],
        tmp_path,
    )

    # (30 + 144*0.4) + (30 + 90*0.4) = 87.6 + 66 = 153.6
    assert film.estimated_render_seconds == pytest.approx(153.6)


def test_every_segment_carries_the_budget_its_copy_has_to_fit(tmp_path) -> None:
    """PC-14 needs the baseline at render time, not at freezing time."""
    film = assemble(
        [BeatPlan(beat_id="b1", part="lt-bold-block", copy={1: "张三"}, voice_seconds=None)],
        tmp_path,
    )

    budgets = film.segments[0].slot_budgets
    assert budgets[0].index == 1
    assert budgets[0].usable_width_px == 366
    assert budgets[0].baseline_overflows_y is True
