"""LE-06 T6: real-network editing API acceptance over PostgreSQL."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener
from uuid import UUID, uuid4

import pytest
from conftest import (
    AlembicRunner,
    process_ids_matching,
)
from scripts.desktop_e2e_prerequisites import (  # type: ignore[import-not-found]
    CONTROL_PLANE_PORT_RANGE,
    require_reserved_port_still_free,
    reserve_control_plane_port,
)
from sqlalchemy import func, insert, select

from automation_tool import __version__
from automation_tool.control_plane.application.device_credentials import (
    DEVICE_CREDENTIAL_SCOPE,
    DeviceCredentialFactory,
)
from automation_tool.control_plane.domain import InstallationId
from automation_tool.control_plane.infrastructure.database import (
    Database,
    device_credentials,
    device_sessions,
    editing_jobs,
    editing_project_timelines,
    editing_projects,
    installations,
    materials,
    timelines,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
CONTENT_DIGEST = "a1b2c3d4" * 8


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: dict[str, Any]
    text: str


@dataclass(frozen=True, slots=True)
class SeededCredential:
    installation_id: InstallationId
    credential: str


@dataclass(frozen=True, slots=True)
class RunningUvicorn:
    process: subprocess.Popen[bytes]
    port: int
    process_marker: str


class FakeUvicornProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.sent_signal: int | None = None
        self.terminated = False
        self.communicated = False

    def poll(self) -> int | None:
        return self.returncode

    def send_signal(self, selected_signal: int) -> None:
        self.sent_signal = selected_signal

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.returncode = -9

    def communicate(self, *, timeout: float) -> tuple[bytes, bytes]:
        self.communicated = True
        self.returncode = 0
        return b"", b""


def request_json(
    port: int,
    method: str,
    path: str,
    *,
    token: str | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 2,
) -> HttpResponse:
    request_id = str(uuid4())
    headers = {
        "accept": "application/json",
        "x-request-id": request_id,
    }
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    encoded = None
    if payload is not None:
        headers["content-type"] = "application/json"
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
    request = Request(
        f"http://127.0.0.1:{port}{path}",
        data=encoded,
        headers=headers,
        method=method,
    )
    try:
        response = build_opener(ProxyHandler({})).open(request, timeout=timeout)
    except HTTPError as error:
        response = error
    with response:
        text = response.read().decode("utf-8")
        body = json.loads(text)
        assert isinstance(body, dict)
        assert response.headers["x-request-id"] == request_id
        assert response.headers["cache-control"] == "no-store"
        return HttpResponse(
            status=response.status,
            body=body,
            text=text,
        )


def assert_error(response: HttpResponse, *, status: int, code: str) -> None:
    assert response.status == status
    assert set(response.body) == {"error"}
    assert response.body["error"]["code"] == code


def port_is_available(port: int) -> bool:
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) != 0


def bounded_process_output(
    stdout: bytes,
    stderr: bytes,
) -> str:
    return (
        f"stdout={stdout[-4096:].decode('utf-8', errors='replace')!r}; "
        f"stderr={stderr[-4096:].decode('utf-8', errors='replace')!r}"
    )


def uvicorn_creation_flags() -> int:
    if sys.platform == "win32":
        return int(subprocess.CREATE_NEW_PROCESS_GROUP)
    return 0


def uvicorn_shutdown_signal() -> int:
    if sys.platform == "win32":
        return int(signal.CTRL_BREAK_EVENT)
    return int(signal.SIGINT)


def reap_failed_uvicorn_startup(
    process: subprocess.Popen[bytes],
) -> tuple[bytes, bytes]:
    if process.poll() is None:
        process.terminate()
    try:
        return process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.communicate(timeout=5)


def start_uvicorn(postgresql_url: str) -> RunningUvicorn:
    port = reserve_control_plane_port()
    assert port in CONTROL_PLANE_PORT_RANGE
    require_reserved_port_still_free(port)
    process_marker = (
        f"automation_tool.control_plane:create_app --factory --host 127.0.0.1 --port {port}"
    )
    assert port_is_available(port)
    assert process_ids_matching(process_marker) == set()
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("AUTOMATION_TOOL_")
    }
    environment["AUTOMATION_TOOL_DATABASE_URL"] = postgresql_url
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "automation_tool.control_plane:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--ws",
            "websockets-sansio",
            "--no-access-log",
            "--log-level",
            "warning",
        ],
        cwd=BACKEND_ROOT,
        creationflags=uvicorn_creation_flags(),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 10
    last_observation = "no connection attempt"
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate(timeout=1)
                raise AssertionError(
                    "The LE-06 Uvicorn process exited during startup: "
                    + bounded_process_output(stdout, stderr)
                )
            try:
                health = request_json(port, "GET", "/api/v1/health", timeout=0.5)
            except (OSError, URLError) as error:
                last_observation = repr(error)
                time.sleep(0.05)
                continue
            last_observation = f"status={health.status}, body={health.body!r}"
            if health.status == 200:
                assert health.body == {
                    "service": "control-plane",
                    "status": "ok",
                    "version": __version__,
                }
                return RunningUvicorn(
                    process=process,
                    port=port,
                    process_marker=process_marker,
                )
            time.sleep(0.05)
    except BaseException:
        reap_failed_uvicorn_startup(process)
        raise
    stdout, stderr = reap_failed_uvicorn_startup(process)
    raise AssertionError(
        "The LE-06 Uvicorn process did not become healthy "
        f"(last_observation={last_observation}): " + bounded_process_output(stdout, stderr)
    )


def stop_uvicorn(server: RunningUvicorn) -> None:
    if server.process.poll() is None:
        server.process.send_signal(uvicorn_shutdown_signal())
    try:
        stdout, stderr = server.process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        server.process.kill()
        stdout, stderr = server.process.communicate(timeout=5)
        raise AssertionError(
            "The LE-06 Uvicorn process required SIGKILL: " + bounded_process_output(stdout, stderr)
        ) from None
    assert server.process.returncode == 0, bounded_process_output(stdout, stderr)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not port_is_available(server.port):
        time.sleep(0.05)
    assert port_is_available(server.port)
    assert process_ids_matching(server.process_marker) == set()


@contextmanager
def real_control_plane(postgresql_url: str) -> Iterator[RunningUvicorn]:
    server = start_uvicorn(postgresql_url)
    try:
        yield server
    finally:
        stop_uvicorn(server)


def test_uvicorn_process_uses_the_windows_process_group_and_break_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeUvicornProcess()
    popen_arguments: dict[str, Any] = {}
    windows_process_group = 512
    windows_break_signal = 21

    def fake_popen(*arguments: Any, **keywords: Any) -> FakeUvicornProcess:
        popen_arguments.update(keywords)
        return process

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        subprocess,
        "CREATE_NEW_PROCESS_GROUP",
        windows_process_group,
        raising=False,
    )
    monkeypatch.setattr(
        signal,
        "CTRL_BREAK_EVENT",
        windows_break_signal,
        raising=False,
    )
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        sys.modules[__name__],
        "reserve_control_plane_port",
        lambda: CONTROL_PLANE_PORT_RANGE.start,
    )
    monkeypatch.setattr(
        sys.modules[__name__],
        "require_reserved_port_still_free",
        lambda port: None,
    )
    monkeypatch.setattr(
        sys.modules[__name__],
        "port_is_available",
        lambda port: True,
    )
    monkeypatch.setattr(
        sys.modules[__name__],
        "process_ids_matching",
        lambda marker: set(),
    )
    monkeypatch.setattr(
        sys.modules[__name__],
        "request_json",
        lambda *arguments, **keywords: HttpResponse(
            status=200,
            body={
                "service": "control-plane",
                "status": "ok",
                "version": __version__,
            },
            text="",
        ),
    )

    server = start_uvicorn("postgresql+asyncpg://redacted")
    stop_uvicorn(server)

    assert popen_arguments["creationflags"] == windows_process_group
    assert process.sent_signal == windows_break_signal


def test_uvicorn_startup_contract_failure_still_reaps_the_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeUvicornProcess()

    monkeypatch.setattr(subprocess, "Popen", lambda *arguments, **keywords: process)
    monkeypatch.setattr(
        sys.modules[__name__],
        "reserve_control_plane_port",
        lambda: CONTROL_PLANE_PORT_RANGE.start,
    )
    monkeypatch.setattr(
        sys.modules[__name__],
        "require_reserved_port_still_free",
        lambda port: None,
    )
    monkeypatch.setattr(
        sys.modules[__name__],
        "port_is_available",
        lambda port: True,
    )
    monkeypatch.setattr(
        sys.modules[__name__],
        "process_ids_matching",
        lambda marker: set(),
    )
    monkeypatch.setattr(
        sys.modules[__name__],
        "request_json",
        lambda *arguments, **keywords: HttpResponse(
            status=200,
            body={"service": "control-plane", "status": "changed"},
            text="",
        ),
    )

    with pytest.raises(AssertionError):
        start_uvicorn("postgresql+asyncpg://redacted")

    assert process.terminated
    assert process.communicated


async def seed_credentials(
    postgresql_url: str,
) -> tuple[SeededCredential, SeededCredential]:
    database = Database.from_url(postgresql_url)
    factory = DeviceCredentialFactory(
        secret_source=secrets.token_bytes,
        id_source=uuid4,
    )
    now = datetime.now(UTC)
    seeded: list[SeededCredential] = []
    try:
        async with database.session() as session:
            for _ in range(2):
                installation_id = InstallationId.new()
                pending = factory.create()
                await session.execute(
                    insert(installations).values(
                        id=installation_id.uuid,
                        device_public_key=secrets.token_bytes(32),
                        created_at=now,
                        updated_at=now,
                    )
                )
                await session.execute(
                    insert(device_credentials).values(
                        id=pending.credential_id,
                        installation_id=installation_id.uuid,
                        version=1,
                        scope=DEVICE_CREDENTIAL_SCOPE,
                        secret_digest=pending.secret_digest,
                        status="active",
                        created_at=now,
                        updated_at=now,
                    )
                )
                seeded.append(
                    SeededCredential(
                        installation_id=installation_id,
                        credential=pending.credential,
                    )
                )
    finally:
        await database.close()
    return seeded[0], seeded[1]


def exchange_app_session(server: RunningUvicorn, credential: str) -> str:
    response = request_json(
        server.port,
        "POST",
        "/api/v1/device-sessions",
        token=credential,
        payload={"capability": "app.control-plane"},
    )
    assert response.status == 201
    assert response.body["capability"] == "app.control-plane"
    session_token = response.body["sessionToken"]
    assert isinstance(session_token, str)
    return session_token


async def verify_persisted_editing_graph(
    postgresql_url: str,
    *,
    installation_id: InstallationId,
    outsider_installation_id: InstallationId,
    project_id: UUID,
    material_id: UUID,
    timeline_id: UUID,
    job_id: UUID,
) -> None:
    database = Database.from_url(postgresql_url)
    try:
        async with database.session() as session:
            project_owner = await session.scalar(
                select(editing_projects.c.installation_id).where(
                    editing_projects.c.project_id == project_id
                )
            )
            material_owner = await session.scalar(
                select(materials.c.installation_id).where(materials.c.material_id == material_id)
            )
            timeline_identity = (
                await session.execute(
                    select(
                        editing_project_timelines.c.project_id,
                        editing_project_timelines.c.timeline_id,
                    ).where(editing_project_timelines.c.project_id == project_id)
                )
            ).one()
            timeline_row = (
                await session.execute(
                    select(
                        timelines.c.project_id,
                        timelines.c.revision,
                    ).where(
                        timelines.c.timeline_id == timeline_id,
                        timelines.c.revision == 1,
                    )
                )
            ).one()
            job_row = (
                await session.execute(
                    select(
                        editing_jobs.c.installation_id,
                        editing_jobs.c.project_id,
                        editing_jobs.c.timeline_id,
                        editing_jobs.c.timeline_revision,
                        editing_jobs.c.status,
                    ).where(editing_jobs.c.job_id == job_id)
                )
            ).one()
            app_session_counts = dict(
                (
                    await session.execute(
                        select(
                            device_sessions.c.installation_id,
                            func.count(),
                        )
                        .where(
                            device_sessions.c.capability == "app.control-plane",
                            device_sessions.c.installation_id.in_(
                                (
                                    installation_id.uuid,
                                    outsider_installation_id.uuid,
                                )
                            ),
                        )
                        .group_by(device_sessions.c.installation_id)
                    )
                ).all()
            )
    finally:
        await database.close()

    assert project_owner == installation_id.uuid
    assert material_owner == installation_id.uuid
    assert timeline_identity == (project_id, timeline_id)
    assert timeline_row == (project_id, 1)
    assert job_row == (
        installation_id.uuid,
        project_id,
        timeline_id,
        1,
        "queued",
    )
    assert app_session_counts == {
        installation_id.uuid: 1,
        outsider_installation_id.uuid: 1,
    }


def test_real_uvicorn_process_round_trips_the_editing_surface_through_postgresql(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
) -> None:
    alembic_runner(postgresql_url, "upgrade", "head")
    owner, outsider = asyncio.run(seed_credentials(postgresql_url))

    with real_control_plane(postgresql_url) as server:
        owner_session = exchange_app_session(server, owner.credential)
        outsider_session = exchange_app_session(server, outsider.credential)

        unauthorized = request_json(
            server.port,
            "GET",
            "/api/v1/editing-projects",
        )
        assert_error(
            unauthorized,
            status=401,
            code="installation_access_denied",
        )

        material_id = uuid4()
        material_payload: dict[str, Any] = {
            "materialId": str(material_id),
            "kind": "video",
            "durationMs": 10_000,
            "width": 1920,
            "height": 1080,
            "contentDigest": CONTENT_DIGEST,
            "hasAudio": True,
            "audioLoudnessLufs": -14.5,
            "hasSpeech": True,
            "speechSegmentsMs": [[1_000, 2_000]],
            "speechTranscript": "真实 HTTP 素材",
            "shotBoundariesMs": [0, 5_000],
            "aiDescription": "真实网络登记的素材",
            "aiTags": ["验收"],
            "descriptionSource": "ai",
            "describedAt": "2026-07-30T08:09:10.123456Z",
        }
        bad_material = request_json(
            server.port,
            "POST",
            "/api/v1/editing-materials",
            token=owner_session,
            payload={
                **material_payload,
                "materialId": str(uuid4()),
                "sourcePath": "/Users/private/素材.mp4",
            },
        )
        assert_error(bad_material, status=422, code="validation")
        assert "private" not in bad_material.text

        registered = request_json(
            server.port,
            "POST",
            "/api/v1/editing-materials",
            token=owner_session,
            payload=material_payload,
        )
        assert registered.status == 201
        assert registered.body["materialId"] == str(material_id)
        assert "path" not in registered.text.lower()
        by_id = request_json(
            server.port,
            "GET",
            f"/api/v1/editing-materials/{material_id}",
            token=owner_session,
        )
        by_digest = request_json(
            server.port,
            "GET",
            "/api/v1/editing-materials?" + urlencode({"contentDigest": CONTENT_DIGEST}),
            token=owner_session,
        )
        assert by_id.body == registered.body
        assert by_digest.body == registered.body
        assert_error(
            request_json(
                server.port,
                "GET",
                f"/api/v1/editing-materials/{material_id}",
                token=outsider_session,
            ),
            status=404,
            code="material_not_found",
        )
        assert_error(
            request_json(
                server.port,
                "GET",
                "/api/v1/editing-materials?" + urlencode({"contentDigest": CONTENT_DIGEST}),
                token=outsider_session,
            ),
            status=404,
            code="material_not_found",
        )

        created = request_json(
            server.port,
            "POST",
            "/api/v1/editing-projects",
            token=owner_session,
            payload={
                "title": "真实 HTTP 剪辑项目",
                "output": {"width": 1080, "height": 1920, "fps": 30},
                "captionStyle": {
                    "fontKey": "source-han-sans",
                    "fontPx": 64,
                    "strokePx": 4,
                    "lineSpacing": 1.25,
                },
            },
        )
        assert created.status == 201
        project_id = UUID(created.body["projectId"])
        owner_projects = request_json(
            server.port,
            "GET",
            "/api/v1/editing-projects?limit=1",
            token=owner_session,
        )
        assert owner_projects.body == {
            "items": [created.body],
            "nextCursor": None,
        }
        outsider_projects = request_json(
            server.port,
            "GET",
            "/api/v1/editing-projects",
            token=outsider_session,
        )
        assert outsider_projects.body == {"items": [], "nextCursor": None}
        assert_error(
            request_json(
                server.port,
                "GET",
                f"/api/v1/editing-projects/{project_id}",
                token=outsider_session,
            ),
            status=404,
            code="editing_project_not_found",
        )

        timeline_path = f"/api/v1/editing-projects/{project_id}/timeline"
        assert_error(
            request_json(
                server.port,
                "GET",
                timeline_path,
                token=owner_session,
            ),
            status=404,
            code="timeline_not_found",
        )
        timeline_draft = {
            "durationMs": 10_000,
            "tracks": [
                {
                    "trackId": "visual",
                    "kind": "visual",
                    "clips": [
                        {
                            "clipId": "visual-one",
                            "startMs": 0,
                            "durationMs": 10_000,
                            "sourceMaterialId": str(material_id),
                            "sourceInMs": 0,
                            "sourceOutMs": 10_000,
                            "text": None,
                            "gainDb": None,
                            "transitionIn": None,
                        }
                    ],
                }
            ],
        }
        saved_timeline = request_json(
            server.port,
            "PUT",
            timeline_path,
            token=owner_session,
            payload=timeline_draft,
        )
        assert saved_timeline.status == 201
        timeline_id = UUID(saved_timeline.body["timelineId"])
        assert saved_timeline.body["projectId"] == str(project_id)
        assert saved_timeline.body["revision"] == 1
        loaded_timeline = request_json(
            server.port,
            "GET",
            timeline_path,
            token=owner_session,
        )
        assert loaded_timeline.body == saved_timeline.body
        assert_error(
            request_json(
                server.port,
                "GET",
                timeline_path,
                token=outsider_session,
            ),
            status=404,
            code="timeline_not_found",
        )

        jobs_path = f"/api/v1/editing-projects/{project_id}/jobs"
        submitted_job = request_json(
            server.port,
            "POST",
            jobs_path,
            token=owner_session,
            payload={},
        )
        assert submitted_job.status == 201
        job_id = UUID(submitted_job.body["jobId"])
        assert submitted_job.body["timelineId"] == str(timeline_id)
        assert submitted_job.body["timelineRevision"] == 1
        assert submitted_job.body["status"] == "queued"
        owner_jobs = request_json(
            server.port,
            "GET",
            jobs_path,
            token=owner_session,
        )
        assert owner_jobs.body == {
            "items": [submitted_job.body],
            "nextCursor": None,
        }
        loaded_job = request_json(
            server.port,
            "GET",
            f"/api/v1/editing-jobs/{job_id}",
            token=owner_session,
        )
        assert loaded_job.body == submitted_job.body
        assert_error(
            request_json(
                server.port,
                "POST",
                jobs_path,
                token=owner_session,
                payload={},
            ),
            status=409,
            code="editing_job_revision_already_queued",
        )
        assert_error(
            request_json(
                server.port,
                "GET",
                jobs_path + "?cursor=not%2Bbase64",
                token=owner_session,
            ),
            status=422,
            code="validation",
        )
        outsider_jobs = request_json(
            server.port,
            "GET",
            jobs_path,
            token=outsider_session,
        )
        assert outsider_jobs.body == {"items": [], "nextCursor": None}
        assert_error(
            request_json(
                server.port,
                "GET",
                f"/api/v1/editing-jobs/{job_id}",
                token=outsider_session,
            ),
            status=404,
            code="editing_job_not_found",
        )

    asyncio.run(
        verify_persisted_editing_graph(
            postgresql_url,
            installation_id=owner.installation_id,
            outsider_installation_id=outsider.installation_id,
            project_id=project_id,
            material_id=material_id,
            timeline_id=timeline_id,
            job_id=job_id,
        )
    )
