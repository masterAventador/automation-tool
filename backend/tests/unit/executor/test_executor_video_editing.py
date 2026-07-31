from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import httpx2
import pytest
from automation_tool.executor.video_editing import (
    EditingExecutionRequest,
    execute_video_editing,
    serve_one_video_editing_request,
)

PROJECT_ID = "00000000-0000-4000-8000-000000000201"
TIMELINE_ID = "00000000-0000-4000-8000-000000000202"
JOB_ID = "00000000-0000-4000-8000-000000000203"
ARTIFACT_ID = "00000000-0000-4000-8000-000000000204"


def _request(tmp_path: Path) -> dict[str, object]:
    input_directory = tmp_path / "input"
    input_directory.mkdir(exist_ok=True)
    source = input_directory / f"{ARTIFACT_ID}.mp4"
    source.write_bytes(b"verified-video-source")
    output = tmp_path / "output"
    output.mkdir(exist_ok=True)
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    state.chmod(0o700)
    return {
        "schemaVersion": 1,
        "executionMode": "submit",
        "credential": {
            "accessKeyId": "LTAI5tVe04TestAccessKey",
            "accessKeySecret": "ve04PrivateSecret1234567890",
            "region": "cn-shanghai",
            "ossBucket": "automation-tool-video-staging",
        },
        "editingJobId": JOB_ID,
        "projectId": PROJECT_ID,
        "timeline": {
            "timelineId": TIMELINE_ID,
            "projectId": PROJECT_ID,
            "revision": 1,
            "durationMs": 3_000,
            "tracks": [
                {
                    "trackId": "visual-main",
                    "kind": "visual",
                    "clips": [
                        {
                            "clipId": "clip-1",
                            "startMs": 0,
                            "durationMs": 3_000,
                            "sourceArtifactId": ARTIFACT_ID,
                            "text": None,
                            "transitionIn": None,
                        }
                    ],
                }
            ],
            "createdAt": "2026-07-31T01:02:03Z",
        },
        "assets": [
            {
                "artifactId": ARTIFACT_ID,
                "path": str(source),
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "sizeBytes": source.stat().st_size,
                "extension": ".mp4",
            }
        ],
        "inputDirectory": str(input_directory),
        "outputDirectory": str(output),
        "stateDirectory": str(state),
        "outputWidth": 128,
        "outputHeight": 128,
    }


def _request_with_mode(tmp_path: Path, mode: str) -> dict[str, object]:
    payload = _request(tmp_path)
    payload["executionMode"] = mode
    return payload


@pytest.mark.asyncio
async def test_real_provider_composition_stages_submits_reconciles_imports_and_cleans(
    tmp_path: Path,
) -> None:
    requests: list[tuple[str, str]] = []
    output = b"real-cloud-output-bytes"

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append((request.method, str(request.url)))
        action = request.headers.get("x-acs-action")
        if action == "SubmitMediaProducingJob":
            return httpx2.Response(
                200,
                json={"JobId": "vendor-job-1234", "RequestId": "request-12345678"},
            )
        if action == "GetMediaProducingJob":
            return httpx2.Response(
                200,
                json={
                    "MediaProducingJob": {
                        "JobId": "vendor-job-1234",
                        "Status": "Success",
                    }
                },
            )
        if request.method == "GET":
            return httpx2.Response(200, content=output)
        if request.method == "HEAD":
            return httpx2.Response(404)
        return httpx2.Response(200)

    request = EditingExecutionRequest.model_validate(_request(tmp_path))
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        result = await execute_video_editing(
            request,
            client=client,
            poll_interval_seconds=0,
        )

    assert result.status == "succeeded"
    assert result.editing_job_id == JOB_ID
    assert result.output_path is not None
    produced = Path(result.output_path)
    assert produced.parent == Path(request.output_directory)
    assert produced.read_bytes() == output
    assert result.output_sha256 == hashlib.sha256(output).hexdigest()
    assert result.output_size_bytes == len(output)
    assert result.failure_code is None
    assert [method for method, _ in requests].count("POST") == 1
    assert [method for method, _ in requests].count("PUT") == 1
    assert any("editing-staging/v1/" in url for _, url in requests)
    assert any("editing-output/v1/" in url for _, url in requests)
    assert [method for method, _ in requests].count("DELETE") == 2
    assert [method for method, _ in requests].count("HEAD") == 2


