"""PB-03: fail-closed guards for the HTTP gateway and PostgreSQL store shells."""

from __future__ import annotations

from datetime import datetime
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
from automation_tool.control_plane.domain.video_publishing import (
    PublishFailureCode,
    PublishJobId,
)
from automation_tool.control_plane.infrastructure.bilibili import (
    BilibiliApiCredentials,
    BilibiliGatewayEndpoints,
    HttpxBilibiliOpenApiGateway,
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
