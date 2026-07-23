"""PB-03: Bilibili upload and single-admission archive creation orchestration."""

from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from automation_tool.control_plane.application.bilibili_archive_publishing import (
    BilibiliArchiveCreationReceipt,
    BilibiliArchiveFields,
    BilibiliArchiveOutcomeUncertain,
    BilibiliArchivePublishRejected,
    BilibiliArchivePublishService,
    BilibiliArchivePublishUnavailable,
    BilibiliCoverStat,
    BilibiliGatewayUnreachable,
    BilibiliPublishAttemptRecord,
    BilibiliPublishMaterial,
    BilibiliPublishMaterialStat,
    BilibiliPublishPhase,
    BilibiliPublishPreparation,
    BilibiliPublishStepFailed,
    BilibiliUploadType,
)
from automation_tool.control_plane.domain.bilibili_open_api import (
    InvalidBilibiliOpenApiMessage,
    load_bilibili_open_api_contract,
)
from automation_tool.control_plane.domain.video_publishing import (
    PublishFailureCode,
    PublishJobId,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts/publishing/bilibili-open-api.v1.json"
FIXTURE_ROOT = REPOSITORY_ROOT / "contracts/publishing/fixtures/bilibili-open-api-v1"

CONTRACT = load_bilibili_open_api_contract(CONTRACT_PATH)
NOW = datetime(2026, 7, 23, 1, 0, tzinfo=UTC)
PART_SIZE = CONTRACT.part_size_bytes
SMALL_MAX = CONTRACT.small_file_max_bytes
CHUNKED_SIZE = SMALL_MAX + PART_SIZE + 5


def _fixture(name: str) -> Any:
    document = json.loads((FIXTURE_ROOT / f"{name}.json").read_text(encoding="utf-8"))
    return document["payload"]


def _error_payload(code: int) -> dict[str, object]:
    return {"code": code, "message": "fixture-error"}


class FixedClock:
    def __init__(self, start: datetime = NOW) -> None:
        self.current = start

    def now(self) -> datetime:
        self.current = self.current + timedelta(seconds=1)
        return self.current


class VirtualZeroReader:
    """Deterministic all-zero material without materializing the full payload."""

    def __init__(self, *, file_name: str, size_bytes: int, duration_seconds: int) -> None:
        self.file_name = file_name
        self.size_bytes = size_bytes
        self.duration_seconds = duration_seconds

    async def stat(self) -> BilibiliPublishMaterialStat:
        return BilibiliPublishMaterialStat(
            file_name=self.file_name,
            size_bytes=self.size_bytes,
            duration_seconds=self.duration_seconds,
        )

    async def sha256(self) -> str:
        digest = hashlib.sha256()
        remaining = self.size_bytes
        chunk = b"\x00" * (1024 * 1024)
        while remaining > 0:
            take = min(remaining, len(chunk))
            digest.update(chunk[:take])
            remaining -= take
        return digest.hexdigest()

    async def read_range(self, offset: int, length: int) -> bytes:
        if offset < 0 or length < 1 or offset + length > self.size_bytes:
            raise AssertionError("fake reader read out of range")
        return b"\x00" * length


class BytesReader:
    def __init__(self, *, file_name: str, content: bytes, duration_seconds: int) -> None:
        self.file_name = file_name
        self.content = content
        self.duration_seconds = duration_seconds

    async def stat(self) -> BilibiliPublishMaterialStat:
        return BilibiliPublishMaterialStat(
            file_name=self.file_name,
            size_bytes=len(self.content),
            duration_seconds=self.duration_seconds,
        )

    async def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    async def read_range(self, offset: int, length: int) -> bytes:
        assert offset >= 0 and length >= 1 and offset + length <= len(self.content)
        return self.content[offset : offset + length]


class BytesCover:
    def __init__(self, *, file_name: str, content: bytes) -> None:
        self.file_name = file_name
        self.content = content

    async def describe(self) -> BilibiliCoverStat:
        return BilibiliCoverStat(file_name=self.file_name, size_bytes=len(self.content))

    async def read(self) -> bytes:
        return self.content


class InMemoryStore:
    """In-memory mirror of the PostgreSQL attempt store semantics."""

    def __init__(self) -> None:
        self.records: dict[str, BilibiliPublishAttemptRecord] = {}
        self.parts: dict[str, dict[int, int]] = {}

    def _key(self, publish_job_id: PublishJobId) -> str:
        return str(publish_job_id)

    def _require(self, publish_job_id: PublishJobId) -> BilibiliPublishAttemptRecord:
        record = self.records.get(self._key(publish_job_id))
        if record is None:
            raise BilibiliArchivePublishRejected
        return record

    async def create_prepared(
        self, record: BilibiliPublishAttemptRecord
    ) -> BilibiliPublishPreparation:
        key = self._key(record.publish_job_id)
        existing = self.records.get(key)
        if existing is not None:
            if existing.request_digest != record.request_digest:
                raise BilibiliArchivePublishRejected
            return BilibiliPublishPreparation(record=existing, replayed=True)
        self.records[key] = record
        self.parts[key] = {}
        return BilibiliPublishPreparation(record=record, replayed=False)

    async def load(self, publish_job_id: PublishJobId) -> BilibiliPublishAttemptRecord | None:
        return self.records.get(self._key(publish_job_id))

    async def record_upload_token(
        self, publish_job_id: PublishJobId, upload_token: str, at: datetime
    ) -> None:
        record = self._require(publish_job_id)
        if record.phase is not BilibiliPublishPhase.PREPARED:
            raise BilibiliArchivePublishRejected
        self.records[self._key(publish_job_id)] = replace(
            record, upload_token=upload_token, updated_at=at
        )

    async def record_part_completed(
        self, publish_job_id: PublishJobId, part_number: int, size_bytes: int, at: datetime
    ) -> bool:
        record = self._require(publish_job_id)
        if record.phase is not BilibiliPublishPhase.PREPARED:
            raise BilibiliArchivePublishRejected
        parts = self.parts[self._key(publish_job_id)]
        if part_number in parts:
            return False
        parts[part_number] = size_bytes
        self.records[self._key(publish_job_id)] = replace(record, updated_at=at)
        return True

    async def completed_part_numbers(self, publish_job_id: PublishJobId) -> frozenset[int]:
        self._require(publish_job_id)
        return frozenset(self.parts[self._key(publish_job_id)])

    async def record_video_uploaded(self, publish_job_id: PublishJobId, at: datetime) -> None:
        record = self._require(publish_job_id)
        if record.phase is not BilibiliPublishPhase.PREPARED or record.upload_token is None:
            raise BilibiliArchivePublishRejected
        self.records[self._key(publish_job_id)] = replace(
            record,
            phase=BilibiliPublishPhase.VIDEO_UPLOADED,
            video_uploaded_at=at,
            updated_at=at,
        )

    async def record_cover_url(
        self, publish_job_id: PublishJobId, cover_url: str, at: datetime
    ) -> None:
        record = self._require(publish_job_id)
        if record.phase not in {
            BilibiliPublishPhase.PREPARED,
            BilibiliPublishPhase.VIDEO_UPLOADED,
        }:
            raise BilibiliArchivePublishRejected
        self.records[self._key(publish_job_id)] = replace(
            record, cover_url=cover_url, updated_at=at
        )

    async def begin_archive_creation(self, publish_job_id: PublishJobId, at: datetime) -> bool:
        record = self._require(publish_job_id)
        if record.phase is not BilibiliPublishPhase.VIDEO_UPLOADED:
            return False
        self.records[self._key(publish_job_id)] = replace(
            record,
            phase=BilibiliPublishPhase.DISPATCHED,
            dispatched_at=at,
            updated_at=at,
        )
        return True

    async def record_submitted(
        self, publish_job_id: PublishJobId, resource_id: str, at: datetime
    ) -> None:
        record = self._require(publish_job_id)
        if record.phase is not BilibiliPublishPhase.DISPATCHED:
            raise BilibiliArchivePublishRejected
        self.records[self._key(publish_job_id)] = replace(
            record,
            phase=BilibiliPublishPhase.SUBMITTED,
            settled_at=at,
            resource_id=resource_id,
            updated_at=at,
        )

    async def record_failed(
        self,
        publish_job_id: PublishJobId,
        failure_code: PublishFailureCode,
        platform_error_code: int,
        at: datetime,
    ) -> None:
        record = self._require(publish_job_id)
        if record.phase is not BilibiliPublishPhase.DISPATCHED:
            raise BilibiliArchivePublishRejected
        self.records[self._key(publish_job_id)] = replace(
            record,
            phase=BilibiliPublishPhase.FAILED,
            settled_at=at,
            failure_code=failure_code,
            platform_error_code=platform_error_code,
            updated_at=at,
        )

    async def record_outcome_uncertain(self, publish_job_id: PublishJobId, at: datetime) -> None:
        record = self._require(publish_job_id)
        if record.phase is not BilibiliPublishPhase.DISPATCHED:
            raise BilibiliArchivePublishRejected
        self.records[self._key(publish_job_id)] = replace(
            record,
            phase=BilibiliPublishPhase.OUTCOME_UNCERTAIN,
            settled_at=at,
            updated_at=at,
        )


class ScriptedGateway:
    """Deterministic gateway double: scripted payloads or raised exceptions."""

    def __init__(self) -> None:
        self.scripts: dict[str, deque[object]] = {}
        self.calls: list[tuple[str, dict[str, object]]] = []

    def script(self, method: str, *outcomes: object) -> None:
        self.scripts.setdefault(method, deque()).extend(outcomes)

    def _next(self, method: str, call: dict[str, object]) -> object:
        self.calls.append((method, call))
        queue = self.scripts.get(method)
        if not queue:
            raise AssertionError(f"gateway method {method} was not scripted")
        outcome = queue.popleft()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def call_count(self, method: str) -> int:
        return sum(1 for name, _ in self.calls if name == method)

    async def upload_init(self, *, access_token: str, file_name: str, upload_type: str) -> object:
        return self._next(
            "upload_init",
            {"access_token": access_token, "file_name": file_name, "upload_type": upload_type},
        )

    async def upload_part(self, *, upload_token: str, part_number: int, payload: bytes) -> object:
        return self._next(
            "upload_part",
            {
                "upload_token": upload_token,
                "part_number": part_number,
                "payload_size": len(payload),
            },
        )

    async def upload_complete(self, *, upload_token: str) -> object:
        return self._next("upload_complete", {"upload_token": upload_token})

    async def upload_small_file(self, *, upload_token: str, payload: bytes) -> object:
        return self._next(
            "upload_small_file",
            {"upload_token": upload_token, "payload_size": len(payload)},
        )

    async def upload_cover(self, *, access_token: str, file_name: str, payload: bytes) -> object:
        return self._next(
            "upload_cover",
            {
                "access_token": access_token,
                "file_name": file_name,
                "payload_size": len(payload),
            },
        )

    async def archive_add(
        self,
        *,
        access_token: str,
        upload_token: str,
        submission: dict[str, object],
    ) -> object:
        return self._next(
            "archive_add",
            {
                "access_token": access_token,
                "upload_token": upload_token,
                "submission": dict(submission),
            },
        )


class FakeTokenProvider:
    def __init__(self, tokens: list[str] | None = None) -> None:
        self.tokens = tokens or ["fixture-access-token-000000000001"]
        self.index = 0
        self.refresh_calls = 0
        self.refresh_error: Exception | None = None

    async def current_access_token(self) -> str:
        return self.tokens[self.index]

    async def refresh_access_token(self) -> str:
        self.refresh_calls += 1
        if self.refresh_error is not None:
            raise self.refresh_error
        if self.index + 1 < len(self.tokens):
            self.index += 1
        return self.tokens[self.index]


def make_service(
    store: InMemoryStore | None = None,
    gateway: ScriptedGateway | None = None,
    provider: FakeTokenProvider | None = None,
) -> tuple[BilibiliArchivePublishService, InMemoryStore, ScriptedGateway, FakeTokenProvider]:
    store = store or InMemoryStore()
    gateway = gateway or ScriptedGateway()
    provider = provider or FakeTokenProvider()
    service = BilibiliArchivePublishService(
        contract=CONTRACT,
        store=store,
        gateway=gateway,
        token_provider=provider,
        clock=FixedClock(),
    )
    return service, store, gateway, provider


def small_reader(content: bytes = b"small-demo-video") -> BytesReader:
    return BytesReader(file_name="demo.mp4", content=content, duration_seconds=90)


def chunked_reader() -> VirtualZeroReader:
    return VirtualZeroReader(
        file_name="feature.mp4", size_bytes=CHUNKED_SIZE, duration_seconds=1800
    )


def fields(**overrides: object) -> BilibiliArchiveFields:
    values: dict[str, Any] = {
        "title": "契约样例一分钟看懂分片上传",
        "tid": 21,
        "tag": "科技,教程",
        "copyright": 1,
        "description": "样例描述",
        "source": None,
        "no_reprint": 0,
    }
    values.update(overrides)
    return BilibiliArchiveFields(**values)


async def prepared_job(
    service: BilibiliArchivePublishService,
    *,
    reader: BytesReader | VirtualZeroReader,
    with_cover: bool = False,
) -> tuple[PublishJobId, BilibiliPublishMaterial]:
    publish_job_id = PublishJobId.new()
    material = await service.validate_material(reader)
    preparation = await service.prepare(
        publish_job_id, material=material, fields=fields(), with_cover=with_cover
    )
    assert preparation.replayed is False
    return publish_job_id, material


def script_small_upload(gateway: ScriptedGateway) -> None:
    gateway.script("upload_init", _fixture("response-upload-init-valid"))
    gateway.script("upload_small_file", _fixture("response-part-upload-valid"))


def script_chunked_upload(gateway: ScriptedGateway, part_count: int) -> None:
    gateway.script("upload_init", _fixture("response-upload-init-valid"))
    for _ in range(part_count):
        gateway.script("upload_part", _fixture("response-part-upload-valid"))
    gateway.script("upload_complete", _fixture("response-upload-complete-valid"))


# ---------------------------------------------------------------------------
# Material validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_material_returns_facts_with_streaming_digest() -> None:
    service, _, _, _ = make_service()
    content = b"small-demo-video"
    material = await service.validate_material(small_reader(content))
    assert material.file_name == "demo.mp4"
    assert material.size_bytes == len(content)
    assert material.duration_seconds == 90
    assert material.sha256 == hashlib.sha256(content).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("file_name", "size_bytes", "duration_seconds"),
    [
        ("demo.mkv", 1024, 90),
        ("demo", 1024, 90),
        ("../demo.mp4", 1024, 90),
        ("demo.mp4", 0, 90),
        ("demo.mp4", CONTRACT.video_max_bytes + 1, 90),
        ("demo.mp4", 1024, 0),
        ("demo.mp4", 1024, CONTRACT.video_max_duration_seconds + 1),
    ],
)
async def test_validate_material_rejects_out_of_contract_material(
    file_name: str, size_bytes: int, duration_seconds: int
) -> None:
    service, _, _, _ = make_service()
    reader = VirtualZeroReader(
        file_name=file_name, size_bytes=size_bytes, duration_seconds=duration_seconds
    )
    with pytest.raises(BilibiliArchivePublishRejected):
        await service.validate_material(reader)