@pytest.mark.asyncio
async def test_interrupted_execution_resumes_by_vendor_job_without_a_second_submit(
    tmp_path: Path,
) -> None:
    first_requests: list[tuple[str, str | None]] = []

    def interrupted_handler(request: httpx2.Request) -> httpx2.Response:
        action = request.headers.get("x-acs-action")
        first_requests.append((request.method, action))
        if action == "SubmitMediaProducingJob":
            return httpx2.Response(
                200,
                json={"JobId": "vendor-job-recovery", "RequestId": "request-recovery1"},
            )
        if action == "GetMediaProducingJob":
            raise KeyboardInterrupt
        return httpx2.Response(200)

    submit_payload = _request_with_mode(tmp_path, "submit")
    submit = EditingExecutionRequest.model_validate(submit_payload)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(interrupted_handler)) as client:
        with pytest.raises(KeyboardInterrupt):
            await execute_video_editing(
                submit,
                client=client,
                poll_interval_seconds=0,
            )

    assert sum(action == "SubmitMediaProducingJob" for _, action in first_requests) == 1

    resumed_requests: list[tuple[str, str | None]] = []
    output = b"recovered-cloud-output"

    def resumed_handler(request: httpx2.Request) -> httpx2.Response:
        action = request.headers.get("x-acs-action")
        resumed_requests.append((request.method, action))
        if action == "SubmitMediaProducingJob":
            raise AssertionError("recovery must not submit a second cloud job")
        if action == "GetMediaProducingJob":
            return httpx2.Response(
                200,
                json={
                    "MediaProducingJob": {
                        "JobId": "vendor-job-recovery",
                        "Status": "Success",
                    }
                },
            )
        if request.method == "GET":
            return httpx2.Response(200, content=output)
        if request.method == "HEAD":
            return httpx2.Response(404)
        return httpx2.Response(200)

    reconcile_payload = _request_with_mode(tmp_path, "reconcile")
    reconcile = EditingExecutionRequest.model_validate(reconcile_payload)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(resumed_handler)) as client:
        recovered = await execute_video_editing(
            reconcile,
            client=client,
            poll_interval_seconds=0,
        )

    assert recovered.status == "succeeded"
    assert recovered.output_path is not None
    assert Path(recovered.output_path).read_bytes() == output
    assert sum(action == "SubmitMediaProducingJob" for _, action in resumed_requests) == 0
    assert sum(action == "GetMediaProducingJob" for _, action in resumed_requests) == 1
    assert not any(method == "PUT" for method, _ in resumed_requests)


@pytest.mark.asyncio
async def test_recovery_restarts_safely_when_interrupted_before_submission(
    tmp_path: Path,
) -> None:
    first_requests: list[tuple[str, str | None]] = []

    def interrupted_handler(request: httpx2.Request) -> httpx2.Response:
        action = request.headers.get("x-acs-action")
        first_requests.append((request.method, action))
        if request.method == "PUT":
            raise KeyboardInterrupt
        raise AssertionError("the cloud job must not be submitted after staging is interrupted")

    submit = EditingExecutionRequest.model_validate(_request_with_mode(tmp_path, "submit"))
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(interrupted_handler)) as client:
        with pytest.raises(KeyboardInterrupt):
            await execute_video_editing(
                submit,
                client=client,
                poll_interval_seconds=0,
            )

    assert first_requests == [("PUT", None)]

    recovered_requests: list[tuple[str, str | None]] = []
    output = b"recovered-after-staging-interruption"

    def recovered_handler(request: httpx2.Request) -> httpx2.Response:
        action = request.headers.get("x-acs-action")
        recovered_requests.append((request.method, action))
        if action == "SubmitMediaProducingJob":
            return httpx2.Response(
                200,
                json={"JobId": "vendor-job-safe-retry", "RequestId": "request-safe-retry"},
            )
        if action == "GetMediaProducingJob":
            return httpx2.Response(
                200,
                json={
                    "MediaProducingJob": {
                        "JobId": "vendor-job-safe-retry",
                        "Status": "Success",
                    }
                },
            )
        if request.method == "GET":
            return httpx2.Response(200, content=output)
        if request.method == "HEAD":
            return httpx2.Response(404)
        return httpx2.Response(200)

    reconcile = EditingExecutionRequest.model_validate(_request_with_mode(tmp_path, "reconcile"))
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(recovered_handler)) as client:
        recovered = await execute_video_editing(
            reconcile,
            client=client,
            poll_interval_seconds=0,
        )

    assert recovered.status == "succeeded"
    assert recovered.output_path is not None
    assert Path(recovered.output_path).read_bytes() == output
    assert sum(action == "SubmitMediaProducingJob" for _, action in recovered_requests) == 1
    assert sum(action == "GetMediaProducingJob" for _, action in recovered_requests) == 1


