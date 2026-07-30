"""Bounded local PCM extraction, VAD aggregation and path-free ASR batches."""

from __future__ import annotations

import io
import math
import os
import shutil
import stat
import subprocess
import tempfile
import time
import wave
from collections.abc import Callable, Generator
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, NoReturn, Protocol, runtime_checkable

import numpy as np

from automation_tool.executor.material_probe import (
    MaterialFacts,
    PackagedMediaTools,
    require_source_unchanged,
)
from automation_tool.executor.material_speech_analysis import (
    MAX_SPEECH_SEGMENTS,
    MAX_TRANSCRIPT_CHARACTERS,
    MaterialSpeechAnalysis,
    MaterialSpeechRejected,
)
from automation_tool.executor.silero_vad import CHUNK_SAMPLES, SAMPLE_RATE_HZ

PCM_BYTES_PER_SAMPLE: Final = 2
PCM_BYTES_PER_MILLISECOND: Final = SAMPLE_RATE_HZ * PCM_BYTES_PER_SAMPLE // 1_000
VAD_CHUNK_MILLISECONDS: Final = CHUNK_SAMPLES * 1_000 // SAMPLE_RATE_HZ
VAD_THRESHOLD: Final = 0.5
MIN_SPEECH_CHUNKS: Final = 8
MIN_SILENCE_CHUNKS: Final = 4
SPEECH_PADDING_MS: Final = 64
MAX_PCM_BYTES: Final = 4 * 60 * 60 * SAMPLE_RATE_HZ * PCM_BYTES_PER_SAMPLE
MAX_ASR_BATCH_DURATION_MS: Final = 180_000
MAX_ASR_WAV_BYTES: Final = 6 * 1024 * 1024
_OUTPUT_SLACK_BYTES: Final = CHUNK_SAMPLES * PCM_BYTES_PER_SAMPLE
_OUTPUT_POLL_SECONDS: Final = 0.02
_PROCESS_REAP_SECONDS: Final = 5.0
_SCRATCH_PREFIX: Final = "automation-tool-speech-analysis-"
_PCM_FILENAME: Final = "audio.pcm"


def _reject() -> NoReturn:
    raise MaterialSpeechRejected from None


@dataclass(frozen=True, slots=True)
class SpeechAudioBatch:
    """One path-free WAV payload accepted by the supplier-neutral ASR boundary."""

    wav_bytes: bytes = field(repr=False)
    duration_ms: int

    def __post_init__(self) -> None:
        if (
            type(self.wav_bytes) is not bytes
            or not 44 <= len(self.wav_bytes) <= MAX_ASR_WAV_BYTES
            or not self.wav_bytes.startswith(b"RIFF")
            or self.wav_bytes[8:12] != b"WAVE"
            or type(self.duration_ms) is not int
            or not 1 <= self.duration_ms <= MAX_ASR_BATCH_DURATION_MS
        ):
            _reject()
        try:
            with wave.open(io.BytesIO(self.wav_bytes), "rb") as source:
                frame_count = source.getnframes()
                valid_shape = (
                    source.getnchannels() == 1
                    and source.getsampwidth() == PCM_BYTES_PER_SAMPLE
                    and source.getframerate() == SAMPLE_RATE_HZ
                    and source.getcomptype() == "NONE"
                    and frame_count > 0
                )
                payload = source.readframes(frame_count)
        except (EOFError, wave.Error):
            _reject()
        if (
            not valid_shape
            or len(payload) != frame_count * PCM_BYTES_PER_SAMPLE
            or math.ceil(frame_count * 1_000 / SAMPLE_RATE_HZ) != self.duration_ms
        ):
            _reject()


@runtime_checkable
class SpeechTranscriptionAdapter(Protocol):
    def transcribe(self, audio: SpeechAudioBatch) -> str: ...


@runtime_checkable
class SpeechProbabilityAnalyzer(Protocol):
    def probability(self, samples: object, *, sample_rate_hz: object) -> float: ...


@dataclass(slots=True, repr=False)
class LocalAudibleSpeechAnalyzerFactory:
    """Lazy owner of the path-shaped resources T1 must never create for silence."""

    tools: PackagedMediaTools
    source: Path
    approved: os.stat_result
    vad_factory: Callable[[], object]
    asr_adapter: SpeechTranscriptionAdapter
    calls: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.tools, PackagedMediaTools)
            or not isinstance(self.source, Path)
            or not isinstance(self.approved, os.stat_result)
            or not callable(self.vad_factory)
            or not isinstance(self.asr_adapter, SpeechTranscriptionAdapter)
        ):
            _reject()

    def __call__(self) -> LocalAudibleSpeechAnalyzer:
        self.calls += 1
        return LocalAudibleSpeechAnalyzer(
            tools=self.tools,
            source=self.source,
            approved=self.approved,
            vad_factory=self.vad_factory,
            asr_adapter=self.asr_adapter,
        )


