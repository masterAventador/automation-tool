"""Aliyun IMS/ICE editing-service preflight, media staging and cost estimation.

VE-04 locks the officially documented IMS/ICE region list, the same-region OSS
staging rule, the minimal RAM policy template, staging object-key/dedup/
lifecycle rules and the media-producing billing tiers into closed, fail-closed
types. This module is the Aliyun adapter's private vocabulary: it never appears
in the provider-neutral editing domain (`video_editing*`), and no credential,
bucket name or user path ever enters an error message. VE-05 consumes the
preflight, staging plan and cost estimate when compiling and submitting real
editing jobs.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum, unique
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Never, final

ALIYUN_IMS_EDITING_STAGING_CONTRACT_VERSION: Final = 1

_CONTRACT_NAME: Final = "aliyun-ims-editing-staging"
_VERIFIED_AT_PATTERN: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_LOGICAL_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_EXTENSION_PATTERN: Final = re.compile(r"^\.[0-9a-z]{1,8}$")
_KEY_PREFIX_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9/-]{0,63}/$")
_PRICE_PATTERN: Final = re.compile(r"^\d+(\.\d{1,6})?$")
_ICE_ACTION_PATTERN: Final = re.compile(r"^ice:[A-Za-z]+$")
_OSS_ACTION_PATTERN: Final = re.compile(r"^oss:[A-Za-z]+$")

_TOP_LEVEL_KEYS: Final = frozenset(
    {
        "contract",
        "version",
        "verified_at",
        "policy",
        "sources",
        "service",
        "regions",
        "same_region_rule",
        "ram",
        "staging",
        "billing",
        "pending_credential_verification",
    }
)

ALLOWED_STAGING_EXTENSIONS: Final = frozenset(
    {".aac", ".jpeg", ".jpg", ".m4a", ".mov", ".mp3", ".mp4", ".png", ".srt", ".wav"}
)
_BILLING_TIER_HEIGHTS: Final = (480, 720, 1080, 1440, 2160)
MAX_STAGING_OBJECT_BYTES: Final = 2_147_483_648
MAX_STAGING_RETENTION_DAYS: Final = 365
MAX_EDITING_OUTPUT_DURATION_MS: Final = 14_400_000
_MIN_OUTPUT_HEIGHT: Final = 16
_MS_PER_MINUTE: Final = 60_000


class InvalidAliyunImsEditingStagingContract(ValueError):
    """The committed Aliyun IMS editing staging contract document is invalid."""

    def __init__(self) -> None:
        super().__init__("Aliyun IMS editing staging contract document is invalid")


class InvalidAliyunImsEditingStagingModel(ValueError):
    """An Aliyun IMS editing staging value violates the locked contract."""

    def __init__(self) -> None:
        super().__init__("Aliyun IMS editing staging value is invalid")


def _reject_contract() -> Never:
    raise InvalidAliyunImsEditingStagingContract


def _reject() -> Never:
    raise InvalidAliyunImsEditingStagingModel


@unique
class AliyunImsRegion(StrEnum):
    """Closed list of IMS/ICE regions from the official endpoint document."""

    CN_BEIJING = "cn-beijing"
    CN_HANGZHOU = "cn-hangzhou"
    CN_SHANGHAI = "cn-shanghai"
    CN_SHENZHEN = "cn-shenzhen"
    AP_SOUTHEAST_1 = "ap-southeast-1"
    US_WEST_1 = "us-west-1"


@unique
class RegionPriceGroup(StrEnum):
    """Official billing groups: mainland China versus overseas regions."""

    MAINLAND = "mainland"
    OVERSEAS = "overseas"


@unique
class PreflightCheckStatus(StrEnum):
    """Closed outcome of one editing-service preflight check."""

    PASSED = "passed"
    FAILED = "failed"
    UNVERIFIED = "unverified"


@final
@dataclass(frozen=True, slots=True)
class EditingBillingTier:
    """One official media-producing resolution tier with locked unit prices."""

    tier_id: str
    max_pixel_height: int
    price_per_minute: Mapping[RegionPriceGroup, Decimal]


@final
@dataclass(frozen=True, slots=True)
class AliyunImsEditingStagingContract:
    """Validated, immutable projection of the committed VE-04 contract."""

    verified_at: str
    api_version: str
    signature_algorithm: str
    connection_test_action: str
    endpoints: Mapping[AliyunImsRegion, str]
    region_labels: Mapping[AliyunImsRegion, str]
    region_price_groups: Mapping[AliyunImsRegion, RegionPriceGroup]
    object_key_prefix: str
    retention_days: int
    max_object_bytes: int
    max_assets_per_plan: int
    allowed_extensions: frozenset[str]
    billing_tiers: tuple[EditingBillingTier, ...]


@final
@dataclass(frozen=True, slots=True)
class EditingServicePreflight:
    """Fail-closed result of region, permission and quota preflight checks."""

    region: AliyunImsRegion
    region_check: PreflightCheckStatus
    permission_check: PreflightCheckStatus
    quota_check: PreflightCheckStatus

    def __post_init__(self) -> None:
        if not isinstance(self.region, AliyunImsRegion) or any(
            not isinstance(status, PreflightCheckStatus)
            for status in (self.region_check, self.permission_check, self.quota_check)
        ):
            _reject()

    @property
    def ready(self) -> bool:
        """True only when every check passed; failed or unverified stays closed."""
        return all(
            status is PreflightCheckStatus.PASSED
            for status in (self.region_check, self.permission_check, self.quota_check)
        )


@final
@dataclass(frozen=True, slots=True)
class StagingAsset:
    """One local media file identified by content digest, never by path."""

    logical_id: str
    sha256_hex: str
    size_bytes: int
    extension: str

    def __post_init__(self) -> None:
        if (
            type(self.logical_id) is not str
            or _LOGICAL_ID_PATTERN.fullmatch(self.logical_id) is None
            or type(self.sha256_hex) is not str
            or _SHA256_PATTERN.fullmatch(self.sha256_hex) is None
            or type(self.size_bytes) is not int
            or not 1 <= self.size_bytes <= MAX_STAGING_OBJECT_BYTES
            or type(self.extension) is not str
            or self.extension not in ALLOWED_STAGING_EXTENSIONS
        ):
            _reject()


@final
@dataclass(frozen=True, slots=True)
class StagingObject:
    """One deduplicated OSS staging object derived from a content digest."""

    sha256_hex: str
    extension: str
    size_bytes: int
    object_key: str
    retention_days: int


@final
@dataclass(frozen=True, slots=True)
class MediaStagingPlan:
    """Same-region, digest-deduplicated staging plan for one editing intent."""

    region: AliyunImsRegion
    objects: tuple[StagingObject, ...]
    _keys_by_logical_id: Mapping[str, str]

    def object_key_for(self, logical_id: str) -> str:
        """Return the staged object key for one logical asset; unknown fails."""
        key = self._keys_by_logical_id.get(logical_id)
        if key is None:
            _reject()
        return key


@final
@dataclass(frozen=True, slots=True)
class EditingCostEstimate:
    """Pre-submission estimate covering only the media-producing billing item."""

    region: AliyunImsRegion
    billed_minutes: int
    tier_id: str
    unit_price_cny: Decimal
    estimated_total_cny: Decimal
    currency: str


def _require_mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        _reject_contract()
    return value


def _require_exact_keys(value: dict[str, Any], keys: frozenset[str]) -> None:
    if set(value) != keys:
        _reject_contract()


def _require_str(value: object) -> str:
    if type(value) is not str or not value:
        _reject_contract()
    return value


def _require_int(value: object, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _reject_contract()
    return value


def _require_true(value: object) -> None:
    if value is not True:
        _reject_contract()


def _require_false(value: object) -> None:
    if value is not False:
        _reject_contract()


def _parse_price(value: object) -> Decimal:
    if type(value) is not str or _PRICE_PATTERN.fullmatch(value) is None:
        _reject_contract()
    try:
        price = Decimal(value)
    except InvalidOperation:  # pragma: no cover - pattern already guards this
        _reject_contract()
    if price <= 0:
        _reject_contract()
    return price


def _validate_sources(value: object) -> None:
    if not isinstance(value, list) or not value:
        _reject_contract()
    for entry in value:
        source = _require_mapping(entry)
        _require_exact_keys(source, frozenset({"claim", "url", "verified_at"}))
        url = _require_str(source["url"])
        if not url.startswith("https://help.aliyun.com/"):
            _reject_contract()
        _require_str(source["claim"])
        if _VERIFIED_AT_PATTERN.fullmatch(_require_str(source["verified_at"])) is None:
            _reject_contract()


def _validate_ram(value: object) -> None:
    ram = _require_mapping(value)
    _require_exact_keys(
        ram,
        frozenset(
            {
                "system_policy_full",
                "system_policy_readonly",
                "ice_resource_level_authorization_supported",
                "minimal_policy_template",
            }
        ),
    )
    if ram["system_policy_full"] != "AliyunICEFullAccess":
        _reject_contract()
    if ram["system_policy_readonly"] != "AliyunICEReadOnlyAccess":
        _reject_contract()
    _require_false(ram["ice_resource_level_authorization_supported"])
    template = _require_mapping(ram["minimal_policy_template"])
    _require_exact_keys(template, frozenset({"Version", "Statement"}))
    if template["Version"] != "1":
        _reject_contract()
    statements = template["Statement"]
    if not isinstance(statements, list) or not statements:
        _reject_contract()
    for entry in statements:
        statement = _require_mapping(entry)
        _require_exact_keys(statement, frozenset({"Effect", "Action", "Resource"}))
        if statement["Effect"] != "Allow":
            _reject_contract()
        actions = statement["Action"]
        if not isinstance(actions, list) or not actions:
            _reject_contract()
        for action in actions:
            action_name = _require_str(action)
            if (
                _ICE_ACTION_PATTERN.fullmatch(action_name) is None
                and _OSS_ACTION_PATTERN.fullmatch(action_name) is None
            ):
                _reject_contract()


def _parse_regions(
    value: object,
) -> tuple[
    dict[AliyunImsRegion, str],
    dict[AliyunImsRegion, str],
    dict[AliyunImsRegion, RegionPriceGroup],
]:
    if not isinstance(value, list):
        _reject_contract()
    endpoints: dict[AliyunImsRegion, str] = {}
    labels: dict[AliyunImsRegion, str] = {}
    groups: dict[AliyunImsRegion, RegionPriceGroup] = {}
    for entry in value:
        region_entry = _require_mapping(entry)
        _require_exact_keys(
            region_entry, frozenset({"region_id", "label", "endpoint", "price_group"})
        )
        region_id = _require_str(region_entry["region_id"])
        try:
            region = AliyunImsRegion(region_id)
            group = RegionPriceGroup(_require_str(region_entry["price_group"]))
        except ValueError:
            _reject_contract()
        if region in endpoints:
            _reject_contract()
        endpoint = _require_str(region_entry["endpoint"])
        if endpoint != f"ice.{region.value}.aliyuncs.com":
            _reject_contract()
        endpoints[region] = endpoint
        labels[region] = _require_str(region_entry["label"])
        groups[region] = group
    if set(endpoints) != set(AliyunImsRegion):
        _reject_contract()
    return endpoints, labels, groups


def _parse_staging(value: object) -> tuple[str, int, int, int, frozenset[str]]:
    staging = _require_mapping(value)
    _require_exact_keys(
        staging,
        frozenset(
            {
                "object_key_prefix",
                "object_key_template",
                "digest_algorithm",
                "deduplication_key",
                "retention_days",
                "lifecycle_rule",
                "max_object_bytes",
                "max_assets_per_plan",
                "allowed_extensions",
            }
        ),
    )
    prefix = _require_str(staging["object_key_prefix"])
    if _KEY_PREFIX_PATTERN.fullmatch(prefix) is None:
        _reject_contract()
    if staging["object_key_template"] != f"{prefix}{{sha256}}{{extension}}":
        _reject_contract()
    if staging["digest_algorithm"] != "sha256" or staging["deduplication_key"] != "sha256":
        _reject_contract()
    retention_days = _require_int(staging["retention_days"], 1, MAX_STAGING_RETENTION_DAYS)
    lifecycle = _require_mapping(staging["lifecycle_rule"])
    _require_exact_keys(lifecycle, frozenset({"match", "prefix", "action", "expiration_days"}))
    if (
        lifecycle["match"] != "prefix"
        or lifecycle["prefix"] != prefix
        or lifecycle["action"] != "delete"
        or lifecycle["expiration_days"] != retention_days
    ):
        _reject_contract()
    max_object_bytes = _require_int(staging["max_object_bytes"], 1, MAX_STAGING_OBJECT_BYTES)
    max_assets = _require_int(staging["max_assets_per_plan"], 1, 1000)
    extensions_value = staging["allowed_extensions"]
    if not isinstance(extensions_value, list) or not extensions_value:
        _reject_contract()
    extensions: set[str] = set()
    for entry in extensions_value:
        extension = _require_str(entry)
        if _EXTENSION_PATTERN.fullmatch(extension) is None or extension in extensions:
            _reject_contract()
        extensions.add(extension)
    if frozenset(extensions) != ALLOWED_STAGING_EXTENSIONS:
        _reject_contract()
    return prefix, retention_days, max_object_bytes, max_assets, frozenset(extensions)


def _parse_billing(value: object) -> tuple[EditingBillingTier, ...]:
    billing = _require_mapping(value)
    _require_exact_keys(
        billing,
        frozenset(
            {
                "billable_item",
                "rounding",
                "failed_jobs_billed",
                "currency",
                "resolution_tiers",
                "excluded_items_note",
            }
        ),
    )
    if (
        billing["billable_item"] != "media_producing_output_minutes"
        or billing["rounding"] != "per_started_minute"
        or billing["currency"] != "CNY"
    ):
        _reject_contract()
    _require_false(billing["failed_jobs_billed"])
    _require_str(billing["excluded_items_note"])
    tiers_value = billing["resolution_tiers"]
    if not isinstance(tiers_value, list) or not tiers_value:
        _reject_contract()
    if len(tiers_value) != len(_BILLING_TIER_HEIGHTS):
        _reject_contract()
    tiers: list[EditingBillingTier] = []
    for expected_height, entry in zip(_BILLING_TIER_HEIGHTS, tiers_value, strict=True):
        tier = _require_mapping(entry)
        _require_exact_keys(tier, frozenset({"tier", "max_pixel_height", "price_per_minute"}))
        tier_id = _require_str(tier["tier"])
        height = _require_int(tier["max_pixel_height"], 1, 4320)
        if height != expected_height:
            _reject_contract()
        prices = _require_mapping(tier["price_per_minute"])
        _require_exact_keys(prices, frozenset({"mainland", "overseas"}))
        tiers.append(
            EditingBillingTier(
                tier_id=tier_id,
                max_pixel_height=height,
                price_per_minute=MappingProxyType(
                    {
                        RegionPriceGroup.MAINLAND: _parse_price(prices["mainland"]),
                        RegionPriceGroup.OVERSEAS: _parse_price(prices["overseas"]),
                    }
                ),
            )
        )
    return tuple(tiers)


def load_aliyun_ims_editing_staging_contract(path: Path) -> AliyunImsEditingStagingContract:
    """Load and fully validate the committed VE-04 contract; fail closed."""
    try:
        document_value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _reject_contract()
    document = _require_mapping(document_value)
    _require_exact_keys(document, _TOP_LEVEL_KEYS)
    if (
        document["contract"] != _CONTRACT_NAME
        or document["version"] != ALIYUN_IMS_EDITING_STAGING_CONTRACT_VERSION
    ):
        _reject_contract()
    verified_at = _require_str(document["verified_at"])
    if _VERIFIED_AT_PATTERN.fullmatch(verified_at) is None:
        _reject_contract()

    policy = _require_mapping(document["policy"])
    _require_exact_keys(
        policy,
        frozenset(
            {
                "long_lived_access_keys_only_in_rust_private_store",
                "secrets_never_in_react_logs_or_errors",
                "no_real_cloud_calls_without_user_credentials",
                "fail_closed",
            }
        ),
    )
    for flag in policy.values():
        _require_true(flag)

    _validate_sources(document["sources"])

    service = _require_mapping(document["service"])
    _require_exact_keys(
        service,
        frozenset(
            {
                "provider",
                "api_version",
                "endpoint_template",
                "signature_algorithm",
                "connection_test_action",
            }
        ),
    )
    if (
        service["provider"] != "aliyun_ims"
        or service["api_version"] != "2020-11-09"
        or service["endpoint_template"] != "ice.{region}.aliyuncs.com"
        or service["signature_algorithm"] != "ACS3-HMAC-SHA256"
        or service["connection_test_action"] != "ListMediaBasicInfos"
    ):
        _reject_contract()

    endpoints, labels, groups = _parse_regions(document["regions"])

    same_region = _require_mapping(document["same_region_rule"])
    _require_exact_keys(
        same_region,
        frozenset(
            {
                "input_output_oss_must_match_service_region",
                "cross_region_materials_supported",
                "external_or_cdn_material_urls_supported",
            }
        ),
    )
    _require_true(same_region["input_output_oss_must_match_service_region"])
    _require_false(same_region["cross_region_materials_supported"])
    _require_false(same_region["external_or_cdn_material_urls_supported"])

    _validate_ram(document["ram"])

    prefix, retention_days, max_object_bytes, max_assets, extensions = _parse_staging(
        document["staging"]
    )
    tiers = _parse_billing(document["billing"])

    pending = document["pending_credential_verification"]
    if not isinstance(pending, list) or not pending:
        _reject_contract()
    for entry in pending:
        _require_str(entry)

    return AliyunImsEditingStagingContract(
        verified_at=verified_at,
        api_version="2020-11-09",
        signature_algorithm="ACS3-HMAC-SHA256",
        connection_test_action="ListMediaBasicInfos",
        endpoints=MappingProxyType(endpoints),
        region_labels=MappingProxyType(labels),
        region_price_groups=MappingProxyType(groups),
        object_key_prefix=prefix,
        retention_days=retention_days,
        max_object_bytes=max_object_bytes,
        max_assets_per_plan=max_assets,
        allowed_extensions=extensions,
        billing_tiers=tiers,
    )


def build_media_staging_plan(
    *,
    contract: AliyunImsEditingStagingContract,
    service_region: AliyunImsRegion,
    bucket_region: AliyunImsRegion,
    assets: tuple[StagingAsset, ...],
) -> MediaStagingPlan:
    """Deduplicate assets by digest into a same-region staging plan."""
    if (
        not isinstance(contract, AliyunImsEditingStagingContract)
        or not isinstance(service_region, AliyunImsRegion)
        or not isinstance(bucket_region, AliyunImsRegion)
        or type(assets) is not tuple
        or not 1 <= len(assets) <= contract.max_assets_per_plan
        or any(not isinstance(asset, StagingAsset) for asset in assets)
    ):
        _reject()
    if bucket_region is not service_region:
        _reject()

    objects_by_digest: dict[str, StagingObject] = {}
    keys_by_logical_id: dict[str, str] = {}
    for asset in assets:
        if (
            asset.logical_id in keys_by_logical_id
            or asset.extension not in contract.allowed_extensions
            or asset.size_bytes > contract.max_object_bytes
        ):
            _reject()
        existing = objects_by_digest.get(asset.sha256_hex)
        if existing is None:
            staging_object = StagingObject(
                sha256_hex=asset.sha256_hex,
                extension=asset.extension,
                size_bytes=asset.size_bytes,
                object_key=f"{contract.object_key_prefix}{asset.sha256_hex}{asset.extension}",
                retention_days=contract.retention_days,
            )
            objects_by_digest[asset.sha256_hex] = staging_object
        elif existing.extension != asset.extension or existing.size_bytes != asset.size_bytes:
            _reject()
        keys_by_logical_id[asset.logical_id] = objects_by_digest[asset.sha256_hex].object_key

    return MediaStagingPlan(
        region=service_region,
        objects=tuple(objects_by_digest.values()),
        _keys_by_logical_id=MappingProxyType(keys_by_logical_id),
    )


def estimate_editing_cost(
    *,
    contract: AliyunImsEditingStagingContract,
    region: AliyunImsRegion,
    output_duration_ms: int,
    output_height: int,
) -> EditingCostEstimate:
    """Estimate the media-producing charge for one intended output."""
    if (
        not isinstance(contract, AliyunImsEditingStagingContract)
        or not isinstance(region, AliyunImsRegion)
        or type(output_duration_ms) is not int
        or not 1 <= output_duration_ms <= MAX_EDITING_OUTPUT_DURATION_MS
        or type(output_height) is not int
        or not _MIN_OUTPUT_HEIGHT <= output_height <= contract.billing_tiers[-1].max_pixel_height
    ):
        _reject()
    tier = next(
        candidate
        for candidate in contract.billing_tiers
        if output_height <= candidate.max_pixel_height
    )
    billed_minutes = -(-output_duration_ms // _MS_PER_MINUTE)
    unit_price = tier.price_per_minute[contract.region_price_groups[region]]
    return EditingCostEstimate(
        region=region,
        billed_minutes=billed_minutes,
        tier_id=tier.tier_id,
        unit_price_cny=unit_price,
        estimated_total_cny=unit_price * billed_minutes,
        currency="CNY",
    )


__all__ = [
    "ALIYUN_IMS_EDITING_STAGING_CONTRACT_VERSION",
    "MAX_EDITING_OUTPUT_DURATION_MS",
    "MAX_STAGING_OBJECT_BYTES",
    "MAX_STAGING_RETENTION_DAYS",
    "AliyunImsEditingStagingContract",
    "AliyunImsRegion",
    "EditingBillingTier",
    "EditingCostEstimate",
    "EditingServicePreflight",
    "InvalidAliyunImsEditingStagingContract",
    "InvalidAliyunImsEditingStagingModel",
    "MediaStagingPlan",
    "PreflightCheckStatus",
    "RegionPriceGroup",
    "StagingAsset",
    "StagingObject",
    "build_media_staging_plan",
    "estimate_editing_cost",
    "load_aliyun_ims_editing_staging_contract",
]
