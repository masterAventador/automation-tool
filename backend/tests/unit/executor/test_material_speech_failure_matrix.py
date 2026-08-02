"""LE-14 T4: close the six decided speech-analysis failure classes."""

from __future__ import annotations

import io
import os
import traceback
import urllib.request
import wave
from pathlib import Path

import pytest

from automation_tool.executor import material_speech_pipeline as pipeline
from automation_tool.executor import material_speech_transcription as transcription
from automation_tool.executor.material_probe import (
    MaterialFacts,
    PackagedMediaTools,
    ProbedMaterialKind,
    approve_source,
)
from automation_tool.executor.material_speech_analysis import (
    MaterialSpeechAnalysis,
    MaterialSpeechRejected,
)
from automation_tool.executor.material_speech_pipeline import (
    LocalAudibleSpeechAnalyzer,
    SpeechAudioBatch,
)
from automation_tool.executor.material_speech_transcription import (
    BAILIAN_ASR_MODEL_ID,
    BAILIAN_BASE_URL,
    BailianSpeechTranscriptionAdapter,
    BailianSpeechTranscriptionConfig,
    SpeechTranscriptionRejected,
)

API_KEY = "sk-private-speech-failure-matrix-key"
PRIVATE_MARKER = "/Users/operator/private/background-passerby.mp4"


class FixedVad:
    def probability(self, samples: object, *, sample_rate_hz: object) -> float:
        del samples, sample_rate_hz
        return 0.0


class SequencedVad:
    def __init__(self, probabilities: list[float]) -> None:
        self._probabilities = iter(probabilities)

    def probability(self, samples: object, *, sample_rate_hz: object) -> float:
        del samples, sample_rate_hz
        return next(self._probabilities)


class FixedAsr:
    def __init__(self, result: str) -> None:
        self._result = result
        self.calls: list[SpeechAudioBatch] = []

    def transcribe(self, audio: SpeechAudioBatch) -> str:
        self.calls.append(audio)
        return self._result


def _facts(duration_ms: int) -> MaterialFacts:
    return MaterialFacts(
        kind=ProbedMaterialKind.VIDEO,
        duration_ms=duration_ms,
        width=640,
        height=360,
        video_codec="h264",
        audio_codec="aac",
        has_audio=True,
        audio_loudness_lufs=-20.0,
        content_digest="f" * 64,
    )


def _tools(tmp_path: Path) -> PackagedMediaTools:
    ffmpeg = tmp_path / "ffmpeg"
    ffprobe = tmp_path / "ffprobe"
    for tool in (ffmpeg, ffprobe):
        tool.write_bytes(b"packaged tool")
        tool.chmod(0o700)
    return PackagedMediaTools(ffprobe_path=ffprobe, ffmpeg_path=ffmpeg)


def _analyzer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    duration_ms: int,
    segments: tuple[tuple[int, int], ...],
    transcript: str,
    confirmed_speech_duration_ms: int | None = None,
) -> tuple[LocalAudibleSpeechAnalyzer, FixedAsr]:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"private video")
    source, approved = approve_source(source)
    pcm_bytes = duration_ms * pipeline.PCM_BYTES_PER_MILLISECOND

    def extract(
        _ffmpeg: Path,
        _source: Path,
        output: Path,
        *,
        duration_ms: int,
    ) -> os.stat_result:
        assert duration_ms * pipeline.PCM_BYTES_PER_MILLISECOND == pcm_bytes
        with output.open("wb") as stream:
            stream.truncate(pcm_bytes)
        return output.stat()

    monkeypatch.setattr(pipeline, "_extract_pcm", extract)
    monkeypatch.setattr(
        pipeline,
        "_detect_speech_segments",
        lambda *_args, **_kwargs: (
            segments,
            (
                sum(end - start for start, end in segments)
                if confirmed_speech_duration_ms is None
                else confirmed_speech_duration_ms
            ),
            pcm_bytes,
        ),
    )
    asr = FixedAsr(transcript)
    return (
        LocalAudibleSpeechAnalyzer(
            tools=_tools(tmp_path),
            source=source,
            approved=approved,
            vad_factory=FixedVad,
            asr_adapter=asr,
        ),
        asr,
    )


@pytest.mark.parametrize(
    "probabilities",
    [
        [0.49] * 20,
        [0.95] * 7 + [0.0] * 13,
    ],
    ids=["pure-music-below-threshold", "isolated-environment-pulse"],
)
def test_music_and_environment_without_confirmed_speech_stay_out_of_asr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    probabilities: list[float],
) -> None:
    source = tmp_path / "music-or-environment.mp4"
    source.write_bytes(b"private video")
    source, approved = approve_source(source)

    def extract(
        _ffmpeg: Path,
        _source: Path,
        output: Path,
        *,
        duration_ms: int,
    ) -> os.stat_result:
        assert duration_ms == 640
        output.write_bytes(b"\0\0" * pipeline.CHUNK_SAMPLES * len(probabilities))
        return output.stat()

    monkeypatch.setattr(pipeline, "_extract_pcm", extract)
    asr = FixedAsr("不应调用")
    analyzer = LocalAudibleSpeechAnalyzer(
        tools=_tools(tmp_path),
        source=source,
        approved=approved,
        vad_factory=lambda: SequencedVad(probabilities),
        asr_adapter=asr,
    )

    assert analyzer.analyze(_facts(640)) == MaterialSpeechAnalysis(False, (), None)
    assert asr.calls == []


