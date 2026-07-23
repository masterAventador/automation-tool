"""VE-06: polling reconciliation, cancellation and recovery for Aliyun editing jobs."""

from pathlib import Path
from typing import final
from uuid import UUID

import pytest

from automation_tool.control_plane.domain.aliyun_ims_editing_provider import (
    AliyunEditingIntent,
    AliyunEditingIntentState,
    AliyunEditingOutputConfig,
    AliyunImsEditingProvider,
    AliyunImsTransportFailure,
    AliyunImsTransportFailureKind,
    AliyunOssBucketName,
    AliyunSubmitAcknowledgement,
    AliyunSubmitMediaProducingRequest,
    InMemoryAliyunEditingIntentStore,
)
from automation_tool.control_plane.domain.aliyun_ims_editing_reconciliation import (
    QUERY_ACTION,
    AliyunEditingReconciliationPolicy,
    AliyunGetMediaProducingRequest,
    AliyunImsEditingReconciler,
    AliyunImsJobNotFound,
    AliyunMediaProducingJobReport,
    AliyunMediaProducingStatus,
    InvalidAliyunImsEditingReconciliationModel,
    build_get_media_producing_request,
)
from automation_tool.control_plane.domain.aliyun_ims_editing_staging import (
    AliyunImsEditingStagingContract,
    AliyunImsRegion,
    EditingServicePreflight,
    MediaStagingPlan,
    load_aliyun_ims_editing_staging_contract,
)
from automation_tool.control_plane.domain.resource_ids import ArtifactId
from automation_tool.control_plane.domain.video_editing import (
    EditingFailureCode,
    EditingJobId,
    EditingJobStatus,
    EditingTimeline,
)
from automation_tool.control_plane.domain.video_editing_provider import (
    EditingProviderErrorCode,
    EditingProviderFailure,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts/video/aliyun-ims-editing-staging.v1.json"

REGION = AliyunImsRegion.CN_BEIJING
BUCKET = AliyunOssBucketName("automation-tool-video-staging")
JOB_ID = EditingJobId(UUID("00000000-0000-4000-8000-0000000000cc"))
VENDOR_JOB_ID = "46c446e2420348e0950e4d7876acc6fb"
REQUEST_HASH = "ab" * 32
OUTPUT_ARTIFACT = ArtifactId(UUID("00000000-0000-4000-8000-000000000009"))


@pytest.fixture(scope="module")
def contract() -> AliyunImsEditingStagingContract:
    return load_aliyun_ims_editing_staging_contract(CONTRACT_PATH)


def _dispatched_intent(
    *,
    status: EditingJobStatus = EditingJobStatus.QUEUED,
    failure_code: EditingFailureCode | None = None,
    output_artifact_ids: tuple[ArtifactId, ...] = (),
) -> AliyunEditingIntent:
    return AliyunEditingIntent(
        editing_job_id=JOB_ID,
        request_hash=REQUEST_HASH,
        state=AliyunEditingIntentState.DISPATCHED,
        vendor_job_id=VENDOR_JOB_ID,
        status=status,
        failure_code=failure_code,
        output_artifact_ids=output_artifact_ids,
    )


def _prepared_intent() -> AliyunEditingIntent:
    return AliyunEditingIntent(
        editing_job_id=JOB_ID,
        request_hash=REQUEST_HASH,
        state=AliyunEditingIntentState.PREPARED,
        vendor_job_id=None,
        status=EditingJobStatus.QUEUED,
        failure_code=None,
        output_artifact_ids=(),
    )


def _uncertain_intent() -> AliyunEditingIntent:
    return AliyunEditingIntent(
        editing_job_id=JOB_ID,
        request_hash=REQUEST_HASH,
        state=AliyunEditingIntentState.UNCERTAIN,
        vendor_job_id=None,
        status=EditingJobStatus.OUTCOME_UNCERTAIN,
        failure_code=None,
        output_artifact_ids=(),
    )


@final
class _ScriptedQueryTransport:
    """Replays a scripted sequence of reports or failures for each query."""

    def __init__(self, outcomes: list[object]) -> None:
        self.requests: list[AliyunGetMediaProducingRequest] = []
        self._outcomes = outcomes

    async def get_media_producing_job(
        self, request: AliyunGetMediaProducingRequest
    ) -> AliyunMediaProducingJobReport:
        self.requests.append(request)
        if not self._outcomes:
            raise AssertionError("query transport exhausted")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, str):
            return AliyunMediaProducingJobReport(
                vendor_job_id=request.vendor_job_id, status_token=outcome
            )
        assert isinstance(outcome, AliyunMediaProducingJobReport)
        return outcome


