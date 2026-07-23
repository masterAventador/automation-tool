"""VE-06 real-cloud acceptance: reconcile a genuine media producing job.

Runs only with `AUTOMATION_TOOL_REAL_CLOUD=1` plus the local credential file.
It queries the real `GetMediaProducingJob` gateway for the job submitted by the
VE-05 real acceptance (JobId recorded in `docs/development/VE-05.md`), drives
the production reconciler (`AliyunImsEditingReconciler`) from a DISPATCHED
intent to the real terminal state, and proves the settled intent is durable
and replay-safe. No new staging or output object is created, so there is
nothing to clean up afterwards. Credentials stay in `.local/secrets` and never
enter code, fixtures or assertions.
"""

import json
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import final
from uuid import UUID

import httpx2
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
    acs3_signed_headers,
    canonical_query_string,
)
from automation_tool.control_plane.domain.aliyun_ims_editing_reconciliation import (
    AliyunEditingReconciliationPolicy,
    AliyunGetMediaProducingRequest,
    AliyunImsEditingReconciler,
    AliyunImsJobNotFound,
    AliyunMediaProducingJobReport,
    NewArtifactAliyunEditingOutputRegistrar,
    build_get_media_producing_request,
)
from automation_tool.control_plane.domain.aliyun_ims_editing_staging import (
    AliyunImsRegion,
    EditingServicePreflight,
    MediaStagingPlan,
    load_aliyun_ims_editing_staging_contract,
)
from automation_tool.control_plane.domain.video_editing import (
    EditingJobId,
    EditingJobStatus,
    EditingTimeline,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts/video/aliyun-ims-editing-staging.v1.json"
CREDENTIAL_PATH = Path(
    os.environ.get(
        "AUTOMATION_TOOL_ALIYUN_CREDENTIALS",
        os.fspath(REPOSITORY_ROOT / ".local/secrets/aliyun-video-editing.json"),
    )
)

# Real JobId acknowledged by the VE-05 real-cloud submission (cn-beijing).
REAL_VENDOR_JOB_ID = "46c446e2420348e0950e4d7876acc6fb"
JOB_ID = EditingJobId(UUID("00000000-0000-4000-8000-0000000000cc"))

pytestmark = pytest.mark.skipif(
    os.environ.get("AUTOMATION_TOOL_REAL_CLOUD") != "1" or not CREDENTIAL_PATH.exists(),
    reason="real Aliyun acceptance requires AUTOMATION_TOOL_REAL_CLOUD=1 and local credentials",
)


@final
@dataclass(frozen=True, slots=True)
class _Credential:
    access_key_id: str
    access_key_secret: str
    region: AliyunImsRegion
    bucket: AliyunOssBucketName


def _load_credential() -> _Credential:
    document = json.loads(CREDENTIAL_PATH.read_text(encoding="utf-8"))
    return _Credential(
        access_key_id=document["accessKeyId"],
        access_key_secret=document["accessKeySecret"],
        region=AliyunImsRegion(document["region"]),
        bucket=AliyunOssBucketName(document["ossBucket"]),
    )


def _utc_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@final
class _RealAliyunImsQueryTransport:
    """Real ACS3-signed GetMediaProducingJob query over HTTPS."""

    def __init__(self, client: httpx2.AsyncClient, credential: _Credential) -> None:
        self._client = client
        self._credential = credential
        self.query_count = 0

    async def get_media_producing_job(
        self, request: AliyunGetMediaProducingRequest
    ) -> AliyunMediaProducingJobReport:
        self.query_count += 1
        query = dict(request.query_parameters())
        headers = acs3_signed_headers(
            access_key_id=self._credential.access_key_id,
            access_key_secret=self._credential.access_key_secret,
            method="GET",
            host=request.endpoint,
            path="/",
            query=query,
            action=request.action,
            api_version=request.api_version,
            body=b"",
            timestamp=_utc_timestamp(),
            nonce=secrets.token_hex(16),
        )
        try:
            response = await self._client.get(
                f"https://{request.endpoint}/?{canonical_query_string(query)}",
                headers=dict(headers),
            )
        except httpx2.ConnectError as error:
            raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.NOT_DISPATCHED) from error
        except httpx2.HTTPError as error:
            raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.RESPONSE_LOST) from error
        if response.status_code == 404:
            raise AliyunImsJobNotFound
        if response.status_code >= 500:
            raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.RESPONSE_LOST)
        if response.status_code == 403:
            raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.REJECTED_PERMISSION)
        if response.status_code == 429:
            raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.REJECTED_THROTTLED)
        if response.status_code != 200:
            raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.REJECTED_INVALID)
        payload = response.json()
        job = payload["MediaProducingJob"]
        return AliyunMediaProducingJobReport(
            vendor_job_id=job["JobId"], status_token=job["Status"]
        )


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


