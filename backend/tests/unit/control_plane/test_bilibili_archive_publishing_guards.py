"""PB-03: fail-closed guard coverage for the publish orchestration surface."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from test_bilibili_archive_publishing import (
    CONTRACT,
    NOW,
    BytesCover,
    FakeTokenProvider,
    FixedClock,
    InMemoryStore,
    ScriptedGateway,
    _error_payload,
    _fixture,
    fields,
    make_service,
    prepared_job,
    script_small_upload,
    small_reader,
    uploaded_job,
)

from automation_tool.control_plane.application.bilibili_archive_publishing import (
    BilibiliArchiveCreationReceipt,
    BilibiliArchiveFields,
    BilibiliArchiveOutcomeUncertain,
    BilibiliArchivePublishRejected,
    BilibiliArchivePublishService,
    BilibiliArchivePublishUnavailable,
    BilibiliCoverStat,
    BilibiliPublishAttemptRecord,
    BilibiliPublishMaterial,
    BilibiliPublishMaterialStat,
    BilibiliPublishPhase,
    BilibiliPublishPreparation,
    BilibiliPublishStepFailed,
    BilibiliUploadType,
    SystemBilibiliPublishClock,
)
from automation_tool.control_plane.domain.video_publishing import PublishJobId


def valid_record_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "publish_job_id": PublishJobId.new(),
        "phase": BilibiliPublishPhase.PREPARED,
        "request_digest": "ab" * 32,
        "material": BilibiliPublishMaterial(
            file_name="demo.mp4", size_bytes=1024, duration_seconds=90, sha256="cd" * 32
        ),
        "fields": fields(),
        "upload_type": BilibiliUploadType.SMALL,
        "part_size_bytes": 0,
        "part_count": 0,
        "has_cover": False,
        "upload_token": None,
        "cover_url": None,
        "video_uploaded_at": None,
        "dispatched_at": None,
        "settled_at": None,
        "resource_id": None,
        "failure_code": None,
        "platform_error_code": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return values


def test_material_stat_and_fields_and_cover_stat_reject_bad_types() -> None:
    with pytest.raises(BilibiliArchivePublishRejected):
        BilibiliPublishMaterialStat(file_name="demo.mp4", size_bytes="big", duration_seconds=1)  # type: ignore[arg-type]
    with pytest.raises(BilibiliArchivePublishRejected):
        BilibiliArchiveFields(
            title="标题",
            tid="21",  # type: ignore[arg-type]
            tag="科技",
            copyright=1,
            description=None,
            source=None,
            no_reprint=0,
        )
    with pytest.raises(BilibiliArchivePublishRejected):
        BilibiliCoverStat(file_name="cover.png", size_bytes="big")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "overrides",
    [
        {"request_digest": "not-a-digest"},
        {"platform_error_code": 0, "failure_code": None},
        {"part_count": 3},
        {"upload_type": BilibiliUploadType.CHUNKED},
        {"cover_url": "https://example.com/cover.jpg"},
        {"has_cover": True, "cover_url": "http://example.com/cover.jpg"},
        {"created_at": datetime(2026, 7, 23, 1, 0)},
        {"updated_at": NOW.replace(year=2025)},
        {"video_uploaded_at": NOW},
        {"resource_id": "BV17B4y1s7R1"},
        {"failure_code": None, "platform_error_code": 123013},
        {
            "phase": BilibiliPublishPhase.VIDEO_UPLOADED,
            "video_uploaded_at": NOW,
            "upload_token": None,
        },
        {"phase": BilibiliPublishPhase.DISPATCHED, "dispatched_at": NOW},
        {
            "phase": BilibiliPublishPhase.SUBMITTED,
            "video_uploaded_at": NOW,
            "dispatched_at": NOW,
            "settled_at": NOW,
            "upload_token": "fixture-upload-token",
            "resource_id": None,
        },
        {
            "phase": BilibiliPublishPhase.FAILED,
            "video_uploaded_at": NOW,
            "dispatched_at": NOW,
            "settled_at": NOW,
            "upload_token": "fixture-upload-token",
            "failure_code": None,
            "platform_error_code": None,
        },
    ],
)
def test_attempt_record_rejects_inconsistent_shapes(overrides: dict[str, Any]) -> None:
    with pytest.raises(BilibiliArchivePublishRejected):
        BilibiliPublishAttemptRecord(**valid_record_values(**overrides))


def test_preparation_and_receipt_reject_bad_values() -> None:
    record = BilibiliPublishAttemptRecord(**valid_record_values())
    with pytest.raises(BilibiliArchivePublishRejected):
        BilibiliPublishPreparation(record=record, replayed="yes")  # type: ignore[arg-type]
    with pytest.raises(BilibiliArchivePublishRejected):
        BilibiliArchiveCreationReceipt(
            publish_job_id=PublishJobId.new(),
            resource_id="not-a-bv",
            request_digest="ab" * 32,
            replayed=False,
        )


def test_service_constructor_rejects_wrong_collaborators() -> None:
    store = InMemoryStore()
    gateway = ScriptedGateway()
    provider = FakeTokenProvider()
    with pytest.raises(BilibiliArchivePublishRejected):
        BilibiliArchivePublishService(
            contract="contract",  # type: ignore[arg-type]
            store=store,
            gateway=gateway,
            token_provider=provider,
        )
    with pytest.raises(BilibiliArchivePublishRejected):
        BilibiliArchivePublishService(
            contract=CONTRACT,
            store="store",  # type: ignore[arg-type]
            gateway=gateway,
            token_provider=provider,
        )


def test_system_clock_returns_utc_now() -> None:
    value = SystemBilibiliPublishClock().now()
    assert value.tzinfo is not None
    assert abs((datetime.now(UTC) - value).total_seconds()) < 60


class BrokenClock:
    def __init__(self, value: object = None) -> None:
        self.value = value

    def now(self) -> Any:
        if self.value is None:
            raise RuntimeError("clock down")
        return self.value


@pytest.mark.asyncio
async def test_clock_failures_surface_as_unavailable() -> None:
    for clock in (BrokenClock(), BrokenClock(datetime(2026, 7, 23, 1, 0))):
        service = BilibiliArchivePublishService(
            contract=CONTRACT,
            store=InMemoryStore(),
            gateway=ScriptedGateway(),
            token_provider=FakeTokenProvider(),
            clock=clock,
        )
        material = await service.validate_material(small_reader())
        with pytest.raises(BilibiliArchivePublishUnavailable):
            await service.prepare(
                PublishJobId.new(), material=material, fields=fields(), with_cover=False
            )


class NoTokenProvider:
    def __init__(self, current: object = None, refreshed: object = "") -> None:
        self._current = current
        self._refreshed = refreshed

    async def current_access_token(self) -> Any:
        if self._current is None:
            raise RuntimeError("token store down")
        return self._current

    async def refresh_access_token(self) -> Any:
        return self._refreshed


@pytest.mark.asyncio
async def test_token_provider_failures_surface_as_unavailable() -> None:
    reader = small_reader()
    for provider in (NoTokenProvider(), NoTokenProvider(current="")):
        service = BilibiliArchivePublishService(
            contract=CONTRACT,
            store=InMemoryStore(),
            gateway=ScriptedGateway(),
            token_provider=provider,
            clock=FixedClock(),
        )
        publish_job_id, _ = await prepared_job(service, reader=reader)
        with pytest.raises(BilibiliArchivePublishUnavailable):
            await service.upload_video(publish_job_id, reader)


@pytest.mark.asyncio
async def test_empty_refreshed_token_surfaces_as_unavailable() -> None:
    provider = NoTokenProvider(current="fixture-access-token-expired", refreshed="")
    service, _, gateway, _ = make_service(provider=provider)  # type: ignore[arg-type]
    reader = small_reader()
    publish_job_id, _ = await prepared_job(service, reader=reader)
    gateway.script("upload_init", _error_payload(127001))
    with pytest.raises(BilibiliArchivePublishUnavailable):
        await service.upload_video(publish_job_id, reader)


class BrokenStore(InMemoryStore):
    def __init__(self, inner: InMemoryStore, broken_method: str) -> None:
        self.records = inner.records
        self.parts = inner.parts
        self._broken_method = broken_method

    def _maybe_break(self, name: str) -> None:
        if name == self._broken_method:
            raise RuntimeError("store down")

    async def load(self, publish_job_id: PublishJobId) -> Any:
        self._maybe_break("load")
        return await super().load(publish_job_id)

    async def create_prepared(self, record: BilibiliPublishAttemptRecord) -> Any:
        self._maybe_break("create_prepared")
        return await super().create_prepared(record)

    async def record_upload_token(
        self, publish_job_id: PublishJobId, upload_token: str, at: datetime
    ) -> None:
        self._maybe_break("record_upload_token")
        await super().record_upload_token(publish_job_id, upload_token, at)


@pytest.mark.asyncio
async def test_store_failures_surface_as_unavailable() -> None:
    reader = small_reader()
    base_service, store, gateway, _ = make_service()
    publish_job_id, material = await prepared_job(base_service, reader=reader)

    for broken_method in ("load", "create_prepared", "record_upload_token"):
        broken = BrokenStore(store, broken_method)
        service = BilibiliArchivePublishService(
            contract=CONTRACT,
            store=broken,
            gateway=gateway,
            token_provider=FakeTokenProvider(),
            clock=FixedClock(),
        )
        with pytest.raises(BilibiliArchivePublishUnavailable):
            if broken_method == "create_prepared":
                await service.prepare(
                    publish_job_id, material=material, fields=fields(), with_cover=False
                )
            else:
                gateway.script("upload_init", _fixture("response-upload-init-valid"))
                await service.upload_video(publish_job_id, reader)


class WrongResultStore(InMemoryStore):
    def __init__(self, inner: InMemoryStore, mode: str) -> None:
        self.records = inner.records
        self.parts = inner.parts
        self._mode = mode

    async def load(self, publish_job_id: PublishJobId) -> Any:
        if self._mode == "wrong_job":
            other = PublishJobId.new()
            record = await super().load(publish_job_id)
            assert record is not None
            return replace(record, publish_job_id=other)
        return await super().load(publish_job_id)

    async def create_prepared(self, record: BilibiliPublishAttemptRecord) -> Any:
        if self._mode == "wrong_preparation":
            return "not-a-preparation"
        return await super().create_prepared(record)


@pytest.mark.asyncio
async def test_store_contract_violations_surface_as_unavailable() -> None:
    reader = small_reader()
    base_service, store, _, _ = make_service()
    publish_job_id, material = await prepared_job(base_service, reader=reader)

    wrong_job = BilibiliArchivePublishService(
        contract=CONTRACT,
        store=WrongResultStore(store, "wrong_job"),
        gateway=ScriptedGateway(),
        token_provider=FakeTokenProvider(),
        clock=FixedClock(),
    )
    with pytest.raises(BilibiliArchivePublishUnavailable):
        await wrong_job.upload_video(publish_job_id, reader)

    wrong_preparation = BilibiliArchivePublishService(
        contract=CONTRACT,
        store=WrongResultStore(store, "wrong_preparation"),
        gateway=ScriptedGateway(),
        token_provider=FakeTokenProvider(),
        clock=FixedClock(),
    )
    with pytest.raises(BilibiliArchivePublishUnavailable):
        await wrong_preparation.prepare(
            publish_job_id, material=material, fields=fields(), with_cover=False
        )


class WrongReader:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    async def stat(self) -> Any:
        if self.mode == "stat_raises":
            raise RuntimeError("reader down")
        if self.mode == "stat_wrong_type":
            return "not-a-stat"
        return BilibiliPublishMaterialStat(file_name="demo.mp4", size_bytes=16, duration_seconds=90)

    async def sha256(self) -> Any:
        if self.mode == "sha_raises":
            raise RuntimeError("reader down")
        return hashlib.sha256(b"x" * 16).hexdigest()

    async def read_range(self, offset: int, length: int) -> Any:
        if self.mode == "short_read":
            return b"x"
        raise RuntimeError("reader down")


@pytest.mark.asyncio
async def test_reader_failures_and_wrong_shapes() -> None:
    service, _, _, _ = make_service()
    with pytest.raises(BilibiliArchivePublishRejected):
        await service.validate_material("not-a-reader")  # type: ignore[arg-type]
    for mode in ("stat_raises", "stat_wrong_type", "sha_raises"):
        with pytest.raises(BilibiliArchivePublishUnavailable):
            await service.validate_material(WrongReader(mode))


@pytest.mark.asyncio
async def test_read_failures_during_upload_surface_as_unavailable() -> None:
    for mode in ("short_read", "read_raises"):
        service, _, gateway, _ = make_service()
        reader = WrongReader(mode)
        publish_job_id = PublishJobId.new()
        material = await service.validate_material(reader)
        await service.prepare(publish_job_id, material=material, fields=fields(), with_cover=False)
        gateway.script("upload_init", _fixture("response-upload-init-valid"))
        with pytest.raises(BilibiliArchivePublishUnavailable):
            await service.upload_video(publish_job_id, reader)


@pytest.mark.asyncio
async def test_prepare_rejects_wrong_argument_types() -> None:
    service, _, _, _ = make_service()
    material = await service.validate_material(small_reader())
    with pytest.raises(BilibiliArchivePublishRejected):
        await service.prepare(
            "job",  # type: ignore[arg-type]
            material=material,
            fields=fields(),
            with_cover=False,
        )
    with pytest.raises(BilibiliArchivePublishRejected):
        await service.prepare(
            PublishJobId.new(),
            material=material,
            fields=fields(),
            with_cover="no",  # type: ignore[arg-type]
        )
    with pytest.raises(BilibiliArchivePublishRejected):
        await service._load("not-a-job-id")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_control_character_file_name_fails_at_init_validation() -> None:
    service, _, gateway, _ = make_service()
    reader = small_reader()
    publish_job_id = PublishJobId.new()
    material = await service.validate_material(reader)
    tricky = replace(material, file_name="de\x01mo.mp4")
    await service.prepare(publish_job_id, material=tricky, fields=fields(), with_cover=False)
    tricky_reader = small_reader()
    tricky_reader.file_name = "de\x01mo.mp4"
    with pytest.raises(BilibiliArchivePublishRejected):
        await service.upload_video(publish_job_id, tricky_reader)
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_non_auth_init_error_fails_without_refresh() -> None:
    service, _, gateway, provider = make_service()
    reader = small_reader()
    publish_job_id, _ = await prepared_job(service, reader=reader)
    gateway.script("upload_init", _error_payload(4010))
    with pytest.raises(BilibiliPublishStepFailed) as failure:
        await service.upload_video(publish_job_id, reader)
    assert failure.value.rejection.code == 4010
    assert provider.refresh_calls == 0


@pytest.mark.asyncio
async def test_gateway_unexpected_exception_surfaces_as_unavailable() -> None:
    service, _, gateway, _ = make_service()
    reader = small_reader()
    publish_job_id, _ = await prepared_job(service, reader=reader)
    gateway.script("upload_init", RuntimeError("boom"))
    with pytest.raises(BilibiliArchivePublishUnavailable):
        await service.upload_video(publish_job_id, reader)


@pytest.mark.asyncio
async def test_upload_video_rejects_settled_phase() -> None:
    service, store, gateway, _ = make_service()
    publish_job_id = await uploaded_job(service, gateway)
    gateway.script("archive_add", _fixture("response-archive-add-valid"))
    await service.create_archive(publish_job_id)
    with pytest.raises(BilibiliArchivePublishRejected):
        await service.upload_video(publish_job_id, small_reader())
    assert store.records[str(publish_job_id)].phase is BilibiliPublishPhase.SUBMITTED


@pytest.mark.asyncio
async def test_cover_upload_auth_refresh_retry_and_wrong_sources() -> None:
    provider = FakeTokenProvider(
        tokens=["fixture-access-token-expired", "fixture-access-token-fresh"]
    )
    service, store, gateway, _ = make_service(provider=provider)
    reader = small_reader()
    publish_job_id, _ = await prepared_job(service, reader=reader, with_cover=True)
    gateway.script("upload_cover", _error_payload(127001))
    gateway.script("upload_cover", _fixture("response-cover-upload-valid"))
    url = await service.upload_cover(
        publish_job_id, BytesCover(file_name="cover.png", content=b"png-bytes")
    )
    assert url.startswith("https://")
    assert provider.refresh_calls == 1
    assert store.records[str(publish_job_id)].cover_url == url

    with pytest.raises(BilibiliArchivePublishRejected):
        await service.upload_cover(publish_job_id, "not-a-cover")  # type: ignore[arg-type]


class WrongCover:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    async def describe(self) -> Any:
        if self.mode == "describe_raises":
            raise RuntimeError("cover down")
        if self.mode == "describe_wrong_type":
            return "not-a-stat"
        return BilibiliCoverStat(file_name="cover.png", size_bytes=9)

    async def read(self) -> Any:
        if self.mode == "read_raises":
            raise RuntimeError("cover down")
        if self.mode == "short_read":
            return b"x"
        return b"png-bytes"


@pytest.mark.asyncio
async def test_cover_source_failures_surface_as_unavailable() -> None:
    for mode in ("describe_raises", "describe_wrong_type", "read_raises", "short_read"):
        service, _, _, _ = make_service()
        reader = small_reader()
        publish_job_id, _ = await prepared_job(service, reader=reader, with_cover=True)
        with pytest.raises(BilibiliArchivePublishUnavailable):
            await service.upload_cover(publish_job_id, WrongCover(mode))


@pytest.mark.asyncio
async def test_cover_second_auth_rejection_fails_after_refresh() -> None:
    provider = FakeTokenProvider(
        tokens=["fixture-access-token-expired", "fixture-access-token-still-bad"]
    )
    service, _, gateway, _ = make_service(provider=provider)
    reader = small_reader()
    publish_job_id, _ = await prepared_job(service, reader=reader, with_cover=True)
    gateway.script("upload_cover", _error_payload(127001))
    gateway.script("upload_cover", _error_payload(127001))
    with pytest.raises(BilibiliPublishStepFailed):
        await service.upload_cover(
            publish_job_id, BytesCover(file_name="cover.png", content=b"png-bytes")
        )
    assert provider.refresh_calls == 1


@pytest.mark.asyncio
async def test_create_archive_revalidates_stored_fields_against_the_contract() -> None:
    service, store, gateway, _ = make_service()
    publish_job_id = await uploaded_job(service, gateway)
    record = store.records[str(publish_job_id)]
    corrupted_fields = BilibiliArchiveFields(
        title=record.fields.title,
        tid=record.fields.tid,
        tag="a,,b",
        copyright=record.fields.copyright,
        description=record.fields.description,
        source=record.fields.source,
        no_reprint=record.fields.no_reprint,
    )
    store.records[str(publish_job_id)] = replace(record, fields=corrupted_fields)
    with pytest.raises(BilibiliArchivePublishRejected):
        await service.create_archive(publish_job_id)
    assert gateway.call_count("archive_add") == 0


@pytest.mark.asyncio
async def test_admission_loss_with_concurrent_success_replays_the_receipt() -> None:
    service, store, gateway, _ = make_service()
    publish_job_id = await uploaded_job(service, gateway)
    gateway.script("archive_add", _fixture("response-archive-add-valid"))
    receipt = await service.create_archive(publish_job_id)

    class LosingStore(InMemoryStore):
        def __init__(self, inner: InMemoryStore) -> None:
            self.records = inner.records
            self.parts = inner.parts
            self.saw_submitted = False

        async def load(self, job: PublishJobId) -> Any:
            record = await super().load(job)
            if record is not None and not self.saw_submitted:
                self.saw_submitted = True
                return replace(
                    record,
                    phase=BilibiliPublishPhase.VIDEO_UPLOADED,
                    dispatched_at=None,
                    settled_at=None,
                    resource_id=None,
                )
            return record

        async def begin_archive_creation(self, job: PublishJobId, at: datetime) -> bool:
            return False

    racing = BilibiliArchivePublishService(
        contract=CONTRACT,
        store=LosingStore(store),
        gateway=gateway,
        token_provider=FakeTokenProvider(),
        clock=FixedClock(),
    )
    replay = await racing.create_archive(publish_job_id)
    assert replay.replayed is True
    assert replay.resource_id == receipt.resource_id
    assert gateway.call_count("archive_add") == 1


@pytest.mark.asyncio
async def test_unexpected_gateway_error_during_create_settles_uncertain() -> None:
    service, store, gateway, _ = make_service()
    publish_job_id = await uploaded_job(service, gateway)
    gateway.script("archive_add", RuntimeError("socket exploded"))
    with pytest.raises(BilibiliArchiveOutcomeUncertain):
        await service.create_archive(publish_job_id)
    assert store.records[str(publish_job_id)].phase is BilibiliPublishPhase.OUTCOME_UNCERTAIN


class RejectedStore(InMemoryStore):
    def __init__(self, inner: InMemoryStore, broken_method: str) -> None:
        self.records = inner.records
        self.parts = inner.parts
        self._broken_method = broken_method

    async def load(self, publish_job_id: PublishJobId) -> Any:
        if self._broken_method == "load":
            raise BilibiliArchivePublishRejected
        return await super().load(publish_job_id)

    async def record_upload_token(
        self, publish_job_id: PublishJobId, upload_token: str, at: datetime
    ) -> None:
        if self._broken_method == "record_upload_token":
            raise BilibiliArchivePublishRejected
        await super().record_upload_token(publish_job_id, upload_token, at)


@pytest.mark.asyncio
async def test_store_rejections_pass_through_unchanged() -> None:
    reader = small_reader()
    base_service, store, gateway, _ = make_service()
    publish_job_id, _ = await prepared_job(base_service, reader=reader)
    for broken_method in ("load", "record_upload_token"):
        service = BilibiliArchivePublishService(
            contract=CONTRACT,
            store=RejectedStore(store, broken_method),
            gateway=gateway,
            token_provider=FakeTokenProvider(),
            clock=FixedClock(),
        )
        gateway.script("upload_init", _fixture("response-upload-init-valid"))
        with pytest.raises(BilibiliArchivePublishRejected):
            await service.upload_video(publish_job_id, reader)


class RejectingReader:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    async def stat(self) -> Any:
        if self.mode == "stat":
            raise BilibiliArchivePublishRejected
        return BilibiliPublishMaterialStat(file_name="demo.mp4", size_bytes=16, duration_seconds=90)

    async def sha256(self) -> Any:
        raise BilibiliArchivePublishRejected

    async def read_range(self, offset: int, length: int) -> Any:
        raise BilibiliArchivePublishRejected


@pytest.mark.asyncio
async def test_reader_rejections_pass_through_unchanged() -> None:
    service, _, _, _ = make_service()
    for mode in ("stat", "sha"):
        with pytest.raises(BilibiliArchivePublishRejected):
            await service.validate_material(RejectingReader(mode))


@pytest.mark.asyncio
async def test_reprint_submission_carries_source_and_omits_description() -> None:
    service, _, gateway, _ = make_service()
    reader = small_reader()
    publish_job_id = PublishJobId.new()
    material = await service.validate_material(reader)
    reprint_fields = fields(copyright=2, source="https://example.com/origin", description=None)
    await service.prepare(
        publish_job_id, material=material, fields=reprint_fields, with_cover=False
    )
    script_small_upload(gateway)
    await service.upload_video(publish_job_id, reader)
    gateway.script("archive_add", _fixture("response-archive-add-valid"))
    await service.create_archive(publish_job_id)
    add_call = next(call for name, call in gateway.calls if name == "archive_add")
    submission = add_call["submission"]
    assert isinstance(submission, dict)
    assert submission["source"] == "https://example.com/origin"
    assert submission["copyright"] == 2
    assert "description" not in submission
