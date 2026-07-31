"""LE-12 T2: authenticated local-editing job protocol and lifecycle."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

import automation_tool.executor.local_editing_worker as worker_protocol_module
from automation_tool.executor.local_editing_worker import (
    LocalEditingCancelCommand,
    LocalEditingStartCommand,
    LocalEditingWorkerBootstrap,
    LocalEditingWorkerBootstrapRejected,
    LocalEditingWorkerFailureCode,
    LocalEditingWorkerPhase,
    LocalEditingWorkerProtocol,
)
from automation_tool.executor.material_probe import PackagedMediaTools

JOB_ID = UUID("123e4567-e89b-42d3-a456-426614174100")
PROJECT_ID = UUID("223e4567-e89b-42d3-a456-426614174101")
TIMELINE_ID = UUID("323e4567-e89b-42d3-a456-426614174102")
ARTIFACT_ID = UUID("423e4567-e89b-42d3-a456-426614174103")
TOKEN = bytes.fromhex("ab" * 32)
COMMAND_DOMAIN = b"automation-tool.video-worker-command.v1\0"
EVENT_DOMAIN = b"automation-tool.video-worker-event.v1\0"


def _executable(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_bytes(b"#!/bin/sh\nexit 0\n")
    path.chmod(0o700)
    return path


def _bootstrap(tmp_path: Path) -> LocalEditingWorkerBootstrap:
    return LocalEditingWorkerBootstrap(
        asset_root=tmp_path,
        media_tools=PackagedMediaTools(
            ffmpeg_path=_executable(tmp_path, "ffmpeg"),
            ffprobe_path=_executable(tmp_path, "ffprobe"),
        ),
        _session_token=TOKEN,
    )


def _proof(domain: bytes, prefix: str, parts: list[str]) -> str:
    message = domain + b"\0".join(part.encode() for part in parts)
    digest = hmac.digest(TOKEN, message, hashlib.sha256)
    return prefix + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _editing_document() -> dict[str, object]:
    return {
        "projectId": str(PROJECT_ID),
        "timelineId": str(TIMELINE_ID),
        "timelineRevision": 7,
    }


def _start_document() -> dict[str, object]:
    editing = _editing_document()
    canonical = json.dumps(editing, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return {
        "authenticationProof": _proof(
            COMMAND_DOMAIN,
            "atvwc1.",
            ["worker.editing.start", "python", "1.0", str(JOB_ID), canonical],
        ),
        "command": "worker.editing.start",
        "editing": editing,
        "jobId": str(JOB_ID),
        "protocolVersion": "1.0",
        "workerKind": "python",
    }


def _cancel_document(job_id: UUID = JOB_ID) -> dict[str, object]:
    return {
        "authenticationProof": _proof(
            COMMAND_DOMAIN,
            "atvwc1.",
            ["worker.cancel", "python", "1.0", str(job_id)],
        ),
        "command": "worker.cancel",
        "jobId": str(job_id),
        "protocolVersion": "1.0",
        "workerKind": "python",
    }


def _line(document: dict[str, object]) -> bytes:
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


def _event(payload: bytes) -> dict[str, object]:
    assert payload.endswith(b"\n")
    value = json.loads(payload)
    assert isinstance(value, dict)
    return value


def _assert_event_proof(document: dict[str, object], detail: str) -> None:
    proof = document["authenticationProof"]
    event = document["event"]
    assert isinstance(proof, str)
    assert isinstance(event, str)
    assert proof == _proof(
        EVENT_DOMAIN,
        "atvwp1.",
        [event, "python", "1.0", "2.0.0", detail],
    )


def test_start_progress_and_success_are_exact_authenticated_events(tmp_path: Path) -> None:
    protocol = LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0")
    assert repr(protocol) == "LocalEditingWorkerProtocol(<redacted>)"

    command = protocol.accept_command(_line(_start_document()))

    assert command == LocalEditingStartCommand(
        job_id=JOB_ID,
        project_id=PROJECT_ID,
        timeline_id=TIMELINE_ID,
        timeline_revision=7,
    )
    assert repr(command) == "LocalEditingStartCommand(<redacted>)"
    for phase, progress in [
        (LocalEditingWorkerPhase.PREPARING, 0),
        (LocalEditingWorkerPhase.RENDERING, 600),
        (LocalEditingWorkerPhase.PUBLISHING, 1000),
    ]:
        document = _event(protocol.progress(JOB_ID, phase, progress))
        assert set(document) == {
            "authenticationProof",
            "event",
            "jobId",
            "phase",
            "progressPermille",
            "protocolVersion",
            "workerKind",
            "workerVersion",
        }
        assert document["phase"] == phase.value
        assert document["progressPermille"] == progress
        _assert_event_proof(document, f"{JOB_ID}\0{phase.value}\0{progress}")

    succeeded = _event(protocol.succeed(JOB_ID, ARTIFACT_ID))
    assert succeeded["event"] == "worker.editing.succeeded"
    assert succeeded["outputArtifactId"] == str(ARTIFACT_ID)
    _assert_event_proof(succeeded, f"{JOB_ID}\0{ARTIFACT_ID}")

    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.progress(JOB_ID, LocalEditingWorkerPhase.PUBLISHING, 1000)


def test_cancel_and_workspace_failure_have_distinct_terminal_events(tmp_path: Path) -> None:
    protocol = LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0")
    protocol.accept_command(_line(_start_document()))
    protocol.progress(JOB_ID, LocalEditingWorkerPhase.PREPARING, 0)

    cancel = protocol.accept_command(_line(_cancel_document()))

    assert cancel == LocalEditingCancelCommand(job_id=JOB_ID)
    assert repr(cancel) == "LocalEditingCancelCommand(<redacted>)"
    cancelled = _event(protocol.cancelled(JOB_ID))
    assert cancelled["event"] == "worker.editing.cancelled"
    _assert_event_proof(cancelled, str(JOB_ID))

    second = LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0")
    second.accept_command(_line(_start_document()))
    failed = _event(second.fail(JOB_ID, LocalEditingWorkerFailureCode.WORKSPACE_UNUSABLE))
    assert failed["event"] == "worker.editing.failed"
    assert failed["failureCode"] == "workspace_unusable"
    _assert_event_proof(failed, f"{JOB_ID}\0workspace_unusable")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(extra=True),
        lambda value: value.update(authenticationProof="atvwc1.forged"),
        lambda value: value.update(command="worker.editing.unknown"),
        lambda value: value.update(workerKind="node"),
        lambda value: value.update(protocolVersion="2.0"),
        lambda value: value.update(jobId="not-a-uuid"),
        lambda value: value.update(jobId=7),
        lambda value: value.update(jobId="00000000-0000-1000-8000-000000000000"),
        lambda value: value.update(editing=[]),
        lambda value: value["editing"].update(extra=True),
        lambda value: value["editing"].update(timelineRevision=0),
        lambda value: value["editing"].update(timelineRevision=True),
        lambda value: value["editing"].update(projectId=str(TIMELINE_ID)),
    ],
)
def test_start_command_fails_closed(
    tmp_path: Path,
    mutate: object,
) -> None:
    document = _start_document()
    assert callable(mutate)
    mutate(document)
    protocol = LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0")

    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.accept_command(_line(document))


def test_protocol_rejects_progress_regression_and_invalid_terminal_order(tmp_path: Path) -> None:
    protocol = LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0")
    protocol.accept_command(_line(_start_document()))

    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.progress(JOB_ID, LocalEditingWorkerPhase.RENDERING, 1)
    protocol.progress(JOB_ID, LocalEditingWorkerPhase.PREPARING, 0)
    protocol.progress(JOB_ID, LocalEditingWorkerPhase.RENDERING, 600)
    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.progress(JOB_ID, LocalEditingWorkerPhase.PREPARING, 700)
    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.progress(JOB_ID, LocalEditingWorkerPhase.RENDERING, 599)
    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.succeed(JOB_ID, ARTIFACT_ID)
    protocol.progress(JOB_ID, LocalEditingWorkerPhase.PUBLISHING, 1000)
    protocol.succeed(JOB_ID, ARTIFACT_ID)


def test_cancel_must_match_the_single_active_job(tmp_path: Path) -> None:
    protocol = LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0")
    protocol.accept_command(_line(_start_document()))
    other = UUID("523e4567-e89b-42d3-a456-426614174104")

    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.accept_command(_line(_cancel_document(other)))
    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.cancelled(JOB_ID)

    protocol.accept_command(_line(_cancel_document()))
    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.accept_command(_line(_cancel_document()))
    protocol.cancelled(JOB_ID)


@pytest.mark.parametrize("payload", [b"", b"{}", b"{}\n\n", b"\xff\n"])
def test_command_wire_shape_is_bounded(tmp_path: Path, payload: bytes) -> None:
    protocol = LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0")

    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.accept_command(payload)


@pytest.mark.parametrize("worker_version", ["", "2", "02.0.0", "1.0.0-01", "x" * 129])
def test_protocol_identity_is_exact(tmp_path: Path, worker_version: str) -> None:
    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        LocalEditingWorkerProtocol(_bootstrap(tmp_path), worker_version)

    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        LocalEditingWorkerProtocol(
            cast(LocalEditingWorkerBootstrap, None),
            "2.0.0",
        )


def test_protocol_accepts_the_same_semver_surface_as_the_rust_launcher(tmp_path: Path) -> None:
    protocol = LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0-rc.1+packaged")

    assert repr(protocol) == "LocalEditingWorkerProtocol(<redacted>)"


def test_cancel_shape_and_proof_fail_closed(tmp_path: Path) -> None:
    protocol = LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0")
    protocol.accept_command(_line(_start_document()))

    extra = _cancel_document()
    extra["extra"] = True
    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.accept_command(_line(extra))

    forged = _cancel_document()
    forged["authenticationProof"] = "atvwc1.forged"
    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.accept_command(_line(forged))


def test_event_builders_reject_raw_values_and_oversized_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol = LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0")
    protocol.accept_command(_line(_start_document()))

    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.progress(JOB_ID, cast(LocalEditingWorkerPhase, "preparing"), 0)
    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.progress(JOB_ID, LocalEditingWorkerPhase.PREPARING, 1001)
    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.fail(
            JOB_ID,
            cast(LocalEditingWorkerFailureCode, "workspace_unusable"),
        )

    monkeypatch.setattr(
        worker_protocol_module,
        "MAX_LOCAL_EDITING_WORKER_BOOTSTRAP_BYTES",
        1,
    )
    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.progress(JOB_ID, LocalEditingWorkerPhase.PREPARING, 0)
