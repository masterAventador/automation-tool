"""LE-14 T3: real packaged FFmpeg feeds the path-free speech pipeline."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, os.fspath(REPOSITORY_ROOT / "scripts"))
from video_runtime_cache import cache_root  # type: ignore[import-not-found]  # noqa: E402

from automation_tool.executor.material_probe import (  # noqa: E402
    PackagedMediaTools,
    approve_source,
    probe_material,
)
from automation_tool.executor.material_speech_analysis import (  # noqa: E402
    MaterialSpeechAnalysis,
    analyze_material_speech,
)
from automation_tool.executor.material_speech_pipeline import (  # noqa: E402
    LocalAudibleSpeechAnalyzerFactory,
    SpeechAudioBatch,
)

_PACKAGED_TOOL_SUBDIRECTORY = "media-toolchain/bin"


def _packaged_tools() -> PackagedMediaTools:
    suffix = ".exe" if os.name == "nt" else ""
    root = cache_root() / _PACKAGED_TOOL_SUBDIRECTORY
    return PackagedMediaTools(
        ffprobe_path=root / f"ffprobe{suffix}",
        ffmpeg_path=root / f"ffmpeg{suffix}",
    )


class FirstTenChunksAreSpeech:
    def __init__(self) -> None:
        self.calls = 0

    def probability(self, samples: object, *, sample_rate_hz: object) -> float:
        assert isinstance(samples, tuple)
        assert len(samples) == 512
        assert sample_rate_hz == 16_000
        self.calls += 1
        return 0.9 if self.calls <= 10 else 0.0


class RecordingAsr:
    def __init__(self) -> None:
        self.batches: list[SpeechAudioBatch] = []

    def transcribe(self, audio: SpeechAudioBatch) -> str:
        self.batches.append(audio)
        return "真实随包工具抽出的音轨。"


def test_real_packaged_ffmpeg_extracts_pcm_before_the_asr_boundary(
    tmp_path: Path,
) -> None:
    tools = _packaged_tools()
    source = tmp_path / "真实 音轨 &$ '.mp4"
    subprocess.run(
        [
            os.fspath(tools.ffmpeg_path),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=navy:s=320x180:r=10:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100:duration=1",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            os.fspath(source),
        ],
        check=True,
        capture_output=True,
    )
    facts = probe_material(tools, source)
    assert facts.has_audio is True
    approved_source, approved = approve_source(source)
    vad = FirstTenChunksAreSpeech()
    asr = RecordingAsr()

    result = analyze_material_speech(
        facts,
        audible_analyzer_factory=LocalAudibleSpeechAnalyzerFactory(
            tools=tools,
            source=approved_source,
            approved=approved,
            vad_factory=lambda: vad,
            asr_adapter=asr,
        ),
    )

    assert result == MaterialSpeechAnalysis(
        has_speech=True,
        speech_segments_ms=((0, 384),),
        speech_transcript="真实随包工具抽出的音轨。",
    )
    assert vad.calls >= 14
    assert len(asr.batches) == 1
    assert asr.batches[0].wav_bytes.startswith(b"RIFF")
    assert asr.batches[0].wav_bytes[8:12] == b"WAVE"
    assert asr.batches[0].duration_ms == facts.duration_ms
