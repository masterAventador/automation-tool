"""Durable PostgreSQL implementation of the Aliyun editing intent store.

VE-06 replaces the process-local reference store with this repository so
single-dispatch intents and reconciliation results survive App restarts.
Rows always satisfy the same closed invariants as `AliyunEditingIntent`;
loading revalidates through the domain constructor, so a corrupted row can
never smuggle an invalid lifecycle into the adapter. Database failures are
reported as the closed `dependency_unavailable` provider error and never leak
driver or credential details.
"""

from __future__ import annotations

from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError

from automation_tool.control_plane.domain.aliyun_ims_editing_provider import (
    AliyunEditingIntent,
    AliyunEditingIntentState,
    InvalidAliyunImsEditingProviderModel,
)
from automation_tool.control_plane.domain.resource_ids import ArtifactId
from automation_tool.control_plane.domain.video_editing import (
    EditingFailureCode,
    EditingJobId,
    EditingJobStatus,
)
from automation_tool.control_plane.domain.video_editing_provider import (
    EditingProviderErrorCode,
    EditingProviderFailure,
)
from automation_tool.control_plane.infrastructure.database.schema import (
    aliyun_editing_intents,
)
from automation_tool.control_plane.infrastructure.database.session import Database


def _intent_from_row(row: RowMapping) -> AliyunEditingIntent:
    failure_code = cast(str | None, row["failure_code"])
    return AliyunEditingIntent(
        editing_job_id=EditingJobId.parse(row["editing_job_id"]),
        request_hash=cast(str, row["request_hash"]),
        state=AliyunEditingIntentState(cast(str, row["state"])),
        vendor_job_id=cast(str | None, row["vendor_job_id"]),
        status=EditingJobStatus(cast(str, row["status"])),
        failure_code=None if failure_code is None else EditingFailureCode(failure_code),
        output_artifact_ids=tuple(
            ArtifactId.parse(value) for value in cast(list[UUID], row["output_artifact_ids"])
        ),
    )


class SqlAlchemyAliyunEditingIntentStore:
    """PostgreSQL intent store satisfying `AliyunEditingIntentStore`."""

    __slots__ = ("_database",)

    def __init__(self, database: Database) -> None:
        if not isinstance(database, Database):
            raise InvalidAliyunImsEditingProviderModel
        self._database = database

    async def load(self, editing_job_id: EditingJobId) -> AliyunEditingIntent | None:
        if not isinstance(editing_job_id, EditingJobId):
            raise InvalidAliyunImsEditingProviderModel
        return await self._run(self._load(editing_job_id))

    async def save(self, intent: AliyunEditingIntent) -> None:
        if not isinstance(intent, AliyunEditingIntent):
            raise InvalidAliyunImsEditingProviderModel
        await self._run(self._save(intent))

    async def load_all(self) -> tuple[AliyunEditingIntent, ...]:
        return await self._run(self._load_all())

    async def load_by_vendor_job_id(self, vendor_job_id: str) -> AliyunEditingIntent | None:
        if type(vendor_job_id) is not str or not vendor_job_id:
            raise InvalidAliyunImsEditingProviderModel
        return await self._run(self._load_by_vendor_job_id(vendor_job_id))

    async def _load(self, editing_job_id: EditingJobId) -> AliyunEditingIntent | None:
        statement = select(aliyun_editing_intents).where(
            aliyun_editing_intents.c.editing_job_id == editing_job_id.uuid
        )
        async with self._database.session() as session:
            row = (await session.execute(statement)).mappings().first()
        return None if row is None else _intent_from_row(row)

    async def _save(self, intent: AliyunEditingIntent) -> None:
        now = datetime.now(UTC)
        values: dict[str, object] = {
            "editing_job_id": intent.editing_job_id.uuid,
            "request_hash": intent.request_hash,
            "state": intent.state.value,
            "vendor_job_id": intent.vendor_job_id,
            "status": intent.status.value,
            "failure_code": None if intent.failure_code is None else intent.failure_code.value,
            "output_artifact_ids": [
                artifact_id.uuid for artifact_id in intent.output_artifact_ids
            ],
            "created_at": now,
            "updated_at": now,
        }
        statement = (
            postgresql_insert(aliyun_editing_intents)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[aliyun_editing_intents.c.editing_job_id],
                set_={
                    "request_hash": values["request_hash"],
                    "state": values["state"],
                    "vendor_job_id": values["vendor_job_id"],
                    "status": values["status"],
                    "failure_code": values["failure_code"],
                    "output_artifact_ids": values["output_artifact_ids"],
                    "updated_at": now,
                },
            )
        )
        async with self._database.session() as session:
            await session.execute(statement)

    async def _load_all(self) -> tuple[AliyunEditingIntent, ...]:
        statement = select(aliyun_editing_intents).order_by(
            aliyun_editing_intents.c.created_at, aliyun_editing_intents.c.editing_job_id
        )
        async with self._database.session() as session:
            rows = (await session.execute(statement)).mappings().all()
        return tuple(_intent_from_row(row) for row in rows)

    async def _load_by_vendor_job_id(self, vendor_job_id: str) -> AliyunEditingIntent | None:
        statement = select(aliyun_editing_intents).where(
            aliyun_editing_intents.c.vendor_job_id == vendor_job_id
        )
        async with self._database.session() as session:
            row = (await session.execute(statement)).mappings().first()
        return None if row is None else _intent_from_row(row)

    async def _run[ResultT](self, operation: Coroutine[Any, Any, ResultT]) -> ResultT:
        """Run one database operation with the closed error mapping."""
        try:
            return await operation
        except (InvalidAliyunImsEditingProviderModel, EditingProviderFailure):
            raise
        except SQLAlchemyError:
            raise EditingProviderFailure(
                EditingProviderErrorCode.DEPENDENCY_UNAVAILABLE
            ) from None


__all__ = ["SqlAlchemyAliyunEditingIntentStore"]