# ---------------------------------------------------------------------------
# Idempotent preparation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prepare_persists_intent_with_request_digest_and_part_plan() -> None:
    service, store, _, _ = make_service()
    publish_job_id, material = await prepared_job(service, reader=chunked_reader())
    record = store.records[str(publish_job_id)]
    assert record.phase is BilibiliPublishPhase.PREPARED
    assert record.upload_type is BilibiliUploadType.CHUNKED
    assert record.part_size_bytes == PART_SIZE
    assert record.part_count == 14
    assert record.material == material
    assert len(record.request_digest) == 64
    assert record.upload_token is None


@pytest.mark.asyncio
async def test_prepare_small_material_uses_small_upload_type() -> None:
    service, store, _, _ = make_service()
    publish_job_id = PublishJobId.new()
    material = BilibiliPublishMaterial(
        file_name="demo.mp4",
        size_bytes=SMALL_MAX,
        duration_seconds=90,
        sha256="ab" * 32,
    )
    await service.prepare(publish_job_id, material=material, fields=fields(), with_cover=False)
    record = store.records[str(publish_job_id)]
    assert record.upload_type is BilibiliUploadType.SMALL
    assert record.part_count == 0
    assert record.part_size_bytes == 0


@pytest.mark.asyncio
async def test_prepare_over_threshold_material_uses_chunked_upload_type() -> None:
    service, store, _, _ = make_service()
    publish_job_id = PublishJobId.new()
    material = BilibiliPublishMaterial(
        file_name="demo.mp4",
        size_bytes=SMALL_MAX + 1,
        duration_seconds=90,
        sha256="ab" * 32,
    )
    await service.prepare(publish_job_id, material=material, fields=fields(), with_cover=False)
    assert store.records[str(publish_job_id)].upload_type is BilibiliUploadType.CHUNKED


