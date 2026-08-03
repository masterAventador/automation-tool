"""LE-12 T2: authenticated local-editing job protocol and lifecycle."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

import automation_tool.executor.local_editing_worker as worker_protocol_module
from automation_tool.executor.local_editing_worker import (
    MAX_LOCAL_EDITING_WORKER_BOOTSTRAP_BYTES,
    LocalEditingCancelCommand,
    LocalEditingStartCommand,
    LocalEditingWorkerBootstrap,
    LocalEditingWorkerBootstrapRejected,
    LocalEditingWorkerFailureCode,
    LocalEditingWorkerPhase,
    LocalEditingWorkerProtocol,
    LocalMaterialForgetCommand,
    LocalMaterialImportCommand,
    LocalMaterialStatusCommand,
    LocalMaterialWorkerFailureCode,
    LocalMaterialWorkerStatus,
    _material_facts_document,
)
from automation_tool.executor.material_probe import (
    MaterialFacts,
    MaterialPathRegistryRejection,
    MaterialProbeRejection,
    PackagedMediaTools,
    ProbedMaterialKind,
)

JOB_ID = UUID("123e4567-e89b-42d3-a456-426614174100")
PROJECT_ID = UUID("223e4567-e89b-42d3-a456-426614174101")
TIMELINE_ID = UUID("323e4567-e89b-42d3-a456-426614174102")
ARTIFACT_ID = UUID("423e4567-e89b-42d3-a456-426614174103")
MATERIAL_ID = UUID("623e4567-e89b-42d3-a456-426614174105")
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


def _import_document(source: Path) -> dict[str, object]:
    return {
        "authenticationProof": _proof(
            COMMAND_DOMAIN,
            "atvwc1.",
            [
                "worker.material.import",
                "python",
                "1.0",
                str(MATERIAL_ID),
                str(source),
            ],
        ),
        "command": "worker.material.import",
        "materialId": str(MATERIAL_ID),
        "protocolVersion": "1.0",
        "sourcePath": str(source),
        "workerKind": "python",
    }


def _forget_document() -> dict[str, object]:
    return {
        "authenticationProof": _proof(
            COMMAND_DOMAIN,
            "atvwc1.",
            ["worker.material.forget", "python", "1.0", str(MATERIAL_ID)],
        ),
        "command": "worker.material.forget",
        "materialId": str(MATERIAL_ID),
        "protocolVersion": "1.0",
        "workerKind": "python",
    }


def _status_document() -> dict[str, object]:
    return {
        "authenticationProof": _proof(
            COMMAND_DOMAIN,
            "atvwc1.",
            ["worker.material.status", "python", "1.0", str(MATERIAL_ID)],
        ),
        "command": "worker.material.status",
        "materialId": str(MATERIAL_ID),
        "protocolVersion": "1.0",
        "workerKind": "python",
    }


def _material_facts() -> MaterialFacts:
    return MaterialFacts(
        kind=ProbedMaterialKind.VIDEO,
        duration_ms=1234,
        width=720,
        height=1280,
        video_codec="h264",
        audio_codec="aac",
        has_audio=True,
        audio_loudness_lufs=-18.25,
        content_digest="cd" * 32,
    )


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


def test_material_import_is_authenticated_and_returns_only_path_free_facts(
    tmp_path: Path,
) -> None:
    source = (tmp_path / "private source.mp4").resolve()
    protocol = LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0")

    command = protocol.accept_command(_line(_import_document(source)))

    assert command == LocalMaterialImportCommand(MATERIAL_ID, source)
    assert repr(command) == "LocalMaterialImportCommand(<redacted>)"
    imported = _event(protocol.material_imported(MATERIAL_ID, _material_facts()))
    assert set(imported) == {
        "authenticationProof",
        "event",
        "facts",
        "materialId",
        "protocolVersion",
        "workerKind",
        "workerVersion",
    }
    assert imported["event"] == "worker.material.imported"
    assert imported["materialId"] == str(MATERIAL_ID)
    assert imported["facts"] == {
        "audioLoudnessLufs": -18.25,
        "contentDigest": "cd" * 32,
        "durationMs": 1234,
        "hasAudio": True,
        "height": 1280,
        "kind": "video",
        "width": 720,
    }
    serialized = json.dumps(imported, sort_keys=True, separators=(",", ":"))
    assert str(source) not in serialized
    canonical = json.dumps(
        imported["facts"], ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    _assert_event_proof(imported, f"{MATERIAL_ID}\0{canonical}")


def test_material_import_failure_is_closed_and_releases_the_single_operation(
    tmp_path: Path,
) -> None:
    source = (tmp_path / "private source.mp4").resolve()
    protocol = LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0")
    protocol.accept_command(_line(_import_document(source)))

    failed = _event(
        protocol.material_import_failed(
            MATERIAL_ID, LocalMaterialWorkerFailureCode.SOURCE_NOT_AT_REST
        )
    )

    assert failed["event"] == "worker.material.import_failed"
    assert failed["failureCode"] == "source_not_at_rest"
    assert str(source) not in json.dumps(failed)
    _assert_event_proof(failed, f"{MATERIAL_ID}\0source_not_at_rest")
    assert protocol.accept_command(_line(_forget_document())) == LocalMaterialForgetCommand(
        MATERIAL_ID
    )


def test_material_failure_vocabulary_is_the_exact_probe_and_registry_union() -> None:
    assert {item.value for item in LocalMaterialWorkerFailureCode} == {
        item.value for item in MaterialProbeRejection
    } | {item.value for item in MaterialPathRegistryRejection}


def test_material_forget_is_authenticated_idempotent_compensation(
    tmp_path: Path,
) -> None:
    protocol = LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0")

    command = protocol.accept_command(_line(_forget_document()))

    assert command == LocalMaterialForgetCommand(MATERIAL_ID)
    assert repr(command) == "LocalMaterialForgetCommand(<redacted>)"
    forgotten = _event(protocol.material_forgotten(MATERIAL_ID))
    assert forgotten["event"] == "worker.material.forgotten"
    _assert_event_proof(forgotten, str(MATERIAL_ID))
    protocol.accept_command(_line(_forget_document()))
    protocol.material_forgotten(MATERIAL_ID)

    failed_protocol = LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0")
    failed_protocol.accept_command(_line(_forget_document()))
    failed = _event(
        failed_protocol.material_forget_failed(
            MATERIAL_ID, LocalMaterialWorkerFailureCode.REGISTRY_UNWRITABLE
        )
    )
    assert failed["event"] == "worker.material.forget_failed"
    assert failed["failureCode"] == "registry_unwritable"
    _assert_event_proof(failed, f"{MATERIAL_ID}\0registry_unwritable")


def test_material_status_is_authenticated_path_free_and_closed(tmp_path: Path) -> None:
    protocol = LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0")

    command = protocol.accept_command(_line(_status_document()))

    assert command == LocalMaterialStatusCommand(MATERIAL_ID)
    assert repr(command) == "LocalMaterialStatusCommand(<redacted>)"
    event = _event(protocol.material_status(MATERIAL_ID, LocalMaterialWorkerStatus.AVAILABLE))
    assert event["event"] == "worker.material.status"
    assert event["status"] == "available"
    _assert_event_proof(event, f"{MATERIAL_ID}\0available")

    protocol.accept_command(_line(_status_document()))
    missing = _event(protocol.material_status(MATERIAL_ID, LocalMaterialWorkerStatus.FILE_MISSING))
    assert missing["status"] == "file_missing"
    _assert_event_proof(missing, f"{MATERIAL_ID}\0file_missing")


def test_material_status_vocabulary_is_available_plus_registry_reasons() -> None:
    assert {item.value for item in LocalMaterialWorkerStatus} == {"available"} | {
        item.value for item in MaterialPathRegistryRejection
    }


@pytest.mark.parametrize(
    "factory, mutation",
    [
        (_import_document, lambda document: document.update(extra=True)),
        (_import_document, lambda document: document.update(authenticationProof="atvwc1.forged")),
        (_import_document, lambda document: document.update(materialId=str(JOB_ID))),
        (_import_document, lambda document: document.update(materialId="not-a-uuid")),
        (_import_document, lambda document: document.update(sourcePath="relative.mp4")),
        (_import_document, lambda document: document.update(sourcePath="/tmp/a\nb.mp4")),
        (_import_document, lambda document: document.update(sourcePath="/" + "a" * 4097)),
        (_forget_document, lambda document: document.update(extra=True)),
        (_forget_document, lambda document: document.update(authenticationProof="atvwc1.forged")),
        (_status_document, lambda document: document.update(extra=True)),
        (_status_document, lambda document: document.update(authenticationProof="atvwc1.forged")),
    ],
)
def test_material_commands_fail_closed(
    tmp_path: Path,
    factory: object,
    mutation: object,
) -> None:
    assert callable(factory)
    assert callable(mutation)
    source = (tmp_path / "private source.mp4").resolve()
    document = factory(source) if factory is _import_document else factory()
    mutation(document)

    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0").accept_command(_line(document))


def test_render_and_material_commands_share_one_operation_slot(tmp_path: Path) -> None:
    source = (tmp_path / "private source.mp4").resolve()
    protocol = LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0")
    protocol.accept_command(_line(_import_document(source)))

    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.accept_command(_line(_start_document()))
    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.accept_command(_line(_forget_document()))

    protocol.material_import_failed(MATERIAL_ID, LocalMaterialWorkerFailureCode.UNREADABLE)
    protocol.accept_command(_line(_start_document()))
    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.accept_command(_line(_import_document(source)))


def test_material_event_builders_reject_wrong_operation_and_invalid_facts(
    tmp_path: Path,
) -> None:
    source = (tmp_path / "private source.mp4").resolve()
    protocol = LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0")
    protocol.accept_command(_line(_import_document(source)))

    invalid = MaterialFacts(
        kind=ProbedMaterialKind.VIDEO,
        duration_ms=0,
        width=720,
        height=1280,
        video_codec="h264",
        audio_codec=None,
        has_audio=False,
        audio_loudness_lufs=None,
        content_digest="cd" * 32,
    )
    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.material_imported(MATERIAL_ID, invalid)
    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.material_forgotten(MATERIAL_ID)


@pytest.mark.parametrize(
    "facts, expected",
    [
        (
            MaterialFacts(
                kind=ProbedMaterialKind.IMAGE,
                duration_ms=None,
                width=640,
                height=360,
                video_codec="png",
                audio_codec=None,
                has_audio=False,
                audio_loudness_lufs=None,
                content_digest="ef" * 32,
            ),
            {"kind": "image", "durationMs": None, "hasAudio": False},
        ),
        (
            MaterialFacts(
                kind=ProbedMaterialKind.AUDIO,
                duration_ms=2000,
                width=None,
                height=None,
                video_codec=None,
                audio_codec="aac",
                has_audio=True,
                audio_loudness_lufs=-20.0,
                content_digest="12" * 32,
            ),
            {"kind": "audio", "durationMs": 2000, "hasAudio": True},
        ),
    ],
)
def test_material_events_accept_every_probe_kind(
    tmp_path: Path,
    facts: MaterialFacts,
    expected: dict[str, object],
) -> None:
    source = (tmp_path / "private source").resolve()
    protocol = LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0")
    protocol.accept_command(_line(_import_document(source)))

    document = _event(protocol.material_imported(MATERIAL_ID, facts))

    actual = document["facts"]
    assert isinstance(actual, dict)
    assert {key: actual[key] for key in expected} == expected


def _facts(**overrides: Any) -> MaterialFacts:
    return replace(_material_facts(), **overrides)


def test_probe_facts_are_projected_only_when_they_describe_one_coherent_file() -> None:
    """The CP registers what this returns, so an incoherent shape is refused here.

    Each case below is a file that could not exist: a picture with a running
    time, a sound with a frame size, a level for something with no sound. Letting
    any of them through would register a material nothing can later render.
    """
    image = _facts(
        kind=ProbedMaterialKind.IMAGE,
        duration_ms=None,
        has_audio=False,
        audio_loudness_lufs=None,
    )
    audio = _facts(kind=ProbedMaterialKind.AUDIO, width=None, height=None)

    cases: list[tuple[str, object]] = [
        ("something that is not probe facts", object()),
        ("a kind from outside the closed set", _facts(kind=cast(Any, "video"))),
        ("a digest that is not text", _facts(content_digest=cast(Any, b"cd" * 32))),
        ("a digest of the wrong shape", _facts(content_digest="not a digest")),
        ("an audio flag that is not a bool", _facts(has_audio=cast(Any, 1))),
        ("a level that is not a float", _facts(audio_loudness_lufs=cast(Any, -18))),
        ("a level that is not finite", _facts(audio_loudness_lufs=float("nan"))),
        ("a level below the floor", _facts(audio_loudness_lufs=-70.5)),
        ("a level above zero", _facts(audio_loudness_lufs=0.5)),
        ("a picture with a running time", replace(image, duration_ms=1)),
        ("a picture that claims sound", replace(image, has_audio=True)),
        ("a picture with a level", replace(image, audio_loudness_lufs=-18.0)),
        ("a sound with a frame width", replace(audio, width=720)),
        ("a sound with a frame height", replace(audio, height=1280)),
        ("a sound that claims no sound", replace(audio, has_audio=False)),
        ("a video with no frame size", _facts(width=None, height=None)),
        ("a video wider than the ceiling", _facts(width=1_000_000)),
        ("a level on something with no sound", _facts(has_audio=False)),
    ]
    for label, facts in cases:
        with pytest.raises(LocalEditingWorkerBootstrapRejected):
            _material_facts_document(facts)
        assert label

    assert _material_facts_document(_material_facts())["hasAudio"] is True


def test_a_material_failure_must_name_a_reason_from_the_closed_set(tmp_path: Path) -> None:
    source = (tmp_path / "private source.mp4").resolve()

    protocol = LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0")
    protocol.accept_command(_line(_import_document(source)))
    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.material_import_failed(MATERIAL_ID, cast(Any, "unreadable"))
    protocol.material_import_failed(MATERIAL_ID, LocalMaterialWorkerFailureCode.UNREADABLE)

    protocol = LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0")
    protocol.accept_command(_line(_forget_document()))
    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.material_forget_failed(MATERIAL_ID, cast(Any, "registry_unwritable"))
    protocol.material_forget_failed(MATERIAL_ID, LocalMaterialWorkerFailureCode.REGISTRY_UNWRITABLE)

    protocol = LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0")
    protocol.accept_command(_line(_status_document()))
    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.material_status(MATERIAL_ID, cast(Any, "available"))
    protocol.material_status(MATERIAL_ID, LocalMaterialWorkerStatus.AVAILABLE)


def test_a_projection_that_cannot_be_serialised_is_still_a_worker_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing may leave this process except a line of the protocol.

    The projection above narrows every value to a finite scalar, so today this
    cannot happen -- which is exactly why the guard is worth pinning: were the
    projection ever widened, the caller must still see a refusal rather than a
    `TypeError` escaping into the pipe as an unparseable crash.
    """
    source = (tmp_path / "private source.mp4").resolve()
    protocol = LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0")
    protocol.accept_command(_line(_import_document(source)))
    monkeypatch.setattr(
        "automation_tool.executor.local_editing_worker._material_facts_document",
        lambda _facts: {"kind": object()},
    )

    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol.material_imported(MATERIAL_ID, _material_facts())


def test_a_material_event_larger_than_one_line_is_refused(tmp_path: Path) -> None:
    """The reader on the other end frames on newlines and caps what it will read."""
    protocol = LocalEditingWorkerProtocol(_bootstrap(tmp_path), "2.0.0")
    protocol.accept_command(_line(_status_document()))

    with pytest.raises(LocalEditingWorkerBootstrapRejected):
        protocol._material_event(
            "worker.material.status",
            MATERIAL_ID,
            str(MATERIAL_ID),
            status="a" * (MAX_LOCAL_EDITING_WORKER_BOOTSTRAP_BYTES + 1),
        )
