"""VE-05 real-cloud acceptance: one genuine SubmitMediaProducingJob dispatch.

This vertical acceptance runs only when the operator explicitly opts in with
`AUTOMATION_TOOL_REAL_CLOUD=1` and the local credential file exists. It stages
one tiny generated video into the project's dedicated OSS bucket, performs the
real read-only preflight call, submits exactly one minimal editing job through
the production adapter (`AliyunImsEditingProvider`), verifies the acknowledged
JobId and persisted request hash, proves the idempotent replay does not
re-dispatch, and deletes the staged test object afterwards. Credentials stay
in `.local/secrets` and never enter code, fixtures or assertions. Polling the
job to completion is deliberately out of scope (VE-06).
"""

import asyncio
import base64
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
)
from automation_tool.control_plane.domain.video_editing import (
    EditingJobId,
    EditingJobStatus,
    EditingProjectId,
    EditingTimeline,
)
from automation_tool.control_plane.domain.video_editing_provider import (
    EditingSubmission,
    editing_submission_idempotency_key,
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
    credential: _Credential,
    *,
    verb: str,
    content_type: str,
    date: str,
    object_key: str,
) -> str:
    string_to_sign = f"{verb}\n\n{content_type}\n{date}\n/{credential.bucket}/{object_key}"
    digest = hmac.new(
        credential.access_key_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return f"OSS {credential.access_key_id}:{base64.b64encode(digest).decode('ascii')}"


async def _oss_put_object(
    client: httpx2.AsyncClient, credential: _Credential, object_key: str, body: bytes
) -> None:
    date = formatdate(usegmt=True)
    content_type = "application/octet-stream"
    response = await client.put(
        f"https://{credential.bucket}.oss-{credential.region.value}.aliyuncs.com/{object_key}",
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
) -> int:
    date = formatdate(usegmt=True)
    response = await client.delete(
        f"https://{credential.bucket}.oss-{credential.region.value}.aliyuncs.com/{object_key}",
        headers={
            "Authorization": _oss_authorization(
                credential, verb="DELETE", content_type="", date=date, object_key=object_key
            ),
            "Date": date,
        },
    )
    return response.status_code


async def _real_preflight(
    client: httpx2.AsyncClient, credential: _Credential, endpoint: str
) -> EditingServicePreflight:
    query = {"PageSize": "1"}
    headers = acs3_signed_headers(
        access_key_id=credential.access_key_id,
        access_key_secret=credential.access_key_secret,
        method="GET",
        host=endpoint,
        path="/",
        query=query,
        action="ListMediaBasicInfos",
        api_version="2020-11-09",
        body=b"",
        timestamp=_utc_timestamp(),
        nonce=secrets.token_hex(16),
    )
    response = await client.get(
        f"https://{endpoint}/?{canonical_query_string(query)}", headers=dict(headers)
    )
    passed = response.status_code == 200 and bool(response.json().get("RequestId"))
    status = PreflightCheckStatus.PASSED if passed else PreflightCheckStatus.FAILED
    return EditingServicePreflight(
        region=credential.region,
        region_check=status,
        permission_check=status,
        quota_check=status,
    )


@final
class _RealAliyunImsTransport:
    """Real ACS3-signed SubmitMediaProducingJob dispatch over HTTPS."""

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
        try:
            response = await self._client.post(
                f"https://{request.endpoint}/?{canonical_query_string(query)}",
                headers=dict(headers),
            )
        except httpx2.ConnectError as error:
            raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.NOT_DISPATCHED) from error
        except httpx2.HTTPError as error:
            raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.RESPONSE_LOST) from error
        if response.status_code >= 500:
            raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.RESPONSE_LOST)
        if response.status_code == 403:
            raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.REJECTED_PERMISSION)
        if response.status_code == 429:
            raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.REJECTED_THROTTLED)
        if response.status_code != 200:
            raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.REJECTED_INVALID)
        payload = response.json()
        return AliyunSubmitAcknowledgement(
            vendor_job_id=payload["JobId"], request_id=payload["RequestId"]
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
    target = directory / "ve05-tiny-input.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=128x128:d=2",
            "-pix_fmt",
            "yuv420p",
            "-y",
            os.fspath(target),
        ],
        check=True,
        capture_output=True,
    )
    return target.read_bytes()


@pytest.mark.asyncio
async def test_real_single_dispatch_submission_with_persisted_job_id(
    tmp_path: Path,
) -> None:
    credential = _load_credential()
    contract = load_aliyun_ims_editing_staging_contract(CONTRACT_PATH)
    endpoint = contract.endpoints[credential.region]
    video_bytes = _generate_tiny_video(tmp_path)
    digest = hashlib.sha256(video_bytes).hexdigest()

    artifact_id = ArtifactId.new()
    plan = build_media_staging_plan(
        contract=contract,
        service_region=credential.region,
        bucket_region=credential.region,
        assets=(
            StagingAsset(
                logical_id=str(artifact_id),
                sha256_hex=digest,
                size_bytes=len(video_bytes),
                extension=".mp4",
            ),
        ),
    )
    staged_key = plan.object_key_for(str(artifact_id))

    project_id = EditingProjectId.new()
    timeline = EditingTimeline(
        timeline_id=TimelineId.new(),
        project_id=project_id,
        revision=1,
        duration_ms=2_000,
        tracks=(
            TimelineTrack(
                track_id="visual-main",
                kind=TimelineTrackKind.VISUAL,
                clips=(
                    TimelineClip(
                        clip_id="visual-1",
                        start_ms=0,
                        duration_ms=2_000,
                        source_artifact_id=artifact_id,
                        text=None,
                        transition_in=None,
                    ),
                ),
            ),
        ),
        created_at=datetime.now(UTC),
    )
    editing_job_id = EditingJobId.new()
    submission = EditingSubmission(
        editing_job_id=editing_job_id,
        project_id=project_id,
        timeline=timeline,
        idempotency_key=editing_submission_idempotency_key(editing_job_id),
    )

    async with httpx2.AsyncClient(timeout=30.0) as client:
        output_key = ""
        try:
            await _oss_put_object(client, credential, staged_key, video_bytes)

            preflight = await _real_preflight(client, credential, endpoint)
            assert preflight.ready, "real preflight (ListMediaBasicInfos) must pass"

            transport = _RealAliyunImsTransport(client, credential)
            store = InMemoryAliyunEditingIntentStore()
            provider = AliyunImsEditingProvider(
                contract=contract,
                region=credential.region,
                staging_bucket=credential.bucket,
                output=AliyunEditingOutputConfig(width=128, height=128),
                preflight_source=_StaticPreflightSource(preflight),
                staging_planner=_SinglePlanPlanner(plan),
                transport=transport,
                intent_store=store,
            )

            snapshot = await provider.submit(submission)
            assert snapshot.status is EditingJobStatus.QUEUED
            intent = await store.load(editing_job_id)
            assert intent is not None
            assert intent.state is AliyunEditingIntentState.DISPATCHED
            assert intent.vendor_job_id
            assert len(intent.request_hash) == 64

            replay = await provider.submit(submission)
            assert replay == snapshot
            assert transport.dispatch_count == 1

            output_key = f"editing-output/v1/{editing_job_id}.mp4"
            print(
                "VE-05 real acceptance: "
                f"JobId={intent.vendor_job_id} requestHash={intent.request_hash}"
            )
        finally:
            await asyncio.sleep(60)
            staged_status = await _oss_delete_object(client, credential, staged_key)
            assert staged_status in {200, 204}
            if output_key:
                await _oss_delete_object(client, credential, output_key)