@pytest.mark.asyncio
async def test_prepare_is_idempotent_for_the_same_intent() -> None:
    service, _, _, _ = make_service()
    publish_job_id = PublishJobId.new()
    material = await service.validate_material(small_reader())
    first = await service.prepare(
        publish_job_id, material=material, fields=fields(), with_cover=False
    )
    second = await service.prepare(
        publish_job_id, material=material, fields=fields(), with_cover=False
    )
    assert first.replayed is False
    assert second.replayed is True
    assert second.record.request_digest == first.record.request_digest


@pytest.mark.asyncio
async def test_prepare_rejects_changed_intent_for_the_same_job() -> None:
    service, _, _, _ = make_service()
    publish_job_id = PublishJobId.new()
    material = await service.validate_material(small_reader())
    await service.prepare(publish_job_id, material=material, fields=fields(), with_cover=False)
    with pytest.raises(BilibiliArchivePublishRejected):
        await service.prepare(
            publish_job_id,
            material=material,
            fields=fields(title="换了标题的第二次准备"),
            with_cover=False,
        )


@pytest.mark.asyncio
async def test_prepare_rejects_fields_outside_the_locked_contract() -> None:
    service, _, _, _ = make_service()
    publish_job_id = PublishJobId.new()
    material = await service.validate_material(small_reader())
    for bad in (
        fields(title="超" * 81),
        fields(copyright=2, source=None),
        fields(copyright=1, source="https://example.com/origin"),
        fields(tag="a," + "b" * 200),
        fields(no_reprint=2),
    ):
        with pytest.raises(BilibiliArchivePublishRejected):
            await service.prepare(publish_job_id, material=material, fields=bad, with_cover=False)


