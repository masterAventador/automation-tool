"""LE-14 T3: only bounded extracted audio may cross the ASR boundary."""

from __future__ import annotations

import io
import os
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from automation_tool.executor import material_speech_pipeline as pipeline
from automation_tool.executor.material_probe import (
    MaterialFacts,
    PackagedMediaTools,
    ProbedMaterialKind,
    approve_source,
)
from automation_tool.executor.material_speech_analysis import (
    MaterialSpeechAnalysis,
    MaterialSpeechRejected,
    analyze_material_speech,
)
from automation_tool.executor.material_speech_pipeline import (
    LocalAudibleSpeechAnalyzer,
    LocalAudibleSpeechAnalyzerFactory,
    SpeechAudioBatch,
)

PRIVATE_VIDEO_BYTES = b"private-video-container-must-never-cross-asr"


class SequencedVad:
    def __init__(self, probabilities: list[float]) -> None:
        self._probabilities = iter(probabilities)
        self.calls = 0

    def probability(self, samples: object, *, sample_rate_hz: object) -> float:
        assert isinstance(samples, tuple)
        assert len(samples) == 512
        assert sample_rate_hz == 16_000
        self.calls += 1
        return next(self._probabilities)


class RecordingAsr:
    def __init__(self) -> None:
        self.calls: list[SpeechAudioBatch] = []

    def transcribe(self, audio: SpeechAudioBatch) -> str:
        self.calls.append(audio)
        return "这段人声只从音轨转写。"


class FinishedExtraction:
    def __init__(self, argv: list[str], **kwargs: object) -> None:
        assert kwargs["stdin"] is pipeline.subprocess.DEVNULL
        assert kwargs["stdout"] is pipeline.subprocess.DEVNULL
        assert kwargs["stderr"] is pipeline.subprocess.DEVNULL
        self.argv = argv
        self.returncode = 0
        output = Path(argv[-1])
        output.write_bytes((1_000).to_bytes(2, "little", signed=True) * 20 * 512)

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


