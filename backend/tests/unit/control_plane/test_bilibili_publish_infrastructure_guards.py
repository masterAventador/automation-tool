"""PB-03: fail-closed guards for the HTTP gateway and PostgreSQL store shells."""

from __future__ import annotations

import time
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from test_bilibili_archive_publishing import CONTRACT, NOW
from test_bilibili_archive_publishing_guards import valid_record_values

from automation_tool.control_plane.application.bilibili_archive_publishing import (
    BilibiliArchivePublishRejected,
    BilibiliArchivePublishUnavailable,
    BilibiliPublishAttemptRecord,
    BilibiliPublishPhase,
)
from automation_tool.control_plane.domain.bilibili_open_api import (
    BilibiliErrorCategory,
    BilibiliPlatformRejection,
    TokenRefresh,
)
from automation_tool.control_plane.domain.video_publishing import (
    PublishFailureCode,
    PublishJobId,
)
from automation_tool.control_plane.infrastructure.bilibili import (
    BilibiliApiCredentials,
    BilibiliGatewayEndpoints,
    BilibiliTokenSnapshot,
    HttpxBilibiliAccessTokenProvider,
    HttpxBilibiliOpenApiGateway,
)
from automation_tool.control_plane.infrastructure.bilibili import (
    token_provider as token_provider_module,
)
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.database.bilibili_publish_repository import (
    SqlAlchemyBilibiliArchivePublishStore,
)

CREDENTIALS = BilibiliApiCredentials(
    client_id="fixture-client-id",
    app_secret="fixture-app-secret",
)


def loopback_endpoints() -> BilibiliGatewayEndpoints:
    base = "http://127.0.0.1:9"
    return BilibiliGatewayEndpoints(
        upload_init_url=f"{base}/init",
        part_upload_url=f"{base}/part",
        upload_complete_url=f"{base}/complete",
        small_file_upload_url=f"{base}/small",
        cover_upload_url=f"{base}/cover",
        archive_add_url=f"{base}/add",
    )


def test_gateway_endpoints_reject_non_string_and_empty_urls() -> None:
    values = {
        "upload_init_url": "https://member.bilibili.com/init",
        "part_upload_url": "https://openupos.bilivideo.com/part",
        "upload_complete_url": "https://member.bilibili.com/complete",
        "small_file_upload_url": "https://openupos.bilivideo.com/small",
        "cover_upload_url": "https://member.bilibili.com/cover",
        "archive_add_url": "https://member.bilibili.com/add",
    }
    for key, bad in (("upload_init_url", None), ("archive_add_url", "")):
        broken: dict[str, Any] = {**values, key: bad}
        with pytest.raises(BilibiliArchivePublishRejected):
            BilibiliGatewayEndpoints(**broken)
    with pytest.raises(BilibiliArchivePublishRejected):
        BilibiliGatewayEndpoints.from_contract("not-a-contract")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_gateway_constructor_rejects_invalid_configuration() -> None:
    for kwargs in (
        {"contract": "contract", "credentials": CREDENTIALS},
        {"contract": CONTRACT, "credentials": "secret"},
        {"contract": CONTRACT, "credentials": CREDENTIALS, "timeout_seconds": 0},
        {"contract": CONTRACT, "credentials": CREDENTIALS, "timeout_seconds": True},
        {"contract": CONTRACT, "credentials": CREDENTIALS, "endpoints": "endpoints"},
    ):
        with pytest.raises(BilibiliArchivePublishRejected):
            HttpxBilibiliOpenApiGateway(**kwargs)  # type: ignore[arg-type]
    gateway = HttpxBilibiliOpenApiGateway(
        contract=CONTRACT, credentials=CREDENTIALS, endpoints=loopback_endpoints()
    )
    await gateway.aclose()


@pytest.mark.asyncio
async def test_access_token_is_refreshed_before_an_irreversible_call_can_see_near_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = HttpxBilibiliAccessTokenProvider(
        contract=CONTRACT,
        credentials=CREDENTIALS,
        access_token="near-expiry-access",
        refresh_token="near-expiry-refresh",
        expires_at_epoch_seconds=int(time.time()) + 60,
    )
    calls = 0

    async def refresh() -> str:
        nonlocal calls
        calls += 1
        return "rotated-before-dispatch"

    monkeypatch.setattr(provider, "refresh_access_token", refresh)
    try:
        assert await provider.current_access_token() == "rotated-before-dispatch"
        assert calls == 1
    finally:
        await provider.aclose()


class BrokenDatabase(Database):
    def __init__(self) -> None:
        pass

    def session(self) -> Any:
        raise RuntimeError("database driver exploded")


def broken_store() -> SqlAlchemyBilibiliArchivePublishStore:
    return SqlAlchemyBilibiliArchivePublishStore(BrokenDatabase())


def prepared_record() -> BilibiliPublishAttemptRecord:
    return BilibiliPublishAttemptRecord(**valid_record_values())