# ---------------------------------------------------------------------------
# Upload orchestration: small file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_small_file_upload_initializes_and_uploads_once() -> None:
    service, store, gateway, _ = make_service()
    reader = small_reader()
    publish_job_id, _ = await prepared_job(service, reader=reader)
    script_small_upload(gateway)
    record = await service.upload_video(publish_job_id, reader)
    assert record.phase is BilibiliPublishPhase.VIDEO_UPLOADED
    assert record.upload_token == "fixture-upload-token-000000000000"
    init_call = next(call for name, call in gateway.calls if name == "upload_init")
    assert init_call["upload_type"] == "0"
    assert store.records[str(publish_job_id)].video_uploaded_at is not None


@pytest.mark.asyncio
async def test_upload_video_rejects_replaced_material_before_any_network_call() -> None:
    service, _, gateway, _ = make_service()
    publish_job_id, _ = await prepared_job(service, reader=small_reader())
    replaced = BytesReader(file_name="demo.mp4", content=b"tampered-content", duration_seconds=90)
    with pytest.raises(BilibiliArchivePublishRejected):
        await service.upload_video(publish_job_id, replaced)
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_upload_video_is_idempotent_after_video_uploaded() -> None:
    service, _, gateway, _ = make_service()
    reader = small_reader()
    publish_job_id, _ = await prepared_job(service, reader=reader)
    script_small_upload(gateway)
    await service.upload_video(publish_job_id, reader)
    record = await service.upload_video(publish_job_id, reader)
    assert record.phase is BilibiliPublishPhase.VIDEO_UPLOADED
    assert gateway.call_count("upload_init") == 1
    assert gateway.call_count("upload_small_file") == 1


