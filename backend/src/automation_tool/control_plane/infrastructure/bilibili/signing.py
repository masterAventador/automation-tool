"""Signature 2.0 header construction for the Bilibili open platform.

Secrets are used only in memory; credentials never enter repr, logs, or
persistence.  The signed-header set and algorithm come from the locked PB-02
contract and any drift fails closed.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Final

from automation_tool.control_plane.application.bilibili_archive_publishing import (
    BilibiliArchivePublishRejected,
)
from automation_tool.control_plane.domain.bilibili_open_api import BilibiliOpenApiContract

_SIGNATURE_METHOD: Final = "HMAC-SHA256"
_EXPECTED_SIGNED_HEADERS: Final = (
    "x-bili-accesskeyid",
    "x-bili-content-md5",
    "x-bili-signature-method",
    "x-bili-signature-nonce",
    "x-bili-signature-version",
    "x-bili-timestamp",
)


def _require_compact_text(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or " " in value:
        raise BilibiliArchivePublishRejected
    return value


@dataclass(frozen=True, slots=True, repr=False)
class BilibiliApiCredentials:
    """Application credentials for signing; never persisted or logged."""

    client_id: str
    app_secret: str

    def __post_init__(self) -> None:
        _require_compact_text(self.client_id)
        _require_compact_text(self.app_secret)

    def __repr__(self) -> str:
        return "BilibiliApiCredentials(<redacted>)"


def build_signed_headers(
    *,
    contract: BilibiliOpenApiContract,
    credentials: BilibiliApiCredentials,
    access_token: str,
    body: bytes,
    content_type: str,
    nonce: str,
    timestamp: int,
) -> dict[str, str]:
    """Build the six signed ``x-bili-*`` headers plus authorization headers."""
    if (
        not isinstance(contract, BilibiliOpenApiContract)
        or not isinstance(credentials, BilibiliApiCredentials)
        or not isinstance(body, bytes)
        or not isinstance(content_type, str)
        or not content_type
        or type(timestamp) is not int
        or timestamp < 1
        or contract.signature_algorithm != _SIGNATURE_METHOD
        or tuple(sorted(contract.signed_headers)) != _EXPECTED_SIGNED_HEADERS
    ):
        raise BilibiliArchivePublishRejected
    _require_compact_text(access_token)
    _require_compact_text(nonce)
    signed: dict[str, str] = {
        "x-bili-accesskeyid": credentials.client_id,
        "x-bili-content-md5": hashlib.md5(body).hexdigest(),
        "x-bili-signature-method": _SIGNATURE_METHOD,
        "x-bili-signature-nonce": nonce,
        "x-bili-signature-version": contract.signature_version,
        "x-bili-timestamp": str(timestamp),
    }
    signed_string = "\n".join(f"{key}:{signed[key]}" for key in sorted(signed))
    signature = hmac.new(
        credentials.app_secret.encode("utf-8"),
        signed_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        **signed,
        "Authorization": signature,
        "access-token": access_token,
        "Content-Type": content_type,
        "Accept": "application/json",
    }


__all__ = ["BilibiliApiCredentials", "build_signed_headers"]
