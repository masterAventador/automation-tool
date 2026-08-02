"""LE-11 T5: prove the three original-audio modes with real decoded levels."""

from __future__ import annotations

import math
import os
import struct
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from PIL import Image

from automation_tool.executor.audio_rendering import AudioRenderSourceBinding
from automation_tool.executor.material_probe import PackagedMediaTools, approve_source
from automation_tool.executor.visual_render_execution import (
    VISUAL_RENDER_OUTPUT_FILENAME,
    VisualRenderReceipt,
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
SAMPLE_RATE = 48_000
AMBIENT_FREQUENCY = 300
NARRATION_FREQUENCY = 1000
MEDIA_COMMAND_TIMEOUT_SECONDS = 30
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


def _tone(tools: PackagedMediaTools, path: Path, frequency: int, duration: float) -> None:
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
            f"sine=frequency={frequency}:sample_rate=44100:duration={duration}",
            "-c:a",
            "pcm_s16le",
            os.fspath(path),
        ),
        check=True,
        timeout=MEDIA_COMMAND_TIMEOUT_SECONDS,
    )


def _audio_plan(
    project_id: UUID,
    timeline_id: UUID,
    narration_id: UUID,
    ambient_id: UUID,
    mode: LocalEditingOriginalAudioMode,
) -> LocalEditingAudioRenderPlan:
    return LocalEditingAudioRenderPlan(
        project_id,
        timeline_id,
        1,
        2000,
        (
            LocalEditingAudioRenderClip(
                1,
                LocalEditingAudioTrackKind.NARRATION,
                narration_id,
                700,
                600,
                0,
                600,
                0.0,
                None,
            ),
            LocalEditingAudioRenderClip(
                2,
                LocalEditingAudioTrackKind.AMBIENT,
                ambient_id,
                0,
                2000,
                0,
                2000,
                -6.0,
                mode,
            ),
        ),
    )


def _decode_left_channel(tools: PackagedMediaTools, output: Path) -> tuple[float, ...]:
    decoded = subprocess.run(
        (
            os.fspath(tools.ffmpeg_path),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            os.fspath(output),
            "-map",
            "0:a:0",
            "-f",
            "f32le",
            "-acodec",
            "pcm_f32le",
            "-ar",
            str(SAMPLE_RATE),
            "-ac",
            "2",
            "pipe:1",
        ),
        check=True,
        capture_output=True,
        timeout=MEDIA_COMMAND_TIMEOUT_SECONDS,
    )
    interleaved = tuple(value[0] for value in struct.iter_unpack("<f", decoded.stdout))
    return interleaved[0::2]


def _frequency_amplitude(
    samples: tuple[float, ...],
    frequency: int,
    start_seconds: float,
    end_seconds: float,
) -> float:
    start = round(start_seconds * SAMPLE_RATE)
    end = round(end_seconds * SAMPLE_RATE)
    window = samples[start:end]
    cosine = 0.0
    sine = 0.0
    for offset, sample in enumerate(window, start=start):
        phase = 2.0 * math.pi * frequency * offset / SAMPLE_RATE
        cosine += sample * math.cos(phase)
        sine += sample * math.sin(phase)
    return 2.0 * math.hypot(cosine, sine) / len(window)


def test_three_real_modes_have_distinct_reproducible_levels(tmp_path: Path) -> None:
    tools = _tools()
    project_id, timeline_id, visual_id, narration_id, ambient_id = (
        uuid4() for _ in range(5)
    )
    visual_path = tmp_path / "visual.png"
    Image.new("RGB", (320, 180), (25, 65, 125)).save(visual_path)
    narration_path = tmp_path / "narration.wav"
    ambient_path = tmp_path / "ambient.wav"
    _tone(tools, narration_path, NARRATION_FREQUENCY, 0.6)
    _tone(tools, ambient_path, AMBIENT_FREQUENCY, 2.0)
    _, visual_approved = approve_source(visual_path)
    _, narration_approved = approve_source(narration_path)
    _, ambient_approved = approve_source(ambient_path)
    visual_plan = LocalEditingVisualRenderPlan(
        project_id,
        timeline_id,
        1,
        720,
        1280,
        30,
        2000,
        (
            LocalEditingVisualRenderClip(
                1,
                visual_id,
                SegmentSelectionMaterialKind.IMAGE,
                0,
                2000,
                None,
                None,
                None,
                None,
            ),
        ),
    )
    visual_sources = (
        VisualRenderSourceBinding(
            visual_id,
            SegmentSelectionMaterialKind.IMAGE,
            visual_path,
        ),
    )
    audio_sources = (
        AudioRenderSourceBinding(narration_id, narration_path, True),
        AudioRenderSourceBinding(ambient_id, ambient_path, True),
    )
    receipts: dict[LocalEditingOriginalAudioMode, VisualRenderReceipt] = {}
    decoded: dict[LocalEditingOriginalAudioMode, tuple[float, ...]] = {}
    for mode in LocalEditingOriginalAudioMode:
        task = tmp_path / mode.value
        task.mkdir()
        receipts[mode] = execute_audiovisual_render(
            tools,
            visual_plan,
            visual_sources,
            (visual_approved,),
            _audio_plan(project_id, timeline_id, narration_id, ambient_id, mode),
            audio_sources,
            (narration_approved, ambient_approved),
            task,
        )
        decoded[mode] = _decode_left_channel(
            tools,
            task / VISUAL_RENDER_OUTPUT_FILENAME,
        )

    before = {
        mode: _frequency_amplitude(samples, AMBIENT_FREQUENCY, 0.2, 0.5)
        for mode, samples in decoded.items()
    }
    during = {
        mode: _frequency_amplitude(samples, AMBIENT_FREQUENCY, 0.9, 1.1)
        for mode, samples in decoded.items()
    }
    narration = {
        mode: _frequency_amplitude(samples, NARRATION_FREQUENCY, 0.9, 1.1)
        for mode, samples in decoded.items()
    }
    auto = LocalEditingOriginalAudioMode.AUTO_DUCK
    fixed = LocalEditingOriginalAudioMode.FIXED_VOLUME
    muted = LocalEditingOriginalAudioMode.MUTED

    assert before[auto] == pytest.approx(before[fixed], rel=0.15)
    assert during[fixed] == pytest.approx(before[fixed], rel=0.15)
    assert during[auto] < during[fixed] * 0.50
    assert before[muted] < before[fixed] * 0.03
    assert during[muted] < during[fixed] * 0.03
    assert min(narration.values()) > 0.05
    assert max(narration.values()) / min(narration.values()) < 1.15
    assert all(receipt.audio_codec == "aac" for receipt in receipts.values())
    assert all(receipt.audio_sample_rate == 48_000 for receipt in receipts.values())
    assert all(receipt.audio_channels == 2 for receipt in receipts.values())
    assert all(receipt.duration_ms == 2000 for receipt in receipts.values())
    print(
        "audioModeLevels="
        f"auto(before={before[auto]:.6f},during={during[auto]:.6f}),"
        f"fixed(before={before[fixed]:.6f},during={during[fixed]:.6f}),"
        f"muted(before={before[muted]:.6f},during={during[muted]:.6f}),"
        f"narrationMin={min(narration.values()):.6f}"
    )
    print(
        "audioModeReceipts="
        + ",".join(
            f"{mode.value}:{receipt.bytes_written}:{receipt.sha256}"
            for mode, receipt in receipts.items()
        )
    )