# ---------------------------------------------------------------------------
# Upload orchestration: chunked with resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chunked_upload_sends_every_planned_part_then_completes() -> None:
    service, store, gateway, _ = make_service()
    reader = chunked_reader()
    publish_job_id, _ = await prepared_job(service, reader=reader)
    script_chunked_upload(gateway, 14)
    record = await service.upload_video(publish_job_id, reader)
    assert record.phase is BilibiliPublishPhase.VIDEO_UPLOADED
    part_calls = [call for name, call in gateway.calls if name == "upload_part"]
    assert [call["part_number"] for call in part_calls] == list(range(1, 15))
    assert part_calls[0]["payload_size"] == PART_SIZE
    assert part_calls[-1]["payload_size"] == CHUNKED_SIZE - 13 * PART_SIZE
    assert store.parts[str(publish_job_id)].keys() == set(range(1, 15))


@pytest.mark.asyncio
async def test_chunked_upload_resumes_from_persisted_part_progress() -> None:
    service, store, gateway, _ = make_service()
    reader = chunked_reader()
    publish_job_id, _ = await prepared_job(service, reader=reader)
    gateway.script("upload_init", _fixture("response-upload-init-valid"))
    gateway.script("upload_part", _fixture("response-part-upload-valid"))
    gateway.script("upload_part", _fixture("response-part-upload-valid"))
    gateway.script("upload_part", BilibiliGatewayUnreachable())
    with pytest.raises(BilibiliGatewayUnreachable):
        await service.upload_video(publish_job_id, reader)
    assert store.records[str(publish_job_id)].phase is BilibiliPublishPhase.PREPARED
    assert store.parts[str(publish_job_id)].keys() == {1, 2}

    resumed_service = BilibiliArchivePublishService(
        contract=CONTRACT,
        store=store,
        gateway=gateway,
        token_provider=FakeTokenProvider(),
        clock=FixedClock(),
    )
    for _ in range(12):
        gateway.script("upload_part", _fixture("response-part-upload-valid"))
    gateway.script("upload_complete", _fixture("response-upload-complete-valid"))
    record = await resumed_service.upload_video(publish_job_id, reader)
    assert record.phase is BilibiliPublishPhase.VIDEO_UPLOADED
    part_numbers = [call["part_number"] for name, call in gateway.calls if name == "upload_part"]
    assert part_numbers == [1, 2, 3, *range(3, 15)]
    assert gateway.call_count("upload_init") == 1


@pytest.mark.asyncio
async def test_chunked_upload_rejects_persisted_parts_outside_the_plan() -> None:
    service, store, gateway, _ = make_service()
    reader = chunked_reader()
    publish_job_id, _ = await prepared_job(service, reader=reader)
    store.parts[str(publish_job_id)][99] = PART_SIZE
    with pytest.raises(BilibiliArchivePublishRejected):
        await service.upload_video(publish_job_id, reader)
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_upload_complete_platform_error_is_classified_and_resumable() -> None:
    service, store, gateway, _ = make_service()
    reader = chunked_reader()
    publish_job_id, _ = await prepared_job(service, reader=reader)
    gateway.script("upload_init", _fixture("response-upload-init-valid"))
    for _ in range(14):
        gateway.script("upload_part", _fixture("response-part-upload-valid"))
    gateway.script("upload_complete", _fixture("response-upload-complete-error-expired"))
    with pytest.raises(BilibiliPublishStepFailed) as failure:
        await service.upload_video(publish_job_id, reader)
    assert failure.value.rejection.failure_code is PublishFailureCode.INVALID_INPUT
    assert store.records[str(publish_job_id)].phase is BilibiliPublishPhase.PREPARED


