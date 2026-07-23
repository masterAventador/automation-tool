"""Real HTTP gateway to the Bilibili open platform (httpx2, signature 2.0).

Production endpoints come from the locked PB-02 contract and must be HTTPS;
plain HTTP is only accepted for explicit loopback hosts so the Mock vertical
test can replay contract fixtures.  Any transport failure, timeout, or
unreadable response raises :class:`BilibiliGatewayUnreachable` so that the
application layer can apply its ambiguity semantics.  No real credentials are
bundled here and nothing is retried implicitly.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import dataclass
from dataclasses import fields as dataclass_fields
from typing import Final, Self
from urllib.parse import urlsplit

import httpx2

from automation_tool.control_plane.application.bilibili_archive_publishing import (
    BilibiliArchivePublishRejected,
    BilibiliGatewayUnreachable,
)
from automation_tool.control_plane.domain.bilibili_open_api import BilibiliOpenApiContract
from automation_tool.control_plane.infrastructure.bilibili.signing import (
    BilibiliApiCredentials,
    build_signed_headers,
)

_LOOPBACK_HOSTS: Final = frozenset({"127.0.0.1", "localhost"})
_DEFAULT_TIMEOUT_SECONDS: Final = 30.0
_RESOURCE_ID_PATTERN: Final = re.compile(r"^BV[0-9A-Za-z]{10}$")


def _validated_url(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise BilibiliArchivePublishRejected
    parts = urlsplit(value)
    if parts.scheme == "https" and parts.hostname:
        return value
    if parts.scheme == "http" and parts.hostname in _LOOPBACK_HOSTS:
        return value
    raise BilibiliArchivePublishRejected


@dataclass(frozen=True, slots=True)
class BilibiliGatewayEndpoints:
    """Absolute endpoint URLs; HTTPS-only except explicit loopback hosts."""

    upload_init_url: str
    part_upload_url: str
    upload_complete_url: str
    small_file_upload_url: str
    cover_upload_url: str
    archive_add_url: str

    def __post_init__(self) -> None:
        for field in dataclass_fields(self):
            _validated_url(getattr(self, field.name))

    @classmethod
    def from_contract(cls, contract: BilibiliOpenApiContract) -> Self:
        if not isinstance(contract, BilibiliOpenApiContract):
            raise BilibiliArchivePublishRejected
        return cls(
            upload_init_url=contract.upload_init_url,
            part_upload_url=contract.part_upload_url,
            upload_complete_url=contract.upload_complete_url,
            small_file_upload_url=contract.small_file_upload_url,
            cover_upload_url=contract.cover_upload_url,
            archive_add_url=contract.archive_add_url,
        )


class HttpxBilibiliOpenApiGateway:
    """One-shot signed requests; responses are returned unparsed to the caller."""

    def __init__(
        self,
        *,
        contract: BilibiliOpenApiContract,
        credentials: BilibiliApiCredentials,
        endpoints: BilibiliGatewayEndpoints | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if (
            not isinstance(contract, BilibiliOpenApiContract)
            or not isinstance(credentials, BilibiliApiCredentials)
            or (endpoints is not None and not isinstance(endpoints, BilibiliGatewayEndpoints))
            or not isinstance(timeout_seconds, int | float)
            or isinstance(timeout_seconds, bool)
            or not 0 < float(timeout_seconds) <= 600
        ):
            raise BilibiliArchivePublishRejected
        self._contract = contract
        self._credentials = credentials
        self._endpoints = endpoints or BilibiliGatewayEndpoints.from_contract(contract)
        self._client = httpx2.AsyncClient(timeout=float(timeout_seconds))

    async def aclose(self) -> None:
        await self._client.aclose()

    def _signed_headers(
        self, *, access_token: str, body: bytes, content_type: str
    ) -> dict[str, str]:
        return build_signed_headers(
            contract=self._contract,
            credentials=self._credentials,
            access_token=access_token,
            body=body,
            content_type=content_type,
            nonce=secrets.token_hex(16),
            timestamp=int(time.time()),
        )

    async def _post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str] | None = None,
        content: bytes = b"",
    ) -> object:
        try:
            response = await self._client.post(url, headers=headers, params=params, content=content)
        except httpx2.HTTPError:
            raise BilibiliGatewayUnreachable from None
        try:
            payload: object = json.loads(response.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise BilibiliGatewayUnreachable from None
        return payload

    async def _post_signed_json(
        self, url: str, *, access_token: str, body: dict[str, object]
    ) -> object:
        content = json.dumps(
            body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        headers = self._signed_headers(
            access_token=access_token, body=content, content_type="application/json"
        )
        return await self._post(url, headers=headers, content=content)

    async def upload_init(self, *, access_token: str, file_name: str, upload_type: str) -> object:
        return await self._post_signed_json(
            self._endpoints.upload_init_url,
            access_token=access_token,
            body={"file_name": file_name, "upload_type": upload_type},
        )

    async def upload_part(self, *, upload_token: str, part_number: int, payload: bytes) -> object:
        return await self._post(
            self._endpoints.part_upload_url,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/octet-stream",
            },
            params={"upload_token": upload_token, "part_number": str(part_number)},
            content=payload,
        )

    async def upload_complete(self, *, upload_token: str) -> object:
        return await self._post(
            self._endpoints.upload_complete_url,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            params={"upload_token": upload_token},
        )

    async def upload_small_file(self, *, upload_token: str, payload: bytes) -> object:
        return await self._post(
            self._endpoints.small_file_upload_url,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/octet-stream",
            },
            params={"upload_token": upload_token},
            content=payload,
        )

    async def upload_cover(self, *, access_token: str, file_name: str, payload: bytes) -> object:
        boundary = f"automation-tool-{secrets.token_hex(16)}"
        body = b"".join(
            (
                f"--{boundary}\r\n".encode(),
                (
                    f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
                ).encode(),
                b"Content-Type: application/octet-stream\r\n\r\n",
                payload,
                f"\r\n--{boundary}--\r\n".encode(),
            )
        )
        headers = self._signed_headers(
            access_token=access_token,
            body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        return await self._post(self._endpoints.cover_upload_url, headers=headers, content=body)

    async def archive_add(
        self,
        *,
        access_token: str,
        upload_token: str,
        submission: dict[str, object],
    ) -> object:
        content = json.dumps(
            submission, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        headers = self._signed_headers(
            access_token=access_token, body=content, content_type="application/json"
        )
        return await self._post(
            self._endpoints.archive_add_url,
            headers=headers,
            params={"upload_token": upload_token},
            content=content,
        )


@dataclass(frozen=True, slots=True)
class BilibiliQueryGatewayEndpoints:
    """Query endpoint URLs; HTTPS-only except explicit loopback hosts."""

    archive_view_url: str
    archive_viewlist_url: str

    def __post_init__(self) -> None:
        for field in dataclass_fields(self):
            _validated_url(getattr(self, field.name))

    @classmethod
    def from_contract(cls, contract: BilibiliOpenApiContract) -> Self:
        if not isinstance(contract, BilibiliOpenApiContract):
            raise BilibiliArchivePublishRejected
        return cls(
            archive_view_url=contract.archive_view_url,
            archive_viewlist_url=contract.archive_viewlist_url,
        )


class HttpxBilibiliArchiveQueryGateway:
    """Signed, read-only archive status queries; it cannot submit anything.

    Both official query endpoints are GET requests with an empty body, so the
    signature covers the constant empty-body MD5.  Responses are returned
    unparsed; transport failures and unreadable responses raise
    :class:`BilibiliGatewayUnreachable` for the reconciliation pacing logic.
    """

    def __init__(
        self,
        *,
        contract: BilibiliOpenApiContract,
        credentials: BilibiliApiCredentials,
        endpoints: BilibiliQueryGatewayEndpoints | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if (
            not isinstance(contract, BilibiliOpenApiContract)
            or not isinstance(credentials, BilibiliApiCredentials)
            or (endpoints is not None and not isinstance(endpoints, BilibiliQueryGatewayEndpoints))
            or not isinstance(timeout_seconds, int | float)
            or isinstance(timeout_seconds, bool)
            or not 0 < float(timeout_seconds) <= 600
        ):
            raise BilibiliArchivePublishRejected
        self._contract = contract
        self._credentials = credentials
        self._endpoints = endpoints or BilibiliQueryGatewayEndpoints.from_contract(contract)
        self._client = httpx2.AsyncClient(timeout=float(timeout_seconds))

    async def aclose(self) -> None:
        await self._client.aclose()

    def _require_token(self, access_token: object) -> str:
        if not isinstance(access_token, str) or not access_token:
            raise BilibiliArchivePublishRejected
        return access_token

    async def _get(self, url: str, *, access_token: str, params: dict[str, str]) -> object:
        headers = build_signed_headers(
            contract=self._contract,
            credentials=self._credentials,
            access_token=access_token,
            body=b"",
            content_type="application/json",
            nonce=secrets.token_hex(16),
            timestamp=int(time.time()),
        )
        try:
            response = await self._client.get(url, headers=headers, params=params)
        except httpx2.HTTPError:
            raise BilibiliGatewayUnreachable from None
        try:
            payload: object = json.loads(response.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise BilibiliGatewayUnreachable from None
        return payload

    async def archive_view(self, *, access_token: str, resource_id: str) -> object:
        token = self._require_token(access_token)
        if not isinstance(resource_id, str) or _RESOURCE_ID_PATTERN.fullmatch(resource_id) is None:
            raise BilibiliArchivePublishRejected
        return await self._get(
            self._endpoints.archive_view_url,
            access_token=token,
            params={"resource_id": resource_id},
        )

    async def archive_viewlist(
        self, *, access_token: str, page_number: int, page_size: int, status_filter: str
    ) -> object:
        token = self._require_token(access_token)
        if (
            type(page_number) is not int
            or page_number < 1
            or type(page_size) is not int
            or not 1 <= page_size <= self._contract.page_size_max
            or status_filter not in self._contract.archive_status_filters
        ):
            raise BilibiliArchivePublishRejected
        return await self._get(
            self._endpoints.archive_viewlist_url,
            access_token=token,
            params={
                "pn": str(page_number),
                "ps": str(page_size),
                "status": str(status_filter),
            },
        )


__all__ = [
    "BilibiliGatewayEndpoints",
    "BilibiliQueryGatewayEndpoints",
    "HttpxBilibiliArchiveQueryGateway",
    "HttpxBilibiliOpenApiGateway",
]
