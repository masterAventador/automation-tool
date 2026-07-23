"""VE-07: real Aliyun finished-video import, cost recording and temp cleanup."""

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import final
from uuid import UUID

import pytest

from automation_tool.control_plane.domain.aliyun_ims_editing_output import (
    AliyunEditingCleanupOutcome,
    AliyunEditingLineageBasis,
    AliyunEditingOutputImporter,
    AliyunEditingTempResourceCleaner,
    AliyunOssObjectMissing,
    AliyunOssObjectRef,
    DirectoryEditingOutputPayloadSink,
    InvalidAliyunImsEditingOutputModel,
    output_object_ref_for,
)
from automation_tool.control_plane.domain.aliyun_ims_editing_provider import (
    AliyunEditingIntent,
    AliyunEditingIntentState,
    AliyunImsTransportFailure,
    AliyunImsTransportFailureKind,
    AliyunOssBucketName,
    InMemoryAliyunEditingIntentStore,
)
from automation_tool.control_plane.domain.aliyun_ims_editing_staging import (
    AliyunImsEditingStagingContract,
    AliyunImsRegion,
    load_aliyun_ims_editing_staging_contract,
)
from automation_tool.control_plane.domain.resource_ids import ArtifactId
from automation_tool.control_plane.domain.video_creation import TimelineId
from automation_tool.control_plane.domain.video_editing import (
    EditingJobId,
    EditingJobStatus,
    EditingProjectId,
)
from automation_tool.control_plane.domain.video_editing_outputs import (
    EditingOutputCostSource,
    EditingOutputKind,
    InMemoryEditingOutputLedger,
)
from automation_tool.control_plane.domain.video_editing_provider import (
    EditingProviderErrorCode,
    EditingProviderFailure,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts/video/aliyun-ims-editing-staging.v1.json"

REGION = AliyunImsRegion.CN_BEIJING
BUCKET = AliyunOssBucketName("automation-tool-video-staging")
JOB_ID = EditingJobId(UUID("00000000-0000-4000-8000-0000000000e7"))
PROJECT_ID = EditingProjectId(UUID("00000000-0000-4000-8000-0000000000e1"))
TIMELINE_ID = TimelineId(UUID("00000000-0000-4000-8000-0000000000e2"))
INPUT_ARTIFACT = ArtifactId(UUID("00000000-0000-4000-8000-0000000000a7"))
VENDOR_JOB_ID = "46c446e2420348e0950e4d7876acc6fb"
REQUEST_HASH = "ab" * 32
PAYLOAD = b"ve-07-finished-video-bytes"
CLOCK_NOW = datetime(2026, 7, 23, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def contract() -> AliyunImsEditingStagingContract:
    return load_aliyun_ims_editing_staging_contract(CONTRACT_PATH)


def _dispatched_intent(
    *, status: EditingJobStatus = EditingJobStatus.RUNNING
) -> AliyunEditingIntent:
    return AliyunEditingIntent(
        editing_job_id=JOB_ID,
        request_hash=REQUEST_HASH,
        state=AliyunEditingIntentState.DISPATCHED,
        vendor_job_id=VENDOR_JOB_ID,
        status=status,
        failure_code=None,
        output_artifact_ids=(),
    )


def _basis() -> AliyunEditingLineageBasis:
    return AliyunEditingLineageBasis(
        project_id=PROJECT_ID,
        timeline_id=TIMELINE_ID,
        timeline_revision=1,
        input_artifact_ids=(INPUT_ARTIFACT,),
        output_duration_ms=30_000,
        output_height=1080,
    )


@final
class _FakeOssTransport:
    """Deterministic OSS port: one stored object map plus call accounting."""

    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.stream_calls = 0
        self.deleted_keys: list[str] = []
        self.fail_streams_with: Exception | None = None
        self.fail_delete_keys: set[str] = set()

    def stream_object(self, ref: AliyunOssObjectRef) -> AsyncIterator[bytes]:
        self.stream_calls += 1
        failure = self.fail_streams_with
        payload = self.objects.get(ref.object_key)

        async def _chunks() -> AsyncIterator[bytes]:
            if failure is not None:
                raise failure
            if payload is None:
                raise AliyunOssObjectMissing
            for index in range(0, len(payload), 8):
                yield payload[index : index + 8]

        return _chunks()

    async def delete_object(self, ref: AliyunOssObjectRef) -> None:
        if ref.object_key in self.fail_delete_keys:
            raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.RESPONSE_LOST)
        self.objects.pop(ref.object_key, None)
        self.deleted_keys.append(ref.object_key)

    async def list_object_keys(self, bucket: AliyunOssBucketName, prefix: str) -> tuple[str, ...]:
        return tuple(sorted(key for key in self.objects if key.startswith(prefix)))


@final
class _FakeBasisSource:
    def __init__(self, basis: AliyunEditingLineageBasis | None) -> None:
        self._basis = basis

    async def basis_for(self, editing_job_id: EditingJobId) -> AliyunEditingLineageBasis:
        if self._basis is None:
            raise EditingProviderFailure(EditingProviderErrorCode.NOT_FOUND)
        return self._basis


def _importer(
    *,
    contract: AliyunImsEditingStagingContract,
    transport: _FakeOssTransport,
    sink: DirectoryEditingOutputPayloadSink,
    ledger: InMemoryEditingOutputLedger,
    intent_store: InMemoryAliyunEditingIntentStore,
    missing_basis: bool = False,
    max_output_bytes: int = 1024,
) -> AliyunEditingOutputImporter:
    return AliyunEditingOutputImporter(
        intent_store=intent_store,
        transport=transport,
        sink=sink,
        ledger=ledger,
        contract=contract,
        region=REGION,
        bucket=BUCKET,
        basis_source=_FakeBasisSource(None if missing_basis else _basis()),
        max_output_bytes=max_output_bytes,
        clock=lambda: CLOCK_NOW,
    )


def _output_key() -> str:
    return f"editing-output/v1/{JOB_ID}.mp4"


async def _prepared_world(
    contract: AliyunImsEditingStagingContract, tmp_path: Path
) -> tuple[
    AliyunEditingOutputImporter,
    _FakeOssTransport,
    DirectoryEditingOutputPayloadSink,
    InMemoryEditingOutputLedger,
]:
    intent_store = InMemoryAliyunEditingIntentStore()
    await intent_store.save(_dispatched_intent())
    transport = _FakeOssTransport({_output_key(): PAYLOAD})
    sink = DirectoryEditingOutputPayloadSink(tmp_path / "artifacts")
    ledger = InMemoryEditingOutputLedger()
    importer = _importer(
        contract=contract,
        transport=transport,
        sink=sink,
        ledger=ledger,
        intent_store=intent_store,
    )
    return importer, transport, sink, ledger


class TestOutputObjectRef:
    def test_builds_documented_output_key(self) -> None:
        ref = output_object_ref_for(bucket=BUCKET, editing_job_id=JOB_ID)
        assert ref.bucket == BUCKET
        assert ref.object_key == _output_key()

    def test_rejects_foreign_prefix(self) -> None:
        with pytest.raises(InvalidAliyunImsEditingOutputModel):
            AliyunOssObjectRef(bucket=BUCKET, object_key="user-data/secret.mp4")


class TestAliyunEditingOutputImporter:
    @pytest.mark.asyncio
    async def test_imports_video_and_records_lineage_and_cost(
        self, contract: AliyunImsEditingStagingContract, tmp_path: Path
    ) -> None:
        importer, _, sink, ledger = await _prepared_world(contract, tmp_path)

        artifact_ids = await importer.register_confirmed_output(JOB_ID)

        assert len(artifact_ids) == 1
        lineage = await ledger.load(JOB_ID)
        assert lineage is not None
        video = lineage.outputs[0]
        assert video.kind is EditingOutputKind.VIDEO
        assert video.byte_size == len(PAYLOAD)
        assert video.sha256_hex == hashlib.sha256(PAYLOAD).hexdigest()
        assert lineage.cost.source is EditingOutputCostSource.ESTIMATED
        assert lineage.cost.billed_minutes == 1
        assert lineage.input_artifact_ids == (INPUT_ARTIFACT,)
        assert lineage.provider_contract_verified_at == contract.verified_at
        stored = sink.path_for(video.artifact_id, video.media_type)
        assert stored.read_bytes() == PAYLOAD

    @pytest.mark.asyncio
    async def test_reimport_is_idempotent_without_second_download(
        self, contract: AliyunImsEditingStagingContract, tmp_path: Path
    ) -> None:
        importer, transport, _, _ = await _prepared_world(contract, tmp_path)
        first = await importer.register_confirmed_output(JOB_ID)
        second = await importer.register_confirmed_output(JOB_ID)
        assert first == second
        assert transport.stream_calls == 1

    @pytest.mark.asyncio
    async def test_unknown_or_prepared_intent_is_not_found(
        self, contract: AliyunImsEditingStagingContract, tmp_path: Path
    ) -> None:
        intent_store = InMemoryAliyunEditingIntentStore()
        importer = _importer(
            contract=contract,
            transport=_FakeOssTransport({}),
            sink=DirectoryEditingOutputPayloadSink(tmp_path / "artifacts"),
            ledger=InMemoryEditingOutputLedger(),
            intent_store=intent_store,
        )
        with pytest.raises(EditingProviderFailure) as unknown:
            await importer.register_confirmed_output(JOB_ID)
        assert unknown.value.code is EditingProviderErrorCode.NOT_FOUND

    @pytest.mark.asyncio
    async def test_missing_output_object_is_provider_error(
        self, contract: AliyunImsEditingStagingContract, tmp_path: Path
    ) -> None:
        importer, transport, _, ledger = await _prepared_world(contract, tmp_path)
        transport.objects.clear()
        with pytest.raises(EditingProviderFailure) as missing:
            await importer.register_confirmed_output(JOB_ID)
        assert missing.value.code is EditingProviderErrorCode.PROVIDER_ERROR
        assert await ledger.load(JOB_ID) is None

    @pytest.mark.asyncio
    async def test_transport_failure_is_dependency_unavailable(
        self, contract: AliyunImsEditingStagingContract, tmp_path: Path
    ) -> None:
        importer, transport, _, ledger = await _prepared_world(contract, tmp_path)
        transport.fail_streams_with = AliyunImsTransportFailure(
            AliyunImsTransportFailureKind.RESPONSE_LOST
        )
        with pytest.raises(EditingProviderFailure) as lost:
            await importer.register_confirmed_output(JOB_ID)
        assert lost.value.code is EditingProviderErrorCode.DEPENDENCY_UNAVAILABLE
        assert await ledger.load(JOB_ID) is None

    @pytest.mark.asyncio
    async def test_empty_output_object_is_rejected(
        self, contract: AliyunImsEditingStagingContract, tmp_path: Path
    ) -> None:
        importer, transport, _, ledger = await _prepared_world(contract, tmp_path)
        transport.objects[_output_key()] = b""
        with pytest.raises(EditingProviderFailure) as empty:
            await importer.register_confirmed_output(JOB_ID)
        assert empty.value.code is EditingProviderErrorCode.PROVIDER_ERROR
        assert await ledger.load(JOB_ID) is None

    @pytest.mark.asyncio
    async def test_oversized_output_fails_without_partial_artifact(
        self, contract: AliyunImsEditingStagingContract, tmp_path: Path
    ) -> None:
        intent_store = InMemoryAliyunEditingIntentStore()
        await intent_store.save(_dispatched_intent())
        transport = _FakeOssTransport({_output_key(): PAYLOAD})
        sink_dir = tmp_path / "artifacts"
        sink = DirectoryEditingOutputPayloadSink(sink_dir)
        ledger = InMemoryEditingOutputLedger()
        importer = _importer(
            contract=contract,
            transport=transport,
            sink=sink,
            ledger=ledger,
            intent_store=intent_store,
            max_output_bytes=len(PAYLOAD) - 1,
        )
        with pytest.raises(EditingProviderFailure) as oversized:
            await importer.register_confirmed_output(JOB_ID)
        assert oversized.value.code is EditingProviderErrorCode.PROVIDER_ERROR
        assert await ledger.load(JOB_ID) is None
        assert not any(sink_dir.rglob("*")) or all(
            path.is_dir() for path in sink_dir.rglob("*")
        )

    @pytest.mark.asyncio
    async def test_missing_lineage_basis_is_not_found_and_nothing_recorded(
        self, contract: AliyunImsEditingStagingContract, tmp_path: Path
    ) -> None:
        intent_store = InMemoryAliyunEditingIntentStore()
        await intent_store.save(_dispatched_intent())
        ledger = InMemoryEditingOutputLedger()
        importer = _importer(
            contract=contract,
            transport=_FakeOssTransport({_output_key(): PAYLOAD}),
            sink=DirectoryEditingOutputPayloadSink(tmp_path / "artifacts"),
            ledger=ledger,
            intent_store=intent_store,
            missing_basis=True,
        )
        with pytest.raises(EditingProviderFailure) as no_basis:
            await importer.register_confirmed_output(JOB_ID)
        assert no_basis.value.code is EditingProviderErrorCode.NOT_FOUND
        assert await ledger.load(JOB_ID) is None


class TestDirectoryEditingOutputPayloadSink:
    @pytest.mark.asyncio
    async def test_persists_atomically_and_rejects_duplicates(self, tmp_path: Path) -> None:
        sink = DirectoryEditingOutputPayloadSink(tmp_path / "artifacts")
        artifact_id = ArtifactId(UUID("00000000-0000-4000-8000-0000000000f1"))

        async def _chunks() -> AsyncIterator[bytes]:
            yield PAYLOAD

        await sink.persist(artifact_id, "video/mp4", _chunks())
        stored = sink.path_for(artifact_id, "video/mp4")
        assert stored.read_bytes() == PAYLOAD

        async def _second() -> AsyncIterator[bytes]:
            yield b"other"

        with pytest.raises(InvalidAliyunImsEditingOutputModel):
            await sink.persist(artifact_id, "video/mp4", _second())
        assert stored.read_bytes() == PAYLOAD

    @pytest.mark.asyncio
    async def test_failed_stream_leaves_no_partial_files(self, tmp_path: Path) -> None:
        directory = tmp_path / "artifacts"
        sink = DirectoryEditingOutputPayloadSink(directory)
        artifact_id = ArtifactId(UUID("00000000-0000-4000-8000-0000000000f2"))

        async def _broken() -> AsyncIterator[bytes]:
            yield b"partial"
            raise AliyunOssObjectMissing

        with pytest.raises(AliyunOssObjectMissing):
            await sink.persist(artifact_id, "video/mp4", _broken())
        assert not any(path.is_file() for path in directory.rglob("*"))

    @pytest.mark.asyncio
    async def test_rejects_media_type_outside_vocabulary(self, tmp_path: Path) -> None:
        sink = DirectoryEditingOutputPayloadSink(tmp_path / "artifacts")
        artifact_id = ArtifactId(UUID("00000000-0000-4000-8000-0000000000f3"))

        async def _chunks() -> AsyncIterator[bytes]:
            yield PAYLOAD

        with pytest.raises(InvalidAliyunImsEditingOutputModel):
            await sink.persist(artifact_id, "application/x-msdownload", _chunks())


class TestAliyunEditingTempResourceCleaner:
    @staticmethod
    def _world(
        objects: dict[str, bytes],
    ) -> tuple[AliyunEditingTempResourceCleaner, _FakeOssTransport]:
        transport = _FakeOssTransport(objects)
        cleaner = AliyunEditingTempResourceCleaner(transport=transport, bucket=BUCKET)
        return cleaner, transport

    @pytest.mark.asyncio
    async def test_successful_import_deletes_staging_and_output_and_verifies(self) -> None:
        staging_key = f"editing-staging/v1/{'cd' * 32}.mp4"
        cleaner, transport = self._world({staging_key: b"in", _output_key(): b"out"})

        report = await cleaner.cleanup(
            JOB_ID,
            staging_object_keys=(staging_key,),
            outcome=AliyunEditingCleanupOutcome.SUCCEEDED_IMPORTED,
        )

        assert set(report.deleted_keys) == {staging_key, _output_key()}
        assert report.retained_keys == ()
        assert report.verified_absent is True
        assert transport.objects == {}

    @pytest.mark.asyncio
    async def test_uncertain_outcome_retains_everything(self) -> None:
        staging_key = f"editing-staging/v1/{'cd' * 32}.mp4"
        cleaner, transport = self._world({staging_key: b"in", _output_key(): b"out"})

        report = await cleaner.cleanup(
            JOB_ID,
            staging_object_keys=(staging_key,),
            outcome=AliyunEditingCleanupOutcome.OUTCOME_UNCERTAIN,
        )

        assert report.deleted_keys == ()
        assert set(report.retained_keys) == {staging_key, _output_key()}
        assert report.verified_absent is False
        assert set(transport.objects) == {staging_key, _output_key()}

    @pytest.mark.asyncio
    async def test_failed_job_deletes_staging_but_not_output_prefix(self) -> None:
        staging_key = f"editing-staging/v1/{'cd' * 32}.mp4"
        cleaner, transport = self._world({staging_key: b"in"})

        report = await cleaner.cleanup(
            JOB_ID,
            staging_object_keys=(staging_key,),
            outcome=AliyunEditingCleanupOutcome.FAILED,
        )

        assert report.deleted_keys == (staging_key,)
        assert transport.objects == {}

    @pytest.mark.asyncio
    async def test_delete_failure_reports_key_as_retained(self) -> None:
        staging_key = f"editing-staging/v1/{'cd' * 32}.mp4"
        cleaner, transport = self._world({staging_key: b"in", _output_key(): b"out"})
        transport.fail_delete_keys = {staging_key}

        report = await cleaner.cleanup(
            JOB_ID,
            staging_object_keys=(staging_key,),
            outcome=AliyunEditingCleanupOutcome.SUCCEEDED_IMPORTED,
        )

        assert staging_key in report.retained_keys
        assert _output_key() in report.deleted_keys
        assert report.verified_absent is False

    @pytest.mark.asyncio
    async def test_rejects_keys_outside_project_prefixes(self) -> None:
        cleaner, _ = self._world({})
        with pytest.raises(InvalidAliyunImsEditingOutputModel):
            await cleaner.cleanup(
                JOB_ID,
                staging_object_keys=("user-data/precious.mp4",),
                outcome=AliyunEditingCleanupOutcome.SUCCEEDED_IMPORTED,
            )