class RunningExtraction:
    payload = b"\x00\x00"
    instances: ClassVar[list[RunningExtraction]] = []

    def __init__(self, argv: list[str], **kwargs: object) -> None:
        del kwargs
        self.argv = argv
        self.returncode: int | None = None
        self.kill_calls = 0
        self.wait_calls = 0
        Path(argv[-1]).write_bytes(self.payload)
        self.instances.append(self)

    def __enter__(self) -> RunningExtraction:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        if self.returncode is not None:
            return self.returncode
        raise pipeline.subprocess.TimeoutExpired(self.argv, timeout)

    def poll(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9


def _facts() -> MaterialFacts:
    return MaterialFacts(
        kind=ProbedMaterialKind.VIDEO,
        duration_ms=640,
        width=640,
        height=360,
        video_codec="h264",
        audio_codec="aac",
        has_audio=True,
        audio_loudness_lufs=-20.0,
        content_digest="a" * 64,
    )


def _tools(tmp_path: Path) -> PackagedMediaTools:
    ffmpeg = tmp_path / "ffmpeg"
    ffprobe = tmp_path / "ffprobe"
    for tool in (ffmpeg, ffprobe):
        tool.write_bytes(b"packaged tool")
        tool.chmod(0o700)
    return PackagedMediaTools(ffprobe_path=ffprobe, ffmpeg_path=ffmpeg)


def test_only_extracted_wav_audio_crosses_the_asr_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "private-source.mp4"
    source.write_bytes(PRIVATE_VIDEO_BYTES)
    source, approved = approve_source(source)
    started: list[FinishedExtraction] = []

    def start(argv: list[str], **kwargs: object) -> FinishedExtraction:
        process = FinishedExtraction(argv, **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr(pipeline.subprocess, "Popen", start)
    vad = SequencedVad([0.9] * 10 + [0.0] * 10)
    asr = RecordingAsr()
    analyzer = LocalAudibleSpeechAnalyzer(
        tools=_tools(tmp_path),
        source=source,
        approved=approved,
        vad_factory=lambda: vad,
        asr_adapter=asr,
    )

    result = analyzer.analyze(_facts())

    assert result == MaterialSpeechAnalysis(
        has_speech=True,
        speech_segments_ms=((0, 384),),
        speech_transcript="这段人声只从音轨转写。",
    )
    assert len(started) == 1
    argv = started[0].argv
    assert argv[argv.index("-map") + 1] == "0:a:0"
    assert "-vn" in argv
    assert "-sn" in argv
    assert "-dn" in argv
    assert len(asr.calls) == 1
    audio = asr.calls[0]
    assert audio.wav_bytes.startswith(b"RIFF")
    assert b"WAVE" in audio.wav_bytes[:16]
    assert PRIVATE_VIDEO_BYTES not in audio.wav_bytes
    assert os.fspath(source).encode() not in audio.wav_bytes
    assert not hasattr(audio, "source")
    assert not hasattr(audio, "path")


def test_asr_batch_rejects_a_fake_header_and_a_duration_mismatch() -> None:
    with pytest.raises(MaterialSpeechRejected):
        SpeechAudioBatch(
            wav_bytes=b"RIFF" + b"\0" * 4 + b"WAVE" + b"\0" * 32,
            duration_ms=100,
        )

    output = io.BytesIO()
    with wave.open(output, "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(16_000)
        destination.writeframes(b"\0\0" * 1_600)
    with pytest.raises(MaterialSpeechRejected):
        SpeechAudioBatch(wav_bytes=output.getvalue(), duration_ms=99)


def test_asr_batch_canonicalizes_wav_and_discards_trailing_private_bytes() -> None:
    output = io.BytesIO()
    with wave.open(output, "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(16_000)
        destination.writeframes(b"\0\0" * 1_600)
    canonical = output.getvalue()

    batch = SpeechAudioBatch(
        wav_bytes=canonical + PRIVATE_VIDEO_BYTES,
        duration_ms=100,
    )

    assert batch.wav_bytes == canonical
    assert PRIVATE_VIDEO_BYTES not in batch.wav_bytes


def test_the_t1_lazy_factory_constructs_the_concrete_lower_funnel_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "factory-source.mp4"
    source.write_bytes(PRIVATE_VIDEO_BYTES)
    source, approved = approve_source(source)
    monkeypatch.setattr(pipeline.subprocess, "Popen", FinishedExtraction)
    vad = SequencedVad([0.9] * 10 + [0.0] * 10)
    asr = RecordingAsr()
    factory = LocalAudibleSpeechAnalyzerFactory(
        tools=_tools(tmp_path),
        source=source,
        approved=approved,
        vad_factory=lambda: vad,
        asr_adapter=asr,
    )

    result = analyze_material_speech(
        _facts(),
        audible_analyzer_factory=factory,
    )

    assert result.has_speech is True
    assert factory.calls == 1
    assert len(asr.calls) == 1


@pytest.mark.parametrize(
    ("probabilities", "duration_ms", "expected"),
    [
        ([0.9] * 7 + [0.0] * 4, 352, ()),
        ([0.9] * 4 + [0.0] * 3 + [0.9] * 4 + [0.0] * 4, 480, ()),
        ([0.9] * 8 + [0.0] * 4, 384, ((0, 320),)),
        ([0.0] * 3 + [0.9] * 8, 352, ((32, 352),)),
        (
            [0.9] * 8 + [0.0] * 3 + [0.9] * 8 + [0.0] * 4,
            736,
            ((0, 672),),
        ),
    ],
    ids=[
        "seven-frames-are-too-short",
        "short-silence-does-not-make-high-frames-consecutive",
        "eight-frames-are-speech",
        "tail-is-clamped",
        "three-silent-frames-do-not-split",
    ],
)
def test_vad_probability_aggregation_is_locked_to_the_decided_thresholds(
    probabilities: list[float],
    duration_ms: int,
    expected: tuple[tuple[int, int], ...],
) -> None:
    assert pipeline._aggregate_probabilities(probabilities, duration_ms=duration_ms) == expected


def test_speech_chunks_must_be_consecutive_before_a_segment_is_confirmed() -> None:
    assert (
        pipeline._aggregate_probabilities(
            [0.9] * 7 + [0.0] + [0.9] + [0.0] * 4,
            duration_ms=416,
        )
        == ()
    )


def test_an_unconfirmed_run_does_not_extend_the_next_confirmed_segment() -> None:
    assert pipeline._aggregate_probabilities(
        [0.9] * 7 + [0.0] + [0.9] * 8 + [0.0] * 4,
        duration_ms=640,
    ) == ((192, 576),)


def test_no_confirmed_speech_never_calls_asr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "ambient-only.mp4"
    source.write_bytes(PRIVATE_VIDEO_BYTES)
    source, approved = approve_source(source)
    monkeypatch.setattr(pipeline.subprocess, "Popen", FinishedExtraction)
    vad = SequencedVad([0.9] * 7 + [0.0] * 13)
    asr = RecordingAsr()

    result = LocalAudibleSpeechAnalyzer(
        tools=_tools(tmp_path),
        source=source,
        approved=approved,
        vad_factory=lambda: vad,
        asr_adapter=asr,
    ).analyze(_facts())

    assert result == MaterialSpeechAnalysis(False, (), None)
    assert asr.calls == []


def test_audio_is_split_at_180_seconds_and_only_windows_with_speech_are_built(
    tmp_path: Path,
) -> None:
    pcm = tmp_path / "long-audio.pcm"
    pcm_bytes = 361_000 * pipeline.PCM_BYTES_PER_MILLISECOND
    with pcm.open("wb") as stream:
        stream.truncate(pcm_bytes)

    batches = tuple(
        pipeline._speech_audio_batches(
            pcm,
            pcm_bytes=pcm_bytes,
            duration_ms=361_000,
            segments=((1_000, 2_000), (360_100, 360_900)),
        )
    )

    assert [batch.duration_ms for batch in batches] == [180_000, 1_000]
    assert all(len(batch.wav_bytes) <= pipeline.MAX_ASR_WAV_BYTES for batch in batches)
    assert all(batch.wav_bytes.startswith(b"RIFF") for batch in batches)


def test_audio_batches_are_rendered_lazily_instead_of_accumulated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pcm = tmp_path / "long-audio.pcm"
    pcm_bytes = 361_000 * pipeline.PCM_BYTES_PER_MILLISECOND
    with pcm.open("wb") as stream:
        stream.truncate(pcm_bytes)
    rendered_payload_sizes: list[int] = []

    class LightweightBatch:
        def __init__(self, *, wav_bytes: bytes, duration_ms: int) -> None:
            self.wav_bytes = wav_bytes
            self.duration_ms = duration_ms

    def render(payload: bytes) -> bytes:
        rendered_payload_sizes.append(len(payload))
        return b"bounded-wav"

    monkeypatch.setattr(pipeline, "_pcm_wav", render)
    monkeypatch.setattr(pipeline, "SpeechAudioBatch", LightweightBatch)

    batches = pipeline._speech_audio_batches(
        pcm,
        pcm_bytes=pcm_bytes,
        duration_ms=361_000,
        segments=((1_000, 2_000), (180_100, 180_900), (360_100, 360_900)),
    )

    assert rendered_payload_sizes == []
    iterator = iter(batches)
    assert next(iterator).duration_ms == 180_000
    assert len(rendered_payload_sizes) == 1
    assert next(iterator).duration_ms == 180_000
    assert len(rendered_payload_sizes) == 2
    assert next(iterator).duration_ms == 1_000
    assert len(rendered_payload_sizes) == 3
    with pytest.raises(StopIteration):
        next(iterator)


def test_transcription_limit_stops_before_a_third_asr_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "bounded-transcript-source.mp4"
    source.write_bytes(PRIVATE_VIDEO_BYTES)
    source, approved = approve_source(source)
    monkeypatch.setattr(pipeline.subprocess, "Popen", FinishedExtraction)
    wav = pipeline._pcm_wav(b"\0\0" * 16)
    batch = SpeechAudioBatch(wav_bytes=wav, duration_ms=1)
    monkeypatch.setattr(
        pipeline,
        "_speech_audio_batches",
        lambda *_args, **_kwargs: (item for item in (batch, batch, batch)),
    )

    class GrowingTranscriptAsr:
        def __init__(self) -> None:
            self.calls = 0

        def transcribe(self, audio: SpeechAudioBatch) -> str:
            assert audio is batch
            self.calls += 1
            if self.calls == 1:
                return "甲" * 60_000
            if self.calls == 2:
                return "乙" * 50_000
            raise AssertionError("the transcript limit must stop further requests")

    asr = GrowingTranscriptAsr()
    analyzer = LocalAudibleSpeechAnalyzer(
        tools=_tools(tmp_path),
        source=source,
        approved=approved,
        vad_factory=lambda: SequencedVad([0.9] * 10 + [0.0] * 10),
        asr_adapter=asr,
    )

    with pytest.raises(MaterialSpeechRejected):
        analyzer.analyze(_facts())

    assert asr.calls == 2


def test_a_linked_pcm_output_is_rejected_before_vad_reads_it(tmp_path: Path) -> None:
    target = tmp_path / "attacker-controlled.pcm"
    target.write_bytes((1_000).to_bytes(2, "little", signed=True) * 8 * 512)
    linked_output = tmp_path / "audio.pcm"
    linked_output.symlink_to(target)
    vad = SequencedVad([0.9] * 8)

    with pytest.raises(
        MaterialSpeechRejected,
        match=r"^material speech analysis rejected$",
    ):
        pipeline._detect_speech_segments(linked_output, vad, duration_ms=256)

    assert vad.calls == 0


def test_windows_pcm_identity_uses_stable_birth_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared = {
        "st_dev": 1,
        "st_ino": 2,
        "st_mode": 0o100666,
        "st_size": 32_000,
        "st_mtime_ns": 100,
        "st_birthtime_ns": 50,
    }
    descriptor = SimpleNamespace(**shared, st_ctime_ns=100)
    path = SimpleNamespace(**shared, st_ctime_ns=50)
    monkeypatch.setattr(pipeline.os, "name", "nt")

    assert pipeline._same_pcm_file(descriptor, path)


def test_pcm_reader_requests_binary_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pcm = tmp_path / "audio.pcm"
    pcm.write_bytes(b"\x00\x1a\x00\x00")
    approved = pcm.stat()
    binary_flag = 0x8000
    opened_with: list[int] = []
    native_open = os.open

    def open_without_test_flag(path: Path, flags: int) -> int:
        opened_with.append(flags)
        return native_open(path, flags & ~binary_flag)

    monkeypatch.setattr(pipeline.os, "O_BINARY", binary_flag, raising=False)
    monkeypatch.setattr(pipeline.os, "open", open_without_test_flag)

    descriptor, _metadata = pipeline._open_stable_pcm(pcm, approved=approved)
    os.close(descriptor)

    assert opened_with[0] & binary_flag


def test_output_at_the_exact_limit_is_accepted(tmp_path: Path) -> None:
    output = tmp_path / "audio.pcm"
    limit = pipeline._pcm_output_limit(1)

    class ExactLimitExtraction(FinishedExtraction):
        def __init__(self, argv: list[str], **kwargs: object) -> None:
            super().__init__(argv, **kwargs)
            Path(argv[-1]).write_bytes(b"\x00" * limit)

    original = pipeline.subprocess.Popen
    try:
        pipeline.subprocess.Popen = ExactLimitExtraction  # type: ignore[assignment]
        pipeline._extract_pcm(
            tmp_path / "ffmpeg",
            tmp_path / "source.mp4",
            output,
            duration_ms=1,
        )
    finally:
        pipeline.subprocess.Popen = original

    assert output.stat().st_size == limit


def test_output_at_limit_plus_one_is_killed_and_reaped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limit = pipeline._pcm_output_limit(1)
    RunningExtraction.instances.clear()
    monkeypatch.setattr(RunningExtraction, "payload", b"\x00" * (limit + 1))
    monkeypatch.setattr(pipeline.subprocess, "Popen", RunningExtraction)

    with pytest.raises(MaterialSpeechRejected):
        pipeline._extract_pcm(
            tmp_path / "ffmpeg",
            tmp_path / "source.mp4",
            tmp_path / "audio.pcm",
            duration_ms=1,
        )

    process = RunningExtraction.instances[0]
    assert process.kill_calls == 1
    assert process.wait_calls >= 2
    assert process.poll() == -9


def test_extraction_rejects_a_replaced_output_inode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ReplacingExtraction(FinishedExtraction):
        def __init__(self, argv: list[str], **kwargs: object) -> None:
            super().__init__(argv, **kwargs)
            output = Path(argv[-1])
            payload = output.read_bytes()
            output.unlink()
            output.write_bytes(payload)

    monkeypatch.setattr(pipeline.subprocess, "Popen", ReplacingExtraction)

    with pytest.raises(MaterialSpeechRejected):
        pipeline._extract_pcm(
            tmp_path / "ffmpeg",
            tmp_path / "source.mp4",
            tmp_path / "audio.pcm",
            duration_ms=640,
        )


def test_timeout_kills_and_reaps_the_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    RunningExtraction.instances.clear()
    monkeypatch.setattr(RunningExtraction, "payload", b"\x00\x00")
    monkeypatch.setattr(pipeline.subprocess, "Popen", RunningExtraction)
    moments = iter((0.0, 31.0))
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: next(moments))

    with pytest.raises(MaterialSpeechRejected):
        pipeline._extract_pcm(
            tmp_path / "ffmpeg",
            tmp_path / "source.mp4",
            tmp_path / "audio.pcm",
            duration_ms=1,
        )

    process = RunningExtraction.instances[0]
    assert process.kill_calls == 1
    assert process.wait_calls >= 1
    assert process.poll() == -9


def test_cleanup_failure_cannot_replace_a_successful_business_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "cleanup-source.mp4"
    source.write_bytes(PRIVATE_VIDEO_BYTES)
    source, approved = approve_source(source)
    monkeypatch.setattr(pipeline.subprocess, "Popen", FinishedExtraction)
    monkeypatch.setattr(
        pipeline.shutil,
        "rmtree",
        lambda _path: (_ for _ in ()).throw(OSError("cleanup failed")),
    )

    result = LocalAudibleSpeechAnalyzer(
        tools=_tools(tmp_path),
        source=source,
        approved=approved,
        vad_factory=lambda: SequencedVad([0.9] * 10 + [0.0] * 10),
        asr_adapter=RecordingAsr(),
    ).analyze(_facts())

    assert result.speech_transcript == "这段人声只从音轨转写。"
