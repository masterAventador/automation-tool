"""PB-03: Bilibili signature 2.0 header construction tests."""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

import pytest

from automation_tool.control_plane.application.bilibili_archive_publishing import (
    BilibiliArchivePublishRejected,
)
from automation_tool.control_plane.domain.bilibili_open_api import (
    load_bilibili_open_api_contract,
)
from automation_tool.control_plane.infrastructure.bilibili.signing import (
    BilibiliApiCredentials,
    build_signed_headers,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CONTRACT = load_bilibili_open_api_contract(
    REPOSITORY_ROOT / "contracts/publishing/bilibili-open-api.v1.json"
)

CREDENTIALS = BilibiliApiCredentials(
    client_id="fixture-client-id",
    app_secret="fixture-app-secret",
)


def test_signed_headers_follow_the_locked_signature_contract() -> None:
    body = b'{"file_name":"demo.mp4","upload_type":"0"}'
    headers = build_signed_headers(
        contract=CONTRACT,
        credentials=CREDENTIALS,
        access_token="fixture-access-token-000000000001",
        body=body,
        content_type="application/json",
        nonce="fixture-nonce-0001",
        timestamp=1_784_000_000,
    )
    signed = {
        "x-bili-accesskeyid": "fixture-client-id",
        "x-bili-content-md5": hashlib.md5(body).hexdigest(),
        "x-bili-signature-method": "HMAC-SHA256",
        "x-bili-signature-nonce": "fixture-nonce-0001",
        "x-bili-signature-version": "2.0",
        "x-bili-timestamp": "1784000000",
    }
    for key, value in signed.items():
        assert headers[key] == value
    signed_string = "\n".join(f"{key}:{signed[key]}" for key in sorted(signed))
    expected_signature = hmac.new(
        b"fixture-app-secret", signed_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    assert headers["Authorization"] == expected_signature
    assert headers["access-token"] == "fixture-access-token-000000000001"
    assert headers["Content-Type"] == "application/json"
    assert headers["Accept"] == "application/json"


def test_empty_body_uses_the_contract_md5_constant() -> None:
    headers = build_signed_headers(
        contract=CONTRACT,
        credentials=CREDENTIALS,
        access_token="fixture-access-token-000000000001",
        body=b"",
        content_type="application/json",
        nonce="fixture-nonce-0002",
        timestamp=1_784_000_000,
    )
    assert headers["x-bili-content-md5"] == "d41d8cd98f00b204e9800998ecf8427e"


@pytest.mark.parametrize(
    ("nonce", "timestamp", "access_token"),
    [
        ("", 1_784_000_000, "fixture-access-token"),
        ("nonce with space", 1_784_000_000, "fixture-access-token"),
        ("fixture-nonce", 0, "fixture-access-token"),
        ("fixture-nonce", -5, "fixture-access-token"),
        ("fixture-nonce", 1_784_000_000, ""),
        ("fixture-nonce", 1_784_000_000, " padded "),
    ],
)
def test_invalid_signing_inputs_are_rejected(nonce: str, timestamp: int, access_token: str) -> None:
    with pytest.raises(BilibiliArchivePublishRejected):
        build_signed_headers(
            contract=CONTRACT,
            credentials=CREDENTIALS,
            access_token=access_token,
            body=b"",
            content_type="application/json",
            nonce=nonce,
            timestamp=timestamp,
        )


@pytest.mark.parametrize(
    ("client_id", "app_secret"),
    [
        ("", "fixture-app-secret"),
        ("fixture-client-id", ""),
        (" padded ", "fixture-app-secret"),
        ("fixture-client-id", " padded "),
    ],
)
def test_invalid_credentials_are_rejected(client_id: str, app_secret: str) -> None:
    with pytest.raises(BilibiliArchivePublishRejected):
        BilibiliApiCredentials(client_id=client_id, app_secret=app_secret)


def test_credentials_repr_never_leaks_the_secret() -> None:
    assert "fixture-app-secret" not in repr(CREDENTIALS)
    assert "fixture-app-secret" not in str(CREDENTIALS)
