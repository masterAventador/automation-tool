"""Align an approved user narration recording to segmented script sentences."""

from __future__ import annotations

import os
import unicodedata
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, NoReturn

from automation_tool.executor.material_probe import (
    MAX_MATERIAL_DURATION_MS,
    MediaStreamFacts,
    PackagedMediaTools,
    ProbedMaterialKind,
    read_stream_facts,
    require_source_unchanged,
)
from automation_tool.executor.material_speech_analysis import MAX_TRANSCRIPT_CHARACTERS
from automation_tool.executor.material_speech_pipeline import (
    LocalRecordedSpeechAnalyzer,
    RecordedSpeechAnalysis,
    SpeechTranscriptionAdapter,
)
from automation_tool.executor.script_segmentation import (
    MAX_SCRIPT_SENTENCES,
    ScriptSegmentationResult,
    ScriptSentence,
)

_MAX_REQUEST_ID_CHARACTERS: Final = 512
_MAX_ALIGNMENT_ERROR_PERCENT: Final = 15
_EXACT_ALIGNMENT_CHARACTERS: Final = 4


class ScriptRecordingRejected(RuntimeError):
    """The user-recording alignment boundary rejected one complete request."""

    def __init__(self) -> None:
        super().__init__("script recording rejected")


def _reject() -> NoReturn:
    raise ScriptRecordingRejected from None


def _valid_text(value: object, *, maximum: int) -> bool:
    return (
        type(value) is str
        and bool(value)
        and value == value.strip()
        and len(value) <= maximum
        and not any(
            character not in {"\n", "\t"} and unicodedata.category(character).startswith("C")
            for character in value
        )
    )


def _valid_request_id(value: object) -> bool:
    return _valid_text(value, maximum=_MAX_REQUEST_ID_CHARACTERS)


@dataclass(frozen=True, slots=True)
class ScriptRecordedClip:
    """One sentence and its real, local VAD-bounded range in the source audio."""

    sentence: ScriptSentence
    source_start_ms: int
    source_end_ms: int
    duration_ms: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.sentence, ScriptSentence)
            or type(self.source_start_ms) is not int
            or type(self.source_end_ms) is not int
            or type(self.duration_ms) is not int
            or not 0 <= self.source_start_ms < self.source_end_ms
            or self.source_end_ms > MAX_MATERIAL_DURATION_MS
            or self.duration_ms != self.source_end_ms - self.source_start_ms
        ):
            _reject()


@dataclass(frozen=True, slots=True)
class ScriptRecordingResult:
    """A path-free all-or-nothing alignment over one caller-owned recording."""

    script_request_id: str
    recording_duration_ms: int
    transcript: str = field(repr=False)
    clips: tuple[ScriptRecordedClip, ...]

    def __post_init__(self) -> None:
        if (
            not _valid_request_id(self.script_request_id)
            or type(self.recording_duration_ms) is not int
            or not 1 <= self.recording_duration_ms <= MAX_MATERIAL_DURATION_MS
            or not _valid_text(self.transcript, maximum=MAX_TRANSCRIPT_CHARACTERS)
            or not isinstance(self.clips, tuple)
            or not 1 <= len(self.clips) <= MAX_SCRIPT_SENTENCES
            or not all(isinstance(clip, ScriptRecordedClip) for clip in self.clips)
            or tuple(clip.sentence.sequence for clip in self.clips)
            != tuple(range(1, len(self.clips) + 1))
        ):
            _reject()
        normalized_sentences = tuple(
            _normalized_speech_text(clip.sentence.text) for clip in self.clips
        )
        if not all(normalized_sentences) or not _sentences_align(
            normalized_sentences,
            _normalized_speech_text(self.transcript),
        ):
            _reject()
        previous_end = 0
        for clip in self.clips:
            if (
                clip.source_start_ms < previous_end
                or clip.source_end_ms > self.recording_duration_ms
            ):
                _reject()
            previous_end = clip.source_end_ms


def _normalized_speech_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character
        for character in normalized
        if unicodedata.category(character)[0] in {"L", "M", "N"}
    )