@pytest.mark.asyncio
async def test_rate_limited_part_upload_maps_to_dependency_unavailable() -> None:
    service, store, gateway, _ = make_service()
    reader = chunked_reader()
    publish_job_id, _ = await prepared_job(service, reader=reader)
    gateway.script("upload_init", _fixture("response-upload-init-valid"))
    gateway.script("upload_part", _error_payload(127306))
    with pytest.raises(BilibiliPublishStepFailed) as failure:
        await service.upload_video(publish_job_id, reader)
    assert failure.value.rejection.failure_code is PublishFailureCode.DEPENDENCY_UNAVAILABLE
    assert store.records[str(publish_job_id)].phase is BilibiliPublishPhase.PREPARED


@pytest.mark.asyncio
async def test_init_disconnect_leaves_the_attempt_resumable() -> None:
    service, store, gateway, _ = make_service()
    reader = small_reader()
    publish_job_id, _ = await prepared_job(service, reader=reader)
    gateway.script("upload_init", BilibiliGatewayUnreachable())
    with pytest.raises(BilibiliGatewayUnreachable):
        await service.upload_video(publish_job_id, reader)
    record = store.records[str(publish_job_id)]
    assert record.phase is BilibiliPublishPhase.PREPARED
    assert record.upload_token is None


# ---------------------------------------------------------------------------
# Access-token expiry mid-flight
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_access_token_on_init_refreshes_once_and_retries() -> None:
    provider = FakeTokenProvider(
        tokens=["fixture-access-token-expired", "fixture-access-token-fresh"]
    )
    service, _, gateway, _ = make_service(provider=provider)
    reader = small_reader()
    publish_job_id, _ = await prepared_job(service, reader=reader)
    gateway.script("upload_init", _error_payload(127001))
    gateway.script("upload_init", _fixture("response-upload-init-valid"))
    gateway.script("upload_small_file", _fixture("response-part-upload-valid"))
    record = await service.upload_video(publish_job_id, reader)
    assert record.phase is BilibiliPublishPhase.VIDEO_UPLOADED
    assert provider.refresh_calls == 1
    init_tokens = [call["access_token"] for name, call in gateway.calls if name == "upload_init"]
    assert init_tokens == ["fixture-access-token-expired", "fixture-access-token-fresh"]


@pytest.mark.asyncio
async def test_second_auth_rejection_after_refresh_fails_without_more_retries() -> None:
    provider = FakeTokenProvider(
        tokens=["fixture-access-token-expired", "fixture-access-token-still-bad"]
    )
    service, _, gateway, _ = make_service(provider=provider)
    reader = small_reader()
    publish_job_id, _ = await prepared_job(service, reader=reader)
    gateway.script("upload_init", _error_payload(127001))
    gateway.script("upload_init", _error_payload(127001))
    with pytest.raises(BilibiliPublishStepFailed):
        await service.upload_video(publish_job_id, reader)
    assert provider.refresh_calls == 1
    assert gateway.call_count("upload_init") == 2


@pytest.mark.asyncio
async def test_refresh_failure_surfaces_as_unavailable() -> None:
    provider = FakeTokenProvider(tokens=["fixture-access-token-expired"])
    provider.refresh_error = RuntimeError("refresh endpoint down")
    service, _, gateway, _ = make_service(provider=provider)
    reader = small_reader()
    publish_job_id, _ = await prepared_job(service, reader=reader)
    gateway.script("upload_init", _error_payload(127001))
    with pytest.raises(BilibiliArchivePublishUnavailable):
        await service.upload_video(publish_job_id, reader)


# ---------------------------------------------------------------------------
# Cover upload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cover_upload_records_https_url() -> None:
    service, store, gateway, _ = make_service()
    reader = small_reader()
    publish_job_id, _ = await prepared_job(service, reader=reader, with_cover=True)
    gateway.script("upload_cover", _fixture("response-cover-upload-valid"))
    url = await service.upload_cover(
        publish_job_id, BytesCover(file_name="cover.png", content=b"png-bytes")
    )
    assert url.startswith("https://")
    assert store.records[str(publish_job_id)].cover_url == url


@pytest.mark.asyncio
async def test_cover_upload_is_idempotent_once_recorded() -> None:
    service, _, gateway, _ = make_service()
    reader = small_reader()
    publish_job_id, _ = await prepared_job(service, reader=reader, with_cover=True)
    gateway.script("upload_cover", _fixture("response-cover-upload-valid"))
    cover = BytesCover(file_name="cover.png", content=b"png-bytes")
    first = await service.upload_cover(publish_job_id, cover)
    second = await service.upload_cover(publish_job_id, cover)
    assert first == second
    assert gateway.call_count("upload_cover") == 1


