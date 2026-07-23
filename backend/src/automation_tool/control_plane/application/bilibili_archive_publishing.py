"""Bilibili upload orchestration and single-admission archive creation (PB-03).

The service prepares one durable publish intent per PublishJob, uploads the
material through the locked PB-02 open-api contract, and creates the archive at
most once: the persisted ``dispatched`` admission is granted a single time, a
lost or unparseable creation response settles ``outcome_uncertain`` and is never
resent automatically.  No access or refresh token is ever persisted here.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Never, Protocol, runtime_checkable

from automation_tool.control_plane.domain.bilibili_open_api import (
    BilibiliErrorCategory,
    BilibiliOpenApiContract,
    BilibiliPlatformRejection,
    parse_archive_add,
    parse_cover_upload,
    parse_transfer_ack,
    parse_upload_init,
    plan_upload_parts,
    validate_archive_submission,
    validate_upload_init_request,
)
from automation_tool.control_plane.domain.video_publishing import (
    PublishFailureCode,
    PublishJobId,
)

REQUEST_DIGEST_VERSION: Final = 1
ALLOWED_VIDEO_EXTENSIONS: Final = frozenset({"mp4", "flv"})
ALLOWED_COVER_EXTENSIONS: Final = frozenset({"jpeg", "jpg", "png"})

_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_FILE_NAME_PATTERN: Final = re.compile(r"^[^/\\]+\.[A-Za-z0-9]+$")
_RESOURCE_ID_PATTERN: Final = re.compile(r"^BV[0-9A-Za-z]{10}$")


class BilibiliArchivePublishRejected(ValueError):
    """Input, state, or persisted progress violates the publish contract."""

    def __init__(self) -> None:
        super().__init__("Bilibili archive publish request is rejected")


class BilibiliArchivePublishUnavailable(RuntimeError):
    """A dependency failed in a way that is not a platform response."""

    def __init__(self) -> None:
        super().__init__("Bilibili archive publishing is unavailable")


class BilibiliGatewayUnreachable(RuntimeError):
    """The platform gave no readable response; the outcome may be ambiguous."""

    def __init__(self) -> None:
        super().__init__("Bilibili open-api gateway is unreachable")


class BilibiliPublishStepFailed(RuntimeError):
    """The platform answered one publish step with a documented rejection."""

    def __init__(self, rejection: BilibiliPlatformRejection) -> None:
        super().__init__("Bilibili publish step failed")
        self.rejection = rejection


class BilibiliArchiveOutcomeUncertain(RuntimeError):
    """Archive creation was admitted but its platform outcome is unknown."""

    def __init__(self) -> None:
        super().__init__("Bilibili archive creation outcome is uncertain")


def _reject() -> Never:
    raise BilibiliArchivePublishRejected


class BilibiliUploadType(StrEnum):
    """Upload pre-processing type from the locked contract."""

    SMALL = "0"
    CHUNKED = "1"


class BilibiliPublishPhase(StrEnum):
    """Closed durable phases of one Bilibili publish attempt."""

    PREPARED = "prepared"
    VIDEO_UPLOADED = "video_uploaded"
    DISPATCHED = "dispatched"
    SUBMITTED = "submitted"
    FAILED = "failed"
    OUTCOME_UNCERTAIN = "outcome_uncertain"


_SETTLED_PHASES: Final = frozenset(
    {
        BilibiliPublishPhase.SUBMITTED,
        BilibiliPublishPhase.FAILED,
        BilibiliPublishPhase.OUTCOME_UNCERTAIN,
    }
)


def _validate_utc(value: object) -> None:
    if not isinstance(value, datetime) or value.utcoffset() != UTC.utcoffset(value):
        _reject()


@dataclass(frozen=True, slots=True)
class BilibiliPublishMaterialStat:
    """Untrusted local material facts reported by a material reader."""

    file_name: str
    size_bytes: int
    duration_seconds: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.file_name, str)
            or type(self.size_bytes) is not int
            or type(self.duration_seconds) is not int
        ):
            _reject()


@dataclass(frozen=True, slots=True)
class BilibiliPublishMaterial:
    """Validated material facts bound into the prepared publish intent."""

    file_name: str
    size_bytes: int
    duration_seconds: int
    sha256: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.file_name, str)
            or not 1 <= len(self.file_name) <= 255
            or _FILE_NAME_PATTERN.fullmatch(self.file_name) is None
            or self.file_name.startswith(".")
            or type(self.size_bytes) is not int
            or self.size_bytes < 1
            or type(self.duration_seconds) is not int
            or self.duration_seconds < 1
            or not isinstance(self.sha256, str)
            or _SHA256_PATTERN.fullmatch(self.sha256) is None
        ):
            _reject()

    @property
    def extension(self) -> str:
        return self.file_name.rsplit(".", 1)[1].lower()


@dataclass(frozen=True, slots=True)
class BilibiliArchiveFields:
    """Archive submission fields; contract boundaries are checked on use."""

    title: str
    tid: int
    tag: str
    copyright: int
    description: str | None
    source: str | None
    no_reprint: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.title, str)
            or type(self.tid) is not int
            or not isinstance(self.tag, str)
            or type(self.copyright) is not int
            or (self.description is not None and not isinstance(self.description, str))
            or (self.source is not None and not isinstance(self.source, str))
            or type(self.no_reprint) is not int
        ):
            _reject()


@dataclass(frozen=True, slots=True)
class BilibiliCoverStat:
    """Untrusted cover facts reported by a cover source."""

    file_name: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.file_name, str) or type(self.size_bytes) is not int:
            _reject()


@dataclass(frozen=True, slots=True)
class BilibiliPublishAttemptRecord:
    """Durable snapshot of one publish attempt; the store is its authority."""

    publish_job_id: PublishJobId
    phase: BilibiliPublishPhase
    request_digest: str
    material: BilibiliPublishMaterial
    fields: BilibiliArchiveFields
    upload_type: BilibiliUploadType
    part_size_bytes: int
    part_count: int
    has_cover: bool
    upload_token: str | None
    cover_url: str | None
    video_uploaded_at: datetime | None
    dispatched_at: datetime | None
    settled_at: datetime | None
    resource_id: str | None
    failure_code: PublishFailureCode | None
    platform_error_code: int | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.publish_job_id, PublishJobId)
            or not isinstance(self.phase, BilibiliPublishPhase)
            or not isinstance(self.request_digest, str)
            or _SHA256_PATTERN.fullmatch(self.request_digest) is None
            or not isinstance(self.material, BilibiliPublishMaterial)
            or not isinstance(self.fields, BilibiliArchiveFields)
            or not isinstance(self.upload_type, BilibiliUploadType)
            or type(self.part_size_bytes) is not int
            or type(self.part_count) is not int
            or type(self.has_cover) is not bool
            or (self.upload_token is not None and not isinstance(self.upload_token, str))
            or (self.cover_url is not None and not isinstance(self.cover_url, str))
            or (
                self.resource_id is not None
                and (
                    not isinstance(self.resource_id, str)
                    or _RESOURCE_ID_PATTERN.fullmatch(self.resource_id) is None
                )
            )
            or (
                self.failure_code is not None
                and not isinstance(self.failure_code, PublishFailureCode)
            )
            or (
                self.platform_error_code is not None
                and (type(self.platform_error_code) is not int or self.platform_error_code < 1)
            )
        ):
            _reject()
        if self.upload_type is BilibiliUploadType.SMALL:
            if self.part_size_bytes != 0 or self.part_count != 0:
                _reject()
        elif self.part_size_bytes < 1 or self.part_count < 1:
            _reject()
        if self.cover_url is not None and (
            not self.has_cover or not self.cover_url.startswith("https://")
        ):
            _reject()
        _validate_utc(self.created_at)
        _validate_utc(self.updated_at)
        if self.updated_at < self.created_at:
            _reject()
        for timestamp in (self.video_uploaded_at, self.dispatched_at, self.settled_at):
            if timestamp is not None:
                _validate_utc(timestamp)
        uploaded = self.video_uploaded_at is not None
        dispatched = self.dispatched_at is not None
        settled = self.settled_at is not None
        phase = self.phase
        if (
            (phase is BilibiliPublishPhase.PREPARED and (uploaded or dispatched or settled))
            or (
                phase is BilibiliPublishPhase.VIDEO_UPLOADED
                and (not uploaded or dispatched or settled)
            )
            or (
                phase is BilibiliPublishPhase.DISPATCHED
                and (not uploaded or not dispatched or settled)
            )
            or (phase in _SETTLED_PHASES and (not uploaded or not dispatched or not settled))
        ):
            _reject()
        if uploaded and self.upload_token is None:
            _reject()
        if (self.resource_id is not None) is not (phase is BilibiliPublishPhase.SUBMITTED):
            _reject()
        failed = phase is BilibiliPublishPhase.FAILED
        if (self.failure_code is not None) is not failed or (
            self.platform_error_code is not None
        ) is not failed:
            _reject()


@dataclass(frozen=True, slots=True)
class BilibiliPublishPreparation:
    """Result of the idempotent prepare step."""

    record: BilibiliPublishAttemptRecord
    replayed: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.record, BilibiliPublishAttemptRecord)
            or type(self.replayed) is not bool
        ):
            _reject()


@dataclass(frozen=True, slots=True)
class BilibiliArchiveCreationReceipt:
    """Durable receipt of the single archive creation side effect."""

    publish_job_id: PublishJobId
    resource_id: str
    request_digest: str
    replayed: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.publish_job_id, PublishJobId)
            or not isinstance(self.resource_id, str)
            or _RESOURCE_ID_PATTERN.fullmatch(self.resource_id) is None
            or not isinstance(self.request_digest, str)
            or _SHA256_PATTERN.fullmatch(self.request_digest) is None
            or type(self.replayed) is not bool
        ):
            _reject()


@runtime_checkable
class BilibiliPublishMaterialReader(Protocol):
    """Streaming access to one local video material."""

    async def stat(self) -> BilibiliPublishMaterialStat: ...

    async def sha256(self) -> str: ...

    async def read_range(self, offset: int, length: int) -> bytes: ...


@runtime_checkable
class BilibiliCoverSource(Protocol):
    """Access to one local cover image."""

    async def describe(self) -> BilibiliCoverStat: ...

    async def read(self) -> bytes: ...


@runtime_checkable
class BilibiliAccessTokenProvider(Protocol):
    """Short-term access-token custody; tokens are never persisted here."""

    async def current_access_token(self) -> str: ...

    async def refresh_access_token(self) -> str: ...


@runtime_checkable
class BilibiliOpenApiGateway(Protocol):
    """Raw transport to the open platform; returns unparsed response payloads."""

    async def upload_init(
        self, *, access_token: str, file_name: str, upload_type: str
    ) -> object: ...

    async def upload_part(
        self, *, upload_token: str, part_number: int, payload: bytes
    ) -> object: ...

    async def upload_complete(self, *, upload_token: str) -> object: ...

    async def upload_small_file(self, *, upload_token: str, payload: bytes) -> object: ...

    async def upload_cover(
        self, *, access_token: str, file_name: str, payload: bytes
    ) -> object: ...

    async def archive_add(
        self,
        *,
        access_token: str,
        upload_token: str,
        submission: dict[str, object],
    ) -> object: ...


@runtime_checkable
class BilibiliArchivePublishStore(Protocol):
    """Durable attempt state with a single archive-creation admission."""

    async def create_prepared(
        self, record: BilibiliPublishAttemptRecord
    ) -> BilibiliPublishPreparation: ...

    async def load(self, publish_job_id: PublishJobId) -> BilibiliPublishAttemptRecord | None: ...

    async def record_upload_token(
        self, publish_job_id: PublishJobId, upload_token: str, at: datetime
    ) -> None: ...

    async def record_part_completed(
        self, publish_job_id: PublishJobId, part_number: int, size_bytes: int, at: datetime
    ) -> bool: ...

    async def completed_part_numbers(self, publish_job_id: PublishJobId) -> frozenset[int]: ...

    async def record_video_uploaded(self, publish_job_id: PublishJobId, at: datetime) -> None: ...

    async def record_cover_url(
        self, publish_job_id: PublishJobId, cover_url: str, at: datetime
    ) -> None: ...

    async def begin_archive_creation(self, publish_job_id: PublishJobId, at: datetime) -> bool: ...

    async def record_submitted(
        self, publish_job_id: PublishJobId, resource_id: str, at: datetime
    ) -> None: ...

    async def record_failed(
        self,
        publish_job_id: PublishJobId,
        failure_code: PublishFailureCode,
        platform_error_code: int,
        at: datetime,
    ) -> None: ...

    async def record_outcome_uncertain(
        self, publish_job_id: PublishJobId, at: datetime
    ) -> None: ...


class BilibiliPublishClock(Protocol):
    def now(self) -> datetime: ...


class SystemBilibiliPublishClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class BilibiliArchivePublishService:
    """Idempotently prepare, upload, and create one archive at most once."""

    def __init__(
        self,
        *,
        contract: BilibiliOpenApiContract,
        store: BilibiliArchivePublishStore,
        gateway: BilibiliOpenApiGateway,
        token_provider: BilibiliAccessTokenProvider,
        clock: BilibiliPublishClock | None = None,
    ) -> None:
        if (
            not isinstance(contract, BilibiliOpenApiContract)
            or not isinstance(store, BilibiliArchivePublishStore)
            or not isinstance(gateway, BilibiliOpenApiGateway)
            or not isinstance(token_provider, BilibiliAccessTokenProvider)
        ):
            _reject()
        self._contract = contract
        self._store = store
        self._gateway = gateway
        self._tokens = token_provider
        self._clock = clock or SystemBilibiliPublishClock()

    def _now(self) -> datetime:
        try:
            value = self._clock.now()
        except Exception:
            raise BilibiliArchivePublishUnavailable from None
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise BilibiliArchivePublishUnavailable
        return value.astimezone(UTC)

    async def _current_token(self) -> str:
        try:
            token = await self._tokens.current_access_token()
        except Exception:
            raise BilibiliArchivePublishUnavailable from None
        if not isinstance(token, str) or not token:
            raise BilibiliArchivePublishUnavailable
        return token

    async def _refreshed_token(self) -> str:
        try:
            token = await self._tokens.refresh_access_token()
        except Exception:
            raise BilibiliArchivePublishUnavailable from None
        if not isinstance(token, str) or not token:
            raise BilibiliArchivePublishUnavailable
        return token

    async def _load(self, publish_job_id: PublishJobId) -> BilibiliPublishAttemptRecord:
        if not isinstance(publish_job_id, PublishJobId):
            _reject()
        try:
            record = await self._store.load(publish_job_id)
        except BilibiliArchivePublishRejected:
            raise
        except Exception:
            raise BilibiliArchivePublishUnavailable from None
        if record is None:
            _reject()
        if not isinstance(record, BilibiliPublishAttemptRecord) or (
            record.publish_job_id != publish_job_id
        ):
            raise BilibiliArchivePublishUnavailable
        return record

    def _validate_material_bounds(self, material: BilibiliPublishMaterial) -> None:
        if (
            material.extension not in ALLOWED_VIDEO_EXTENSIONS
            or material.size_bytes > self._contract.video_max_bytes
            or material.duration_seconds > self._contract.video_max_duration_seconds
        ):
            _reject()

    async def validate_material(
        self, reader: BilibiliPublishMaterialReader
    ) -> BilibiliPublishMaterial:
        if not isinstance(reader, BilibiliPublishMaterialReader):
            _reject()
        try:
            stat = await reader.stat()
        except BilibiliArchivePublishRejected:
            raise
        except Exception:
            raise BilibiliArchivePublishUnavailable from None
        if not isinstance(stat, BilibiliPublishMaterialStat):
            raise BilibiliArchivePublishUnavailable
        if stat.size_bytes < 1 or stat.duration_seconds < 1:
            _reject()
        try:
            digest = await reader.sha256()
        except BilibiliArchivePublishRejected:
            raise
        except Exception:
            raise BilibiliArchivePublishUnavailable from None
        material = BilibiliPublishMaterial(
            file_name=stat.file_name,
            size_bytes=stat.size_bytes,
            duration_seconds=stat.duration_seconds,
            sha256=digest,
        )
        self._validate_material_bounds(material)
        return material

    def _request_digest(
        self,
        publish_job_id: PublishJobId,
        material: BilibiliPublishMaterial,
        fields: BilibiliArchiveFields,
        with_cover: bool,
    ) -> str:
        canonical = json.dumps(
            {
                "digest_version": REQUEST_DIGEST_VERSION,
                "platform": "bilibili",
                "publish_job_id": str(publish_job_id),
                "material_file_name": material.file_name,
                "material_size_bytes": material.size_bytes,
                "material_duration_seconds": material.duration_seconds,
                "material_sha256": material.sha256,
                "title": fields.title,
                "tid": fields.tid,
                "tag": fields.tag,
                "copyright": fields.copyright,
                "description": fields.description,
                "source": fields.source,
                "no_reprint": fields.no_reprint,
                "with_cover": with_cover,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _validate_fields(self, fields: BilibiliArchiveFields) -> None:
        try:
            validate_archive_submission(
                self._contract,
                title=fields.title,
                tid=fields.tid,
                tag=fields.tag,
                copyright_=fields.copyright,
                description=fields.description,
                source=fields.source,
                no_reprint=fields.no_reprint,
                cover_url=None,
            )
        except ValueError:
            raise BilibiliArchivePublishRejected from None

    async def prepare(
        self,
        publish_job_id: PublishJobId,
        *,
        material: BilibiliPublishMaterial,
        fields: BilibiliArchiveFields,
        with_cover: bool,
    ) -> BilibiliPublishPreparation:
        if (
            not isinstance(publish_job_id, PublishJobId)
            or not isinstance(material, BilibiliPublishMaterial)
            or not isinstance(fields, BilibiliArchiveFields)
            or type(with_cover) is not bool
        ):
            _reject()
        self._validate_material_bounds(material)
        self._validate_fields(fields)
        if material.size_bytes <= self._contract.small_file_max_bytes:
            upload_type = BilibiliUploadType.SMALL
            part_size_bytes = 0
            part_count = 0
        else:
            upload_type = BilibiliUploadType.CHUNKED
            # Bounds were validated above, so the contract part plan cannot fail.
            part_sizes = plan_upload_parts(self._contract, material.size_bytes)
            part_size_bytes = self._contract.part_size_bytes
            part_count = len(part_sizes)
        now = self._now()
        record = BilibiliPublishAttemptRecord(
            publish_job_id=publish_job_id,
            phase=BilibiliPublishPhase.PREPARED,
            request_digest=self._request_digest(publish_job_id, material, fields, with_cover),
            material=material,
            fields=fields,
            upload_type=upload_type,
            part_size_bytes=part_size_bytes,
            part_count=part_count,
            has_cover=with_cover,
            upload_token=None,
            cover_url=None,
            video_uploaded_at=None,
            dispatched_at=None,
            settled_at=None,
            resource_id=None,
            failure_code=None,
            platform_error_code=None,
            created_at=now,
            updated_at=now,
        )
        try:
            preparation = await self._store.create_prepared(record)
        except BilibiliArchivePublishRejected:
            raise
        except Exception:
            raise BilibiliArchivePublishUnavailable from None
        if not isinstance(preparation, BilibiliPublishPreparation):
            raise BilibiliArchivePublishUnavailable
        return preparation

    def _step_result(
        self, parsed: object, *, allow_auth_retry: bool
    ) -> BilibiliPlatformRejection | None:
        """Return an auth rejection eligible for one refresh retry, or fail."""
        if isinstance(parsed, BilibiliPlatformRejection):
            if allow_auth_retry and parsed.category is BilibiliErrorCategory.AUTH_REJECTED:
                return parsed
            raise BilibiliPublishStepFailed(parsed)
        return None

    async def _initialize_upload(self, record: BilibiliPublishAttemptRecord) -> str:
        try:
            validate_upload_init_request(
                self._contract,
                file_name=record.material.file_name,
                upload_type=record.upload_type.value,
            )
        except ValueError:
            raise BilibiliArchivePublishRejected from None
        token = await self._current_token()
        payload = await self._gateway_call(
            self._gateway.upload_init(
                access_token=token,
                file_name=record.material.file_name,
                upload_type=record.upload_type.value,
            )
        )
        parsed = parse_upload_init(self._contract, payload)
        if self._step_result(parsed, allow_auth_retry=True) is not None:
            token = await self._refreshed_token()
            payload = await self._gateway_call(
                self._gateway.upload_init(
                    access_token=token,
                    file_name=record.material.file_name,
                    upload_type=record.upload_type.value,
                )
            )
            parsed = parse_upload_init(self._contract, payload)
            self._step_result(parsed, allow_auth_retry=False)
        if isinstance(parsed, BilibiliPlatformRejection):  # pragma: no cover - defensive
            raise BilibiliPublishStepFailed(parsed)
        upload_token = parsed.upload_token
        await self._store_call(
            self._store.record_upload_token(record.publish_job_id, upload_token, self._now())
        )
        return upload_token

    async def _gateway_call(self, awaitable: object) -> object:
        try:
            return await awaitable  # type: ignore[misc]
        except (BilibiliGatewayUnreachable, BilibiliPublishStepFailed):
            raise
        except Exception:
            raise BilibiliArchivePublishUnavailable from None

    async def _store_call(self, awaitable: object) -> object:
        try:
            return await awaitable  # type: ignore[misc]
        except BilibiliArchivePublishRejected:
            raise
        except Exception:
            raise BilibiliArchivePublishUnavailable from None

    async def _verify_reader_matches(
        self, record: BilibiliPublishAttemptRecord, reader: BilibiliPublishMaterialReader
    ) -> None:
        material = await self.validate_material(reader)
        if material != record.material:
            _reject()

    async def upload_video(
        self,
        publish_job_id: PublishJobId,
        reader: BilibiliPublishMaterialReader,
    ) -> BilibiliPublishAttemptRecord:
        record = await self._load(publish_job_id)
        if record.phase is BilibiliPublishPhase.VIDEO_UPLOADED:
            return record
        if record.phase is not BilibiliPublishPhase.PREPARED:
            _reject()
        await self._verify_reader_matches(record, reader)
        if record.upload_type is BilibiliUploadType.CHUNKED:
            completed = await self._store_call(self._store.completed_part_numbers(publish_job_id))
            if not isinstance(completed, frozenset) or not completed <= set(
                range(1, record.part_count + 1)
            ):
                _reject()
        else:
            completed = frozenset()
        upload_token = record.upload_token
        if upload_token is None:
            upload_token = await self._initialize_upload(record)
        if record.upload_type is BilibiliUploadType.SMALL:
            payload_bytes = await self._read_range(reader, 0, record.material.size_bytes)
            ack = await self._gateway_call(
                self._gateway.upload_small_file(upload_token=upload_token, payload=payload_bytes)
            )
            self._parse_ack(ack)
        else:
            offset = 0
            for index in range(record.part_count):
                part_number = index + 1
                is_last = part_number == record.part_count
                size = record.material.size_bytes - offset if is_last else record.part_size_bytes
                if part_number not in completed:
                    payload_bytes = await self._read_range(reader, offset, size)
                    ack = await self._gateway_call(
                        self._gateway.upload_part(
                            upload_token=upload_token,
                            part_number=part_number,
                            payload=payload_bytes,
                        )
                    )
                    self._parse_ack(ack)
                    await self._store_call(
                        self._store.record_part_completed(
                            publish_job_id, part_number, size, self._now()
                        )
                    )
                offset += size
            ack = await self._gateway_call(self._gateway.upload_complete(upload_token=upload_token))
            self._parse_ack(ack)
        await self._store_call(self._store.record_video_uploaded(publish_job_id, self._now()))
        return await self._load(publish_job_id)

    def _parse_ack(self, payload: object) -> None:
        parsed = parse_transfer_ack(self._contract, payload)
        if isinstance(parsed, BilibiliPlatformRejection):
            raise BilibiliPublishStepFailed(parsed)

    async def _read_range(
        self, reader: BilibiliPublishMaterialReader, offset: int, length: int
    ) -> bytes:
        try:
            payload = await reader.read_range(offset, length)
        except Exception:
            raise BilibiliArchivePublishUnavailable from None
        if not isinstance(payload, bytes) or len(payload) != length:
            raise BilibiliArchivePublishUnavailable
        return payload

    async def upload_cover(self, publish_job_id: PublishJobId, cover: BilibiliCoverSource) -> str:
        record = await self._load(publish_job_id)
        if not isinstance(cover, BilibiliCoverSource):
            _reject()
        if not record.has_cover or record.phase not in {
            BilibiliPublishPhase.PREPARED,
            BilibiliPublishPhase.VIDEO_UPLOADED,
        }:
            _reject()
        if record.cover_url is not None:
            return record.cover_url
        try:
            stat = await cover.describe()
        except Exception:
            raise BilibiliArchivePublishUnavailable from None
        if not isinstance(stat, BilibiliCoverStat):
            raise BilibiliArchivePublishUnavailable
        if (
            _FILE_NAME_PATTERN.fullmatch(stat.file_name) is None
            or stat.file_name.rsplit(".", 1)[1].lower() not in ALLOWED_COVER_EXTENSIONS
            or stat.size_bytes < 1
            or stat.size_bytes > self._contract.cover_max_bytes
        ):
            _reject()
        try:
            payload_bytes = await cover.read()
        except Exception:
            raise BilibiliArchivePublishUnavailable from None
        if not isinstance(payload_bytes, bytes) or len(payload_bytes) != stat.size_bytes:
            raise BilibiliArchivePublishUnavailable
        token = await self._current_token()
        payload = await self._gateway_call(
            self._gateway.upload_cover(
                access_token=token, file_name=stat.file_name, payload=payload_bytes
            )
        )
        parsed = parse_cover_upload(self._contract, payload)
        if self._step_result(parsed, allow_auth_retry=True) is not None:
            token = await self._refreshed_token()
            payload = await self._gateway_call(
                self._gateway.upload_cover(
                    access_token=token, file_name=stat.file_name, payload=payload_bytes
                )
            )
            parsed = parse_cover_upload(self._contract, payload)
            self._step_result(parsed, allow_auth_retry=False)
        if isinstance(parsed, BilibiliPlatformRejection):  # pragma: no cover - defensive
            raise BilibiliPublishStepFailed(parsed)
        await self._store_call(
            self._store.record_cover_url(publish_job_id, parsed.url, self._now())
        )
        return parsed.url

    def _submission_payload(self, record: BilibiliPublishAttemptRecord) -> dict[str, object]:
        fields = record.fields
        try:
            validate_archive_submission(
                self._contract,
                title=fields.title,
                tid=fields.tid,
                tag=fields.tag,
                copyright_=fields.copyright,
                description=fields.description,
                source=fields.source,
                no_reprint=fields.no_reprint,
                cover_url=record.cover_url,
            )
        except ValueError:
            raise BilibiliArchivePublishRejected from None
        submission: dict[str, object] = {
            "title": fields.title,
            "tid": fields.tid,
            "tag": fields.tag,
            "copyright": fields.copyright,
            "no_reprint": fields.no_reprint,
        }
        if fields.description is not None:
            submission["description"] = fields.description
        if fields.source is not None:
            submission["source"] = fields.source
        if record.cover_url is not None:
            submission["cover_url"] = record.cover_url
        return submission

    async def create_archive(self, publish_job_id: PublishJobId) -> BilibiliArchiveCreationReceipt:
        record = await self._load(publish_job_id)
        if record.phase is BilibiliPublishPhase.SUBMITTED:
            resource_id = record.resource_id
            if resource_id is None:  # pragma: no cover - defensive
                raise BilibiliArchivePublishUnavailable
            return BilibiliArchiveCreationReceipt(
                publish_job_id=publish_job_id,
                resource_id=resource_id,
                request_digest=record.request_digest,
                replayed=True,
            )
        if record.phase is BilibiliPublishPhase.DISPATCHED:
            raise BilibiliArchiveOutcomeUncertain
        if record.phase is not BilibiliPublishPhase.VIDEO_UPLOADED:
            _reject()
        if record.has_cover and record.cover_url is None:
            _reject()
        upload_token = record.upload_token
        if upload_token is None:  # pragma: no cover - defensive
            _reject()
        submission = self._submission_payload(record)
        token = await self._current_token()
        admitted = await self._store_call(
            self._store.begin_archive_creation(publish_job_id, self._now())
        )
        if admitted is not True:
            refreshed = await self._load(publish_job_id)
            if (
                refreshed.phase is BilibiliPublishPhase.SUBMITTED
                and refreshed.resource_id is not None
            ):
                return BilibiliArchiveCreationReceipt(
                    publish_job_id=publish_job_id,
                    resource_id=refreshed.resource_id,
                    request_digest=refreshed.request_digest,
                    replayed=True,
                )
            _reject()
        try:
            payload = await self._gateway.archive_add(
                access_token=token,
                upload_token=upload_token,
                submission=submission,
            )
        except BilibiliGatewayUnreachable:
            await self._store_call(
                self._store.record_outcome_uncertain(publish_job_id, self._now())
            )
            raise BilibiliArchiveOutcomeUncertain from None
        except Exception:
            await self._store_call(
                self._store.record_outcome_uncertain(publish_job_id, self._now())
            )
            raise BilibiliArchiveOutcomeUncertain from None
        try:
            parsed = parse_archive_add(self._contract, payload)
        except ValueError:
            await self._store_call(
                self._store.record_outcome_uncertain(publish_job_id, self._now())
            )
            raise BilibiliArchiveOutcomeUncertain from None
        if isinstance(parsed, BilibiliPlatformRejection):
            await self._store_call(
                self._store.record_failed(
                    publish_job_id, parsed.failure_code, parsed.code, self._now()
                )
            )
            raise BilibiliPublishStepFailed(parsed)
        await self._store_call(
            self._store.record_submitted(publish_job_id, parsed.resource_id, self._now())
        )
        return BilibiliArchiveCreationReceipt(
            publish_job_id=publish_job_id,
            resource_id=parsed.resource_id,
            request_digest=record.request_digest,
            replayed=False,
        )


__all__ = [
    "ALLOWED_COVER_EXTENSIONS",
    "ALLOWED_VIDEO_EXTENSIONS",
    "REQUEST_DIGEST_VERSION",
    "BilibiliAccessTokenProvider",
    "BilibiliArchiveCreationReceipt",
    "BilibiliArchiveFields",
    "BilibiliArchiveOutcomeUncertain",
    "BilibiliArchivePublishRejected",
    "BilibiliArchivePublishService",
    "BilibiliArchivePublishStore",
    "BilibiliArchivePublishUnavailable",
    "BilibiliCoverSource",
    "BilibiliCoverStat",
    "BilibiliGatewayUnreachable",
    "BilibiliOpenApiGateway",
    "BilibiliPublishAttemptRecord",
    "BilibiliPublishClock",
    "BilibiliPublishMaterial",
    "BilibiliPublishMaterialReader",
    "BilibiliPublishMaterialStat",
    "BilibiliPublishPhase",
    "BilibiliPublishPreparation",
    "BilibiliPublishStepFailed",
    "BilibiliUploadType",
    "SystemBilibiliPublishClock",
]