def _levenshtein_distance(left: str, right: str) -> int:
    if len(left) > len(right):
        left, right = right, left
    previous = list(range(len(left) + 1))
    for right_index, right_character in enumerate(right, start=1):
        current = [right_index]
        for left_index, left_character in enumerate(left, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[left_index] + 1,
                    previous[left_index - 1] + int(left_character != right_character),
                )
            )
        previous = current
    return previous[-1]


def _texts_align(script_text: str, transcript: str) -> bool:
    maximum = max(len(script_text), len(transcript))
    if not script_text or not transcript:
        return False
    if maximum <= _EXACT_ALIGNMENT_CHARACTERS:
        return script_text == transcript
    allowed_errors = maximum * _MAX_ALIGNMENT_ERROR_PERCENT // 100
    if abs(len(script_text) - len(transcript)) > allowed_errors:
        return False
    return _levenshtein_distance(script_text, transcript) <= allowed_errors


def _aligned_slice_length_bounds(expected: str) -> tuple[int, int]:
    expected_length = len(expected)
    minimum = max(
        1,
        expected_length - expected_length * _MAX_ALIGNMENT_ERROR_PERCENT // 100,
    )
    maximum = expected_length * 100 // (100 - _MAX_ALIGNMENT_ERROR_PERCENT)
    return minimum, maximum


def _slice_proves_sentence(
    normalized_sentences: tuple[str, ...],
    *,
    sentence_index: int,
    actual: str,
) -> bool:
    expected = normalized_sentences[sentence_index]
    if not _texts_align(expected, actual):
        return False
    expected_distance = _levenshtein_distance(expected, actual)
    for other_index, other in enumerate(normalized_sentences):
        if other_index == sentence_index or other == expected:
            continue
        if (
            _texts_align(other, actual)
            and _levenshtein_distance(other, actual) <= expected_distance
        ):
            # A slice that is at least as close to another distinct script
            # sentence cannot prove the declared order. Fail closed rather
            # than binding a near-duplicate sentence to the wrong audio.
            return False
        if actual in other:
            # A shortened slice copied wholly from another distinct sentence
            # can be paid for by the global deletion budget when the intended
            # sentence was never spoken. It cannot establish sentence
            # presence, even when it also falls within this sentence's ASR
            # tolerance.
            return False
    return True


def _sentences_align(
    normalized_sentences: tuple[str, ...],
    transcript: str,
) -> bool:
    if not _texts_align("".join(normalized_sentences), transcript):
        return False
    length_bounds = tuple(
        _aligned_slice_length_bounds(sentence) for sentence in normalized_sentences
    )
    minimum_suffix = [0] * (len(normalized_sentences) + 1)
    maximum_suffix = [0] * (len(normalized_sentences) + 1)
    for index in range(len(normalized_sentences) - 1, -1, -1):
        minimum, maximum = length_bounds[index]
        minimum_suffix[index] = minimum + minimum_suffix[index + 1]
        maximum_suffix[index] = maximum + maximum_suffix[index + 1]
    if not minimum_suffix[0] <= len(transcript) <= maximum_suffix[0]:
        return False
    reachable_starts = {0}
    for index, (minimum, maximum) in enumerate(length_bounds):
        reachable_ends: set[int] = set()
        for start in reachable_starts:
            first_end = max(
                start + minimum,
                len(transcript) - maximum_suffix[index + 1],
            )
            last_end = min(
                start + maximum,
                len(transcript) - minimum_suffix[index + 1],
            )
            for end in range(first_end, last_end + 1):
                if _slice_proves_sentence(
                    normalized_sentences,
                    sentence_index=index,
                    actual=transcript[start:end],
                ):
                    reachable_ends.add(end)
        if not reachable_ends:
            return False
        reachable_starts = reachable_ends
    return len(transcript) in reachable_starts


