"""LE-15 T3: reuse LE-14's bounded local audio and neutral ASR boundary."""

from __future__ import annotations

import subprocess
from collections.abc import Generator
from pathlib import Path

import pytest

from automation_tool.executor.material_probe import PackagedMediaTools, approve_source
from automation_tool.executor.material_speech_analysis import (
    MAX_TRANSCRIPT_CHARACTERS,
    MaterialSpeechRejected,
)
from automation_tool.executor.material_speech_pipeline import (
    LocalRecordedSpeechAnalyzer,
    RecordedSpeechAnalysis,
    SpeechAudioBatch,
)


class SequencedVad:
    def __init__(self, probabilities: list[float]) -> None:
        self._probabilities = iter(probabilities)

    def probability(self, samples: object, *, sample_rate_hz: object) -> float:
        assert isinstance(samples, tuple)
        assert len(samples) == 512
        assert sample_rate_hz == 16_000
        return next(self._probabilities)


class RecordingAsr:
    def __init__(self, transcript: str = "短旁白") -> None:
        self.transcript = transcript
        self.calls: list[SpeechAudioBatch] = []

    def transcribe(self, audio: SpeechAudioBatch) -> str:
        self.calls.append(audio)
        return self.transcript


class FinishedExtraction:
    chunks = 64

    def __init__(self, argv: list[str], **kwargs: object) -> None:
        assert kwargs["stdin"] is subprocess.DEVNULL
        assert kwargs["stdout"] is subprocess.DEVNULL
        assert kwargs["stderr"] is subprocess.DEVNULL
        self.argv = argv
        self.returncode = 0
        Path(argv[-1]).write_bytes(b"\x01\x00" * 512 * self.chunks)

    def __enter__(self) -> FinishedExtraction:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return self.returncode

    def poll(self) -> int:
        return self.returncode

    def kill(self) -> None:
        raise AssertionError("a successful extraction must not be killed")


def _tools(tmp_path: Path) -> PackagedMediaTools:
    ffmpeg = tmp_path / "ffmpeg"
    ffprobe = tmp_path / "ffprobe"
    for tool in (ffmpeg, ffprobe):
        tool.write_bytes(b"packaged tool")
        tool.chmod(0o700)
    return PackagedMediaTools(ffprobe_path=ffprobe, ffmpeg_path=ffmpeg)


@pytest.fixture(autouse=True)
def _reset_extraction_chunks() -> None:
    FinishedExtraction.chunks = 64


def _analyzer(
    tmp_path: Path,
    *,
    vad: SequencedVad,
    asr: RecordingAsr,
) -> LocalRecordedSpeechAnalyzer:
    source = tmp_path / "private recording.m4a"
    source.write_bytes(b"private container bytes")
    source, approved = approve_source(source)
    return LocalRecordedSpeechAnalyzer(
        tools=_tools(tmp_path),
        source=source,
        approved=approved,
        vad_factory=lambda: vad,
        asr_adapter=asr,
    )


def test_short_known_narration_skips_the_material_background_speech_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "automation_tool.executor.material_speech_pipeline.subprocess.Popen",
        FinishedExtraction,
    )
    asr = RecordingAsr()
    analyzer = _analyzer(
        tmp_path,
        vad=SequencedVad([0.9] * 10 + [0.0] * 54),
        asr=asr,
    )

    result = analyzer.analyze(2_048, minimum_segments=1)

    assert result == RecordedSpeechAnalysis(
        duration_ms=2_048,
        speech_segments_ms=((0, 384),),
        transcript="短旁白",
    )
    assert len(asr.calls) == 1
    assert asr.calls[0].duration_ms == 2_048
    assert asr.calls[0].wav_bytes.startswith(b"RIFF")
    assert not hasattr(asr.calls[0], "path")


def test_too_few_real_speech_segments_is_rejected_before_asr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "automation_tool.executor.material_speech_pipeline.subprocess.Popen",
        FinishedExtraction,
    )
    asr = RecordingAsr()
    analyzer = _analyzer(
        tmp_path,
        vad=SequencedVad([0.9] * 10 + [0.0] * 54),
        asr=asr,
    )

    with pytest.raises(MaterialSpeechRejected) as captured:
        analyzer.analyze(2_048, minimum_segments=2)

    assert asr.calls == []
    assert captured.value.__context__ is None


def test_multiple_real_segments_are_preserved_while_asr_stays_path_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FinishedExtraction.chunks = 32
    monkeypatch.setattr(
        "automation_tool.executor.material_speech_pipeline.subprocess.Popen",
        FinishedExtraction,
    )
    asr = RecordingAsr("第一句 第二句")
    analyzer = _analyzer(
        tmp_path,
        vad=SequencedVad([0.9] * 8 + [0.0] * 8 + [0.9] * 8 + [0.0] * 8),
        asr=asr,
    )

    result = analyzer.analyze(1_024, minimum_segments=2)

    assert result.speech_segments_ms == ((0, 320), (448, 832))
    assert result.transcript == "第一句 第二句"
    assert len(asr.calls) == 1


