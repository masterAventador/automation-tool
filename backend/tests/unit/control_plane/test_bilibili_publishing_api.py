"""Production API bridge tests for the official Bilibili publishing route."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from automation_tool.control_plane import create_app
from automation_tool.control_plane.api import bilibili_publishing as publishing_api
from automation_tool.control_plane.api.errors import AppError
from automation_tool.control_plane.api.installation_access import (
    require_current_installation_access,
)
from automation_tool.control_plane.application.bilibili_archive_publishing import (
    BilibiliArchivePublishRejected,
    BilibiliArchivePublishUnavailable,
    BilibiliPublishPhase,
)
from automation_tool.control_plane.application.bilibili_publishing_runtime import (
    BilibiliPublishingRuntime,
    BilibiliPublishRuntimeResult,
)
from automation_tool.control_plane.domain.resource_ids import InstallationId
from automation_tool.control_plane.domain.video_publishing import PublishJobId

VIDEO = b"fixture-video-payload"
VIDEO_SHA256 = hashlib.sha256(VIDEO).hexdigest()
JOB = PublishJobId.new()
INSTALLATION = InstallationId.new()
SESSION = "fixture-session-token-with-more-than-thirty-two-bytes"


def result(
    phase: BilibiliPublishPhase,
    *,
    resource_id: str | None = None,
) -> BilibiliPublishRuntimeResult:
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


ROOT = f"/api/v1/publishing/bilibili/jobs/{JOB}"


def test_a_client_id_carrying_whitespace_is_refused() -> None:
    """It is signed into every request; a padded one signs something else."""
    runtime = FakeRuntime()
    payload = prepare_payload()
    credential = payload["credential"]
    assert isinstance(credential, dict)
    credential["clientId"] = "fixture client id"

    response = client(runtime).post(ROOT, json=payload)

    assert response.status_code == 422
    assert runtime.calls == []


def test_a_job_identifier_that_is_not_one_is_refused_before_the_runtime() -> None:
    runtime = FakeRuntime()
    api = client(runtime)

    for label, path in [
        ("preparing", "/api/v1/publishing/bilibili/jobs/not-a-job"),
        ("uploading", "/api/v1/publishing/bilibili/jobs/not-a-job/video"),
        ("submitting", "/api/v1/publishing/bilibili/jobs/not-a-job/submission"),
        ("cancelling", "/api/v1/publishing/bilibili/jobs/not-a-job/session"),
    ]:
        if label == "preparing":
            response = api.post(path, json=prepare_payload())
        elif label == "uploading":
            response = api.put(path, content=VIDEO, headers={"x-bilibili-publish-session": SESSION})
        elif label == "submitting":
            response = api.post(path, headers={"x-bilibili-publish-session": SESSION})
        else:
            response = api.delete(path, headers={"x-bilibili-publish-session": SESSION})
        assert response.status_code == 422, label
    assert runtime.calls == []


def test_a_missing_runtime_is_reported_as_unavailable_rather_than_a_crash() -> None:
    """The App may be up before publishing is configured; that is retryable."""
    app = create_app(database=None)
    app.dependency_overrides[require_current_installation_access] = lambda: INSTALLATION

    response = TestClient(app).post(ROOT, json=prepare_payload())

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "bilibili_publishing_unavailable"


def test_the_declared_upload_size_is_checked_before_a_byte_is_written() -> None:
    """The ceiling is what the platform accepts; a bigger claim never opens a file."""
    runtime = FakeRuntime()
    api = client(runtime)

    for label, declared in [
        ("nothing at all", 0),
        ("more than the platform takes", runtime.maximum_video_bytes + 1),
    ]:
        response = api.put(
            f"{ROOT}/video",
            content=b"",
            headers={
                "x-bilibili-publish-session": SESSION,
                "content-length": str(declared),
            },
        )
        assert response.status_code == 422, label
    assert runtime.calls == []


def test_a_rejected_or_unavailable_runtime_keeps_its_own_disposition() -> None:
    """Retryable and not-retryable lead the App to different next steps."""

    class _Refusing(FakeRuntime):
        def __init__(self, failure: BaseException) -> None:
            super().__init__()
            self.failure = failure

        async def prepare(self, **_values: Any) -> tuple[str, BilibiliPublishRuntimeResult]:
            raise self.failure

    for label, failure, expected in [
        ("a request the runtime refused", BilibiliArchivePublishRejected(), 422),
        ("a dependency that was not there", BilibiliArchivePublishUnavailable(), 503),
    ]:
        response = client(_Refusing(failure)).post(ROOT, json=prepare_payload())
        assert response.status_code == expected, label


def _refusing_runtime(step: str, failure: BaseException) -> FakeRuntime:
    class _Refusing(FakeRuntime):
        async def upload_video(self, **values: Any) -> BilibiliPublishRuntimeResult:
            if step == "upload":
                raise failure
            return await super().upload_video(**values)

        async def submit(self, **values: Any) -> BilibiliPublishRuntimeResult:
            if step == "submit":
                raise failure
            return await super().submit(**values)

        async def cancel(self, **values: Any) -> None:
            if step == "cancel":
                raise failure
            await super().cancel(**values)

    return _Refusing()


def _call(api: TestClient, step: str) -> Any:
    headers = {"x-bilibili-publish-session": SESSION}
    if step == "upload":
        return api.put(f"{ROOT}/video", content=VIDEO, headers=headers)
    if step == "submit":
        return api.post(f"{ROOT}/submission", headers=headers)
    return api.delete(f"{ROOT}/session", headers=headers)


def test_every_step_keeps_the_runtimes_own_disposition() -> None:
    """A refusal, an unreachable gateway and a platform failure are three answers."""
    cases: list[tuple[str, str, BaseException, int]] = [
        ("upload refused", "upload", BilibiliArchivePublishRejected(), 422),
        ("upload unavailable", "upload", BilibiliArchivePublishUnavailable(), 503),
        ("submit refused", "submit", BilibiliArchivePublishRejected(), 422),
        ("submit unavailable", "submit", BilibiliArchivePublishUnavailable(), 503),
        ("cancel refused", "cancel", BilibiliArchivePublishRejected(), 422),
    ]
    for label, step, failure, expected in cases:
        response = _call(client(_refusing_runtime(step, failure)), step)
        assert response.status_code == expected, label


def test_a_platform_failure_is_retryable_only_when_the_dependency_was() -> None:
    """The platform's own code decides whether the App may try again."""
    from automation_tool.control_plane.application.bilibili_archive_publishing import (
        BilibiliPublishStepFailed,
    )
    from automation_tool.control_plane.domain.bilibili_open_api import (
        BilibiliErrorCategory,
        BilibiliPlatformRejection,
    )
    from automation_tool.control_plane.domain.video_publishing import PublishFailureCode

    for label, failure_code, expected in [
        ("a dependency that was not there", PublishFailureCode.DEPENDENCY_UNAVAILABLE, 503),
        ("a platform error", PublishFailureCode.PLATFORM_ERROR, 409),
    ]:
        rejection = BilibiliPlatformRejection(
            code=-101,
            category=BilibiliErrorCategory.PLATFORM_BUSY,
            failure_code=failure_code,
        )
        response = _call(
            client(_refusing_runtime("upload", BilibiliPublishStepFailed(rejection))),
            "upload",
        )
        assert response.status_code == expected, label


