"""Authenticated stdin bootstrap for the packaged local-editing Worker."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Never
from uuid import RFC_4122, UUID

from automation_tool.executor.material_probe import (
    MAX_MATERIAL_DIMENSION,
    MAX_MATERIAL_DURATION_MS,
    MAX_PATH_CHARACTERS,
    MaterialFacts,
    MaterialPathRegistryRejection,
    MaterialProbeRejected,
    PackagedMediaTools,
    ProbedMaterialKind,
)
from automation_tool.executor.smart_edit_generation import SmartEditGenerationStage
from automation_tool.protocol.safe_text import contains_control_or_bidi

MAX_LOCAL_EDITING_WORKER_BOOTSTRAP_BYTES = 16 * 1024
_TOKEN_PATTERN = re.compile(r"^[0-9a-f]{64}\Z")
_API_KEY_PATTERN = re.compile(r"^sk-[A-Za-z0-9._-]{17,253}\Z")
_SCRIPT_MODEL_KEYS = frozenset(
    {"apiKey", "baseUrl", "modelId", "sourceProvider", "upstreamProvider"}
)
_SCRIPT_MODEL_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_SCRIPT_MODEL_IDS = frozenset({"deepseek-v4-pro", "glm-5.2", "qwen3.7-max-2026-06-08"})
_ROOT_KEYS = frozenset(
    {
        "assetRoot",
        "bootstrapVersion",
        "enableWebUi",
        "localSessionToken",
        "mediaTools",
        "pexelsApiKey",
        "protocolVersion",
        "renderBrowser",
        "scriptModel",
        "workerKind",
    }
)
_MEDIA_TOOL_KEYS = frozenset({"ffmpegPath", "ffprobePath"})
_START_COMMAND_KEYS = frozenset(
    {
        "authenticationProof",
        "command",
        "editing",
        "jobId",
        "protocolVersion",
        "workerKind",
    }
)
_CANCEL_COMMAND_KEYS = frozenset(
    {
        "authenticationProof",
        "command",
        "jobId",
        "protocolVersion",
        "workerKind",
    }
)
_MATERIAL_IMPORT_COMMAND_KEYS = frozenset(
    {
        "authenticationProof",
        "command",
        "materialId",
        "protocolVersion",
        "sourcePath",
        "workerKind",
    }
)
_MATERIAL_FORGET_COMMAND_KEYS = frozenset(
    {
        "authenticationProof",
        "command",
        "materialId",
        "protocolVersion",
        "workerKind",
    }
)
_MATERIAL_STATUS_COMMAND_KEYS = _MATERIAL_FORGET_COMMAND_KEYS
_SMART_EDIT_COMMAND_KEYS = _CANCEL_COMMAND_KEYS
_EDITING_KEYS = frozenset({"projectId", "timelineId", "timelineRevision"})
_COMMAND_AUTHENTICATION_DOMAIN = b"automation-tool.video-worker-command.v1\0"
_EVENT_AUTHENTICATION_DOMAIN = b"automation-tool.video-worker-event.v1\0"
_COMMAND_PROOF_PREFIX = "atvwc1."
_EVENT_PROOF_PREFIX = "atvwp1."
_PROTOCOL_VERSION = "1.0"
_WORKER_KIND = "python"
_SEMANTIC_VERSION_PATTERN = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-(?:(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)


class LocalEditingWorkerBootstrapRejected(ValueError):
    """The Worker bootstrap is malformed or names unverified local tools."""

    def __init__(self) -> None:
        super().__init__("local editing worker bootstrap rejected")


def _reject() -> Never:
    raise LocalEditingWorkerBootstrapRejected from None


def _load_object(payload: bytes) -> dict[str, object]:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        document: dict[str, object] = {}
        for key, value in pairs:
            if key in document:
                _reject()
            document[key] = value
        return document

    try:
        value = json.loads(payload, object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _reject()
    if not isinstance(value, dict):
        _reject()
    return value


def _asset_root(value: object) -> Path:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= MAX_PATH_CHARACTERS
        or contains_control_or_bidi(value)
    ):
        _reject()
    path = Path(value)
    try:
        if not path.is_absolute() or path.is_symlink() or not path.is_dir():
            _reject()
        if any(ancestor.is_symlink() for ancestor in path.parents):
            _reject()
        return path.resolve(strict=True)
    except OSError:
        _reject()


def _media_tools(value: object) -> PackagedMediaTools:
    if not isinstance(value, dict) or set(value) != _MEDIA_TOOL_KEYS:
        _reject()
    ffmpeg = value.get("ffmpegPath")
    ffprobe = value.get("ffprobePath")
    if not isinstance(ffmpeg, str) or not isinstance(ffprobe, str) or ffmpeg == ffprobe:
        _reject()
    try:
        return PackagedMediaTools(
            ffprobe_path=Path(ffprobe),
            ffmpeg_path=Path(ffmpeg),
        )
    except MaterialProbeRejected:
        _reject()


@dataclass(frozen=True, slots=True, repr=False)
class LocalEditingScriptModelConfiguration:
    base_url: str
    model_id: str
    api_key: str = field(repr=False)

    def __repr__(self) -> str:
        return (
            "LocalEditingScriptModelConfiguration("
            f"base_url={self.base_url!r}, model_id={self.model_id!r}, api_key=<redacted>)"
        )


def _script_model(value: object) -> LocalEditingScriptModelConfiguration | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != _SCRIPT_MODEL_KEYS:
        _reject()
    api_key = value.get("apiKey")
    model_id = value.get("modelId")
    if (
        value.get("sourceProvider") != "bailian"
        or value.get("upstreamProvider") != "openai"
        or value.get("baseUrl") != _SCRIPT_MODEL_BASE_URL
        or type(model_id) is not str
        or model_id not in _SCRIPT_MODEL_IDS
        or type(api_key) is not str
        or _API_KEY_PATTERN.fullmatch(api_key) is None
    ):
        _reject()
    return LocalEditingScriptModelConfiguration(
        base_url=_SCRIPT_MODEL_BASE_URL,
        model_id=model_id,
        api_key=api_key,
    )


def _uuid_v4(value: object) -> UUID:
    if not isinstance(value, str):
        _reject()
    try:
        parsed = UUID(value)
    except ValueError:
        _reject()
    if str(parsed) != value or parsed.version != 4 or parsed.variant != RFC_4122:
        _reject()
    return parsed


def _source_path(value: object) -> Path:
    """Accept only one canonical absolute path without touching the filesystem."""

    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= MAX_PATH_CHARACTERS
        or contains_control_or_bidi(value)
    ):
        _reject()
    path = Path(value)
    if not path.is_absolute() or str(path) != value:
        _reject()
    return path


def _material_facts_document(facts: object) -> dict[str, object]:
    """Project trusted probe facts onto the smaller, path-free CP registration shape."""

    if not isinstance(facts, MaterialFacts) or not isinstance(facts.kind, ProbedMaterialKind):
        _reject()
    duration = facts.duration_ms
    width = facts.width
    height = facts.height
    loudness = facts.audio_loudness_lufs
    if (
        not isinstance(facts.content_digest, str)
        or _TOKEN_PATTERN.fullmatch(facts.content_digest) is None
        or type(facts.has_audio) is not bool
        or (loudness is not None and (type(loudness) is not float or not math.isfinite(loudness)))
        or (loudness is not None and not -70.0 <= loudness <= 0.0)
    ):
        _reject()
    if facts.kind is ProbedMaterialKind.IMAGE:
        if duration is not None or facts.has_audio or loudness is not None:
            _reject()
    elif type(duration) is not int or not 1 <= duration <= MAX_MATERIAL_DURATION_MS:
        _reject()
    if facts.kind is ProbedMaterialKind.AUDIO:
        if width is not None or height is not None or not facts.has_audio:
            _reject()
    elif any(
        type(value) is not int or not 1 <= value <= MAX_MATERIAL_DIMENSION
        for value in (width, height)
    ):
        _reject()
    if not facts.has_audio and loudness is not None:
        _reject()
    return {
        "audioLoudnessLufs": loudness,
        "contentDigest": facts.content_digest,
        "durationMs": duration,
        "hasAudio": facts.has_audio,
        "height": height,
        "kind": facts.kind.value,
        "width": width,
    }


def _line_object(payload: bytes) -> dict[str, object]:
    if (
        not isinstance(payload, bytes)
        or not 1 < len(payload) <= MAX_LOCAL_EDITING_WORKER_BOOTSTRAP_BYTES
        or not payload.endswith(b"\n")
        or payload.count(b"\n") != 1
    ):
        _reject()
    return _load_object(payload[:-1])


def _authentication_proof(
    token: bytes,
    domain: bytes,
    prefix: str,
    parts: tuple[str, ...],
) -> str:
    message = domain + b"\0".join(part.encode() for part in parts)
    digest = hmac.digest(token, message, hashlib.sha256)
    return prefix + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


@dataclass(frozen=True, slots=True, repr=False)
class LocalEditingWorkerBootstrap:
    asset_root: Path = field(repr=False)
    media_tools: PackagedMediaTools = field(repr=False)
    _session_token: bytes = field(repr=False)
    script_model: LocalEditingScriptModelConfiguration | None = field(
        default=None,
        repr=False,
    )

    def session_token_bytes(self) -> bytes:
        return self._session_token

    def __repr__(self) -> str:
        return "LocalEditingWorkerBootstrap(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class LocalEditingStartCommand:
    job_id: UUID
    project_id: UUID
    timeline_id: UUID
    timeline_revision: int

    def __repr__(self) -> str:
        return "LocalEditingStartCommand(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class LocalEditingCancelCommand:
    job_id: UUID

    def __repr__(self) -> str:
        return "LocalEditingCancelCommand(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class LocalSmartEditStartCommand:
    job_id: UUID

    def __repr__(self) -> str:
        return "LocalSmartEditStartCommand(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class LocalSmartEditCommitCommand:
    job_id: UUID

    def __repr__(self) -> str:
        return "LocalSmartEditCommitCommand(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class LocalSmartEditAbortCommand:
    job_id: UUID

    def __repr__(self) -> str:
        return "LocalSmartEditAbortCommand(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class LocalMaterialImportCommand:
    """One local-only source selection; its path must never reach repr or stdout."""

    material_id: UUID
    source_path: Path = field(repr=False)

    def __repr__(self) -> str:
        return "LocalMaterialImportCommand(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class LocalMaterialForgetCommand:
    material_id: UUID

    def __repr__(self) -> str:
        return "LocalMaterialForgetCommand(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class LocalMaterialStatusCommand:
    material_id: UUID

    def __repr__(self) -> str:
        return "LocalMaterialStatusCommand(<redacted>)"


class LocalEditingWorkerPhase(StrEnum):
    PREPARING = "preparing"
    RENDERING = "rendering"
    PUBLISHING = "publishing"


class LocalEditingWorkerFailureCode(StrEnum):
    INVALID_TIMELINE = "invalid_timeline"
    MATERIAL_UNAVAILABLE = "material_unavailable"
    MATERIAL_UNSUPPORTED = "material_unsupported"
    FONT_UNAVAILABLE = "font_unavailable"
    RENDER_FAILED = "render_failed"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    PERMISSION_DENIED = "permission_denied"
    WORKSPACE_UNUSABLE = "workspace_unusable"


class LocalSmartEditFailureCode(StrEnum):
    INSUFFICIENT_MATERIALS = "insufficient_materials"
    SOURCE_TOO_SHORT = "source_too_short"
    NO_RELEVANT_MATERIAL = "no_relevant_material"
    CONFIGURATION_MISSING = "configuration_missing"
    UPSTREAM_REJECTED = "upstream_rejected"
    MATERIAL_UNAVAILABLE = "material_unavailable"
    LOCAL_FAILED = "local_failed"
    WORKSPACE_UNUSABLE = "workspace_unusable"
    COMMIT_FAILED = "commit_failed"


class LocalMaterialWorkerFailureCode(StrEnum):
    """Closed union of probe and path-registry rejection vocabularies."""

    UNREADABLE = "unreadable"
    SOURCE_NOT_AT_REST = "source_not_at_rest"
    UNSAFE_PATH = "unsafe_path"
    UNDECODABLE = "undecodable"
    NO_USABLE_STREAM = "no_usable_stream"
    UNUSABLE_DURATION = "unusable_duration"
    TOO_LONG = "too_long"
    UNUSABLE_FRAME_SIZE = "unusable_frame_size"
    FRAME_TOO_LARGE = "frame_too_large"
    FILE_TOO_LARGE = "file_too_large"
    SILENT_AUDIO = "silent_audio"
    PROBE_CRASHED = "probe_crashed"
    PROBE_FAILED = "probe_failed"
    WORKSPACE_UNUSABLE = "workspace_unusable"
    UNUSABLE_IDENTIFIER = "unusable_identifier"
    NOT_REGISTERED = "not_registered"
    FILE_MISSING = "file_missing"
    FILE_UNREADABLE = "file_unreadable"
    FILE_CHANGED = "file_changed"
    REGISTRY_UNREADABLE = "registry_unreadable"
    REGISTRY_UNWRITABLE = "registry_unwritable"
    REGISTRY_FULL = "registry_full"


class LocalMaterialWorkerStatus(StrEnum):
    """Path-free availability plus the registry's exact closed reasons."""

    AVAILABLE = "available"
    UNUSABLE_IDENTIFIER = MaterialPathRegistryRejection.UNUSABLE_IDENTIFIER.value
    NOT_REGISTERED = MaterialPathRegistryRejection.NOT_REGISTERED.value
    FILE_MISSING = MaterialPathRegistryRejection.FILE_MISSING.value
    FILE_UNREADABLE = MaterialPathRegistryRejection.FILE_UNREADABLE.value
    FILE_CHANGED = MaterialPathRegistryRejection.FILE_CHANGED.value
    REGISTRY_UNREADABLE = MaterialPathRegistryRejection.REGISTRY_UNREADABLE.value
    REGISTRY_UNWRITABLE = MaterialPathRegistryRejection.REGISTRY_UNWRITABLE.value
    REGISTRY_FULL = MaterialPathRegistryRejection.REGISTRY_FULL.value


