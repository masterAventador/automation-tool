"""Production HTTPS transports for Aliyun IMS editing and OSS staging.

Long-lived credentials reach these adapters only from the native device
boundary. They are never serialized by this module, included in repr/error
messages, or persisted. Vendor response bodies are parsed under a hard byte
limit and every failure is mapped to the closed domain vocabulary.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import formatdate
from typing import Final, Never, final

import httpx2

from automation_tool.control_plane.domain.aliyun_ims_editing_output import (
    AliyunOssObjectMissing,
    AliyunOssObjectRef,
)
from automation_tool.control_plane.domain.aliyun_ims_editing_provider import (
    AliyunImsTransportFailure,
    AliyunImsTransportFailureKind,
    AliyunOssBucketName,
    AliyunSubmitAcknowledgement,
    AliyunSubmitMediaProducingRequest,
    acs3_signed_headers,
    canonical_query_string,
)
from automation_tool.control_plane.domain.aliyun_ims_editing_reconciliation import (
    AliyunGetMediaProducingRequest,
    AliyunImsJobNotFound,
    AliyunMediaProducingJobReport,
)
from automation_tool.control_plane.domain.aliyun_ims_editing_staging import AliyunImsRegion

_MAX_RESPONSE_BYTES: Final = 64 * 1024
_OSS_CONTENT_TYPE: Final = "application/octet-stream"


class InvalidAliyunEditingTransport(ValueError):
    """Native adapter input is invalid without reflecting its value."""

    def __init__(self) -> None:
        super().__init__("Aliyun editing transport configuration is invalid")


def _reject() -> Never:
    raise InvalidAliyunEditingTransport


@final
@dataclass(frozen=True, slots=True, repr=False)
class AliyunEditingCredential:
    """One native-only credential and its same-region staging bucket."""

    access_key_id: str
    access_key_secret: str
    region: AliyunImsRegion
    bucket: AliyunOssBucketName

    def __post_init__(self) -> None:
        key_id = self.access_key_id
        key_secret = self.access_key_secret
        if (
            type(key_id) is not str
            or not 16 <= len(key_id) <= 64
            or not key_id.startswith("LTAI")
            or not key_id.isascii()
            or not key_id.isalnum()
            or type(key_secret) is not str
            or not 20 <= len(key_secret) <= 128
            or not key_secret.isascii()
            or any(not (character.isalnum() or character in "/+=-_") for character in key_secret)
            or not isinstance(self.region, AliyunImsRegion)
            or type(self.bucket) is not AliyunOssBucketName
        ):
            _reject()

    def __repr__(self) -> str:
        return "AliyunEditingCredential(<redacted>)"


def _utc_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _nonce() -> str:
    return secrets.token_hex(16)


def _http_date() -> str:
    return formatdate(usegmt=True)


def _response_json(response: httpx2.Response) -> dict[str, object]:
    content = response.content
    if not content or len(content) > _MAX_RESPONSE_BYTES:
        raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.RESPONSE_LOST)
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.RESPONSE_LOST) from None
    if not isinstance(payload, dict):
        raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.RESPONSE_LOST)
    return payload


def _submit_status_failure(status_code: int) -> AliyunImsTransportFailure:
    if status_code == 400:
        kind = AliyunImsTransportFailureKind.REJECTED_INVALID
    elif status_code in {401, 403}:
        kind = AliyunImsTransportFailureKind.REJECTED_PERMISSION
    elif status_code == 429:
        kind = AliyunImsTransportFailureKind.REJECTED_THROTTLED
    else:
        kind = AliyunImsTransportFailureKind.RESPONSE_LOST
    return AliyunImsTransportFailure(kind)


def _signed_request_headers(
    credential: AliyunEditingCredential,
    *,
    method: str,
    host: str,
    query: dict[str, str],
    action: str,
    api_version: str,
    timestamp: str,
    nonce: str,
) -> dict[str, str]:
    return dict(
        acs3_signed_headers(
            access_key_id=credential.access_key_id,
            access_key_secret=credential.access_key_secret,
            method=method,
            host=host,
            path="/",
            query=query,
            action=action,
            api_version=api_version,
            body=b"",
            timestamp=timestamp,
            nonce=nonce,
        )
    )


@final
class AliyunImsEditingTransport:
    """ACS3-signed submit and bounded status-query implementation."""

    __slots__ = ("_client", "_credential", "_nonce", "_timestamp")

    def __init__(
        self,
        client: httpx2.AsyncClient,
        credential: AliyunEditingCredential,
        *,
        timestamp: Callable[[], str] = _utc_timestamp,
        nonce: Callable[[], str] = _nonce,
    ) -> None:
        if (
            not isinstance(client, httpx2.AsyncClient)
            or not isinstance(credential, AliyunEditingCredential)
            or not callable(timestamp)
            or not callable(nonce)
        ):
            _reject()
        self._client = client
        self._credential = credential
        self._timestamp = timestamp
        self._nonce = nonce

    def __repr__(self) -> str:
        return "AliyunImsEditingTransport(<redacted>)"

    async def submit_media_producing_job(
        self, request: AliyunSubmitMediaProducingRequest
    ) -> AliyunSubmitAcknowledgement:
        if not isinstance(request, AliyunSubmitMediaProducingRequest):
            _reject()
        query = dict(request.query_parameters())
        headers = _signed_request_headers(
            self._credential,
            method="POST",
            host=request.endpoint,
            query=query,
            action=request.action,
            api_version=request.api_version,
            timestamp=self._timestamp(),
            nonce=self._nonce(),
        )
        try:
            response = await self._client.post(
                f"https://{request.endpoint}/?{canonical_query_string(query)}",
                headers=headers,
            )
        except httpx2.ConnectError:
            raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.NOT_DISPATCHED) from None
        except httpx2.HTTPError:
            raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.RESPONSE_LOST) from None
        if response.status_code != 200:
            raise _submit_status_failure(response.status_code)
        payload = _response_json(response)
        try:
            return AliyunSubmitAcknowledgement(
                vendor_job_id=payload["JobId"],  # type: ignore[arg-type]
                request_id=payload["RequestId"],  # type: ignore[arg-type]
            )
        except (KeyError, TypeError, ValueError):
            raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.RESPONSE_LOST) from None

    async def get_media_producing_job(
        self, request: AliyunGetMediaProducingRequest
    ) -> AliyunMediaProducingJobReport:
        if not isinstance(request, AliyunGetMediaProducingRequest):
            _reject()
        query = dict(request.query_parameters())
        headers = _signed_request_headers(
            self._credential,
            method="GET",
            host=request.endpoint,
            query=query,
            action=request.action,
            api_version=request.api_version,
            timestamp=self._timestamp(),
            nonce=self._nonce(),
        )
        try:
            response = await self._client.get(
                f"https://{request.endpoint}/?{canonical_query_string(query)}",
                headers=headers,
            )
        except httpx2.HTTPError:
            raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.RESPONSE_LOST) from None
        if response.status_code == 404:
            raise AliyunImsJobNotFound
        if response.status_code != 200:
            raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.RESPONSE_LOST)
        payload = _response_json(response)
        job = payload.get("MediaProducingJob")
        if not isinstance(job, dict):
            raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.RESPONSE_LOST)
        try:
            return AliyunMediaProducingJobReport(
                vendor_job_id=job["JobId"],
                status_token=job["Status"],
            )
        except (KeyError, TypeError, ValueError):
            raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.RESPONSE_LOST) from None


def _oss_url(credential: AliyunEditingCredential, object_key: str) -> str:
    return f"https://{credential.bucket}.oss-{credential.region.value}.aliyuncs.com/{object_key}"


def _oss_authorization(
    credential: AliyunEditingCredential,
    *,
    method: str,
    content_type: str,
    date: str,
    object_key: str,
) -> str:
    message = f"{method}\n\n{content_type}\n{date}\n/{credential.bucket}/{object_key}"
    digest = hmac.new(
        credential.access_key_secret.encode(),
        message.encode(),
        hashlib.sha1,
    ).digest()
    return f"OSS {credential.access_key_id}:{base64.b64encode(digest).decode()}"


@final
class AliyunOssEditingTransport:
    """Streaming same-region OSS staging and output transport."""

    __slots__ = ("_client", "_credential", "_http_date")

    def __init__(
        self,
        client: httpx2.AsyncClient,
        credential: AliyunEditingCredential,
        *,
        http_date: Callable[[], str] = _http_date,
    ) -> None:
        if (
            not isinstance(client, httpx2.AsyncClient)
            or not isinstance(credential, AliyunEditingCredential)
            or not callable(http_date)
        ):
            _reject()
        self._client = client
        self._credential = credential
        self._http_date = http_date

    def __repr__(self) -> str:
        return "AliyunOssEditingTransport(<redacted>)"

    def _headers(
        self,
        *,
        method: str,
        object_key: str,
        content_type: str = "",
    ) -> dict[str, str]:
        date = self._http_date()
        return {
            "Authorization": _oss_authorization(
                self._credential,
                method=method,
                content_type=content_type,
                date=date,
                object_key=object_key,
            ),
            "Date": date,
            **({"Content-Type": content_type} if content_type else {}),
        }

    def _require_ref(self, ref: AliyunOssObjectRef) -> None:
        if not isinstance(ref, AliyunOssObjectRef) or ref.bucket != self._credential.bucket:
            _reject()

    async def put_object(
        self,
        ref: AliyunOssObjectRef,
        chunks: AsyncIterator[bytes],
        *,
        content_length: int,
    ) -> None:
        self._require_ref(ref)
        if (
            not hasattr(chunks, "__aiter__")
            or type(content_length) is not int
            or content_length <= 0
        ):
            _reject()
        headers = self._headers(
            method="PUT",
            object_key=ref.object_key,
            content_type=_OSS_CONTENT_TYPE,
        )
        headers["Content-Length"] = str(content_length)
        try:
            response = await self._client.put(
                _oss_url(self._credential, ref.object_key),
                headers=headers,
                content=chunks,
            )
        except httpx2.HTTPError:
            raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.RESPONSE_LOST) from None
        if response.status_code != 200:
            raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.RESPONSE_LOST)

    def stream_object(self, ref: AliyunOssObjectRef) -> AsyncIterator[bytes]:
        self._require_ref(ref)

        async def _chunks() -> AsyncIterator[bytes]:
            try:
                async with self._client.stream(
                    "GET",
                    _oss_url(self._credential, ref.object_key),
                    headers=self._headers(method="GET", object_key=ref.object_key),
                ) as response:
                    if response.status_code == 404:
                        raise AliyunOssObjectMissing
                    if response.status_code != 200:
                        raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.RESPONSE_LOST)
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        if chunk:
                            yield chunk
            except (AliyunOssObjectMissing, AliyunImsTransportFailure):
                raise
            except httpx2.HTTPError:
                raise AliyunImsTransportFailure(
                    AliyunImsTransportFailureKind.RESPONSE_LOST
                ) from None

        return _chunks()

    async def delete_object(self, ref: AliyunOssObjectRef) -> None:
        self._require_ref(ref)
        try:
            response = await self._client.delete(
                _oss_url(self._credential, ref.object_key),
                headers=self._headers(method="DELETE", object_key=ref.object_key),
            )
        except httpx2.HTTPError:
            raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.RESPONSE_LOST) from None
        if response.status_code not in {200, 204, 404}:
            raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.RESPONSE_LOST)

    async def list_object_keys(self, bucket: AliyunOssBucketName, prefix: str) -> tuple[str, ...]:
        try:
            ref = AliyunOssObjectRef(bucket=bucket, object_key=prefix)
        except (TypeError, ValueError):
            _reject()
        self._require_ref(ref)
        try:
            response = await self._client.head(
                _oss_url(self._credential, ref.object_key),
                headers=self._headers(method="HEAD", object_key=ref.object_key),
            )
        except httpx2.HTTPError:
            raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.RESPONSE_LOST) from None
        if response.status_code == 200:
            return (prefix,)
        if response.status_code == 404:
            return ()
        raise AliyunImsTransportFailure(AliyunImsTransportFailureKind.RESPONSE_LOST)


__all__ = [
    "AliyunEditingCredential",
    "AliyunImsEditingTransport",
    "AliyunOssEditingTransport",
    "InvalidAliyunEditingTransport",
]