def _clips_from_segments(
    sentences: tuple[ScriptSentence, ...],
    normalized_sentences: tuple[str, ...],
    segments: tuple[tuple[int, int], ...],
) -> tuple[ScriptRecordedClip, ...]:
    segment_cumulative_durations: list[int] = []
    running_duration = 0
    for start, end in segments:
        running_duration += end - start
        segment_cumulative_durations.append(running_duration)
    total_speech_duration = running_duration
    total_characters = sum(len(text) for text in normalized_sentences)
    sentence_cumulative_characters = 0
    group_start_index = 0
    clips: list[ScriptRecordedClip] = []
    for sentence_index, (sentence, normalized) in enumerate(
        zip(sentences, normalized_sentences, strict=True)
    ):
        sentence_cumulative_characters += len(normalized)
        remaining_sentences = len(sentences) - sentence_index - 1
        if remaining_sentences:
            maximum_end_index = len(segments) - remaining_sentences - 1
            target = total_speech_duration * sentence_cumulative_characters / total_characters
            group_end_index = min(
                range(group_start_index, maximum_end_index + 1),
                key=lambda index: (
                    abs(segment_cumulative_durations[index] - target),
                    index,
                ),
            )
        else:
            group_end_index = len(segments) - 1
        source_start_ms = segments[group_start_index][0]
        source_end_ms = segments[group_end_index][1]
        clips.append(
            ScriptRecordedClip(
                sentence=sentence,
                source_start_ms=source_start_ms,
                source_end_ms=source_end_ms,
                duration_ms=source_end_ms - source_start_ms,
            )
        )
        group_start_index = group_end_index + 1
    return tuple(clips)


def _align_script_recording(
    script: ScriptSegmentationResult,
    *,
    source: Path,
    approved: os.stat_result,
    tools: PackagedMediaTools,
    vad_factory: Callable[[], object],
    asr_adapter: SpeechTranscriptionAdapter,
) -> ScriptRecordingResult:
    if (
        not isinstance(script, ScriptSegmentationResult)
        or not isinstance(source, Path)
        or not isinstance(approved, os.stat_result)
        or not isinstance(tools, PackagedMediaTools)
        or not callable(vad_factory)
        or not isinstance(asr_adapter, SpeechTranscriptionAdapter)
    ):
        _reject()
    normalized_sentences = tuple(
        _normalized_speech_text(sentence.text) for sentence in script.sentences
    )
    if not all(normalized_sentences):
        _reject()
    tools.revalidate()
    source, checked = require_source_unchanged(source, approved)
    facts = read_stream_facts(tools, source)
    source, checked = require_source_unchanged(source, checked)
    if (
        not isinstance(facts, MediaStreamFacts)
        or facts.kind is not ProbedMaterialKind.AUDIO
        or type(facts.duration_ms) is not int
        or not 1 <= facts.duration_ms <= MAX_MATERIAL_DURATION_MS
    ):
        _reject()
    analyzer = LocalRecordedSpeechAnalyzer(
        tools=tools,
        source=source,
        approved=checked,
        vad_factory=vad_factory,
        asr_adapter=asr_adapter,
    )
    analysis = analyzer.analyze(
        facts.duration_ms,
        minimum_segments=len(script.sentences),
    )
    if (
        not isinstance(analysis, RecordedSpeechAnalysis)
        or analysis.duration_ms != facts.duration_ms
        or len(analysis.speech_segments_ms) < len(script.sentences)
    ):
        _reject()
    normalized_transcript = _normalized_speech_text(analysis.transcript)
    if not _sentences_align(normalized_sentences, normalized_transcript):
        _reject()
    return ScriptRecordingResult(
        script_request_id=script.request_id,
        recording_duration_ms=facts.duration_ms,
        transcript=analysis.transcript,
        clips=_clips_from_segments(
            script.sentences,
            normalized_sentences,
            analysis.speech_segments_ms,
        ),
    )


def align_script_recording(
    script: ScriptSegmentationResult,
    *,
    source: Path,
    approved: os.stat_result,
    tools: PackagedMediaTools,
    vad_factory: Callable[[], object],
    asr_adapter: SpeechTranscriptionAdapter,
) -> ScriptRecordingResult:
    """Transcribe and align one recording, closing every failure to one error."""

    result: ScriptRecordingResult | None = None
    with suppress(Exception):
        result = _align_script_recording(
            script,
            source=source,
            approved=approved,
            tools=tools,
            vad_factory=vad_factory,
            asr_adapter=asr_adapter,
        )
    if result is None:
        _reject()
    return result


__all__ = [
    "ScriptRecordedClip",
    "ScriptRecordingRejected",
    "ScriptRecordingResult",
    "align_script_recording",
]
