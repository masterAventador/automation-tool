"""LE-14 T3: only bounded extracted audio may cross the ASR boundary."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import time
import wave
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, cast

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
        assert kwargs["stdin"] is subprocess.DEVNULL
        assert kwargs["stdout"] is subprocess.DEVNULL
        assert kwargs["stderr"] is subprocess.DEVNULL
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
        assert timeout is not None
        raise subprocess.TimeoutExpired(self.argv, timeout)

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

    monkeypatch.setattr(subprocess, "Popen", start)
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
    monkeypatch.setattr(subprocess, "Popen", FinishedExtraction)
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
    monkeypatch.setattr(subprocess, "Popen", FinishedExtraction)
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
    monkeypatch.setattr(subprocess, "Popen", FinishedExtraction)
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
    monkeypatch.setattr(os, "name", "nt")

    assert pipeline._same_pcm_file(
        cast(os.stat_result, descriptor),
        cast(os.stat_result, path),
    )


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

    monkeypatch.setattr(os, "O_BINARY", binary_flag, raising=False)
    monkeypatch.setattr(os, "open", open_without_test_flag)

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

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(subprocess, "Popen", ExactLimitExtraction)
        pipeline._extract_pcm(
            tmp_path / "ffmpeg",
            tmp_path / "source.mp4",
            output,
            duration_ms=1,
        )

    assert output.stat().st_size == limit


def test_output_at_limit_plus_one_is_killed_and_reaped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limit = pipeline._pcm_output_limit(1)
    RunningExtraction.instances.clear()
    monkeypatch.setattr(RunningExtraction, "payload", b"\x00" * (limit + 1))
    monkeypatch.setattr(subprocess, "Popen", RunningExtraction)

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

    monkeypatch.setattr(subprocess, "Popen", ReplacingExtraction)

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
    monkeypatch.setattr(subprocess, "Popen", RunningExtraction)
    moments = iter((0.0, 31.0))
    monkeypatch.setattr(time, "monotonic", lambda: next(moments))

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
    monkeypatch.setattr(subprocess, "Popen", FinishedExtraction)
    monkeypatch.setattr(
        shutil,
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


def _analyzer_arguments(tmp_path: Path) -> dict[str, object]:
    source = tmp_path / "private-source.mp4"
    source.write_bytes(PRIVATE_VIDEO_BYTES)
    resolved, approved = approve_source(source)
    return {
        "tools": _tools(tmp_path),
        "source": resolved,
        "approved": approved,
        "vad_factory": lambda: SequencedVad([0.0]),
        "asr_adapter": RecordingAsr(),
    }


def test_both_analyzers_refuse_collaborators_they_cannot_use(tmp_path: Path) -> None:
    """Each of these is used to reach the operator's file or a paid model call."""
    complete = _analyzer_arguments(tmp_path)

    cases: list[tuple[str, dict[str, object]]] = [
        ("tools that are not the packaged pair", {"tools": object()}),
        ("a source that is not a path", {"source": str(complete["source"])}),
        ("an approval that is not a stat", {"approved": (0, 0)}),
        ("a vad factory that cannot be called", {"vad_factory": None}),
        ("an asr adapter of the wrong type", {"asr_adapter": object()}),
    ]
    for label, overrides in cases:
        arguments = {**complete, **overrides}
        for builder in (LocalAudibleSpeechAnalyzer, LocalAudibleSpeechAnalyzerFactory):
            with pytest.raises(MaterialSpeechRejected):
                builder(**arguments)  # type: ignore[arg-type]
        assert label


def test_analysis_refuses_facts_that_describe_nothing_to_listen_to(
    tmp_path: Path,
) -> None:
    """No audio track, or no length: there is nothing to extract and nothing to bill."""
    analyzer = LocalAudibleSpeechAnalyzer(**_analyzer_arguments(tmp_path))  # type: ignore[arg-type]

    cases: list[tuple[str, object]] = [
        ("something that is not probe facts", object()),
        ("facts that declare no audio", replace(_facts(), has_audio=False)),
        ("a duration that is not an int", replace(_facts(), duration_ms=cast(int, 640.0))),
        ("a duration of zero", replace(_facts(), duration_ms=0)),
    ]
    for label, facts in cases:
        with pytest.raises(MaterialSpeechRejected):
            analyzer.analyze(cast(MaterialFacts, facts))
        assert label


