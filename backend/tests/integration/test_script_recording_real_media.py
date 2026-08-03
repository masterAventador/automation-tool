"""LE-15 T3: real packaged media tools feed the recording-alignment boundary."""

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
)
from automation_tool.executor.material_speech_pipeline import (  # noqa: E402
    SpeechAudioBatch,
)
from automation_tool.executor.script_recording import (  # noqa: E402
    align_script_recording,
)
from automation_tool.executor.script_segmentation import (  # noqa: E402
    ScriptSegmentationResult,
    ScriptSentence,
)

_PACKAGED_TOOL_SUBDIRECTORY = "media-toolchain/bin"


def _packaged_tools() -> PackagedMediaTools:
    suffix = ".exe" if os.name == "nt" else ""
    root = cache_root() / _PACKAGED_TOOL_SUBDIRECTORY
    return PackagedMediaTools(
        ffprobe_path=root / f"ffprobe{suffix}",
        ffmpeg_path=root / f"ffmpeg{suffix}",
    )


class TwoSpeechRegions:
    def __init__(self) -> None:
        self.calls = 0

    def probability(self, samples: object, *, sample_rate_hz: object) -> float:
        assert isinstance(samples, tuple)
        assert len(samples) == 512
        assert sample_rate_hz == 16_000
        self.calls += 1
        return 0.9 if 1 <= self.calls <= 8 or 17 <= self.calls <= 24 else 0.0


class RecordingAsr:
    def __init__(self) -> None:
        self.batches: list[SpeechAudioBatch] = []

    def transcribe(self, audio: SpeechAudioBatch) -> str:
        self.batches.append(audio)
        return "第一句 第二句"


def test_real_ffprobe_and_ffmpeg_produce_two_path_free_recorded_clips(
    tmp_path: Path,
) -> None:
    tools = _packaged_tools()
    source = tmp_path / "用户 录音 &$ '.wav"
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
            "sine=frequency=440:sample_rate=48000:duration=1.024",
            "-c:a",
            "pcm_s16le",
            os.fspath(source),
        ],
        check=True,
        capture_output=True,
    )
    source, approved = approve_source(source)
    vad = TwoSpeechRegions()
    asr = RecordingAsr()

    result = align_script_recording(
        ScriptSegmentationResult(
            request_id="real-recording",
            sentences=(
                ScriptSentence(sequence=1, text="第一句"),
                ScriptSentence(sequence=2, text="第二句"),
            ),
        ),
        source=source,
        approved=approved,
        tools=tools,
        vad_factory=lambda: vad,
        asr_adapter=asr,
    )

    assert result.recording_duration_ms == 1_024
    assert tuple(
        (clip.source_start_ms, clip.source_end_ms, clip.duration_ms) for clip in result.clips
    ) == ((0, 320, 320), (448, 832, 384))
    assert len(asr.batches) == 1
    assert asr.batches[0].duration_ms == 1_024
    assert asr.batches[0].wav_bytes.startswith(b"RIFF")
    assert os.fspath(source).encode() not in asr.batches[0].wav_bytes
