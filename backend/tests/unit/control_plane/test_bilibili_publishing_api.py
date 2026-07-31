"""Production API bridge tests for the official Bilibili publishing route."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from automation_tool.control_plane import create_app
from automation_tool.control_plane.api import bilibili_publishing as publishing_api
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.bilibili_archive_publishing import (
    BilibiliPublishPhase,
)
from automation_tool.control_plane.application.bilibili_publishing_runtime import (
    BilibiliPublishRuntimeResult,
)
from automation_tool.control_plane.domain.resource_ids import InstallationId
from automation_tool.control_plane.domain.video_publishing import PublishJobId

VIDEO = b"fixture-video-payload"
VIDEO_SHA256 = hashlib.sha256(VIDEO).hexdigest()
JOB = PublishJobId.new()
INSTALLATION = InstallationId.new()
SESSION = "fixture-session-token-with-more-than-thirty-two-bytes"


def result(phase: BilibiliPublishPhase, *, resource_id: str | None = None) -> Any:
    return BilibiliPublishRuntimeResult(
        phase=phase,
        request_digest="a" * 64,
        resource_id=resource_id,
        replayed=False,
        credential_rotation=None,
    )


class FakeRuntime:
    maximum_video_bytes = 1024

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def prepare(self, **values: Any) -> tuple[str, BilibiliPublishRuntimeResult]:
        assert values["installation_id"] == INSTALLATION
        assert values["publish_job_id"] == JOB
        assert values["credential"].app_secret == "fixture-app-secret"
        assert values["credential"].access_token == "fixture-access-token"
        assert values["credential"].refresh_token == "fixture-refresh-token"
        assert values["material"].file_name == f"{JOB}.mp4"
        assert values["material"].size_bytes == len(VIDEO)
        assert values["material"].sha256 == VIDEO_SHA256
        assert values["fields"].title == "验收标题"
        assert values["fields"].tid == 21
        self.calls.append("prepare")
        return SESSION, result(BilibiliPublishPhase.PREPARED)

    async def upload_video(self, **values: Any) -> BilibiliPublishRuntimeResult:
        assert values["installation_id"] == INSTALLATION
        assert values["publish_job_id"] == JOB
        assert values["session_token"] == SESSION
        material_root: Path = values["material_root"]
        assert (material_root / f"{JOB}.mp4").read_bytes() == VIDEO
        self.calls.append("upload")
        return result(BilibiliPublishPhase.VIDEO_UPLOADED)

    async def submit(self, **values: Any) -> BilibiliPublishRuntimeResult:
        assert values["installation_id"] == INSTALLATION
        assert values["publish_job_id"] == JOB
        assert values["session_token"] == SESSION
        self.calls.append("submit")
        return result(BilibiliPublishPhase.SUBMITTED, resource_id="BV17B4y1s7R1")

    async def cancel(self, **values: Any) -> None:
        assert values["installation_id"] == INSTALLATION
        assert values["publish_job_id"] == JOB
        assert values["session_token"] == SESSION
        self.calls.append("cancel")


def client(runtime: FakeRuntime) -> TestClient:
    app = create_app(database=None)
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION
    app.dependency_overrides[publishing_api._runtime] = lambda: runtime
    return TestClient(app)


def prepare_payload() -> dict[str, object]:
    return {
        "credential": {
            "clientId": "fixture-client-id",
            "appSecret": "fixture-app-secret",
            "accessToken": "fixture-access-token",
            "refreshToken": "fixture-refresh-token",
            "expiresAtEpochSeconds": 2_000_000_000,
        },
        "material": {
            "sizeBytes": len(VIDEO),
            "durationSeconds": 12,
            "sha256": VIDEO_SHA256,
        },
        "archive": {
            "title": "验收标题",
            "tid": 21,
            "tag": "自动化,视频",
            "description": "验收简介",
            "noReprint": 1,
        },
    }


def test_prepare_upload_and_single_submission_share_one_bound_session() -> None:
    runtime = FakeRuntime()
    api = client(runtime)
    root = f"/api/v1/publishing/bilibili/jobs/{JOB}"

    prepared = api.post(root, json=prepare_payload())
    assert prepared.status_code == 201
    assert prepared.json() == {
        "publishJobId": str(JOB),
        "phase": "prepared",
        "requestDigest": "a" * 64,
        "resourceId": None,
        "replayed": False,
        "sessionToken": SESSION,
        "credentialRotation": None,
    }
    assert "fixture-app-secret" not in prepared.text
    assert "fixture-access-token" not in prepared.text

    uploaded = api.put(
        f"{root}/video",
        content=VIDEO,
        headers={"x-bilibili-publish-session": SESSION},
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["phase"] == "video_uploaded"
    assert uploaded.json()["sessionToken"] is None

    submitted = api.post(
        f"{root}/submission",
        headers={"x-bilibili-publish-session": SESSION},
    )
    assert submitted.status_code == 202
    assert submitted.json()["phase"] == "submitted"
    assert submitted.json()["resourceId"] == "BV17B4y1s7R1"
    assert runtime.calls == ["prepare", "upload", "submit"]


def test_stream_size_mismatch_is_rejected_before_the_runtime_sees_a_video() -> None:
    runtime = FakeRuntime()
    response = client(runtime).put(
        f"/api/v1/publishing/bilibili/jobs/{JOB}/video",
        content=VIDEO,
        headers={
            "x-bilibili-publish-session": SESSION,
            "content-length": str(len(VIDEO) + 1),
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "bilibili_publishing_invalid"
    assert runtime.calls == []


def test_cancel_retires_the_installation_bound_session_without_returning_secrets() -> None:
    runtime = FakeRuntime()
    response = client(runtime).delete(
        f"/api/v1/publishing/bilibili/jobs/{JOB}/session",
        headers={"x-bilibili-publish-session": SESSION},
    )

    assert response.status_code == 204
    assert response.content == b""
    assert SESSION not in response.text
    assert runtime.calls == ["cancel"]


def test_validation_errors_never_reflect_publishing_credentials() -> None:
    runtime = FakeRuntime()
    payload = prepare_payload()
    payload["archive"] = {
        "title": "",
        "tid": 21,
        "tag": "自动化",
        "description": "验收简介",
        "noReprint": 1,
    }
    response = client(runtime).post(
        f"/api/v1/publishing/bilibili/jobs/{JOB}",
        json=payload,
    )

    assert response.status_code == 422
    for secret in [
        "fixture-app-secret",
        "fixture-access-token",
        "fixture-refresh-token",
    ]:
        assert secret not in response.text
    assert runtime.calls == []