def test_store_constructor_rejects_non_database() -> None:
    with pytest.raises(BilibiliArchivePublishRejected):
        SqlAlchemyBilibiliArchivePublishStore("database")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_store_input_validation_rejects_before_touching_the_database() -> None:
    store = broken_store()
    job = PublishJobId.new()
    naive = datetime(2026, 7, 23, 2, 0)
    with pytest.raises(BilibiliArchivePublishRejected):
        await store.create_prepared("not-a-record")  # type: ignore[arg-type]
    with pytest.raises(BilibiliArchivePublishRejected):
        await store.load("not-a-job")  # type: ignore[arg-type]
    with pytest.raises(BilibiliArchivePublishRejected):
        await store.record_upload_token(job, "", NOW)
    with pytest.raises(BilibiliArchivePublishRejected):
        await store.record_upload_token(job, "token", naive)
    with pytest.raises(BilibiliArchivePublishRejected):
        await store.record_part_completed(job, "1", 8, NOW)  # type: ignore[arg-type]
    with pytest.raises(BilibiliArchivePublishRejected):
        await store.record_part_completed(job, 1, 0, NOW)
    with pytest.raises(BilibiliArchivePublishRejected):
        await store.record_cover_url(job, "http://not-https.example/cover.jpg", NOW)
    with pytest.raises(BilibiliArchivePublishRejected):
        await store.record_submitted(job, "", NOW)
    with pytest.raises(BilibiliArchivePublishRejected):
        await store.record_failed(job, "invalid_input", 123013, NOW)  # type: ignore[arg-type]
    with pytest.raises(BilibiliArchivePublishRejected):
        await store.record_failed(job, PublishFailureCode.INVALID_INPUT, 0, NOW)


@pytest.mark.asyncio
async def test_store_wraps_unexpected_database_errors_as_unavailable() -> None:
    store = broken_store()
    job = PublishJobId.new()
    record = prepared_record()
    with pytest.raises(BilibiliArchivePublishUnavailable):
        await store.create_prepared(record)
    with pytest.raises(BilibiliArchivePublishUnavailable):
        await store.load(job)
    with pytest.raises(BilibiliArchivePublishUnavailable):
        await store.record_upload_token(job, "fixture-upload-token", NOW)
    with pytest.raises(BilibiliArchivePublishUnavailable):
        await store.record_part_completed(job, 1, 8, NOW)
    with pytest.raises(BilibiliArchivePublishUnavailable):
        await store.completed_part_numbers(job)
    with pytest.raises(BilibiliArchivePublishUnavailable):
        await store.record_video_uploaded(job, NOW)
    with pytest.raises(BilibiliArchivePublishUnavailable):
        await store.record_cover_url(job, "https://example.com/cover.jpg", NOW)
    with pytest.raises(BilibiliArchivePublishUnavailable):
        await store.begin_archive_creation(job, NOW)
    with pytest.raises(BilibiliArchivePublishUnavailable):
        await store.record_submitted(job, "BV17B4y1s7R1", NOW)
    with pytest.raises(BilibiliArchivePublishUnavailable):
        await store.record_failed(job, PublishFailureCode.PLATFORM_ERROR, 4010, NOW)
    with pytest.raises(BilibiliArchivePublishUnavailable):
        await store.record_outcome_uncertain(job, NOW)


@pytest.mark.asyncio
async def test_store_rejects_non_prepared_record_on_create() -> None:
    record = BilibiliPublishAttemptRecord(
        **valid_record_values(
            phase=BilibiliPublishPhase.VIDEO_UPLOADED,
            video_uploaded_at=NOW,
            upload_token="fixture-upload-token",
        )
    )
    with pytest.raises(BilibiliArchivePublishRejected):
        await broken_store().create_prepared(record)


def _provider_arguments() -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "credentials": CREDENTIALS,
        "access_token": "an-access-token",
        "refresh_token": "a-refresh-token",
        "expires_at_epoch_seconds": int(time.time()) + 3_600,
    }


def test_the_token_provider_refuses_anything_it_cannot_sign_with() -> None:
    """Every one of these reaches the platform on an irreversible call."""
    cases: list[tuple[str, dict[str, Any]]] = [
        ("a contract that is not one", {"contract": "bilibili"}),
        ("credentials that are not credentials", {"credentials": None}),
        ("a timeout that is a bool", {"timeout_seconds": True}),
        ("a timeout that is not a number", {"timeout_seconds": "30"}),
        ("a timeout of zero", {"timeout_seconds": 0}),
        ("a timeout past the ceiling", {"timeout_seconds": 601}),
        ("an expiry that is not an int", {"expires_at_epoch_seconds": 1.0}),
        ("an expiry before the epoch", {"expires_at_epoch_seconds": 0}),
        ("an access token that is empty", {"access_token": ""}),
        ("an access token carrying whitespace", {"access_token": "two words"}),
        ("an access token with untrimmed space", {"access_token": " token"}),
        ("a refresh token that is not text", {"refresh_token": None}),
        ("a refresh token past the ceiling", {"refresh_token": "a" * 4_097}),
    ]
    for label, overrides in cases:
        with pytest.raises(BilibiliArchivePublishRejected):
            HttpxBilibiliAccessTokenProvider(**{**_provider_arguments(), **overrides})
        assert label