@dataclass(frozen=True, slots=True, repr=False)
class LocalAudibleSpeechAnalyzer:
    """Concrete lower funnel created only after LE-07 admitted audible material."""

    tools: PackagedMediaTools
    source: Path
    approved: os.stat_result
    vad_factory: Callable[[], object]
    asr_adapter: SpeechTranscriptionAdapter

    def __post_init__(self) -> None:
        if (
            not isinstance(self.tools, PackagedMediaTools)
            or not isinstance(self.source, Path)
            or not isinstance(self.approved, os.stat_result)
            or not callable(self.vad_factory)
            or not isinstance(self.asr_adapter, SpeechTranscriptionAdapter)
        ):
            _reject()

    def analyze(self, facts: MaterialFacts) -> MaterialSpeechAnalysis:
        failed = False
        result: MaterialSpeechAnalysis | None = None
        try:
            result = self._analyze(facts)
        except Exception:
            failed = True
        if failed or result is None:
            _reject()
        return result

    def _analyze(self, facts: MaterialFacts) -> MaterialSpeechAnalysis:
        if (
            type(facts) is not MaterialFacts
            or not facts.has_audio
            or type(facts.duration_ms) is not int
            or facts.duration_ms <= 0
        ):
            _reject()
        self.tools.revalidate()
        source, checked = require_source_unchanged(self.source, self.approved)
        try:
            workspace_text = tempfile.mkdtemp(prefix=_SCRATCH_PREFIX)
        except OSError:
            _reject()
        workspace = Path(workspace_text)
        pcm_path = workspace / _PCM_FILENAME
        try:
            approved_pcm = _extract_pcm(
                self.tools.ffmpeg_path,
                source,
                pcm_path,
                duration_ms=facts.duration_ms,
            )
            require_source_unchanged(source, checked)
            vad = self.vad_factory()
            if not isinstance(vad, SpeechProbabilityAnalyzer):
                _reject()
            segments, pcm_bytes = _detect_speech_segments(
                pcm_path,
                vad,
                duration_ms=facts.duration_ms,
                approved=approved_pcm,
            )
            if not segments:
                return MaterialSpeechAnalysis(False, (), None)
            transcripts: list[str] = []
            transcript_characters = 0
            batches = _speech_audio_batches(
                pcm_path,
                pcm_bytes=pcm_bytes,
                duration_ms=facts.duration_ms,
                segments=segments,
                approved=approved_pcm,
            )
            try:
                for batch in batches:
                    transcript = self.asr_adapter.transcribe(batch)
                    if (
                        type(transcript) is not str
                        or not transcript
                        or transcript != transcript.strip()
                    ):
                        _reject()
                    transcript_characters += len(transcript) + int(bool(transcripts))
                    if transcript_characters > MAX_TRANSCRIPT_CHARACTERS:
                        _reject()
                    transcripts.append(transcript)
            finally:
                batches.close()
            if not transcripts:
                _reject()
            transcript = "\n".join(transcripts)
            return MaterialSpeechAnalysis(True, segments, transcript)
        finally:
            with suppress(OSError):
                shutil.rmtree(workspace)


def _extraction_timeout_seconds(duration_ms: int) -> float:
    return float(min(900, 30 + math.ceil(duration_ms / 1_000 / 8)))


def _pcm_output_limit(duration_ms: int) -> int:
    return min(MAX_PCM_BYTES, duration_ms * PCM_BYTES_PER_MILLISECOND + _OUTPUT_SLACK_BYTES)


def _ffmpeg_argv(ffmpeg: Path, source: Path, output: Path) -> list[str]:
    return [
        os.fspath(ffmpeg),
        "-y",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        os.fspath(source),
        "-map",
        "0:a:0",
        "-vn",
        "-sn",
        "-dn",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE_HZ),
        "-c:a",
        "pcm_s16le",
        "-f",
        "s16le",
        os.fspath(output),
    ]


