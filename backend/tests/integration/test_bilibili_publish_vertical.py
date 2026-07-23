"""PB-03: Mock vertical chain — real service, httpx2 gateway, PostgreSQL store.

The Bilibili platform is replayed by a scripted loopback HTTP server serving
the locked PB-02 contract fixtures; no real credentials or real platform calls
are involved.  Everything else is real: temp-file material, streaming digests,
signature 2.0 headers, the httpx2 client, and the PostgreSQL attempt store.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete

from automation_tool.control_plane.application.bilibili_archive_publishing import (
    BilibiliArchiveFields,
    BilibiliArchiveOutcomeUncertain,
    BilibiliArchivePublishRejected,
    BilibiliArchivePublishService,
    BilibiliGatewayUnreachable,
    BilibiliPublishPhase,
)
from automation_tool.control_plane.domain.bilibili_open_api import (
    load_bilibili_open_api_contract,
)
from automation_tool.control_plane.domain.video_publishing import PublishJobId
from automation_tool.control_plane.infrastructure.bilibili import (
    BilibiliApiCredentials,
    BilibiliGatewayEndpoints,
    FilesystemBilibiliCoverSource,
    FilesystemBilibiliPublishMaterial,
    HttpxBilibiliOpenApiGateway,
)
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.database.bilibili_publish_repository import (
    SqlAlchemyBilibiliArchivePublishStore,
)
from automation_tool.control_plane.infrastructure.database.schema import (
    bilibili_publish_attempts,
    bilibili_upload_parts,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CONTRACT = load_bilibili_open_api_contract(
    REPOSITORY_ROOT / "contracts/publishing/bilibili-open-api.v1.json"
)
FIXTURE_ROOT = REPOSITORY_ROOT / "contracts/publishing/fixtures/bilibili-open-api-v1"

CHUNKED_SIZE = CONTRACT.small_file_max_bytes + CONTRACT.part_size_bytes + 5
SIGNED_HEADER_NAMES = (
    "x-bili-accesskeyid",
    "x-bili-content-md5",
    "x-bili-signature-method",
    "x-bili-signature-nonce",
    "x-bili-signature-version",
    "x-bili-timestamp",
    "authorization",
    "access-token",
)

CREDENTIALS = BilibiliApiCredentials(
    client_id="fixture-client-id",
    app_secret="fixture-app-secret",
)


class FixtureTokenProvider:
    async def current_access_token(self) -> str:
        return "fixture-access-token-000000000001"

    async def refresh_access_token(self) -> str:
        return "fixture-access-token-000000000002"


def _fixture(name: str) -> dict[str, Any]:
    document = json.loads((FIXTURE_ROOT / f"{name}.json").read_text(encoding="utf-8"))
    payload: dict[str, Any] = document["payload"]
    return payload


class RecordedRequest:
    def __init__(
        self,
        route: str,
        headers: dict[str, str],
        params: dict[str, list[str]],
        body_length: int,
    ) -> None:
        self.route = route
        self.headers = headers
        self.params = params
        self.body_length = body_length


class MockBilibiliServer:
    """Scripted loopback replay of the open-platform HTTP surface."""

    def __init__(self) -> None:
        self.scripts: dict[str, deque[tuple[str, dict[str, Any] | float | None]]] = {}
        self.requests: list[RecordedRequest] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *arguments: object) -> None:
                del format, arguments

            def do_POST(self) -> None:
                parts = urlsplit(self.path)
                route = parts.path
                length = int(self.headers.get("Content-Length", "0"))
                if length:
                    self.rfile.read(length)
                server.requests.append(
                    RecordedRequest(
                        route,
                        {key.lower(): value for key, value in self.headers.items()},
                        parse_qs(parts.query),
                        length,
                    )
                )
                queue = server.scripts.get(route)
                if not queue:
                    self.send_response(500)
                    self.end_headers()
                    return
                action, detail = queue.popleft()
                if action == "drop":
                    self.connection.close()
                    return
                if action == "hang":
                    assert isinstance(detail, int | float)
                    time.sleep(float(detail))
                    self.connection.close()
                    return
                assert action == "json" and isinstance(detail, dict)
                body = json.dumps(detail, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}"

    def script(self, route: str, action: str, detail: dict[str, Any] | float | None) -> None:
        self.scripts.setdefault(route, deque()).append((action, detail))

    def route_count(self, route: str) -> int:
        return sum(1 for request in self.requests if request.route == route)

    def close(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=10)
        self._server.server_close()


def endpoints_for(server: MockBilibiliServer) -> BilibiliGatewayEndpoints:
    base = server.base_url
    return BilibiliGatewayEndpoints(
        upload_init_url=f"{base}/init",
        part_upload_url=f"{base}/part",
        upload_complete_url=f"{base}/complete",
        small_file_upload_url=f"{base}/small",
        cover_upload_url=f"{base}/cover",
        archive_add_url=f"{base}/add",
    )


def write_sparse_video(root: Path, name: str, size: int) -> None:
    with (root / name).open("wb") as handle:
        handle.truncate(size)


def submission_fields() -> BilibiliArchiveFields:
    return BilibiliArchiveFields(
        title="契约样例一分钟看懂分片上传",
        tid=21,
        tag="科技,教程",
        copyright=1,
        description="样例描述",
        source=None,
        no_reprint=0,
    )


async def reset_data(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(bilibili_upload_parts))
        await session.execute(delete(bilibili_publish_attempts))


def test_gateway_endpoints_reject_plain_http_outside_loopback() -> None:
    with pytest.raises(BilibiliArchivePublishRejected):
        BilibiliGatewayEndpoints(
            upload_init_url="http://member.bilibili.com/init",
            part_upload_url="https://openupos.bilivideo.com/part",
            upload_complete_url="https://member.bilibili.com/complete",
            small_file_upload_url="https://openupos.bilivideo.com/small",
            cover_upload_url="https://member.bilibili.com/cover",
            archive_add_url="https://member.bilibili.com/add",
        )
    contract_endpoints = BilibiliGatewayEndpoints.from_contract(CONTRACT)
    assert contract_endpoints.archive_add_url == CONTRACT.archive_add_url


@pytest.mark.asyncio
async def test_chunked_breakpoint_resume_cover_and_single_creation(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
    tmp_path: Path,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    server = MockBilibiliServer()
    gateway = HttpxBilibiliOpenApiGateway(
        contract=CONTRACT,
        credentials=CREDENTIALS,
        endpoints=endpoints_for(server),
        timeout_seconds=5.0,
    )
    try:
        await reset_data(database)
        write_sparse_video(tmp_path, "feature.mp4", CHUNKED_SIZE)
        material_reader = FilesystemBilibiliPublishMaterial(
            root=tmp_path, file_name="feature.mp4", duration_seconds=1800
        )
        (tmp_path / "cover.png").write_bytes(b"png-bytes")

        store = SqlAlchemyBilibiliArchivePublishStore(database)
        service = BilibiliArchivePublishService(
            contract=CONTRACT,
            store=store,
            gateway=gateway,
            token_provider=FixtureTokenProvider(),
        )
        publish_job_id = PublishJobId.new()
        material = await service.validate_material(material_reader)
        preparation = await service.prepare(
            publish_job_id,
            material=material,
            fields=submission_fields(),
            with_cover=True,
        )
        assert preparation.record.part_count == 14

        server.script("/init", "json", _fixture("response-upload-init-valid"))
        server.script("/part", "json", _fixture("response-part-upload-valid"))
        server.script("/part", "json", _fixture("response-part-upload-valid"))
        server.script("/part", "drop", None)
        with pytest.raises(BilibiliGatewayUnreachable):
            await service.upload_video(publish_job_id, material_reader)

        interrupted = await store.load(publish_job_id)
        assert interrupted is not None
        assert interrupted.phase is BilibiliPublishPhase.PREPARED
        assert await store.completed_part_numbers(publish_job_id) == frozenset({1, 2})

        restarted_store = SqlAlchemyBilibiliArchivePublishStore(database)
        restarted_service = BilibiliArchivePublishService(
            contract=CONTRACT,
            store=restarted_store,
            gateway=gateway,
            token_provider=FixtureTokenProvider(),
        )
        for _ in range(12):
            server.script("/part", "json", _fixture("response-part-upload-valid"))
        server.script("/complete", "json", _fixture("response-upload-complete-valid"))
        record = await restarted_service.upload_video(publish_job_id, material_reader)
        assert record.phase is BilibiliPublishPhase.VIDEO_UPLOADED
        assert server.route_count("/init") == 1
        part_numbers = [
            request.params["part_number"][0]
            for request in server.requests
            if request.route == "/part"
        ]
        assert part_numbers == [str(number) for number in [1, 2, 3, *range(3, 15)]]

        server.script("/cover", "json", _fixture("response-cover-upload-valid"))
        cover_url = await restarted_service.upload_cover(
            publish_job_id,
            FilesystemBilibiliCoverSource(root=tmp_path, file_name="cover.png"),
        )
        assert cover_url.startswith("https://")

        server.script("/add", "json", _fixture("response-archive-add-valid"))
        receipt = await restarted_service.create_archive(publish_job_id)
        assert receipt.resource_id == "BV17B4y1s7R1"
        assert receipt.replayed is False

        stored = await restarted_store.load(publish_job_id)
        assert stored is not None
        assert stored.phase is BilibiliPublishPhase.SUBMITTED
        assert stored.resource_id == "BV17B4y1s7R1"
        assert stored.request_digest == receipt.request_digest

        replay = await restarted_service.create_archive(publish_job_id)
        assert replay.replayed is True
        assert server.route_count("/add") == 1

        for request in server.requests:
            if request.route in {"/init", "/add", "/cover"}:
                for name in SIGNED_HEADER_NAMES:
                    assert name in request.headers, (request.route, name)
                assert "fixture-app-secret" not in json.dumps(request.headers)
    finally:
        await gateway.aclose()
        server.close()
        await database.close()


@pytest.mark.asyncio
async def test_lost_creation_response_settles_uncertain_and_never_resends(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
    tmp_path: Path,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    server = MockBilibiliServer()
    gateway = HttpxBilibiliOpenApiGateway(
        contract=CONTRACT,
        credentials=CREDENTIALS,
        endpoints=endpoints_for(server),
        timeout_seconds=1.0,
    )
    try:
        await reset_data(database)
        (tmp_path / "demo.mp4").write_bytes(b"tiny-demo-video-bytes")
        material_reader = FilesystemBilibiliPublishMaterial(
            root=tmp_path, file_name="demo.mp4", duration_seconds=30
        )
        store = SqlAlchemyBilibiliArchivePublishStore(database)
        service = BilibiliArchivePublishService(
            contract=CONTRACT,
            store=store,
            gateway=gateway,
            token_provider=FixtureTokenProvider(),
        )
        publish_job_id = PublishJobId.new()
        material = await service.validate_material(material_reader)
        await service.prepare(
            publish_job_id,
            material=material,
            fields=submission_fields(),
            with_cover=False,
        )
        server.script("/init", "json", _fixture("response-upload-init-valid"))
        server.script("/small", "json", _fixture("response-part-upload-valid"))
        record = await service.upload_video(publish_job_id, material_reader)
        assert record.phase is BilibiliPublishPhase.VIDEO_UPLOADED
        assert server.route_count("/part") == 0

        server.script("/add", "hang", 5.0)
        with pytest.raises(BilibiliArchiveOutcomeUncertain):
            await service.create_archive(publish_job_id)

        stored = await store.load(publish_job_id)
        assert stored is not None
        assert stored.phase is BilibiliPublishPhase.OUTCOME_UNCERTAIN
        assert stored.resource_id is None

        with pytest.raises(BilibiliArchivePublishRejected):
            await service.create_archive(publish_job_id)
        assert server.route_count("/add") == 1
    finally:
        await gateway.aclose()
        server.close()
        await database.close()


@pytest.mark.asyncio
async def test_unreadable_platform_response_maps_to_gateway_unreachable() -> None:
    server = MockBilibiliServer()
    gateway = HttpxBilibiliOpenApiGateway(
        contract=CONTRACT,
        credentials=CREDENTIALS,
        endpoints=endpoints_for(server),
        timeout_seconds=2.0,
    )
    try:
        with pytest.raises(BilibiliGatewayUnreachable):
            await gateway.upload_complete(upload_token="fixture-upload-token-000000000000")
    finally:
        await gateway.aclose()
        server.close()