@pytest.mark.asyncio
async def test_recovery_never_replays_an_ambiguous_prepared_submission(
    tmp_path: Path,
) -> None:
    first_requests: list[tuple[str, str | None]] = []

    def interrupted_handler(request: httpx2.Request) -> httpx2.Response:
        action = request.headers.get("x-acs-action")
        first_requests.append((request.method, action))
        if action == "SubmitMediaProducingJob":
            raise KeyboardInterrupt
        return httpx2.Response(200)

    submit = EditingExecutionRequest.model_validate(_request_with_mode(tmp_path, "submit"))
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(interrupted_handler)) as client:
        with pytest.raises(KeyboardInterrupt):
            await execute_video_editing(
                submit,
                client=client,
                poll_interval_seconds=0,
            )

    assert sum(action == "SubmitMediaProducingJob" for _, action in first_requests) == 1

    def forbidden_handler(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError(f"ambiguous recovery must not call {request.method}")

    reconcile = EditingExecutionRequest.model_validate(_request_with_mode(tmp_path, "reconcile"))
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(forbidden_handler)) as client:
        recovered = await execute_video_editing(
            reconcile,
            client=client,
            poll_interval_seconds=0,
        )

    assert recovered.status == "outcome_uncertain"


@pytest.mark.asyncio
async def test_definitive_submission_rejection_fails_and_cleans_staging(
    tmp_path: Path,
) -> None:
    requests: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request.method)
        if request.headers.get("x-acs-action") == "SubmitMediaProducingJob":
            return httpx2.Response(403, text="private upstream body")
        if request.method == "HEAD":
            return httpx2.Response(404)
        return httpx2.Response(200)

    request = EditingExecutionRequest.model_validate(_request(tmp_path))
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        result = await execute_video_editing(request, client=client)

    assert result.status == "failed"
    assert result.failure_code == "dependency_unavailable"
    assert requests == ["PUT", "POST", "DELETE", "HEAD"]


def test_request_rejects_unverified_paths_digests_and_supplier_fields(tmp_path: Path) -> None:
    payload = _request(tmp_path)
    payload["provider"] = "aliyun"
    with pytest.raises(ValueError):
        EditingExecutionRequest.model_validate(payload)

    payload = _request(tmp_path)
    payload["assets"][0]["sha256"] = "0" * 64  # type: ignore[index]
    request = EditingExecutionRequest.model_validate(payload)
    with pytest.raises(ValueError, match="rejected") as failure:
        request.verify_files()
    assert str(tmp_path) not in str(failure.value)


def test_one_shot_protocol_never_reflects_credentials_or_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = _request(tmp_path)
    payload["credential"]["accessKeySecret"] = "bad"  # type: ignore[index]
    exit_code = serve_one_video_editing_request(
        json.dumps(payload).encode(),
        client_factory=lambda: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "bad" not in captured.err
    assert str(tmp_path) not in captured.err


def test_executor_module_dispatches_the_bounded_video_editing_protocol() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "automation_tool.executor", "--execute-video-editing"],
        input=b"{}",
        capture_output=True,
        timeout=30,
    )

    assert completed.returncode == 2
    assert completed.stdout == b""
    assert completed.stderr == b"Video editing request is rejected\n"