def _extract_pcm(
    ffmpeg: Path,
    source: Path,
    output: Path,
    *,
    duration_ms: int,
) -> os.stat_result:
    limit = _pcm_output_limit(duration_ms)
    descriptor: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(output, flags, 0o600)
        approved_output = os.fstat(descriptor)
    except OSError:
        _reject()
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
    try:
        process = subprocess.Popen(
            _ffmpeg_argv(ffmpeg, source, output),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        _reject()
    deadline = time.monotonic() + _extraction_timeout_seconds(duration_ms)
    returncode: int | None = None
    with process:
        try:
            while returncode is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _kill_and_reap(process)
                    _reject()
                try:
                    returncode = process.wait(timeout=min(_OUTPUT_POLL_SECONDS, remaining))
                except subprocess.TimeoutExpired:
                    returncode = None
                current = _ordinary_file_metadata(output)
                if current is None or not _same_output_inode(approved_output, current):
                    _kill_and_reap(process)
                    _reject()
                if current.st_size > limit:
                    _kill_and_reap(process)
                    _reject()
        except BaseException:
            _kill_and_reap(process)
            raise
    current = _ordinary_file_metadata(output)
    if (
        returncode != 0
        or current is None
        or not _same_output_inode(approved_output, current)
        or current.st_size <= 0
        or current.st_size > limit
        or current.st_size % 2
    ):
        _reject()
    return current


def _ordinary_file_metadata(path: Path) -> os.stat_result | None:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError:
        return None
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        return None
    return metadata


def _same_output_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and stat.S_IFMT(left.st_mode) == stat.S_IFMT(right.st_mode)
    )


def _kill_and_reap(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        with suppress(OSError):
            process.kill()
    try:
        process.wait(timeout=_PROCESS_REAP_SECONDS)
    except subprocess.TimeoutExpired:
        with suppress(OSError):
            process.kill()
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=_PROCESS_REAP_SECONDS)


def _detect_speech_segments(
    pcm_path: Path,
    vad: SpeechProbabilityAnalyzer,
    *,
    duration_ms: int,
    approved: os.stat_result | None = None,
) -> tuple[tuple[tuple[int, int], ...], int]:
    probabilities: list[float] = []
    pcm_bytes = 0
    remaining_pcm_bytes = duration_ms * PCM_BYTES_PER_MILLISECOND
    descriptor: int | None = None
    try:
        descriptor, before = _open_stable_pcm(pcm_path, approved=approved)
        while remaining_pcm_bytes:
            chunk = os.read(
                descriptor,
                min(
                    CHUNK_SAMPLES * PCM_BYTES_PER_SAMPLE,
                    remaining_pcm_bytes,
                ),
            )
            if not chunk:
                break
            pcm_bytes += len(chunk)
            remaining_pcm_bytes -= len(chunk)
            if len(chunk) % PCM_BYTES_PER_SAMPLE:
                _reject()
            samples = np.frombuffer(chunk, dtype="<i2").astype(np.float32)
            actual_samples = len(samples)
            if actual_samples < CHUNK_SAMPLES:
                samples = np.pad(samples, (0, CHUNK_SAMPLES - actual_samples))
            normalized = tuple(float(value) for value in samples / 32768.0)
            probability = vad.probability(normalized, sample_rate_hz=SAMPLE_RATE_HZ)
            if (
                type(probability) is not float
                or not math.isfinite(probability)
                or not 0.0 <= probability <= 1.0
            ):
                _reject()
            probabilities.append(probability)
        _require_stable_pcm(pcm_path, descriptor, before)
    except MaterialSpeechRejected:
        raise
    except (OSError, ValueError, TypeError):
        _reject()
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
    actual_duration_ms = min(
        duration_ms,
        math.ceil(pcm_bytes / PCM_BYTES_PER_SAMPLE * 1_000 / SAMPLE_RATE_HZ),
    )
    return _aggregate_probabilities(probabilities, duration_ms=actual_duration_ms), pcm_bytes


def _aggregate_probabilities(
    probabilities: list[float],
    *,
    duration_ms: int,
) -> tuple[tuple[int, int], ...]:
    segments: list[tuple[int, int]] = []
    candidate_start: int | None = None
    consecutive_speech_chunks = 0
    confirmed = False
    silence_chunks = 0
    for index, probability in enumerate(probabilities):
        if probability >= VAD_THRESHOLD:
            if candidate_start is None:
                candidate_start = index
            consecutive_speech_chunks += 1
            if consecutive_speech_chunks >= MIN_SPEECH_CHUNKS:
                confirmed = True
            silence_chunks = 0
            continue
        if candidate_start is None:
            continue
        if not confirmed:
            candidate_start = None
            consecutive_speech_chunks = 0
            silence_chunks = 0
            continue
        silence_chunks += 1
        consecutive_speech_chunks = 0
        if silence_chunks < MIN_SILENCE_CHUNKS:
            continue
        first_silence = index + 1 - silence_chunks
        if confirmed:
            _append_segment(
                segments,
                start_ms=candidate_start * VAD_CHUNK_MILLISECONDS - SPEECH_PADDING_MS,
                end_ms=first_silence * VAD_CHUNK_MILLISECONDS + SPEECH_PADDING_MS,
                duration_ms=duration_ms,
            )
        candidate_start = None
        consecutive_speech_chunks = 0
        confirmed = False
        silence_chunks = 0
    if candidate_start is not None and confirmed:
        first_trailing_silence = len(probabilities) - silence_chunks
        _append_segment(
            segments,
            start_ms=candidate_start * VAD_CHUNK_MILLISECONDS - SPEECH_PADDING_MS,
            end_ms=first_trailing_silence * VAD_CHUNK_MILLISECONDS + SPEECH_PADDING_MS,
            duration_ms=duration_ms,
        )
    if len(segments) > MAX_SPEECH_SEGMENTS:
        _reject()
    return tuple(segments)