@pytest.mark.asyncio
async def test_real_job_reconciles_to_real_terminal_state() -> None:
    credential = _load_credential()
    contract = load_aliyun_ims_editing_staging_contract(CONTRACT_PATH)

    store = InMemoryAliyunEditingIntentStore()
    await store.save(
        AliyunEditingIntent(
            editing_job_id=JOB_ID,
            request_hash="ab" * 32,
            state=AliyunEditingIntentState.DISPATCHED,
            vendor_job_id=REAL_VENDOR_JOB_ID,
            status=EditingJobStatus.QUEUED,
            failure_code=None,
            output_artifact_ids=(),
        )
    )
    provider = AliyunImsEditingProvider(
        contract=contract,
        region=credential.region,
        staging_bucket=credential.bucket,
        output=AliyunEditingOutputConfig(width=128, height=128),
        preflight_source=_UnusedPreflightSource(),
        staging_planner=_UnusedPlanner(),
        transport=_UnusedSubmitTransport(),
        intent_store=store,
    )

    async with httpx2.AsyncClient(timeout=30.0) as client:
        transport = _RealAliyunImsQueryTransport(client, credential)
        reconciler = AliyunImsEditingReconciler(
            provider=provider,
            intent_store=store,
            transport=transport,
            contract=contract,
            region=credential.region,
            registrar=NewArtifactAliyunEditingOutputRegistrar(),
            policy=AliyunEditingReconciliationPolicy(
                max_polls=30,
                transient_failure_limit=5,
                poll_interval_seconds=2.0,
            ),
        )

        request = build_get_media_producing_request(
            contract=contract, region=credential.region, vendor_job_id=REAL_VENDOR_JOB_ID
        )
        assert request.endpoint == f"ice.{credential.region.value}.aliyuncs.com"

        snapshot = await reconciler.reconcile_until_terminal(JOB_ID)
        assert snapshot.status in {
            EditingJobStatus.SUCCEEDED,
            EditingJobStatus.FAILED,
            EditingJobStatus.OUTCOME_UNCERTAIN,
        }
        # The 2-second VE-05 job finished long ago; the real gateway must
        # report a definitive terminal state, not an uncertain giving-up.
        assert snapshot.status is EditingJobStatus.SUCCEEDED
        assert snapshot.output_artifact_ids

        stored = await store.load(JOB_ID)
        assert stored is not None
        assert stored.state is AliyunEditingIntentState.DISPATCHED
        assert stored.status is EditingJobStatus.SUCCEEDED

        # Replay safety: a second full reconciliation returns the settled
        # result without any additional network query.
        queries_after_first = transport.query_count
        replay = await reconciler.reconcile_until_terminal(JOB_ID)
        assert replay == snapshot
        assert transport.query_count == queries_after_first

        print(
            "VE-06 real acceptance: "
            f"JobId={REAL_VENDOR_JOB_ID} terminal={snapshot.status.value} "
            f"queries={transport.query_count}"
        )