@pytest.mark.asyncio
async def test_a_token_that_is_not_near_expiry_is_used_as_it_is() -> None:
    """Rotation is single-use; refreshing a token that is still good spends one for nothing."""
    provider = HttpxBilibiliAccessTokenProvider(**_provider_arguments())
    try:
        assert await provider.current_access_token() == "an-access-token"
        assert repr(provider) == "HttpxBilibiliAccessTokenProvider(<redacted>)"
    finally:
        await provider.aclose()


def test_a_token_snapshot_must_describe_a_pair_that_could_exist() -> None:
    complete: dict[str, Any] = {
        "access_token": "an-access-token",
        "refresh_token": "a-refresh-token",
        "expires_at_epoch_seconds": 1_785_000_000,
        "revision": 0,
    }

    cases: list[tuple[str, dict[str, Any]]] = [
        ("an access token that is empty", {"access_token": ""}),
        ("a refresh token carrying whitespace", {"refresh_token": "two words"}),
        ("an expiry that is not an int", {"expires_at_epoch_seconds": 1.0}),
        ("an expiry before the epoch", {"expires_at_epoch_seconds": 0}),
        ("a revision that is not an int", {"revision": 1.0}),
        ("a negative revision", {"revision": -1}),
    ]
    for label, overrides in cases:
        with pytest.raises(BilibiliArchivePublishRejected):
            BilibiliTokenSnapshot(**{**complete, **overrides})
        assert label

    snapshot = BilibiliTokenSnapshot(**complete)
    assert "an-access-token" not in repr(snapshot)
    assert "a-refresh-token" not in repr(snapshot)
    assert "<redacted>" in repr(snapshot)


class _ScriptedClient:
    """Stands in for the HTTP client so no refresh ever leaves the process."""

    def __init__(self, *, content: bytes = b"{}", failure: BaseException | None = None) -> None:
        self.content = content
        self.failure = failure
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, **options: Any) -> Any:
        self.calls.append({"url": url, **options})
        if self.failure is not None:
            raise self.failure
        return SimpleNamespace(content=self.content)

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_a_refresh_rotates_the_pair_and_counts_the_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single-use rotation: the caller has to be able to see that it happened."""
    provider = HttpxBilibiliAccessTokenProvider(**_provider_arguments())
    client = _ScriptedClient()
    monkeypatch.setattr(provider, "_client", client)
    monkeypatch.setattr(
        token_provider_module,
        "parse_token_refresh",
        lambda _contract, _payload: TokenRefresh(
            access_token="rotated-access",
            refresh_token="rotated-refresh",
            expires_at_epoch_seconds=int(time.time()) + 7_200,
        ),
    )

    assert await provider.refresh_access_token() == "rotated-access"

    snapshot = provider.snapshot()
    assert snapshot.access_token == "rotated-access"
    assert snapshot.refresh_token == "rotated-refresh"
    assert snapshot.revision == 1
    # The secret never rides in the URL or the headers.
    sent = client.calls[0]
    assert "rotated" not in sent["url"]
    assert sent["data"]["grant_type"] == CONTRACT.grant_type_refresh_token


@pytest.mark.asyncio
async def test_a_refresh_that_cannot_be_completed_is_unavailable_not_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retryable and not-retryable differ; a gateway that did not answer is the first."""
    import httpx2

    cases: list[tuple[str, BaseException | None, bytes]] = [
        ("the gateway did not answer", httpx2.HTTPError("connection reset"), b"{}"),
        ("bytes that are not utf-8", None, b"\xff\xfe"),
        ("a body that will not parse", None, b"{not json"),
    ]
    for label, failure, content in cases:
        provider = HttpxBilibiliAccessTokenProvider(**_provider_arguments())
        monkeypatch.setattr(
            provider, "_client", _ScriptedClient(content=content, failure=failure)
        )
        with pytest.raises(BilibiliArchivePublishUnavailable):
            await provider.refresh_access_token()
        assert label


@pytest.mark.asyncio
async def test_a_refresh_the_platform_refused_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A platform rejection and an unreadable answer both mean: no usable token."""
    for label, parsed in [
        (
            "the platform said no",
            BilibiliPlatformRejection(
                code=-101,
                category=BilibiliErrorCategory.AUTH_REJECTED,
                failure_code=PublishFailureCode.PLATFORM_ERROR,
            ),
        ),
        ("something that is not a refresh", object()),
    ]:
        provider = HttpxBilibiliAccessTokenProvider(**_provider_arguments())
        monkeypatch.setattr(provider, "_client", _ScriptedClient())
        monkeypatch.setattr(
            token_provider_module,
            "parse_token_refresh",
            lambda _contract, _payload, _parsed=parsed: _parsed,
        )
        with pytest.raises(BilibiliArchivePublishUnavailable):
            await provider.refresh_access_token()
        assert label
