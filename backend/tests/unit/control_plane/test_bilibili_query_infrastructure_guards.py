"""PB-04: fail-closed guards for the query-only HTTP gateway shell."""

from __future__ import annotations

from typing import Any

import pytest
from test_bilibili_archive_publishing import CONTRACT

from automation_tool.control_plane.application.bilibili_archive_publishing import (
    BilibiliArchivePublishRejected,
)
from automation_tool.control_plane.infrastructure.bilibili import (
    BilibiliApiCredentials,
    BilibiliQueryGatewayEndpoints,
    HttpxBilibiliArchiveQueryGateway,
)

CREDENTIALS = BilibiliApiCredentials(
    client_id="fixture-client-id",
    app_secret="fixture-app-secret",
)


def loopback_endpoints() -> BilibiliQueryGatewayEndpoints:
    base = "http://127.0.0.1:9"
    return BilibiliQueryGatewayEndpoints(
        archive_view_url=f"{base}/view",
        archive_viewlist_url=f"{base}/viewlist",
    )


def test_query_endpoints_only_accept_https_or_explicit_loopback() -> None:
    endpoints = BilibiliQueryGatewayEndpoints.from_contract(CONTRACT)
    assert endpoints.archive_view_url == CONTRACT.archive_view_url
    assert endpoints.archive_viewlist_url == CONTRACT.archive_viewlist_url
    assert loopback_endpoints().archive_view_url.startswith("http://127.0.0.1")
    for broken in (
        {"archive_view_url": "http://member.bilibili.com/view"},
        {"archive_view_url": ""},
        {"archive_view_url": None},
        {"archive_viewlist_url": "ftp://member.bilibili.com/viewlist"},
    ):
        values: dict[str, Any] = {
            "archive_view_url": "https://member.bilibili.com/view",
            "archive_viewlist_url": "https://member.bilibili.com/viewlist",
            **broken,
        }
        with pytest.raises(BilibiliArchivePublishRejected):
            BilibiliQueryGatewayEndpoints(**values)
    with pytest.raises(BilibiliArchivePublishRejected):
        BilibiliQueryGatewayEndpoints.from_contract("not-a-contract")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_query_gateway_constructor_rejects_invalid_configuration() -> None:
    for kwargs in (
        {"contract": "contract", "credentials": CREDENTIALS},
        {"contract": CONTRACT, "credentials": "secret"},
        {"contract": CONTRACT, "credentials": CREDENTIALS, "timeout_seconds": 0},
        {"contract": CONTRACT, "credentials": CREDENTIALS, "timeout_seconds": True},
        {"contract": CONTRACT, "credentials": CREDENTIALS, "endpoints": "endpoints"},
    ):
        with pytest.raises(BilibiliArchivePublishRejected):
            HttpxBilibiliArchiveQueryGateway(**kwargs)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_query_gateway_validates_untrusted_request_inputs() -> None:
    gateway = HttpxBilibiliArchiveQueryGateway(
        contract=CONTRACT, credentials=CREDENTIALS, endpoints=loopback_endpoints()
    )
    try:
        for resource_id in ("", "av170001", "BV17B4y1s7R1/extra", None, 42):
            with pytest.raises(BilibiliArchivePublishRejected):
                await gateway.archive_view(
                    access_token="fixture-access-token-000000000001",
                    resource_id=resource_id,  # type: ignore[arg-type]
                )
        with pytest.raises(BilibiliArchivePublishRejected):
            await gateway.archive_view(access_token="", resource_id="BV17B4y1s7R1")
        bad_list_inputs: tuple[tuple[Any, Any, Any], ...] = (
            (0, 50, "all"),
            (1, 0, "all"),
            (1, CONTRACT.page_size_max + 1, "all"),
            (1, 50, "everything"),
            ("1", 50, "all"),
            (1, 50, None),
        )
        for page_number, page_size, status_filter in bad_list_inputs:
            with pytest.raises(BilibiliArchivePublishRejected):
                await gateway.archive_viewlist(
                    access_token="fixture-access-token-000000000001",
                    page_number=page_number,
                    page_size=page_size,
                    status_filter=status_filter,
                )
    finally:
        await gateway.aclose()


def test_query_gateway_has_no_submission_surface() -> None:
    assert not hasattr(HttpxBilibiliArchiveQueryGateway, "archive_add")
    assert not hasattr(HttpxBilibiliArchiveQueryGateway, "upload_init")
