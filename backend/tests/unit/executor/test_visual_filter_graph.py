"""LE-10 T3/T4: compile VIDEO/IMAGE plans into one FFmpeg graph."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest

from automation_tool.executor.caption_overlay import (
    VisualCaptionOverlayBinding,
    VisualCaptionOverlaySet,
)
from automation_tool.executor.material_probe import PackagedMediaTools
from automation_tool.executor.visual_rendering import (
    VisualFfmpegCommand,
    VisualFilterGraphRejected,
    VisualFilterGraphRejection,
    VisualRenderSourceBinding,
    compile_visual_ffmpeg_command,
)
from automation_tool.protocol.local_editing import SegmentSelectionMaterialKind
from automation_tool.protocol.local_rendering import (
    LocalEditingVisualRenderClip,
    LocalEditingVisualRenderPlan,
    LocalEditingVisualTransitionKind,
)


def _executable(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_bytes(b"#!/bin/sh\nexit 0\n")
    path.chmod(0o700)
    return path


def _tools(directory: Path) -> PackagedMediaTools:
    return PackagedMediaTools(
        ffprobe_path=_executable(directory, "ffprobe"),
        ffmpeg_path=_executable(directory, "ffmpeg"),
    )


def _clip(
    sequence: int,
    material_id: UUID,
    kind: SegmentSelectionMaterialKind,
    *,
    start_ms: int,
    duration_ms: int,
    transition_ms: int | None = None,
    transition_kind: LocalEditingVisualTransitionKind = LocalEditingVisualTransitionKind.FADE,
) -> LocalEditingVisualRenderClip:
    return LocalEditingVisualRenderClip(
        sequence=sequence,
        material_id=material_id,
        kind=kind,
        start_ms=start_ms,
        duration_ms=duration_ms,
        source_in_ms=700 if kind is SegmentSelectionMaterialKind.VIDEO else None,
        source_out_ms=(700 + duration_ms if kind is SegmentSelectionMaterialKind.VIDEO else None),
        transition_kind=None if transition_ms is None else transition_kind,
        transition_duration_ms=transition_ms,
    )


def _plan(
    clips: tuple[LocalEditingVisualRenderClip, ...],
    *,
    duration_ms: int,
    fps: int = 30,
) -> LocalEditingVisualRenderPlan:
    return LocalEditingVisualRenderPlan(
        project_id=uuid4(),
        timeline_id=uuid4(),
        timeline_revision=1,
        output_width=720,
        output_height=1280,
        output_fps=fps,
        duration_ms=duration_ms,
        clips=clips,
    )


def _source(
    directory: Path,
    material_id: UUID,
    kind: SegmentSelectionMaterialKind,
    name: str,
) -> VisualRenderSourceBinding:
    path = directory / name
    path.write_bytes(b"source")
    return VisualRenderSourceBinding(
        material_id=material_id,
        kind=kind,
        source_path=path,
    )


def test_mixed_video_image_hard_cut_compiles_one_path_safe_filter_graph(
    tmp_path: Path,
) -> None:
    tools = _tools(tmp_path)
    video_id = uuid4()
    image_id = uuid4()
    video = _source(tmp_path, video_id, SegmentSelectionMaterialKind.VIDEO, "视频 $' 片段.mp4")
    image = _source(tmp_path, image_id, SegmentSelectionMaterialKind.IMAGE, "图片 & 片段.png")
    output = tmp_path / "rendered.mp4"
    plan = _plan(
        (
            _clip(1, video_id, SegmentSelectionMaterialKind.VIDEO, start_ms=0, duration_ms=150),
            _clip(2, image_id, SegmentSelectionMaterialKind.IMAGE, start_ms=150, duration_ms=150),
        ),
        duration_ms=300,
    )

    result = compile_visual_ffmpeg_command(tools, plan, (video, image), output)

    assert isinstance(result, VisualFfmpegCommand)
    assert result.argv[0] == os.fspath(tools.ffmpeg_path)
    assert result.argv.count(os.fspath(video.source_path)) == 1
    assert result.argv.count(os.fspath(image.source_path)) == 1
    assert result.argv[-1] == os.fspath(output)
    assert result.argv.count("-filter_complex") == 1
    assert result.filter_complex == result.argv[result.argv.index("-filter_complex") + 1]
    assert "trim=start=0.700:end=0.850" in result.filter_complex
    assert "scale=w=720:h=1280:force_original_aspect_ratio=increase" in result.filter_complex
    assert "crop=w=720:h=1280:x=(in_w-out_w)/2:y=(in_h-out_h)/2" in result.filter_complex
    assert result.filter_complex.count("fps=30") == 2
    assert "trim=end_frame=5" in result.filter_complex
    assert "trim=end_frame=4" in result.filter_complex
    assert "concat=n=2:v=1:a=0[outv]" in result.filter_complex
    assert not any(
        os.fspath(path) in result.filter_complex for path in (video.source_path, image.source_path)
    )
    assert result.target_frames == 9
    assert result.argv[result.argv.index("-an") : result.argv.index("-an") + 5] == (
        "-an",
        "-frames:v",
        "9",
        "-c:v",
        "libx264",
    )
    assert "veryfast" in result.argv
    assert "23" in result.argv
    assert "yuv420p" in result.argv
    assert "cfr" in result.argv
    assert result.argv[result.argv.index("-map_metadata") + 1] == "-1"
    assert result.argv[result.argv.index("-map_chapters") + 1] == "-1"
    assert repr(result) == "VisualFfmpegCommand(<redacted>)"
    assert os.fspath(video.source_path) not in repr(result)


def test_image_input_loops_at_target_fps_and_reused_material_gets_one_input_per_clip(
    tmp_path: Path,
) -> None:
    tools = _tools(tmp_path)
    image_id = uuid4()
    image = _source(tmp_path, image_id, SegmentSelectionMaterialKind.IMAGE, "still.png")
    plan = _plan(
        (
            _clip(1, image_id, SegmentSelectionMaterialKind.IMAGE, start_ms=0, duration_ms=100),
            _clip(2, image_id, SegmentSelectionMaterialKind.IMAGE, start_ms=100, duration_ms=100),
        ),
        duration_ms=200,
    )

    result = compile_visual_ffmpeg_command(tools, plan, (image,), tmp_path / "result.mp4")

    assert result.argv.count(os.fspath(image.source_path)) == 2
    assert result.argv.count("-loop") == 2
    assert result.argv.count("-framerate") == 2
    assert result.filter_complex.startswith("[0:v:0]")
    assert "[1:v:0]" in result.filter_complex


def test_single_clip_maps_normalized_label_without_concat(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    image_id = uuid4()
    image = _source(tmp_path, image_id, SegmentSelectionMaterialKind.IMAGE, "still.png")
    plan = _plan(
        (_clip(1, image_id, SegmentSelectionMaterialKind.IMAGE, start_ms=0, duration_ms=100),),
        duration_ms=100,
    )

    result = compile_visual_ffmpeg_command(tools, plan, (image,), tmp_path / "result.mp4")

    assert "concat=" not in result.filter_complex
    assert result.argv[result.argv.index("-map") + 1] == "[v1]"


def test_caption_pngs_overlay_in_absolute_frame_windows_and_keep_paths_out_of_graph(
    tmp_path: Path,
) -> None:
    tools = _tools(tmp_path)
    image_id = uuid4()
    image = _source(tmp_path, image_id, SegmentSelectionMaterialKind.IMAGE, "still.png")
    plan = _plan(
        (_clip(1, image_id, image.kind, start_ms=0, duration_ms=1000),),
        duration_ms=1000,
    )
    first_png = tmp_path / "caption-0001.png"
    second_png = tmp_path / "caption-0002.png"
    first_png.write_bytes(b"png")
    second_png.write_bytes(b"png")
    overlays = VisualCaptionOverlaySet(
        project_id=plan.project_id,
        timeline_id=plan.timeline_id,
        timeline_revision=plan.timeline_revision,
        output_width=plan.output_width,
        output_height=plan.output_height,
        output_fps=plan.output_fps,
        duration_ms=plan.duration_ms,
        target_frames=30,
        captions=(
            VisualCaptionOverlayBinding(1, 3, 12, first_png),
            VisualCaptionOverlayBinding(2, 18, 27, second_png),
        ),
    )

    result = compile_visual_ffmpeg_command(
        tools,
        plan,
        (image,),
        tmp_path / "result.mp4",
        caption_overlays=overlays,
    )

    assert result.argv.count(os.fspath(first_png)) == 1
    assert result.argv.count(os.fspath(second_png)) == 1
    assert (
        "[1:v:0]fps=30,settb=1/30,trim=end_frame=30,setpts=N,format=rgba[c1]"
        in result.filter_complex
    )
    assert (
        "[v1][c1]overlay=x=(main_w-overlay_w)/2:"
        r"y=max(0\,main_h-overlay_h-102):enable=between(n\,3\,11):"
        "eof_action=pass:repeatlast=0[outc1]"
    ) in result.filter_complex
    assert (
        "[outc1][c2]overlay=x=(main_w-overlay_w)/2:"
        r"y=max(0\,main_h-overlay_h-102):enable=between(n\,18\,26):"
        "eof_action=pass:repeatlast=0[outc2]"
    ) in result.filter_complex
    assert os.fspath(first_png) not in result.filter_complex
    assert os.fspath(second_png) not in result.filter_complex
    assert result.argv[result.argv.index("-map") + 1] == "[outc2]"


def test_caption_inputs_start_after_every_visual_clip_input(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    first_id = uuid4()
    second_id = uuid4()
    first = _source(tmp_path, first_id, SegmentSelectionMaterialKind.IMAGE, "first.png")
    second = _source(tmp_path, second_id, SegmentSelectionMaterialKind.IMAGE, "second.png")
    plan = _plan(
        (
            _clip(1, first_id, first.kind, start_ms=0, duration_ms=500),
            _clip(2, second_id, second.kind, start_ms=500, duration_ms=500),
        ),
        duration_ms=1000,
    )
    png = tmp_path / "caption.png"
    png.write_bytes(b"png")
    overlays = VisualCaptionOverlaySet(
        project_id=plan.project_id,
        timeline_id=plan.timeline_id,
        timeline_revision=plan.timeline_revision,
        output_width=plan.output_width,
        output_height=plan.output_height,
        output_fps=plan.output_fps,
        duration_ms=plan.duration_ms,
        target_frames=30,
        captions=(VisualCaptionOverlayBinding(1, 3, 12, png),),
    )

    result = compile_visual_ffmpeg_command(
        tools,
        plan,
        (first, second),
        tmp_path / "result.mp4",
        caption_overlays=overlays,
    )

    assert "[2:v:0]fps=30" in result.filter_complex
    assert result.argv.index(os.fspath(png)) > result.argv.index(os.fspath(second.source_path))


def test_empty_caption_set_is_byte_identical_to_omitting_captions(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    image_id = uuid4()
    image = _source(tmp_path, image_id, SegmentSelectionMaterialKind.IMAGE, "still.png")
    plan = _plan(
        (_clip(1, image_id, image.kind, start_ms=0, duration_ms=1000),),
        duration_ms=1000,
    )
    overlays = VisualCaptionOverlaySet(
        project_id=plan.project_id,
        timeline_id=plan.timeline_id,
        timeline_revision=plan.timeline_revision,
        output_width=plan.output_width,
        output_height=plan.output_height,
        output_fps=plan.output_fps,
        duration_ms=plan.duration_ms,
        target_frames=30,
        captions=(),
    )

    without = compile_visual_ffmpeg_command(tools, plan, (image,), tmp_path / "result.mp4")
    with_empty = compile_visual_ffmpeg_command(
        tools,
        plan,
        (image,),
        tmp_path / "result.mp4",
        caption_overlays=overlays,
    )

    assert with_empty == without


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("project_id", uuid4()),
        ("timeline_id", uuid4()),
        ("timeline_revision", 99),
        ("output_width", 1280),
        ("output_height", 720),
        ("output_fps", 24),
        ("duration_ms", 900),
        ("target_frames", 29),
    ],
)
def test_caption_set_must_match_the_visual_plan_identity(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    tools = _tools(tmp_path)
    image_id = uuid4()
    image = _source(tmp_path, image_id, SegmentSelectionMaterialKind.IMAGE, "still.png")
    plan = _plan(
        (_clip(1, image_id, image.kind, start_ms=0, duration_ms=1000),),
        duration_ms=1000,
    )
    values: dict[str, object] = {
        "project_id": plan.project_id,
        "timeline_id": plan.timeline_id,
        "timeline_revision": plan.timeline_revision,
        "output_width": plan.output_width,
        "output_height": plan.output_height,
        "output_fps": plan.output_fps,
        "duration_ms": plan.duration_ms,
        "target_frames": 30,
        "captions": (),
    }
    values[field] = value
    overlays = VisualCaptionOverlaySet(**values)  # type: ignore[arg-type]

    with pytest.raises(VisualFilterGraphRejected) as error:
        compile_visual_ffmpeg_command(
            tools,
            plan,
            (image,),
            tmp_path / "result.mp4",
            caption_overlays=overlays,
        )

    assert error.value.code is VisualFilterGraphRejection.INVALID_CAPTIONS
    assert error.value.__cause__ is None


def test_wrong_or_mutated_caption_set_is_rejected_before_paths_are_used(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    image_id = uuid4()
    image = _source(tmp_path, image_id, SegmentSelectionMaterialKind.IMAGE, "still.png")
    plan = _plan(
        (_clip(1, image_id, image.kind, start_ms=0, duration_ms=1000),),
        duration_ms=1000,
    )
    with pytest.raises(VisualFilterGraphRejected) as wrong_type:
        compile_visual_ffmpeg_command(
            tools,
            plan,
            (image,),
            tmp_path / "result.mp4",
            caption_overlays=cast(VisualCaptionOverlaySet, object()),
        )
    assert wrong_type.value.code is VisualFilterGraphRejection.INVALID_CAPTIONS

    png = tmp_path / "caption.png"
    png.write_bytes(b"png")
    binding = VisualCaptionOverlayBinding(1, 3, 12, png)
    overlays = VisualCaptionOverlaySet(
        project_id=plan.project_id,
        timeline_id=plan.timeline_id,
        timeline_revision=plan.timeline_revision,
        output_width=plan.output_width,
        output_height=plan.output_height,
        output_fps=plan.output_fps,
        duration_ms=plan.duration_ms,
        target_frames=30,
        captions=(binding,),
    )
    object.__setattr__(binding, "source_path", Path("relative.png"))
    object.__setattr__(overlays, "captions", (binding,))
    with pytest.raises(VisualFilterGraphRejected) as mutated:
        compile_visual_ffmpeg_command(
            tools,
            plan,
            (image,),
            tmp_path / "result.mp4",
            caption_overlays=overlays,
        )
    assert mutated.value.code is VisualFilterGraphRejection.INVALID_CAPTIONS


@pytest.mark.parametrize(
    "construct",
    [
        lambda root: VisualRenderSourceBinding(
            material_id=UUID(int=0),
            kind=SegmentSelectionMaterialKind.IMAGE,
            source_path=root / "image.png",
        ),
        lambda root: VisualRenderSourceBinding(
            material_id=uuid4(),
            kind=cast(SegmentSelectionMaterialKind, "image"),
            source_path=root / "image.png",
        ),
        lambda root: VisualRenderSourceBinding(
            material_id=uuid4(),
            kind=SegmentSelectionMaterialKind.AUDIO,
            source_path=root / "audio.wav",
        ),
        lambda root: VisualRenderSourceBinding(
            material_id=uuid4(),
            kind=SegmentSelectionMaterialKind.IMAGE,
            source_path=Path("relative.png"),
        ),
        lambda root: VisualRenderSourceBinding(
            material_id=uuid4(),
            kind=SegmentSelectionMaterialKind.IMAGE,
            source_path=root / "unsafe\u202epng",
        ),
        lambda root: VisualRenderSourceBinding(
            material_id=uuid4(),
            kind=SegmentSelectionMaterialKind.IMAGE,
            source_path=Path("/") / ("a" * 4096),
        ),
    ],
)
def test_source_binding_shape_fails_closed(
    tmp_path: Path,
    construct: Callable[[Path], VisualRenderSourceBinding],
) -> None:
    with pytest.raises(VisualFilterGraphRejected) as error:
        construct(tmp_path)

    assert error.value.code is VisualFilterGraphRejection.INVALID_BINDINGS
    assert error.value.__cause__ is None


@pytest.mark.parametrize(
    "bindings",
    [
        lambda root, video, image: (),
        lambda root, video, image: cast(
            tuple[VisualRenderSourceBinding, ...],
            [video, image],
        ),
        lambda root, video, image: (video,),
        lambda root, video, image: (video, image, _source(root, uuid4(), image.kind, "extra.png")),
        lambda root, video, image: (video, image, image),
        lambda root, video, image: (
            video,
            VisualRenderSourceBinding(
                material_id=image.material_id,
                kind=SegmentSelectionMaterialKind.VIDEO,
                source_path=image.source_path,
            ),
        ),
    ],
)
def test_material_bindings_must_be_unique_exact_and_kind_matched(
    tmp_path: Path,
    bindings: Callable[
        [Path, VisualRenderSourceBinding, VisualRenderSourceBinding],
        tuple[VisualRenderSourceBinding, ...],
    ],
) -> None:
    tools = _tools(tmp_path)
    video_id = uuid4()
    image_id = uuid4()
    video = _source(tmp_path, video_id, SegmentSelectionMaterialKind.VIDEO, "video.mp4")
    image = _source(tmp_path, image_id, SegmentSelectionMaterialKind.IMAGE, "image.png")
    plan = _plan(
        (
            _clip(1, video_id, video.kind, start_ms=0, duration_ms=100),
            _clip(2, image_id, image.kind, start_ms=100, duration_ms=100),
        ),
        duration_ms=200,
    )

    with pytest.raises(VisualFilterGraphRejected) as error:
        compile_visual_ffmpeg_command(
            tools,
            plan,
            bindings(tmp_path, video, image),
            tmp_path / "result.mp4",
        )

    assert error.value.code is VisualFilterGraphRejection.INVALID_BINDINGS
    assert error.value.__cause__ is None


@pytest.mark.parametrize(
    ("transition_kind", "ffmpeg_transition"),
    [
        (LocalEditingVisualTransitionKind.FADE, "fade"),
        (LocalEditingVisualTransitionKind.DISSOLVE, "dissolve"),
        (LocalEditingVisualTransitionKind.WIPE, "wipeleft"),
    ],
)
def test_transition_compiles_frame_derived_duration_and_absolute_offset(
    tmp_path: Path,
    transition_kind: LocalEditingVisualTransitionKind,
    ffmpeg_transition: str,
) -> None:
    tools = _tools(tmp_path)
    first_id = uuid4()
    second_id = uuid4()
    first = _source(tmp_path, first_id, SegmentSelectionMaterialKind.IMAGE, "first.png")
    second = _source(tmp_path, second_id, SegmentSelectionMaterialKind.IMAGE, "second.png")
    plan = _plan(
        (
            _clip(1, first_id, first.kind, start_ms=0, duration_ms=100),
            _clip(
                2,
                second_id,
                second.kind,
                start_ms=80,
                duration_ms=100,
                transition_ms=20,
                transition_kind=transition_kind,
            ),
        ),
        duration_ms=180,
        fps=50,
    )

    result = compile_visual_ffmpeg_command(
        tools,
        plan,
        (first, second),
        tmp_path / "result.mp4",
    )

    assert (
        f"[v1][v2]xfade=transition={ffmpeg_transition}:duration=0.020000000:"
        "offset=0.080000000[out2]"
    ) in result.filter_complex
    assert "concat=" not in result.filter_complex
    assert result.argv[result.argv.index("-map") + 1] == "[out2]"


def test_consecutive_transitions_keep_absolute_offsets_and_unique_chain_labels(
    tmp_path: Path,
) -> None:
    tools = _tools(tmp_path)
    material_ids = (uuid4(), uuid4(), uuid4())
    sources = tuple(
        _source(tmp_path, material_id, SegmentSelectionMaterialKind.IMAGE, f"{index}.png")
        for index, material_id in enumerate(material_ids, start=1)
    )
    plan = _plan(
        (
            _clip(1, material_ids[0], sources[0].kind, start_ms=0, duration_ms=500),
            _clip(
                2,
                material_ids[1],
                sources[1].kind,
                start_ms=400,
                duration_ms=500,
                transition_ms=100,
                transition_kind=LocalEditingVisualTransitionKind.DISSOLVE,
            ),
            _clip(
                3,
                material_ids[2],
                sources[2].kind,
                start_ms=800,
                duration_ms=500,
                transition_ms=100,
                transition_kind=LocalEditingVisualTransitionKind.WIPE,
            ),
        ),
        duration_ms=1300,
    )

    result = compile_visual_ffmpeg_command(tools, plan, sources, tmp_path / "result.mp4")

    assert (
        "[v1][v2]xfade=transition=dissolve:duration=0.100000000:offset=0.400000000[out2]"
    ) in result.filter_complex
    assert (
        "[out2][v3]xfade=transition=wipeleft:duration=0.100000000:offset=0.800000000[out3]"
    ) in result.filter_complex
    assert result.filter_complex.count("[out2]") == 2
    assert result.argv[result.argv.index("-map") + 1] == "[out3]"
    assert result.target_frames == 39


def test_hard_cuts_and_transition_form_one_pairwise_chain(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    material_ids = tuple(uuid4() for _ in range(5))
    sources = tuple(
        _source(tmp_path, material_id, SegmentSelectionMaterialKind.IMAGE, f"{index}.png")
        for index, material_id in enumerate(material_ids, start=1)
    )
    plan = _plan(
        (
            _clip(1, material_ids[0], sources[0].kind, start_ms=0, duration_ms=200),
            _clip(2, material_ids[1], sources[1].kind, start_ms=200, duration_ms=200),
            _clip(
                3,
                material_ids[2],
                sources[2].kind,
                start_ms=350,
                duration_ms=250,
                transition_ms=50,
            ),
            _clip(4, material_ids[3], sources[3].kind, start_ms=600, duration_ms=200),
            _clip(
                5,
                material_ids[4],
                sources[4].kind,
                start_ms=750,
                duration_ms=250,
                transition_ms=50,
                transition_kind=LocalEditingVisualTransitionKind.DISSOLVE,
            ),
        ),
        duration_ms=1000,
        fps=20,
    )

    result = compile_visual_ffmpeg_command(tools, plan, sources, tmp_path / "result.mp4")

    assert "[v1][v2]concat=n=2:v=1:a=0,settb=1/20[out2]" in result.filter_complex
    assert (
        "[out2][v3]xfade=transition=fade:duration=0.050000000:offset=0.350000000[out3]"
    ) in result.filter_complex
    assert "[out3][v4]concat=n=2:v=1:a=0,settb=1/20[out4]" in result.filter_complex
    assert (
        "[out4][v5]xfade=transition=dissolve:duration=0.050000000:offset=0.750000000[out5]"
    ) in result.filter_complex
    assert result.argv[result.argv.index("-map") + 1] == "[out5]"
    assert result.target_frames == 20


@pytest.mark.parametrize("output", [Path("relative.mp4"), Path("/private/tmp/result.mov")])
def test_output_path_must_be_absolute_mp4(tmp_path: Path, output: Path) -> None:
    tools = _tools(tmp_path)
    image_id = uuid4()
    image = _source(tmp_path, image_id, SegmentSelectionMaterialKind.IMAGE, "image.png")
    plan = _plan(
        (_clip(1, image_id, image.kind, start_ms=0, duration_ms=100),),
        duration_ms=100,
    )

    with pytest.raises(VisualFilterGraphRejected) as error:
        compile_visual_ffmpeg_command(tools, plan, (image,), output)

    assert error.value.code is VisualFilterGraphRejection.INVALID_OUTPUT


def test_invalid_plan_and_disappeared_tool_have_fixed_rejections(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    image_id = uuid4()
    image = _source(tmp_path, image_id, SegmentSelectionMaterialKind.IMAGE, "image.png")
    plan = _plan(
        (_clip(1, image_id, image.kind, start_ms=0, duration_ms=100),),
        duration_ms=100,
    )
    object.__setattr__(plan, "clips", cast(tuple[LocalEditingVisualRenderClip, ...], []))
    with pytest.raises(VisualFilterGraphRejected) as invalid:
        compile_visual_ffmpeg_command(tools, plan, (image,), tmp_path / "result.mp4")
    assert invalid.value.code is VisualFilterGraphRejection.INVALID_PLAN

    plan = _plan(
        (_clip(1, image_id, image.kind, start_ms=0, duration_ms=100),),
        duration_ms=100,
    )
    tools.ffmpeg_path.unlink()
    with pytest.raises(VisualFilterGraphRejected) as unavailable:
        compile_visual_ffmpeg_command(tools, plan, (image,), tmp_path / "result.mp4")
    assert unavailable.value.code is VisualFilterGraphRejection.TOOL_UNAVAILABLE


def test_mutated_binding_and_wrong_tools_type_are_rejected_before_use(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    image_id = uuid4()
    image = _source(tmp_path, image_id, SegmentSelectionMaterialKind.IMAGE, "image.png")
    plan = _plan(
        (_clip(1, image_id, image.kind, start_ms=0, duration_ms=100),),
        duration_ms=100,
    )
    object.__setattr__(image, "source_path", Path("relative.png"))
    with pytest.raises(VisualFilterGraphRejected) as mutated:
        compile_visual_ffmpeg_command(tools, plan, (image,), tmp_path / "result.mp4")
    assert mutated.value.code is VisualFilterGraphRejection.INVALID_BINDINGS

    with pytest.raises(VisualFilterGraphRejected) as wrong_tools:
        compile_visual_ffmpeg_command(
            cast(PackagedMediaTools, object()),
            plan,
            (image,),
            tmp_path / "result.mp4",
        )
    assert wrong_tools.value.code is VisualFilterGraphRejection.TOOL_UNAVAILABLE


def test_source_binding_and_module_boundaries_do_not_leak_paths(tmp_path: Path) -> None:
    binding = _source(tmp_path, uuid4(), SegmentSelectionMaterialKind.IMAGE, "private.png")
    source = (
        Path(__file__).parents[3] / "src" / "automation_tool" / "executor" / "visual_rendering.py"
    ).read_text(encoding="utf-8")

    assert repr(binding) == "VisualRenderSourceBinding(<redacted>)"
    assert os.fspath(binding.source_path) not in repr(binding)
    assert "automation_tool.control_plane" not in source
