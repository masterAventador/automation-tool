"""VE-07 real-cloud acceptance: finished-video import, lineage, cost, cleanup.

Runs only with `AUTOMATION_TOOL_REAL_CLOUD=1` and local credentials. The full
production chain executes against the real gateway: one tiny generated video
is staged to the project OSS bucket, one minimal editing job is submitted
through the production `AliyunImsEditingProvider`, and the production
`AliyunImsEditingReconciler` — wired with the real VE-07
`AliyunEditingOutputImporter` instead of the VE-06 minimal registrar — polls
the job to its real terminal state. On the confirmed success the importer
streams the produced MP4 from OSS, verifies its digest, records lineage and
the estimated cost, and the temp-resource cleaner then deletes the staged
input and the output object and verifies their absence. Credentials never
enter code, fixtures or assertions.
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
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import formatdate
from pathlib import Path
from typing import final

import httpx2
import pytest

from automation_tool.control_plane.domain.aliyun_ims_editing_output import (
    AliyunEditingCleanupOutcome,
    AliyunEditingLineageBasis,
    AliyunEditingOutputImporter,
    AliyunEditingTempResourceCleaner,
    AliyunOssObjectMissing,
    AliyunOssObjectRef,
    DirectoryEditingOutputPayloadSink,
)
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
from automation_tool.control_plane.domain.video_editing_outputs import (
    EditingOutputCostSource,
    EditingOutputKind,
    InMemoryEditingOutputLedger,
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


@final
class _RealOssOutputTransport:
    """Real OSS implementation of the VE-07 output transport port."""

    def __init__(self, client: httpx2.AsyncClient, credential: _Credential) -> None:
        self._client = client
        self._credential = credential
        self.stream_calls = 0

    def stream_object(self, ref: AliyunOssObjectRef) -> AsyncIterator[bytes]:
        self.stream_calls += 1

        async def _chunks() -> AsyncIterator[bytes]:
            date = formatdate(usegmt=True)
            headers = {
                "Authorization": _oss_authorization(
                    self._credential,
                    verb="GET",
                    content_type="",
                    date=date,
                    object_key=ref.object_key,
                ),
                "Date": date,
            }
            try:
                async with self._client.stream(
                    "GET", _oss_url(self._credential, ref.object_key), headers=headers
                ) as response:
                    if response.status_code == 404:
                        raise AliyunOssObjectMissing
                    if response.status_code != 200:
                        raise AliyunImsTransportFailure(
                            AliyunImsTransportFailureKind.RESPONSE_LOST
                        )
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        yield chunk
            except httpx2.HTTPError as error:
                raise AliyunImsTransportFailure(
                    AliyunImsTransportFailureKind.RESPONSE_LOST
                ) from error

        return _chunks()

    async def delete_object(self, ref: AliyunOssObjectRef) -> None:
        date = formatdate(usegmt=True)
        response = await self._client.delete(
            _oss_url(self._credential, ref.object_key),
            headers={
                "Authorization": _oss_authorization(
                    self._credential,
                    verb="DELETE",
                    content_type="",
                    date=date,
                    object_key=ref.object_key,
                ),
                "Date": date,
            },
        )
        if response.status_code not in {200, 204}:
            raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.RESPONSE_LOST)

    async def list_object_keys(
        self, bucket: AliyunOssBucketName, prefix: str
    ) -> tuple[str, ...]:
        # Absence verification via a signed HEAD on the exact key: this
        # transport is only queried with full object keys after deletion.
        date = formatdate(usegmt=True)
        response = await self._client.head(
            _oss_url(self._credential, prefix),
            headers={
                "Authorization": _oss_authorization(
                    self._credential,
                    verb="HEAD",
                    content_type="",
                    date=date,
                    object_key=prefix,
                ),
                "Date": date,
            },
        )
        if response.status_code == 200:
            return (prefix,)
        if response.status_code == 404:
            return ()
        raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.RESPONSE_LOST)


@final
class _RealAliyunImsSubmitTransport:
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
class _RealAliyunImsQueryTransport:
    """Real ACS3-signed GetMediaProducingJob query over HTTPS."""

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


@final
class _StaticBasisSource:
    def __init__(self, basis: AliyunEditingLineageBasis) -> None:
        self._basis = basis

    async def basis_for(self, editing_job_id: EditingJobId) -> AliyunEditingLineageBasis:
        return self._basis


def _generate_tiny_video(directory: Path) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None, "ffmpeg is required to generate the tiny acceptance video"
    target = directory / "ve07-tiny-input.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=128x128:d=2",
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
async def test_real_output_import_lineage_cost_and_cleanup(tmp_path: Path) -> None:
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
                        source_artifact_id=input_artifact_id,
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
    output_key = f"editing-output/v1/{editing_job_id}.mp4"

    async with httpx2.AsyncClient(timeout=60.0) as client:
        oss_transport = _RealOssOutputTransport(client, credential)
        try:
            await _oss_put_object(client, credential, staged_key, video_bytes)

            preflight = EditingServicePreflight(
                region=credential.region,
                region_check=PreflightCheckStatus.PASSED,
                permission_check=PreflightCheckStatus.PASSED,
                quota_check=PreflightCheckStatus.PASSED,
            )
            intent_store = InMemoryAliyunEditingIntentStore()
            provider = AliyunImsEditingProvider(
                contract=contract,
                region=credential.region,
                staging_bucket=credential.bucket,
                output=AliyunEditingOutputConfig(width=128, height=128),
                preflight_source=_StaticPreflightSource(preflight),
                staging_planner=_SinglePlanPlanner(plan),
                transport=_RealAliyunImsSubmitTransport(client, credential),
                intent_store=intent_store,
            )
            snapshot = await provider.submit(submission)
            assert snapshot.status is EditingJobStatus.QUEUED

            ledger = InMemoryEditingOutputLedger()
            sink = DirectoryEditingOutputPayloadSink(tmp_path / "imported")
            importer = AliyunEditingOutputImporter(
                intent_store=intent_store,
                transport=oss_transport,
                sink=sink,
                ledger=ledger,
                contract=contract,
                region=credential.region,
                bucket=credential.bucket,
                basis_source=_StaticBasisSource(
                    AliyunEditingLineageBasis(
                        project_id=project_id,
                        timeline_id=timeline.timeline_id,
                        timeline_revision=timeline.revision,
                        input_artifact_ids=(input_artifact_id,),
                        output_duration_ms=timeline.duration_ms,
                        output_height=128,
                    )
                ),
            )
            reconciler = AliyunImsEditingReconciler(
                provider=provider,
                intent_store=intent_store,
                transport=_RealAliyunImsQueryTransport(client, credential),
                contract=contract,
                region=credential.region,
                registrar=importer,
                policy=AliyunEditingReconciliationPolicy(
                    max_polls=60, transient_failure_limit=5, poll_interval_seconds=5.0
                ),
            )
            settled = await reconciler.reconcile_until_terminal(editing_job_id)
            assert settled.status is EditingJobStatus.SUCCEEDED, settled.status
            assert len(settled.output_artifact_ids) == 1

            lineage = await ledger.load(editing_job_id)
            assert lineage is not None
            video = lineage.outputs[0]
            assert video.kind is EditingOutputKind.VIDEO
            stored = sink.path_for(video.artifact_id, video.media_type)
            payload = stored.read_bytes()
            assert len(payload) == video.byte_size > 0
            assert hashlib.sha256(payload).hexdigest() == video.sha256_hex
            assert lineage.cost.source is EditingOutputCostSource.ESTIMATED
            assert lineage.cost.billed_minutes == 1
            assert lineage.cost.currency == "CNY"
            assert lineage.input_artifact_ids == (input_artifact_id,)

            replay = await importer.register_confirmed_output(editing_job_id)
            assert replay == settled.output_artifact_ids
            assert oss_transport.stream_calls == 1

            cleaner = AliyunEditingTempResourceCleaner(
                transport=oss_transport, bucket=credential.bucket
            )
            report = await cleaner.cleanup(
                editing_job_id,
                staging_object_keys=(staged_key,),
                outcome=AliyunEditingCleanupOutcome.SUCCEEDED_IMPORTED,
            )
            assert set(report.deleted_keys) == {staged_key, output_key}
            assert report.retained_keys == ()
            assert report.verified_absent is True

            print(
                "VE-07 real acceptance: "
                f"job={editing_job_id} bytes={video.byte_size} "
                f"sha256={video.sha256_hex} "
                f"cost={lineage.cost.total_cny} CNY ({lineage.cost.tier_id})"
            )
        finally:
            # Safety net: idempotent deletes even if an assertion failed above.
            for key in (staged_key, output_key):
                with contextlib.suppress(AliyunImsTransportFailure, asyncio.CancelledError):
                    await oss_transport.delete_object(
                        AliyunOssObjectRef(bucket=credential.bucket, object_key=key)
                    )
