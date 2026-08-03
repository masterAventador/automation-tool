"""LE-14 T1: stop the speech funnel on LE-07's measured audio fact."""

from __future__ import annotations

from dataclasses import replace

import pytest

from automation_tool.executor.material_probe import (
    MAX_MATERIAL_DURATION_MS,
    MaterialFacts,
    ProbedMaterialKind,
)
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
        replace(_facts(has_audio=True), has_audio=1),  # type: ignore[arg-type]
        replace(_facts(has_audio=True), duration_ms="9000"),  # type: ignore[arg-type]
        replace(_facts(has_audio=True), duration_ms=True),
        replace(_facts(has_audio=True), duration_ms=0),
        replace(
            _facts(has_audio=True),
            duration_ms=MAX_MATERIAL_DURATION_MS + 1,
        ),
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


def test_a_speech_analysis_must_describe_one_coherent_reading() -> None:
    """It is persisted onto the material, so a shape no analysis could produce is refused."""
    cases: list[tuple[str, dict[str, object]]] = [
        ("a speech flag that is not a bool", {"has_speech": 1}),
        ("segments that are not a tuple", {"speech_segments_ms": [(0, 100)]}),
        (
            "silence carrying segments",
            {"has_speech": False, "speech_segments_ms": ((0, 100),), "speech_transcript": None},
        ),
        (
            "silence carrying a transcript",
            {"has_speech": False, "speech_segments_ms": (), "speech_transcript": "话"},
        ),
        (
            "speech with no segments",
            {"has_speech": True, "speech_segments_ms": (), "speech_transcript": "话"},
        ),
        (
            "speech with no transcript",
            {"has_speech": True, "speech_segments_ms": ((0, 100),), "speech_transcript": None},
        ),
        (
            "a transcript with untrimmed space",
            {"has_speech": True, "speech_segments_ms": ((0, 100),), "speech_transcript": " 话 "},
        ),
        (
            "a transcript carrying a control character",
            {
                "has_speech": True,
                "speech_segments_ms": ((0, 100),),
                "speech_transcript": "话\x00音",
            },
        ),
        (
            "a window that is not a pair of ints",
            {
                "has_speech": True,
                "speech_segments_ms": (("0", 100),),
                "speech_transcript": "话",
            },
        ),
        (
            "windows that run backwards",
            {
                "has_speech": True,
                "speech_segments_ms": ((100, 200), (0, 50)),
                "speech_transcript": "话",
            },
        ),
        (
            "a window that ends before it starts",
            {
                "has_speech": True,
                "speech_segments_ms": ((100, 100),),
                "speech_transcript": "话",
            },
        ),
    ]
    for label, overrides in cases:
        arguments: dict[str, object] = {
            "has_speech": True,
            "speech_segments_ms": ((0, 100),),
            "speech_transcript": "这段是真实原声。",
        }
        arguments.update(overrides)
        with pytest.raises(MaterialSpeechRejected):
            MaterialSpeechAnalysis(**arguments)  # type: ignore[arg-type]
        assert label


def test_speech_that_runs_past_the_material_is_refused() -> None:
    """The windows are read back against the material's own length; they must fit in it."""
    analyzer = RecordingAudibleAnalyzer(
        MaterialSpeechAnalysis(
            has_speech=True,
            speech_segments_ms=((0, 9_500),),
            speech_transcript="这段是真实原声。",
        )
    )

    with pytest.raises(MaterialSpeechRejected):
        analyze_material_speech(
            _facts(has_audio=True),
            audible_analyzer_factory=RecordingAnalyzerFactory(analyzer),
        )


def test_a_factory_that_builds_the_wrong_thing_is_refused() -> None:
    """The factory is lazy on purpose; what it hands back is still checked."""
    with pytest.raises(MaterialSpeechRejected):
        analyze_material_speech(
            _facts(has_audio=True),
            audible_analyzer_factory=RecordingAnalyzerFactory(object()),
        )
