"""One-shot native video-editing execution through the production provider.

The signed Executor receives one bounded request on stdin, stages only files
that Tauri already copied into a private input directory, runs the existing
provider/reconciliation/output-import chain, emits one sanitized JSON result,
and exits. Credentials and absolute paths never enter argv, the environment,
logs, Control Plane persistence, or the WebView.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import stat
import sys
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Final, Literal, Never, TextIO, final

import httpx2
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from automation_tool.control_plane.domain.aliyun_ims_editing_output import (
    AliyunEditingCleanupOutcome,
    AliyunEditingLineageBasis,
    AliyunEditingOutputImporter,
    AliyunEditingTempResourceCleaner,
    AliyunOssObjectRef,
    DirectoryEditingOutputPayloadSink,
)
from automation_tool.control_plane.domain.aliyun_ims_editing_provider import (
    AliyunEditingIntent,
    AliyunEditingIntentState,
    AliyunEditingOutputConfig,
    AliyunImsEditingProvider,
    AliyunImsTransportFailure,
    AliyunOssBucketName,
)
from automation_tool.control_plane.domain.aliyun_ims_editing_reconciliation import (
    AliyunEditingReconciliationPolicy,
    AliyunImsEditingReconciler,
)
from automation_tool.control_plane.domain.aliyun_ims_editing_staging import (
    AliyunImsRegion,
    EditingServicePreflight,
    MediaStagingPlan,
    PreflightCheckStatus,
    StagingAsset,
    build_media_staging_plan,
    load_aliyun_ims_editing_staging_contract,
)
from automation_tool.control_plane.domain.resource_ids import ArtifactId
from automation_tool.control_plane.domain.video_creation import (
    TimelineClip,
    TimelineId,
    TimelineTrack,
    TimelineTrackKind,
    TimelineTransition,
    TransitionKind,
)
from automation_tool.control_plane.domain.video_editing import (
    EditingJobId,
    EditingJobStatus,
    EditingProjectId,
    EditingTimeline,
)
from automation_tool.control_plane.domain.video_editing_outputs import (
    EditingOutputKind,
    InMemoryEditingOutputLedger,
)
from automation_tool.control_plane.domain.video_editing_provider import (
    EditingProviderFailure,
    EditingSubmission,
    editing_submission_idempotency_key,
)
from automation_tool.control_plane.infrastructure.aliyun.editing import (
    AliyunEditingCredential,
    AliyunImsEditingTransport,
    AliyunOssEditingTransport,
)
from automation_tool.control_plane.infrastructure.aliyun.editing_intent_store import (
    FileAliyunEditingIntentStore,
)

_SCHEMA_VERSION: Final = 1
MAX_REQUEST_BYTES: Final = 1024 * 1024
_STREAM_CHUNK_BYTES: Final = 1024 * 1024
_ALLOWED_EXTENSIONS: Final = frozenset(
    {".aac", ".jpeg", ".jpg", ".m4a", ".mov", ".mp3", ".mp4", ".png", ".srt", ".wav"}
)
type _FailureCode = Literal[
    "invalid_input",
    "dependency_unavailable",
    "resource_exhausted",
    "editing_failed",
]


class VideoEditingExecutionRejected(ValueError):
    def __init__(self) -> None:
        super().__init__("Video editing execution request is rejected")


def _reject() -> Never:
    raise VideoEditingExecutionRejected


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EditingCredentialInput(_StrictModel):
    access_key_id: Annotated[
        str,
        Field(
            alias="accessKeyId",
            min_length=16,
            max_length=64,
            pattern=r"^LTAI[A-Za-z0-9]+$",
            repr=False,
        ),
    ]
    access_key_secret: Annotated[
        str,
        Field(
            alias="accessKeySecret",
            min_length=20,
            max_length=128,
            pattern=r"^[A-Za-z0-9/+=_-]+$",
            repr=False,
        ),
    ]
    region: Literal[
        "ap-southeast-1",
        "cn-beijing",
        "cn-hangzhou",
        "cn-shanghai",
        "cn-shenzhen",
        "us-west-1",
    ]
    oss_bucket: Annotated[
        str,
        Field(
            alias="ossBucket",
            min_length=3,
            max_length=63,
            pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$",
        ),
    ]


class EditingTransitionInput(_StrictModel):
    kind: Literal["cut", "fade", "dissolve", "wipe"]
    duration_ms: Annotated[int, Field(alias="durationMs", ge=1, le=10_000)]


class EditingClipInput(_StrictModel):
    clip_id: Annotated[str, Field(alias="clipId", pattern=r"^[a-z][a-z0-9-]{0,63}$")]
    start_ms: Annotated[int, Field(alias="startMs", ge=0, le=600_000)]
    duration_ms: Annotated[int, Field(alias="durationMs", ge=1, le=600_000)]
    source_artifact_id: str | None = Field(alias="sourceArtifactId")
    text: Annotated[str, Field(min_length=1, max_length=2_000)] | None
    transition_in: EditingTransitionInput | None = Field(alias="transitionIn")


class EditingTrackInput(_StrictModel):
    track_id: Annotated[str, Field(alias="trackId", pattern=r"^[a-z][a-z0-9-]{0,63}$")]
    kind: Literal["visual", "audio", "caption"]
    clips: Annotated[list[EditingClipInput], Field(min_length=1, max_length=512)]


class EditingTimelineInput(_StrictModel):
    timeline_id: str = Field(alias="timelineId")
    project_id: str = Field(alias="projectId")
    revision: Annotated[int, Field(ge=1)]
    duration_ms: Annotated[int, Field(alias="durationMs", ge=100, le=600_000)]
    tracks: Annotated[list[EditingTrackInput], Field(min_length=1, max_length=32)]
    created_at: datetime = Field(alias="createdAt")


class EditingAssetInput(_StrictModel):
    artifact_id: str = Field(alias="artifactId")
    path: str
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    size_bytes: Annotated[int, Field(alias="sizeBytes", ge=1, le=16 * 1024 * 1024 * 1024)]
    extension: str

    @field_validator("extension")
    @classmethod
    def validate_extension(cls, value: str) -> str:
        if value not in _ALLOWED_EXTENSIONS:
            raise ValueError("unsupported extension")
        return value


class EditingExecutionRequest(_StrictModel):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    execution_mode: Literal["submit", "reconcile"] = Field(alias="executionMode")
    credential: EditingCredentialInput
    editing_job_id: str = Field(alias="editingJobId")
    project_id: str = Field(alias="projectId")
    timeline: EditingTimelineInput
    assets: Annotated[list[EditingAssetInput], Field(min_length=1, max_length=256)]
    input_directory: str = Field(alias="inputDirectory")
    output_directory: str = Field(alias="outputDirectory")
    state_directory: str = Field(alias="stateDirectory")
    output_width: Annotated[int, Field(alias="outputWidth", ge=128, le=4096)]
    output_height: Annotated[int, Field(alias="outputHeight", ge=128, le=4096)]

    def verify_files(self) -> None:
        """Re-prove the native staging boundary before any network side effect."""

        input_root = _private_directory(self.input_directory)
        output_root = _private_directory(self.output_directory)
        state_root = _private_directory(self.state_directory)
        if (
            input_root in (output_root, state_root)
            or output_root == state_root
            or input_root in output_root.parents
            or output_root in input_root.parents
            or input_root.parent != output_root.parent
            or input_root.parent != state_root.parent
        ):
            _reject()
        seen: set[str] = set()
        for asset in self.assets:
            try:
                artifact_id = ArtifactId.parse(asset.artifact_id)
            except (TypeError, ValueError):
                _reject()
            path = Path(asset.path)
            if (
                str(artifact_id) in seen
                or not path.is_absolute()
                or path.parent != input_root
                or path.name != f"{artifact_id}{asset.extension}"
            ):
                _reject()
            seen.add(str(artifact_id))
            try:
                metadata = path.lstat()
            except OSError:
                _reject()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_size != asset.size_bytes
            ):
                _reject()
            digest = hashlib.sha256()
            total = 0
            try:
                with path.open("rb") as source:
                    while chunk := source.read(_STREAM_CHUNK_BYTES):
                        total += len(chunk)
                        if total > asset.size_bytes:
                            _reject()
                        digest.update(chunk)
            except OSError:
                _reject()
            if total != asset.size_bytes or digest.hexdigest() != asset.sha256:
                _reject()

        required = {
            clip.source_artifact_id
            for track in self.timeline.tracks
            for clip in track.clips
            if clip.source_artifact_id is not None
        }
        if required != seen or self.timeline.project_id != self.project_id:
            _reject()


class EditingExecutionResult(_StrictModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    status: Literal["succeeded", "failed", "outcome_uncertain"]
    editing_job_id: str = Field(alias="editingJobId")
    output_path: str | None = Field(alias="outputPath")
    output_sha256: str | None = Field(alias="outputSha256")
    output_size_bytes: int | None = Field(alias="outputSizeBytes")
    failure_code: str | None = Field(alias="failureCode")


def _private_directory(value: str) -> Path:
    path = Path(value)
    try:
        metadata = path.lstat()
    except OSError:
        _reject()
    if not path.is_absolute() or not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
        _reject()
    return path


def _contract_path() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    root = (
        Path(frozen_root) if isinstance(frozen_root, str) else Path(__file__).resolve().parents[4]
    )
    path = root / "contracts/video/aliyun-ims-editing-staging.v1.json"
    if not path.is_file():
        _reject()
    return path


def _domain_timeline(request: EditingExecutionRequest) -> EditingTimeline:
    try:
        project_id = EditingProjectId.parse(request.project_id)
        tracks = tuple(
            TimelineTrack(
                track_id=track.track_id,
                kind=TimelineTrackKind(track.kind),
                clips=tuple(
                    TimelineClip(
                        clip_id=clip.clip_id,
                        start_ms=clip.start_ms,
                        duration_ms=clip.duration_ms,
                        source_artifact_id=(
                            None
                            if clip.source_artifact_id is None
                            else ArtifactId.parse(clip.source_artifact_id)
                        ),
                        text=clip.text,
                        transition_in=(
                            None
                            if clip.transition_in is None
                            else TimelineTransition(
                                kind=TransitionKind(clip.transition_in.kind),
                                duration_ms=clip.transition_in.duration_ms,
                            )
                        ),
                    )
                    for clip in track.clips
                ),
            )
            for track in request.timeline.tracks
        )
        return EditingTimeline(
            timeline_id=TimelineId.parse(request.timeline.timeline_id),
            project_id=project_id,
            revision=request.timeline.revision,
            duration_ms=request.timeline.duration_ms,
            tracks=tracks,
            created_at=request.timeline.created_at.astimezone(UTC),
        )
    except (TypeError, ValueError):
        _reject()


@final
class _SinglePlanPlanner:
    __slots__ = ("_plan",)

    def __init__(self, plan: MediaStagingPlan) -> None:
        self._plan = plan

    async def plan(self, _timeline: EditingTimeline) -> MediaStagingPlan:
        return self._plan


@final
class _StaticPreflightSource:
    __slots__ = ("_preflight",)

    def __init__(self, preflight: EditingServicePreflight) -> None:
        self._preflight = preflight

    async def current(self) -> EditingServicePreflight:
        return self._preflight


@final
class _StaticBasisSource:
    __slots__ = ("_basis",)

    def __init__(self, basis: AliyunEditingLineageBasis) -> None:
        self._basis = basis

    async def basis_for(self, _editing_job_id: EditingJobId) -> AliyunEditingLineageBasis:
        return self._basis


async def _file_chunks(path: Path) -> AsyncIterator[bytes]:
    try:
        with path.open("rb") as source:
            while chunk := source.read(_STREAM_CHUNK_BYTES):
                yield chunk
                await asyncio.sleep(0)
    except OSError:
        _reject()


def _failed_result(
    editing_job_id: EditingJobId,
    failure_code: _FailureCode,
) -> EditingExecutionResult:
    return EditingExecutionResult(
        status="failed",
        editingJobId=str(editing_job_id),
        outputPath=None,
        outputSha256=None,
        outputSizeBytes=None,
        failureCode=failure_code,
    )


def _uncertain_result(editing_job_id: EditingJobId) -> EditingExecutionResult:
    return EditingExecutionResult(
        status="outcome_uncertain",
        editingJobId=str(editing_job_id),
        outputPath=None,
        outputSha256=None,
        outputSizeBytes=None,
        failureCode=None,
    )


def _closed_failure_code(value: str) -> _FailureCode:
    if value == "invalid_input":
        return "invalid_input"
    if value == "dependency_unavailable":
        return "dependency_unavailable"
    if value == "resource_exhausted":
        return "resource_exhausted"
    return "editing_failed"


async def execute_video_editing(
    request: EditingExecutionRequest,
    *,
    client: httpx2.AsyncClient,
    poll_interval_seconds: float = 5.0,
) -> EditingExecutionResult:
    """Execute one complete provider chain under one cross-process lease."""
    if (
        not isinstance(request, EditingExecutionRequest)
        or not isinstance(client, httpx2.AsyncClient)
        or isinstance(poll_interval_seconds, bool)
        or not isinstance(poll_interval_seconds, (int, float))
        or not 0 <= poll_interval_seconds <= 60
    ):
        _reject()
    intent_store = FileAliyunEditingIntentStore(Path(request.state_directory))
    with intent_store.execution_lease():
        return await _execute_video_editing_with_store(
            request,
            client=client,
            intent_store=intent_store,
            poll_interval_seconds=poll_interval_seconds,
        )


async def _execute_video_editing_with_store(
    request: EditingExecutionRequest,
    *,
    client: httpx2.AsyncClient,
    intent_store: FileAliyunEditingIntentStore,
    poll_interval_seconds: float,
) -> EditingExecutionResult:
    request.verify_files()
    timeline = _domain_timeline(request)
    try:
        editing_job_id = EditingJobId.parse(request.editing_job_id)
        region = AliyunImsRegion(request.credential.region)
        bucket = AliyunOssBucketName(request.credential.oss_bucket)
        credential = AliyunEditingCredential(
            access_key_id=request.credential.access_key_id,
            access_key_secret=request.credential.access_key_secret,
            region=region,
            bucket=bucket,
        )
    except (TypeError, ValueError):
        _reject()
    contract = load_aliyun_ims_editing_staging_contract(_contract_path())
    assets = tuple(
        StagingAsset(
            logical_id=asset.artifact_id,
            sha256_hex=asset.sha256,
            size_bytes=asset.size_bytes,
            extension=asset.extension,
        )
        for asset in request.assets
    )
    plan = build_media_staging_plan(
        contract=contract,
        service_region=region,
        bucket_region=region,
        assets=assets,
    )
    oss = AliyunOssEditingTransport(client, credential)
    staged_refs = tuple(
        AliyunOssObjectRef(bucket=bucket, object_key=entry.object_key) for entry in plan.objects
    )
    cleaner = AliyunEditingTempResourceCleaner(transport=oss, bucket=bucket)
    existing = await intent_store.load(editing_job_id)
    if request.execution_mode == "submit":
        if existing is not None:
            _reject()
    elif existing is not None and existing.state in {
        AliyunEditingIntentState.PREPARED,
        AliyunEditingIntentState.UNCERTAIN,
    }:
        return _uncertain_result(editing_job_id)

    async def cleanup(outcome: AliyunEditingCleanupOutcome) -> None:
        with contextlib.suppress(AliyunImsTransportFailure):
            await cleaner.cleanup(
                editing_job_id,
                staging_object_keys=tuple(ref.object_key for ref in staged_refs),
                outcome=outcome,
            )

    if existing is None:
        by_digest = {asset.sha256: asset for asset in request.assets}
        try:
            for entry, ref in zip(plan.objects, staged_refs, strict=True):
                asset = by_digest[entry.sha256_hex]
                await oss.put_object(
                    ref,
                    _file_chunks(Path(asset.path)),
                    content_length=asset.size_bytes,
                )
        except AliyunImsTransportFailure:
            await cleanup(AliyunEditingCleanupOutcome.FAILED)
            return _failed_result(editing_job_id, "dependency_unavailable")

    ims = AliyunImsEditingTransport(client, credential)
    provider = AliyunImsEditingProvider(
        contract=contract,
        region=region,
        staging_bucket=bucket,
        output=AliyunEditingOutputConfig(
            width=request.output_width,
            height=request.output_height,
        ),
        preflight_source=_StaticPreflightSource(
            EditingServicePreflight(
                region=region,
                region_check=PreflightCheckStatus.PASSED,
                permission_check=PreflightCheckStatus.PASSED,
                quota_check=PreflightCheckStatus.PASSED,
            )
        ),
        staging_planner=_SinglePlanPlanner(plan),
        transport=ims,
        intent_store=intent_store,
    )
    submission = EditingSubmission(
        editing_job_id=editing_job_id,
        project_id=timeline.project_id,
        timeline=timeline,
        idempotency_key=editing_submission_idempotency_key(editing_job_id),
    )
    try:
        initial = await provider.submit(submission)
    except EditingProviderFailure as failure:
        await cleanup(AliyunEditingCleanupOutcome.FAILED)
        failure_code = _closed_failure_code(failure.code.value)
        return _failed_result(editing_job_id, failure_code)
    if initial.status is EditingJobStatus.OUTCOME_UNCERTAIN:
        return _uncertain_result(editing_job_id)

    ledger = InMemoryEditingOutputLedger()
    sink = DirectoryEditingOutputPayloadSink(Path(request.output_directory))
    if initial.status is EditingJobStatus.SUCCEEDED:
        persisted = await intent_store.load(editing_job_id)
        if persisted is None:
            _reject()
        result = _persisted_success_result(persisted, sink)
        await cleanup(AliyunEditingCleanupOutcome.SUCCEEDED_IMPORTED)
        return result
    if initial.status in {
        EditingJobStatus.FAILED,
        EditingJobStatus.CANCELLED,
    }:
        persisted = await intent_store.load(editing_job_id)
        raw_failure_code = (
            persisted.failure_code.value
            if persisted is not None and persisted.failure_code is not None
            else "editing_failed"
        )
        await cleanup(AliyunEditingCleanupOutcome.FAILED)
        return _failed_result(editing_job_id, _closed_failure_code(raw_failure_code))
    importer = AliyunEditingOutputImporter(
        intent_store=intent_store,
        transport=oss,
        sink=sink,
        ledger=ledger,
        contract=contract,
        region=region,
        bucket=bucket,
        basis_source=_StaticBasisSource(
            AliyunEditingLineageBasis(
                project_id=timeline.project_id,
                timeline_id=timeline.timeline_id,
                timeline_revision=timeline.revision,
                input_artifact_ids=tuple(
                    ArtifactId.parse(asset.artifact_id) for asset in request.assets
                ),
                output_duration_ms=timeline.duration_ms,
                output_height=request.output_height,
            )
        ),
    )
    reconciler = AliyunImsEditingReconciler(
        provider=provider,
        intent_store=intent_store,
        transport=ims,
        contract=contract,
        region=region,
        registrar=importer,
        policy=AliyunEditingReconciliationPolicy(
            max_polls=120,
            transient_failure_limit=5,
            poll_interval_seconds=float(poll_interval_seconds),
        ),
    )
    try:
        settled = await reconciler.reconcile_until_terminal(editing_job_id)
    except (AliyunImsTransportFailure, EditingProviderFailure):
        return _uncertain_result(editing_job_id)
    if settled.status is EditingJobStatus.OUTCOME_UNCERTAIN:
        return _uncertain_result(editing_job_id)
    if settled.status is not EditingJobStatus.SUCCEEDED:
        raw_failure_code = (
            settled.failure_code.value if settled.failure_code is not None else "editing_failed"
        )
        failure_code = _closed_failure_code(raw_failure_code)
        await cleanup(AliyunEditingCleanupOutcome.FAILED)
        return _failed_result(editing_job_id, failure_code)

    lineage = await ledger.load(editing_job_id)
    if lineage is None:
        _reject()
    video = next(
        (output for output in lineage.outputs if output.kind is EditingOutputKind.VIDEO),
        None,
    )
    if video is None:
        _reject()
    output_path = sink.path_for(video.artifact_id, video.media_type)
    await cleanup(AliyunEditingCleanupOutcome.SUCCEEDED_IMPORTED)
    return EditingExecutionResult(
        status="succeeded",
        editingJobId=str(editing_job_id),
        outputPath=os.fspath(output_path),
        outputSha256=video.sha256_hex,
        outputSizeBytes=video.byte_size,
        failureCode=None,
    )


def _persisted_success_result(
    intent: AliyunEditingIntent,
    sink: DirectoryEditingOutputPayloadSink,
) -> EditingExecutionResult:
    if (
        not isinstance(intent, AliyunEditingIntent)
        or intent.status is not EditingJobStatus.SUCCEEDED
        or len(intent.output_artifact_ids) != 1
    ):
        _reject()
    path = sink.path_for(intent.output_artifact_ids[0], "video/mp4")
    try:
        metadata = path.lstat()
    except OSError:
        _reject()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or metadata.st_nlink != 1
        or not 0 < metadata.st_size <= 32 * 1024 * 1024 * 1024
    ):
        _reject()
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(_STREAM_CHUNK_BYTES):
                digest.update(chunk)
    except OSError:
        _reject()
    return EditingExecutionResult(
        status="succeeded",
        editingJobId=str(intent.editing_job_id),
        outputPath=os.fspath(path),
        outputSha256=digest.hexdigest(),
        outputSizeBytes=metadata.st_size,
        failureCode=None,
    )


def _write_fixed(stream: TextIO, text: str) -> None:
    with contextlib.suppress(Exception):
        stream.write(text + "\n")
        stream.flush()


def serve_one_video_editing_request(
    source: bytes,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    client_factory: Callable[[], httpx2.AsyncClient] = lambda: httpx2.AsyncClient(timeout=60),
) -> int:
    """Parse one bounded stdin document, execute, write one result and exit."""

    output = sys.stdout if stdout is None else stdout
    error = sys.stderr if stderr is None else stderr
    if type(source) is not bytes or not 0 < len(source) <= MAX_REQUEST_BYTES:
        _write_fixed(error, "Video editing request is rejected")
        return 2
    try:
        request = EditingExecutionRequest.model_validate_json(source)
    except (ValidationError, ValueError):
        _write_fixed(error, "Video editing request is rejected")
        return 2

    async def _run() -> EditingExecutionResult:
        async with client_factory() as client:
            return await execute_video_editing(request, client=client)

    try:
        result = asyncio.run(_run())
        serialized = result.model_dump_json(by_alias=True)
    except Exception:
        _write_fixed(error, "Video editing execution is unavailable")
        return 1
    _write_fixed(output, serialized)
    return 0


__all__ = [
    "MAX_REQUEST_BYTES",
    "EditingExecutionRequest",
    "EditingExecutionResult",
    "VideoEditingExecutionRejected",
    "execute_video_editing",
    "serve_one_video_editing_request",
]
