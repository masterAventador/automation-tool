"""Aliyun finished-video import, cost recording and temp-resource cleanup (VE-07).

This adapter replaces the VE-06 minimal registrar: a confirmed vendor success
streams the produced MP4 from the documented output object
(`editing-output/v1/<editing-job>.mp4`), verifies its size bound and SHA-256
while writing through an atomic payload sink, and records the neutral lineage
(inputs, frozen timeline revision, provider identity, contract verification
date and the estimated cost) exactly once. A ledger hit short-circuits into
the already-registered artifact identifiers with zero further downloads.

The first-phase Aliyun output configuration produces only the MP4: no cover
or subtitle objects exist, so none are fabricated; content facts (digest,
size, media type) live on the registered artifact record itself.

Temp OSS resources are cleaned by explicit policy: a successfully imported
job deletes its staging objects and the output object and verifies absence;
a failed job deletes staging only; an uncertain outcome retains everything —
evidence is never destroyed while a result is still unresolved. Object keys
outside the project staging/output prefixes are rejected, never deleted.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum, unique
from pathlib import Path
from types import MappingProxyType
from typing import Final, Never, Protocol, final

from automation_tool.control_plane.domain.aliyun_ims_editing_provider import (
    ALIYUN_IMS_EDITING_PROVIDER_ID,
    OUTPUT_OBJECT_KEY_PREFIX,
    AliyunEditingIntentState,
    AliyunEditingIntentStore,
    AliyunImsTransportFailure,
    AliyunOssBucketName,
)
from automation_tool.control_plane.domain.aliyun_ims_editing_staging import (
    AliyunImsEditingStagingContract,
    AliyunImsRegion,
    InvalidAliyunImsEditingStagingModel,
    estimate_editing_cost,
)
from automation_tool.control_plane.domain.resource_ids import ArtifactId
from automation_tool.control_plane.domain.video_creation import (
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACT_REFERENCES,
    TimelineId,
)
from automation_tool.control_plane.domain.video_editing import EditingJobId, EditingProjectId
from automation_tool.control_plane.domain.video_editing_outputs import (
    EditingOutputArtifactRecord,
    EditingOutputCost,
    EditingOutputCostSource,
    EditingOutputKind,
    EditingOutputLedger,
    EditingOutputLedgerConflict,
    EditingOutputLineage,
)
from automation_tool.control_plane.domain.video_editing_provider import (
    EditingProviderErrorCode,
    EditingProviderFailure,
)

STAGING_OBJECT_KEY_PREFIX: Final = "editing-staging/v1/"
OUTPUT_MEDIA_TYPE: Final = "video/mp4"

_OBJECT_NAME_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_SINK_MEDIA_EXTENSIONS: Final = MappingProxyType(
    {
        "video/mp4": ".mp4",
        "video/webm": ".webm",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "text/vtt": ".vtt",
        "application/x-subrip": ".srt",
        "application/json": ".json",
    }
)
_STREAM_CHUNK_BYTES: Final = 1024 * 1024


class InvalidAliyunImsEditingOutputModel(ValueError):
    """An Aliyun editing output value or operation input is invalid."""

    def __init__(self) -> None:
        super().__init__("Aliyun IMS editing output value is invalid")


@final
class AliyunOssObjectMissing(Exception):
    """The expected OSS object definitively does not exist."""

    def __init__(self) -> None:
        super().__init__("Aliyun OSS object is missing")


class _EmptyOutputObject(Exception):
    """Internal marker: the vendor output object carried zero bytes."""


class _OversizedOutputObject(Exception):
    """Internal marker: the vendor output object exceeded the size bound."""


def _reject() -> Never:
    raise InvalidAliyunImsEditingOutputModel


def _fail(code: EditingProviderErrorCode) -> Never:
    raise EditingProviderFailure(code)


def _sync_directory_entry(directory: Path) -> None:
    """Make the rename durable, on the platforms that can express it.

    Opening a directory read-only in order to `fsync` it is a POSIX idiom;
    Windows refuses the open outright, so the call raised `PermissionError`
    and took the whole import down. There is no user-mode equivalent to
    reach for, so durability of the directory entry rests on NTFS's own
    metadata journaling there and the payload's own `fsync` still runs on
    both platforms.
    """
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@final
@dataclass(frozen=True, slots=True)
class AliyunOssObjectRef:
    """One OSS object confined to the project staging/output prefixes."""

    bucket: AliyunOssBucketName
    object_key: str

    def __post_init__(self) -> None:
        if type(self.bucket) is not AliyunOssBucketName or type(self.object_key) is not str:
            _reject()
        for prefix in (STAGING_OBJECT_KEY_PREFIX, OUTPUT_OBJECT_KEY_PREFIX):
            if self.object_key.startswith(prefix):
                if _OBJECT_NAME_PATTERN.fullmatch(self.object_key[len(prefix) :]) is None:
                    _reject()
                return
        _reject()


def output_object_ref_for(
    *, bucket: AliyunOssBucketName, editing_job_id: EditingJobId
) -> AliyunOssObjectRef:
    """Return the documented output object for one editing job."""
    if not isinstance(editing_job_id, EditingJobId):
        _reject()
    return AliyunOssObjectRef(
        bucket=bucket, object_key=f"{OUTPUT_OBJECT_KEY_PREFIX}{editing_job_id}.mp4"
    )


class AliyunOssOutputTransport(Protocol):
    """Network port for reading, deleting and listing project OSS objects."""

    def stream_object(self, ref: AliyunOssObjectRef) -> AsyncIterator[bytes]:
        """Stream one object; raise `AliyunOssObjectMissing` or transport failure."""
        ...

    async def delete_object(self, ref: AliyunOssObjectRef) -> None:
        """Delete one object idempotently; raise transport failure otherwise."""
        ...

    async def list_object_keys(self, bucket: AliyunOssBucketName, prefix: str) -> tuple[str, ...]:
        """List the object keys currently stored under one project prefix."""
        ...


class EditingOutputPayloadSink(Protocol):
    """Local storage port; `persist` is atomic and leaves nothing on failure."""

    async def persist(
        self, artifact_id: ArtifactId, media_type: str, chunks: AsyncIterator[bytes]
    ) -> None:
        """Consume the stream to durable storage or raise without residue."""
        ...


@final
class DirectoryEditingOutputPayloadSink:
    """Filesystem sink writing each artifact atomically into one directory."""

    __slots__ = ("_directory",)

    def __init__(self, directory: Path) -> None:
        if not isinstance(directory, Path):
            _reject()
        directory.mkdir(parents=True, exist_ok=True)
        self._directory = directory

    def path_for(self, artifact_id: ArtifactId, media_type: str) -> Path:
        """Return the stable payload path for one registered artifact."""
        extension = _SINK_MEDIA_EXTENSIONS.get(media_type)
        if not isinstance(artifact_id, ArtifactId) or extension is None:
            _reject()
        return self._directory / f"{artifact_id}{extension}"

    async def persist(
        self, artifact_id: ArtifactId, media_type: str, chunks: AsyncIterator[bytes]
    ) -> None:
        final_path = self.path_for(artifact_id, media_type)
        if final_path.exists():
            _reject()
        temporary_path = self._directory / f".import-{final_path.name}.tmp"
        try:
            with temporary_path.open("wb") as handle:
                async for chunk in chunks:
                    if type(chunk) is not bytes:
                        _reject()
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, final_path)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
        _sync_directory_entry(self._directory)


@final
@dataclass(frozen=True, slots=True)
class AliyunEditingLineageBasis:
    """Durable submission facts required to record lineage and cost."""

    project_id: EditingProjectId
    timeline_id: TimelineId
    timeline_revision: int
    input_artifact_ids: tuple[ArtifactId, ...] = field(repr=False)
    output_duration_ms: int
    output_height: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.project_id, EditingProjectId)
            or type(self.timeline_id) is not TimelineId
            or type(self.timeline_revision) is not int
            or self.timeline_revision < 1
            or type(self.output_duration_ms) is not int
            or self.output_duration_ms < 1
            or type(self.output_height) is not int
            or self.output_height < 1
        ):
            _reject()
        if (
            not isinstance(self.input_artifact_ids, tuple)
            or not 1 <= len(self.input_artifact_ids) <= MAX_ARTIFACT_REFERENCES
            or any(not isinstance(value, ArtifactId) for value in self.input_artifact_ids)
            or len(set(self.input_artifact_ids)) != len(self.input_artifact_ids)
        ):
            _reject()


class AliyunEditingLineageBasisSource(Protocol):
    """Port resolving the durable lineage basis for one editing job."""

    async def basis_for(self, editing_job_id: EditingJobId) -> AliyunEditingLineageBasis:
        """Return the basis; raise `EditingProviderFailure(NOT_FOUND)` if absent."""
        ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


@final
class AliyunEditingOutputImporter:
    """Real registrar behind the VE-06 `AliyunEditingOutputRegistrar` port."""

    __slots__ = (
        "_basis_source",
        "_bucket",
        "_clock",
        "_contract",
        "_intent_store",
        "_ledger",
        "_max_output_bytes",
        "_region",
        "_sink",
        "_transport",
    )

    def __init__(
        self,
        *,
        intent_store: AliyunEditingIntentStore,
        transport: AliyunOssOutputTransport,
        sink: EditingOutputPayloadSink,
        ledger: EditingOutputLedger,
        contract: AliyunImsEditingStagingContract,
        region: AliyunImsRegion,
        bucket: AliyunOssBucketName,
        basis_source: AliyunEditingLineageBasisSource,
        max_output_bytes: int = MAX_ARTIFACT_BYTES,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if (
            not isinstance(contract, AliyunImsEditingStagingContract)
            or not isinstance(region, AliyunImsRegion)
            or type(bucket) is not AliyunOssBucketName
            or type(max_output_bytes) is not int
            or not 1 <= max_output_bytes <= MAX_ARTIFACT_BYTES
        ):
            _reject()
        self._intent_store = intent_store
        self._transport = transport
        self._sink = sink
        self._ledger = ledger
        self._contract = contract
        self._region = region
        self._bucket = bucket
        self._basis_source = basis_source
        self._max_output_bytes = max_output_bytes
        self._clock = clock

    async def register_confirmed_output(
        self, editing_job_id: EditingJobId
    ) -> tuple[ArtifactId, ...]:
        """Import the confirmed output once and return its artifact identifiers."""
        if not isinstance(editing_job_id, EditingJobId):
            _fail(EditingProviderErrorCode.INVALID_INPUT)
        recorded = await self._ledger.load(editing_job_id)
        if recorded is not None:
            return tuple(record.artifact_id for record in recorded.outputs)

        intent = await self._intent_store.load(editing_job_id)
        if intent is None or intent.state is AliyunEditingIntentState.PREPARED:
            _fail(EditingProviderErrorCode.NOT_FOUND)
        basis = await self._basis_source.basis_for(editing_job_id)
        if not isinstance(basis, AliyunEditingLineageBasis):
            _fail(EditingProviderErrorCode.PROVIDER_ERROR)

        artifact_id = ArtifactId.new()
        reference = output_object_ref_for(bucket=self._bucket, editing_job_id=editing_job_id)
        hasher = hashlib.sha256()
        byte_count = 0

        async def _verified_chunks() -> AsyncIterator[bytes]:
            nonlocal byte_count
            async for chunk in self._transport.stream_object(reference):
                if type(chunk) is not bytes:
                    raise InvalidAliyunImsEditingOutputModel
                byte_count += len(chunk)
                if byte_count > self._max_output_bytes:
                    raise _OversizedOutputObject
                hasher.update(chunk)
                yield chunk
            if byte_count == 0:
                raise _EmptyOutputObject

        try:
            await self._sink.persist(artifact_id, OUTPUT_MEDIA_TYPE, _verified_chunks())
        except (AliyunOssObjectMissing, _EmptyOutputObject, _OversizedOutputObject):
            _fail(EditingProviderErrorCode.PROVIDER_ERROR)
        except AliyunImsTransportFailure:
            _fail(EditingProviderErrorCode.DEPENDENCY_UNAVAILABLE)

        lineage = self._build_lineage(
            editing_job_id=editing_job_id,
            basis=basis,
            artifact_id=artifact_id,
            byte_size=byte_count,
            sha256_hex=hasher.hexdigest(),
        )
        try:
            await self._ledger.save(lineage)
        except EditingOutputLedgerConflict:
            concurrent = await self._ledger.load(editing_job_id)
            if concurrent is None:
                _fail(EditingProviderErrorCode.PROVIDER_ERROR)
            return tuple(record.artifact_id for record in concurrent.outputs)
        return (artifact_id,)

    def _build_lineage(
        self,
        *,
        editing_job_id: EditingJobId,
        basis: AliyunEditingLineageBasis,
        artifact_id: ArtifactId,
        byte_size: int,
        sha256_hex: str,
    ) -> EditingOutputLineage:
        try:
            estimate = estimate_editing_cost(
                contract=self._contract,
                region=self._region,
                output_duration_ms=basis.output_duration_ms,
                output_height=basis.output_height,
            )
        except InvalidAliyunImsEditingStagingModel:
            _fail(EditingProviderErrorCode.INVALID_INPUT)
        now = self._clock()
        record = EditingOutputArtifactRecord(
            artifact_id=artifact_id,
            kind=EditingOutputKind.VIDEO,
            media_type=OUTPUT_MEDIA_TYPE,
            byte_size=byte_size,
            sha256_hex=sha256_hex,
            created_at=now,
        )
        cost = EditingOutputCost(
            source=EditingOutputCostSource.ESTIMATED,
            currency=estimate.currency,
            billed_minutes=estimate.billed_minutes,
            tier_id=estimate.tier_id,
            unit_price_cny=estimate.unit_price_cny,
            total_cny=estimate.estimated_total_cny,
        )
        return EditingOutputLineage(
            editing_job_id=editing_job_id,
            project_id=basis.project_id,
            timeline_id=basis.timeline_id,
            timeline_revision=basis.timeline_revision,
            provider_id=ALIYUN_IMS_EDITING_PROVIDER_ID,
            provider_contract_verified_at=self._contract.verified_at,
            input_artifact_ids=basis.input_artifact_ids,
            outputs=(record,),
            cost=cost,
            created_at=now,
        )


@unique
class AliyunEditingCleanupOutcome(StrEnum):
    """Closed cleanup policy trigger derived from the settled job result."""

    SUCCEEDED_IMPORTED = "succeeded_imported"
    FAILED = "failed"
    OUTCOME_UNCERTAIN = "outcome_uncertain"


@final
@dataclass(frozen=True, slots=True)
class AliyunEditingCleanupReport:
    """The exact keys deleted, retained and the post-delete verification."""

    deleted_keys: tuple[str, ...]
    retained_keys: tuple[str, ...]
    verified_absent: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.deleted_keys, tuple)
            or not isinstance(self.retained_keys, tuple)
            or any(type(key) is not str for key in self.deleted_keys + self.retained_keys)
            or type(self.verified_absent) is not bool
        ):
            _reject()


@final
class AliyunEditingTempResourceCleaner:
    """Policy-driven deletion of one job's temporary staging/output objects."""

    __slots__ = ("_bucket", "_transport")

    def __init__(
        self, *, transport: AliyunOssOutputTransport, bucket: AliyunOssBucketName
    ) -> None:
        if type(bucket) is not AliyunOssBucketName:
            _reject()
        self._transport = transport
        self._bucket = bucket

    async def cleanup(
        self,
        editing_job_id: EditingJobId,
        *,
        staging_object_keys: tuple[str, ...],
        outcome: AliyunEditingCleanupOutcome,
    ) -> AliyunEditingCleanupReport:
        """Apply the retention policy for one settled job and verify deletions."""
        if not isinstance(editing_job_id, EditingJobId) or not isinstance(
            outcome, AliyunEditingCleanupOutcome
        ):
            _reject()
        if not isinstance(staging_object_keys, tuple):
            _reject()
        staging_refs = tuple(
            AliyunOssObjectRef(bucket=self._bucket, object_key=key)
            for key in staging_object_keys
        )
        if any(
            not ref.object_key.startswith(STAGING_OBJECT_KEY_PREFIX) for ref in staging_refs
        ):
            _reject()
        output_ref = output_object_ref_for(bucket=self._bucket, editing_job_id=editing_job_id)

        if outcome is AliyunEditingCleanupOutcome.OUTCOME_UNCERTAIN:
            return AliyunEditingCleanupReport(
                deleted_keys=(),
                retained_keys=(
                    *(ref.object_key for ref in staging_refs),
                    output_ref.object_key,
                ),
                verified_absent=False,
            )

        targets = list(staging_refs)
        if outcome is AliyunEditingCleanupOutcome.SUCCEEDED_IMPORTED:
            targets.append(output_ref)

        deleted: list[str] = []
        retained: list[str] = []
        for ref in targets:
            try:
                await self._transport.delete_object(ref)
            except AliyunImsTransportFailure:
                retained.append(ref.object_key)
            else:
                deleted.append(ref.object_key)

        verified = not retained
        if verified:
            for key in deleted:
                remaining = await self._transport.list_object_keys(self._bucket, key)
                if remaining:
                    verified = False
                    break
        return AliyunEditingCleanupReport(
            deleted_keys=tuple(deleted),
            retained_keys=tuple(retained),
            verified_absent=verified,
        )


__all__ = [
    "OUTPUT_MEDIA_TYPE",
    "STAGING_OBJECT_KEY_PREFIX",
    "AliyunEditingCleanupOutcome",
    "AliyunEditingCleanupReport",
    "AliyunEditingLineageBasis",
    "AliyunEditingLineageBasisSource",
    "AliyunEditingOutputImporter",
    "AliyunEditingTempResourceCleaner",
    "AliyunOssObjectMissing",
    "AliyunOssObjectRef",
    "AliyunOssOutputTransport",
    "DirectoryEditingOutputPayloadSink",
    "EditingOutputPayloadSink",
    "InvalidAliyunImsEditingOutputModel",
    "output_object_ref_for",
]
