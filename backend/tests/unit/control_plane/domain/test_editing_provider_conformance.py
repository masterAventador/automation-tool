"""VE-08: one conformance suite proves every editing provider interchangeable.

The same provider-neutral suite runs against the production Aliyun adapter
(deterministic fake transports) and against a fake second provider with a
deliberately different capability matrix and internal execution model. The
suite only speaks the `VideoEditingProvider` protocol — it imports nothing
vendor-specific — which is the executable proof that a future Tencent adapter
plugs in without touching the domain layer or the pages.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import final
from uuid import UUID

import pytest

from automation_tool.control_plane.domain.aliyun_ims_editing_provider import (
    AliyunEditingOutputConfig,
    AliyunImsEditingProvider,
    AliyunOssBucketName,
    AliyunSubmitAcknowledgement,
    AliyunSubmitMediaProducingRequest,
    InMemoryAliyunEditingIntentStore,
)
from automation_tool.control_plane.domain.aliyun_ims_editing_reconciliation import (
    AliyunEditingReconciliationPolicy,
    AliyunGetMediaProducingRequest,
    AliyunImsEditingReconciler,
    AliyunMediaProducingJobReport,
    NewArtifactAliyunEditingOutputRegistrar,
)
from automation_tool.control_plane.domain.aliyun_ims_editing_staging import (
    AliyunImsEditingStagingContract,
    AliyunImsRegion,
    EditingServicePreflight,
    MediaStagingPlan,
    PreflightCheckStatus,
    StagingAsset,
    build_media_staging_plan,
    load_aliyun_ims_editing_staging_contract,
)
from automation_tool.control_plane.domain.fake_second_editing_provider import (
    FAKE_SECOND_EDITING_PROVIDER_ID,
    FakeSecondEditingProvider,
)
from automation_tool.control_plane.domain.resource_ids import ArtifactId
from automation_tool.control_plane.domain.video_creation import (
    TimelineClip,
    TimelineId,
    TimelineTrack,
    TimelineTrackKind,
    TimelineTransition,
    TransitionKind,
)
from automation_tool.control_plane.domain.video_editing import (
    EditingJobId,
    EditingProjectId,
    EditingTimeline,
)
from automation_tool.control_plane.domain.video_editing_provider import (
    EditingProviderCapabilities,
    EditingProviderId,
    EditingProviderJobSnapshot,
    EditingSubmission,
    VideoEditingProviderRegistry,
    editing_submission_idempotency_key,
)
from automation_tool.control_plane.domain.video_editing_provider_conformance import (
    EditingProviderConformanceScenario,
    EditingProviderConformanceViolation,
    run_editing_provider_conformance,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts/video/aliyun-ims-editing-staging.v1.json"

REGION = AliyunImsRegion.CN_BEIJING
BUCKET = AliyunOssBucketName("automation-tool-video-staging")
INPUT_ARTIFACT = ArtifactId(UUID("00000000-0000-4000-8000-0000000000a8"))
CREATED_AT = datetime(2026, 7, 23, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def contract() -> AliyunImsEditingStagingContract:
    return load_aliyun_ims_editing_staging_contract(CONTRACT_PATH)


def _timeline(
    *,
    project_id: EditingProjectId,
    transition: TransitionKind | None = None,
    duration_ms: int = 4_000,
) -> EditingTimeline:
    clips = [
        TimelineClip(
            clip_id="clip-1",
            start_ms=0,
            duration_ms=2_000,
            source_artifact_id=INPUT_ARTIFACT,
            text=None,
            transition_in=None,
        ),
        TimelineClip(
            clip_id="clip-2",
            start_ms=2_000,
            duration_ms=2_000,
            source_artifact_id=INPUT_ARTIFACT,
            text=None,
            transition_in=None
            if transition is None
            else TimelineTransition(kind=transition, duration_ms=500),
        ),
    ]
    return EditingTimeline(
        timeline_id=TimelineId.new(),
        project_id=project_id,
        revision=1,
        duration_ms=duration_ms,
        tracks=(
            TimelineTrack(
                track_id="visual-main",
                kind=TimelineTrackKind.VISUAL,
                clips=tuple(clips),
            ),
        ),
        created_at=CREATED_AT,
    )


def _submission(timeline: EditingTimeline) -> EditingSubmission:
    editing_job_id = EditingJobId.new()
    return EditingSubmission(
        editing_job_id=editing_job_id,
        project_id=timeline.project_id,
        timeline=timeline,
        idempotency_key=editing_submission_idempotency_key(editing_job_id),
    )


@final
class _FakeSubmitTransport:
    def __init__(self) -> None:
        self.dispatch_count = 0

    async def submit_media_producing_job(
        self, request: AliyunSubmitMediaProducingRequest
    ) -> AliyunSubmitAcknowledgement:
        self.dispatch_count += 1
        return AliyunSubmitAcknowledgement(
            vendor_job_id=f"conformance-{self.dispatch_count:04d}-0000",
            request_id="request-0000-0000",
        )


@final
class _AlwaysSuccessQueryTransport:
    async def get_media_producing_job(
        self, request: AliyunGetMediaProducingRequest
    ) -> AliyunMediaProducingJobReport:
        return AliyunMediaProducingJobReport(
            vendor_job_id=request.vendor_job_id, status_token="Success"
        )


@final
class _StaticPreflightSource:
    def __init__(self, region: AliyunImsRegion) -> None:
        self._preflight = EditingServicePreflight(
            region=region,
            region_check=PreflightCheckStatus.PASSED,
            permission_check=PreflightCheckStatus.PASSED,
            quota_check=PreflightCheckStatus.PASSED,
        )

    async def current(self) -> EditingServicePreflight:
        return self._preflight


@final
class _DigestPlanner:
    def __init__(self, contract: AliyunImsEditingStagingContract) -> None:
        self._contract = contract

    async def plan(self, timeline: EditingTimeline) -> MediaStagingPlan:
        return build_media_staging_plan(
            contract=self._contract,
            service_region=REGION,
            bucket_region=REGION,
            assets=(
                StagingAsset(
                    logical_id=str(INPUT_ARTIFACT),
                    sha256_hex="ab" * 32,
                    size_bytes=1024,
                    extension=".mp4",
                ),
            ),
        )


def _aliyun_scenario(
    contract: AliyunImsEditingStagingContract,
) -> EditingProviderConformanceScenario:
    project_id = EditingProjectId.new()
    intent_store = InMemoryAliyunEditingIntentStore()
    provider = AliyunImsEditingProvider(
        contract=contract,
        region=REGION,
        staging_bucket=BUCKET,
        output=AliyunEditingOutputConfig(width=1280, height=720),
        preflight_source=_StaticPreflightSource(REGION),
        staging_planner=_DigestPlanner(contract),
        transport=_FakeSubmitTransport(),
        intent_store=intent_store,
    )
    reconciler = AliyunImsEditingReconciler(
        provider=provider,
        intent_store=intent_store,
        transport=_AlwaysSuccessQueryTransport(),
        contract=contract,
        region=REGION,
        registrar=NewArtifactAliyunEditingOutputRegistrar(),
        policy=AliyunEditingReconciliationPolicy(
            max_polls=3, transient_failure_limit=2, poll_interval_seconds=0.0
        ),
    )

    async def _drive_to_success(editing_job_id: EditingJobId) -> None:
        await reconciler.reconcile_until_terminal(editing_job_id)

    supported = _submission(_timeline(project_id=project_id, transition=TransitionKind.FADE))
    return EditingProviderConformanceScenario(
        provider=provider,
        supported_submission=supported,
        unsupported_timeline=_timeline(
            project_id=project_id, transition=TransitionKind.DISSOLVE
        ),
        conflicting_timeline=_timeline(project_id=project_id, duration_ms=6_000),
        drive_to_success=_drive_to_success,
    )


def _fake_second_scenario() -> EditingProviderConformanceScenario:
    project_id = EditingProjectId.new()
    provider = FakeSecondEditingProvider()

    async def _drive_to_success(editing_job_id: EditingJobId) -> None:
        await provider.complete_job(editing_job_id)

    supported = _submission(
        _timeline(project_id=project_id, transition=TransitionKind.DISSOLVE)
    )
    return EditingProviderConformanceScenario(
        provider=provider,
        supported_submission=supported,
        unsupported_timeline=_timeline(project_id=project_id, transition=TransitionKind.WIPE),
        conflicting_timeline=_timeline(project_id=project_id, duration_ms=6_000),
        drive_to_success=_drive_to_success,
    )


class TestConformanceSuiteAcrossProviders:
    @pytest.mark.asyncio
    async def test_aliyun_adapter_passes_the_shared_suite(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        report = await run_editing_provider_conformance(_aliyun_scenario(contract))
        assert report.provider_id == EditingProviderId("aliyun_ims")
        assert len(report.passed_checks) >= 10

    @pytest.mark.asyncio
    async def test_fake_second_provider_passes_the_same_suite(self) -> None:
        report = await run_editing_provider_conformance(_fake_second_scenario())
        assert report.provider_id == FAKE_SECOND_EDITING_PROVIDER_ID
        assert len(report.passed_checks) >= 10

    @pytest.mark.asyncio
    async def test_both_reports_cover_the_identical_check_list(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        aliyun = await run_editing_provider_conformance(_aliyun_scenario(contract))
        fake = await run_editing_provider_conformance(_fake_second_scenario())
        assert aliyun.passed_checks == fake.passed_checks

    @pytest.mark.asyncio
    async def test_violating_provider_is_reported_not_masked(self) -> None:
        broken = _fake_second_scenario()

        @final
        class _LyingProvider:
            """Delegates everything but forges a wrong provider id on get."""

            def __init__(self, inner: FakeSecondEditingProvider) -> None:
                self._inner = inner

            async def capabilities(self) -> EditingProviderCapabilities:
                return await self._inner.capabilities()

            async def validate(self, timeline: EditingTimeline) -> None:
                await self._inner.validate(timeline)

            async def submit(
                self, submission: EditingSubmission
            ) -> EditingProviderJobSnapshot:
                return await self._inner.submit(submission)

            async def get(self, editing_job_id: EditingJobId) -> EditingProviderJobSnapshot:
                snapshot = await self._inner.get(editing_job_id)
                return EditingProviderJobSnapshot(
                    provider_id=EditingProviderId("mismatched_vendor"),
                    editing_job_id=snapshot.editing_job_id,
                    status=snapshot.status,
                    failure_code=snapshot.failure_code,
                    output_artifact_ids=snapshot.output_artifact_ids,
                )

            async def cancel(
                self, editing_job_id: EditingJobId
            ) -> EditingProviderJobSnapshot:
                return await self._inner.cancel(editing_job_id)

            async def fetch_artifacts(
                self, editing_job_id: EditingJobId
            ) -> tuple[ArtifactId, ...]:
                return await self._inner.fetch_artifacts(editing_job_id)

        assert isinstance(broken.provider, FakeSecondEditingProvider)
        lying = EditingProviderConformanceScenario(
            provider=_LyingProvider(broken.provider),
            supported_submission=broken.supported_submission,
            unsupported_timeline=broken.unsupported_timeline,
            conflicting_timeline=broken.conflicting_timeline,
            drive_to_success=broken.drive_to_success,
        )
        with pytest.raises(EditingProviderConformanceViolation):
            await run_editing_provider_conformance(lying)


class TestRegistryReplaceability:
    @pytest.mark.asyncio
    async def test_both_providers_register_and_resolve_through_one_registry(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        registry = VideoEditingProviderRegistry()
        aliyun = _aliyun_scenario(contract).provider
        fake = FakeSecondEditingProvider()
        registry.register(EditingProviderId("aliyun_ims"), aliyun)
        registry.register(FAKE_SECOND_EDITING_PROVIDER_ID, fake)
        assert registry.registered_provider_ids() == (
            EditingProviderId("aliyun_ims"),
            FAKE_SECOND_EDITING_PROVIDER_ID,
        )
        assert registry.resolve(FAKE_SECOND_EDITING_PROVIDER_ID) is fake

    def test_fake_second_provider_imports_no_vendor_modules(self) -> None:
        import automation_tool.control_plane.domain.fake_second_editing_provider as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        for forbidden in ("aliyun", "Aliyun", "tencent", "Tencent", "oss", "ims"):
            assert forbidden not in source, forbidden

    def test_conformance_suite_imports_no_vendor_modules(self) -> None:
        import automation_tool.control_plane.domain.video_editing_provider_conformance as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        for forbidden in ("aliyun", "Aliyun", "tencent", "Tencent"):
            assert forbidden not in source, forbidden
