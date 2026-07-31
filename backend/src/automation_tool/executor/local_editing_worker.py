"""Authenticated stdin bootstrap for the packaged local-editing Worker."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Never
from uuid import RFC_4122, UUID

from automation_tool.executor.material_probe import (
    MAX_PATH_CHARACTERS,
    MaterialProbeRejected,
    PackagedMediaTools,
)
from automation_tool.protocol.safe_text import contains_control_or_bidi

MAX_LOCAL_EDITING_WORKER_BOOTSTRAP_BYTES = 16 * 1024
_TOKEN_PATTERN = re.compile(r"^[0-9a-f]{64}\Z")
_ROOT_KEYS = frozenset(
    {
        "assetRoot",
        "bootstrapVersion",
        "enableWebUi",
        "localSessionToken",
        "mediaTools",
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


@dataclass(slots=True)
class _ActiveJob:
    job_id: UUID
    phase: LocalEditingWorkerPhase | None = None
    progress_per_mille: int = -1
    cancelling: bool = False


class LocalEditingWorkerProtocol:
    """One authenticated, single-job command/event stream."""

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
        self._active: _ActiveJob | None = None

    def __repr__(self) -> str:
        return "LocalEditingWorkerProtocol(<redacted>)"

    def accept_command(
        self,
        payload: bytes,
    ) -> LocalEditingStartCommand | LocalEditingCancelCommand:
        document = _line_object(payload)
        command = document.get("command")
        if command == "worker.editing.start":
            return self._accept_start(document)
        if command == "worker.cancel":
            return self._accept_cancel(document)
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
        if active is None or active.job_id != job_id or active.cancelling:
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

    def progress(
        self,
        job_id: UUID,
        phase: LocalEditingWorkerPhase,
        progress_per_mille: int,
    ) -> bytes:
        active = self._require_active(job_id)
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
        active = self._require_active(job_id)
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
        self._require_active(job_id)
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
        active = self._require_active(job_id)
        if not active.cancelling:
            _reject()
        payload = self._event("worker.editing.cancelled", job_id, str(job_id))
        self._active = None
        return payload

    def _require_active(self, job_id: UUID) -> _ActiveJob:
        if (
            not isinstance(job_id, UUID)
            or job_id.version != 4
            or job_id.variant != RFC_4122
            or self._active is None
            or self._active.job_id != job_id
        ):
            _reject()
        return self._active

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
        or document.get("scriptModel") is not None
        or not isinstance(token, str)
        or _TOKEN_PATTERN.fullmatch(token) is None
    ):
        _reject()
    return LocalEditingWorkerBootstrap(
        asset_root=_asset_root(document.get("assetRoot")),
        media_tools=_media_tools(document.get("mediaTools")),
        _session_token=bytes.fromhex(token),
    )


__all__ = [
    "MAX_LOCAL_EDITING_WORKER_BOOTSTRAP_BYTES",
    "LocalEditingCancelCommand",
    "LocalEditingStartCommand",
    "LocalEditingWorkerBootstrap",
    "LocalEditingWorkerBootstrapRejected",
    "LocalEditingWorkerFailureCode",
    "LocalEditingWorkerPhase",
    "LocalEditingWorkerProtocol",
    "parse_local_editing_worker_bootstrap",
]
