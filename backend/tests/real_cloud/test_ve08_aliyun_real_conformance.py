"""VE-08 real-cloud sample: the shared conformance suite against real Aliyun.

Runs only with `AUTOMATION_TOOL_REAL_CLOUD=1` and local credentials. The
identical provider-neutral suite that the fake second provider passes is
executed against the production Aliyun adapter wired to the real gateway:
one tiny staged input, one real submission, real polling to the confirmed
terminal state. Local-only checks (validation, idempotent replay, conflict,
unknown jobs) cost nothing; exactly one media-producing job is billed. The
staged input and produced output are deleted afterwards.
"""

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import formatdate
from pathlib import Path
from typing import final

import httpx2
import pytest

from automation_tool.control_plane.domain.aliyun_ims_editing_provider import (
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
)
from automation_tool.control_plane.domain.aliyun_ims_editing_staging import (
    AliyunImsRegion,
    EditingServicePreflight,
    MediaStagingPlan,
    PreflightCheckStatus,
    StagingAsset,
    build_media_staging_plan,
    load_aliyun_ims_editing_staging_contract,
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
    EditingProviderId,
    EditingSubmission,
    editing_submission_idempotency_key,
)
from automation_tool.control_plane.domain.video_editing_provider_conformance import (
    CONFORMANCE_CHECKS,
    EditingProviderConformanceScenario,
    run_editing_provider_conformance,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts/video/aliyun-ims-editing-staging.v1.json"
CREDENTIAL_PATH = Path(
    os.environ.get(
        "AUTOMATION_TOOL_ALIYUN_CREDENTIALS",
        os.fspath(REPOSITORY_ROOT / ".local/secrets/aliyun-video-editing.json"),
    )
)

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


def _oss_authorization(
    credential: _Credential, *, verb: str, content_type: str, date: str, object_key: str
) -> str:
    string_to_sign = f"{verb}\n\n{content_type}\n{date}\n/{credential.bucket}/{object_key}"
    digest = hmac.new(
        credential.access_key_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return f"OSS {credential.access_key_id}:{base64.b64encode(digest).decode('ascii')}"


def _oss_url(credential: _Credential, object_key: str) -> str:
    return (
        f"https://{credential.bucket}.oss-{credential.region.value}"
        f".aliyuncs.com/{object_key}"
    )


async def _oss_put_object(
    client: httpx2.AsyncClient, credential: _Credential, object_key: str, body: bytes
) -> None:
    date = formatdate(usegmt=True)
    content_type = "application/octet-stream"
    response = await client.put(
        _oss_url(credential, object_key),
        content=body,
        headers={
            "Authorization": _oss_authorization(
                credential,
                verb="PUT",
                content_type=content_type,
                date=date,
                object_key=object_key,
            ),
            "Content-Type": content_type,
            "Date": date,
        },
    )
    assert response.status_code == 200, f"OSS PutObject failed with {response.status_code}"


async def _oss_delete_object(
    client: httpx2.AsyncClient, credential: _Credential, object_key: str
) -> None:
    date = formatdate(usegmt=True)
    await client.delete(
        _oss_url(credential, object_key),
        headers={
            "Authorization": _oss_authorization(
                credential, verb="DELETE", content_type="", date=date, object_key=object_key
            ),
            "Date": date,
        },
    )


@final
class _RealSubmitTransport:
    def __init__(self, client: httpx2.AsyncClient, credential: _Credential) -> None:
        self._client = client
        self._credential = credential
        self.dispatch_count = 0

    async def submit_media_producing_job(
        self, request: AliyunSubmitMediaProducingRequest
    ) -> AliyunSubmitAcknowledgement:
        self.dispatch_count += 1
        query = dict(request.query_parameters())
        headers = acs3_signed_headers(
            access_key_id=self._credential.access_key_id,
            access_key_secret=self._credential.access_key_secret,
            method="POST",
            host=request.endpoint,
            path="/",
            query=query,
            action=request.action,
            api_version=request.api_version,
            body=b"",
            timestamp=_utc_timestamp(),
            nonce=secrets.token_hex(16),
        )
        response = await self._client.post(
            f"https://{request.endpoint}/?{canonical_query_string(query)}",
            headers=dict(headers),
        )
        assert response.status_code == 200, f"submit failed with {response.status_code}"
        payload = response.json()
        return AliyunSubmitAcknowledgement(
            vendor_job_id=payload["JobId"], request_id=payload["RequestId"]
        )


@final
class _RealQueryTransport:
    def __init__(self, client: httpx2.AsyncClient, credential: _Credential) -> None:
        self._client = client
        self._credential = credential

    async def get_media_producing_job(
        self, request: AliyunGetMediaProducingRequest
    ) -> AliyunMediaProducingJobReport:
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
        except httpx2.HTTPError as error:
            raise AliyunImsTransportFailure(
                AliyunImsTransportFailureKind.RESPONSE_LOST
            ) from error
        if response.status_code == 404:
            raise AliyunImsJobNotFound
        if response.status_code != 200:
            raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.RESPONSE_LOST)
        job = response.json().get("MediaProducingJob", {})
        return AliyunMediaProducingJobReport(
            vendor_job_id=job["JobId"], status_token=job["Status"]
        )


@final
class _SinglePlanPlanner:
    def __init__(self, plan: MediaStagingPlan) -> None:
        self._plan = plan

    async def plan(self, timeline: EditingTimeline) -> MediaStagingPlan:
        return self._plan


@final
class _StaticPreflightSource:
    def __init__(self, preflight: EditingServicePreflight) -> None:
        self._preflight = preflight

    async def current(self) -> EditingServicePreflight:
        return self._preflight


def _generate_tiny_video(directory: Path) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None, "ffmpeg is required to generate the tiny acceptance video"
    target = directory / "ve08-tiny-input.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=128x128:d=2",
            "-pix_fmt",
            "yuv420p",
            "-y",
            os.fspath(target),
        ],
        check=True,
        capture_output=True,
    )
    return target.read_bytes()


