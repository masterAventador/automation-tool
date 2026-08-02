"""LE-11 T4: real packaged H.264 + AAC execution and publication."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image

from automation_tool.executor.audio_rendering import AudioRenderSourceBinding
from automation_tool.executor.material_probe import PackagedMediaTools, approve_source
from automation_tool.executor.visual_render_execution import (
    VISUAL_RENDER_OUTPUT_FILENAME,
    execute_audiovisual_render,
)
from automation_tool.executor.visual_rendering import VisualRenderSourceBinding
from automation_tool.protocol.local_editing import SegmentSelectionMaterialKind
from automation_tool.protocol.local_rendering import (
    LocalEditingAudioRenderClip,
    LocalEditingAudioRenderPlan,
    LocalEditingAudioTrackKind,
    LocalEditingOriginalAudioMode,
    LocalEditingVisualRenderClip,
    LocalEditingVisualRenderPlan,
)

TOOLCHAIN_ROOT_ENVIRONMENT = "AUTOMATION_TOOL_LE11_TOOLCHAIN_ROOT"
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


def test_real_audiovisual_render_publishes_verified_h264_aac(
    tmp_path: Path,
) -> None:
    tools = _tools()
    project_id, timeline_id, visual_id, audio_id = (uuid4() for _ in range(4))
    visual_path = tmp_path / "visual.png"
    Image.new("RGB", (320, 180), (20, 60, 120)).save(visual_path)
    _, visual_approved = approve_source(visual_path)
    audio_path = tmp_path / "narration.wav"
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
            "sine=frequency=700:sample_rate=44100:duration=1",
            "-c:a",
            "pcm_s16le",
            os.fspath(audio_path),
        ),
        check=True,
    )
    _, audio_approved = approve_source(audio_path)
    visual_plan = LocalEditingVisualRenderPlan(
        project_id,
        timeline_id,
        1,
        720,
        1280,
        30,
        1000,
        (
            LocalEditingVisualRenderClip(
                1,
                visual_id,
                SegmentSelectionMaterialKind.IMAGE,
                0,
                1000,
                None,
                None,
                None,
                None,
            ),
        ),
    )
    audio_plan = LocalEditingAudioRenderPlan(
        project_id,
        timeline_id,
        1,
        1000,
        (
            LocalEditingAudioRenderClip(
                1,
                LocalEditingAudioTrackKind.NARRATION,
                audio_id,
                0,
                1000,
                0,
                1000,
                0.0,
                None,
            ),
        ),
    )
    task = tmp_path / "task"
    task.mkdir()

    receipt = execute_audiovisual_render(
        tools,
        visual_plan,
        (
            VisualRenderSourceBinding(
                visual_id,
                SegmentSelectionMaterialKind.IMAGE,
                visual_path,
            ),
        ),
        (visual_approved,),
        audio_plan,
        (AudioRenderSourceBinding(audio_id, audio_path, True),),
        (audio_approved,),
        task,
    )

    output = task / VISUAL_RENDER_OUTPUT_FILENAME
    assert output.stat().st_size == receipt.bytes_written
    assert receipt.frame_count == 30
    assert (receipt.width, receipt.height) == (720, 1280)
    assert receipt.fps == 30
    assert receipt.duration_ms == 1000
    assert receipt.audio_codec == "aac"
    assert receipt.audio_sample_rate == 48000
    assert receipt.audio_channels == 2
    assert [path.name for path in task.iterdir()] == [VISUAL_RENDER_OUTPUT_FILENAME]
    print(
        "audiovisualReceipt="
        f"h264+aac,{receipt.width}x{receipt.height},{receipt.fps}fps,"
        f"{receipt.frame_count}frames,{receipt.duration_ms}ms,"
        f"48000Hz,stereo,{receipt.bytes_written}bytes,sha256={receipt.sha256}"
    )


def test_real_portrait_render_keeps_audio_from_the_same_landscape_video(
    tmp_path: Path,
) -> None:
    tools = _tools()
    project_id, timeline_id, material_id = (uuid4() for _ in range(3))
    source = tmp_path / "speech.mkv"
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
            "color=c=navy:s=1280x720:r=17:d=7.173",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=700:sample_rate=44100:duration=6.173",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "flac",
            "-t",
            "6.173",
            os.fspath(source),
        ),
        check=True,
        timeout=30,
    )
    _, approved = approve_source(source)
    duration_ms = 5_088
    visual_plan = LocalEditingVisualRenderPlan(
        project_id,
        timeline_id,
        1,
        720,
        1280,
        20,
        duration_ms,
        (
            LocalEditingVisualRenderClip(
                1,
                material_id,
                SegmentSelectionMaterialKind.VIDEO,
                0,
                duration_ms,
                480,
                5_568,
                None,
                None,
            ),
        ),
    )
    audio_plan = LocalEditingAudioRenderPlan(
        project_id,
        timeline_id,
        1,
        duration_ms,
        (
            LocalEditingAudioRenderClip(
                1,
                LocalEditingAudioTrackKind.AMBIENT,
                material_id,
                0,
                duration_ms,
                480,
                5_568,
                0.0,
                LocalEditingOriginalAudioMode.FIXED_VOLUME,
            ),
        ),
    )
    task = tmp_path / "task"
    task.mkdir()

    receipt = execute_audiovisual_render(
        tools,
        visual_plan,
        (
            VisualRenderSourceBinding(
                material_id,
                SegmentSelectionMaterialKind.VIDEO,
                source,
            ),
        ),
        (approved,),
        audio_plan,
        (AudioRenderSourceBinding(material_id, source, True),),
        (approved,),
        task,
    )

    assert receipt.duration_ms == 5_100
    assert receipt.frame_count == 102
    assert receipt.audio_codec == "aac"
