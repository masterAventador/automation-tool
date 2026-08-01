"""Synthesize one audio clip per segmented script sentence."""

from __future__ import annotations

import stat
import unicodedata
from collections.abc import Callable
from contextlib import suppress
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


class ScriptVoiceoverCancelled(RuntimeError):
    """The caller cooperatively stopped a batch between sentences."""

    def __init__(self) -> None:
        super().__init__("script voiceover cancelled")


def _reject() -> NoReturn:
    raise ScriptVoiceoverRejected from None


def _never_cancel() -> bool:
    return False


def _cancel_if_requested(cancellation_requested: Callable[[], bool]) -> None:
    requested: object = None
    with suppress(Exception):
        requested = cancellation_requested()
    if type(requested) is not bool:
        _reject()
    if requested:
        raise ScriptVoiceoverCancelled from None


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
    parent_metadata = None
    parent_unreadable = False
    try:
        parent_metadata = expected.parent.lstat()
    except FileNotFoundError:
        pass
    except OSError:
        parent_unreadable = True
    if parent_unreadable or (
        parent_metadata is not None
        and (not stat.S_ISDIR(parent_metadata.st_mode) or stat.S_ISLNK(parent_metadata.st_mode))
    ):
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
    metadata = None
    resolved = None
    try:
        metadata = output_path.lstat()
        resolved = output_path.resolve(strict=True)
    except OSError:
        pass
    if metadata is None or resolved is None:
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
    cancellation_requested: Callable[[], bool] = _never_cancel,
) -> ScriptVoiceoverResult:
    """Synthesize and probe every sentence, or leave no audio from the batch."""

    if (
        not isinstance(script, ScriptSegmentationResult)
        or not isinstance(config, VoiceoverConfig)
        or not isinstance(workspace, AuthoringWorkspace)
        or not isinstance(tools, PackagedMediaTools)
        or not callable(cancellation_requested)
    ):
        _reject()

    clips: list[ScriptVoiceoverClip] = []
    batch_workspace: AuthoringWorkspace | None = None
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
        # A scoped view snapshots everything that predates this call as seeded
        # and therefore rolls back only files authored by this batch.
        batch_workspace = AuthoringWorkspace(workspace.root)
        for sentence, relative_path, output_path in planned_outputs:
            _cancel_if_requested(cancellation_requested)
            # Close the window between the batch preflight and this sentence.
            output_path = _fresh_output_path(batch_workspace, relative_path)
            synthesized = _require_written_audio(
                synthesize_voiceover(
                    config,
                    sentence.text,
                    workspace=batch_workspace,
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
    except (ScriptVoiceoverCancelled, ScriptVoiceoverRejected):
        if batch_workspace is not None:
            batch_workspace.rollback_authored_files()
        raise
    except Exception:
        if batch_workspace is not None:
            batch_workspace.rollback_authored_files()
    _reject()


__all__ = [
    "ScriptVoiceoverCancelled",
    "ScriptVoiceoverClip",
    "ScriptVoiceoverRejected",
    "ScriptVoiceoverResult",
    "synthesize_script_voiceovers",
]
