"""LE-11 T2: execute the public audio graph with packaged FFmpeg."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from automation_tool.executor.audio_rendering import compile_audio_filter_graph
from automation_tool.executor.material_probe import PackagedMediaTools
from automation_tool.protocol.local_rendering import (
    LocalEditingAudioRenderClip,
    LocalEditingAudioRenderPlan,
    LocalEditingAudioTrackKind,
    LocalEditingOriginalAudioMode,
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


def test_real_graph_outputs_one_exact_48khz_stereo_timeline(tmp_path: Path) -> None:
    tools = _tools()
    project_id = uuid4()
    timeline_id = uuid4()
    identities = tuple(uuid4() for _ in range(4))
    clips = (
        LocalEditingAudioRenderClip(
            1,
            LocalEditingAudioTrackKind.NARRATION,
            identities[0],
            200,
            300,
            100,
            400,
            0.0,
            None,
        ),
        LocalEditingAudioRenderClip(
            2,
            LocalEditingAudioTrackKind.AMBIENT,
            identities[1],
            0,
            500,
            100,
            600,
            -12.0,
            LocalEditingOriginalAudioMode.AUTO_DUCK,
        ),
        LocalEditingAudioRenderClip(
            3,
            LocalEditingAudioTrackKind.AMBIENT,
            identities[2],
            500,
            500,
            100,
            600,
            -9.0,
            LocalEditingOriginalAudioMode.FIXED_VOLUME,
        ),
        LocalEditingAudioRenderClip(
            4,
            LocalEditingAudioTrackKind.MUSIC,
            identities[3],
            0,
            1000,
            100,
            1100,
            -24.0,
            None,
        ),
    )
    plan = LocalEditingAudioRenderPlan(
        project_id,
        timeline_id,
        1,
        1000,
        clips,
    )
    compiled = compile_audio_filter_graph(plan, first_input_index=0)
    sources: list[Path] = []
    for index, frequency in enumerate((900, 300, 500, 120), start=1):
        source = tmp_path / f"source-{index}.wav"
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
                f"sine=frequency={frequency}:sample_rate=44100:duration=1.2",
                "-c:a",
                "pcm_s16le",
                os.fspath(source),
            ),
            check=True,
        )
        sources.append(source)
    output = tmp_path / "mix.wav"
    arguments: list[str] = [
        os.fspath(tools.ffmpeg_path),
        "-y",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    for source in sources:
        arguments.extend(("-i", os.fspath(source)))
    arguments.extend(
        (
            "-filter_complex",
            compiled.filter_graph,
            "-map",
            f"[{compiled.output_label}]",
            "-c:a",
            "pcm_s16le",
            os.fspath(output),
        )
    )
    subprocess.run(arguments, check=True)
    probe = subprocess.run(
        (
            os.fspath(tools.ffprobe_path),
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,sample_rate,channels:format=duration",
            "-of",
            "json",
            os.fspath(output),
        ),
        check=True,
        capture_output=True,
        text=True,
    )
    metadata = json.loads(probe.stdout)

    assert metadata["streams"] == [
        {"codec_type": "audio", "sample_rate": "48000", "channels": 2}
    ]
    assert float(metadata["format"]["duration"]) == pytest.approx(1.0, abs=0.001)
    print(
        "audioGraphReceipt="
        f"48000Hz,stereo,{metadata['format']['duration']}s,{output.stat().st_size}bytes"
    )
