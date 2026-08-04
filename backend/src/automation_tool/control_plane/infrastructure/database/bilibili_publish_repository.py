"""PostgreSQL attempt store for Bilibili publishing with single admission.

The ``dispatched`` transition is an atomic conditional update: exactly one
caller wins the archive-creation admission, every later caller observes the
durable phase.  Access and refresh tokens are never stored; the short-term
``upload_token`` is persisted only to resume interrupted part uploads.
"""

from __future__ import annotations

from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from automation_tool.control_plane.application.bilibili_archive_publishing import (
    BilibiliArchiveFields,
    BilibiliArchivePublishRejected,
    BilibiliArchivePublishUnavailable,
    BilibiliPublishAttemptRecord,
    BilibiliPublishMaterial,
    BilibiliPublishPhase,
    BilibiliPublishPreparation,
    BilibiliUploadType,
)
from automation_tool.control_plane.application.bilibili_archive_reconciliation import (
    BilibiliReconciliationOutcome,
    BilibiliReconciliationRecord,
)
from automation_tool.control_plane.domain.video_publishing import (
    PublishFailureCode,
    PublishJobId,
)
from automation_tool.control_plane.infrastructure.database.schema import (
    bilibili_publish_attempts,
    bilibili_publish_reconciliations,
    bilibili_upload_parts,
)
from automation_tool.control_plane.infrastructure.database.session import Database

_ACTIVE_COVER_PHASES = (
    BilibiliPublishPhase.PREPARED.value,
    BilibiliPublishPhase.VIDEO_UPLOADED.value,
)

_RECONCILABLE_PHASES = (
    BilibiliPublishPhase.DISPATCHED.value,
    BilibiliPublishPhase.SUBMITTED.value,
    BilibiliPublishPhase.OUTCOME_UNCERTAIN.value,
)


def _utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise BilibiliArchivePublishRejected
    return value.astimezone(UTC)


def _optional_utc(value: object) -> datetime | None:
    return None if value is None else _utc(value)


def _record(row: RowMapping) -> BilibiliPublishAttemptRecord:
    failure_code = cast(str | None, row["failure_code"])
    return BilibiliPublishAttemptRecord(
        publish_job_id=PublishJobId.parse(row["publish_job_id"]),
        phase=BilibiliPublishPhase(cast(str, row["phase"])),
        request_digest=cast(str, row["request_digest"]),
        material=BilibiliPublishMaterial(
            file_name=cast(str, row["material_file_name"]),
            size_bytes=cast(int, row["material_size_bytes"]),
            duration_seconds=cast(int, row["material_duration_seconds"]),
            sha256=cast(str, row["material_sha256"]),
        ),
        fields=BilibiliArchiveFields(
            title=cast(str, row["title"]),
            tid=cast(int, row["tid"]),
            tag=cast(str, row["tag"]),
            copyright=cast(int, row["copyright"]),
            description=cast(str | None, row["description"]),
            source=cast(str | None, row["source"]),
            no_reprint=cast(int, row["no_reprint"]),
        ),
        upload_type=BilibiliUploadType(cast(str, row["upload_type"])),
        part_size_bytes=cast(int, row["part_size_bytes"]),
        part_count=cast(int, row["part_count"]),
        has_cover=cast(bool, row["has_cover"]),
        upload_token=cast(str | None, row["upload_token"]),
        cover_url=cast(str | None, row["cover_url"]),
        video_uploaded_at=_optional_utc(row["video_uploaded_at"]),
        dispatched_at=_optional_utc(row["dispatched_at"]),
        settled_at=_optional_utc(row["settled_at"]),
        resource_id=cast(str | None, row["resource_id"]),
        failure_code=None if failure_code is None else PublishFailureCode(failure_code),
        platform_error_code=cast(int | None, row["platform_error_code"]),
        created_at=_utc(row["created_at"]),
        updated_at=_utc(row["updated_at"]),
    )


