"""LE-15 T3: align one approved user recording to segmented script sentences."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

import pytest

from automation_tool.executor import script_recording
from automation_tool.executor.material_probe import (
    MediaStreamFacts,
    PackagedMediaTools,
    ProbedMaterialKind,
    approve_source,
)
from automation_tool.executor.material_speech_pipeline import (
    RecordedSpeechAnalysis,
    SpeechAudioBatch,
)
from automation_tool.executor.script_recording import (
    ScriptRecordedClip,
    ScriptRecordingRejected,
    ScriptRecordingResult,
    align_script_recording,
)
from automation_tool.executor.script_segmentation import (
    ScriptSegmentationResult,
    ScriptSentence,
)


class NeverCalledAsr:
    def __init__(self) -> None:
        self.calls: list[SpeechAudioBatch] = []

    def transcribe(self, audio: SpeechAudioBatch) -> str:
        self.calls.append(audio)
        raise AssertionError("the injected local analyzer owns ASR in these orchestration tests")


class InjectedRecordedSpeechAnalyzer:
    next_result = RecordedSpeechAnalysis(
        duration_ms=5_000,
        speech_segments_ms=((100, 1_000), (1_200, 2_100), (2_500, 4_000)),
        transcript="HELLO world\uff0c第二句。",
    )
    constructions: ClassVar[list[InjectedRecordedSpeechAnalyzer]] = []

    def __init__(
        self,
        *,
        tools: PackagedMediaTools,
        source: Path,
        approved: os.stat_result,
        vad_factory: Callable[[], object],
        asr_adapter: NeverCalledAsr,
    ) -> None:
        self.tools = tools
        self.source = source
        self.approved = approved
        self.vad_factory = vad_factory
        self.asr_adapter = asr_adapter
        self.calls: list[tuple[int, int]] = []
        self.constructions.append(self)

    def analyze(
        self,
        duration_ms: int,
        *,
        minimum_segments: int,
    ) -> RecordedSpeechAnalysis:
        self.calls.append((duration_ms, minimum_segments))
        return self.next_result


@pytest.fixture(autouse=True)
def _clear_injected_analyzers() -> None:
    InjectedRecordedSpeechAnalyzer.constructions.clear()
    InjectedRecordedSpeechAnalyzer.next_result = RecordedSpeechAnalysis(
        duration_ms=5_000,
        speech_segments_ms=((100, 1_000), (1_200, 2_100), (2_500, 4_000)),
        transcript="HELLO world\uff0c第二句。",
    )


def _script(*sentences: str) -> ScriptSegmentationResult:
    return ScriptSegmentationResult(
        request_id="script-request",
        sentences=tuple(
            ScriptSentence(sequence=sequence, text=text)
            for sequence, text in enumerate(sentences, start=1)
        ),
    )


def _tools(tmp_path: Path) -> PackagedMediaTools:
    ffmpeg = tmp_path / "ffmpeg"
    ffprobe = tmp_path / "ffprobe"
    for tool in (ffmpeg, ffprobe):
        tool.write_bytes(b"packaged tool")
        tool.chmod(0o700)
    return PackagedMediaTools(ffprobe_path=ffprobe, ffmpeg_path=ffmpeg)


def _audio_facts(duration_ms: int = 5_000) -> MediaStreamFacts:
    return MediaStreamFacts(
        kind=ProbedMaterialKind.AUDIO,
        duration_ms=duration_ms,
        width=None,
        height=None,
        video_codec=None,
        audio_codec="pcm_s16le",
    )


def _arrange_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    facts: MediaStreamFacts | None = None,
) -> tuple[Path, os.stat_result, PackagedMediaTools, NeverCalledAsr]:
    source = tmp_path / "private user recording.wav"
    source.write_bytes(b"private recording bytes")
    source, approved = approve_source(source)
    tools = _tools(tmp_path)
    asr = NeverCalledAsr()
    monkeypatch.setattr(
        script_recording,
        "read_stream_facts",
        lambda actual_tools, actual_source: _audio_facts() if facts is None else facts,
    )
    monkeypatch.setattr(
        script_recording,
        "LocalRecordedSpeechAnalyzer",
        InjectedRecordedSpeechAnalyzer,
    )
    return source, approved, tools, asr


def test_two_sentences_use_only_real_vad_boundaries_and_keep_the_result_path_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, approved, tools, asr = _arrange_boundary(tmp_path, monkeypatch)

    result = align_script_recording(
        _script("Hello, world!", "第二句。"),
        source=source,
        approved=approved,
        tools=tools,
        vad_factory=object,
        asr_adapter=asr,
    )

    assert result == ScriptRecordingResult(
        script_request_id="script-request",
        recording_duration_ms=5_000,
        transcript="HELLO world\uff0c第二句。",
        clips=(
            ScriptRecordedClip(
                sentence=ScriptSentence(sequence=1, text="Hello, world!"),
                source_start_ms=100,
                source_end_ms=2_100,
                duration_ms=2_000,
            ),
            ScriptRecordedClip(
                sentence=ScriptSentence(sequence=2, text="第二句。"),
                source_start_ms=2_500,
                source_end_ms=4_000,
                duration_ms=1_500,
            ),
        ),
    )
    analyzer = InjectedRecordedSpeechAnalyzer.constructions[0]
    assert analyzer.source == source
    assert analyzer.approved == approved
    assert analyzer.calls == [(5_000, 2)]
    assert asr.calls == []
    assert os.fspath(source) not in repr(result)
    assert "HELLO" not in repr(result)
    assert not hasattr(result, "source")
    assert not hasattr(result, "path")


def test_more_vad_segments_are_grouped_near_cumulative_sentence_weight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, approved, tools, asr = _arrange_boundary(tmp_path, monkeypatch)
    InjectedRecordedSpeechAnalyzer.next_result = RecordedSpeechAnalysis(
        duration_ms=5_000,
        speech_segments_ms=(
            (100, 500),
            (700, 1_100),
            (1_300, 2_100),
            (2_500, 4_000),
        ),
        transcript="甲乙丙丁戊己庚辛壬癸",
    )

    result = align_script_recording(
        _script("甲乙丙", "丁戊己庚", "辛壬癸"),
        source=source,
        approved=approved,
        tools=tools,
        vad_factory=object,
        asr_adapter=asr,
    )

    assert tuple((clip.source_start_ms, clip.source_end_ms) for clip in result.clips) == (
        (100, 1_100),
        (1_300, 2_100),
        (2_500, 4_000),
    )


@pytest.mark.parametrize(
    ("script_text", "transcript"),
    [
        ("Ab C\uff0c\uff11\uff12\uff01", "abc12"),
        ("abcdefghij", "abcdXfghij"),
        ("abcdefghij", "abcdefghi"),
    ],
)
def test_text_alignment_accepts_normalization_and_a_bounded_recognition_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    script_text: str,
    transcript: str,
) -> None:
    source, approved, tools, asr = _arrange_boundary(tmp_path, monkeypatch)
    InjectedRecordedSpeechAnalyzer.next_result = RecordedSpeechAnalysis(
        duration_ms=5_000,
        speech_segments_ms=((100, 4_000),),
        transcript=transcript,
    )

    result = align_script_recording(
        _script(script_text),
        source=source,
        approved=approved,
        tools=tools,
        vad_factory=object,
        asr_adapter=asr,
    )

    assert result.clips[0].sentence.text == script_text


def test_sentence_partition_moves_when_an_earlier_sentence_loses_its_first_character(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, approved, tools, asr = _arrange_boundary(tmp_path, monkeypatch)
    InjectedRecordedSpeechAnalyzer.next_result = RecordedSpeechAnalysis(
        duration_ms=5_000,
        speech_segments_ms=((100, 2_000), (2_500, 4_000)),
        transcript="bcdefghijklmnopqrst",
    )

    result = align_script_recording(
        _script("abcdefghij", "klmnopqrst"),
        source=source,
        approved=approved,
        tools=tools,
        vad_factory=object,
        asr_adapter=asr,
    )

    assert tuple(clip.sentence.text for clip in result.clips) == (
        "abcdefghij",
        "klmnopqrst",
    )


def test_exact_overlapping_short_sentences_are_not_mistaken_for_a_missing_sentence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, approved, tools, asr = _arrange_boundary(tmp_path, monkeypatch)
    InjectedRecordedSpeechAnalyzer.next_result = RecordedSpeechAnalysis(
        duration_ms=5_000,
        speech_segments_ms=((100, 1_000), (1_500, 2_500), (3_000, 4_000)),
        transcript="天气气温温暖",
    )

    result = align_script_recording(
        _script("天气", "气温", "温暖"),
        source=source,
        approved=approved,
        tools=tools,
        vad_factory=object,
        asr_adapter=asr,
    )

    assert tuple(clip.sentence.text for clip in result.clips) == (
        "天气",
        "气温",
        "温暖",
    )


def test_maximum_exact_script_avoids_edit_distance_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normalized_sentences = tuple(chr(0x4E00 + index) * 31 for index in range(128))
    transcript = "".join(normalized_sentences)

    def unexpected_edit_distance(left: str, right: str) -> int:
        del left, right
        raise AssertionError("exact text must not enter edit-distance work")

    monkeypatch.setattr(
        script_recording,
        "_levenshtein_distance",
        unexpected_edit_distance,
    )

    assert script_recording._sentences_align(normalized_sentences, transcript)


def test_exact_partition_search_does_not_expand_every_valid_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normalized_sentences = tuple(chr(0x4E00 + index) * 20 for index in range(16))
    transcript = "".join(normalized_sentences)
    candidate_checks = 0

    def accept_candidate(
        actual_sentences: tuple[str, ...],
        *,
        sentence_index: int,
        actual: str,
    ) -> bool:
        nonlocal candidate_checks
        del actual_sentences, sentence_index, actual
        candidate_checks += 1
        return True

    monkeypatch.setattr(
        script_recording,
        "_slice_proves_sentence",
        accept_candidate,
    )

    assert script_recording._sentences_align(normalized_sentences, transcript)
    assert candidate_checks <= len(normalized_sentences) * 2


def test_partition_search_stops_at_a_hard_candidate_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normalized_sentences = tuple(chr(0x4E00 + index) * 20 for index in range(64))
    transcript = "".join(normalized_sentences)
    maximum_candidate_checks = 1_024
    candidate_checks = 0

    def exhaust_candidates(
        actual_sentences: tuple[str, ...],
        *,
        sentence_index: int,
        actual: str,
    ) -> bool:
        nonlocal candidate_checks
        del actual_sentences, actual
        candidate_checks += 1
        if candidate_checks > maximum_candidate_checks:
            raise AssertionError("partition search exceeded its hard work budget")
        return sentence_index < len(normalized_sentences) - 1

    monkeypatch.setattr(
        script_recording,
        "_slice_proves_sentence",
        exhaust_candidates,
    )

    assert not script_recording._sentences_align(normalized_sentences, transcript)
    assert candidate_checks == maximum_candidate_checks


@pytest.mark.parametrize(
    ("sentences", "transcript"),
    [
        (("甲乙",), "甲丙"),
        (("abcdefghij",), "abcdXXghij"),
        (("第一句", "第二句"), "第二句第一句"),
        (("只有一句",), "只有一句还多了一整句"),
        (("😀\uff01",), "😀"),
        (("一句",), "😀"),
    ],
)
def test_text_alignment_rejects_short_errors_large_differences_reordering_and_empty_speech(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sentences: tuple[str, ...],
    transcript: str,
) -> None:
    source, approved, tools, asr = _arrange_boundary(tmp_path, monkeypatch)
    InjectedRecordedSpeechAnalyzer.next_result = RecordedSpeechAnalysis(
        duration_ms=5_000,
        speech_segments_ms=tuple(
            (100 + index * 1_000, 900 + index * 1_000) for index in range(len(sentences))
        ),
        transcript=transcript,
    )

    with pytest.raises(ScriptRecordingRejected) as captured:
        align_script_recording(
            _script(*sentences),
            source=source,
            approved=approved,
            tools=tools,
            vad_factory=object,
            asr_adapter=asr,
        )

    assert str(captured.value) == "script recording rejected"
    assert captured.value.__context__ is None


@pytest.mark.parametrize(
    ("sentences", "transcript"),
    [
        (("abcdefghij", "Z"), "abcdefghij"),
        (("aaaaaaaaab", "aaaaaaaaac"), "aaaaaaaaacaaaaaaaaab"),
        (("a" * 33 + "b" * 7, "b" * 7), "a" * 33 + "b" * 7),
        (
            ("a" * 17 + "xyz", "xyz123q", "123" + "c" * 17),
            ("a" * 17 + "xyz") + ("123" + "c" * 17),
        ),
        (tuple("abcdefghij"), "abcdefghi"),
    ],
)
def test_each_sentence_must_be_present_in_order_despite_the_global_error_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sentences: tuple[str, ...],
    transcript: str,
) -> None:
    source, approved, tools, asr = _arrange_boundary(tmp_path, monkeypatch)
    InjectedRecordedSpeechAnalyzer.next_result = RecordedSpeechAnalysis(
        duration_ms=5_000,
        speech_segments_ms=tuple(
            (100 + index * 400, 300 + index * 400) for index in range(len(sentences))
        ),
        transcript=transcript,
    )

    with pytest.raises(ScriptRecordingRejected):
        align_script_recording(
            _script(*sentences),
            source=source,
            approved=approved,
            tools=tools,
            vad_factory=object,
            asr_adapter=asr,
        )


@pytest.mark.parametrize(
    "facts",
    [
        MediaStreamFacts(
            kind=ProbedMaterialKind.VIDEO,
            duration_ms=5_000,
            width=640,
            height=360,
            video_codec="h264",
            audio_codec="aac",
        ),
        MediaStreamFacts(
            kind=ProbedMaterialKind.AUDIO,
            duration_ms=None,
            width=None,
            height=None,
            video_codec=None,
            audio_codec="aac",
        ),
    ],
)
def test_non_audio_or_missing_duration_is_rejected_before_local_analysis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    facts: MediaStreamFacts,
) -> None:
    source, approved, tools, asr = _arrange_boundary(
        tmp_path,
        monkeypatch,
        facts=facts,
    )

    with pytest.raises(ScriptRecordingRejected):
        align_script_recording(
            _script("一句"),
            source=source,
            approved=approved,
            tools=tools,
            vad_factory=object,
            asr_adapter=asr,
        )

    assert InjectedRecordedSpeechAnalyzer.constructions == []
    assert asr.calls == []


def test_non_speech_script_is_rejected_before_local_analysis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, approved, tools, asr = _arrange_boundary(tmp_path, monkeypatch)

    with pytest.raises(ScriptRecordingRejected):
        align_script_recording(
            _script("😀\uff01"),
            source=source,
            approved=approved,
            tools=tools,
            vad_factory=object,
            asr_adapter=asr,
        )

    assert InjectedRecordedSpeechAnalyzer.constructions == []
    assert asr.calls == []


def test_path_bearing_filesystem_failure_is_closed_without_an_exception_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, approved, tools, asr = _arrange_boundary(tmp_path, monkeypatch)

    def fail_probe(actual_tools: PackagedMediaTools, actual_source: Path) -> MediaStreamFacts:
        del actual_tools
        raise FileNotFoundError(actual_source)

    monkeypatch.setattr(script_recording, "read_stream_facts", fail_probe)

    with pytest.raises(ScriptRecordingRejected) as captured:
        align_script_recording(
            _script("一句"),
            source=source,
            approved=approved,
            tools=tools,
            vad_factory=object,
            asr_adapter=asr,
        )

    assert captured.value.__context__ is None
    assert os.fspath(source) not in str(captured.value)


def test_public_result_objects_reject_forged_ranges_and_sequences() -> None:
    sentence = ScriptSentence(sequence=1, text="一句")
    with pytest.raises(ScriptRecordingRejected):
        ScriptRecordedClip(
            sentence=sentence,
            source_start_ms=100,
            source_end_ms=100,
            duration_ms=0,
        )
    with pytest.raises(ScriptRecordingRejected):
        ScriptRecordedClip(
            sentence=sentence,
            source_start_ms=100,
            source_end_ms=200,
            duration_ms=99,
        )
    with pytest.raises(ScriptRecordingRejected):
        ScriptRecordingResult(
            script_request_id="script-request",
            recording_duration_ms=150,
            transcript="一句",
            clips=(
                ScriptRecordedClip(
                    sentence=sentence,
                    source_start_ms=100,
                    source_end_ms=200,
                    duration_ms=100,
                ),
            ),
        )
    valid_clip = ScriptRecordedClip(
        sentence=sentence,
        source_start_ms=100,
        source_end_ms=200,
        duration_ms=100,
    )
    with pytest.raises(ScriptRecordingRejected):
        ScriptRecordingResult(
            script_request_id="",
            recording_duration_ms=1_000,
            transcript="一句",
            clips=(valid_clip,),
        )
    with pytest.raises(ScriptRecordingRejected):
        ScriptRecordingResult(
            script_request_id="script-request",
            recording_duration_ms=0,
            transcript="一句",
            clips=(valid_clip,),
        )
    with pytest.raises(ScriptRecordingRejected):
        ScriptRecordingResult(
            script_request_id="script-request",
            recording_duration_ms=1_000,
            transcript="",
            clips=(valid_clip,),
        )
    with pytest.raises(ScriptRecordingRejected):
        ScriptRecordingResult(
            script_request_id="script-request",
            recording_duration_ms=1_000,
            transcript="一句",
            clips=[],  # type: ignore[arg-type]
        )
    with pytest.raises(ScriptRecordingRejected):
        ScriptRecordingResult(
            script_request_id="script-request",
            recording_duration_ms=1_000,
            transcript="一句",
            clips=(),
        )
    with pytest.raises(ScriptRecordingRejected):
        ScriptRecordingResult(
            script_request_id="script-request",
            recording_duration_ms=1_000,
            transcript="一句",
            clips=(
                ScriptRecordedClip(
                    sentence=ScriptSentence(sequence=2, text="二句"),
                    source_start_ms=100,
                    source_end_ms=200,
                    duration_ms=100,
                ),
            ),
        )
    with pytest.raises(ScriptRecordingRejected):
        ScriptRecordingResult(
            script_request_id="script-request",
            recording_duration_ms=1_000,
            transcript="完全不同",
            clips=(valid_clip,),
        )


def test_invalid_boundary_arguments_and_analyzer_results_are_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, approved, tools, asr = _arrange_boundary(tmp_path, monkeypatch)

    with pytest.raises(ScriptRecordingRejected):
        align_script_recording(
            _script("一句"),
            source="private.wav",  # type: ignore[arg-type]
            approved=approved,
            tools=tools,
            vad_factory=object,
            asr_adapter=asr,
        )

    InjectedRecordedSpeechAnalyzer.next_result = RecordedSpeechAnalysis(
        duration_ms=4_999,
        speech_segments_ms=((100, 4_000),),
        transcript="一句",
    )
    with pytest.raises(ScriptRecordingRejected):
        align_script_recording(
            _script("一句"),
            source=source,
            approved=approved,
            tools=tools,
            vad_factory=object,
            asr_adapter=asr,
        )

    InjectedRecordedSpeechAnalyzer.next_result = RecordedSpeechAnalysis(
        duration_ms=5_000,
        speech_segments_ms=((100, 4_000),),
        transcript="第一句第二句",
    )
    with pytest.raises(ScriptRecordingRejected):
        align_script_recording(
            _script("第一句", "第二句"),
            source=source,
            approved=approved,
            tools=tools,
            vad_factory=object,
            asr_adapter=asr,
        )
