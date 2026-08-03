"""LE-10 T6: real packaged FFmpeg execution and publication acceptance."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image

from automation_tool.executor import visual_render_execution as execution
from automation_tool.executor.material_probe import PackagedMediaTools, approve_source
from automation_tool.executor.visual_render_execution import (
    VISUAL_RENDER_OUTPUT_FILENAME,
    VisualRenderExecutionRejected,
    VisualRenderExecutionRejection,
    execute_visual_render,
)
from automation_tool.executor.visual_rendering import (
    VisualFfmpegCommand,
    VisualRenderSourceBinding,
)
from automation_tool.protocol.local_editing import SegmentSelectionMaterialKind
from automation_tool.protocol.local_rendering import (
    LocalEditingCaptionRenderCue,
    LocalEditingCaptionRenderPlan,
    LocalEditingCaptionRenderStyle,
    LocalEditingVisualRenderClip,
    LocalEditingVisualRenderPlan,
    LocalEditingVisualTransitionKind,
)

TOOLCHAIN_ROOT_ENVIRONMENT = "AUTOMATION_TOOL_LE10_TOOLCHAIN_ROOT"
pytestmark = pytest.mark.skipif(
    TOOLCHAIN_ROOT_ENVIRONMENT not in os.environ,
    reason="requires the verified packaged media toolchain",
)


def _tools() -> PackagedMediaTools:
    suffix = ".exe" if os.name == "nt" else ""
    root = Path(os.environ[TOOLCHAIN_ROOT_ENVIRONMENT]) / "bin"
    return PackagedMediaTools(
        ffprobe_path=root / f"ffprobe{suffix}",
        ffmpeg_path=root / f"ffmpeg{suffix}",
    )


def _request(
    tmp_path: Path,
) -> tuple[
    LocalEditingVisualRenderPlan,
    tuple[VisualRenderSourceBinding, ...],
    tuple[os.stat_result, ...],
    LocalEditingCaptionRenderPlan,
]:
    source = tmp_path / "source.png"
    Image.new("RGB", (320, 180), (22, 44, 88)).save(source)
    _, approved = approve_source(source)
    material_id = uuid4()
    project_id = uuid4()
    timeline_id = uuid4()
    plan = LocalEditingVisualRenderPlan(
        project_id=project_id,
        timeline_id=timeline_id,
        timeline_revision=1,
        output_width=720,
        output_height=1280,
        output_fps=30,
        duration_ms=1000,
        clips=(
            LocalEditingVisualRenderClip(
                sequence=1,
                material_id=material_id,
                kind=SegmentSelectionMaterialKind.IMAGE,
                start_ms=0,
                duration_ms=1000,
                source_in_ms=None,
                source_out_ms=None,
                transition_kind=None,
                transition_duration_ms=None,
            ),
        ),
    )
    caption_plan = LocalEditingCaptionRenderPlan(
        project_id=project_id,
        timeline_id=timeline_id,
        timeline_revision=1,
        output_width=720,
        output_height=1280,
        output_fps=30,
        duration_ms=1000,
        style=LocalEditingCaptionRenderStyle(
            font_key="noto-sans-cjk-sc-bold",
            font_px=24,
            stroke_px=1,
            line_spacing=1.2,
        ),
        cues=(
            LocalEditingCaptionRenderCue(
                sequence=1,
                start_ms=250,
                duration_ms=500,
                text="真机字幕",
            ),
        ),
    )
    return (
        plan,
        (
            VisualRenderSourceBinding(
                material_id=material_id,
                kind=SegmentSelectionMaterialKind.IMAGE,
                source_path=source,
            ),
        ),
        (approved,),
        caption_plan,
    )


def test_real_packaged_ffmpeg_publishes_one_verified_captioned_h264(
    tmp_path: Path,
) -> None:
    tools = _tools()
    task = tmp_path / "task"
    task.mkdir()
    plan, sources, approvals, caption_plan = _request(tmp_path)

    receipt = execute_visual_render(
        tools,
        plan,
        sources,
        approvals,
        task,
        caption_plan=caption_plan,
    )

    output = task / VISUAL_RENDER_OUTPUT_FILENAME
    assert output.stat().st_size == receipt.bytes_written
    assert receipt.frame_count == 30
    assert (receipt.width, receipt.height) == (720, 1280)
    assert receipt.fps == 30
    assert receipt.duration_ms == 1000
    assert len(receipt.sha256) == 64
    assert [path.name for path in task.iterdir()] == [VISUAL_RENDER_OUTPUT_FILENAME]
    print(
        "visualReceipt="
        f"h264,{receipt.width}x{receipt.height},{receipt.fps}fps,"
        f"{receipt.frame_count}frames,{receipt.duration_ms}ms,"
        f"{receipt.bytes_written}bytes,sha256={receipt.sha256}"
    )


def test_real_packaged_ffmpeg_cancel_stops_child_and_publishes_nothing(
    tmp_path: Path,
) -> None:
    tools = _tools()
    task = tmp_path / "task"
    task.mkdir()
    plan, sources, approvals, _ = _request(tmp_path)
    probes = 0

    def cancel_after_spawn() -> bool:
        nonlocal probes
        probes += 1
        return probes >= 2

    with pytest.raises(VisualRenderExecutionRejected) as raised:
        execute_visual_render(
            tools,
            plan,
            sources,
            approvals,
            task,
            cancel_requested=cancel_after_spawn,
        )

    assert raised.value.code is VisualRenderExecutionRejection.CANCELLED
    assert list(task.iterdir()) == []


def test_real_packaged_ffmpeg_timeout_stops_child_and_publishes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _tools()
    task = tmp_path / "task"
    task.mkdir()
    plan, sources, approvals, _ = _request(tmp_path)
    monkeypatch.setattr(execution, "_FFMPEG_TIMEOUT_SECONDS", 0.0)

    with pytest.raises(VisualRenderExecutionRejected) as raised:
        execute_visual_render(tools, plan, sources, approvals, task)

    assert raised.value.code is VisualRenderExecutionRejection.TIMED_OUT
    assert list(task.iterdir()) == []


def test_real_packaged_ffmpeg_wrong_frame_shape_is_rejected_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _tools()
    task = tmp_path / "task"
    task.mkdir()
    plan, sources, approvals, _ = _request(tmp_path)

    def wrong_shape_command(
        tools_value: PackagedMediaTools,
        plan_value: LocalEditingVisualRenderPlan,
        sources_value: tuple[VisualRenderSourceBinding, ...],
        output_path: Path,
        **kwargs: object,
    ) -> VisualFfmpegCommand:
        assert tools_value is tools
        assert plan_value is plan
        assert sources_value == sources
        return VisualFfmpegCommand(
            argv=(
                os.fspath(tools.ffmpeg_path),
                "-y",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=640x1280:r=30:d=1",
                "-frames:v",
                "30",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                os.fspath(output_path),
            ),
            filter_complex="color=redacted",
            target_frames=30,
            output_width=plan.output_width,
            output_height=plan.output_height,
            output_fps=plan.output_fps,
        )

    monkeypatch.setattr(execution, "compile_visual_ffmpeg_command", wrong_shape_command)

    with pytest.raises(VisualRenderExecutionRejected) as raised:
        execute_visual_render(tools, plan, sources, approvals, task)

    assert raised.value.code is VisualRenderExecutionRejection.OUTPUT_INVALID
    assert list(task.iterdir()) == []


def test_real_public_execution_combines_video_images_transitions_and_caption(
    tmp_path: Path,
) -> None:
    tools = _tools()
    video = tmp_path / "video.mp4"
    subprocess.run(
        (
            os.fspath(tools.ffmpeg_path),
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=320x180:r=20:d=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            os.fspath(video),
        ),
        check=True,
    )
    first_image = tmp_path / "first.png"
    second_image = tmp_path / "second.png"
    Image.new("RGB", (320, 180), (180, 30, 50)).save(first_image)
    Image.new("RGB", (320, 180), (20, 150, 90)).save(second_image)
    paths = (video, first_image, second_image)
    approvals = tuple(approve_source(path)[1] for path in paths)
    material_ids = (uuid4(), uuid4(), uuid4())
    project_id = uuid4()
    timeline_id = uuid4()
    plan = LocalEditingVisualRenderPlan(
        project_id=project_id,
        timeline_id=timeline_id,
        timeline_revision=1,
        output_width=720,
        output_height=1280,
        output_fps=20,
        duration_ms=1000,
        clips=(
            LocalEditingVisualRenderClip(
                sequence=1,
                material_id=material_ids[0],
                kind=SegmentSelectionMaterialKind.VIDEO,
                start_ms=0,
                duration_ms=500,
                source_in_ms=0,
                source_out_ms=500,
                transition_kind=None,
                transition_duration_ms=None,
            ),
            LocalEditingVisualRenderClip(
                sequence=2,
                material_id=material_ids[1],
                kind=SegmentSelectionMaterialKind.IMAGE,
                start_ms=400,
                duration_ms=400,
                source_in_ms=None,
                source_out_ms=None,
                transition_kind=LocalEditingVisualTransitionKind.FADE,
                transition_duration_ms=100,
            ),
            LocalEditingVisualRenderClip(
                sequence=3,
                material_id=material_ids[2],
                kind=SegmentSelectionMaterialKind.IMAGE,
                start_ms=700,
                duration_ms=300,
                source_in_ms=None,
                source_out_ms=None,
                transition_kind=LocalEditingVisualTransitionKind.DISSOLVE,
                transition_duration_ms=100,
            ),
        ),
    )
    sources = tuple(
        VisualRenderSourceBinding(
            material_id=material_id,
            kind=(
                SegmentSelectionMaterialKind.VIDEO
                if sequence == 0
                else SegmentSelectionMaterialKind.IMAGE
            ),
            source_path=path,
        )
        for sequence, (material_id, path) in enumerate(zip(material_ids, paths, strict=True))
    )
    caption_plan = LocalEditingCaptionRenderPlan(
        project_id=project_id,
        timeline_id=timeline_id,
        timeline_revision=1,
        output_width=720,
        output_height=1280,
        output_fps=20,
        duration_ms=1000,
        style=LocalEditingCaptionRenderStyle(
            font_key="noto-sans-cjk-sc-bold",
            font_px=36,
            stroke_px=2,
            line_spacing=1.2,
        ),
        cues=(
            LocalEditingCaptionRenderCue(
                sequence=1,
                start_ms=250,
                duration_ms=500,
                text="视频图片连续转场",
            ),
        ),
    )
    task = tmp_path / "complex-task"
    task.mkdir()

    receipt = execute_visual_render(
        tools,
        plan,
        sources,
        approvals,
        task,
        caption_plan=caption_plan,
    )

    assert (receipt.width, receipt.height) == (720, 1280)
    assert receipt.fps == 20
    assert receipt.frame_count == 20
    assert receipt.duration_ms == 1000
    assert (task / VISUAL_RENDER_OUTPUT_FILENAME).stat().st_size == receipt.bytes_written
    assert [path.name for path in task.iterdir()] == [VISUAL_RENDER_OUTPUT_FILENAME]
    print(
        "complexVisualReceipt="
        f"h264,{receipt.width}x{receipt.height},{receipt.fps}fps,"
        f"{receipt.frame_count}frames,{receipt.duration_ms}ms,"
        f"{receipt.bytes_written}bytes,sha256={receipt.sha256}"
    )