@final
class _CountingRegistrar:
    def __init__(self) -> None:
        self.calls: list[EditingJobId] = []

    async def register_confirmed_output(
        self, editing_job_id: EditingJobId
    ) -> tuple[ArtifactId, ...]:
        self.calls.append(editing_job_id)
        return (OUTPUT_ARTIFACT,)


@final
class _NoSleep:
    def __init__(self) -> None:
        self.intervals: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.intervals.append(seconds)


@final
class _UnusedPreflightSource:
    async def current(self) -> EditingServicePreflight:
        raise AssertionError("preflight must not run during reconciliation")


@final
class _UnusedPlanner:
    async def plan(self, timeline: EditingTimeline) -> MediaStagingPlan:
        raise AssertionError("staging planner must not run during reconciliation")


@final
class _UnusedSubmitTransport:
    async def submit_media_producing_job(
        self, request: AliyunSubmitMediaProducingRequest
    ) -> AliyunSubmitAcknowledgement:
        raise AssertionError("submit transport must not run during reconciliation")


def _policy(
    *,
    max_polls: int = 5,
    transient_failure_limit: int = 3,
    poll_interval_seconds: float = 0.0,
) -> AliyunEditingReconciliationPolicy:
    return AliyunEditingReconciliationPolicy(
        max_polls=max_polls,
        transient_failure_limit=transient_failure_limit,
        poll_interval_seconds=poll_interval_seconds,
    )


async def _reconciler(
    contract: AliyunImsEditingStagingContract,
    *,
    intents: list[AliyunEditingIntent],
    outcomes: list[object],
    policy: AliyunEditingReconciliationPolicy | None = None,
) -> tuple[
    AliyunImsEditingReconciler,
    _ScriptedQueryTransport,
    InMemoryAliyunEditingIntentStore,
    _CountingRegistrar,
    _NoSleep,
]:
    store = InMemoryAliyunEditingIntentStore()
    for intent in intents:
        await store.save(intent)
    provider = AliyunImsEditingProvider(
        contract=contract,
        region=REGION,
        staging_bucket=BUCKET,
        output=AliyunEditingOutputConfig(width=1080, height=1920),
        preflight_source=_UnusedPreflightSource(),
        staging_planner=_UnusedPlanner(),
        transport=_UnusedSubmitTransport(),
        intent_store=store,
    )
    transport = _ScriptedQueryTransport(outcomes)
    registrar = _CountingRegistrar()
    sleep = _NoSleep()
    reconciler = AliyunImsEditingReconciler(
        provider=provider,
        intent_store=store,
        transport=transport,
        contract=contract,
        region=REGION,
        registrar=registrar,
        policy=policy if policy is not None else _policy(),
        sleep=sleep,
    )
    return reconciler, transport, store, registrar, sleep