@pytest.mark.asyncio
async def test_cover_upload_rejects_bad_material_and_uncovered_jobs() -> None:
    service, _, gateway, _ = make_service()
    reader = small_reader()
    covered_job, _ = await prepared_job(service, reader=reader, with_cover=True)
    oversized = BytesCover(file_name="cover.png", content=b"x")
    oversized_stat = BilibiliCoverStat(
        file_name="cover.png", size_bytes=CONTRACT.cover_max_bytes + 1
    )

    class OversizedCover:
        async def describe(self) -> BilibiliCoverStat:
            return oversized_stat

        async def read(self) -> bytes:
            return b"x"

    with pytest.raises(BilibiliArchivePublishRejected):
        await service.upload_cover(covered_job, OversizedCover())
    with pytest.raises(BilibiliArchivePublishRejected):
        await service.upload_cover(
            covered_job, BytesCover(file_name="cover.gif", content=b"gif-bytes")
        )

    second_service, _, second_gateway, _ = make_service()
    plain_reader = small_reader()
    plain_job, _ = await prepared_job(second_service, reader=plain_reader, with_cover=False)
    with pytest.raises(BilibiliArchivePublishRejected):
        await second_service.upload_cover(plain_job, oversized)
    assert gateway.calls == []
    assert second_gateway.calls == []


@pytest.mark.asyncio
async def test_cover_upload_malformed_response_fails_closed() -> None:
    service, store, gateway, _ = make_service()
    reader = small_reader()
    publish_job_id, _ = await prepared_job(service, reader=reader, with_cover=True)
    gateway.script("upload_cover", _fixture("response-cover-upload-malformed-url"))
    with pytest.raises(InvalidBilibiliOpenApiMessage):
        await service.upload_cover(
            publish_job_id, BytesCover(file_name="cover.png", content=b"png-bytes")
        )
    assert store.records[str(publish_job_id)].cover_url is None


# ---------------------------------------------------------------------------
# Single-admission archive creation
# ---------------------------------------------------------------------------


async def uploaded_job(
    service: BilibiliArchivePublishService,
    gateway: ScriptedGateway,
    *,
    with_cover: bool = False,
) -> PublishJobId:
    reader = small_reader()
    publish_job_id, _ = await prepared_job(service, reader=reader, with_cover=with_cover)
    script_small_upload(gateway)
    await service.upload_video(publish_job_id, reader)
    if with_cover:
        gateway.script("upload_cover", _fixture("response-cover-upload-valid"))
        await service.upload_cover(
            publish_job_id, BytesCover(file_name="cover.png", content=b"png-bytes")
        )
    return publish_job_id


@pytest.mark.asyncio
async def test_create_archive_submits_once_and_persists_the_receipt() -> None:
    service, store, gateway, _ = make_service()
    publish_job_id = await uploaded_job(service, gateway, with_cover=True)
    gateway.script("archive_add", _fixture("response-archive-add-valid"))
    receipt = await service.create_archive(publish_job_id)
    assert isinstance(receipt, BilibiliArchiveCreationReceipt)
    assert receipt.resource_id == "BV17B4y1s7R1"
    assert receipt.replayed is False
    record = store.records[str(publish_job_id)]
    assert record.phase is BilibiliPublishPhase.SUBMITTED
    assert record.resource_id == "BV17B4y1s7R1"
    add_call = next(call for name, call in gateway.calls if name == "archive_add")
    submission = add_call["submission"]
    assert isinstance(submission, dict)
    assert submission["title"] == "契约样例一分钟看懂分片上传"
    assert submission["cover_url"] == store.records[str(publish_job_id)].cover_url
    assert receipt.request_digest == record.request_digest


@pytest.mark.asyncio
async def test_create_archive_replays_the_recorded_receipt_without_resubmitting() -> None:
    service, _, gateway, _ = make_service()
    publish_job_id = await uploaded_job(service, gateway)
    gateway.script("archive_add", _fixture("response-archive-add-valid"))
    first = await service.create_archive(publish_job_id)
    second = await service.create_archive(publish_job_id)
    assert second.replayed is True
    assert second.resource_id == first.resource_id
    assert gateway.call_count("archive_add") == 1


@pytest.mark.asyncio
async def test_create_archive_requires_completed_upload_and_recorded_cover() -> None:
    service, _, gateway, _ = make_service()
    reader = small_reader()
    pending_job, _ = await prepared_job(service, reader=reader)
    with pytest.raises(BilibiliArchivePublishRejected):
        await service.create_archive(pending_job)

    covered_service, _, covered_gateway, _ = make_service()
    covered_reader = small_reader()
    covered_job, _ = await prepared_job(covered_service, reader=covered_reader, with_cover=True)
    script_small_upload(covered_gateway)
    await covered_service.upload_video(covered_job, covered_reader)
    with pytest.raises(BilibiliArchivePublishRejected):
        await covered_service.create_archive(covered_job)
    assert gateway.call_count("archive_add") == 0
    assert covered_gateway.call_count("archive_add") == 0


@pytest.mark.asyncio
async def test_lost_create_response_settles_outcome_uncertain_and_never_resends() -> None:
    service, store, gateway, _ = make_service()
    publish_job_id = await uploaded_job(service, gateway)
    gateway.script("archive_add", BilibiliGatewayUnreachable())
    with pytest.raises(BilibiliArchiveOutcomeUncertain):
        await service.create_archive(publish_job_id)
    record = store.records[str(publish_job_id)]
    assert record.phase is BilibiliPublishPhase.OUTCOME_UNCERTAIN
    assert record.resource_id is None

    with pytest.raises(BilibiliArchivePublishRejected):
        await service.create_archive(publish_job_id)
    assert gateway.call_count("archive_add") == 1