@dataclass(slots=True)
class _ActiveJob:
    job_id: UUID
    operation: str = "editing"
    phase: LocalEditingWorkerPhase | None = None
    progress_per_mille: int = -1
    cancelling: bool = False
    smart_stage: SmartEditGenerationStage | None = None
    result_digest: str | None = None
    awaiting_commit: bool = False
    finalizing: str | None = None


@dataclass(frozen=True, slots=True)
class _ActiveMaterialOperation:
    material_id: UUID
    command: str


class LocalEditingWorkerProtocol:
    """One authenticated stream with one render or material operation at a time."""

    __slots__ = ("_active", "_token", "_worker_version")

    def __init__(
        self,
        bootstrap: LocalEditingWorkerBootstrap,
        worker_version: str,
    ) -> None:
        if (
            not isinstance(bootstrap, LocalEditingWorkerBootstrap)
            or not isinstance(worker_version, str)
            or len(worker_version) > 128
            or _SEMANTIC_VERSION_PATTERN.fullmatch(worker_version) is None
        ):
            _reject()
        self._token = bootstrap.session_token_bytes()
        self._worker_version = worker_version
        self._active: _ActiveJob | _ActiveMaterialOperation | None = None

    def __repr__(self) -> str:
        return "LocalEditingWorkerProtocol(<redacted>)"

    def has_active_operation(self) -> bool:
        """Whether a rejected command must stay rejected instead of using legacy fallback."""

        return self._active is not None

    def accept_command(
        self,
        payload: bytes,
    ) -> (
        LocalEditingStartCommand
        | LocalEditingCancelCommand
        | LocalMaterialImportCommand
        | LocalMaterialForgetCommand
        | LocalMaterialStatusCommand
        | LocalSmartEditStartCommand
        | LocalSmartEditCommitCommand
        | LocalSmartEditAbortCommand
    ):
        document = _line_object(payload)
        command = document.get("command")
        if command == "worker.editing.start":
            return self._accept_start(document)
        if command == "worker.cancel":
            return self._accept_cancel(document)
        if command == "worker.material.import":
            return self._accept_material_import(document)
        if command == "worker.material.forget":
            return self._accept_material_forget(document)
        if command == "worker.material.status":
            return self._accept_material_status(document)
        if command == "worker.smart_edit.start":
            return self._accept_smart_edit_start(document)
        if command == "worker.smart_edit.commit":
            return self._accept_smart_edit_finalize(document, "commit")
        if command == "worker.smart_edit.abort":
            return self._accept_smart_edit_finalize(document, "abort")
        _reject()

    def _accept_smart_edit_start(
        self,
        document: dict[str, object],
    ) -> LocalSmartEditStartCommand:
        if (
            set(document) != _SMART_EDIT_COMMAND_KEYS
            or document.get("protocolVersion") != _PROTOCOL_VERSION
            or document.get("workerKind") != _WORKER_KIND
            or self._active is not None
        ):
            _reject()
        job_id = _uuid_v4(document.get("jobId"))
        self._require_command_proof(document, "worker.smart_edit.start", job_id)
        self._active = _ActiveJob(job_id, operation="smart_edit")
        return LocalSmartEditStartCommand(job_id)

    def _accept_smart_edit_finalize(
        self,
        document: dict[str, object],
        action: str,
    ) -> LocalSmartEditCommitCommand | LocalSmartEditAbortCommand:
        command_name = f"worker.smart_edit.{action}"
        if (
            action not in {"commit", "abort"}
            or set(document) != _SMART_EDIT_COMMAND_KEYS
            or document.get("protocolVersion") != _PROTOCOL_VERSION
            or document.get("workerKind") != _WORKER_KIND
        ):
            _reject()
        job_id = _uuid_v4(document.get("jobId"))
        active = self._require_active(job_id, operation="smart_edit")
        if (
            not active.awaiting_commit
            or active.result_digest is None
            or active.finalizing is not None
            or active.cancelling
        ):
            _reject()
        self._require_command_proof(document, command_name, job_id)
        active.finalizing = action
        if action == "commit":
            return LocalSmartEditCommitCommand(job_id)
        return LocalSmartEditAbortCommand(job_id)

    def _require_command_proof(
        self,
        document: dict[str, object],
        command: str,
        job_id: UUID,
    ) -> None:
        expected = _authentication_proof(
            self._token,
            _COMMAND_AUTHENTICATION_DOMAIN,
            _COMMAND_PROOF_PREFIX,
            (command, _WORKER_KIND, _PROTOCOL_VERSION, str(job_id)),
        )
        supplied = document.get("authenticationProof")
        if not isinstance(supplied, str) or not hmac.compare_digest(supplied, expected):
            _reject()

    def _accept_start(self, document: dict[str, object]) -> LocalEditingStartCommand:
        if (
            set(document) != _START_COMMAND_KEYS
            or document.get("protocolVersion") != _PROTOCOL_VERSION
            or document.get("workerKind") != _WORKER_KIND
            or self._active is not None
        ):
            _reject()
        job_id = _uuid_v4(document.get("jobId"))
        editing = document.get("editing")
        if not isinstance(editing, dict) or set(editing) != _EDITING_KEYS:
            _reject()
        project_id = _uuid_v4(editing.get("projectId"))
        timeline_id = _uuid_v4(editing.get("timelineId"))
        timeline_revision = editing.get("timelineRevision")
        if (
            project_id == timeline_id
            or type(timeline_revision) is not int
            or not 1 <= timeline_revision <= 2_147_483_647
        ):
            _reject()
        canonical = json.dumps(
            editing,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        expected = _authentication_proof(
            self._token,
            _COMMAND_AUTHENTICATION_DOMAIN,
            _COMMAND_PROOF_PREFIX,
            ("worker.editing.start", _WORKER_KIND, _PROTOCOL_VERSION, str(job_id), canonical),
        )
        supplied = document.get("authenticationProof")
        if not isinstance(supplied, str) or not hmac.compare_digest(supplied, expected):
            _reject()
        self._active = _ActiveJob(job_id)
        return LocalEditingStartCommand(
            job_id=job_id,
            project_id=project_id,
            timeline_id=timeline_id,
            timeline_revision=timeline_revision,
        )

    def _accept_cancel(self, document: dict[str, object]) -> LocalEditingCancelCommand:
        if (
            set(document) != _CANCEL_COMMAND_KEYS
            or document.get("protocolVersion") != _PROTOCOL_VERSION
            or document.get("workerKind") != _WORKER_KIND
        ):
            _reject()
        job_id = _uuid_v4(document.get("jobId"))
        active = self._active
        if (
            not isinstance(active, _ActiveJob)
            or active.job_id != job_id
            or active.cancelling
            or active.awaiting_commit
            or active.finalizing is not None
        ):
            _reject()
        expected = _authentication_proof(
            self._token,
            _COMMAND_AUTHENTICATION_DOMAIN,
            _COMMAND_PROOF_PREFIX,
            ("worker.cancel", _WORKER_KIND, _PROTOCOL_VERSION, str(job_id)),
        )
        supplied = document.get("authenticationProof")
        if not isinstance(supplied, str) or not hmac.compare_digest(supplied, expected):
            _reject()
        active.cancelling = True
        return LocalEditingCancelCommand(job_id)

    def _accept_material_import(self, document: dict[str, object]) -> LocalMaterialImportCommand:
        if (
            set(document) != _MATERIAL_IMPORT_COMMAND_KEYS
            or document.get("protocolVersion") != _PROTOCOL_VERSION
            or document.get("workerKind") != _WORKER_KIND
            or self._active is not None
        ):
            _reject()
        material_id = _uuid_v4(document.get("materialId"))
        source_path = _source_path(document.get("sourcePath"))
        expected = _authentication_proof(
            self._token,
            _COMMAND_AUTHENTICATION_DOMAIN,
            _COMMAND_PROOF_PREFIX,
            (
                "worker.material.import",
                _WORKER_KIND,
                _PROTOCOL_VERSION,
                str(material_id),
                str(source_path),
            ),
        )
        supplied = document.get("authenticationProof")
        if not isinstance(supplied, str) or not hmac.compare_digest(supplied, expected):
            _reject()
        self._active = _ActiveMaterialOperation(material_id, "import")
        return LocalMaterialImportCommand(material_id, source_path)

    def _accept_material_forget(self, document: dict[str, object]) -> LocalMaterialForgetCommand:
        if (
            set(document) != _MATERIAL_FORGET_COMMAND_KEYS
            or document.get("protocolVersion") != _PROTOCOL_VERSION
            or document.get("workerKind") != _WORKER_KIND
            or self._active is not None
        ):
            _reject()
        material_id = _uuid_v4(document.get("materialId"))
        expected = _authentication_proof(
            self._token,
            _COMMAND_AUTHENTICATION_DOMAIN,
            _COMMAND_PROOF_PREFIX,
            (
                "worker.material.forget",
                _WORKER_KIND,
                _PROTOCOL_VERSION,
                str(material_id),
            ),
        )
        supplied = document.get("authenticationProof")
        if not isinstance(supplied, str) or not hmac.compare_digest(supplied, expected):
            _reject()
        self._active = _ActiveMaterialOperation(material_id, "forget")
        return LocalMaterialForgetCommand(material_id)

    def _accept_material_status(self, document: dict[str, object]) -> LocalMaterialStatusCommand:
        if (
            set(document) != _MATERIAL_STATUS_COMMAND_KEYS
            or document.get("protocolVersion") != _PROTOCOL_VERSION
            or document.get("workerKind") != _WORKER_KIND
            or self._active is not None
        ):
            _reject()
        material_id = _uuid_v4(document.get("materialId"))
        expected = _authentication_proof(
            self._token,
            _COMMAND_AUTHENTICATION_DOMAIN,
            _COMMAND_PROOF_PREFIX,
            (
                "worker.material.status",
                _WORKER_KIND,
                _PROTOCOL_VERSION,
                str(material_id),
            ),
        )
        supplied = document.get("authenticationProof")
        if not isinstance(supplied, str) or not hmac.compare_digest(supplied, expected):
            _reject()
        self._active = _ActiveMaterialOperation(material_id, "status")
        return LocalMaterialStatusCommand(material_id)

    def progress(
        self,
        job_id: UUID,
        phase: LocalEditingWorkerPhase,
        progress_per_mille: int,
    ) -> bytes:
        active = self._require_active(job_id, operation="editing")
        if (
            not isinstance(phase, LocalEditingWorkerPhase)
            or type(progress_per_mille) is not int
            or not 0 <= progress_per_mille <= 1000
        ):
            _reject()
        order = {
            LocalEditingWorkerPhase.PREPARING: 0,
            LocalEditingWorkerPhase.RENDERING: 1,
            LocalEditingWorkerPhase.PUBLISHING: 2,
        }
        if active.phase is None:
            if phase is not LocalEditingWorkerPhase.PREPARING or progress_per_mille != 0:
                _reject()
        elif order[phase] < order[active.phase] or progress_per_mille < active.progress_per_mille:
            _reject()
        active.phase = phase
        active.progress_per_mille = progress_per_mille
        return self._event(
            "worker.editing.progress",
            job_id,
            f"{job_id}\0{phase.value}\0{progress_per_mille}",
            phase=phase.value,
            progressPermille=progress_per_mille,
        )

    def succeed(self, job_id: UUID, output_artifact_id: UUID) -> bytes:
        active = self._require_active(job_id, operation="editing")
        artifact = _uuid_v4(str(output_artifact_id))
        if (
            active.phase is not LocalEditingWorkerPhase.PUBLISHING
            or active.progress_per_mille != 1000
        ):
            _reject()
        payload = self._event(
            "worker.editing.succeeded",
            job_id,
            f"{job_id}\0{artifact}",
            outputArtifactId=str(artifact),
        )
        self._active = None
        return payload

    def fail(self, job_id: UUID, failure_code: LocalEditingWorkerFailureCode) -> bytes:
        self._require_active(job_id, operation="editing")
        if not isinstance(failure_code, LocalEditingWorkerFailureCode):
            _reject()
        payload = self._event(
            "worker.editing.failed",
            job_id,
            f"{job_id}\0{failure_code.value}",
            failureCode=failure_code.value,
        )
        self._active = None
        return payload

    def cancelled(self, job_id: UUID) -> bytes:
        active = self._require_active(job_id, operation="editing")
        if not active.cancelling:
            _reject()
        payload = self._event("worker.editing.cancelled", job_id, str(job_id))
        self._active = None
        return payload

    def smart_edit_progress(
        self,
        job_id: UUID,
        stage: SmartEditGenerationStage,
        progress_per_mille: int,
    ) -> bytes:
        active = self._require_active(job_id, operation="smart_edit")
        if (
            not isinstance(stage, SmartEditGenerationStage)
            or type(progress_per_mille) is not int
            or not 0 <= progress_per_mille <= 1_000
            or active.awaiting_commit
            or active.finalizing is not None
        ):
            _reject()
        order = {value: index for index, value in enumerate(SmartEditGenerationStage)}
        if active.smart_stage is None:
            if stage is not SmartEditGenerationStage.PREPARING or progress_per_mille != 0:
                _reject()
        elif (
            order[stage] < order[active.smart_stage]
            or progress_per_mille < active.progress_per_mille
        ):
            _reject()
        active.smart_stage = stage
        active.progress_per_mille = progress_per_mille
        return self._event(
            "worker.smart_edit.progress",
            job_id,
            f"{job_id}\0{stage.value}\0{progress_per_mille}",
            stage=stage.value,
            progressPermille=progress_per_mille,
        )

    def smart_edit_prepared(self, job_id: UUID, result_digest: str) -> bytes:
        active = self._require_active(job_id, operation="smart_edit")
        if (
            active.smart_stage is not SmartEditGenerationStage.COMPLETED
            or active.progress_per_mille != 1_000
            or active.cancelling
            or active.awaiting_commit
            or type(result_digest) is not str
            or _TOKEN_PATTERN.fullmatch(result_digest) is None
        ):
            _reject()
        active.result_digest = result_digest
        active.awaiting_commit = True
        return self._event(
            "worker.smart_edit.prepared",
            job_id,
            f"{job_id}\0{result_digest}",
            resultDigest=result_digest,
        )

    def smart_edit_succeeded(self, job_id: UUID, result_digest: str) -> bytes:
        active = self._require_active(job_id, operation="smart_edit")
        if (
            active.finalizing != "commit"
            or active.result_digest != result_digest
            or type(result_digest) is not str
            or _TOKEN_PATTERN.fullmatch(result_digest) is None
        ):
            _reject()
        payload = self._event(
            "worker.smart_edit.succeeded",
            job_id,
            f"{job_id}\0{result_digest}",
            resultDigest=result_digest,
        )
        self._active = None
        return payload

    def smart_edit_failed(
        self,
        job_id: UUID,
        failure_code: LocalSmartEditFailureCode,
    ) -> bytes:
        self._require_active(job_id, operation="smart_edit")
        if not isinstance(failure_code, LocalSmartEditFailureCode):
            _reject()
        payload = self._event(
            "worker.smart_edit.failed",
            job_id,
            f"{job_id}\0{failure_code.value}",
            failureCode=failure_code.value,
        )
        self._active = None
        return payload

    def smart_edit_cancelled(self, job_id: UUID) -> bytes:
        active = self._require_active(job_id, operation="smart_edit")
        if not active.cancelling or active.awaiting_commit:
            _reject()
        payload = self._event("worker.smart_edit.cancelled", job_id, str(job_id))
        self._active = None
        return payload

    def smart_edit_aborted(self, job_id: UUID) -> bytes:
        active = self._require_active(job_id, operation="smart_edit")
        if active.finalizing != "abort":
            _reject()
        payload = self._event("worker.smart_edit.aborted", job_id, str(job_id))
        self._active = None
        return payload

    def material_imported(self, material_id: UUID, facts: MaterialFacts) -> bytes:
        self._require_material_active(material_id, "import")
        document = _material_facts_document(facts)
        try:
            canonical = json.dumps(
                document,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            )
        except (TypeError, ValueError):
            _reject()
        payload = self._material_event(
            "worker.material.imported",
            material_id,
            f"{material_id}\0{canonical}",
            facts=document,
        )
        self._active = None
        return payload

    def material_import_failed(
        self,
        material_id: UUID,
        failure_code: LocalMaterialWorkerFailureCode,
    ) -> bytes:
        self._require_material_active(material_id, "import")
        if not isinstance(failure_code, LocalMaterialWorkerFailureCode):
            _reject()
        payload = self._material_event(
            "worker.material.import_failed",
            material_id,
            f"{material_id}\0{failure_code.value}",
            failureCode=failure_code.value,
        )
        self._active = None
        return payload

    def material_forgotten(self, material_id: UUID) -> bytes:
        self._require_material_active(material_id, "forget")
        payload = self._material_event("worker.material.forgotten", material_id, str(material_id))
        self._active = None
        return payload

    def material_forget_failed(
        self,
        material_id: UUID,
        failure_code: LocalMaterialWorkerFailureCode,
    ) -> bytes:
        self._require_material_active(material_id, "forget")
        if not isinstance(failure_code, LocalMaterialWorkerFailureCode):
            _reject()
        payload = self._material_event(
            "worker.material.forget_failed",
            material_id,
            f"{material_id}\0{failure_code.value}",
            failureCode=failure_code.value,
        )
        self._active = None
        return payload

    def material_status(
        self,
        material_id: UUID,
        status: LocalMaterialWorkerStatus,
    ) -> bytes:
        self._require_material_active(material_id, "status")
        if not isinstance(status, LocalMaterialWorkerStatus):
            _reject()
        payload = self._material_event(
            "worker.material.status",
            material_id,
            f"{material_id}\0{status.value}",
            status=status.value,
        )
        self._active = None
        return payload

    def _require_active(self, job_id: UUID, *, operation: str) -> _ActiveJob:
        if (
            not isinstance(job_id, UUID)
            or job_id.version != 4
            or job_id.variant != RFC_4122
            or not isinstance(self._active, _ActiveJob)
            or self._active.job_id != job_id
            or self._active.operation != operation
        ):
            _reject()
        return self._active

    def _require_material_active(self, material_id: UUID, command: str) -> _ActiveMaterialOperation:
        active = self._active
        if (
            not isinstance(material_id, UUID)
            or material_id.version != 4
            or material_id.variant != RFC_4122
            or not isinstance(active, _ActiveMaterialOperation)
            or active.material_id != material_id
            or active.command != command
        ):
            _reject()
        return active

    def _event(self, event: str, job_id: UUID, detail: str, **facts: object) -> bytes:
        document: dict[str, object] = {
            "authenticationProof": _authentication_proof(
                self._token,
                _EVENT_AUTHENTICATION_DOMAIN,
                _EVENT_PROOF_PREFIX,
                (event, _WORKER_KIND, _PROTOCOL_VERSION, self._worker_version, detail),
            ),
            "event": event,
            "jobId": str(job_id),
            "protocolVersion": _PROTOCOL_VERSION,
            "workerKind": _WORKER_KIND,
            "workerVersion": self._worker_version,
        }
        document.update(facts)
        payload = json.dumps(document, separators=(",", ":")).encode() + b"\n"
        if len(payload) > MAX_LOCAL_EDITING_WORKER_BOOTSTRAP_BYTES:
            _reject()
        return payload

    def _material_event(self, event: str, material_id: UUID, detail: str, **facts: object) -> bytes:
        document: dict[str, object] = {
            "authenticationProof": _authentication_proof(
                self._token,
                _EVENT_AUTHENTICATION_DOMAIN,
                _EVENT_PROOF_PREFIX,
                (event, _WORKER_KIND, _PROTOCOL_VERSION, self._worker_version, detail),
            ),
            "event": event,
            "materialId": str(material_id),
            "protocolVersion": _PROTOCOL_VERSION,
            "workerKind": _WORKER_KIND,
            "workerVersion": self._worker_version,
        }
        document.update(facts)
        payload = json.dumps(document, separators=(",", ":"), allow_nan=False).encode() + b"\n"
        if len(payload) > MAX_LOCAL_EDITING_WORKER_BOOTSTRAP_BYTES:
            _reject()
        return payload


def parse_local_editing_worker_bootstrap(
    payload: bytes,
) -> LocalEditingWorkerBootstrap:
    """Parse the sole Rust-issued bootstrap without consulting process state."""

    if (
        not isinstance(payload, bytes)
        or not 1 < len(payload) <= MAX_LOCAL_EDITING_WORKER_BOOTSTRAP_BYTES
        or not payload.endswith(b"\n")
        or payload.count(b"\n") != 1
    ):
        _reject()
    document = _load_object(payload[:-1])
    token = document.get("localSessionToken")
    if (
        set(document) != _ROOT_KEYS
        or document.get("bootstrapVersion") != "1"
        or document.get("protocolVersion") != "1.0"
        or document.get("workerKind") != "python"
        or document.get("enableWebUi") is not False
        or document.get("renderBrowser") is not None
        # Carried for the material worker's WebUI. This worker has no use for
        # a stock-footage key and never reads the value, but a document whose
        # shape it does not fully recognise is still rejected.
        or not (
            document.get("pexelsApiKey") is None
            or isinstance(document.get("pexelsApiKey"), str)
        )
        or not isinstance(token, str)
        or _TOKEN_PATTERN.fullmatch(token) is None
    ):
        _reject()
    return LocalEditingWorkerBootstrap(
        asset_root=_asset_root(document.get("assetRoot")),
        media_tools=_media_tools(document.get("mediaTools")),
        _session_token=bytes.fromhex(token),
        script_model=_script_model(document.get("scriptModel")),
    )


__all__ = [
    "MAX_LOCAL_EDITING_WORKER_BOOTSTRAP_BYTES",
    "LocalEditingCancelCommand",
    "LocalEditingScriptModelConfiguration",
    "LocalEditingStartCommand",
    "LocalEditingWorkerBootstrap",
    "LocalEditingWorkerBootstrapRejected",
    "LocalEditingWorkerFailureCode",
    "LocalEditingWorkerPhase",
    "LocalEditingWorkerProtocol",
    "LocalMaterialForgetCommand",
    "LocalMaterialImportCommand",
    "LocalMaterialStatusCommand",
    "LocalMaterialWorkerFailureCode",
    "LocalMaterialWorkerStatus",
    "LocalSmartEditAbortCommand",
    "LocalSmartEditCommitCommand",
    "LocalSmartEditFailureCode",
    "LocalSmartEditStartCommand",
    "parse_local_editing_worker_bootstrap",
]
