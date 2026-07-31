from __future__ import annotations

from email.utils import formatdate
from typing import Any

import httpx2
import pytest

from automation_tool.control_plane.domain.aliyun_ims_editing_output import (
    AliyunOssObjectMissing,
    AliyunOssObjectRef,
)
from automation_tool.control_plane.domain.aliyun_ims_editing_provider import (
    AliyunImsTransportFailure,
    AliyunImsTransportFailureKind,
    AliyunOssBucketName,
    AliyunSubmitMediaProducingRequest,
)
from automation_tool.control_plane.domain.aliyun_ims_editing_reconciliation import (
    AliyunGetMediaProducingRequest,
)
from automation_tool.control_plane.domain.aliyun_ims_editing_staging import AliyunImsRegion
from automation_tool.control_plane.domain.video_editing import EditingJobId
from automation_tool.control_plane.domain.video_editing_provider import (
    editing_submission_idempotency_key,
)
from automation_tool.control_plane.infrastructure.aliyun.editing import (
    AliyunEditingCredential,
    AliyunImsEditingTransport,
    AliyunOssEditingTransport,
)


def _credential() -> AliyunEditingCredential:
    return AliyunEditingCredential(
        access_key_id="LTAI5tVe04TestAccessKey",
        access_key_secret="ve04PrivateSecret1234567890",
        region=AliyunImsRegion.CN_SHANGHAI,
        bucket=AliyunOssBucketName("automation-tool-video-staging"),
    )


def _submit_request() -> AliyunSubmitMediaProducingRequest:
    job_id = EditingJobId.parse("00000000-0000-4000-8000-000000000201")
    return AliyunSubmitMediaProducingRequest(
        endpoint="ice.cn-shanghai.aliyuncs.com",
        api_version="2020-11-09",
        action="SubmitMediaProducingJob",
        region=AliyunImsRegion.CN_SHANGHAI,
        timeline_json='{"VideoTracks":[]}',
        output_media_target="oss-object",
        output_media_config_json='{"MediaURL":"test"}',
        client_token=editing_submission_idempotency_key(job_id),
    )


def _query_request() -> AliyunGetMediaProducingRequest:
    return AliyunGetMediaProducingRequest(
        endpoint="ice.cn-shanghai.aliyuncs.com",
        api_version="2020-11-09",
        action="GetMediaProducingJob",
        region=AliyunImsRegion.CN_SHANGHAI,
        vendor_job_id="12345678-abcd",
    )


def _client(handler: Any) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


def test_credential_repr_and_errors_never_reflect_secrets() -> None:
    credential = _credential()
    rendered = repr(credential)
    assert "LTAI" not in rendered
    assert "PrivateSecret" not in rendered
    assert rendered == "AliyunEditingCredential(<redacted>)"

    with pytest.raises(ValueError, match="invalid") as failure:
        AliyunEditingCredential(
            access_key_id="bad",
            access_key_secret="also-bad",
            region=AliyunImsRegion.CN_SHANGHAI,
            bucket=AliyunOssBucketName("automation-tool-video-staging"),
        )
    assert "bad" not in str(failure.value)


@pytest.mark.asyncio
async def test_submit_and_query_use_signed_fixed_actions_and_parse_closed_responses() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.headers["x-acs-action"] == "SubmitMediaProducingJob":
            return httpx2.Response(
                200,
                json={"JobId": "12345678-abcd", "RequestId": "request-12345678"},
            )
        return httpx2.Response(
            200,
            json={
                "MediaProducingJob": {
                    "JobId": "12345678-abcd",
                    "Status": "Processing",
                }
            },
        )

    async with _client(handler) as client:
        transport = AliyunImsEditingTransport(
            client,
            _credential(),
            timestamp=lambda: "2026-07-31T01:02:03Z",
            nonce=lambda: "00112233445566778899aabbccddeeff",
        )
        acknowledgement = await transport.submit_media_producing_job(_submit_request())
        report = await transport.get_media_producing_job(_query_request())

    assert acknowledgement.vendor_job_id == "12345678-abcd"
    assert acknowledgement.request_id == "request-12345678"
    assert report.vendor_job_id == "12345678-abcd"
    assert report.status_token == "Processing"
    assert [request.method for request in requests] == ["POST", "GET"]
    assert all(
        request.headers["authorization"].startswith("ACS3-HMAC-SHA256 Credential=")
        for request in requests
    )
    assert [request.headers["x-acs-action"] for request in requests] == [
        "SubmitMediaProducingJob",
        "GetMediaProducingJob",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (400, AliyunImsTransportFailureKind.REJECTED_INVALID),
        (403, AliyunImsTransportFailureKind.REJECTED_PERMISSION),
        (429, AliyunImsTransportFailureKind.REJECTED_THROTTLED),
        (500, AliyunImsTransportFailureKind.RESPONSE_LOST),
    ],
)
async def test_submit_maps_http_failures_without_reflecting_bodies(
    status: int, kind: AliyunImsTransportFailureKind
) -> None:
    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, text="accessKeySecret=private-upstream-body")

    async with _client(handler) as client:
        transport = AliyunImsEditingTransport(client, _credential())
        with pytest.raises(AliyunImsTransportFailure) as failure:
            await transport.submit_media_producing_job(_submit_request())
    assert failure.value.kind is kind
    assert "private-upstream-body" not in str(failure.value)


@pytest.mark.asyncio
async def test_oss_put_stream_get_delete_and_absence_probe_are_signed() -> None:
    requests: list[httpx2.Request] = []
    body = b"verified-video-bytes"

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.method == "HEAD":
            return httpx2.Response(404)
        if request.method == "GET":
            return httpx2.Response(200, content=body)
        return httpx2.Response(200)

    ref = AliyunOssObjectRef(
        bucket=_credential().bucket,
        object_key="editing-staging/v1/"
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef.mp4",
    )

    async def chunks():
        yield body[:8]
        yield body[8:]

    async with _client(handler) as client:
        transport = AliyunOssEditingTransport(
            client,
            _credential(),
            http_date=lambda: formatdate(1_775_000_000, usegmt=True),
        )
        await transport.put_object(ref, chunks(), content_length=len(body))
        received = b"".join([chunk async for chunk in transport.stream_object(ref)])
        await transport.delete_object(ref)
        keys = await transport.list_object_keys(ref.bucket, ref.object_key)

    assert received == body
    assert keys == ()
    assert [request.method for request in requests] == ["PUT", "GET", "DELETE", "HEAD"]
    assert all(request.headers["authorization"].startswith("OSS ") for request in requests)
    assert requests[0].headers["content-length"] == str(len(body))
    assert "transfer-encoding" not in requests[0].headers


@pytest.mark.asyncio
async def test_missing_oss_output_is_distinct_from_an_ambiguous_transport_failure() -> None:
    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(404, text="private object name")

    ref = AliyunOssObjectRef(
        bucket=_credential().bucket,
        object_key="editing-output/v1/00000000-0000-4000-8000-000000000201.mp4",
    )
    async with _client(handler) as client:
        transport = AliyunOssEditingTransport(client, _credential())
        with pytest.raises(AliyunOssObjectMissing):
            _ = b"".join([chunk async for chunk in transport.stream_object(ref)])