def test_a_scratch_directory_that_cannot_be_created_stops_the_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The extracted PCM is a copy of the operator's audio; with nowhere private
    to put it the run does not start."""
    analyzer = LocalAudibleSpeechAnalyzer(**_analyzer_arguments(tmp_path))  # type: ignore[arg-type]

    def refuse(*_args: object, **_options: object) -> str:
        raise OSError("no space left on device")

    monkeypatch.setattr(tempfile, "mkdtemp", refuse)

    with pytest.raises(MaterialSpeechRejected):
        analyzer.analyze(_facts())


def test_a_vad_factory_answering_with_the_wrong_thing_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The factory is lazy on purpose; what it builds is still checked."""
    arguments = _analyzer_arguments(tmp_path)
    arguments["vad_factory"] = lambda: object()
    analyzer = LocalAudibleSpeechAnalyzer(**arguments)  # type: ignore[arg-type]
    monkeypatch.setattr(subprocess, "Popen", FinishedExtraction)

    with pytest.raises(MaterialSpeechRejected):
        analyzer.analyze(_facts())


class _ExtractionVariant(FinishedExtraction):
    """A finished ffmpeg whose product is wrong in one specific way."""

    payload: ClassVar[bytes] = (1_000).to_bytes(2, "little", signed=True) * 20 * 512
    exit_code: ClassVar[int] = 0

    def __init__(self, argv: list[str], **kwargs: object) -> None:
        del kwargs
        self.argv = argv
        self.returncode = self.exit_code
        Path(argv[-1]).write_bytes(self.payload)


def test_an_extraction_whose_product_is_unusable_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit code alone proves nothing; the file it left behind is measured."""

    class _Failed(_ExtractionVariant):
        exit_code = 1

    class _Empty(_ExtractionVariant):
        payload = b""

    class _OddBytes(_ExtractionVariant):
        payload = b"\x00" * 1_025

    for label, variant in [
        ("a non-zero exit", _Failed),
        ("an empty file", _Empty),
        ("a half sample at the end", _OddBytes),
    ]:
        analyzer = LocalAudibleSpeechAnalyzer(**_analyzer_arguments(tmp_path))  # type: ignore[arg-type]
        monkeypatch.setattr(subprocess, "Popen", variant)
        with pytest.raises(MaterialSpeechRejected):
            analyzer.analyze(_facts())
        monkeypatch.undo()
        assert label


def test_an_extraction_that_cannot_be_started_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analyzer = LocalAudibleSpeechAnalyzer(**_analyzer_arguments(tmp_path))  # type: ignore[arg-type]

    def refuse(*_args: object, **_options: object) -> object:
        raise OSError("exec format error")

    monkeypatch.setattr(subprocess, "Popen", refuse)

    with pytest.raises(MaterialSpeechRejected):
        analyzer.analyze(_facts())


def test_an_output_name_something_already_holds_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opened O_EXCL: the extractor writes a name nothing else may already own."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    analyzer = LocalAudibleSpeechAnalyzer(**_analyzer_arguments(tmp_path))  # type: ignore[arg-type]

    def planting_mkdtemp(*_args: object, **_options: object) -> str:
        for name in ("audio.pcm", "speech.pcm", "extracted.pcm"):
            (scratch / name).write_bytes(b"squatting")
        return os.fspath(scratch)

    monkeypatch.setattr(tempfile, "mkdtemp", planting_mkdtemp)
    monkeypatch.setattr(subprocess, "Popen", FinishedExtraction)

    with pytest.raises(MaterialSpeechRejected):
        analyzer.analyze(_facts())


def test_an_extraction_that_outlives_its_budget_is_killed_and_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model that will not finish is stopped rather than waited on forever."""
    RunningExtraction.instances.clear()
    analyzer = LocalAudibleSpeechAnalyzer(**_analyzer_arguments(tmp_path))  # type: ignore[arg-type]
    monkeypatch.setattr(subprocess, "Popen", RunningExtraction)
    monkeypatch.setattr(pipeline, "_extraction_timeout_seconds", lambda _duration: 0.0)

    with pytest.raises(MaterialSpeechRejected):
        analyzer.analyze(_facts())

    assert RunningExtraction.instances
    assert RunningExtraction.instances[0].kill_calls >= 1