@pytest.mark.parametrize(
    ("duration_ms", "segments", "transcript"),
    [
        (0, ((0, 1),), "一句"),
        (1_000, (), "一句"),
        (1_000, ((0, 1_001),), "一句"),
        (1_000, ((0, 500), (400, 900)), "一句"),
        (1_000, ((0, 500),), ""),
    ],
)
def test_recorded_speech_result_rejects_invalid_public_shapes(
    duration_ms: int,
    segments: tuple[tuple[int, int], ...],
    transcript: str,
) -> None:
    with pytest.raises(MaterialSpeechRejected):
        RecordedSpeechAnalysis(
            duration_ms=duration_ms,
            speech_segments_ms=segments,
            transcript=transcript,
        )


def test_invalid_analyzer_construction_and_call_arguments_are_closed(
    tmp_path: Path,
) -> None:
    source = tmp_path / "recording.wav"
    source.write_bytes(b"recording")
    source, approved = approve_source(source)
    tools = _tools(tmp_path)
    asr = RecordingAsr()

    with pytest.raises(MaterialSpeechRejected):
        LocalRecordedSpeechAnalyzer(
            tools=tools,
            source="recording.wav",  # type: ignore[arg-type]
            approved=approved,
            vad_factory=lambda: SequencedVad([]),
            asr_adapter=asr,
        )

    analyzer = LocalRecordedSpeechAnalyzer(
        tools=tools,
        source=source,
        approved=approved,
        vad_factory=lambda: SequencedVad([]),
        asr_adapter=asr,
    )
    with pytest.raises(MaterialSpeechRejected) as captured:
        analyzer.analyze(0, minimum_segments=1)
    assert captured.value.__context__ is None
    assert asr.calls == []


def test_temporary_workspace_failure_is_closed_before_vad_and_asr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asr = RecordingAsr()
    analyzer = _analyzer(
        tmp_path,
        vad=SequencedVad([]),
        asr=asr,
    )

    def fail_workspace(*args: object, **kwargs: object) -> str:
        del args, kwargs
        raise OSError("private workspace")

    monkeypatch.setattr(
        "automation_tool.executor.material_speech_pipeline.tempfile.mkdtemp",
        fail_workspace,
    )

    with pytest.raises(MaterialSpeechRejected) as captured:
        analyzer.analyze(2_048, minimum_segments=1)
    assert captured.value.__context__ is None
    assert asr.calls == []


def test_invalid_vad_is_closed_before_asr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "automation_tool.executor.material_speech_pipeline.subprocess.Popen",
        FinishedExtraction,
    )
    source = tmp_path / "recording.wav"
    source.write_bytes(b"recording")
    source, approved = approve_source(source)
    asr = RecordingAsr()
    analyzer = LocalRecordedSpeechAnalyzer(
        tools=_tools(tmp_path),
        source=source,
        approved=approved,
        vad_factory=object,
        asr_adapter=asr,
    )

    with pytest.raises(MaterialSpeechRejected):
        analyzer.analyze(2_048, minimum_segments=1)
    assert asr.calls == []


@pytest.mark.parametrize("transcript", ["", " padded ", "x" * (MAX_TRANSCRIPT_CHARACTERS + 1)])
def test_invalid_asr_text_is_rejected_without_a_partial_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transcript: str,
) -> None:
    monkeypatch.setattr(
        "automation_tool.executor.material_speech_pipeline.subprocess.Popen",
        FinishedExtraction,
    )
    asr = RecordingAsr(transcript)
    analyzer = _analyzer(
        tmp_path,
        vad=SequencedVad([0.9] * 10 + [0.0] * 54),
        asr=asr,
    )

    with pytest.raises(MaterialSpeechRejected):
        analyzer.analyze(2_048, minimum_segments=1)
    assert len(asr.calls) == 1


def test_an_empty_internal_batch_iterator_cannot_become_a_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "automation_tool.executor.material_speech_pipeline.subprocess.Popen",
        FinishedExtraction,
    )

    def no_batches(*args: object, **kwargs: object) -> Generator[SpeechAudioBatch, None, None]:
        del args, kwargs
        if False:
            yield SpeechAudioBatch(b"", 0)

    monkeypatch.setattr(
        "automation_tool.executor.material_speech_pipeline._speech_audio_batches",
        no_batches,
    )
    asr = RecordingAsr()
    analyzer = _analyzer(
        tmp_path,
        vad=SequencedVad([0.9] * 10 + [0.0] * 54),
        asr=asr,
    )

    with pytest.raises(MaterialSpeechRejected):
        analyzer.analyze(2_048, minimum_segments=1)
    assert asr.calls == []
