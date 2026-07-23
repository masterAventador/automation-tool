"""VE-07: provider-neutral editing output records, lineage, cost and ledger."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

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
    InMemoryEditingOutputLedger,
    InvalidEditingOutputModel,
)
from automation_tool.control_plane.domain.video_editing_provider import EditingProviderId

JOB_ID = EditingJobId(UUID("00000000-0000-4000-8000-0000000000d7"))
PROJECT_ID = EditingProjectId(UUID("00000000-0000-4000-8000-0000000000d1"))
TIMELINE_ID = TimelineId(UUID("00000000-0000-4000-8000-0000000000d2"))
INPUT_ARTIFACT = ArtifactId(UUID("00000000-0000-4000-8000-0000000000a1"))
OUTPUT_ARTIFACT = ArtifactId(UUID("00000000-0000-4000-8000-0000000000b1"))
PROVIDER_ID = EditingProviderId("aliyun_ims")
CREATED_AT = datetime(2026, 7, 23, 12, 0, 0, tzinfo=UTC)
SHA256 = "ab" * 32


def _video_record(
    *,
    artifact_id: ArtifactId = OUTPUT_ARTIFACT,
    kind: EditingOutputKind = EditingOutputKind.VIDEO,
    media_type: str = "video/mp4",
    byte_size: int = 1024,
    sha256_hex: str = SHA256,
) -> EditingOutputArtifactRecord:
    return EditingOutputArtifactRecord(
        artifact_id=artifact_id,
        kind=kind,
        media_type=media_type,
        byte_size=byte_size,
        sha256_hex=sha256_hex,
        created_at=CREATED_AT,
    )


def _cost(
    *,
    billed_minutes: int = 1,
    unit_price_cny: Decimal = Decimal("0.85"),
    total_cny: Decimal = Decimal("0.85"),
) -> EditingOutputCost:
    return EditingOutputCost(
        source=EditingOutputCostSource.ESTIMATED,
        currency="CNY",
        billed_minutes=billed_minutes,
        tier_id="hd-1080",
        unit_price_cny=unit_price_cny,
        total_cny=total_cny,
    )


def _lineage(
    *,
    outputs: tuple[EditingOutputArtifactRecord, ...] | None = None,
    input_artifact_ids: tuple[ArtifactId, ...] = (INPUT_ARTIFACT,),
) -> EditingOutputLineage:
    return EditingOutputLineage(
        editing_job_id=JOB_ID,
        project_id=PROJECT_ID,
        timeline_id=TIMELINE_ID,
        timeline_revision=1,
        provider_id=PROVIDER_ID,
        provider_contract_verified_at="2026-07-23",
        input_artifact_ids=input_artifact_ids,
        outputs=outputs if outputs is not None else (_video_record(),),
        cost=_cost(),
        created_at=CREATED_AT,
    )


class TestEditingOutputArtifactRecord:
    def test_accepts_each_kind_with_documented_media_type(self) -> None:
        cases = {
            EditingOutputKind.VIDEO: "video/mp4",
            EditingOutputKind.COVER: "image/jpeg",
            EditingOutputKind.SUBTITLE: "text/vtt",
            EditingOutputKind.METADATA: "application/json",
        }
        for kind, media_type in cases.items():
            record = _video_record(kind=kind, media_type=media_type)
            assert record.kind is kind
            assert record.media_type == media_type

    def test_rejects_media_type_outside_kind_vocabulary(self) -> None:
        with pytest.raises(InvalidEditingOutputModel):
            _video_record(kind=EditingOutputKind.VIDEO, media_type="application/json")

    def test_rejects_unknown_media_type(self) -> None:
        with pytest.raises(InvalidEditingOutputModel):
            _video_record(media_type="video/x-msvideo")

    def test_rejects_zero_and_negative_byte_size(self) -> None:
        for byte_size in (0, -1):
            with pytest.raises(InvalidEditingOutputModel):
                _video_record(byte_size=byte_size)

    def test_rejects_malformed_sha256(self) -> None:
        for digest in ("", "zz" * 32, "AB" * 32, "ab" * 31):
            with pytest.raises(InvalidEditingOutputModel):
                _video_record(sha256_hex=digest)

    def test_rejects_naive_created_at(self) -> None:
        with pytest.raises(InvalidEditingOutputModel):
            EditingOutputArtifactRecord(
                artifact_id=OUTPUT_ARTIFACT,
                kind=EditingOutputKind.VIDEO,
                media_type="video/mp4",
                byte_size=1,
                sha256_hex=SHA256,
                created_at=datetime(2026, 7, 23),
            )


class TestEditingOutputCost:
    def test_requires_total_to_equal_unit_price_times_minutes(self) -> None:
        cost = _cost(
            billed_minutes=3, unit_price_cny=Decimal("0.85"), total_cny=Decimal("2.55")
        )
        assert cost.total_cny == Decimal("2.55")
        with pytest.raises(InvalidEditingOutputModel):
            _cost(billed_minutes=3, unit_price_cny=Decimal("0.85"), total_cny=Decimal("2.56"))

    def test_rejects_non_cny_currency_and_non_positive_minutes(self) -> None:
        with pytest.raises(InvalidEditingOutputModel):
            EditingOutputCost(
                source=EditingOutputCostSource.ESTIMATED,
                currency="USD",
                billed_minutes=1,
                tier_id="hd-1080",
                unit_price_cny=Decimal("0.85"),
                total_cny=Decimal("0.85"),
            )
        with pytest.raises(InvalidEditingOutputModel):
            _cost(billed_minutes=0, total_cny=Decimal("0"))

    def test_rejects_negative_unit_price(self) -> None:
        with pytest.raises(InvalidEditingOutputModel):
            _cost(unit_price_cny=Decimal("-0.01"), total_cny=Decimal("-0.01"))

    def test_cost_source_vocabulary_is_closed(self) -> None:
        assert {source.value for source in EditingOutputCostSource} == {
            "estimated",
            "billed",
        }


class TestEditingOutputLineage:
    def test_accepts_complete_lineage(self) -> None:
        lineage = _lineage()
        assert lineage.editing_job_id == JOB_ID
        assert lineage.outputs[0].kind is EditingOutputKind.VIDEO

    def test_requires_exactly_one_video_output(self) -> None:
        metadata_only = _video_record(
            kind=EditingOutputKind.METADATA, media_type="application/json"
        )
        with pytest.raises(InvalidEditingOutputModel):
            _lineage(outputs=(metadata_only,))
        second_video = _video_record(
            artifact_id=ArtifactId(UUID("00000000-0000-4000-8000-0000000000b2"))
        )
        with pytest.raises(InvalidEditingOutputModel):
            _lineage(outputs=(_video_record(), second_video))

    def test_allows_supplementary_kinds_alongside_video(self) -> None:
        cover = EditingOutputArtifactRecord(
            artifact_id=ArtifactId(UUID("00000000-0000-4000-8000-0000000000b3")),
            kind=EditingOutputKind.COVER,
            media_type="image/png",
            byte_size=10,
            sha256_hex="cd" * 32,
            created_at=CREATED_AT,
        )
        lineage = _lineage(outputs=(_video_record(), cover))
        assert len(lineage.outputs) == 2

    def test_rejects_empty_inputs_and_duplicate_ids(self) -> None:
        with pytest.raises(InvalidEditingOutputModel):
            _lineage(input_artifact_ids=())
        with pytest.raises(InvalidEditingOutputModel):
            _lineage(input_artifact_ids=(INPUT_ARTIFACT, INPUT_ARTIFACT))

    def test_rejects_output_reusing_input_artifact_id(self) -> None:
        with pytest.raises(InvalidEditingOutputModel):
            _lineage(outputs=(_video_record(artifact_id=INPUT_ARTIFACT),))

    def test_rejects_duplicate_output_artifact_ids(self) -> None:
        metadata = _video_record(
            kind=EditingOutputKind.METADATA, media_type="application/json"
        )
        with pytest.raises(InvalidEditingOutputModel):
            _lineage(outputs=(_video_record(), metadata))

    def test_rejects_malformed_contract_verification_date(self) -> None:
        for verified_at in ("", "2026/07/23", "20260723", "2026-7-23"):
            with pytest.raises(InvalidEditingOutputModel):
                EditingOutputLineage(
                    editing_job_id=JOB_ID,
                    project_id=PROJECT_ID,
                    timeline_id=TIMELINE_ID,
                    timeline_revision=1,
                    provider_id=PROVIDER_ID,
                    provider_contract_verified_at=verified_at,
                    input_artifact_ids=(INPUT_ARTIFACT,),
                    outputs=(_video_record(),),
                    cost=_cost(),
                    created_at=CREATED_AT,
                )

    def test_rejects_non_positive_timeline_revision(self) -> None:
        with pytest.raises(InvalidEditingOutputModel):
            EditingOutputLineage(
                editing_job_id=JOB_ID,
                project_id=PROJECT_ID,
                timeline_id=TIMELINE_ID,
                timeline_revision=0,
                provider_id=PROVIDER_ID,
                provider_contract_verified_at="2026-07-23",
                input_artifact_ids=(INPUT_ARTIFACT,),
                outputs=(_video_record(),),
                cost=_cost(),
                created_at=CREATED_AT,
            )


class TestInMemoryEditingOutputLedger:
    @pytest.mark.asyncio
    async def test_saves_and_loads_one_lineage(self) -> None:
        ledger = InMemoryEditingOutputLedger()
        lineage = _lineage()
        await ledger.save(lineage)
        assert await ledger.load(JOB_ID) == lineage

    @pytest.mark.asyncio
    async def test_load_unknown_job_returns_none(self) -> None:
        assert await InMemoryEditingOutputLedger().load(JOB_ID) is None

    @pytest.mark.asyncio
    async def test_identical_duplicate_save_is_idempotent(self) -> None:
        ledger = InMemoryEditingOutputLedger()
        await ledger.save(_lineage())
        await ledger.save(_lineage())
        assert await ledger.load(JOB_ID) == _lineage()

    @pytest.mark.asyncio
    async def test_conflicting_duplicate_save_is_rejected(self) -> None:
        ledger = InMemoryEditingOutputLedger()
        await ledger.save(_lineage())
        different = _lineage(
            outputs=(
                _video_record(
                    artifact_id=ArtifactId(UUID("00000000-0000-4000-8000-0000000000b9"))
                ),
            )
        )
        with pytest.raises(EditingOutputLedgerConflict):
            await ledger.save(different)
        assert await ledger.load(JOB_ID) == _lineage()