def _insert_values(record: BilibiliPublishAttemptRecord) -> dict[str, object]:
    return {
        "publish_job_id": record.publish_job_id.uuid,
        "phase": record.phase.value,
        "request_digest": record.request_digest,
        "material_file_name": record.material.file_name,
        "material_size_bytes": record.material.size_bytes,
        "material_duration_seconds": record.material.duration_seconds,
        "material_sha256": record.material.sha256,
        "title": record.fields.title,
        "tid": record.fields.tid,
        "tag": record.fields.tag,
        "copyright": record.fields.copyright,
        "description": record.fields.description,
        "source": record.fields.source,
        "no_reprint": record.fields.no_reprint,
        "upload_type": record.upload_type.value,
        "part_size_bytes": record.part_size_bytes,
        "part_count": record.part_count,
        "has_cover": record.has_cover,
        "upload_token": record.upload_token,
        "cover_url": record.cover_url,
        "video_uploaded_at": record.video_uploaded_at,
        "dispatched_at": record.dispatched_at,
        "settled_at": record.settled_at,
        "resource_id": record.resource_id,
        "failure_code": None if record.failure_code is None else record.failure_code.value,
        "platform_error_code": record.platform_error_code,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _require_job_id(publish_job_id: object) -> PublishJobId:
    if not isinstance(publish_job_id, PublishJobId):
        raise BilibiliArchivePublishRejected
    return publish_job_id


class SqlAlchemyBilibiliArchivePublishStore:
    """Durable, phase-guarded persistence for one publish attempt per job."""

    def __init__(self, database: Database) -> None:
        if not isinstance(database, Database):
            raise BilibiliArchivePublishRejected
        self._database = database

    async def _execute[ResultT](self, operation: Coroutine[Any, Any, ResultT]) -> ResultT:
        """Run one database operation with the closed error mapping."""
        try:
            return await operation
        except BilibiliArchivePublishRejected:
            raise
        except IntegrityError:
            raise BilibiliArchivePublishRejected from None
        except (OSError, SQLAlchemyError):
            raise BilibiliArchivePublishUnavailable from None
        except Exception:
            raise BilibiliArchivePublishUnavailable from None

    async def create_prepared(
        self, record: BilibiliPublishAttemptRecord
    ) -> BilibiliPublishPreparation:
        if (
            not isinstance(record, BilibiliPublishAttemptRecord)
            or record.phase is not BilibiliPublishPhase.PREPARED
        ):
            raise BilibiliArchivePublishRejected

        async def operation() -> BilibiliPublishPreparation:
            async with self._database.session() as session:
                existing = (
                    (
                        await session.execute(
                            select(bilibili_publish_attempts)
                            .where(
                                bilibili_publish_attempts.c.publish_job_id
                                == record.publish_job_id.uuid
                            )
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing is not None:
                    stored = _record(existing)
                    if stored.request_digest != record.request_digest:
                        raise BilibiliArchivePublishRejected
                    return BilibiliPublishPreparation(record=stored, replayed=True)
                await session.execute(
                    insert(bilibili_publish_attempts).values(**_insert_values(record))
                )
                return BilibiliPublishPreparation(record=record, replayed=False)

        return await self._execute(operation())

    async def load(self, publish_job_id: PublishJobId) -> BilibiliPublishAttemptRecord | None:
        job_id = _require_job_id(publish_job_id)

        async def operation() -> BilibiliPublishAttemptRecord | None:
            async with self._database.session() as session:
                row = (
                    (
                        await session.execute(
                            select(bilibili_publish_attempts).where(
                                bilibili_publish_attempts.c.publish_job_id == job_id.uuid
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                return None if row is None else _record(row)

        return await self._execute(operation())

    async def _guarded_update(
        self,
        publish_job_id: PublishJobId,
        *,
        values: dict[str, object],
        phases: tuple[str, ...],
        require_upload_token: bool = False,
        require_has_cover: bool = False,
    ) -> bool:
        statement = (
            update(bilibili_publish_attempts)
            .where(
                bilibili_publish_attempts.c.publish_job_id == publish_job_id.uuid,
                bilibili_publish_attempts.c.phase.in_(phases),
            )
            .values(**values)
        )
        if require_upload_token:
            statement = statement.where(bilibili_publish_attempts.c.upload_token.is_not(None))
        if require_has_cover:
            statement = statement.where(bilibili_publish_attempts.c.has_cover.is_(True))
        returning = statement.returning(bilibili_publish_attempts.c.publish_job_id)

        async def operation() -> bool:
            async with self._database.session() as session:
                result = await session.execute(returning)
                return result.scalar_one_or_none() is not None

        return await self._execute(operation())

    async def record_upload_token(
        self, publish_job_id: PublishJobId, upload_token: str, at: datetime
    ) -> None:
        job_id = _require_job_id(publish_job_id)
        if not isinstance(upload_token, str) or not upload_token or len(upload_token) > 512:
            raise BilibiliArchivePublishRejected
        updated = await self._guarded_update(
            job_id,
            values={"upload_token": upload_token, "updated_at": _utc(at)},
            phases=(BilibiliPublishPhase.PREPARED.value,),
        )
        if not updated:
            raise BilibiliArchivePublishRejected

    async def record_part_completed(
        self, publish_job_id: PublishJobId, part_number: int, size_bytes: int, at: datetime
    ) -> bool:
        job_id = _require_job_id(publish_job_id)
        if type(part_number) is not int or type(size_bytes) is not int or size_bytes < 1:
            raise BilibiliArchivePublishRejected
        completed_at = _utc(at)

        async def operation() -> bool:
            async with self._database.session() as session:
                attempt = (
                    (
                        await session.execute(
                            select(
                                bilibili_publish_attempts.c.phase,
                                bilibili_publish_attempts.c.upload_type,
                                bilibili_publish_attempts.c.part_count,
                            )
                            .where(bilibili_publish_attempts.c.publish_job_id == job_id.uuid)
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if (
                    attempt is None
                    or attempt["phase"] != BilibiliPublishPhase.PREPARED.value
                    or attempt["upload_type"] != BilibiliUploadType.CHUNKED.value
                    or not 1 <= part_number <= cast(int, attempt["part_count"])
                ):
                    raise BilibiliArchivePublishRejected
                result = await session.execute(
                    postgresql_insert(bilibili_upload_parts)
                    .values(
                        publish_job_id=job_id.uuid,
                        part_number=part_number,
                        size_bytes=size_bytes,
                        completed_at=completed_at,
                    )
                    .on_conflict_do_nothing(index_elements=["publish_job_id", "part_number"])
                    .returning(bilibili_upload_parts.c.part_number)
                )
                return result.scalar_one_or_none() is not None

        return await self._execute(operation())

    async def completed_part_numbers(self, publish_job_id: PublishJobId) -> frozenset[int]:
        job_id = _require_job_id(publish_job_id)

        async def operation() -> frozenset[int]:
            async with self._database.session() as session:
                exists = await session.scalar(
                    select(bilibili_publish_attempts.c.publish_job_id).where(
                        bilibili_publish_attempts.c.publish_job_id == job_id.uuid
                    )
                )
                if exists is None:
                    raise BilibiliArchivePublishRejected
                rows = await session.execute(
                    select(bilibili_upload_parts.c.part_number).where(
                        bilibili_upload_parts.c.publish_job_id == job_id.uuid
                    )
                )
                return frozenset(cast(int, value) for value in rows.scalars())

        return await self._execute(operation())

    async def record_video_uploaded(self, publish_job_id: PublishJobId, at: datetime) -> None:
        job_id = _require_job_id(publish_job_id)
        moment = _utc(at)
        updated = await self._guarded_update(
            job_id,
            values={
                "phase": BilibiliPublishPhase.VIDEO_UPLOADED.value,
                "video_uploaded_at": moment,
                "updated_at": moment,
            },
            phases=(BilibiliPublishPhase.PREPARED.value,),
            require_upload_token=True,
        )
        if not updated:
            raise BilibiliArchivePublishRejected

    async def record_cover_url(
        self, publish_job_id: PublishJobId, cover_url: str, at: datetime
    ) -> None:
        job_id = _require_job_id(publish_job_id)
        if (
            not isinstance(cover_url, str)
            or not cover_url.startswith("https://")
            or len(cover_url) > 1024
        ):
            raise BilibiliArchivePublishRejected
        updated = await self._guarded_update(
            job_id,
            values={"cover_url": cover_url, "updated_at": _utc(at)},
            phases=_ACTIVE_COVER_PHASES,
            require_has_cover=True,
        )
        if not updated:
            raise BilibiliArchivePublishRejected

    async def begin_archive_creation(self, publish_job_id: PublishJobId, at: datetime) -> bool:
        job_id = _require_job_id(publish_job_id)
        moment = _utc(at)
        admitted = await self._guarded_update(
            job_id,
            values={
                "phase": BilibiliPublishPhase.DISPATCHED.value,
                "dispatched_at": moment,
                "updated_at": moment,
            },
            phases=(BilibiliPublishPhase.VIDEO_UPLOADED.value,),
        )
        if admitted:
            return True
        if await self.load(job_id) is None:
            raise BilibiliArchivePublishRejected
        return False

    async def record_submitted(
        self, publish_job_id: PublishJobId, resource_id: str, at: datetime
    ) -> None:
        job_id = _require_job_id(publish_job_id)
        if not isinstance(resource_id, str) or not resource_id or len(resource_id) > 16:
            raise BilibiliArchivePublishRejected
        moment = _utc(at)
        updated = await self._guarded_update(
            job_id,
            values={
                "phase": BilibiliPublishPhase.SUBMITTED.value,
                "settled_at": moment,
                "resource_id": resource_id,
                "updated_at": moment,
            },
            phases=(BilibiliPublishPhase.DISPATCHED.value,),
        )
        if not updated:
            raise BilibiliArchivePublishRejected

    async def record_failed(
        self,
        publish_job_id: PublishJobId,
        failure_code: PublishFailureCode,
        platform_error_code: int,
        at: datetime,
    ) -> None:
        job_id = _require_job_id(publish_job_id)
        if (
            not isinstance(failure_code, PublishFailureCode)
            or type(platform_error_code) is not int
            or platform_error_code < 1
        ):
            raise BilibiliArchivePublishRejected
        moment = _utc(at)
        updated = await self._guarded_update(
            job_id,
            values={
                "phase": BilibiliPublishPhase.FAILED.value,
                "settled_at": moment,
                "failure_code": failure_code.value,
                "platform_error_code": platform_error_code,
                "updated_at": moment,
            },
            phases=(BilibiliPublishPhase.DISPATCHED.value,),
        )
        if not updated:
            raise BilibiliArchivePublishRejected

    async def record_outcome_uncertain(self, publish_job_id: PublishJobId, at: datetime) -> None:
        job_id = _require_job_id(publish_job_id)
        moment = _utc(at)
        updated = await self._guarded_update(
            job_id,
            values={
                "phase": BilibiliPublishPhase.OUTCOME_UNCERTAIN.value,
                "settled_at": moment,
                "updated_at": moment,
            },
            phases=(BilibiliPublishPhase.DISPATCHED.value,),
        )
        if not updated:
            raise BilibiliArchivePublishRejected

    async def list_reconcilable(self) -> tuple[BilibiliPublishAttemptRecord, ...]:
        """Return every attempt PB-04 reconciliation must consume, oldest first."""

        async def operation() -> tuple[BilibiliPublishAttemptRecord, ...]:
            async with self._database.session() as session:
                rows = await session.execute(
                    select(bilibili_publish_attempts)
                    .where(bilibili_publish_attempts.c.phase.in_(_RECONCILABLE_PHASES))
                    .order_by(
                        bilibili_publish_attempts.c.created_at,
                        bilibili_publish_attempts.c.publish_job_id,
                    )
                )
                return tuple(_record(row) for row in rows.mappings())

        return await self._execute(operation())


def _reconciliation_record(row: RowMapping) -> BilibiliReconciliationRecord:
    failure_code = cast(str | None, row["failure_code"])
    return BilibiliReconciliationRecord(
        publish_job_id=PublishJobId.parse(row["publish_job_id"]),
        outcome=BilibiliReconciliationOutcome(cast(str, row["outcome"])),
        resource_id=cast(str | None, row["resource_id"]),
        archive_state=cast(int | None, row["archive_state"]),
        failure_code=None if failure_code is None else PublishFailureCode(failure_code),
        last_checked_at=_optional_utc(row["last_checked_at"]),
        settled_at=_optional_utc(row["settled_at"]),
        created_at=_utc(row["created_at"]),
        updated_at=_utc(row["updated_at"]),
    )


class SqlAlchemyBilibiliReconciliationStore:
    """Durable, monotonic reconciliation outcomes; settled rows never change."""

    def __init__(self, database: Database) -> None:
        if not isinstance(database, Database):
            raise BilibiliArchivePublishRejected
        self._database = database

    async def _execute[ResultT](self, operation: Coroutine[Any, Any, ResultT]) -> ResultT:
        try:
            return await operation
        except BilibiliArchivePublishRejected:
            raise
        except IntegrityError:
            raise BilibiliArchivePublishRejected from None
        except (OSError, SQLAlchemyError):
            raise BilibiliArchivePublishUnavailable from None
        except Exception:
            raise BilibiliArchivePublishUnavailable from None

    async def ensure_pending(
        self, publish_job_id: PublishJobId, resource_id: str | None, at: datetime
    ) -> BilibiliReconciliationRecord:
        job_id = _require_job_id(publish_job_id)
        if resource_id is not None and (
            not isinstance(resource_id, str) or not resource_id or len(resource_id) > 16
        ):
            raise BilibiliArchivePublishRejected
        moment = _utc(at)

        async def operation() -> BilibiliReconciliationRecord:
            async with self._database.session() as session:
                await session.execute(
                    postgresql_insert(bilibili_publish_reconciliations)
                    .values(
                        publish_job_id=job_id.uuid,
                        outcome=BilibiliReconciliationOutcome.PENDING.value,
                        resource_id=resource_id,
                        archive_state=None,
                        failure_code=None,
                        last_checked_at=None,
                        settled_at=None,
                        created_at=moment,
                        updated_at=moment,
                    )
                    .on_conflict_do_nothing(index_elements=["publish_job_id"])
                )
                row = (
                    (
                        await session.execute(
                            select(bilibili_publish_reconciliations).where(
                                bilibili_publish_reconciliations.c.publish_job_id == job_id.uuid
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                return _reconciliation_record(row)

        return await self._execute(operation())

    async def load(self, publish_job_id: PublishJobId) -> BilibiliReconciliationRecord | None:
        job_id = _require_job_id(publish_job_id)

        async def operation() -> BilibiliReconciliationRecord | None:
            async with self._database.session() as session:
                row = (
                    (
                        await session.execute(
                            select(bilibili_publish_reconciliations).where(
                                bilibili_publish_reconciliations.c.publish_job_id == job_id.uuid
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                return None if row is None else _reconciliation_record(row)

        return await self._execute(operation())

    async def find_by_resource_id(self, resource_id: str) -> BilibiliReconciliationRecord | None:
        if not isinstance(resource_id, str) or not resource_id or len(resource_id) > 16:
            raise BilibiliArchivePublishRejected

        async def operation() -> BilibiliReconciliationRecord | None:
            async with self._database.session() as session:
                row = (
                    (
                        await session.execute(
                            select(bilibili_publish_reconciliations).where(
                                bilibili_publish_reconciliations.c.resource_id == resource_id
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                return None if row is None else _reconciliation_record(row)

        return await self._execute(operation())

    async def list_unsettled(self) -> tuple[BilibiliReconciliationRecord, ...]:
        async def operation() -> tuple[BilibiliReconciliationRecord, ...]:
            async with self._database.session() as session:
                rows = await session.execute(
                    select(bilibili_publish_reconciliations)
                    .where(
                        bilibili_publish_reconciliations.c.outcome
                        == BilibiliReconciliationOutcome.PENDING.value
                    )
                    .order_by(
                        bilibili_publish_reconciliations.c.created_at,
                        bilibili_publish_reconciliations.c.publish_job_id,
                    )
                )
                return tuple(_reconciliation_record(row) for row in rows.mappings())

        return await self._execute(operation())

    async def _pending_update(
        self, publish_job_id: PublishJobId, values: dict[str, object]
    ) -> bool:
        statement = (
            update(bilibili_publish_reconciliations)
            .where(
                bilibili_publish_reconciliations.c.publish_job_id == publish_job_id.uuid,
                bilibili_publish_reconciliations.c.outcome
                == BilibiliReconciliationOutcome.PENDING.value,
            )
            .values(**values)
            .returning(bilibili_publish_reconciliations.c.publish_job_id)
        )

        async def operation() -> bool:
            async with self._database.session() as session:
                result = await session.execute(statement)
                return result.scalar_one_or_none() is not None

        return await self._execute(operation())

    async def record_checked(
        self, publish_job_id: PublishJobId, archive_state: int | None, at: datetime
    ) -> None:
        job_id = _require_job_id(publish_job_id)
        if archive_state is not None and type(archive_state) is not int:
            raise BilibiliArchivePublishRejected
        moment = _utc(at)
        updated = await self._pending_update(
            job_id,
            {"archive_state": archive_state, "last_checked_at": moment, "updated_at": moment},
        )
        if not updated:
            raise BilibiliArchivePublishRejected

    async def adopt_resource_id(
        self, publish_job_id: PublishJobId, resource_id: str, at: datetime
    ) -> None:
        job_id = _require_job_id(publish_job_id)
        if not isinstance(resource_id, str) or not resource_id or len(resource_id) > 16:
            raise BilibiliArchivePublishRejected
        moment = _utc(at)
        statement = (
            update(bilibili_publish_reconciliations)
            .where(
                bilibili_publish_reconciliations.c.publish_job_id == job_id.uuid,
                bilibili_publish_reconciliations.c.outcome
                == BilibiliReconciliationOutcome.PENDING.value,
                bilibili_publish_reconciliations.c.resource_id.is_(None),
            )
            .values(resource_id=resource_id, updated_at=moment)
            .returning(bilibili_publish_reconciliations.c.publish_job_id)
        )

        async def operation() -> bool:
            async with self._database.session() as session:
                result = await session.execute(statement)
                return result.scalar_one_or_none() is not None

        if not await self._execute(operation()):
            raise BilibiliArchivePublishRejected

    async def settle(
        self,
        publish_job_id: PublishJobId,
        outcome: BilibiliReconciliationOutcome,
        archive_state: int | None,
        failure_code: PublishFailureCode | None,
        at: datetime,
    ) -> bool:
        job_id = _require_job_id(publish_job_id)
        if (
            not isinstance(outcome, BilibiliReconciliationOutcome)
            or outcome is BilibiliReconciliationOutcome.PENDING
            or (archive_state is not None and type(archive_state) is not int)
            or (failure_code is not None and not isinstance(failure_code, PublishFailureCode))
            or (failure_code is not None) is not (outcome is BilibiliReconciliationOutcome.FAILED)
        ):
            raise BilibiliArchivePublishRejected
        moment = _utc(at)
        settled = await self._pending_update(
            job_id,
            {
                "outcome": outcome.value,
                "archive_state": archive_state,
                "failure_code": None if failure_code is None else failure_code.value,
                "last_checked_at": moment,
                "settled_at": moment,
                "updated_at": moment,
            },
        )
        if settled:
            return True
        if await self.load(job_id) is None:
            raise BilibiliArchivePublishRejected
        return False


__all__ = [
    "SqlAlchemyBilibiliArchivePublishStore",
    "SqlAlchemyBilibiliReconciliationStore",
]
