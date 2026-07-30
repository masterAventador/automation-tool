"""LE-14 T1: stop the speech funnel on LE-07's measured audio fact."""

from __future__ import annotations

from dataclasses import replace

import pytest

from automation_tool.executor.material_probe import MaterialFacts, ProbedMaterialKind
from automation_tool.executor.material_speech_analysis import (
    MaterialSpeechAnalysis,
    MaterialSpeechRejected,
    analyze_material_speech,
)


class RecordingAudibleAnalyzer:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[MaterialFacts] = []

    def analyze(self, facts: MaterialFacts) -> object:
        self.calls.append(facts)
        return self.result


class RecordingAnalyzerFactory:
    def __init__(self, analyzer: object) -> None:
        self.analyzer = analyzer
        self.calls = 0

    def __call__(self) -> object:
        self.calls += 1
        return self.analyzer


def _facts(*, has_audio: bool) -> MaterialFacts:
    return MaterialFacts(
        kind=ProbedMaterialKind.VIDEO,
        duration_ms=9_000,
        width=640,
        height=360,
        video_codec="h264",
        audio_codec="aac",
        has_audio=has_audio,
        audio_loudness_lufs=-21.8 if has_audio else None,
        content_digest="a" * 64,
    )


def test_a_silent_track_stops_before_the_audible_speech_analyzer() -> None:
    facts = _facts(has_audio=False)
    analyzer = RecordingAudibleAnalyzer(
        MaterialSpeechAnalysis(
            has_speech=True,
            speech_segments_ms=((1_000, 4_000),),
            speech_transcript="这条结果不应被消费",
        )
    )
    factory = RecordingAnalyzerFactory(analyzer)

    result = analyze_material_speech(facts, audible_analyzer_factory=factory)

    assert facts.audio_codec == "aac"
    assert result == MaterialSpeechAnalysis(
        has_speech=False,
        speech_segments_ms=(),
        speech_transcript=None,
    )
    assert factory.calls == 0
    assert analyzer.calls == []


def test_an_audible_material_enters_the_lower_funnel_once() -> None:
    facts = _facts(has_audio=True)
    expected = MaterialSpeechAnalysis(
        has_speech=True,
        speech_segments_ms=((1_000, 4_000), (5_000, 8_000)),
        speech_transcript="今天我们测试本地人声漏斗。",
    )
    analyzer = RecordingAudibleAnalyzer(expected)
    factory = RecordingAnalyzerFactory(analyzer)

    result = analyze_material_speech(facts, audible_analyzer_factory=factory)

    assert result is expected
    assert factory.calls == 1
    assert analyzer.calls == [facts]


@pytest.mark.parametrize(
    "facts",
    [
        None,
        object(),
        replace(_facts(has_audio=True), has_audio=1),
    ],
)
def test_invalid_probe_facts_are_rejected_before_the_lower_funnel(
    facts: object,
) -> None:
    analyzer = RecordingAudibleAnalyzer(MaterialSpeechAnalysis(False, (), None))
    factory = RecordingAnalyzerFactory(analyzer)

    with pytest.raises(
        MaterialSpeechRejected,
        match=r"^material speech analysis rejected$",
    ):
        analyze_material_speech(facts, audible_analyzer_factory=factory)

    assert factory.calls == 0
    assert analyzer.calls == []


def test_an_invalid_lower_funnel_result_is_not_returned_as_a_partial_result() -> None:
    analyzer = RecordingAudibleAnalyzer(object())
    factory = RecordingAnalyzerFactory(analyzer)

    with pytest.raises(
        MaterialSpeechRejected,
        match=r"^material speech analysis rejected$",
    ):
        analyze_material_speech(
            _facts(has_audio=True),
            audible_analyzer_factory=factory,
        )

    assert factory.calls == 1
    assert len(analyzer.calls) == 1


def test_a_lower_funnel_failure_does_not_retain_its_private_exception() -> None:
    class FailingAnalyzer:
        def analyze(self, facts: MaterialFacts) -> MaterialSpeechAnalysis:
            del facts
            raise RuntimeError("/Users/operator/private/voice.mp4")

    factory = RecordingAnalyzerFactory(FailingAnalyzer())
    with pytest.raises(
        MaterialSpeechRejected,
        match=r"^material speech analysis rejected$",
    ) as captured:
        analyze_material_speech(
            _facts(has_audio=True),
            audible_analyzer_factory=factory,
        )

    assert factory.calls == 1
    assert captured.value.__context__ is None