def test_a_reap_that_does_not_finish_kills_a_second_time(tmp_path: Path) -> None:
    """The first kill can land on a process already wedged; the guard says so."""

    class _Unreapable:
        def __init__(self) -> None:
            self.kill_calls = 0
            self.wait_calls = 0

        def poll(self) -> int | None:
            return None

        def kill(self) -> None:
            self.kill_calls += 1

        def wait(self, timeout: float | None = None) -> int:
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise subprocess.TimeoutExpired("ffmpeg", timeout or 0.0)
            return -9

    process = _Unreapable()
    pipeline._kill_and_reap(cast(subprocess.Popen[bytes], process))

    assert process.kill_calls == 2, "one kill before the reap, one after it timed out"


def test_a_reap_that_never_finishes_still_returns(tmp_path: Path) -> None:
    """Both waits timing out must not leave the caller hanging on cleanup."""

    class _NeverReaped:
        def poll(self) -> int | None:
            return None

        def kill(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            raise subprocess.TimeoutExpired("ffmpeg", timeout or 0.0)

    pipeline._kill_and_reap(cast(subprocess.Popen[bytes], _NeverReaped()))


def test_only_a_real_regular_file_has_metadata_worth_comparing(tmp_path: Path) -> None:
    """A link or a directory at the output name is not the file that was opened."""
    real = tmp_path / "real.pcm"
    real.write_bytes(b"\x00\x00")
    directory = tmp_path / "a-directory"
    directory.mkdir()
    link = tmp_path / "link.pcm"
    link.symlink_to(real)

    assert pipeline._ordinary_file_metadata(real) is not None
    assert pipeline._ordinary_file_metadata(tmp_path / "absent.pcm") is None
    assert pipeline._ordinary_file_metadata(directory) is None
    assert pipeline._ordinary_file_metadata(link) is None


def _pcm(tmp_path: Path, payload: bytes, name: str = "audio.pcm") -> Path:
    path = tmp_path / name
    path.write_bytes(payload)
    return path


def test_detection_stops_at_the_end_of_the_pcm_it_was_given(tmp_path: Path) -> None:
    """The declared duration is what ffmpeg promised; the file is what it delivered."""
    one_chunk = (1_000).to_bytes(2, "little", signed=True) * 512
    path = _pcm(tmp_path, one_chunk)
    vad = SequencedVad([0.9] * 4)

    segments, speech_ms, pcm_bytes = pipeline._detect_speech_segments(path, vad, duration_ms=10_000)

    assert pcm_bytes == len(one_chunk)
    assert vad.calls == 1
    assert segments == () or speech_ms >= 0


def test_a_final_partial_chunk_is_padded_rather_than_dropped(tmp_path: Path) -> None:
    """The tail of the audio still gets a reading; silence is padded in, not skipped."""
    payload = (1_000).to_bytes(2, "little", signed=True) * (512 + 100)
    path = _pcm(tmp_path, payload)
    vad = SequencedVad([0.9, 0.9])

    _segments, _speech_ms, pcm_bytes = pipeline._detect_speech_segments(
        path, vad, duration_ms=10_000
    )

    assert pcm_bytes == len(payload)
    assert vad.calls == 2, "the short tail is measured too"


def test_pcm_that_ends_mid_sample_is_refused(tmp_path: Path) -> None:
    """Two bytes per sample; an odd byte count means the file is not what it claims."""
    path = _pcm(tmp_path, b"\x00" * 1_025)

    with pytest.raises(MaterialSpeechRejected):
        pipeline._detect_speech_segments(path, SequencedVad([0.0]), duration_ms=10_000)


def test_a_vad_answering_outside_zero_to_one_is_refused(tmp_path: Path) -> None:
    """The number is compared against a decided threshold; anything else is not one."""
    payload = (1_000).to_bytes(2, "little", signed=True) * 512

    class _Answering:
        def __init__(self, value: object) -> None:
            self._value = value

        def probability(self, _samples: object, *, sample_rate_hz: object) -> float:
            del sample_rate_hz
            return cast(float, self._value)

    for label, value in [
        ("an int rather than a float", 1),
        ("not a number at all", "0.9"),
        ("a value above one", 1.5),
        ("a value below zero", -0.1),
        ("a value that is not finite", float("nan")),
    ]:
        with pytest.raises(MaterialSpeechRejected):
            pipeline._detect_speech_segments(
                _pcm(tmp_path, payload, f"audio-{abs(hash(label))}.pcm"),
                cast(Any, _Answering(value)),
                duration_ms=10_000,
            )
        assert label


def test_a_vad_that_raises_is_reported_as_one_closed_reason(tmp_path: Path) -> None:
    payload = (1_000).to_bytes(2, "little", signed=True) * 512

    class _Exploding:
        def probability(self, _samples: object, *, sample_rate_hz: object) -> float:
            del sample_rate_hz
            raise TypeError("analyzer defect")

    with pytest.raises(MaterialSpeechRejected):
        pipeline._detect_speech_segments(
            _pcm(tmp_path, payload), cast(Any, _Exploding()), duration_ms=10_000
        )


def test_a_pcm_file_the_opener_cannot_trust_is_refused(tmp_path: Path) -> None:
    """The extracted audio is opened once and everything after reads that descriptor."""
    real = tmp_path / "audio.pcm"
    real.write_bytes(b"\x00\x00" * 512)
    link = tmp_path / "link.pcm"
    link.symlink_to(real)
    directory = tmp_path / "a-directory"
    directory.mkdir()

    for label, path in [
        ("nothing at that name", tmp_path / "absent.pcm"),
        ("a symlink", link),
        ("a directory", directory),
    ]:
        with pytest.raises(MaterialSpeechRejected):
            pipeline._open_stable_pcm(path, approved=None)
        assert label


def test_a_pcm_file_that_is_not_the_approved_one_is_refused(tmp_path: Path) -> None:
    """Same name, different file: the extraction's own product is the only one read."""
    first = tmp_path / "audio.pcm"
    first.write_bytes(b"\x00\x00" * 512)
    approved = first.stat()
    first.unlink()
    first.write_bytes(b"\x01\x01" * 512)

    with pytest.raises(MaterialSpeechRejected):
        pipeline._open_stable_pcm(first, approved=approved)


def test_a_pcm_file_replaced_while_it_is_read_is_refused(tmp_path: Path) -> None:
    """Stability is re-checked after the read, not assumed from the open."""
    path = tmp_path / "audio.pcm"
    path.write_bytes(b"\x00\x00" * 512)
    descriptor, before = pipeline._open_stable_pcm(path, approved=None)
    try:
        path.unlink()
        path.write_bytes(b"\x01\x01" * 512)
        with pytest.raises(MaterialSpeechRejected):
            pipeline._require_stable_pcm(path, descriptor, before)
    finally:
        os.close(descriptor)


def test_a_stability_check_on_a_closed_descriptor_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "audio.pcm"
    path.write_bytes(b"\x00\x00" * 512)
    descriptor, before = pipeline._open_stable_pcm(path, approved=None)
    os.close(descriptor)

    with pytest.raises(MaterialSpeechRejected):
        pipeline._require_stable_pcm(path, descriptor, before)


def test_neighbouring_speech_windows_are_merged_rather_than_listed_twice() -> None:
    """Two runs that touch describe one stretch of speech, not two."""
    segments: list[tuple[int, int]] = []
    pipeline._append_segment(segments, start_ms=0, end_ms=400, duration_ms=10_000)
    pipeline._append_segment(segments, start_ms=380, end_ms=900, duration_ms=10_000)

    assert segments == [(0, 900)]


def test_a_window_that_falls_outside_the_material_is_dropped() -> None:
    """Padding can push a window past either end; what is left may be nothing."""
    segments: list[tuple[int, int]] = []
    pipeline._append_segment(segments, start_ms=-200, end_ms=-50, duration_ms=10_000)
    pipeline._append_segment(segments, start_ms=10_500, end_ms=11_000, duration_ms=10_000)

    assert segments == []


def test_an_asr_batch_that_is_not_one_whole_wav_is_refused() -> None:
    """This is what crosses the paid boundary; nothing else may."""
    valid = io.BytesIO()
    with wave.open(valid, "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(16_000)
        destination.writeframes(b"\x00\x00" * 16_000)
    payload = valid.getvalue()

    cases: list[tuple[str, dict[str, object]]] = [
        ("bytes that are not bytes", {"wav_bytes": bytearray(payload)}),
        ("a header shorter than a wav header", {"wav_bytes": payload[:43]}),
        ("no RIFF marker", {"wav_bytes": b"FFIR" + payload[4:]}),
        ("no WAVE marker", {"wav_bytes": payload[:8] + b"EVAW" + payload[12:]}),
        ("a duration that is not an int", {"duration_ms": 1_000.0}),
        ("a duration of zero", {"duration_ms": 0}),
        ("a duration past the batch ceiling", {"duration_ms": 10**9}),
    ]
    for label, overrides in cases:
        arguments: dict[str, object] = {"wav_bytes": payload, "duration_ms": 1_000}
        arguments.update(overrides)
        with pytest.raises(MaterialSpeechRejected):
            SpeechAudioBatch(**arguments)  # type: ignore[arg-type]
        assert label


def test_more_speech_windows_than_the_model_may_carry_are_refused() -> None:
    """The segment list is persisted onto the material, which bounds how many it holds."""
    # A confirmed run needs `MIN_SPEECH_CHUNKS` consecutive readings, and the
    # silence between two runs has to outlast the padding on both sides or the
    # two get merged into one window. 8 speech chunks then 8 silent ones does
    # both, repeated past the ceiling.
    from automation_tool.control_plane.domain.material import MAX_SPEECH_SEGMENTS

    alternating = ([0.9] * 8 + [0.0] * 8) * (MAX_SPEECH_SEGMENTS + 1)

    with pytest.raises(MaterialSpeechRejected):
        pipeline._aggregate_probability_evidence(alternating, duration_ms=10_000_000)


def test_a_batch_reader_that_cannot_read_what_it_asked_for_is_refused(
    tmp_path: Path,
) -> None:
    """The window was computed from the declared byte count; a short read means drift."""
    path = tmp_path / "audio.pcm"
    path.write_bytes(b"\x00\x00" * 512)

    batches = pipeline._speech_audio_batches(
        path,
        segments=((0, 5_000),),
        # A byte count larger than the file: the window it computes runs past
        # the end, so the read comes back short.
        pcm_bytes=10 * 1_024 * 1_024,
        duration_ms=10_000,
        approved=None,
    )

    with pytest.raises(MaterialSpeechRejected):
        list(batches)


def test_a_batch_reader_that_produced_nothing_is_refused(tmp_path: Path) -> None:
    """Speech was confirmed, so something has to reach the model; nothing is a defect."""
    path = tmp_path / "audio.pcm"
    path.write_bytes(b"\x00\x00" * 512)

    batches = pipeline._speech_audio_batches(
        path,
        # A window that overlaps no confirmed segment: every batch is skipped.
        segments=((9_000, 9_500),),
        pcm_bytes=1_024,
        duration_ms=1_000,
        approved=None,
    )

    with pytest.raises(MaterialSpeechRejected):
        list(batches)


def test_a_wav_larger_than_the_batch_ceiling_is_refused() -> None:
    """The ceiling is what the paid boundary accepts; a bigger one is not sent."""
    oversize = b"\x00\x00" * (pipeline.MAX_ASR_WAV_BYTES // 2 + 1)

    with pytest.raises(MaterialSpeechRejected):
        pipeline._pcm_wav(oversize)


def test_reading_exactly_stops_when_the_file_does(tmp_path: Path) -> None:
    """A short file is not padded and not retried; the caller compares the length."""
    path = tmp_path / "audio.pcm"
    path.write_bytes(b"\x00\x01\x02\x03")
    descriptor = os.open(path, os.O_RDONLY)
    try:
        assert pipeline._read_exact(descriptor, 4) == b"\x00\x01\x02\x03"
        os.lseek(descriptor, 0, os.SEEK_SET)
        assert pipeline._read_exact(descriptor, 100) == b"\x00\x01\x02\x03"
    finally:
        os.close(descriptor)


def test_the_pcm_identity_time_follows_the_platform_that_records_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows has a creation time and POSIX does not; each reads its own field.

    Only one of these is reachable on a given host, so the platform value is
    supplied and the field selection is what gets asserted.
    """
    path = tmp_path / "audio.pcm"
    path.write_bytes(b"\x00\x00")
    metadata = path.stat()

    monkeypatch.setattr(os, "name", "posix")
    assert pipeline._pcm_identity_time_ns(metadata) == metadata.st_ctime_ns

    monkeypatch.setattr(os, "name", "nt")
    expected = getattr(metadata, "st_birthtime_ns", metadata.st_ctime_ns)
    assert pipeline._pcm_identity_time_ns(metadata) == expected


def test_a_pcm_path_that_cannot_be_opened_at_all_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`os.open` succeeding and `os.fstat` failing leaves a descriptor to close."""
    path = tmp_path / "audio.pcm"
    path.write_bytes(b"\x00\x00" * 512)
    real_fstat = os.fstat

    def refusing_fstat(descriptor: int) -> os.stat_result:
        del descriptor
        raise OSError("bad file descriptor")

    monkeypatch.setattr(os, "fstat", refusing_fstat)
    with pytest.raises(MaterialSpeechRejected):
        pipeline._open_stable_pcm(path, approved=None)
    monkeypatch.setattr(os, "fstat", real_fstat)


def test_a_batch_reader_that_cannot_seek_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Anything the driver raises mid-read becomes one closed reason."""
    path = tmp_path / "audio.pcm"
    path.write_bytes(b"\x00\x00" * 512)

    def refusing_lseek(*_args: object, **_options: object) -> int:
        raise OSError("illegal seek")

    monkeypatch.setattr(os, "lseek", refusing_lseek)

    batches = pipeline._speech_audio_batches(
        path,
        segments=((0, 500),),
        pcm_bytes=1_024,
        duration_ms=1_000,
        approved=None,
    )

    with pytest.raises(MaterialSpeechRejected):
        list(batches)


def test_an_output_the_extractor_cannot_create_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`os.open` failing outright leaves no descriptor to close on the way out."""
    analyzer = LocalAudibleSpeechAnalyzer(**_analyzer_arguments(tmp_path))  # type: ignore[arg-type]
    real_open = os.open

    def refusing_open(path: str | os.PathLike[str], flags: int, mode: int = 0o777) -> int:
        if os.fspath(path).endswith(pipeline._PCM_FILENAME):
            raise OSError("permission denied")
        return real_open(path, flags, mode)

    monkeypatch.setattr(os, "open", refusing_open)
    monkeypatch.setattr(subprocess, "Popen", FinishedExtraction)

    with pytest.raises(MaterialSpeechRejected):
        analyzer.analyze(_facts())


def test_detection_and_batching_close_nothing_when_nothing_opened(tmp_path: Path) -> None:
    """The cleanup runs on every exit; with no descriptor there is nothing to close."""
    absent = tmp_path / "absent.pcm"

    with pytest.raises(MaterialSpeechRejected):
        pipeline._detect_speech_segments(absent, SequencedVad([0.0]), duration_ms=1_000)

    batches = pipeline._speech_audio_batches(
        absent,
        segments=((0, 500),),
        pcm_bytes=1_024,
        duration_ms=1_000,
        approved=None,
    )
    with pytest.raises(MaterialSpeechRejected):
        list(batches)


def test_a_run_that_never_confirmed_leaves_no_segment_behind() -> None:
    """Short bursts of noise reach the silence threshold without confirming anything."""
    too_short_to_confirm = ([0.9] * 2 + [0.0] * 12) * 3

    segments, speech_ms = pipeline._aggregate_probability_evidence(
        too_short_to_confirm, duration_ms=10_000
    )

    assert segments == ()
    assert speech_ms == 0


def test_an_output_whose_identity_cannot_be_read_is_refused_before_ffmpeg_runs(
    tmp_path: Path,
) -> None:
    """That stat is what pins the file ffmpeg is about to fill; without it there is
    nothing to compare the finished output against."""
    started: list[object] = []

    def never_started(*args: object, **kwargs: object) -> object:
        started.append(args)
        raise AssertionError("ffmpeg must not start without a pinned output")

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(subprocess, "Popen", never_started)
        monkeypatch.setattr(os, "fstat", _raise_io_error)
        with pytest.raises(MaterialSpeechRejected):
            pipeline._extract_pcm(
                tmp_path / "ffmpeg",
                tmp_path / "source.mp4",
                tmp_path / "audio.pcm",
                duration_ms=1,
            )

    assert started == []


def _raise_io_error(descriptor: int) -> os.stat_result:
    raise OSError("private stat failure")
