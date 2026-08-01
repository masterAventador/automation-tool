"""Private two-phase Worker transaction for one smart-edit generation."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Never
from uuid import RFC_4122, UUID, uuid4

from automation_tool.control_plane.domain.material import (
    DescriptionSource,
    Material,
    MaterialId,
    MaterialKind,
)
from automation_tool.executor.local_editing_worker import (
    LocalEditingWorkerBootstrap,
    LocalSmartEditFailureCode,
    LocalSmartEditStartCommand,
)
from automation_tool.executor.material_probe import (
    MaterialPathRegistry,
    MaterialPathRegistryRejected,
    read_content_digest,
)
from automation_tool.executor.material_speech_pipeline import (
    LocalAudibleSpeechAnalyzerFactory,
)
from automation_tool.executor.material_speech_transcription import (
    BailianSpeechTranscriptionAdapter,
    load_bailian_speech_transcription_config,
)
from automation_tool.executor.material_understanding import (
    BailianMaterialUnderstandingAdapter,
    load_bailian_material_understanding_config,
)
from automation_tool.executor.motion_authoring.authoring_workspace import (
    AuthoringWorkspace,
)
from automation_tool.executor.motion_authoring.voiceover import (
    VoiceoverConfig,
    voiceover_config_from_catalog,
)
from automation_tool.executor.script_segmentation import (
    BailianScriptSegmentationAdapter,
    load_bailian_script_segmentation_config,
)
from automation_tool.executor.semantic_matching import (
    BailianSemanticMatchingAdapter,
    load_bailian_semantic_matching_config,
)
from automation_tool.executor.silero_vad import create_silero_vad
from automation_tool.executor.smart_edit_generation import (
    CancellationProbe,
    SmartEditGenerationCancelled,
    SmartEditGenerationFailure,
    SmartEditGenerationPipeline,
    SmartEditGenerationRejected,
    SmartEditGenerationResult,
    SmartEditGenerationStage,
    SmartEditMaterialAnalysis,
    SmartEditNarrationRegistration,
    generate_smart_edit_timeline_draft,
)
from automation_tool.executor.smart_edit_pipeline import LocalSmartEditGenerationPipeline

_REQUEST_SCHEMA = "smart-edit-generation-request.v1"
_RESULT_SCHEMA = "smart-edit-generation-result.v1"
_REQUEST_NAME = "request.json"
_RESULT_NAME = "result.json"
_STAGING_NAME = "staging"
_MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
_CATALOG_RELATIVE_PATH = Path("contracts/video/bailian-model-catalog.v1.json")
_MATERIAL_KEYS = {
    "aiDescription",
    "aiTags",
    "audioLoudnessLufs",
    "contentDigest",
    "describedAt",
    "descriptionSource",
    "durationMs",
    "hasAudio",
    "hasSpeech",
    "height",
    "kind",
    "materialId",
    "shotBoundariesMs",
    "speechSegmentsMs",
    "speechTranscript",
    "width",
}


class LocalSmartEditWorkerRejected(RuntimeError):
    def __init__(self, code: LocalSmartEditFailureCode) -> None:
        self.code = code
        super().__init__("smart edit worker rejected")

    def __repr__(self) -> str:
        return "LocalSmartEditWorkerRejected(<redacted>)"


class LocalSmartEditWorkerCancelled(RuntimeError):
    def __init__(self) -> None:
        super().__init__("smart edit worker cancelled")


def _reject(code: LocalSmartEditFailureCode) -> Never:
    raise LocalSmartEditWorkerRejected(code) from None


def _uuid(value: object) -> UUID:
    parsed: UUID | None = None
    if isinstance(value, str):
        with suppress(ValueError):
            parsed = UUID(value)
    if parsed is None or parsed.version != 4 or parsed.variant != RFC_4122 or str(parsed) != value:
        _reject(LocalSmartEditFailureCode.LOCAL_FAILED)
    return parsed


def _timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    parsed: datetime | None = None
    if isinstance(value, str):
        with suppress(ValueError):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed is None or parsed.tzinfo is None:
        _reject(LocalSmartEditFailureCode.LOCAL_FAILED)
    return parsed


def _tuple_of_ints(value: object) -> tuple[int, ...]:
    if not isinstance(value, list) or not all(type(item) is int for item in value):
        _reject(LocalSmartEditFailureCode.LOCAL_FAILED)
    return tuple(value)


def _speech_segments(value: object) -> tuple[tuple[int, int], ...]:
    if not isinstance(value, list):
        _reject(LocalSmartEditFailureCode.LOCAL_FAILED)
    segments: list[tuple[int, int]] = []
    for segment in value:
        if (
            not isinstance(segment, list)
            or len(segment) != 2
            or any(type(item) is not int for item in segment)
        ):
            _reject(LocalSmartEditFailureCode.LOCAL_FAILED)
        segments.append((segment[0], segment[1]))
    return tuple(segments)


def _material(value: object) -> Material:
    if not isinstance(value, dict) or set(value) != _MATERIAL_KEYS:
        _reject(LocalSmartEditFailureCode.LOCAL_FAILED)
    tags = value["aiTags"]
    if not isinstance(tags, list) or not all(type(tag) is str for tag in tags):
        _reject(LocalSmartEditFailureCode.LOCAL_FAILED)
    try:
        return Material.register(
            material_id=MaterialId(_uuid(value["materialId"])),
            kind=MaterialKind(value["kind"]),
            duration_ms=value["durationMs"],
            width=value["width"],
            height=value["height"],
            content_digest=value["contentDigest"],
            has_audio=value["hasAudio"],
            audio_loudness_lufs=value["audioLoudnessLufs"],
            has_speech=value["hasSpeech"],
            speech_segments_ms=_speech_segments(value["speechSegmentsMs"]),
            speech_transcript=value["speechTranscript"],
            shot_boundaries_ms=_tuple_of_ints(value["shotBoundariesMs"]),
            ai_description=value["aiDescription"],
            ai_tags=tuple(tags),
            description_source=DescriptionSource(value["descriptionSource"]),
            described_at=_timestamp(value["describedAt"]),
        )
    except LocalSmartEditWorkerRejected:
        raise
    except Exception:
        pass
    _reject(LocalSmartEditFailureCode.LOCAL_FAILED)


def _load_object(path: Path) -> dict[str, object]:
    payload: bytes | None = None
    try:
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or not 1 <= metadata.st_size <= _MAX_DOCUMENT_BYTES
        ):
            _reject(LocalSmartEditFailureCode.LOCAL_FAILED)
        payload = path.read_bytes()
        if len(payload) != metadata.st_size:
            _reject(LocalSmartEditFailureCode.LOCAL_FAILED)
    except LocalSmartEditWorkerRejected:
        raise
    except OSError:
        pass
    if payload is None:
        _reject(LocalSmartEditFailureCode.LOCAL_FAILED)

    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                _reject(LocalSmartEditFailureCode.LOCAL_FAILED)
            result[key] = value
        return result

    document: object = None
    with suppress(UnicodeError, json.JSONDecodeError):
        document = json.loads(payload, object_pairs_hook=no_duplicates)
    if not isinstance(document, dict):
        _reject(LocalSmartEditFailureCode.LOCAL_FAILED)
    return document


@dataclass(frozen=True, slots=True)
class _SmartEditRequest:
    prompt: str = field(repr=False)
    materials: tuple[Material, ...] = field(repr=False)
    enable_thinking: bool


def _request(path: Path, job_id: UUID) -> _SmartEditRequest:
    document = _load_object(path)
    if (
        set(document)
        != {
            "enableThinking",
            "jobId",
            "materials",
            "prompt",
            "schemaVersion",
        }
        or document.get("schemaVersion") != _REQUEST_SCHEMA
    ):
        _reject(LocalSmartEditFailureCode.LOCAL_FAILED)
    if _uuid(document.get("jobId")) != job_id:
        _reject(LocalSmartEditFailureCode.LOCAL_FAILED)
    prompt = document.get("prompt")
    materials = document.get("materials")
    enable_thinking = document.get("enableThinking")
    if (
        type(prompt) is not str
        or not prompt
        or prompt != prompt.strip()
        or len(prompt) > 4_000
        or not isinstance(materials, list)
        or not 1 <= len(materials) <= 32
        or type(enable_thinking) is not bool
    ):
        _reject(LocalSmartEditFailureCode.LOCAL_FAILED)
    return _SmartEditRequest(
        prompt=prompt,
        materials=tuple(_material(value) for value in materials),
        enable_thinking=enable_thinking,
    )


def _analysis(update: SmartEditMaterialAnalysis) -> dict[str, object]:
    return {
        "aiDescription": update.ai_description,
        "aiTags": list(update.ai_tags),
        "contentDigest": update.content_digest,
        "describedAt": (
            update.described_at.isoformat().replace("+00:00", "Z")
            if update.described_at is not None
            else None
        ),
        "descriptionSource": update.description_source.value,
        "hasSpeech": update.has_speech,
        "materialId": str(update.material_id),
        "shotBoundariesMs": list(update.shot_boundaries_ms),
        "speechSegmentsMs": [list(segment) for segment in update.speech_segments_ms],
        "speechTranscript": update.speech_transcript,
    }


def _narration(value: SmartEditNarrationRegistration) -> dict[str, object]:
    return {
        "bytesWritten": value.bytes_written,
        "contentDigest": value.content_digest,
        "durationMs": value.duration_ms,
        "materialId": str(value.material_id),
        "relativePath": value.relative_path,
        "sequence": value.sequence,
    }


def _result_document(job_id: UUID, result: SmartEditGenerationResult) -> dict[str, object]:
    return {
        "analysisUpdates": [_analysis(value) for value in result.analysis_updates],
        "draft": {
            "durationMs": result.draft.duration_ms,
            "paragraphs": [
                {
                    "audioMaterialId": str(paragraph.audio_material_id),
                    "captionText": paragraph.caption_text,
                    "durationMs": paragraph.duration_ms,
                    "kind": paragraph.kind.value,
                    "sequence": paragraph.sequence,
                    "visualMaterialId": str(paragraph.visual_material_id),
                    "visualSourceInMs": paragraph.visual_source_in_ms,
                    "visualSourceOutMs": paragraph.visual_source_out_ms,
                }
                for paragraph in result.draft.paragraphs
            ],
        },
        "jobId": str(job_id),
        "narrationRegistrations": [_narration(value) for value in result.narration_registrations],
        "schemaVersion": _RESULT_SCHEMA,
    }


def _private_directory(path: Path) -> None:
    created = False
    try:
        path.mkdir(parents=True, mode=0o700, exist_ok=False)
        created = True
        if os.name != "nt":
            path.chmod(0o700)
    except OSError:
        pass
    if not created:
        _reject(LocalSmartEditFailureCode.WORKSPACE_UNUSABLE)
    _require_private_directory(path, LocalSmartEditFailureCode.WORKSPACE_UNUSABLE)


def _require_private_directory(path: Path, code: LocalSmartEditFailureCode) -> None:
    usable = False
    try:
        metadata = path.lstat()
        usable = (
            not path.is_symlink()
            and stat.S_ISDIR(metadata.st_mode)
            and path.resolve(strict=True) == Path(os.path.abspath(path))
            and (os.name == "nt" or stat.S_IMODE(metadata.st_mode) & 0o077 == 0)
        )
    except OSError:
        pass
    if not usable:
        _reject(code)


@dataclass(slots=True, repr=False)
class LocalSmartEditStagedJob:
    job_id: UUID
    job_root: Path = field(repr=False)
    workspace_root: Path = field(repr=False)
    response_path: Path = field(repr=False)
    result_digest: str
    narration_registrations: tuple[SmartEditNarrationRegistration, ...] = field(repr=False)
    finalized: bool = field(default=False, init=False, repr=False)

    def __repr__(self) -> str:
        return "LocalSmartEditStagedJob(<redacted>)"


def _job_root(bootstrap: LocalEditingWorkerBootstrap, job_id: UUID) -> Path:
    return bootstrap.asset_root / "local-executor" / "smart-edit" / "jobs" / str(job_id)


def _cleanup_job(job_root: Path) -> bool:
    with suppress(OSError):
        shutil.rmtree(job_root)
    try:
        job_root.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _failure_code(value: SmartEditGenerationFailure) -> LocalSmartEditFailureCode:
    return LocalSmartEditFailureCode(value.code.value)


def _catalog_path() -> Path:
    frozen = getattr(sys, "_MEIPASS", None)
    root = Path(frozen) if isinstance(frozen, str) else Path(__file__).resolve().parents[4]
    return root / _CATALOG_RELATIVE_PATH


def create_local_smart_edit_pipeline(
    bootstrap: LocalEditingWorkerBootstrap,
    workspace: AuthoringWorkspace,
) -> LocalSmartEditGenerationPipeline:
    """Build the shipped provider adapters without exposing the bootstrap key."""

    if (
        not isinstance(bootstrap, LocalEditingWorkerBootstrap)
        or bootstrap.script_model is None
        or not isinstance(workspace, AuthoringWorkspace)
    ):
        _reject(LocalSmartEditFailureCode.CONFIGURATION_MISSING)
    model = bootstrap.script_model
    catalog = _catalog_path()
    understanding: BailianMaterialUnderstandingAdapter | None = None
    transcription: BailianSpeechTranscriptionAdapter | None = None
    script: BailianScriptSegmentationAdapter | None = None
    semantic: BailianSemanticMatchingAdapter | None = None
    voiceover: VoiceoverConfig | None = None
    registry: MaterialPathRegistry | None = None
    try:
        understanding = BailianMaterialUnderstandingAdapter(
            load_bailian_material_understanding_config(
                catalog_path=catalog,
                api_key=model.api_key,
                timeout_seconds=120,
            )
        )
        transcription = BailianSpeechTranscriptionAdapter(
            load_bailian_speech_transcription_config(
                catalog_path=catalog,
                api_key=model.api_key,
                timeout_seconds=90,
            )
        )
        script = BailianScriptSegmentationAdapter(
            load_bailian_script_segmentation_config(
                catalog_path=catalog,
                api_key=model.api_key,
                model_id=model.model_id,
                timeout_seconds=120,
            )
        )
        semantic = BailianSemanticMatchingAdapter(
            load_bailian_semantic_matching_config(
                catalog_path=catalog,
                api_key=model.api_key,
                model_id=model.model_id,
                timeout_seconds=120,
            )
        )
        voiceover = voiceover_config_from_catalog(
            catalog_path=catalog,
            api_key=model.api_key,
        )
        registry = _state_registry(bootstrap)
    except LocalSmartEditWorkerRejected:
        raise
    except Exception:
        pass
    if (
        understanding is None
        or transcription is None
        or script is None
        or semantic is None
        or voiceover is None
        or registry is None
    ):
        _reject(LocalSmartEditFailureCode.CONFIGURATION_MISSING)

    def audible_factory(source: Path, approved: os.stat_result) -> object:
        return LocalAudibleSpeechAnalyzerFactory(
            tools=bootstrap.media_tools,
            source=source,
            approved=approved,
            vad_factory=create_silero_vad,
            asr_adapter=transcription,
        )

    return LocalSmartEditGenerationPipeline(
        tools=bootstrap.media_tools,
        registry=registry,
        workspace=workspace,
        understanding_adapter=understanding,
        audible_speech_analyzer_factory=audible_factory,
        script_adapter=script,
        semantic_adapter=semantic,
        voiceover_config=voiceover,
        now=lambda: datetime.now(UTC),
        material_id_factory=uuid4,
    )


def prepare_smart_edit_job(
    bootstrap: LocalEditingWorkerBootstrap,
    command: LocalSmartEditStartCommand,
    *,
    pipeline_factory: Callable[[AuthoringWorkspace], SmartEditGenerationPipeline],
    cancel_requested: CancellationProbe,
    progress: Callable[[SmartEditGenerationStage, int], None],
) -> LocalSmartEditStagedJob:
    if (
        not isinstance(bootstrap, LocalEditingWorkerBootstrap)
        or not isinstance(command, LocalSmartEditStartCommand)
        or not callable(pipeline_factory)
        or not callable(cancel_requested)
        or not callable(progress)
    ):
        _reject(LocalSmartEditFailureCode.LOCAL_FAILED)
    job_root = _job_root(bootstrap, command.job_id)
    request_path = job_root / _REQUEST_NAME
    workspace_root = job_root / _STAGING_NAME
    failed_code: LocalSmartEditFailureCode | None = None
    cancelled = False
    staged: LocalSmartEditStagedJob | None = None
    try:
        _require_private_directory(
            job_root,
            LocalSmartEditFailureCode.WORKSPACE_UNUSABLE,
        )
        request = _request(request_path, command.job_id)
        _private_directory(workspace_root)
        workspace = AuthoringWorkspace(workspace_root)
        pipeline = pipeline_factory(workspace)
        if not isinstance(pipeline, SmartEditGenerationPipeline):
            _reject(LocalSmartEditFailureCode.CONFIGURATION_MISSING)
        outcome = generate_smart_edit_timeline_draft(
            pipeline,
            prompt=request.prompt,
            materials=request.materials,
            enable_thinking=request.enable_thinking,
            progress=progress,
            cancellation_requested=cancel_requested,
        )
        if isinstance(outcome, SmartEditGenerationFailure):
            failed_code = _failure_code(outcome)
        elif isinstance(outcome, SmartEditGenerationResult):
            document = _result_document(command.job_id, outcome)
            payload = json.dumps(
                document,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            if not 1 <= len(payload) <= _MAX_DOCUMENT_BYTES:
                _reject(LocalSmartEditFailureCode.LOCAL_FAILED)
            response_path = job_root / _RESULT_NAME
            descriptor = os.open(
                response_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            try:
                with os.fdopen(descriptor, "wb", closefd=True) as sink:
                    descriptor = -1
                    sink.write(payload)
                    sink.flush()
                    os.fsync(sink.fileno())
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
            staged = LocalSmartEditStagedJob(
                job_id=command.job_id,
                job_root=job_root,
                workspace_root=workspace_root,
                response_path=response_path,
                result_digest=hashlib.sha256(payload).hexdigest(),
                narration_registrations=outcome.narration_registrations,
            )
        else:
            failed_code = LocalSmartEditFailureCode.LOCAL_FAILED
    except SmartEditGenerationCancelled:
        cancelled = True
    except LocalSmartEditWorkerRejected as error:
        failed_code = error.code
    except SmartEditGenerationRejected:
        failed_code = LocalSmartEditFailureCode.UPSTREAM_REJECTED
    except Exception:
        failed_code = LocalSmartEditFailureCode.LOCAL_FAILED
    if staged is not None:
        return staged
    if not _cleanup_job(job_root):
        _reject(LocalSmartEditFailureCode.WORKSPACE_UNUSABLE)
    if cancelled:
        raise LocalSmartEditWorkerCancelled from None
    _reject(failed_code or LocalSmartEditFailureCode.LOCAL_FAILED)


def _state_registry(bootstrap: LocalEditingWorkerBootstrap) -> MaterialPathRegistry:
    state = bootstrap.asset_root / "local-executor" / "state"
    if not state.exists():
        _private_directory(state)
    try:
        return MaterialPathRegistry(state_directory=state)
    except MaterialPathRegistryRejected:
        _reject(LocalSmartEditFailureCode.COMMIT_FAILED)


def _verify_staged_narration(staged: LocalSmartEditStagedJob) -> None:
    for registration in staged.narration_registrations:
        source = staged.workspace_root.joinpath(*registration.relative_path.split("/"))
        valid = False
        try:
            metadata = source.lstat()
            valid = (
                source.resolve(strict=True).is_relative_to(
                    staged.workspace_root.resolve(strict=True)
                )
                and not source.is_symlink()
                and stat.S_ISREG(metadata.st_mode)
                and metadata.st_size == registration.bytes_written
                and read_content_digest(source) == registration.content_digest
            )
            if not valid:
                _reject(LocalSmartEditFailureCode.COMMIT_FAILED)
        except LocalSmartEditWorkerRejected:
            raise
        except Exception:
            pass
        if not valid:
            _reject(LocalSmartEditFailureCode.COMMIT_FAILED)


def _verify_staged_result(staged: LocalSmartEditStagedJob) -> None:
    valid = False
    try:
        metadata = staged.response_path.lstat()
        valid = (
            staged.response_path.resolve(strict=True).is_relative_to(
                staged.job_root.resolve(strict=True)
            )
            and not staged.response_path.is_symlink()
            and stat.S_ISREG(metadata.st_mode)
            and 1 <= metadata.st_size <= _MAX_DOCUMENT_BYTES
            and read_content_digest(staged.response_path) == staged.result_digest
        )
    except Exception:
        pass
    if not valid:
        _reject(LocalSmartEditFailureCode.COMMIT_FAILED)


def commit_smart_edit_job(
    bootstrap: LocalEditingWorkerBootstrap,
    staged: LocalSmartEditStagedJob,
) -> None:
    if (
        not isinstance(bootstrap, LocalEditingWorkerBootstrap)
        or not isinstance(staged, LocalSmartEditStagedJob)
        or staged.finalized
    ):
        _reject(LocalSmartEditFailureCode.COMMIT_FAILED)
    _require_private_directory(staged.job_root, LocalSmartEditFailureCode.COMMIT_FAILED)
    _verify_staged_result(staged)
    _verify_staged_narration(staged)
    if not staged.narration_registrations:
        if not _cleanup_job(staged.job_root):
            _reject(LocalSmartEditFailureCode.COMMIT_FAILED)
        staged.finalized = True
        return
    generated_parent = bootstrap.asset_root / "local-executor" / "generated-materials"
    if not generated_parent.exists():
        _private_directory(generated_parent)
    _require_private_directory(generated_parent, LocalSmartEditFailureCode.COMMIT_FAILED)
    durable = generated_parent / str(staged.job_id)
    moved = False
    committed = False
    try:
        if durable.exists() or durable.is_symlink():
            _reject(LocalSmartEditFailureCode.COMMIT_FAILED)
        os.replace(staged.workspace_root, durable)
        moved = True
        mappings = tuple(
            (
                registration.material_id,
                durable.joinpath(*registration.relative_path.split("/")),
            )
            for registration in staged.narration_registrations
        )
        _state_registry(bootstrap).register_many(mappings)
        committed = True
    except LocalSmartEditWorkerRejected:
        pass
    except (MaterialPathRegistryRejected, OSError):
        pass
    if not committed:
        if moved:
            rolled_back = False
            try:
                os.replace(durable, staged.workspace_root)
                rolled_back = True
            except OSError:
                pass
            if not rolled_back:
                with suppress(OSError):
                    shutil.rmtree(durable)
        _reject(LocalSmartEditFailureCode.COMMIT_FAILED)
    staged.finalized = True
    _cleanup_job(staged.job_root)


def abort_smart_edit_job(staged: LocalSmartEditStagedJob) -> None:
    if not isinstance(staged, LocalSmartEditStagedJob) or staged.finalized:
        _reject(LocalSmartEditFailureCode.LOCAL_FAILED)
    if not _cleanup_job(staged.job_root):
        _reject(LocalSmartEditFailureCode.WORKSPACE_UNUSABLE)
    staged.finalized = True


__all__ = [
    "LocalSmartEditStagedJob",
    "LocalSmartEditWorkerCancelled",
    "LocalSmartEditWorkerRejected",
    "abort_smart_edit_job",
    "commit_smart_edit_job",
    "create_local_smart_edit_pipeline",
    "prepare_smart_edit_job",
]
