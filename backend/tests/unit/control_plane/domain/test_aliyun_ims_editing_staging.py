"""VE-04: Aliyun IMS/ICE editing credentials preflight and media staging tests."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from automation_tool.control_plane.domain.aliyun_ims_editing_staging import (
    ALIYUN_IMS_EDITING_STAGING_CONTRACT_VERSION,
    AliyunImsEditingStagingContract,
    AliyunImsRegion,
    EditingCostEstimate,
    EditingServicePreflight,
    InvalidAliyunImsEditingStagingContract,
    InvalidAliyunImsEditingStagingModel,
    MediaStagingPlan,
    PreflightCheckStatus,
    RegionPriceGroup,
    StagingAsset,
    build_media_staging_plan,
    estimate_editing_cost,
    load_aliyun_ims_editing_staging_contract,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts/video/aliyun-ims-editing-staging.v1.json"

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
FIXED_CONTRACT_MESSAGE = "Aliyun IMS editing staging contract document is invalid"
FIXED_MODEL_MESSAGE = "Aliyun IMS editing staging value is invalid"


@pytest.fixture(scope="module")
def contract() -> AliyunImsEditingStagingContract:
    return load_aliyun_ims_editing_staging_contract(CONTRACT_PATH)


def _contract_document() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    return document


def _write(tmp_path: Path, document: dict[str, Any]) -> Path:
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def _asset(
    logical_id: str = "artifact-1",
    sha256_hex: str = DIGEST_A,
    size_bytes: int = 1024,
    extension: str = ".mp4",
) -> StagingAsset:
    return StagingAsset(
        logical_id=logical_id,
        sha256_hex=sha256_hex,
        size_bytes=size_bytes,
        extension=extension,
    )


class TestContractLoading:
    def test_repository_contract_loads(self, contract: AliyunImsEditingStagingContract) -> None:
        assert ALIYUN_IMS_EDITING_STAGING_CONTRACT_VERSION == 1
        assert contract.verified_at == "2026-07-23"
        assert contract.api_version == "2020-11-09"
        assert contract.signature_algorithm == "ACS3-HMAC-SHA256"

    def test_regions_match_closed_enum(self, contract: AliyunImsEditingStagingContract) -> None:
        assert set(contract.region_price_groups) == set(AliyunImsRegion)
        assert contract.region_price_groups[AliyunImsRegion.CN_BEIJING] is RegionPriceGroup.MAINLAND
        assert contract.region_price_groups[AliyunImsRegion.US_WEST_1] is RegionPriceGroup.OVERSEAS
        for region in AliyunImsRegion:
            assert contract.endpoints[region] == f"ice.{region.value}.aliyuncs.com"

    def test_staging_rules_locked(self, contract: AliyunImsEditingStagingContract) -> None:
        assert contract.object_key_prefix == "editing-staging/v1/"
        assert contract.retention_days == 7
        assert contract.max_object_bytes == 2_147_483_648
        assert contract.max_assets_per_plan == 100
        assert ".mp4" in contract.allowed_extensions

    def test_billing_tiers_locked(self, contract: AliyunImsEditingStagingContract) -> None:
        heights = [tier.max_pixel_height for tier in contract.billing_tiers]
        assert heights == [480, 720, 1080, 1440, 2160]
        top = contract.billing_tiers[-1]
        assert top.price_per_minute[RegionPriceGroup.MAINLAND] == Decimal("0.24")
        assert top.price_per_minute[RegionPriceGroup.OVERSEAS] == Decimal("0.72")

    def test_missing_file_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(InvalidAliyunImsEditingStagingContract):
            load_aliyun_ims_editing_staging_contract(tmp_path / "missing.json")

    def test_malformed_json_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "contract.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(InvalidAliyunImsEditingStagingContract):
            load_aliyun_ims_editing_staging_contract(path)

    def test_non_object_document_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "contract.json"
        path.write_text("[]", encoding="utf-8")
        with pytest.raises(InvalidAliyunImsEditingStagingContract):
            load_aliyun_ims_editing_staging_contract(path)

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda document: document.pop("sources"),
            lambda document: document.update(version=2),
            lambda document: document.update(contract="other"),
            lambda document: document.update(verified_at="not-a-date"),
            lambda document: document.update(extra="field"),
            lambda document: document["service"].update(provider="tencent"),
            lambda document: document["service"].update(signature_algorithm="ACS3-RSA"),
            lambda document: document["regions"].pop(),
            lambda document: document["regions"][0].update(region_id="cn-qingdao"),
            lambda document: document["regions"][0].update(price_group="unknown"),
            lambda document: document["regions"][0].update(endpoint="ice.evil.example.com"),
            lambda document: document["regions"][0].update(label=""),
            lambda document: document["regions"].append(dict(document["regions"][0])),
            lambda document: document["same_region_rule"].update(
                input_output_oss_must_match_service_region=False
            ),
            lambda document: document["staging"].update(object_key_prefix="/absolute/"),
            lambda document: document["staging"].update(
                object_key_template="editing-staging/v1/{name}"
            ),
            lambda document: document["staging"].update(digest_algorithm="md5"),
            lambda document: document["staging"].update(deduplication_key="filename"),
            lambda document: document["staging"].update(retention_days=0),
            lambda document: document["staging"].update(retention_days=366),
            lambda document: document["staging"]["lifecycle_rule"].update(action="keep"),
            lambda document: document["staging"]["lifecycle_rule"].update(expiration_days=30),
            lambda document: document["staging"]["lifecycle_rule"].update(prefix="other/"),
            lambda document: document["staging"].update(max_object_bytes=0),
            lambda document: document["staging"].update(max_assets_per_plan=0),
            lambda document: document["staging"].update(allowed_extensions=[]),
            lambda document: document["staging"].update(allowed_extensions=["mp4"]),
            lambda document: document["staging"].update(allowed_extensions=[".MP4"]),
            lambda document: document["billing"].update(currency="USD"),
            lambda document: document["billing"].update(failed_jobs_billed=True),
            lambda document: document["billing"].update(rounding="per_second"),
            lambda document: document["billing"]["resolution_tiers"].pop(0),
            lambda document: document["billing"]["resolution_tiers"][0].update(
                max_pixel_height=720
            ),
            lambda document: document["billing"]["resolution_tiers"][0]["price_per_minute"].update(
                mainland="0"
            ),
            lambda document: document["billing"]["resolution_tiers"][0]["price_per_minute"].update(
                mainland="abc"
            ),
            lambda document: document["ram"].update(system_policy_full="AliyunOSSFullAccess"),
            lambda document: document["ram"]["minimal_policy_template"].update(Version="2012"),
            lambda document: document["ram"]["minimal_policy_template"].update(Statement=[]),
            lambda document: document["sources"].clear(),
            lambda document: document["sources"][0].update(url="http://example.com"),
            lambda document: document["sources"][0].update(verified_at="someday"),
            lambda document: document["ram"].update(system_policy_readonly="AliyunICEFullAccess"),
            lambda document: document["ram"]["minimal_policy_template"]["Statement"][0].update(
                Effect="Deny"
            ),
            lambda document: document["ram"]["minimal_policy_template"]["Statement"][0].update(
                Action=[]
            ),
            lambda document: document["ram"]["minimal_policy_template"]["Statement"][0].update(
                Action=["ec2:RunInstances"]
            ),
            lambda document: document.update(regions={}),
            lambda document: document["staging"]["allowed_extensions"].remove(".mp4"),
            lambda document: document["billing"].update(resolution_tiers="tiers"),
            lambda document: document.update(pending_credential_verification=[]),
        ],
    )
    def test_tampered_document_rejected(self, tmp_path: Path, mutate: Any) -> None:
        document = _contract_document()
        mutate(document)
        with pytest.raises(InvalidAliyunImsEditingStagingContract) as info:
            load_aliyun_ims_editing_staging_contract(_write(tmp_path, document))
        assert str(info.value) == FIXED_CONTRACT_MESSAGE


class TestPreflight:
    def test_ready_only_when_all_checks_pass(self) -> None:
        preflight = EditingServicePreflight(
            region=AliyunImsRegion.CN_SHANGHAI,
            region_check=PreflightCheckStatus.PASSED,
            permission_check=PreflightCheckStatus.PASSED,
            quota_check=PreflightCheckStatus.PASSED,
        )
        assert preflight.ready is True

    @pytest.mark.parametrize(
        "statuses",
        [
            (PreflightCheckStatus.FAILED, PreflightCheckStatus.PASSED, PreflightCheckStatus.PASSED),
            (PreflightCheckStatus.PASSED, PreflightCheckStatus.FAILED, PreflightCheckStatus.PASSED),
            (PreflightCheckStatus.PASSED, PreflightCheckStatus.PASSED, PreflightCheckStatus.FAILED),
            (
                PreflightCheckStatus.UNVERIFIED,
                PreflightCheckStatus.PASSED,
                PreflightCheckStatus.PASSED,
            ),
            (
                PreflightCheckStatus.PASSED,
                PreflightCheckStatus.UNVERIFIED,
                PreflightCheckStatus.UNVERIFIED,
            ),
        ],
    )
    def test_any_non_passed_check_fails_closed(
        self, statuses: tuple[PreflightCheckStatus, PreflightCheckStatus, PreflightCheckStatus]
    ) -> None:
        region_check, permission_check, quota_check = statuses
        preflight = EditingServicePreflight(
            region=AliyunImsRegion.CN_SHANGHAI,
            region_check=region_check,
            permission_check=permission_check,
            quota_check=quota_check,
        )
        assert preflight.ready is False

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"region": "cn-shanghai"},
            {"region_check": "passed"},
            {"permission_check": None},
            {"quota_check": 1},
        ],
    )
    def test_invalid_field_types_rejected(self, kwargs: dict[str, Any]) -> None:
        values: dict[str, Any] = {
            "region": AliyunImsRegion.CN_SHANGHAI,
            "region_check": PreflightCheckStatus.PASSED,
            "permission_check": PreflightCheckStatus.PASSED,
            "quota_check": PreflightCheckStatus.PASSED,
        }
        values.update(kwargs)
        with pytest.raises(InvalidAliyunImsEditingStagingModel):
            EditingServicePreflight(**values)

    def test_preflight_is_immutable(self) -> None:
        preflight = EditingServicePreflight(
            region=AliyunImsRegion.CN_SHANGHAI,
            region_check=PreflightCheckStatus.PASSED,
            permission_check=PreflightCheckStatus.PASSED,
            quota_check=PreflightCheckStatus.PASSED,
        )
        with pytest.raises(AttributeError):
            preflight.region = AliyunImsRegion.CN_BEIJING  # type: ignore[misc]


class TestStagingAsset:
    def test_valid_asset(self) -> None:
        asset = _asset()
        assert asset.sha256_hex == DIGEST_A
        assert asset.extension == ".mp4"

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"logical_id": ""},
            {"logical_id": "a" * 65},
            {"logical_id": "../escape"},
            {"logical_id": "with space"},
            {"logical_id": 7},
            {"sha256_hex": "A" * 64},
            {"sha256_hex": "a" * 63},
            {"sha256_hex": "g" * 64},
            {"sha256_hex": 5},
            {"size_bytes": 0},
            {"size_bytes": -1},
            {"size_bytes": True},
            {"size_bytes": 2_147_483_649},
            {"extension": "mp4"},
            {"extension": ".exe"},
            {"extension": ".MP4"},
            {"extension": None},
        ],
    )
    def test_invalid_asset_rejected(self, kwargs: dict[str, Any]) -> None:
        values: dict[str, Any] = {
            "logical_id": "artifact-1",
            "sha256_hex": DIGEST_A,
            "size_bytes": 1024,
            "extension": ".mp4",
        }
        values.update(kwargs)
        with pytest.raises(InvalidAliyunImsEditingStagingModel) as info:
            StagingAsset(**values)
        assert str(info.value) == FIXED_MODEL_MESSAGE


class TestStagingPlan:
    def test_plan_stages_to_digest_keys(self, contract: AliyunImsEditingStagingContract) -> None:
        plan = build_media_staging_plan(
            contract=contract,
            service_region=AliyunImsRegion.CN_SHANGHAI,
            bucket_region=AliyunImsRegion.CN_SHANGHAI,
            assets=(_asset(),),
        )
        assert isinstance(plan, MediaStagingPlan)
        assert plan.region is AliyunImsRegion.CN_SHANGHAI
        [staging_object] = plan.objects
        assert staging_object.object_key == f"editing-staging/v1/{DIGEST_A}.mp4"
        assert staging_object.retention_days == 7
        assert plan.object_key_for("artifact-1") == staging_object.object_key

    def test_plan_deduplicates_identical_content(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        plan = build_media_staging_plan(
            contract=contract,
            service_region=AliyunImsRegion.CN_BEIJING,
            bucket_region=AliyunImsRegion.CN_BEIJING,
            assets=(
                _asset(logical_id="artifact-1"),
                _asset(logical_id="artifact-2"),
                _asset(logical_id="artifact-3", sha256_hex=DIGEST_B, extension=".png"),
            ),
        )
        assert len(plan.objects) == 2
        assert plan.object_key_for("artifact-1") == plan.object_key_for("artifact-2")
        assert plan.object_key_for("artifact-3") == f"editing-staging/v1/{DIGEST_B}.png"

    def test_cross_region_bucket_fails_closed(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        with pytest.raises(InvalidAliyunImsEditingStagingModel) as info:
            build_media_staging_plan(
                contract=contract,
                service_region=AliyunImsRegion.CN_SHANGHAI,
                bucket_region=AliyunImsRegion.CN_BEIJING,
                assets=(_asset(),),
            )
        assert str(info.value) == FIXED_MODEL_MESSAGE

    def test_conflicting_metadata_for_same_digest_rejected(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        with pytest.raises(InvalidAliyunImsEditingStagingModel):
            build_media_staging_plan(
                contract=contract,
                service_region=AliyunImsRegion.CN_SHANGHAI,
                bucket_region=AliyunImsRegion.CN_SHANGHAI,
                assets=(
                    _asset(logical_id="artifact-1", size_bytes=1024),
                    _asset(logical_id="artifact-2", size_bytes=2048),
                ),
            )

    def test_duplicate_logical_id_rejected(self, contract: AliyunImsEditingStagingContract) -> None:
        with pytest.raises(InvalidAliyunImsEditingStagingModel):
            build_media_staging_plan(
                contract=contract,
                service_region=AliyunImsRegion.CN_SHANGHAI,
                bucket_region=AliyunImsRegion.CN_SHANGHAI,
                assets=(_asset(), _asset(sha256_hex=DIGEST_B)),
            )

    def test_empty_and_oversized_plans_rejected(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        with pytest.raises(InvalidAliyunImsEditingStagingModel):
            build_media_staging_plan(
                contract=contract,
                service_region=AliyunImsRegion.CN_SHANGHAI,
                bucket_region=AliyunImsRegion.CN_SHANGHAI,
                assets=(),
            )
        too_many = tuple(
            _asset(logical_id=f"artifact-{index}", sha256_hex=f"{index:064x}")
            for index in range(101)
        )
        with pytest.raises(InvalidAliyunImsEditingStagingModel):
            build_media_staging_plan(
                contract=contract,
                service_region=AliyunImsRegion.CN_SHANGHAI,
                bucket_region=AliyunImsRegion.CN_SHANGHAI,
                assets=too_many,
            )

    def test_invalid_argument_types_rejected(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        with pytest.raises(InvalidAliyunImsEditingStagingModel):
            build_media_staging_plan(
                contract=contract,
                service_region="cn-shanghai",  # type: ignore[arg-type]
                bucket_region=AliyunImsRegion.CN_SHANGHAI,
                assets=(_asset(),),
            )
        with pytest.raises(InvalidAliyunImsEditingStagingModel):
            build_media_staging_plan(
                contract=contract,
                service_region=AliyunImsRegion.CN_SHANGHAI,
                bucket_region=AliyunImsRegion.CN_SHANGHAI,
                assets=[_asset()],  # type: ignore[arg-type]
            )
        with pytest.raises(InvalidAliyunImsEditingStagingModel):
            build_media_staging_plan(
                contract=contract,
                service_region=AliyunImsRegion.CN_SHANGHAI,
                bucket_region=AliyunImsRegion.CN_SHANGHAI,
                assets=(object(),),  # type: ignore[arg-type]
            )

    def test_unknown_logical_id_lookup_fails_closed(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        plan = build_media_staging_plan(
            contract=contract,
            service_region=AliyunImsRegion.CN_SHANGHAI,
            bucket_region=AliyunImsRegion.CN_SHANGHAI,
            assets=(_asset(),),
        )
        with pytest.raises(InvalidAliyunImsEditingStagingModel):
            plan.object_key_for("missing")


class TestCostEstimate:
    def test_mainland_1080p_estimate(self, contract: AliyunImsEditingStagingContract) -> None:
        estimate = estimate_editing_cost(
            contract=contract,
            region=AliyunImsRegion.CN_SHANGHAI,
            output_duration_ms=90_000,
            output_height=1080,
        )
        assert isinstance(estimate, EditingCostEstimate)
        assert estimate.billed_minutes == 2
        assert estimate.tier_id == "up_to_1080p"
        assert estimate.unit_price_cny == Decimal("0.06")
        assert estimate.estimated_total_cny == Decimal("0.12")
        assert estimate.currency == "CNY"

    def test_partial_minute_rounds_up(self, contract: AliyunImsEditingStagingContract) -> None:
        estimate = estimate_editing_cost(
            contract=contract,
            region=AliyunImsRegion.CN_BEIJING,
            output_duration_ms=1,
            output_height=480,
        )
        assert estimate.billed_minutes == 1
        assert estimate.estimated_total_cny == Decimal("0.015")

    def test_overseas_region_uses_overseas_price(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        estimate = estimate_editing_cost(
            contract=contract,
            region=AliyunImsRegion.AP_SOUTHEAST_1,
            output_duration_ms=60_000,
            output_height=720,
        )
        assert estimate.unit_price_cny == Decimal("0.09")
        assert estimate.estimated_total_cny == Decimal("0.09")

    def test_height_maps_to_smallest_covering_tier(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        estimate = estimate_editing_cost(
            contract=contract,
            region=AliyunImsRegion.CN_SHANGHAI,
            output_duration_ms=60_000,
            output_height=721,
        )
        assert estimate.tier_id == "up_to_1080p"

    @pytest.mark.parametrize(
        ("duration_ms", "height"),
        [
            (0, 1080),
            (-1, 1080),
            (14_400_001, 1080),
            (60_000, 0),
            (60_000, 15),
            (60_000, 2161),
            (True, 1080),
            (60_000, True),
        ],
    )
    def test_invalid_estimate_inputs_rejected(
        self,
        contract: AliyunImsEditingStagingContract,
        duration_ms: int,
        height: int,
    ) -> None:
        with pytest.raises(InvalidAliyunImsEditingStagingModel):
            estimate_editing_cost(
                contract=contract,
                region=AliyunImsRegion.CN_SHANGHAI,
                output_duration_ms=duration_ms,
                output_height=height,
            )

    def test_invalid_region_rejected(self, contract: AliyunImsEditingStagingContract) -> None:
        with pytest.raises(InvalidAliyunImsEditingStagingModel):
            estimate_editing_cost(
                contract=contract,
                region="cn-shanghai",  # type: ignore[arg-type]
                output_duration_ms=60_000,
                output_height=1080,
            )


class TestSanitizedErrors:
    def test_error_messages_are_fixed_and_leak_free(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        with pytest.raises(InvalidAliyunImsEditingStagingModel) as info:
            StagingAsset(
                logical_id="secret-bucket/path",
                sha256_hex=DIGEST_A,
                size_bytes=1,
                extension=".mp4",
            )
        message = str(info.value)
        assert message == FIXED_MODEL_MESSAGE
        assert "secret-bucket" not in message
        assert DIGEST_A not in message
