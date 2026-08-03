"""LE-19 T2: authenticated smart-edit Worker lifecycle and two-phase terminal."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

from automation_tool.executor.local_editing_worker import (
    LocalEditingCancelCommand,
    LocalEditingScriptModelConfiguration,
    LocalEditingWorkerBootstrap,
    LocalEditingWorkerBootstrapRejected,
    LocalEditingWorkerProtocol,
    LocalSmartEditAbortCommand,
    LocalSmartEditCommitCommand,
    LocalSmartEditFailureCode,
    LocalSmartEditStartCommand,
    parse_local_editing_worker_bootstrap,
)
from automation_tool.executor.material_probe import PackagedMediaTools
from automation_tool.executor.smart_edit_generation import SmartEditGenerationStage

JOB_ID = UUID("123e4567-e89b-42d3-a456-426614174100")
OTHER_JOB_ID = UUID("223e4567-e89b-42d3-a456-426614174101")
TOKEN = bytes.fromhex("ab" * 32)
RESULT_DIGEST = "cd" * 32
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


def _command(name: str, *, job_id: UUID = JOB_ID) -> dict[str, object]:
    return {
        "authenticationProof": _proof(
            COMMAND_DOMAIN,
            "atvwc1.",
            [name, "python", "1.0", str(job_id)],
        ),
        "command": name,
        "jobId": str(job_id),
        "protocolVersion": "1.0",
        "workerKind": "python",
    }


def _line(document: dict[str, object]) -> bytes:
    return json.dumps(document, separators=(",", ":")).encode() + b"\n"


def _event(payload: bytes) -> dict[str, object]:
    value = json.loads(payload)
    assert isinstance(value, dict)
    return value


def _assert_event_proof(document: dict[str, object], detail: str) -> None:
    assert document["authenticationProof"] == _proof(
        EVENT_DOMAIN,
        "atvwp1.",
        [str(document["event"]), "python", "1.0", "2.0.0", detail],
    )


def _bootstrap_line(tmp_path: Path, script_model: object) -> bytes:
    ffmpeg = _executable(tmp_path, "bootstrap-ffmpeg")
    ffprobe = _executable(tmp_path, "bootstrap-ffprobe")
    return _line(
        {
            "assetRoot": str(tmp_path),
            "bootstrapVersion": "1",
            "enableWebUi": False,
            "localSessionToken": TOKEN.hex(),
            "mediaTools": {
                "ffmpegPath": str(ffmpeg),
                "ffprobePath": str(ffprobe),
            },
            "protocolVersion": "1.0",
            "renderBrowser": None,
            "scriptModel": script_model,
            "workerKind": "python",
        }
    )


def test_bootstrap_accepts_one_locked_redacted_script_model(tmp_path: Path) -> None:
    model = {
        "apiKey": "sk-" + "private" * 4,
        "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "modelId": "qwen3.7-max-2026-06-08",
        "sourceProvider": "bailian",
        "upstreamProvider": "openai",
    }

    bootstrap = parse_local_editing_worker_bootstrap(_bootstrap_line(tmp_path, model))

    assert bootstrap.script_model == LocalEditingScriptModelConfiguration(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model_id="qwen3.7-max-2026-06-08",
        api_key="sk-" + "private" * 4,
    )
    assert "private" not in repr(bootstrap)
    assert "private" not in repr(bootstrap.script_model)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(extra=True),
        lambda value: value.update(apiKey="private"),
        lambda value: value.update(baseUrl="https://example.com/v1"),
        lambda value: value.update(modelId="unlocked-model"),
        lambda value: value.update(sourceProvider="openai"),
        lambda value: value.update(upstreamProvider="bailian"),
    ],
)
def test_bootstrap_rejects_script_model_drift(
    tmp_path: Path,
    mutation: object,
) -> None:
    model = {
        "apiKey": "sk-" + "private" * 4,
        "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "modelId": "qwen3.7-max-2026-06-08",
        "sourceProvider": "bailian",
        "upstreamProvider": "openai",
    }
    assert callable(mutation)
    mutation(model)

    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        parse_local_editing_worker_bootstrap(_bootstrap_line(tmp_path, model))


def test_smart_edit_prepare_commit_and_success_are_one_authenticated_transaction(
    tmp_path: Path,
) -> None:
    protocol = LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0")

    command = protocol.accept_command(_line(_command("worker.smart_edit.start")))

    assert command == LocalSmartEditStartCommand(JOB_ID)
    assert repr(command) == "LocalSmartEditStartCommand(<redacted>)"
    for stage, progress in (
        (SmartEditGenerationStage.PREPARING, 0),
        (SmartEditGenerationStage.ANALYZING, 100),
        (SmartEditGenerationStage.COMPLETED, 1_000),
    ):
        event = _event(protocol.smart_edit_progress(JOB_ID, stage, progress))
        assert event["event"] == "worker.smart_edit.progress"
        assert event["stage"] == stage.value
        assert event["progressPermille"] == progress
        _assert_event_proof(event, f"{JOB_ID}\0{stage.value}\0{progress}")

    prepared = _event(protocol.smart_edit_prepared(JOB_ID, RESULT_DIGEST))
    assert prepared["event"] == "worker.smart_edit.prepared"
    assert prepared["resultDigest"] == RESULT_DIGEST
    _assert_event_proof(prepared, f"{JOB_ID}\0{RESULT_DIGEST}")

    commit = protocol.accept_command(_line(_command("worker.smart_edit.commit")))
    assert commit == LocalSmartEditCommitCommand(JOB_ID)
    succeeded = _event(protocol.smart_edit_succeeded(JOB_ID, RESULT_DIGEST))
    assert succeeded["event"] == "worker.smart_edit.succeeded"
    assert succeeded["resultDigest"] == RESULT_DIGEST
    _assert_event_proof(succeeded, f"{JOB_ID}\0{RESULT_DIGEST}")

    assert protocol.accept_command(
        _line(_command("worker.smart_edit.start", job_id=OTHER_JOB_ID))
    ) == LocalSmartEditStartCommand(OTHER_JOB_ID)


def test_cancel_and_abort_are_distinct_and_release_staged_state(tmp_path: Path) -> None:
    protocol = LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0")
    protocol.accept_command(_line(_command("worker.smart_edit.start")))

    cancel = protocol.accept_command(_line(_command("worker.cancel")))

    assert cancel == LocalEditingCancelCommand(JOB_ID)
    cancelled = _event(protocol.smart_edit_cancelled(JOB_ID))
    assert cancelled["event"] == "worker.smart_edit.cancelled"
    _assert_event_proof(cancelled, str(JOB_ID))

    protocol.accept_command(_line(_command("worker.smart_edit.start")))
    protocol.smart_edit_progress(JOB_ID, SmartEditGenerationStage.PREPARING, 0)
    protocol.smart_edit_progress(JOB_ID, SmartEditGenerationStage.COMPLETED, 1_000)
    protocol.smart_edit_prepared(JOB_ID, RESULT_DIGEST)
    abort = protocol.accept_command(_line(_command("worker.smart_edit.abort")))
    assert abort == LocalSmartEditAbortCommand(JOB_ID)
    aborted = _event(protocol.smart_edit_aborted(JOB_ID))
    assert aborted["event"] == "worker.smart_edit.aborted"
    _assert_event_proof(aborted, str(JOB_ID))


def test_smart_failure_is_closed_and_terminal_order_is_strict(tmp_path: Path) -> None:
    protocol = LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0")
    protocol.accept_command(_line(_command("worker.smart_edit.start")))

    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.smart_edit_prepared(JOB_ID, RESULT_DIGEST)
    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.accept_command(_line(_command("worker.smart_edit.commit")))
    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.smart_edit_progress(JOB_ID, SmartEditGenerationStage.SCRIPTING, 350)

    failed = _event(
        protocol.smart_edit_failed(
            JOB_ID,
            LocalSmartEditFailureCode.UPSTREAM_REJECTED,
        )
    )
    assert failed["event"] == "worker.smart_edit.failed"
    assert failed["failureCode"] == "upstream_rejected"
    _assert_event_proof(failed, f"{JOB_ID}\0upstream_rejected")
    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.smart_edit_failed(JOB_ID, LocalSmartEditFailureCode.LOCAL_FAILED)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(extra=True),
        lambda value: value.update(authenticationProof="atvwc1.forged"),
        lambda value: value.update(protocolVersion="2.0"),
        lambda value: value.update(workerKind="node"),
        lambda value: value.update(jobId="not-a-uuid"),
    ],
)
def test_smart_commands_reject_unknown_fields_bad_proofs_and_identity(
    tmp_path: Path,
    mutation: object,
) -> None:
    document = _command("worker.smart_edit.start")
    assert callable(mutation)
    mutation(document)

    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0").accept_command(_line(document))


def _started(tmp_path: Path) -> LocalEditingWorkerProtocol:
    protocol = LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0")
    protocol.accept_command(_line(_command("worker.smart_edit.start")))
    return protocol


def _prepared(tmp_path: Path) -> LocalEditingWorkerProtocol:
    protocol = _started(tmp_path)
    protocol.smart_edit_progress(JOB_ID, SmartEditGenerationStage.PREPARING, 0)
    protocol.smart_edit_progress(JOB_ID, SmartEditGenerationStage.COMPLETED, 1_000)
    protocol.smart_edit_prepared(JOB_ID, RESULT_DIGEST)
    return protocol


def test_the_two_phase_terminal_commands_never_print_which_job(tmp_path: Path) -> None:
    protocol = _prepared(tmp_path)

    commit = protocol.accept_command(_line(_command("worker.smart_edit.commit")))
    assert repr(commit) == "LocalSmartEditCommitCommand(<redacted>)"

    protocol = _prepared(tmp_path)
    abort = protocol.accept_command(_line(_command("worker.smart_edit.abort")))
    assert repr(abort) == "LocalSmartEditAbortCommand(<redacted>)"


def test_an_operation_in_flight_is_visible_to_the_caller(tmp_path: Path) -> None:
    """The reader asks this to tell "refused" apart from "nothing running"."""
    protocol = LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0")

    assert protocol.has_active_operation() is False
    protocol.accept_command(_line(_command("worker.smart_edit.start")))
    assert protocol.has_active_operation() is True
    protocol.smart_edit_failed(JOB_ID, LocalSmartEditFailureCode.LOCAL_FAILED)
    assert protocol.has_active_operation() is False


def test_a_terminal_command_of_the_wrong_shape_is_refused(tmp_path: Path) -> None:
    mutations: list[tuple[str, Callable[[dict[str, object]], object]]] = [
        ("an unknown field", lambda value: value.update(extra=True)),
        ("a missing field", lambda value: value.pop("jobId")),
        ("another protocol version", lambda value: value.update(protocolVersion="2.0")),
        ("another worker kind", lambda value: value.update(workerKind="node")),
    ]
    for label, mutate in mutations:
        protocol = _prepared(tmp_path)
        document = _command("worker.smart_edit.commit")
        mutate(document)

        with pytest.raises(LocalEditingWorkerBootstrapRejected):
            protocol.accept_command(_line(document))
        assert label


def test_progress_refuses_a_stage_or_reading_it_cannot_use(tmp_path: Path) -> None:
    protocol = _started(tmp_path)

    cases: list[tuple[str, object, object]] = [
        ("a stage from outside the closed set", "preparing", 0),
        ("a reading that is not an int", SmartEditGenerationStage.PREPARING, 0.0),
        ("a negative reading", SmartEditGenerationStage.PREPARING, -1),
        ("a reading past the end", SmartEditGenerationStage.PREPARING, 1_001),
        ("a first stage that is not the first", SmartEditGenerationStage.ANALYZING, 0),
        ("a first reading that is not zero", SmartEditGenerationStage.PREPARING, 1),
    ]
    for label, stage, progress in cases:
        with pytest.raises(LocalEditingWorkerBootstrapRejected):
            protocol.smart_edit_progress(JOB_ID, cast(Any, stage), cast(Any, progress))
        assert label


def test_progress_never_runs_backwards(tmp_path: Path) -> None:
    """A later reading may repeat, but it may not undo what was already reported."""
    protocol = _started(tmp_path)
    protocol.smart_edit_progress(JOB_ID, SmartEditGenerationStage.PREPARING, 0)
    protocol.smart_edit_progress(JOB_ID, SmartEditGenerationStage.ANALYZING, 500)

    for label, stage, progress in [
        ("an earlier stage", SmartEditGenerationStage.PREPARING, 500),
        ("a smaller reading", SmartEditGenerationStage.ANALYZING, 499),
    ]:
        with pytest.raises(LocalEditingWorkerBootstrapRejected):
            protocol.smart_edit_progress(JOB_ID, stage, progress)
        assert label

    protocol.smart_edit_progress(JOB_ID, SmartEditGenerationStage.ANALYZING, 500)


def test_progress_is_refused_once_the_result_is_staged(tmp_path: Path) -> None:
    protocol = _prepared(tmp_path)

    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.smart_edit_progress(JOB_ID, SmartEditGenerationStage.COMPLETED, 1_000)


def test_success_must_name_the_digest_that_was_staged(tmp_path: Path) -> None:
    """Committing one result and announcing another would publish an unverified file."""
    protocol = _prepared(tmp_path)
    protocol.accept_command(_line(_command("worker.smart_edit.commit")))

    for label, digest in [
        ("a different digest", "ef" * 32),
        ("a digest of the wrong shape", "not-a-digest"),
        ("a digest that is not text", cast(str, None)),
    ]:
        with pytest.raises(LocalEditingWorkerBootstrapRejected):
            protocol.smart_edit_succeeded(JOB_ID, digest)
        assert label

    protocol.smart_edit_succeeded(JOB_ID, RESULT_DIGEST)


def test_success_is_refused_before_the_commit_arrives(tmp_path: Path) -> None:
    protocol = _prepared(tmp_path)

    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.smart_edit_succeeded(JOB_ID, RESULT_DIGEST)


def test_failure_must_name_a_reason_from_the_closed_set(tmp_path: Path) -> None:
    protocol = _started(tmp_path)

    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.smart_edit_failed(JOB_ID, cast(Any, "local_failed"))

    protocol.smart_edit_failed(JOB_ID, LocalSmartEditFailureCode.LOCAL_FAILED)


def test_cancellation_is_only_announced_after_one_was_asked_for(tmp_path: Path) -> None:
    protocol = _started(tmp_path)

    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.smart_edit_cancelled(JOB_ID)


def test_a_staged_result_may_not_be_cancelled_away(tmp_path: Path) -> None:
    """Once staged the only exits are commit and abort, which say what happened."""
    protocol = _prepared(tmp_path)

    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.accept_command(_line(_command("worker.cancel")))


def test_an_abort_is_only_announced_after_one_was_asked_for(tmp_path: Path) -> None:
    protocol = _prepared(tmp_path)

    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.smart_edit_aborted(JOB_ID)

    protocol.accept_command(_line(_command("worker.smart_edit.commit")))
    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.smart_edit_aborted(JOB_ID)
