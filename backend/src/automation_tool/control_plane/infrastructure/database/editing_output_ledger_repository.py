"""Durable PostgreSQL implementation of the editing output ledger (VE-07).

The ledger is write-once: one confirmed editing success maps to exactly one
lineage row plus its ordered artifact rows. Identical replays are no-ops, a
different lineage for the same job is rejected as a conflict, and loading
revalidates every row through the domain constructors so a corrupted row can
never smuggle an invalid record back into the application. Database failures
map to the closed `dependency_unavailable` error and never leak driver or
credential details.
"""

from __future__ import annotations

from collections.abc import Coroutine
from datetime import datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from automation_tool.control_plane.domain.resource_ids import ArtifactId
from automation_tool.control_plane.domain.video_creation import TimelineId
from automation_tool.control_plane.domain.video_editing import EditingJobId, EditingProjectId
from automation_tool.control_plane.domain.video_editing_outputs import (
    EditingOutputArtifactRecord,
    EditingOutputCost,
    EditingOutputCostSource,
    EditingOutputKind,
    EditingOutputLedgerConflict,
    EditingOutputLineage,
    InvalidEditingOutputModel,
)
from automation_tool.control_plane.domain.video_editing_provider import (
    EditingProviderErrorCode,
    EditingProviderFailure,
    EditingProviderId,
)
from automation_tool.control_plane.infrastructure.database.schema import (
    editing_output_artifacts,
    editing_output_lineages,
)
from automation_tool.control_plane.infrastructure.database.session import Database


def _record_from_row(row: RowMapping) -> EditingOutputArtifactRecord:
    return EditingOutputArtifactRecord(
        artifact_id=ArtifactId.parse(row["artifact_id"]),
        kind=EditingOutputKind(cast(str, row["kind"])),
        media_type=cast(str, row["media_type"]),
        byte_size=int(cast(int, row["byte_size"])),
        sha256_hex=cast(str, row["sha256_hex"]),
        created_at=cast(datetime, row["created_at"]),
    )


def _lineage_from_rows(
    lineage_row: RowMapping, artifact_rows: tuple[RowMapping, ...]
) -> EditingOutputLineage:
    return EditingOutputLineage(
        editing_job_id=EditingJobId.parse(lineage_row["editing_job_id"]),
        project_id=EditingProjectId.parse(lineage_row["project_id"]),
        timeline_id=TimelineId(cast(UUID, lineage_row["timeline_id"])),
        timeline_revision=int(cast(int, lineage_row["timeline_revision"])),
        provider_id=EditingProviderId(cast(str, lineage_row["provider_id"])),
        provider_contract_verified_at=cast(
            str, lineage_row["provider_contract_verified_at"]
        ),
        input_artifact_ids=tuple(
            ArtifactId.parse(value)
            for value in cast(list[UUID], lineage_row["input_artifact_ids"])
        ),
        outputs=tuple(_record_from_row(row) for row in artifact_rows),
        cost=EditingOutputCost(
            source=EditingOutputCostSource(cast(str, lineage_row["cost_source"])),
            currency=cast(str, lineage_row["cost_currency"]),
            billed_minutes=int(cast(int, lineage_row["cost_billed_minutes"])),
            tier_id=cast(str, lineage_row["cost_tier_id"]),
            unit_price_cny=cast(Decimal, lineage_row["cost_unit_price_cny"]),
            total_cny=cast(Decimal, lineage_row["cost_total_cny"]),
        ),
        created_at=cast(datetime, lineage_row["created_at"]),
    )


class SqlAlchemyEditingOutputLedger:
    """PostgreSQL write-once ledger satisfying `EditingOutputLedger`."""

    __slots__ = ("_database",)

    def __init__(self, database: Database) -> None:
        if not isinstance(database, Database):
            raise InvalidEditingOutputModel
        self._database = database

    async def save(self, lineage: EditingOutputLineage) -> None:
        if not isinstance(lineage, EditingOutputLineage):
            raise InvalidEditingOutputModel
        await self._run(self._save(lineage))

    async def load(self, editing_job_id: EditingJobId) -> EditingOutputLineage | None:
        if not isinstance(editing_job_id, EditingJobId):
            raise InvalidEditingOutputModel
        return await self._run(self._load(editing_job_id))

    async def _save(self, lineage: EditingOutputLineage) -> None:
        existing = await self._load(lineage.editing_job_id)
        if existing is not None:
            if existing != lineage:
                raise EditingOutputLedgerConflict
            return
        try:
            async with self._database.session() as session:
                await session.execute(
                    insert(editing_output_lineages).values(
                        editing_job_id=lineage.editing_job_id.uuid,
                        project_id=lineage.project_id.uuid,
                        timeline_id=lineage.timeline_id.uuid,
                        timeline_revision=lineage.timeline_revision,
                        provider_id=str(lineage.provider_id),
                        provider_contract_verified_at=lineage.provider_contract_verified_at,
                        input_artifact_ids=[
                            artifact_id.uuid for artifact_id in lineage.input_artifact_ids
                        ],
                        cost_source=lineage.cost.source.value,
                        cost_currency=lineage.cost.currency,
                        cost_billed_minutes=lineage.cost.billed_minutes,
                        cost_tier_id=lineage.cost.tier_id,
                        cost_unit_price_cny=lineage.cost.unit_price_cny,
                        cost_total_cny=lineage.cost.total_cny,
                        created_at=lineage.created_at,
                    )
                )
                await session.execute(
                    insert(editing_output_artifacts),
                    [
                        {
                            "artifact_id": record.artifact_id.uuid,
                            "editing_job_id": lineage.editing_job_id.uuid,
                            "position": position,
                            "kind": record.kind.value,
                            "media_type": record.media_type,
                            "byte_size": record.byte_size,
                            "sha256_hex": record.sha256_hex,
                            "created_at": record.created_at,
                        }
                        for position, record in enumerate(lineage.outputs)
                    ],
                )
        except IntegrityError:
            concurrent = await self._load(lineage.editing_job_id)
            if concurrent is None or concurrent != lineage:
                raise EditingOutputLedgerConflict from None
            return

    async def _load(self, editing_job_id: EditingJobId) -> EditingOutputLineage | None:
        lineage_statement = select(editing_output_lineages).where(
            editing_output_lineages.c.editing_job_id == editing_job_id.uuid
        )
        artifact_statement = (
            select(editing_output_artifacts)
            .where(editing_output_artifacts.c.editing_job_id == editing_job_id.uuid)
            .order_by(editing_output_artifacts.c.position)
        )
        async with self._database.session() as session:
            lineage_row = (await session.execute(lineage_statement)).mappings().first()
            if lineage_row is None:
                return None
            artifact_rows = tuple((await session.execute(artifact_statement)).mappings().all())
        return _lineage_from_rows(lineage_row, artifact_rows)

    async def _run[ResultT](self, operation: Coroutine[Any, Any, ResultT]) -> ResultT:
        """Run one database operation with the closed error mapping."""
        try:
            return await operation
        except (
            InvalidEditingOutputModel,
            EditingOutputLedgerConflict,
            EditingProviderFailure,
        ):
            raise
        except SQLAlchemyError:
            raise EditingProviderFailure(
                EditingProviderErrorCode.DEPENDENCY_UNAVAILABLE
            ) from None


__all__ = ["SqlAlchemyEditingOutputLedger"]
