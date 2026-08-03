"""In-memory OAuth token rotation for the Bilibili open platform.

The desktop remains the durable owner of the tokens.  A publishing API request
creates one short-lived provider, and a refresh returns the rotated pair to the
desktop before the provider is discarded.  Neither token is written to the
Control Plane database or included in repr/log output.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Final

import httpx2

from automation_tool.control_plane.application.bilibili_archive_publishing import (
    BilibiliArchivePublishRejected,
    BilibiliArchivePublishUnavailable,
)
from automation_tool.control_plane.domain.bilibili_open_api import (
    BilibiliOpenApiContract,
    BilibiliPlatformRejection,
    TokenRefresh,
    parse_token_refresh,
)
from automation_tool.control_plane.infrastructure.bilibili.signing import (
    BilibiliApiCredentials,
)

_DEFAULT_TIMEOUT_SECONDS: Final = 30.0
_MAX_TOKEN_LENGTH: Final = 4096
_REFRESH_SKEW_SECONDS: Final = 5 * 60


def _secret(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= _MAX_TOKEN_LENGTH
        or value != value.strip()
        or any(character.isspace() for character in value)
    ):
        raise BilibiliArchivePublishRejected
    return value


@dataclass(frozen=True, slots=True, repr=False)
class BilibiliTokenSnapshot:
    """The current pair and whether this provider rotated it."""

    access_token: str
    refresh_token: str
    expires_at_epoch_seconds: int
    revision: int

    def __post_init__(self) -> None:
        _secret(self.access_token)
        _secret(self.refresh_token)
        if (
            type(self.expires_at_epoch_seconds) is not int
            or self.expires_at_epoch_seconds < 1
            or type(self.revision) is not int
            or self.revision < 0
        ):
            raise BilibiliArchivePublishRejected

    def __repr__(self) -> str:
        return (
            "BilibiliTokenSnapshot("
            f"expires_at_epoch_seconds={self.expires_at_epoch_seconds}, "
            f"revision={self.revision}, <redacted>)"
        )


class HttpxBilibiliAccessTokenProvider:
    """One request-scoped token provider with single-use refresh rotation."""

    def __init__(
        self,
        *,
        contract: BilibiliOpenApiContract,
        credentials: BilibiliApiCredentials,
        access_token: str,
        refresh_token: str,
        expires_at_epoch_seconds: int,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if (
            not isinstance(contract, BilibiliOpenApiContract)
            or not isinstance(credentials, BilibiliApiCredentials)
            or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int | float)
            or not 0 < float(timeout_seconds) <= 600
            or type(expires_at_epoch_seconds) is not int
            or expires_at_epoch_seconds < 1
        ):
            raise BilibiliArchivePublishRejected
        self._contract = contract
        self._credentials = credentials
        self._access_token = _secret(access_token)
        self._refresh_token = _secret(refresh_token)
        self._expires_at_epoch_seconds = expires_at_epoch_seconds
        self._revision = 0
        self._client = httpx2.AsyncClient(timeout=float(timeout_seconds))

    def __repr__(self) -> str:
        return "HttpxBilibiliAccessTokenProvider(<redacted>)"

    async def aclose(self) -> None:
        await self._client.aclose()

    async def current_access_token(self) -> str:
        if self._expires_at_epoch_seconds <= int(time.time()) + _REFRESH_SKEW_SECONDS:
            return await self.refresh_access_token()
        return self._access_token

    async def refresh_access_token(self) -> str:
        try:
            response = await self._client.post(
                self._contract.refresh_token_url,
                data={
                    "client_id": self._credentials.client_id,
                    "client_secret": self._credentials.app_secret,
                    "grant_type": self._contract.grant_type_refresh_token,
                    "refresh_token": self._refresh_token,
                },
                headers={"Accept": "application/json"},
            )
            payload: object = json.loads(response.content.decode("utf-8"))
            parsed = parse_token_refresh(self._contract, payload)
        except (
            httpx2.HTTPError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValueError,
        ):
            raise BilibiliArchivePublishUnavailable from None
        if isinstance(parsed, BilibiliPlatformRejection):
            raise BilibiliArchivePublishUnavailable
        if not isinstance(parsed, TokenRefresh):
            raise BilibiliArchivePublishUnavailable
        self._access_token = parsed.access_token
        self._refresh_token = parsed.refresh_token
        self._expires_at_epoch_seconds = parsed.expires_at_epoch_seconds
        self._revision += 1
        return self._access_token

    def snapshot(self) -> BilibiliTokenSnapshot:
        return BilibiliTokenSnapshot(
            access_token=self._access_token,
            refresh_token=self._refresh_token,
            expires_at_epoch_seconds=self._expires_at_epoch_seconds,
            revision=self._revision,
        )


__all__ = ["BilibiliTokenSnapshot", "HttpxBilibiliAccessTokenProvider"]