def _append_segment(
    segments: list[tuple[int, int]],
    *,
    start_ms: int,
    end_ms: int,
    duration_ms: int,
) -> None:
    bounded_start = max(0, start_ms)
    bounded_end = min(duration_ms, end_ms)
    if bounded_end <= bounded_start:
        return
    if segments and bounded_start <= segments[-1][1]:
        segments[-1] = (segments[-1][0], max(segments[-1][1], bounded_end))
    else:
        segments.append((bounded_start, bounded_end))


def _speech_audio_batches(
    pcm_path: Path,
    *,
    pcm_bytes: int,
    duration_ms: int,
    segments: tuple[tuple[int, int], ...],
    approved: os.stat_result | None = None,
) -> Generator[SpeechAudioBatch, None, None]:
    total_duration_ms = min(
        duration_ms,
        math.ceil(pcm_bytes / PCM_BYTES_PER_SAMPLE * 1_000 / SAMPLE_RATE_HZ),
    )
    yielded = False
    descriptor: int | None = None
    before: os.stat_result | None = None
    try:
        descriptor, before = _open_stable_pcm(pcm_path, approved=approved)
        for start_ms in range(0, total_duration_ms, MAX_ASR_BATCH_DURATION_MS):
            end_ms = min(total_duration_ms, start_ms + MAX_ASR_BATCH_DURATION_MS)
            if not any(
                segment_start < end_ms and segment_end > start_ms
                for segment_start, segment_end in segments
            ):
                continue
            start_byte = start_ms * PCM_BYTES_PER_MILLISECOND
            end_byte = min(pcm_bytes, end_ms * PCM_BYTES_PER_MILLISECOND)
            os.lseek(descriptor, start_byte, os.SEEK_SET)
            payload = _read_exact(descriptor, end_byte - start_byte)
            if len(payload) != end_byte - start_byte:
                _reject()
            wav_bytes = _pcm_wav(payload)
            yielded = True
            yield SpeechAudioBatch(
                wav_bytes=wav_bytes,
                duration_ms=math.ceil(
                    len(payload) / PCM_BYTES_PER_SAMPLE * 1_000 / SAMPLE_RATE_HZ
                ),
            )
        if not yielded:
            _reject()
    except MaterialSpeechRejected:
        raise
    except OSError:
        _reject()
    finally:
        if descriptor is not None:
            try:
                if before is not None:
                    _require_stable_pcm(pcm_path, descriptor, before)
            finally:
                with suppress(OSError):
                    os.close(descriptor)


def _open_stable_pcm(
    path: Path,
    *,
    approved: os.stat_result | None = None,
) -> tuple[int, os.stat_result]:
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        current = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(current.st_mode)
            or not _same_pcm_file(before, current)
            or (approved is not None and not _same_pcm_file(approved, before))
        ):
            _reject()
        return descriptor, before
    except MaterialSpeechRejected:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        raise
    except OSError:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        _reject()


def _require_stable_pcm(
    path: Path,
    descriptor: int,
    before: os.stat_result,
) -> None:
    try:
        after = os.fstat(descriptor)
        current = path.lstat()
    except OSError:
        _reject()
    if (
        stat.S_ISLNK(current.st_mode)
        or not _same_pcm_file(before, after)
        or not _same_pcm_file(before, current)
    ):
        _reject()


def _same_pcm_file(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and left.st_mode == right.st_mode
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and left.st_ctime_ns == right.st_ctime_ns
    )


def _read_exact(descriptor: int, byte_count: int) -> bytes:
    chunks: list[bytes] = []
    remaining = byte_count
    while remaining:
        chunk = os.read(descriptor, min(remaining, 1024 * 1024))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _pcm_wav(payload: bytes) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(PCM_BYTES_PER_SAMPLE)
        destination.setframerate(SAMPLE_RATE_HZ)
        destination.writeframes(payload)
    rendered = output.getvalue()
    if len(rendered) > MAX_ASR_WAV_BYTES:
        _reject()
    return rendered


__all__ = [
    "MAX_ASR_BATCH_DURATION_MS",
    "MAX_ASR_WAV_BYTES",
    "LocalAudibleSpeechAnalyzer",
    "LocalAudibleSpeechAnalyzerFactory",
    "SpeechAudioBatch",
    "SpeechProbabilityAnalyzer",
    "SpeechTranscriptionAdapter",
]
