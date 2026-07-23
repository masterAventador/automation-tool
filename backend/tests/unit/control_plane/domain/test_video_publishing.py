import json
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pytest

from automation_tool.control_plane.domain import ArtifactId
from automation_tool.control_plane.domain.video_publishing import (
    FIRST_RELEASE_PUBLISHING_CAPABILITIES,
    PUBLISHING_CAPABILITIES_CONTRACT_VERSION,
    InvalidPublishJobTransition,
    InvalidVideoPublishingModel,
    PublishCapability,
    PublishFailureCode,
    PublishJob,
    PublishJobId,
    PublishJobStateMachine,
    PublishJobStatus,
    PublishMechanism,
    PublishPlatform,
    enabled_publish_platforms,
    publish_capability_for,
)

NOW = datetime(2026, 7, 23, 0, 0, tzinfo=UTC)
SHA256 = "b" * 64
CONTRACT_PATH = (
    Path(__file__).resolve().parents[5] / "contracts/quality/publishing-capabilities.v1.json"
)

ENABLED = {
    PublishPlatform.BILIBILI: PublishMechanism.OFFICIAL_API,
    PublishPlatform.DOUYIN: PublishMechanism.BROWSER_USE,
}
DEFERRED = {
    PublishPlatform.KUAISHOU,
    PublishPlatform.XIAOHONGSHU,
    PublishPlatform.WECHAT_CHANNELS,
}