@pytest.mark.parametrize("transcript", ["", "不可用\x00结果"])
def test_vad_false_positive_with_unusable_transcript_returns_no_partial_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transcript: str,
) -> None:
    analyzer, asr = _analyzer(
        tmp_path,
        monkeypatch,
        duration_ms=2_000,
        segments=((0, 1_000),),
        transcript=transcript,
    )

    with pytest.raises(
        MaterialSpeechRejected,
        match=r"^material speech analysis rejected$",
    ):
        analyzer.analyze(_facts(2_000))

    assert len(asr.calls) == 1


def test_dialect_and_noisy_unicode_transcript_is_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transcript = "侬好，今朝风噪蛮大。\nLet's keep going.\t嗯"  # noqa: RUF001
    analyzer, asr = _analyzer(
        tmp_path,
        monkeypatch,
        duration_ms=2_000,
        segments=((0, 1_000),),
        transcript=transcript,
    )

    result = analyzer.analyze(_facts(2_000))

    assert result == MaterialSpeechAnalysis(True, ((0, 1_000),), transcript)
    assert len(asr.calls) == 1


def test_asr_timeout_is_one_redacted_attempt_without_exception_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def timeout(
        _request: urllib.request.Request,
        *,
        timeout: float,
    ) -> object:
        nonlocal calls
        calls += 1
        raise TimeoutError(f"{PRIVATE_MARKER} {API_KEY} after {timeout}")

    monkeypatch.setattr(transcription, "_open_request", timeout)
    adapter = BailianSpeechTranscriptionAdapter(
        BailianSpeechTranscriptionConfig(
            base_url=BAILIAN_BASE_URL,
            model_id=BAILIAN_ASR_MODEL_ID,
            api_key=API_KEY,
            timeout_seconds=90,
        )
    )

    with pytest.raises(
        SpeechTranscriptionRejected,
        match=r"^speech transcription request rejected$",
    ) as captured:
        adapter.transcribe(SpeechAudioBatch(_wav(), 100))

    rendered = "".join(
        traceback.format_exception(
            type(captured.value),
            captured.value,
            captured.value.__traceback__,
        )
    )
    assert calls == 1
    assert PRIVATE_MARKER not in rendered
    assert API_KEY not in rendered
    assert captured.value.__context__ is None


@pytest.mark.parametrize(
    ("duration_ms", "segments"),
    [
        (10_000, ((4_000, 6_999),)),
        (60_000, ((20_000, 24_999),)),
    ],
    ids=["one-millisecond-below-coverage", "one-millisecond-below-duration"],
)
def test_sparse_background_passerby_is_rejected_before_asr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    duration_ms: int,
    segments: tuple[tuple[int, int], ...],
) -> None:
    analyzer, asr = _analyzer(
        tmp_path,
        monkeypatch,
        duration_ms=duration_ms,
        segments=segments,
        transcript="路人在远处说了一句话。",
    )

    with pytest.raises(
        MaterialSpeechRejected,
        match=r"^material speech analysis rejected$",
    ):
        analyzer.analyze(_facts(duration_ms))

    assert asr.calls == []


def test_padding_cannot_turn_sparse_vad_positives_into_primary_speech(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probabilities = ([0.95] * 8 + [0.0] * 5) * 8
    segments, confirmed_speech_duration_ms = pipeline._aggregate_probability_evidence(
        probabilities,
        duration_ms=10_000,
    )
    assert sum(end - start for start, end in segments) == 3_008
    assert confirmed_speech_duration_ms == 2_048

    analyzer, asr = _analyzer(
        tmp_path,
        monkeypatch,
        duration_ms=10_000,
        segments=segments,
        transcript="多段稀疏路人声不应上传。",
        confirmed_speech_duration_ms=confirmed_speech_duration_ms,
    )

    with pytest.raises(
        MaterialSpeechRejected,
        match=r"^material speech analysis rejected$",
    ):
        analyzer.analyze(_facts(10_000))

    assert asr.calls == []


@pytest.mark.parametrize(
    ("duration_ms", "segments"),
    [
        (10_000, ((4_000, 7_000),)),
        (60_000, ((20_000, 25_000),)),
    ],
    ids=["thirty-percent-coverage", "five-seconds-total"],
)
def test_either_primary_speech_threshold_still_enters_asr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    duration_ms: int,
    segments: tuple[tuple[int, int], ...],
) -> None:
    analyzer, asr = _analyzer(
        tmp_path,
        monkeypatch,
        duration_ms=duration_ms,
        segments=segments,
        transcript="这是主体人声。",
    )

    result = analyzer.analyze(_facts(duration_ms))

    assert result == MaterialSpeechAnalysis(True, segments, "这是主体人声。")
    assert len(asr.calls) == 1


def _wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(16_000)
        destination.writeframes(b"\x01\x00" * 1_600)
    return output.getvalue()
