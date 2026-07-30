"""Supplier-neutral orchestration for the material speech-analysis funnel."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Final, NoReturn, Protocol, runtime_checkable

from automation_tool.executor.material_probe import MaterialFacts

MAX_SPEECH_SEGMENTS: Final = 4_096
MAX_TRANSCRIPT_CHARACTERS: Final = 100_000


class MaterialSpeechRejected(RuntimeError):
    """The speech-analysis boundary rejected input, execution or output."""

    def __init__(self) -> None:
        super().__init__("material speech analysis rejected")


def _reject() -> NoReturn:
    raise MaterialSpeechRejected from None


@dataclass(frozen=True, slots=True)
class MaterialSpeechAnalysis:
    """Path-free speech facts that can be written into one Material."""

    has_speech: bool
    speech_segments_ms: tuple[tuple[int, int], ...]
    speech_transcript: str | None

    def __post_init__(self) -> None:
        if type(self.has_speech) is not bool or not isinstance(self.speech_segments_ms, tuple):
            _reject()
        if not self.has_speech:
            if self.speech_segments_ms or self.speech_transcript is not None:
                _reject()
            return
        if (
            not 1 <= len(self.speech_segments_ms) <= MAX_SPEECH_SEGMENTS
            or type(self.speech_transcript) is not str
            or not self.speech_transcript
            or self.speech_transcript != self.speech_transcript.strip()
            or len(self.speech_transcript) > MAX_TRANSCRIPT_CHARACTERS
            or any(
                character not in {"\n", "\t"} and unicodedata.category(character).startswith("C")
                for character in self.speech_transcript
            )
        ):
            _reject()
        previous_end = 0
        for segment in self.speech_segments_ms:
            if (
                not isinstance(segment, tuple)
                or len(segment) != 2
                or any(type(value) is not int for value in segment)
            ):
                _reject()
            start, end = segment
            if start < previous_end or end <= start:
                _reject()
            previous_end = end


@runtime_checkable
class AudibleSpeechAnalyzer(Protocol):
    """The VAD and ASR stages used only after audible content was measured."""

    def analyze(self, facts: MaterialFacts) -> MaterialSpeechAnalysis: ...


@runtime_checkable
class AudibleSpeechAnalyzerFactory(Protocol):
    """Construct the lower funnel only after stage one admits the material."""

    def __call__(self) -> AudibleSpeechAnalyzer: ...


def analyze_material_speech(
    facts: object,
    *,
    audible_analyzer_factory: object,
) -> MaterialSpeechAnalysis:
    """Stop silent material at stage one, otherwise enter the lower funnel once."""

    if (
        type(facts) is not MaterialFacts
        or type(facts.has_audio) is not bool
        or not isinstance(audible_analyzer_factory, AudibleSpeechAnalyzerFactory)
    ):
        _reject()
    if not facts.has_audio:
        return MaterialSpeechAnalysis(
            has_speech=False,
            speech_segments_ms=(),
            speech_transcript=None,
        )

    failed = False
    candidate: object = None
    try:
        analyzer = audible_analyzer_factory()
        if not isinstance(analyzer, AudibleSpeechAnalyzer):
            failed = True
        else:
            candidate = analyzer.analyze(facts)
    except Exception:
        failed = True
    if failed or type(candidate) is not MaterialSpeechAnalysis:
        _reject()
    if facts.duration_ms is not None and any(
        end > facts.duration_ms for _start, end in candidate.speech_segments_ms
    ):
        _reject()
    return candidate


__all__ = [
    "MAX_SPEECH_SEGMENTS",
    "MAX_TRANSCRIPT_CHARACTERS",
    "AudibleSpeechAnalyzer",
    "AudibleSpeechAnalyzerFactory",
    "MaterialSpeechAnalysis",
    "MaterialSpeechRejected",
    "analyze_material_speech",
]