def _job(
    *,
    platform: PublishPlatform = PublishPlatform.BILIBILI,
    mechanism: PublishMechanism = PublishMechanism.OFFICIAL_API,
    status: PublishJobStatus = PublishJobStatus.DRAFT,
    failure_code: PublishFailureCode | None = None,
    **overrides: object,
) -> PublishJob:
    values: dict[str, object] = {
        "publish_job_id": PublishJobId.new(),
        "platform": platform,
        "mechanism": mechanism,
        "status": status,
        "revision": 1,
        "video_artifact_id": ArtifactId.new(),
        "cover_artifact_id": None,
        "title": "新品三大核心卖点视频",
        "description": "覆盖三个核心卖点的成片",
        "content_sha256": SHA256,
        "failure_code": failure_code,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return PublishJob(**values)  # type: ignore[arg-type]


class TestCapabilityFreeze:
    def test_platform_enum_is_closed_to_five_platforms(self) -> None:
        assert {member.value for member in PublishPlatform} == {
            "bilibili",
            "douyin",
            "kuaishou",
            "xiaohongshu",
            "wechat_channels",
        }

    def test_mechanism_enum_is_closed(self) -> None:
        assert {member.value for member in PublishMechanism} == {
            "official_api",
            "browser_use",
            "deferred",
        }

    def test_registry_covers_every_platform_exactly_once(self) -> None:
        assert set(FIRST_RELEASE_PUBLISHING_CAPABILITIES) == set(PublishPlatform)

    def test_registry_is_immutable(self) -> None:
        with pytest.raises(TypeError):
            FIRST_RELEASE_PUBLISHING_CAPABILITIES[PublishPlatform.KUAISHOU] = (  # type: ignore[index]
                PublishMechanism.BROWSER_USE
            )

    def test_only_bilibili_api_and_douyin_browser_use_are_enabled(self) -> None:
        for platform, mechanism in ENABLED.items():
            assert FIRST_RELEASE_PUBLISHING_CAPABILITIES[platform] is mechanism
        for platform in DEFERRED:
            assert FIRST_RELEASE_PUBLISHING_CAPABILITIES[platform] is PublishMechanism.DEFERRED
        assert enabled_publish_platforms() == frozenset(ENABLED)

    def test_capability_factory_matches_registry(self) -> None:
        for platform in PublishPlatform:
            capability = publish_capability_for(platform)
            assert capability.platform is platform
            assert capability.mechanism is FIRST_RELEASE_PUBLISHING_CAPABILITIES[platform]
            assert capability.enabled is (capability.mechanism is not PublishMechanism.DEFERRED)

    def test_capability_factory_rejects_unknown_platform(self) -> None:
        with pytest.raises(InvalidVideoPublishingModel):
            publish_capability_for("bilibili")

    def test_capability_rejects_unknown_platform_and_mechanism(self) -> None:
        with pytest.raises(InvalidVideoPublishingModel):
            PublishCapability(
                platform="youtube",  # type: ignore[arg-type]
                mechanism=PublishMechanism.OFFICIAL_API,
                enabled=True,
            )
        with pytest.raises(InvalidVideoPublishingModel):
            PublishCapability(
                platform=PublishPlatform.BILIBILI,
                mechanism="playwright",  # type: ignore[arg-type]
                enabled=True,
            )

    def test_deferred_platform_cannot_be_enabled(self) -> None:
        for platform in DEFERRED:
            with pytest.raises(InvalidVideoPublishingModel):
                PublishCapability(
                    platform=platform,
                    mechanism=PublishMechanism.DEFERRED,
                    enabled=True,
                )

    def test_deferred_platform_cannot_switch_to_active_mechanism(self) -> None:
        for platform in DEFERRED:
            for mechanism in (PublishMechanism.OFFICIAL_API, PublishMechanism.BROWSER_USE):
                with pytest.raises(InvalidVideoPublishingModel):
                    PublishCapability(platform=platform, mechanism=mechanism, enabled=True)

    def test_enabled_platform_cannot_switch_mechanism(self) -> None:
        with pytest.raises(InvalidVideoPublishingModel):
            PublishCapability(
                platform=PublishPlatform.BILIBILI,
                mechanism=PublishMechanism.BROWSER_USE,
                enabled=True,
            )
        with pytest.raises(InvalidVideoPublishingModel):
            PublishCapability(
                platform=PublishPlatform.DOUYIN,
                mechanism=PublishMechanism.OFFICIAL_API,
                enabled=True,
            )

    def test_enabled_platform_cannot_be_marked_disabled_or_deferred(self) -> None:
        for platform, mechanism in ENABLED.items():
            with pytest.raises(InvalidVideoPublishingModel):
                PublishCapability(platform=platform, mechanism=mechanism, enabled=False)
            with pytest.raises(InvalidVideoPublishingModel):
                PublishCapability(
                    platform=platform,
                    mechanism=PublishMechanism.DEFERRED,
                    enabled=False,
                )

    def test_capability_is_immutable(self) -> None:
        capability = publish_capability_for(PublishPlatform.BILIBILI)
        with pytest.raises(FrozenInstanceError):
            capability.enabled = False  # type: ignore[misc]


class TestContractFile:
    def test_contract_file_matches_domain_registry(self) -> None:
        document = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        assert document["version"] == PUBLISHING_CAPABILITIES_CONTRACT_VERSION
        assert sorted(document["mechanisms"]) == sorted(member.value for member in PublishMechanism)
        assert set(document["platforms"]) == {member.value for member in PublishPlatform}
        for platform in PublishPlatform:
            entry = document["platforms"][platform.value]
            capability = publish_capability_for(platform)
            assert entry["mechanism"] == capability.mechanism.value
            assert entry["enabled"] is capability.enabled


class TestPublishJobStateMachine:
    def test_status_enum_is_closed(self) -> None:
        assert {member.value for member in PublishJobStatus} == {
            "draft",
            "awaiting_approval",
            "approved",
            "dispatching",
            "published",
            "rejected",
            "failed",
            "cancelling",
            "cancelled",
            "outcome_uncertain",
        }

    def test_happy_path_requires_approval_before_dispatch(self) -> None:
        chain = (
            PublishJobStatus.DRAFT,
            PublishJobStatus.AWAITING_APPROVAL,
            PublishJobStatus.APPROVED,
            PublishJobStatus.DISPATCHING,
            PublishJobStatus.PUBLISHED,
        )
        for current, target in pairwise(chain):
            assert PublishJobStateMachine.transition(current, target) is target

    def test_dispatching_is_only_reachable_from_approved(self) -> None:
        for status in PublishJobStatus:
            allowed = status is PublishJobStatus.APPROVED
            assert (
                PublishJobStateMachine.can_transition(status, PublishJobStatus.DISPATCHING)
                is allowed
            )

    def test_may_dispatch_only_after_approval(self) -> None:
        for status in PublishJobStatus:
            assert PublishJobStateMachine.may_dispatch(status) is (
                status is PublishJobStatus.APPROVED
            )
        assert PublishJobStateMachine.may_dispatch("approved") is False

    def test_pre_dispatch_statuses_carry_no_external_side_effect(self) -> None:
        assert PublishJobStateMachine.pre_dispatch_statuses() == frozenset(
            {
                PublishJobStatus.DRAFT,
                PublishJobStatus.AWAITING_APPROVAL,
                PublishJobStatus.APPROVED,
            }
        )

    def test_terminal_statuses_accept_no_transition(self) -> None:
        terminals = {
            PublishJobStatus.PUBLISHED,
            PublishJobStatus.REJECTED,
            PublishJobStatus.FAILED,
            PublishJobStatus.CANCELLED,
        }
        assert PublishJobStateMachine.terminal_statuses() == frozenset(terminals)
        for terminal in terminals:
            assert PublishJobStateMachine.is_terminal(terminal)
            assert PublishJobStateMachine.allowed_targets(terminal) == frozenset()
            for target in PublishJobStatus:
                with pytest.raises(InvalidPublishJobTransition):
                    PublishJobStateMachine.transition(terminal, target)

    def test_dispatching_can_end_uncertain_but_draft_cannot(self) -> None:
        assert PublishJobStateMachine.can_transition(
            PublishJobStatus.DISPATCHING, PublishJobStatus.OUTCOME_UNCERTAIN
        )
        for status in PublishJobStateMachine.pre_dispatch_statuses():
            assert not PublishJobStateMachine.can_transition(
                status, PublishJobStatus.OUTCOME_UNCERTAIN
            )

    def test_uncertain_outcome_is_resolvable_by_reconciliation_only(self) -> None:
        assert PublishJobStateMachine.is_terminal(PublishJobStatus.OUTCOME_UNCERTAIN) is False
        assert PublishJobStateMachine.allowed_targets(
            PublishJobStatus.OUTCOME_UNCERTAIN
        ) == frozenset(
            {
                PublishJobStatus.PUBLISHED,
                PublishJobStatus.REJECTED,
                PublishJobStatus.FAILED,
            }
        )
        for target in (
            PublishJobStatus.DRAFT,
            PublishJobStatus.AWAITING_APPROVAL,
            PublishJobStatus.APPROVED,
            PublishJobStatus.DISPATCHING,
            PublishJobStatus.CANCELLING,
            PublishJobStatus.CANCELLED,
            PublishJobStatus.OUTCOME_UNCERTAIN,
        ):
            with pytest.raises(InvalidPublishJobTransition):
                PublishJobStateMachine.transition(PublishJobStatus.OUTCOME_UNCERTAIN, target)

    def test_reconciliation_targets_are_legal_from_both_ambiguous_states(self) -> None:
        for origin in (PublishJobStatus.DISPATCHING, PublishJobStatus.OUTCOME_UNCERTAIN):
            for target in (
                PublishJobStatus.PUBLISHED,
                PublishJobStatus.REJECTED,
                PublishJobStatus.FAILED,
            ):
                assert PublishJobStateMachine.transition(origin, target) is target

    def test_cancel_is_cooperative(self) -> None:
        for status in (
            PublishJobStatus.DRAFT,
            PublishJobStatus.AWAITING_APPROVAL,
            PublishJobStatus.APPROVED,
            PublishJobStatus.DISPATCHING,
        ):
            assert PublishJobStateMachine.can_transition(status, PublishJobStatus.CANCELLING)
        assert PublishJobStateMachine.allowed_targets(PublishJobStatus.CANCELLING) == frozenset(
            {
                PublishJobStatus.CANCELLED,
                PublishJobStatus.PUBLISHED,
                PublishJobStatus.REJECTED,
                PublishJobStatus.FAILED,
                PublishJobStatus.OUTCOME_UNCERTAIN,
            }
        )

    def test_rework_returns_to_draft_without_side_effects(self) -> None:
        assert PublishJobStateMachine.can_transition(
            PublishJobStatus.AWAITING_APPROVAL, PublishJobStatus.DRAFT
        )
        assert not PublishJobStateMachine.can_transition(
            PublishJobStatus.DISPATCHING, PublishJobStatus.DRAFT
        )

    def test_non_status_values_are_rejected(self) -> None:
        with pytest.raises(InvalidPublishJobTransition):
            PublishJobStateMachine.transition("draft", PublishJobStatus.AWAITING_APPROVAL)
        with pytest.raises(InvalidPublishJobTransition):
            PublishJobStateMachine.allowed_targets("draft")
        assert PublishJobStateMachine.is_terminal("published") is False
        assert (
            PublishJobStateMachine.can_transition(PublishJobStatus.DRAFT, "awaiting_approval")
            is False
        )


class TestPublishJob:
    def test_valid_jobs_for_both_enabled_platforms(self) -> None:
        bilibili = _job()
        assert bilibili.platform is PublishPlatform.BILIBILI
        douyin = _job(
            platform=PublishPlatform.DOUYIN,
            mechanism=PublishMechanism.BROWSER_USE,
        )
        assert douyin.mechanism is PublishMechanism.BROWSER_USE

    def test_field_names_are_frozen(self) -> None:
        assert tuple(field.name for field in fields(PublishJob)) == (
            "publish_job_id",
            "platform",
            "mechanism",
            "status",
            "revision",
            "video_artifact_id",
            "cover_artifact_id",
            "title",
            "description",
            "content_sha256",
            "failure_code",
            "created_at",
            "updated_at",
        )

    def test_job_is_immutable(self) -> None:
        job = _job()
        with pytest.raises(FrozenInstanceError):
            job.status = PublishJobStatus.PUBLISHED  # type: ignore[misc]

    def test_deferred_platforms_cannot_construct_any_job(self) -> None:
        for platform in DEFERRED:
            for mechanism in PublishMechanism:
                with pytest.raises(InvalidVideoPublishingModel):
                    _job(platform=platform, mechanism=mechanism)

    def test_enabled_platform_with_wrong_mechanism_is_rejected(self) -> None:
        with pytest.raises(InvalidVideoPublishingModel):
            _job(platform=PublishPlatform.BILIBILI, mechanism=PublishMechanism.BROWSER_USE)
        with pytest.raises(InvalidVideoPublishingModel):
            _job(platform=PublishPlatform.DOUYIN, mechanism=PublishMechanism.OFFICIAL_API)
        with pytest.raises(InvalidVideoPublishingModel):
            _job(platform=PublishPlatform.BILIBILI, mechanism=PublishMechanism.DEFERRED)

    def test_identifier_and_artifact_types_are_enforced(self) -> None:
        with pytest.raises(InvalidVideoPublishingModel):
            _job(publish_job_id=str(PublishJobId.new()))
        with pytest.raises(InvalidVideoPublishingModel):
            _job(video_artifact_id=str(ArtifactId.new()))
        with pytest.raises(InvalidVideoPublishingModel):
            _job(cover_artifact_id="cover")

    def test_cover_must_differ_from_video(self) -> None:
        video = ArtifactId.new()
        with pytest.raises(InvalidVideoPublishingModel):
            _job(video_artifact_id=video, cover_artifact_id=video)

    def test_text_and_hash_validation(self) -> None:
        with pytest.raises(InvalidVideoPublishingModel):
            _job(title="")
        with pytest.raises(InvalidVideoPublishingModel):
            _job(title=" 前后空白 ")
        with pytest.raises(InvalidVideoPublishingModel):
            _job(title="a" * 201)
        with pytest.raises(InvalidVideoPublishingModel):
            _job(description="b" * 4001)
        with pytest.raises(InvalidVideoPublishingModel):
            _job(title="标题\x00隐藏控制字符")
        assert _job(description="第一行\n第二行").description == "第一行\n第二行"
        with pytest.raises(InvalidVideoPublishingModel):
            _job(content_sha256=SHA256.upper())
        with pytest.raises(InvalidVideoPublishingModel):
            _job(content_sha256="deadbeef")

    def test_revision_and_time_validation(self) -> None:
        with pytest.raises(InvalidVideoPublishingModel):
            _job(revision=0)
        with pytest.raises(InvalidVideoPublishingModel):
            _job(revision=True)
        with pytest.raises(InvalidVideoPublishingModel):
            _job(created_at=NOW.replace(tzinfo=None))
        with pytest.raises(InvalidVideoPublishingModel):
            _job(updated_at=NOW - timedelta(seconds=1))

    def test_failure_code_matches_failed_status_only(self) -> None:
        failed = _job(
            status=PublishJobStatus.FAILED,
            failure_code=PublishFailureCode.PLATFORM_ERROR,
        )
        assert failed.failure_code is PublishFailureCode.PLATFORM_ERROR
        with pytest.raises(InvalidVideoPublishingModel):
            _job(status=PublishJobStatus.FAILED, failure_code=None)
        with pytest.raises(InvalidVideoPublishingModel):
            _job(
                status=PublishJobStatus.PUBLISHED,
                failure_code=PublishFailureCode.PLATFORM_ERROR,
            )
        with pytest.raises(InvalidVideoPublishingModel):
            _job(status=PublishJobStatus.FAILED, failure_code="platform_error")  # type: ignore[arg-type]

    def test_optional_description_and_cover_are_allowed(self) -> None:
        job = _job(description=None, cover_artifact_id=ArtifactId.new())
        assert job.description is None
        assert job.cover_artifact_id is not None