@pytest.mark.asyncio
async def test_malformed_create_response_settles_outcome_uncertain() -> None:
    service, store, gateway, _ = make_service()
    publish_job_id = await uploaded_job(service, gateway)
    gateway.script("archive_add", _fixture("response-archive-add-malformed-resource"))
    with pytest.raises(BilibiliArchiveOutcomeUncertain):
        await service.create_archive(publish_job_id)
    assert store.records[str(publish_job_id)].phase is BilibiliPublishPhase.OUTCOME_UNCERTAIN


@pytest.mark.asyncio
async def test_dispatched_leftover_from_a_crash_is_fenced_without_resending() -> None:
    service, store, gateway, _ = make_service()
    publish_job_id = await uploaded_job(service, gateway)
    record = store.records[str(publish_job_id)]
    store.records[str(publish_job_id)] = replace(
        record,
        phase=BilibiliPublishPhase.DISPATCHED,
        dispatched_at=record.updated_at,
    )
    with pytest.raises(BilibiliArchiveOutcomeUncertain):
        await service.create_archive(publish_job_id)
    assert store.records[str(publish_job_id)].phase is BilibiliPublishPhase.DISPATCHED
    assert gateway.call_count("archive_add") == 0


@pytest.mark.asyncio
async def test_create_archive_platform_rejection_is_classified_and_terminal() -> None:
    service, store, gateway, _ = make_service()
    publish_job_id = await uploaded_job(service, gateway)
    gateway.script("archive_add", _fixture("response-archive-add-error-sensitive"))
    with pytest.raises(BilibiliPublishStepFailed) as failure:
        await service.create_archive(publish_job_id)
    record = store.records[str(publish_job_id)]
    assert record.phase is BilibiliPublishPhase.FAILED
    assert record.failure_code is failure.value.rejection.failure_code
    assert record.platform_error_code == failure.value.rejection.code
    with pytest.raises(BilibiliArchivePublishRejected):
        await service.create_archive(publish_job_id)
    assert gateway.call_count("archive_add") == 1


@pytest.mark.asyncio
async def test_unknown_create_error_code_fails_closed_as_platform_error() -> None:
    service, store, gateway, _ = make_service()
    publish_job_id = await uploaded_job(service, gateway)
    gateway.script("archive_add", _fixture("response-archive-add-error-unknown-code"))
    with pytest.raises(BilibiliPublishStepFailed) as failure:
        await service.create_archive(publish_job_id)
    assert failure.value.rejection.failure_code is PublishFailureCode.PLATFORM_ERROR
    assert store.records[str(publish_job_id)].phase is BilibiliPublishPhase.FAILED


@pytest.mark.asyncio
async def test_rate_limited_create_settles_failed_with_dependency_unavailable() -> None:
    service, store, gateway, _ = make_service()
    publish_job_id = await uploaded_job(service, gateway)
    gateway.script("archive_add", _fixture("response-archive-add-error-frequency"))
    with pytest.raises(BilibiliPublishStepFailed) as failure:
        await service.create_archive(publish_job_id)
    assert failure.value.rejection.failure_code is PublishFailureCode.DEPENDENCY_UNAVAILABLE
    assert store.records[str(publish_job_id)].phase is BilibiliPublishPhase.FAILED


@pytest.mark.asyncio
async def test_concurrent_admission_loss_does_not_call_the_platform() -> None:
    service, store, gateway, _ = make_service()
    publish_job_id = await uploaded_job(service, gateway)

    class SingleLossStore(InMemoryStore):
        def __init__(self, inner: InMemoryStore) -> None:
            self.records = inner.records
            self.parts = inner.parts

        async def begin_archive_creation(self, publish_job_id: PublishJobId, at: datetime) -> bool:
            return False

    racing_service = BilibiliArchivePublishService(
        contract=CONTRACT,
        store=SingleLossStore(store),
        gateway=gateway,
        token_provider=FakeTokenProvider(),
        clock=FixedClock(),
    )
    with pytest.raises(BilibiliArchivePublishRejected):
        await racing_service.create_archive(publish_job_id)
    assert gateway.call_count("archive_add") == 0


@pytest.mark.asyncio
async def test_unknown_job_is_rejected_everywhere() -> None:
    service, _, gateway, _ = make_service()
    missing = PublishJobId.new()
    with pytest.raises(BilibiliArchivePublishRejected):
        await service.upload_video(missing, small_reader())
    with pytest.raises(BilibiliArchivePublishRejected):
        await service.upload_cover(missing, BytesCover(file_name="cover.png", content=b"x"))
    with pytest.raises(BilibiliArchivePublishRejected):
        await service.create_archive(missing)
    assert gateway.calls == []