def test_an_upload_the_filesystem_refuses_is_reported_as_unavailable(
    monkeypatch: Any,
) -> None:
    """Nowhere durable to stage the video is a retryable local problem, not a refusal."""
    import os as os_module

    def refusing_fsync(_descriptor: int) -> None:
        raise OSError("input/output error")

    monkeypatch.setattr(os_module, "fsync", refusing_fsync)
    runtime = FakeRuntime()

    response = client(runtime).put(
        f"{ROOT}/video",
        content=VIDEO,
        headers={"x-bilibili-publish-session": SESSION},
    )

    assert response.status_code == 503
    assert runtime.calls == []


def test_the_runtime_is_read_off_the_app_the_bootstrap_configured() -> None:
    """Every route resolves through this; it reads app state and checks the type.

    The runtime is built by the bootstrap with real credentials and a real
    gateway, so the instance here is assembled without running that -- what is
    asserted is the lookup and its type check, not how one gets built.
    """
    from types import SimpleNamespace

    configured = object.__new__(BilibiliPublishingRuntime)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(bilibili_publishing_runtime=configured))
    )

    assert publishing_api._runtime(cast(Any, request)) is configured

    absent = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(bilibili_publishing_runtime=None))
    )
    with pytest.raises(AppError):
        publishing_api._runtime(cast(Any, absent))


def test_a_rotated_credential_reaches_the_app_that_has_to_store_it() -> None:
    """Rotation is single-use; the App cannot keep publishing without the new pair."""
    from automation_tool.control_plane.application.bilibili_publishing_runtime import (
        BilibiliCredentialRotation,
    )

    class _Rotating(FakeRuntime):
        async def prepare(self, **_values: Any) -> tuple[str, BilibiliPublishRuntimeResult]:
            self.calls.append("prepare")
            return SESSION, BilibiliPublishRuntimeResult(
                phase=BilibiliPublishPhase.PREPARED,
                request_digest="a" * 64,
                resource_id=None,
                replayed=False,
                credential_rotation=BilibiliCredentialRotation(
                    access_token="rotated-access",
                    refresh_token="rotated-refresh",
                    expires_at_epoch_seconds=2_000_000_000,
                ),
            )

    response = client(_Rotating()).post(ROOT, json=prepare_payload())

    assert response.status_code == 201
    rotation = response.json()["credentialRotation"]
    assert rotation["accessToken"] == "rotated-access"
    assert rotation["refreshToken"] == "rotated-refresh"


def test_a_stream_longer_than_it_declared_is_cut_off_and_refused() -> None:
    """Written bytes are counted as they arrive, not trusted from the header."""
    runtime = FakeRuntime()

    response = client(runtime).put(
        f"{ROOT}/video",
        content=VIDEO,
        headers={
            "x-bilibili-publish-session": SESSION,
            "content-length": str(len(VIDEO) - 1),
        },
    )

    assert response.status_code == 422
    assert runtime.calls == []
