"""Synthesize one audio clip per segmented script sentence."""

from __future__ import annotations

import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NoReturn

from automation_tool.executor.material_probe import (
    MAX_MATERIAL_DURATION_MS,
    MediaStreamFacts,
    PackagedMediaTools,
    ProbedMaterialKind,
    read_stream_facts,
)
from automation_tool.executor.motion_authoring.agent import AuthoringWorkspace
from automation_tool.executor.motion_authoring.voiceover import (
    MAX_VOICEOVER_BYTES,
    SynthesizedVoiceover,
    VoiceoverConfig,
    synthesize_voiceover,
)
from automation_tool.executor.script_segmentation import (
    MAX_SCRIPT_SENTENCES,
    ScriptSegmentationResult,
    ScriptSentence,
)

_MAX_REQUEST_ID_CHARACTERS: Final = 512
_OUTPUT_DIRECTORY: Final = "voiceover"


class ScriptVoiceoverRejected(RuntimeError):
    """The segmented-script voiceover boundary rejected one complete batch."""

    def __init__(self) -> None:
        super().__init__("script voiceover request rejected")


def _reject() -> NoReturn:
    raise ScriptVoiceoverRejected from None


def _relative_path(sequence: int) -> str:
    return f"{_OUTPUT_DIRECTORY}/sentence-{sequence:04d}.wav"


def _valid_request_id(value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and value == value.strip()
        and len(value) <= _MAX_REQUEST_ID_CHARACTERS
        and not any(unicodedata.category(character).startswith("C") for character in value)
    )


@dataclass(frozen=True, slots=True)
class ScriptVoiceoverClip:
    """One script sentence and the measured audio written for it."""

    sentence: ScriptSentence
    relative_path: str
    duration_ms: int
    bytes_written: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.sentence, ScriptSentence)
            or type(self.relative_path) is not str
            or self.relative_path != _relative_path(self.sentence.sequence)
            or type(self.duration_ms) is not int
            or not 1 <= self.duration_ms <= MAX_MATERIAL_DURATION_MS
            or type(self.bytes_written) is not int
            or not 1 <= self.bytes_written <= MAX_VOICEOVER_BYTES
        ):
            _reject()


@dataclass(frozen=True, slots=True)
class ScriptVoiceoverResult:
    """One all-or-nothing voiceover batch, carrying no host paths."""

    script_request_id: str
    clips: tuple[ScriptVoiceoverClip, ...]

    def __post_init__(self) -> None:
        if (
            not _valid_request_id(self.script_request_id)
            or not isinstance(self.clips, tuple)
            or not 1 <= len(self.clips) <= MAX_SCRIPT_SENTENCES
            or not all(isinstance(clip, ScriptVoiceoverClip) for clip in self.clips)
            or tuple(clip.sentence.sequence for clip in self.clips)
            != tuple(range(1, len(self.clips) + 1))
        ):
            _reject()


def _fresh_output_path(workspace: AuthoringWorkspace, relative_path: str) -> Path:
    expected = workspace.root.joinpath(*relative_path.split("/"))
    resolved = workspace.resolve(relative_path)
    if resolved != expected or expected.is_symlink() or expected.exists():
        _reject()
    return expected


def _require_written_audio(
    synthesized: object,
    *,
    relative_path: str,
    output_path: Path,
) -> SynthesizedVoiceover:
    if (
        not isinstance(synthesized, SynthesizedVoiceover)
        or synthesized.relative_path != relative_path
        or type(synthesized.bytes_written) is not int
        or not 1 <= synthesized.bytes_written <= MAX_VOICEOVER_BYTES
    ):
        _reject()
    try:
        metadata = output_path.lstat()
        resolved = output_path.resolve(strict=True)
    except OSError:
        _reject()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or output_path.is_symlink()
        or resolved != output_path
        or metadata.st_size != synthesized.bytes_written
    ):
        _reject()
    return synthesized


def _require_audio_facts(facts: object) -> MediaStreamFacts:
    if (
        not isinstance(facts, MediaStreamFacts)
        or facts.kind is not ProbedMaterialKind.AUDIO
        or type(facts.duration_ms) is not int
        or not 1 <= facts.duration_ms <= MAX_MATERIAL_DURATION_MS
    ):
        _reject()
    return facts


def synthesize_script_voiceovers(
    script: ScriptSegmentationResult,
    *,
    config: VoiceoverConfig,
    workspace: AuthoringWorkspace,
    tools: PackagedMediaTools,
) -> ScriptVoiceoverResult:
    """Synthesize and probe every sentence, or leave no audio from the batch."""

    if (
        not isinstance(script, ScriptSegmentationResult)
        or not isinstance(config, VoiceoverConfig)
        or not isinstance(workspace, AuthoringWorkspace)
        or not isinstance(tools, PackagedMediaTools)
    ):
        _reject()

    clips: list[ScriptVoiceoverClip] = []
    try:
        # Prove every local precondition before the first billable cloud call.
        # `read_stream_facts` revalidates the tools again at the moment of use;
        # this first pass exists so a missing tool cannot waste one TTS result.
        tools.revalidate()
        planned_outputs = tuple(
            (
                sentence,
                _relative_path(sentence.sequence),
                _fresh_output_path(
                    workspace,
                    _relative_path(sentence.sequence),
                ),
            )
            for sentence in script.sentences
        )
        for sentence, relative_path, output_path in planned_outputs:
            # Close the window between the batch preflight and this sentence.
            output_path = _fresh_output_path(workspace, relative_path)
            synthesized = _require_written_audio(
                synthesize_voiceover(
                    config,
                    sentence.text,
                    workspace=workspace,
                    relative_path=relative_path,
                ),
                relative_path=relative_path,
                output_path=output_path,
            )
            facts = _require_audio_facts(read_stream_facts(tools, output_path))
            assert facts.duration_ms is not None
            clips.append(
                ScriptVoiceoverClip(
                    sentence=sentence,
                    relative_path=relative_path,
                    duration_ms=facts.duration_ms,
                    bytes_written=synthesized.bytes_written,
                )
            )
        return ScriptVoiceoverResult(
            script_request_id=script.request_id,
            clips=tuple(clips),
        )
    except ScriptVoiceoverRejected:
        workspace.rollback_authored_files()
        raise
    except Exception:
        workspace.rollback_authored_files()
    _reject()


__all__ = [
    "ScriptVoiceoverClip",
    "ScriptVoiceoverRejected",
    "ScriptVoiceoverResult",
    "synthesize_script_voiceovers",
]
