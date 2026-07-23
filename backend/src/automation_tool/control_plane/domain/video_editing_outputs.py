"""Provider-neutral finished-output records, lineage, cost and ledger (VE-07).

A confirmed editing success is durably recorded as one `EditingOutputLineage`:
the registered output artifacts (exactly one video plus optional cover,
subtitle or metadata documents), the input artifacts and frozen timeline
revision they were produced from, the provider identity and contract
verification date, and the cost record. The cost source vocabulary is closed:
first-phase entries are `estimated` (the VE-04 billing contract applied to the
submitted output); `billed` is reserved for the pending real-invoice
verification and never fabricated.

Vendor DTOs never appear here; Aliyun specifics stay in their adapter.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum, unique
from types import MappingProxyType
from typing import Final, Never, Protocol, final

from automation_tool.control_plane.domain.resource_ids import ArtifactId
from automation_tool.control_plane.domain.video_creation import (
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACT_REFERENCES,
    TimelineId,
)
from automation_tool.control_plane.domain.video_editing import EditingJobId, EditingProjectId
from automation_tool.control_plane.domain.video_editing_provider import EditingProviderId

MAX_EDITING_OUTPUT_ARTIFACTS: Final = 8
MAX_BILLED_MINUTES: Final = 10_000

_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_TIER_ID_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_VERIFIED_AT_PATTERN: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_UNIT_PRICE_CNY: Final = Decimal("1000")


class InvalidEditingOutputModel(ValueError):
    """A provider-neutral editing output value is invalid."""

    def __init__(self) -> None:
        super().__init__("Editing output domain model is invalid")


class EditingOutputLedgerConflict(Exception):
    """A different lineage is already recorded for this editing job."""

    def __init__(self) -> None:
        super().__init__("Editing output lineage already recorded differently")


def _reject() -> Never:
    raise InvalidEditingOutputModel


@unique
class EditingOutputKind(StrEnum):
    """Closed vocabulary of registered finished-output artifact kinds."""

    VIDEO = "video"
    COVER = "cover"
    SUBTITLE = "subtitle"
    METADATA = "metadata"


@unique
class EditingOutputCostSource(StrEnum):
    """Closed provenance of one cost record; `billed` awaits real invoices."""

    ESTIMATED = "estimated"
    BILLED = "billed"


_KIND_MEDIA_TYPES: Final[Mapping[EditingOutputKind, frozenset[str]]] = MappingProxyType(
    {
        EditingOutputKind.VIDEO: frozenset({"video/mp4", "video/webm"}),
        EditingOutputKind.COVER: frozenset({"image/jpeg", "image/png"}),
        EditingOutputKind.SUBTITLE: frozenset({"text/vtt", "application/x-subrip"}),
        EditingOutputKind.METADATA: frozenset({"application/json"}),
    }
)


def _validate_timestamp(value: object) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        _reject()


@final
@dataclass(frozen=True, slots=True)
class EditingOutputArtifactRecord:
    """One registered finished-output artifact with verified content facts."""

    artifact_id: ArtifactId
    kind: EditingOutputKind
    media_type: str
    byte_size: int
    sha256_hex: str
    created_at: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.artifact_id, ArtifactId)
            or not isinstance(self.kind, EditingOutputKind)
            or type(self.media_type) is not str
            or self.media_type not in _KIND_MEDIA_TYPES[self.kind]
            or type(self.byte_size) is not int
            or not 1 <= self.byte_size <= MAX_ARTIFACT_BYTES
            or type(self.sha256_hex) is not str
            or _SHA256_PATTERN.fullmatch(self.sha256_hex) is None
        ):
            _reject()
        _validate_timestamp(self.created_at)


@final
@dataclass(frozen=True, slots=True)
class EditingOutputCost:
    """One cost record for a finished editing job; totals are exact."""

    source: EditingOutputCostSource
    currency: str
    billed_minutes: int
    tier_id: str
    unit_price_cny: Decimal
    total_cny: Decimal

    def __post_init__(self) -> None:
        if (
            not isinstance(self.source, EditingOutputCostSource)
            or self.currency != "CNY"
            or type(self.billed_minutes) is not int
            or not 1 <= self.billed_minutes <= MAX_BILLED_MINUTES
            or type(self.tier_id) is not str
            or _TIER_ID_PATTERN.fullmatch(self.tier_id) is None
            or not isinstance(self.unit_price_cny, Decimal)
            or not isinstance(self.total_cny, Decimal)
            or not Decimal(0) <= self.unit_price_cny <= _MAX_UNIT_PRICE_CNY
            or self.total_cny != self.unit_price_cny * self.billed_minutes
        ):
            _reject()


@final
@dataclass(frozen=True, slots=True)
class EditingOutputLineage:
    """The durable provenance of one confirmed editing success."""

    editing_job_id: EditingJobId
    project_id: EditingProjectId
    timeline_id: TimelineId
    timeline_revision: int
    provider_id: EditingProviderId
    provider_contract_verified_at: str
    input_artifact_ids: tuple[ArtifactId, ...] = field(repr=False)
    outputs: tuple[EditingOutputArtifactRecord, ...]
    cost: EditingOutputCost
    created_at: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.editing_job_id, EditingJobId)
            or not isinstance(self.project_id, EditingProjectId)
            or type(self.timeline_id) is not TimelineId
            or type(self.timeline_revision) is not int
            or self.timeline_revision < 1
            or type(self.provider_id) is not EditingProviderId
            or type(self.provider_contract_verified_at) is not str
            or _VERIFIED_AT_PATTERN.fullmatch(self.provider_contract_verified_at) is None
            or not isinstance(self.cost, EditingOutputCost)
        ):
            _reject()
        if (
            not isinstance(self.input_artifact_ids, tuple)
            or not 1 <= len(self.input_artifact_ids) <= MAX_ARTIFACT_REFERENCES
            or any(not isinstance(value, ArtifactId) for value in self.input_artifact_ids)
            or len(set(self.input_artifact_ids)) != len(self.input_artifact_ids)
        ):
            _reject()
        if (
            not isinstance(self.outputs, tuple)
            or not 1 <= len(self.outputs) <= MAX_EDITING_OUTPUT_ARTIFACTS
            or any(not isinstance(record, EditingOutputArtifactRecord) for record in self.outputs)
        ):
            _reject()
        output_ids = [record.artifact_id for record in self.outputs]
        video_count = sum(1 for record in self.outputs if record.kind is EditingOutputKind.VIDEO)
        if (
            len(set(output_ids)) != len(output_ids)
            or video_count != 1
            or set(output_ids) & set(self.input_artifact_ids)
        ):
            _reject()
        _validate_timestamp(self.created_at)


class EditingOutputLedger(Protocol):
    """Durable, write-once store of finished-output lineages."""

    async def save(self, lineage: EditingOutputLineage) -> None:
        """Record once; identical replays are no-ops, conflicts are rejected."""
        ...

    async def load(self, editing_job_id: EditingJobId) -> EditingOutputLineage | None:
        """Return the recorded lineage, or None when the job has none."""
        ...


@final
class InMemoryEditingOutputLedger:
    """Process-local reference implementation of the output ledger port."""

    __slots__ = ("_lineages",)

    def __init__(self) -> None:
        self._lineages: dict[EditingJobId, EditingOutputLineage] = {}

    async def save(self, lineage: EditingOutputLineage) -> None:
        if not isinstance(lineage, EditingOutputLineage):
            _reject()
        existing = self._lineages.get(lineage.editing_job_id)
        if existing is not None:
            if existing != lineage:
                raise EditingOutputLedgerConflict
            return
        self._lineages[lineage.editing_job_id] = lineage

    async def load(self, editing_job_id: EditingJobId) -> EditingOutputLineage | None:
        if not isinstance(editing_job_id, EditingJobId):
            _reject()
        return self._lineages.get(editing_job_id)


__all__ = [
    "MAX_BILLED_MINUTES",
    "MAX_EDITING_OUTPUT_ARTIFACTS",
    "EditingOutputArtifactRecord",
    "EditingOutputCost",
    "EditingOutputCostSource",
    "EditingOutputKind",
    "EditingOutputLedger",
    "EditingOutputLedgerConflict",
    "EditingOutputLineage",
    "InMemoryEditingOutputLedger",
    "InvalidEditingOutputModel",
]