class TestGetMediaProducingRequest:
    def test_build_request_targets_official_query_action(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        request = build_get_media_producing_request(
            contract=contract, region=REGION, vendor_job_id=VENDOR_JOB_ID
        )
        assert request.endpoint == "ice.cn-beijing.aliyuncs.com"
        assert request.api_version == "2020-11-09"
        assert request.action == QUERY_ACTION == "GetMediaProducingJob"
        assert dict(request.query_parameters()) == {"JobId": VENDOR_JOB_ID}

    @pytest.mark.parametrize("vendor_job_id", ["", "short", "bad job id", "字", "a" * 129])
    def test_invalid_vendor_job_id_is_rejected(
        self, contract: AliyunImsEditingStagingContract, vendor_job_id: str
    ) -> None:
        with pytest.raises(InvalidAliyunImsEditingReconciliationModel):
            build_get_media_producing_request(
                contract=contract, region=REGION, vendor_job_id=vendor_job_id
            )

    def test_endpoint_must_match_region(self) -> None:
        with pytest.raises(InvalidAliyunImsEditingReconciliationModel):
            AliyunGetMediaProducingRequest(
                endpoint="ice.cn-shanghai.aliyuncs.com",
                api_version="2020-11-09",
                action=QUERY_ACTION,
                region=REGION,
                vendor_job_id=VENDOR_JOB_ID,
            )


class TestMediaProducingJobReport:
    @pytest.mark.parametrize(
        ("token", "status"),
        [
            ("Init", AliyunMediaProducingStatus.INIT),
            ("Queuing", AliyunMediaProducingStatus.QUEUING),
            ("Processing", AliyunMediaProducingStatus.PROCESSING),
            ("Success", AliyunMediaProducingStatus.SUCCESS),
            ("Failed", AliyunMediaProducingStatus.FAILED),
        ],
    )
    def test_known_official_status_tokens(
        self, token: str, status: AliyunMediaProducingStatus
    ) -> None:
        report = AliyunMediaProducingJobReport(vendor_job_id=VENDOR_JOB_ID, status_token=token)
        assert report.known_status() is status

    def test_unknown_token_is_carried_without_guessing(self) -> None:
        report = AliyunMediaProducingJobReport(
            vendor_job_id=VENDOR_JOB_ID, status_token="Migrating"
        )
        assert report.known_status() is None

    @pytest.mark.parametrize("token", ["", "has space", "字", "a" * 65, "semi;colon"])
    def test_malformed_status_token_is_rejected(self, token: str) -> None:
        with pytest.raises(InvalidAliyunImsEditingReconciliationModel):
            AliyunMediaProducingJobReport(vendor_job_id=VENDOR_JOB_ID, status_token=token)


class TestReconciliationPolicy:
    def test_accepts_bounded_policy(self) -> None:
        policy = _policy(max_polls=1, transient_failure_limit=1, poll_interval_seconds=0.0)
        assert policy.max_polls == 1

    @pytest.mark.parametrize(
        ("max_polls", "limit", "interval"),
        [(0, 1, 0.0), (1, 0, 0.0), (-1, 1, 0.0), (1, 1, -0.1), (1, 1, 3601.0), (10_001, 1, 0.0)],
    )
    def test_rejects_out_of_bound_policy(
        self, max_polls: int, limit: int, interval: float
    ) -> None:
        with pytest.raises(InvalidAliyunImsEditingReconciliationModel):
            AliyunEditingReconciliationPolicy(
                max_polls=max_polls,
                transient_failure_limit=limit,
                poll_interval_seconds=interval,
            )


@pytest.mark.asyncio
class TestPollOnce:
    async def test_processing_promotes_queued_to_running(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        reconciler, transport, store, _, _ = await _reconciler(
            contract, intents=[_dispatched_intent()], outcomes=["Processing"]
        )
        snapshot = await reconciler.poll_once(JOB_ID)
        assert snapshot.status is EditingJobStatus.RUNNING
        stored = await store.load(JOB_ID)
        assert stored is not None and stored.status is EditingJobStatus.RUNNING
        assert transport.requests[0].vendor_job_id == VENDOR_JOB_ID

    @pytest.mark.parametrize("token", ["Init", "Queuing"])
    async def test_pre_processing_states_keep_queued(
        self, contract: AliyunImsEditingStagingContract, token: str
    ) -> None:
        reconciler, _, store, _, _ = await _reconciler(
            contract, intents=[_dispatched_intent()], outcomes=[token]
        )
        snapshot = await reconciler.poll_once(JOB_ID)
        assert snapshot.status is EditingJobStatus.QUEUED
        stored = await store.load(JOB_ID)
        assert stored is not None and stored.status is EditingJobStatus.QUEUED

    async def test_stale_init_never_regresses_running(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        reconciler, _, store, _, _ = await _reconciler(
            contract,
            intents=[_dispatched_intent(status=EditingJobStatus.RUNNING)],
            outcomes=["Init"],
        )
        snapshot = await reconciler.poll_once(JOB_ID)
        assert snapshot.status is EditingJobStatus.RUNNING
        stored = await store.load(JOB_ID)
        assert stored is not None and stored.status is EditingJobStatus.RUNNING

    async def test_success_from_running_records_confirmed_outputs(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        reconciler, _, store, registrar, _ = await _reconciler(
            contract,
            intents=[_dispatched_intent(status=EditingJobStatus.RUNNING)],
            outcomes=["Success"],
        )
        snapshot = await reconciler.poll_once(JOB_ID)
        assert snapshot.status is EditingJobStatus.SUCCEEDED
        assert snapshot.output_artifact_ids == (OUTPUT_ARTIFACT,)
        assert registrar.calls == [JOB_ID]
        stored = await store.load(JOB_ID)
        assert stored is not None and stored.status is EditingJobStatus.SUCCEEDED

    async def test_success_straight_from_queued_walks_legal_transitions(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        reconciler, _, store, registrar, _ = await _reconciler(
            contract, intents=[_dispatched_intent()], outcomes=["Success"]
        )
        snapshot = await reconciler.poll_once(JOB_ID)
        assert snapshot.status is EditingJobStatus.SUCCEEDED
        assert registrar.calls == [JOB_ID]
        stored = await store.load(JOB_ID)
        assert stored is not None and stored.status is EditingJobStatus.SUCCEEDED

    @pytest.mark.parametrize("token", ["Failed", "Fail"])
    async def test_vendor_failure_becomes_editing_failed(
        self, contract: AliyunImsEditingStagingContract, token: str
    ) -> None:
        reconciler, _, store, registrar, _ = await _reconciler(
            contract, intents=[_dispatched_intent()], outcomes=[token]
        )
        snapshot = await reconciler.poll_once(JOB_ID)
        assert snapshot.status is EditingJobStatus.FAILED
        assert snapshot.failure_code is EditingFailureCode.EDITING_FAILED
        assert registrar.calls == []
        stored = await store.load(JOB_ID)
        assert stored is not None and stored.failure_code is EditingFailureCode.EDITING_FAILED

    async def test_terminal_intent_short_circuits_without_query(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        reconciler, transport, _, registrar, _ = await _reconciler(
            contract,
            intents=[
                _dispatched_intent(
                    status=EditingJobStatus.SUCCEEDED,
                    output_artifact_ids=(OUTPUT_ARTIFACT,),
                )
            ],
            outcomes=[],
        )
        snapshot = await reconciler.poll_once(JOB_ID)
        assert snapshot.status is EditingJobStatus.SUCCEEDED
        assert transport.requests == []
        assert registrar.calls == []

    async def test_duplicate_success_report_is_idempotent(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        reconciler, _, _, registrar, _ = await _reconciler(
            contract,
            intents=[_dispatched_intent(status=EditingJobStatus.RUNNING)],
            outcomes=["Success"],
        )
        first = await reconciler.poll_once(JOB_ID)
        second = await reconciler.poll_once(JOB_ID)
        assert first == second
        assert registrar.calls == [JOB_ID]

    async def test_unknown_status_from_running_settles_outcome_uncertain(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        reconciler, _, store, registrar, _ = await _reconciler(
            contract,
            intents=[_dispatched_intent(status=EditingJobStatus.RUNNING)],
            outcomes=["Migrating"],
        )
        snapshot = await reconciler.poll_once(JOB_ID)
        assert snapshot.status is EditingJobStatus.OUTCOME_UNCERTAIN
        assert registrar.calls == []
        stored = await store.load(JOB_ID)
        assert stored is not None and stored.status is EditingJobStatus.OUTCOME_UNCERTAIN

    async def test_unknown_status_from_queued_settles_outcome_uncertain(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        reconciler, _, store, _, _ = await _reconciler(
            contract, intents=[_dispatched_intent()], outcomes=["Migrating"]
        )
        snapshot = await reconciler.poll_once(JOB_ID)
        assert snapshot.status is EditingJobStatus.OUTCOME_UNCERTAIN
        stored = await store.load(JOB_ID)
        assert stored is not None and stored.status is EditingJobStatus.OUTCOME_UNCERTAIN

    async def test_report_for_wrong_vendor_job_is_provider_error(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        wrong = AliyunMediaProducingJobReport(
            vendor_job_id="ffffffffffffffffffffffffffffffff", status_token="Success"
        )
        reconciler, _, store, registrar, _ = await _reconciler(
            contract, intents=[_dispatched_intent()], outcomes=[wrong]
        )
        with pytest.raises(EditingProviderFailure) as error:
            await reconciler.poll_once(JOB_ID)
        assert error.value.code is EditingProviderErrorCode.PROVIDER_ERROR
        assert registrar.calls == []
        stored = await store.load(JOB_ID)
        assert stored is not None and stored.status is EditingJobStatus.QUEUED

    async def test_unknown_job_is_not_found(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        reconciler, _, _, _, _ = await _reconciler(contract, intents=[], outcomes=[])
        with pytest.raises(EditingProviderFailure) as error:
            await reconciler.poll_once(JOB_ID)
        assert error.value.code is EditingProviderErrorCode.NOT_FOUND

    async def test_prepared_intent_is_not_reconcilable(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        reconciler, transport, _, _, _ = await _reconciler(
            contract, intents=[_prepared_intent()], outcomes=[]
        )
        with pytest.raises(EditingProviderFailure) as error:
            await reconciler.poll_once(JOB_ID)
        assert error.value.code is EditingProviderErrorCode.NOT_FOUND
        assert transport.requests == []

    async def test_uncertain_intent_returns_without_query_or_replay(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        reconciler, transport, _, _, _ = await _reconciler(
            contract, intents=[_uncertain_intent()], outcomes=[]
        )
        snapshot = await reconciler.poll_once(JOB_ID)
        assert snapshot.status is EditingJobStatus.OUTCOME_UNCERTAIN
        assert transport.requests == []

    async def test_job_not_found_from_gateway_propagates(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        reconciler, _, store, _, _ = await _reconciler(
            contract, intents=[_dispatched_intent()], outcomes=[AliyunImsJobNotFound()]
        )
        with pytest.raises(AliyunImsJobNotFound):
            await reconciler.poll_once(JOB_ID)
        stored = await store.load(JOB_ID)
        assert stored is not None and stored.status is EditingJobStatus.QUEUED


@pytest.mark.asyncio
class TestReconcileUntilTerminal:
    async def test_polls_until_confirmed_success(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        policy = _policy(max_polls=5, poll_interval_seconds=1.5)
        reconciler, transport, _, registrar, sleep = await _reconciler(
            contract,
            intents=[_dispatched_intent()],
            outcomes=["Init", "Processing", "Success"],
            policy=policy,
        )
        snapshot = await reconciler.reconcile_until_terminal(JOB_ID)
        assert snapshot.status is EditingJobStatus.SUCCEEDED
        assert len(transport.requests) == 3
        assert registrar.calls == [JOB_ID]
        assert sleep.intervals == [1.5, 1.5]

    async def test_transient_failures_within_limit_keep_polling(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        reconciler, _, _, _, _ = await _reconciler(
            contract,
            intents=[_dispatched_intent()],
            outcomes=[
                AliyunImsTransportFailure(AliyunImsTransportFailureKind.RESPONSE_LOST),
                AliyunImsTransportFailure(AliyunImsTransportFailureKind.REJECTED_THROTTLED),
                "Success",
            ],
            policy=_policy(max_polls=5, transient_failure_limit=3),
        )
        snapshot = await reconciler.reconcile_until_terminal(JOB_ID)
        assert snapshot.status is EditingJobStatus.SUCCEEDED

    async def test_query_failures_beyond_limit_settle_outcome_uncertain(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        reconciler, _, store, _, _ = await _reconciler(
            contract,
            intents=[_dispatched_intent()],
            outcomes=[
                AliyunImsTransportFailure(AliyunImsTransportFailureKind.RESPONSE_LOST),
                AliyunImsJobNotFound(),
            ],
            policy=_policy(max_polls=5, transient_failure_limit=2),
        )
        snapshot = await reconciler.reconcile_until_terminal(JOB_ID)
        assert snapshot.status is EditingJobStatus.OUTCOME_UNCERTAIN
        stored = await store.load(JOB_ID)
        assert stored is not None and stored.status is EditingJobStatus.OUTCOME_UNCERTAIN

    async def test_poll_budget_exhaustion_is_classified_outcome_uncertain(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        reconciler, transport, store, _, _ = await _reconciler(
            contract,
            intents=[_dispatched_intent()],
            outcomes=["Processing", "Processing", "Processing"],
            policy=_policy(max_polls=3),
        )
        snapshot = await reconciler.reconcile_until_terminal(JOB_ID)
        assert snapshot.status is EditingJobStatus.OUTCOME_UNCERTAIN
        assert len(transport.requests) == 3
        stored = await store.load(JOB_ID)
        assert stored is not None and stored.status is EditingJobStatus.OUTCOME_UNCERTAIN

    async def test_already_terminal_job_returns_without_network(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        reconciler, transport, _, _, sleep = await _reconciler(
            contract,
            intents=[
                _dispatched_intent(
                    status=EditingJobStatus.FAILED,
                    failure_code=EditingFailureCode.EDITING_FAILED,
                )
            ],
            outcomes=[],
        )
        snapshot = await reconciler.reconcile_until_terminal(JOB_ID)
        assert snapshot.status is EditingJobStatus.FAILED
        assert transport.requests == []
        assert sleep.intervals == []


@pytest.mark.asyncio
class TestRequestCancel:
    async def test_cancel_marks_cancelling_without_cloud_cancel_call(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        reconciler, transport, store, _, _ = await _reconciler(
            contract, intents=[_dispatched_intent()], outcomes=[]
        )
        snapshot = await reconciler.request_cancel(JOB_ID)
        assert snapshot.status is EditingJobStatus.CANCELLING
        assert transport.requests == []
        stored = await store.load(JOB_ID)
        assert stored is not None and stored.status is EditingJobStatus.CANCELLING

    async def test_cancelled_job_may_still_converge_to_real_success(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        reconciler, _, _, registrar, _ = await _reconciler(
            contract, intents=[_dispatched_intent()], outcomes=["Success"]
        )
        await reconciler.request_cancel(JOB_ID)
        snapshot = await reconciler.reconcile_until_terminal(JOB_ID)
        assert snapshot.status is EditingJobStatus.SUCCEEDED
        assert registrar.calls == [JOB_ID]

    async def test_cancelled_job_may_converge_to_real_failure(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        reconciler, _, _, _, _ = await _reconciler(
            contract, intents=[_dispatched_intent()], outcomes=["Failed"]
        )
        await reconciler.request_cancel(JOB_ID)
        snapshot = await reconciler.reconcile_until_terminal(JOB_ID)
        assert snapshot.status is EditingJobStatus.FAILED

    async def test_cancel_with_exhausted_budget_is_outcome_uncertain(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        reconciler, _, _, _, _ = await _reconciler(
            contract,
            intents=[_dispatched_intent()],
            outcomes=["Processing", "Processing"],
            policy=_policy(max_polls=2),
        )
        await reconciler.request_cancel(JOB_ID)
        snapshot = await reconciler.reconcile_until_terminal(JOB_ID)
        assert snapshot.status is EditingJobStatus.OUTCOME_UNCERTAIN

    async def test_cancel_after_terminal_keeps_result(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        reconciler, _, _, _, _ = await _reconciler(
            contract,
            intents=[
                _dispatched_intent(
                    status=EditingJobStatus.SUCCEEDED,
                    output_artifact_ids=(OUTPUT_ARTIFACT,),
                )
            ],
            outcomes=[],
        )
        snapshot = await reconciler.request_cancel(JOB_ID)
        assert snapshot.status is EditingJobStatus.SUCCEEDED

    async def test_cancel_unknown_job_is_not_found(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        reconciler, _, _, _, _ = await _reconciler(contract, intents=[], outcomes=[])
        with pytest.raises(EditingProviderFailure) as error:
            await reconciler.request_cancel(JOB_ID)
        assert error.value.code is EditingProviderErrorCode.NOT_FOUND


@pytest.mark.asyncio
class TestCrashRecovery:
    async def test_recovery_buckets_every_persisted_intent_state(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        prepared_id = EditingJobId(UUID("00000000-0000-4000-8000-000000000001"))
        resumable_id = EditingJobId(UUID("00000000-0000-4000-8000-000000000002"))
        settled_id = EditingJobId(UUID("00000000-0000-4000-8000-000000000003"))
        uncertain_id = EditingJobId(UUID("00000000-0000-4000-8000-000000000004"))
        intents = [
            AliyunEditingIntent(
                editing_job_id=prepared_id,
                request_hash=REQUEST_HASH,
                state=AliyunEditingIntentState.PREPARED,
                vendor_job_id=None,
                status=EditingJobStatus.QUEUED,
                failure_code=None,
                output_artifact_ids=(),
            ),
            AliyunEditingIntent(
                editing_job_id=resumable_id,
                request_hash=REQUEST_HASH,
                state=AliyunEditingIntentState.DISPATCHED,
                vendor_job_id=VENDOR_JOB_ID,
                status=EditingJobStatus.RUNNING,
                failure_code=None,
                output_artifact_ids=(),
            ),
            AliyunEditingIntent(
                editing_job_id=settled_id,
                request_hash=REQUEST_HASH,
                state=AliyunEditingIntentState.DISPATCHED,
                vendor_job_id="1f470a3a89d94f41a43bc6419ba6a144",
                status=EditingJobStatus.SUCCEEDED,
                failure_code=None,
                output_artifact_ids=(OUTPUT_ARTIFACT,),
            ),
            AliyunEditingIntent(
                editing_job_id=uncertain_id,
                request_hash=REQUEST_HASH,
                state=AliyunEditingIntentState.UNCERTAIN,
                vendor_job_id=None,
                status=EditingJobStatus.OUTCOME_UNCERTAIN,
                failure_code=None,
                output_artifact_ids=(),
            ),
        ]
        reconciler, _, _, _, _ = await _reconciler(contract, intents=intents, outcomes=[])
        report = await reconciler.recover()
        assert report.resumable == (resumable_id,)
        assert report.awaiting_resubmission == (prepared_id,)
        assert report.uncertain == (uncertain_id,)
        assert report.settled == (settled_id,)

    async def test_restart_resumes_dispatched_job_to_real_terminal(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        # A fresh reconciler over the persisted intents simulates the restart.
        restarted, _, _, registrar, _ = await _reconciler(
            contract,
            intents=[_dispatched_intent(status=EditingJobStatus.RUNNING)],
            outcomes=["Success"],
        )
        report = await restarted.recover()
        assert report.resumable == (JOB_ID,)
        snapshot = await restarted.reconcile_until_terminal(JOB_ID)
        assert snapshot.status is EditingJobStatus.SUCCEEDED
        assert registrar.calls == [JOB_ID]

    async def test_recovery_never_replays_uncertain_submission(
        self, contract: AliyunImsEditingStagingContract
    ) -> None:
        reconciler, transport, store, _, _ = await _reconciler(
            contract, intents=[_uncertain_intent()], outcomes=[]
        )
        report = await reconciler.recover()
        assert report.uncertain == (JOB_ID,)
        snapshot = await reconciler.poll_once(JOB_ID)
        assert snapshot.status is EditingJobStatus.OUTCOME_UNCERTAIN
        assert transport.requests == []
        stored = await store.load(JOB_ID)
        assert stored is not None and stored.state is AliyunEditingIntentState.UNCERTAIN


@pytest.mark.asyncio
class TestIntentStoreEnumeration:
    async def test_in_memory_store_lists_and_finds_by_vendor_job_id(self) -> None:
        store = InMemoryAliyunEditingIntentStore()
        intent = _dispatched_intent()
        await store.save(intent)
        assert await store.load_all() == (intent,)
        assert await store.load_by_vendor_job_id(VENDOR_JOB_ID) == intent
        assert await store.load_by_vendor_job_id("1f470a3a89d94f41a43bc6419ba6a144") is None