def _timeline(
    *,
    project_id: EditingProjectId,
    input_artifact_id: ArtifactId,
    transition: TransitionKind | None,
    duration_ms: int,
) -> EditingTimeline:
    second_clip_transition = (
        None if transition is None else TimelineTransition(kind=transition, duration_ms=500)
    )
    half = duration_ms // 2
    return EditingTimeline(
        timeline_id=TimelineId.new(),
        project_id=project_id,
        revision=1,
        duration_ms=duration_ms,
        tracks=(
            TimelineTrack(
                track_id="visual-main",
                kind=TimelineTrackKind.VISUAL,
                clips=(
                    TimelineClip(
                        clip_id="clip-1",
                        start_ms=0,
                        duration_ms=half,
                        source_artifact_id=input_artifact_id,
                        text=None,
                        transition_in=None,
                    ),
                    TimelineClip(
                        clip_id="clip-2",
                        start_ms=half,
                        duration_ms=duration_ms - half,
                        source_artifact_id=input_artifact_id,
                        text=None,
                        transition_in=second_clip_transition,
                    ),
                ),
            ),
        ),
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_real_aliyun_adapter_passes_the_shared_conformance_suite(
    tmp_path: Path,
) -> None:
    credential = _load_credential()
    contract = load_aliyun_ims_editing_staging_contract(CONTRACT_PATH)
    video_bytes = _generate_tiny_video(tmp_path)
    digest = hashlib.sha256(video_bytes).hexdigest()

    input_artifact_id = ArtifactId.new()
    plan = build_media_staging_plan(
        contract=contract,
        service_region=credential.region,
        bucket_region=credential.region,
        assets=(
            StagingAsset(
                logical_id=str(input_artifact_id),
                sha256_hex=digest,
                size_bytes=len(video_bytes),
                extension=".mp4",
            ),
        ),
    )
    staged_key = plan.object_key_for(str(input_artifact_id))

    project_id = EditingProjectId.new()
    supported_timeline = _timeline(
        project_id=project_id,
        input_artifact_id=input_artifact_id,
        transition=TransitionKind.FADE,
        duration_ms=2_000,
    )
    editing_job_id = EditingJobId.new()
    submission = EditingSubmission(
        editing_job_id=editing_job_id,
        project_id=project_id,
        timeline=supported_timeline,
        idempotency_key=editing_submission_idempotency_key(editing_job_id),
    )
    output_key = f"editing-output/v1/{editing_job_id}.mp4"

    async with httpx2.AsyncClient(timeout=60.0) as client:
        try:
            await _oss_put_object(client, credential, staged_key, video_bytes)

            intent_store = InMemoryAliyunEditingIntentStore()
            submit_transport = _RealSubmitTransport(client, credential)
            provider = AliyunImsEditingProvider(
                contract=contract,
                region=credential.region,
                staging_bucket=credential.bucket,
                output=AliyunEditingOutputConfig(width=128, height=128),
                preflight_source=_StaticPreflightSource(
                    EditingServicePreflight(
                        region=credential.region,
                        region_check=PreflightCheckStatus.PASSED,
                        permission_check=PreflightCheckStatus.PASSED,
                        quota_check=PreflightCheckStatus.PASSED,
                    )
                ),
                staging_planner=_SinglePlanPlanner(plan),
                transport=submit_transport,
                intent_store=intent_store,
            )
            reconciler = AliyunImsEditingReconciler(
                provider=provider,
                intent_store=intent_store,
                transport=_RealQueryTransport(client, credential),
                contract=contract,
                region=credential.region,
                registrar=NewArtifactAliyunEditingOutputRegistrar(),
                policy=AliyunEditingReconciliationPolicy(
                    max_polls=60, transient_failure_limit=5, poll_interval_seconds=5.0
                ),
            )

            async def _drive_to_success(job_id: EditingJobId) -> None:
                snapshot = await reconciler.reconcile_until_terminal(job_id)
                assert snapshot.status.value == "succeeded", snapshot.status

            scenario = EditingProviderConformanceScenario(
                provider=provider,
                supported_submission=submission,
                unsupported_timeline=_timeline(
                    project_id=project_id,
                    input_artifact_id=input_artifact_id,
                    transition=TransitionKind.DISSOLVE,
                    duration_ms=2_000,
                ),
                conflicting_timeline=_timeline(
                    project_id=project_id,
                    input_artifact_id=input_artifact_id,
                    transition=None,
                    duration_ms=4_000,
                ),
                drive_to_success=_drive_to_success,
            )
            report = await run_editing_provider_conformance(scenario)
            assert report.provider_id == EditingProviderId("aliyun_ims")
            assert report.passed_checks == CONFORMANCE_CHECKS
            assert submit_transport.dispatch_count == 1

            print(
                "VE-08 real acceptance: aliyun_ims passed "
                f"{len(report.passed_checks)} conformance checks with a single "
                "real dispatch"
            )
        finally:
            for key in (staged_key, output_key):
                with contextlib.suppress(httpx2.HTTPError, asyncio.CancelledError):
                    await _oss_delete_object(client, credential, key)
