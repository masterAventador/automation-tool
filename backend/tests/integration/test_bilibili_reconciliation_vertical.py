"""PB-04: Mock vertical chain — real service, httpx2 query gateway, PostgreSQL.

The platform is replayed by a scripted loopback HTTP server (POST for the
PB-03 publishing surface, GET for the PB-04 query surface).  Everything else
is real: signature 2.0 headers, the httpx2 clients, the PostgreSQL attempt and
reconciliation stores, and process-restart recovery through fresh instances.
No real credentials or real platform calls are involved, and the tests assert
that reconciliation never re-submits an archive.
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
    BilibiliArchivePublishService,
    BilibiliPublishPhase,
)
from automation_tool.control_plane.application.bilibili_archive_reconciliation import (
    BilibiliArchiveReconciliationService,
    BilibiliReconciliationDecision,
    BilibiliReconciliationOutcome,
)
from automation_tool.control_plane.domain.bilibili_open_api import (
    load_bilibili_open_api_contract,
)
from automation_tool.control_plane.domain.video_publishing import (
    PublishFailureCode,
    PublishJobId,
    PublishJobStatus,
)
from automation_tool.control_plane.infrastructure.bilibili import (
    BilibiliApiCredentials,
    BilibiliGatewayEndpoints,
    BilibiliQueryGatewayEndpoints,
    FilesystemBilibiliPublishMaterial,
    HttpxBilibiliArchiveQueryGateway,
    HttpxBilibiliOpenApiGateway,
)
from automation_tool.control_plane.infrastructure.database import Database
from automation_tool.control_plane.infrastructure.database.bilibili_publish_repository import (
    SqlAlchemyBilibiliArchivePublishStore,
    SqlAlchemyBilibiliReconciliationStore,
)
from automation_tool.control_plane.infrastructure.database.schema import (
    bilibili_publish_attempts,
    bilibili_publish_reconciliations,
    bilibili_upload_parts,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CONTRACT = load_bilibili_open_api_contract(
    REPOSITORY_ROOT / "contracts/publishing/bilibili-open-api.v1.json"
)
FIXTURE_ROOT = REPOSITORY_ROOT / "contracts/publishing/fixtures/bilibili-open-api-v1"

TITLE = "契约样例一分钟看懂分片上传"
RESOURCE_ID = "BV17B4y1s7R1"

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


def _archive_item(
    *,
    resource_id: str = RESOURCE_ID,
    title: str = TITLE,
    state: int = -30,
    state_desc: str = "审核中",
    reject_reason: str = "",
    ctime: int | None = None,
    ptime: int = 0,
) -> dict[str, Any]:
    return {
        "resource_id": resource_id,
        "title": title,
        "cover": "https://i1.hdslb.com/bfs/archive/fixture.jpg",
        "tid": 21,
        "no_reprint": 0,
        "desc": "样例描述",
        "tag": "科技,教程",
        "copyright": 1,
        "ctime": int(time.time()) if ctime is None else ctime,
        "ptime": ptime,
        "addit_info": {
            "state": state,
            "state_desc": state_desc,
            "reject_reason": reject_reason,
        },
        "video_info": {},
    }


def _view_payload(**overrides: Any) -> dict[str, Any]:
    return {"code": 0, "message": "0", "ttl": 1, "data": _archive_item(**overrides)}


def _viewlist_payload(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "code": 0,
        "message": "0",
        "ttl": 1,
        "data": {
            "list": items,
            "page": {"pn": 1, "ps": CONTRACT.page_size_max, "total": len(items)},
        },
    }


class MockBilibiliServer:
    """Scripted loopback replay of the publish (POST) and query (GET) surface."""

    def __init__(self) -> None:
        self.scripts: dict[str, deque[tuple[str, dict[str, Any] | float | None]]] = {}
        self.requests: list[tuple[str, str, dict[str, list[str]]]] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *arguments: object) -> None:
                del format, arguments

            def _serve(self, method: str) -> None:
                parts = urlsplit(self.path)
                route = parts.path
                length = int(self.headers.get("Content-Length", "0"))
                if length:
                    self.rfile.read(length)
                server.requests.append((method, route, parse_qs(parts.query)))
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

            def do_POST(self) -> None:
                self._serve("POST")

            def do_GET(self) -> None:
                self._serve("GET")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}"

    def script(self, route: str, action: str, detail: dict[str, Any] | float | None) -> None:
        self.scripts.setdefault(route, deque()).append((action, detail))

    def route_count(self, route: str) -> int:
        return sum(1 for _, request_route, _ in self.requests if request_route == route)

    def close(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=10)
        self._server.server_close()


def publish_endpoints(server: MockBilibiliServer) -> BilibiliGatewayEndpoints:
    base = server.base_url
    return BilibiliGatewayEndpoints(
        upload_init_url=f"{base}/init",
        part_upload_url=f"{base}/part",
        upload_complete_url=f"{base}/complete",
        small_file_upload_url=f"{base}/small",
        cover_upload_url=f"{base}/cover",
        archive_add_url=f"{base}/add",
    )


def query_endpoints(server: MockBilibiliServer) -> BilibiliQueryGatewayEndpoints:
    base = server.base_url
    return BilibiliQueryGatewayEndpoints(
        archive_view_url=f"{base}/view",
        archive_viewlist_url=f"{base}/viewlist",
    )


def submission_fields() -> BilibiliArchiveFields:
    return BilibiliArchiveFields(
        title=TITLE,
        tid=21,
        tag="科技,教程",
        copyright=1,
        description="样例描述",
        source=None,
        no_reprint=0,
    )


async def reset_data(database: Database) -> None:
    async with database.session() as session:
        await session.execute(delete(bilibili_publish_reconciliations))
        await session.execute(delete(bilibili_upload_parts))
        await session.execute(delete(bilibili_publish_attempts))


def reconciliation_service(
    database: Database, server: MockBilibiliServer, gateway: HttpxBilibiliArchiveQueryGateway
) -> BilibiliArchiveReconciliationService:
    """Build a fresh service instance, as a restarted process would."""
    return BilibiliArchiveReconciliationService(
        contract=CONTRACT,
        attempt_source=SqlAlchemyBilibiliArchivePublishStore(database),
        store=SqlAlchemyBilibiliReconciliationStore(database),
        gateway=gateway,
        token_provider=FixtureTokenProvider(),
    )


async def publish_small_archive(
    database: Database,
    server: MockBilibiliServer,
    gateway: HttpxBilibiliOpenApiGateway,
    tmp_path: Path,
    *,
    lose_creation_response: bool,
) -> PublishJobId:
    """Drive the real PB-03 chain up to submitted or lost-response uncertain."""
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
        publish_job_id, material=material, fields=submission_fields(), with_cover=False
    )
    server.script("/init", "json", _fixture("response-upload-init-valid"))
    server.script("/small", "json", _fixture("response-part-upload-valid"))
    await service.upload_video(publish_job_id, material_reader)
    if lose_creation_response:
        server.script("/add", "hang", 5.0)
        with pytest.raises(BilibiliArchiveOutcomeUncertain):
            await service.create_archive(publish_job_id)
    else:
        server.script("/add", "json", _fixture("response-archive-add-valid"))
        receipt = await service.create_archive(publish_job_id)
        assert receipt.resource_id == RESOURCE_ID
    return publish_job_id


@pytest.mark.asyncio
async def test_submitted_archive_reconciles_review_then_published(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
    tmp_path: Path,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    server = MockBilibiliServer()
    publish_gateway = HttpxBilibiliOpenApiGateway(
        contract=CONTRACT,
        credentials=CREDENTIALS,
        endpoints=publish_endpoints(server),
        timeout_seconds=5.0,
    )
    query_gateway = HttpxBilibiliArchiveQueryGateway(
        contract=CONTRACT,
        credentials=CREDENTIALS,
        endpoints=query_endpoints(server),
        timeout_seconds=1.0,
    )
    try:
        await reset_data(database)
        publish_job_id = await publish_small_archive(
            database, server, publish_gateway, tmp_path, lose_creation_response=False
        )

        # Restarted process: recover the queue from PostgreSQL facts alone.
        service = reconciliation_service(database, server, query_gateway)
        queue = await service.recover()
        assert queue == (publish_job_id,)

        server.script("/view", "json", _view_payload(state=-30))
        in_review = await service.reconcile_once(publish_job_id)
        assert in_review.decision is BilibiliReconciliationDecision.IN_REVIEW
        assert in_review.archive_state == -30

        # Query timeout: pacing only, no state change, no resubmission.
        server.script("/view", "hang", 3.0)
        timed_out = await service.reconcile_once(publish_job_id)
        assert timed_out.decision is BilibiliReconciliationDecision.QUERY_UNREACHABLE
        assert timed_out.outcome is BilibiliReconciliationOutcome.PENDING

        # Another restart between polls; the pending row survives.
        restarted = reconciliation_service(database, server, query_gateway)
        server.script("/view", "json", _view_payload(state=0, ptime=int(time.time())))
        published = await restarted.reconcile_once(publish_job_id)
        assert published.decision is BilibiliReconciliationDecision.PUBLISHED
        assert published.publish_job_target is PublishJobStatus.PUBLISHED
        assert published.resource_id == RESOURCE_ID

        replay = await restarted.reconcile_once(publish_job_id)
        assert replay.decision is BilibiliReconciliationDecision.ALREADY_SETTLED

        store = SqlAlchemyBilibiliReconciliationStore(database)
        record = await store.load(publish_job_id)
        assert record is not None
        assert record.outcome is BilibiliReconciliationOutcome.PUBLISHED
        assert server.route_count("/add") == 1
    finally:
        await publish_gateway.aclose()
        await query_gateway.aclose()
        server.close()
        await database.close()


@pytest.mark.asyncio
async def test_lost_response_converges_onto_the_existing_archive(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
    tmp_path: Path,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    server = MockBilibiliServer()
    publish_gateway = HttpxBilibiliOpenApiGateway(
        contract=CONTRACT,
        credentials=CREDENTIALS,
        endpoints=publish_endpoints(server),
        timeout_seconds=1.0,
    )
    query_gateway = HttpxBilibiliArchiveQueryGateway(
        contract=CONTRACT,
        credentials=CREDENTIALS,
        endpoints=query_endpoints(server),
        timeout_seconds=5.0,
    )
    try:
        await reset_data(database)
        publish_job_id = await publish_small_archive(
            database, server, publish_gateway, tmp_path, lose_creation_response=True
        )
        attempts = SqlAlchemyBilibiliArchivePublishStore(database)
        attempt = await attempts.load(publish_job_id)
        assert attempt is not None
        assert attempt.phase is BilibiliPublishPhase.OUTCOME_UNCERTAIN

        service = reconciliation_service(database, server, query_gateway)
        queue = await service.recover()
        assert queue == (publish_job_id,)

        # The archive actually exists on the platform: adopt it and converge
        # to its real review state instead of resubmitting.
        server.script("/viewlist", "json", _viewlist_payload([_archive_item()]))
        server.script(
            "/view",
            "json",
            _view_payload(state=-2, state_desc="已退回", reject_reason="内容不符合规范"),
        )
        result = await service.reconcile_once(publish_job_id)

        assert result.decision is BilibiliReconciliationDecision.REJECTED
        assert result.publish_job_target is PublishJobStatus.REJECTED
        assert result.resource_id == RESOURCE_ID
        store = SqlAlchemyBilibiliReconciliationStore(database)
        record = await store.load(publish_job_id)
        assert record is not None
        assert record.outcome is BilibiliReconciliationOutcome.REJECTED
        assert record.resource_id == RESOURCE_ID
        assert server.route_count("/add") == 1
    finally:
        await publish_gateway.aclose()
        await query_gateway.aclose()
        server.close()
        await database.close()


@pytest.mark.asyncio
async def test_lost_response_with_provably_absent_archive_settles_failed(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
    tmp_path: Path,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    server = MockBilibiliServer()
    publish_gateway = HttpxBilibiliOpenApiGateway(
        contract=CONTRACT,
        credentials=CREDENTIALS,
        endpoints=publish_endpoints(server),
        timeout_seconds=1.0,
    )
    query_gateway = HttpxBilibiliArchiveQueryGateway(
        contract=CONTRACT,
        credentials=CREDENTIALS,
        endpoints=query_endpoints(server),
        timeout_seconds=1.0,
    )
    try:
        await reset_data(database)
        publish_job_id = await publish_small_archive(
            database, server, publish_gateway, tmp_path, lose_creation_response=True
        )
        service = reconciliation_service(database, server, query_gateway)
        await service.recover()

        # Listing timeout first: still uncertain, nothing settles.
        server.script("/viewlist", "hang", 3.0)
        pending = await service.reconcile_once(publish_job_id)
        assert pending.decision is BilibiliReconciliationDecision.QUERY_UNREACHABLE
        assert pending.outcome is BilibiliReconciliationOutcome.PENDING

        # Complete enumeration without a candidate proves the creation never
        # happened; only now may the loss settle as failed.
        server.script("/viewlist", "json", _viewlist_payload([]))
        result = await service.reconcile_once(publish_job_id)

        assert result.decision is BilibiliReconciliationDecision.FAILED_ABSENT
        assert result.publish_job_target is PublishJobStatus.FAILED
        store = SqlAlchemyBilibiliReconciliationStore(database)
        record = await store.load(publish_job_id)
        assert record is not None
        assert record.outcome is BilibiliReconciliationOutcome.FAILED
        assert record.failure_code is PublishFailureCode.PLATFORM_ERROR
        assert record.resource_id is None
        assert server.route_count("/add") == 1
        assert server.route_count("/view") == 0
    finally:
        await publish_gateway.aclose()
        await query_gateway.aclose()
        server.close()
        await database.close()
