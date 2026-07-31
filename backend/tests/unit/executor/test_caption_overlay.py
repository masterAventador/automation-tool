"""LE-10 T5: render path-free caption cues into local frame-window bindings."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from PIL import Image

from automation_tool.executor import caption_overlay
from automation_tool.executor.caption_overlay import (
    CaptionOverlayRejected,
    CaptionOverlayRejection,
    VisualCaptionOverlayBinding,
    VisualCaptionOverlaySet,
    render_caption_overlay_set,
)
from automation_tool.executor.captions.fonts import CaptionFontRejected
from automation_tool.protocol.local_rendering import (
    MAX_LOCAL_EDITING_CAPTION_CUES,
    LocalEditingCaptionRenderCue,
    LocalEditingCaptionRenderPlan,
    LocalEditingCaptionRenderStyle,
)


def _plan(
    cues: tuple[LocalEditingCaptionRenderCue, ...] | None = None,
    **overrides: Any,
) -> LocalEditingCaptionRenderPlan:
    return LocalEditingCaptionRenderPlan(
        **{
            "project_id": uuid4(),
            "timeline_id": uuid4(),
            "timeline_revision": 2,
            "output_width": 720,
            "output_height": 1280,
            "output_fps": 30,
            "duration_ms": 1000,
            "style": LocalEditingCaptionRenderStyle(
                font_key="noto-sans-cjk-sc-bold",
                font_px=48,
                stroke_px=2,
                line_spacing=1.2,
            ),
            "cues": (
                LocalEditingCaptionRenderCue(1, 100, 300, "第一条字幕"),
                LocalEditingCaptionRenderCue(2, 600, 300, "第二条字幕"),
            )
            if cues is None
            else cues,
            **overrides,
        }
    )


def test_caption_cues_render_to_redacted_absolute_frame_bindings(
    tmp_path: Path,
) -> None:
    result = render_caption_overlay_set(_plan(), tmp_path)

    assert result.target_frames == 30
    assert tuple((item.start_frame, item.end_frame) for item in result.captions) == (
        (3, 12),
        (18, 27),
    )
    assert tuple(item.source_path.name for item in result.captions) == (
        "caption-0001.png",
        "caption-0002.png",
    )
    assert all(item.source_path.is_file() for item in result.captions)
    assert "第一条字幕" not in repr(result)
    assert all(repr(item) == "VisualCaptionOverlayBinding(<redacted>)" for item in result.captions)
    with Image.open(result.captions[0].source_path) as image:
        assert image.mode == "RGBA"
        assert image.getchannel("A").getbbox() is not None


def test_captionless_plan_returns_an_empty_set_without_writing(tmp_path: Path) -> None:
    result = render_caption_overlay_set(_plan(()), tmp_path)

    assert result.captions == ()
    assert list(tmp_path.iterdir()) == []


def test_subframe_caption_is_rejected_before_rendering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def should_not_render(*args: object, **kwargs: object) -> Path:
        nonlocal calls
        calls += 1
        raise AssertionError("unreachable")

    monkeypatch.setattr(caption_overlay, "render_caption", should_not_render)
    plan = _plan((LocalEditingCaptionRenderCue(1, 0, 1, "不会被静默丢掉"),))

    with pytest.raises(CaptionOverlayRejected) as error:
        render_caption_overlay_set(plan, tmp_path)

    assert error.value.code is CaptionOverlayRejection.CAPTION_BELOW_ONE_FRAME
    assert error.value.__cause__ is None
    assert calls == 0
    assert list(tmp_path.iterdir()) == []


def test_a_later_render_failure_removes_every_png_from_this_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fail_second(
        text: str,
        style: object,
        *,
        frame_width: int,
        frame_height: int,
        destination: Path,
    ) -> Path:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise CaptionFontRejected(f"private text must not escape: {text}")
        destination.write_bytes(b"png")
        return destination

    monkeypatch.setattr(caption_overlay, "render_caption", fail_second)

    with pytest.raises(CaptionOverlayRejected) as error:
        render_caption_overlay_set(_plan(), tmp_path)

    assert error.value.code is CaptionOverlayRejection.RENDER_FAILED
    assert error.value.__cause__ is None
    assert "private" not in str(error.value)
    assert list(tmp_path.iterdir()) == []


def test_an_interrupt_cleans_finished_pngs_and_is_not_turned_into_a_render_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def interrupt_second(
        text: str,
        style: object,
        *,
        frame_width: int,
        frame_height: int,
        destination: Path,
    ) -> Path:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        destination.write_bytes(b"png")
        return destination

    monkeypatch.setattr(caption_overlay, "render_caption", interrupt_second)

    with pytest.raises(KeyboardInterrupt):
        render_caption_overlay_set(_plan(), tmp_path)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("directory_kind", ["relative", "missing", "symlink"])
def test_workspace_must_be_an_existing_absolute_real_directory(
    tmp_path: Path,
    directory_kind: str,
) -> None:
    if directory_kind == "relative":
        directory = Path("relative")
    elif directory_kind == "missing":
        directory = tmp_path / "missing"
    else:
        target = tmp_path / "target"
        target.mkdir()
        directory = tmp_path / "link"
        directory.symlink_to(target, target_is_directory=True)

    with pytest.raises(CaptionOverlayRejected) as error:
        render_caption_overlay_set(_plan(()), directory)

    assert error.value.code is CaptionOverlayRejection.INVALID_WORKSPACE


def test_existing_caption_file_is_never_overwritten(tmp_path: Path) -> None:
    existing = tmp_path / "caption-0001.png"
    existing.write_bytes(b"previous")

    with pytest.raises(CaptionOverlayRejected) as error:
        render_caption_overlay_set(_plan(), tmp_path)

    assert error.value.code is CaptionOverlayRejection.INVALID_WORKSPACE
    assert existing.read_bytes() == b"previous"


def test_mutated_plan_and_binding_values_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(CaptionOverlayRejected):
        render_caption_overlay_set(cast(LocalEditingCaptionRenderPlan, object()), tmp_path)

    plan = _plan(())
    object.__setattr__(plan, "cues", [])
    with pytest.raises(CaptionOverlayRejected) as invalid_plan:
        render_caption_overlay_set(plan, tmp_path)
    assert invalid_plan.value.code is CaptionOverlayRejection.INVALID_PLAN

    plan = _plan()
    object.__setattr__(plan.cues[0], "text", "\u202eprivate")
    with pytest.raises(CaptionOverlayRejected) as invalid_nested:
        render_caption_overlay_set(plan, tmp_path)
    assert invalid_nested.value.code is CaptionOverlayRejection.INVALID_PLAN

    with pytest.raises(CaptionOverlayRejected):
        VisualCaptionOverlayBinding(
            sequence=1,
            start_frame=2,
            end_frame=2,
            source_path=tmp_path / "caption.png",
        )

    with pytest.raises(CaptionOverlayRejected):
        VisualCaptionOverlayBinding(
            sequence=MAX_LOCAL_EDITING_CAPTION_CUES + 1,
            start_frame=0,
            end_frame=1,
            source_path=tmp_path / "caption.png",
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"project_id": UUID(int=0)},
        {"timeline_id": UUID(int=0)},
        {"timeline_revision": 0},
        {"output_width": 0},
        {"output_height": 0},
        {"output_fps": 0},
        {"duration_ms": 0},
        {"target_frames": 0},
        {"captions": cast(tuple[VisualCaptionOverlayBinding, ...], [])},
    ],
)
def test_overlay_set_shape_fails_closed(tmp_path: Path, overrides: dict[str, object]) -> None:
    values: dict[str, object] = {
        "project_id": uuid4(),
        "timeline_id": uuid4(),
        "timeline_revision": 1,
        "output_width": 720,
        "output_height": 1280,
        "output_fps": 30,
        "duration_ms": 1000,
        "target_frames": 30,
        "captions": (),
    }
    values.update(overrides)

    with pytest.raises(CaptionOverlayRejected):
        VisualCaptionOverlaySet(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "overrides",
    [
        {"output_width": 127},
        {"output_width": 721},
        {"output_width": 4097},
        {"output_height": 127},
        {"output_height": 4097},
        {"output_fps": 11},
        {"output_fps": 61},
        {"duration_ms": 99},
        {"duration_ms": 600_001},
        {"target_frames": 29},
    ],
)
def test_overlay_set_reuses_protocol_bounds_and_derives_target_frames(
    overrides: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "project_id": uuid4(),
        "timeline_id": uuid4(),
        "timeline_revision": 1,
        "output_width": 720,
        "output_height": 1280,
        "output_fps": 30,
        "duration_ms": 1000,
        "target_frames": 30,
        "captions": (),
    }
    values.update(overrides)

    with pytest.raises(CaptionOverlayRejected):
        VisualCaptionOverlaySet(**cast(Any, values))


def test_overlay_set_rejects_more_than_the_protocol_caption_limit(tmp_path: Path) -> None:
    captions = tuple(
        VisualCaptionOverlayBinding(index, index - 1, index, tmp_path / f"{index}.png")
        for index in range(1, MAX_LOCAL_EDITING_CAPTION_CUES + 1)
    )

    with pytest.raises(CaptionOverlayRejected):
        VisualCaptionOverlaySet(
            project_id=uuid4(),
            timeline_id=uuid4(),
            timeline_revision=1,
            output_width=720,
            output_height=1280,
            output_fps=60,
            duration_ms=10_000,
            target_frames=600,
            captions=(*captions, captions[-1]),
        )


def test_overlay_set_rebuilds_nested_bindings_and_validates_layout(tmp_path: Path) -> None:
    first = VisualCaptionOverlayBinding(1, 3, 12, tmp_path / "first.png")
    second = VisualCaptionOverlayBinding(2, 18, 27, tmp_path / "second.png")
    values = {
        "project_id": uuid4(),
        "timeline_id": uuid4(),
        "timeline_revision": 1,
        "output_width": 720,
        "output_height": 1280,
        "output_fps": 30,
        "duration_ms": 1000,
        "target_frames": 30,
    }
    result = VisualCaptionOverlaySet(**cast(Any, values), captions=(first, second))
    assert repr(result) == "VisualCaptionOverlaySet(<redacted>)"

    object.__setattr__(first, "start_frame", -1)
    with pytest.raises(CaptionOverlayRejected):
        VisualCaptionOverlaySet(**cast(Any, values), captions=(first, second))

    with pytest.raises(CaptionOverlayRejected):
        VisualCaptionOverlaySet(
            **cast(Any, values),
            captions=(
                VisualCaptionOverlayBinding(1, 3, 12, tmp_path / "one.png"),
                VisualCaptionOverlayBinding(2, 11, 27, tmp_path / "two.png"),
            ),
        )

    with pytest.raises(CaptionOverlayRejected):
        VisualCaptionOverlaySet(
            **cast(Any, values),
            captions=(VisualCaptionOverlayBinding(2, 3, 12, tmp_path / "gap.png"),),
        )

    with pytest.raises(CaptionOverlayRejected):
        VisualCaptionOverlaySet(
            **cast(Any, values),
            captions=(VisualCaptionOverlayBinding(1, 3, 31, tmp_path / "late.png"),),
        )

    invalid_binding = VisualCaptionOverlayBinding(1, 3, 12, tmp_path / "mutated.png")
    object.__setattr__(invalid_binding, "source_path", Path("relative.png"))
    with pytest.raises(CaptionOverlayRejected):
        VisualCaptionOverlaySet(**cast(Any, values), captions=(invalid_binding,))


def test_renderer_must_return_the_exact_png_it_wrote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(caption_overlay, "render_caption", lambda *args, **kwargs: tmp_path)

    with pytest.raises(CaptionOverlayRejected) as error:
        render_caption_overlay_set(_plan(), tmp_path)

    assert error.value.code is CaptionOverlayRejection.RENDER_FAILED
    assert list(tmp_path.iterdir()) == []


def test_cleanup_failure_does_not_expose_the_local_path_or_original_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fail_second(
        text: str,
        style: object,
        *,
        frame_width: int,
        frame_height: int,
        destination: Path,
    ) -> Path:
        nonlocal calls
        calls += 1
        destination.write_bytes(b"png")
        if calls == 2:
            raise CaptionFontRejected("private")
        return destination

    original_unlink = Path.unlink

    def fail_one_unlink(path: Path, *, missing_ok: bool = False) -> None:
        if path.name == "caption-0001.png":
            raise OSError("private path")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(caption_overlay, "render_caption", fail_second)
    monkeypatch.setattr(Path, "unlink", fail_one_unlink)

    with pytest.raises(CaptionOverlayRejected) as error:
        render_caption_overlay_set(_plan(), tmp_path)

    assert error.value.code is CaptionOverlayRejection.RENDER_FAILED
    assert error.value.__cause__ is None
