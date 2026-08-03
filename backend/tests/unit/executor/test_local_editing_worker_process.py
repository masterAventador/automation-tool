"""The packaged Worker turns one private App job bundle into a real render call."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from automation_tool.executor.audio_rendering import AudioRenderSourceBinding
from automation_tool.executor.local_editing_worker import (
    LocalEditingStartCommand,
    LocalEditingWorkerBootstrap,
    LocalEditingWorkerFailureCode,
    parse_local_editing_worker_bootstrap,
)
from automation_tool.executor.local_editing_worker_process import (
    LocalEditingRenderDiagnosticCode,
    LocalEditingRenderRejected,
    execute_local_editing_job,
)
from automation_tool.executor.material_probe import (
    MATERIAL_PATH_REGISTRY_FILE_NAME,
    MaterialPathRegistry,
)
from automation_tool.executor.visual_render_execution import (
    VisualRenderExecutionRejected,
    VisualRenderExecutionRejection,
    VisualRenderReceipt,
)
from automation_tool.executor.visual_rendering import VisualRenderSourceBinding
from automation_tool.protocol.local_rendering import LocalEditingVisualRenderPlan

JOB_ID = UUID("00000000-0000-4000-8000-000000000001")
PROJECT_ID = UUID("00000000-0000-4000-8000-000000000002")
TIMELINE_ID = UUID("00000000-0000-4000-8000-000000000003")
MATERIAL_ID = UUID("00000000-0000-4000-8000-000000000004")
ARTIFACT_ID = UUID("00000000-0000-4000-8000-000000000005")
NARRATION_ID = UUID("00000000-0000-4000-8000-000000000006")


def private_directory(path: Path) -> Path:
    path.mkdir(parents=True, mode=0o700)
    if os.name == "posix":
        path.chmod(0o700)
    return path


def bootstrap(app_data: Path) -> LocalEditingWorkerBootstrap:
    tools = private_directory(app_data / "tools")
    ffmpeg = tools / "ffmpeg"
    ffprobe = tools / "ffprobe"
    for path in (ffmpeg, ffprobe):
        path.write_bytes(b"controlled executable")
        path.chmod(0o700)
    return parse_local_editing_worker_bootstrap(
        (
            json.dumps(
                {
                    "assetRoot": str(app_data),
                    "bootstrapVersion": "1",
                    "enableWebUi": False,
                    "localSessionToken": "11" * 32,
                    "mediaTools": {
                        "ffmpegPath": str(ffmpeg),
                        "ffprobePath": str(ffprobe),
                    },
                    "protocolVersion": "1.0",
                    "renderBrowser": None,
                    "scriptModel": None,
                    "workerKind": "python",
                },
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
    )


def render_request() -> dict[str, object]:
    return {
        "schemaVersion": "local-editing-render-request.v1",
        "jobId": str(JOB_ID),
        "project": {
            "projectId": str(PROJECT_ID),
            "title": "真实出片",
            "output": {"width": 1280, "height": 720, "fps": 25},
            "captionStyle": {
                "fontKey": "noto-sans-cjk-sc",
                "fontPx": 42,
                "strokePx": 2,
                "lineSpacing": 1.2,
            },
            "createdAt": "2026-08-01T00:00:00Z",
        },
        "timeline": {
            "timelineId": str(TIMELINE_ID),
            "projectId": str(PROJECT_ID),
            "revision": 1,
            "durationMs": 1000,
            "tracks": [
                {
                    "trackId": "track-visual",
                    "kind": "visual",
                    "clips": [
                        {
                            "clipId": "clip-visual",
                            "startMs": 0,
                            "durationMs": 1000,
                            "sourceMaterialId": str(MATERIAL_ID),
                            "sourceInMs": 0,
                            "sourceOutMs": 1000,
                            "text": None,
                            "gainDb": None,
                            "transitionIn": None,
                            "originalAudioMode": None,
                        }
                    ],
                }
            ],
            "createdAt": "2026-08-01T00:00:01Z",
        },
        "materials": [{"materialId": str(MATERIAL_ID), "hasAudio": False}],
    }


def prepare_job(tmp_path: Path) -> tuple[Path, Path]:
    app_data = private_directory(tmp_path / "app-data")
    state = private_directory(app_data / "local-executor" / "state")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"controlled source")
    MaterialPathRegistry(state_directory=state).register(MATERIAL_ID, source)
    checkpoints = private_directory(
        app_data / "video-workspaces-v1" / "jobs" / str(JOB_ID) / "checkpoints"
    )
    private_directory(checkpoints.parent / "outputs")
    (checkpoints / "local-editing-render-request.checkpoint").write_text(
        json.dumps(render_request(), separators=(",", ":")), encoding="utf-8"
    )
    return app_data, source


def test_job_bundle_builds_existing_render_plan_and_uses_registered_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_data, source = prepare_job(tmp_path)
    observed: dict[str, object] = {}

    def render(
        tools: object,
        plan: object,
        sources: object,
        approvals: object,
        task_directory: Path,
        **options: object,
    ) -> VisualRenderReceipt:
        observed.update(
            {
                "tools": tools,
                "plan": plan,
                "sources": sources,
                "approvals": approvals,
                "taskDirectory": task_directory,
                "options": options,
            }
        )
        (task_directory / "render.mp4").write_bytes(b"real render boundary")
        return VisualRenderReceipt(
            frame_count=25,
            width=1280,
            height=720,
            fps=25,
            duration_ms=1000,
            bytes_written=20,
            sha256="a" * 64,
        )

    monkeypatch.setattr(
        "automation_tool.executor.local_editing_worker_process.execute_visual_render",
        render,
    )

    artifact_id = execute_local_editing_job(
        bootstrap(app_data),
        LocalEditingStartCommand(JOB_ID, PROJECT_ID, TIMELINE_ID, 1),
        cancel_requested=lambda: False,
        artifact_id_factory=lambda: ARTIFACT_ID,
    )

    assert artifact_id == ARTIFACT_ID
    plan = cast("LocalEditingVisualRenderPlan", observed["plan"])
    sources = cast("tuple[VisualRenderSourceBinding, ...]", observed["sources"])
    assert plan.project_id == PROJECT_ID
    assert plan.timeline_id == TIMELINE_ID
    assert sources[0].source_path == source.resolve()
    assert observed["taskDirectory"] == (
        app_data / "video-workspaces-v1" / "jobs" / str(JOB_ID) / "outputs"
    )


def test_job_bundle_deduplicates_one_static_source_reused_by_multiple_clips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_data, source = prepare_job(tmp_path)
    checkpoint = (
        app_data
        / "video-workspaces-v1"
        / "jobs"
        / str(JOB_ID)
        / "checkpoints"
        / "local-editing-render-request.checkpoint"
    )
    request = render_request()
    timeline = cast("dict[str, object]", request["timeline"])
    visual_track = cast("list[dict[str, object]]", timeline["tracks"])[0]
    clips = cast("list[dict[str, object]]", visual_track["clips"])
    clips[0].update(
        {
            "durationMs": 500,
            "sourceInMs": None,
            "sourceOutMs": None,
        }
    )
    clips.append(
        {
            **clips[0],
            "clipId": "clip-visual-second",
            "startMs": 500,
        }
    )
    checkpoint.write_text(json.dumps(request, separators=(",", ":")), encoding="utf-8")
    observed: dict[str, object] = {}

    def render(
        tools: object,
        plan: object,
        sources: object,
        approvals: object,
        task_directory: Path,
        **options: object,
    ) -> VisualRenderReceipt:
        observed["sources"] = sources
        observed["approvals"] = approvals
        (task_directory / "render.mp4").write_bytes(b"real render boundary")
        return VisualRenderReceipt(
            frame_count=25,
            width=1280,
            height=720,
            fps=25,
            duration_ms=1000,
            bytes_written=20,
            sha256="a" * 64,
        )

    monkeypatch.setattr(
        "automation_tool.executor.local_editing_worker_process.execute_visual_render",
        render,
    )

    execute_local_editing_job(
        bootstrap(app_data),
        LocalEditingStartCommand(JOB_ID, PROJECT_ID, TIMELINE_ID, 1),
        cancel_requested=lambda: False,
        artifact_id_factory=lambda: ARTIFACT_ID,
    )

    sources = cast("tuple[VisualRenderSourceBinding, ...]", observed["sources"])
    approvals = cast("tuple[object, ...]", observed["approvals"])
    assert len(sources) == 1
    assert sources[0].material_id == MATERIAL_ID
    assert sources[0].source_path == source.resolve()
    assert len(approvals) == 1


def test_job_bundle_fails_closed_when_the_command_and_checkpoint_disagree(
    tmp_path: Path,
) -> None:
    app_data, _ = prepare_job(tmp_path)

    with pytest.raises(LocalEditingRenderRejected) as rejected:
        execute_local_editing_job(
            bootstrap(app_data),
            LocalEditingStartCommand(JOB_ID, PROJECT_ID, TIMELINE_ID, 2),
            cancel_requested=lambda: False,
            artifact_id_factory=lambda: ARTIFACT_ID,
        )

    assert rejected.value.code is LocalEditingWorkerFailureCode.INVALID_TIMELINE
    assert str(rejected.value) == "local editing render rejected"


def test_job_bundle_preserves_a_path_free_missing_registry_diagnostic(tmp_path: Path) -> None:
    app_data, _ = prepare_job(tmp_path)
    (app_data / "local-executor" / "state" / MATERIAL_PATH_REGISTRY_FILE_NAME).unlink()

    with pytest.raises(LocalEditingRenderRejected) as rejected:
        execute_local_editing_job(
            bootstrap(app_data),
            LocalEditingStartCommand(JOB_ID, PROJECT_ID, TIMELINE_ID, 1),
            cancel_requested=lambda: False,
            artifact_id_factory=lambda: ARTIFACT_ID,
        )

    assert rejected.value.code is LocalEditingWorkerFailureCode.MATERIAL_UNAVAILABLE
    assert rejected.value.diagnostic is LocalEditingRenderDiagnosticCode.REGISTRY_FILE_MISSING
    assert repr(rejected.value) == "LocalEditingRenderRejected(<redacted>)"


def _write_checkpoint(app_data: Path, document: dict[str, object]) -> None:
    checkpoints = app_data / "video-workspaces-v1" / "jobs" / str(JOB_ID) / "checkpoints"
    (checkpoints / "local-editing-render-request.checkpoint").write_text(
        json.dumps(document, separators=(",", ":")), encoding="utf-8"
    )


def _start_command(**overrides: object) -> LocalEditingStartCommand:
    arguments: dict[str, object] = {
        "job_id": JOB_ID,
        "project_id": PROJECT_ID,
        "timeline_id": TIMELINE_ID,
        "timeline_revision": 1,
    }
    arguments.update(overrides)
    return LocalEditingStartCommand(**arguments)  # type: ignore[arg-type]


def _execute(prepared: LocalEditingWorkerBootstrap, command: LocalEditingStartCommand) -> UUID:
    return execute_local_editing_job(
        prepared,
        command,
        cancel_requested=lambda: False,
        artifact_id_factory=lambda: ARTIFACT_ID,
    )


def test_job_refuses_a_checkpoint_written_for_another_schema_or_job(tmp_path: Path) -> None:
    app_data, _source = prepare_job(tmp_path)
    prepared = bootstrap(app_data)

    for label, changes in [
        ("schema version", {"schemaVersion": "local-editing-render-request.v2"}),
        ("job id", {"jobId": str(PROJECT_ID)}),
    ]:
        document = render_request()
        document.update(changes)
        _write_checkpoint(app_data, document)

        with pytest.raises(LocalEditingRenderRejected) as caught:
            _execute(prepared, _start_command())
        assert caught.value.code is LocalEditingWorkerFailureCode.INVALID_TIMELINE, label


def test_job_refuses_a_checkpoint_that_names_a_different_project_or_revision(
    tmp_path: Path,
) -> None:
    """The command and the checkpoint must agree on exactly what is being rendered."""
    app_data, _source = prepare_job(tmp_path)
    prepared = bootstrap(app_data)
    other = UUID("00000000-0000-4000-8000-00000000009f")

    for label, command in [
        ("project id", _start_command(project_id=other)),
        ("timeline id", _start_command(timeline_id=other)),
        ("timeline revision", _start_command(timeline_revision=2)),
    ]:
        with pytest.raises(LocalEditingRenderRejected) as caught:
            _execute(prepared, command)
        assert caught.value.code is LocalEditingWorkerFailureCode.INVALID_TIMELINE, label


def test_job_refuses_a_timeline_that_belongs_to_another_project(tmp_path: Path) -> None:
    app_data, _source = prepare_job(tmp_path)
    document = render_request()
    timeline = cast(dict[str, object], document["timeline"])
    timeline["projectId"] = "00000000-0000-4000-8000-0000000000aa"
    _write_checkpoint(app_data, document)

    with pytest.raises(LocalEditingRenderRejected) as caught:
        _execute(bootstrap(app_data), _start_command())

    assert caught.value.code is LocalEditingWorkerFailureCode.INVALID_TIMELINE


def test_job_refuses_a_clip_whose_material_the_checkpoint_never_bound(
    tmp_path: Path,
) -> None:
    """A clip may only use material the same document declared."""
    app_data, _source = prepare_job(tmp_path)
    document = render_request()
    document["materials"] = [
        {"materialId": "00000000-0000-4000-8000-0000000000bb", "hasAudio": False}
    ]
    _write_checkpoint(app_data, document)

    with pytest.raises(LocalEditingRenderRejected) as caught:
        _execute(bootstrap(app_data), _start_command())

    assert caught.value.code is LocalEditingWorkerFailureCode.MATERIAL_UNAVAILABLE
    assert caught.value.diagnostic is LocalEditingRenderDiagnosticCode.BINDING_MISSING


def test_job_reports_an_unregistered_material_without_naming_a_path(
    tmp_path: Path,
) -> None:
    app_data = private_directory(tmp_path / "app-data")
    state = private_directory(app_data / "local-executor" / "state")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"controlled source")
    registry = MaterialPathRegistry(state_directory=state)
    registry.register(UUID("00000000-0000-4000-8000-0000000000cc"), source)
    checkpoints = private_directory(
        app_data / "video-workspaces-v1" / "jobs" / str(JOB_ID) / "checkpoints"
    )
    private_directory(checkpoints.parent / "outputs")
    _write_checkpoint(app_data, render_request())

    with pytest.raises(LocalEditingRenderRejected) as caught:
        _execute(bootstrap(app_data), _start_command())

    assert caught.value.code is LocalEditingWorkerFailureCode.MATERIAL_UNAVAILABLE
    assert str(source) not in str(caught.value)
    assert repr(caught.value) == "LocalEditingRenderRejected(<redacted>)"


def _receipt() -> VisualRenderReceipt:
    return VisualRenderReceipt(
        frame_count=25,
        width=1280,
        height=720,
        fps=25,
        duration_ms=1000,
        bytes_written=20,
        sha256="a" * 64,
    )


def test_job_reports_an_unforeseen_planning_failure_as_a_timeline_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The catch-all exists for errors nobody enumerated, so the test raises one.

    Every failure this module knows how to name is already refused with its own
    code above; what is asserted here is that anything else still leaves as one
    closed worker code rather than as whatever the planner happened to raise.
    """
    app_data, _source = prepare_job(tmp_path)

    def explode(*_args: object, **_options: object) -> object:
        raise RuntimeError("planner defect")

    monkeypatch.setattr(
        "automation_tool.executor.local_editing_worker_process"
        ".create_local_editing_caption_render_plan",
        explode,
    )

    with pytest.raises(LocalEditingRenderRejected) as caught:
        _execute(bootstrap(app_data), _start_command())

    assert caught.value.code is LocalEditingWorkerFailureCode.INVALID_TIMELINE
    assert caught.value.diagnostic is LocalEditingRenderDiagnosticCode.REJECTED
    assert str(caught.value) == "local editing render rejected"


def test_job_renders_audio_with_the_visual_track_when_the_timeline_has_sound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A timeline with an audio clip must not fall through to the picture-only call."""
    app_data, _source = prepare_job(tmp_path)
    narration_source = tmp_path / "narration.m4a"
    narration_source.write_bytes(b"controlled narration")
    MaterialPathRegistry(
        state_directory=app_data / "local-executor" / "state",
    ).register(NARRATION_ID, narration_source)
    document = render_request()
    timeline = cast(dict[str, object], document["timeline"])
    tracks = cast(list[dict[str, object]], timeline["tracks"])
    tracks.append(
        {
            "trackId": "track-narration",
            "kind": "narration",
            "clips": [
                {
                    "clipId": "clip-narration",
                    "startMs": 0,
                    "durationMs": 1000,
                    "sourceMaterialId": str(NARRATION_ID),
                    "sourceInMs": 0,
                    "sourceOutMs": 1000,
                    "text": None,
                    # The narration lane requires a level on every clip: the
                    # domain refuses an audible clip that states no gain.
                    "gainDb": -6.0,
                    "transitionIn": None,
                    "originalAudioMode": None,
                }
            ],
        }
    )
    materials = cast(list[dict[str, object]], document["materials"])
    materials.append({"materialId": str(NARRATION_ID), "hasAudio": True})
    _write_checkpoint(app_data, document)
    observed: dict[str, object] = {}

    def audiovisual(
        tools: object,
        visual_plan: object,
        visual_sources: object,
        visual_approvals: object,
        audio_plan: object,
        audio_sources: object,
        audio_approvals: object,
        task_directory: Path,
        **options: object,
    ) -> VisualRenderReceipt:
        observed["audioSources"] = audio_sources
        observed["audioApprovals"] = audio_approvals
        (task_directory / "render.mp4").write_bytes(b"real render boundary")
        return _receipt()

    def picture_only(*_args: object, **_options: object) -> VisualRenderReceipt:
        raise AssertionError("a timeline with audio must not take the picture-only call")

    monkeypatch.setattr(
        "automation_tool.executor.local_editing_worker_process.execute_audiovisual_render",
        audiovisual,
    )
    monkeypatch.setattr(
        "automation_tool.executor.local_editing_worker_process.execute_visual_render",
        picture_only,
    )

    assert _execute(bootstrap(app_data), _start_command()) == ARTIFACT_ID
    audio_sources = cast(tuple[AudioRenderSourceBinding, ...], observed["audioSources"])
    assert [binding.material_id for binding in audio_sources] == [NARRATION_ID]
    assert audio_sources[0].source_path == narration_source.resolve()
    assert audio_sources[0].has_audio is True
    assert len(cast(tuple[object, ...], observed["audioApprovals"])) == 1


def test_job_translates_a_render_rejection_into_a_worker_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_data, _source = prepare_job(tmp_path)
    prepared = bootstrap(app_data)

    for label, rejection, expected in [
        (
            "the shipped encoder is missing",
            VisualRenderExecutionRejection.TOOL_UNAVAILABLE,
            LocalEditingWorkerFailureCode.RENDER_FAILED,
        ),
        (
            "the source moved mid-render",
            VisualRenderExecutionRejection.SOURCE_CHANGED,
            LocalEditingWorkerFailureCode.MATERIAL_UNAVAILABLE,
        ),
        (
            "the output exceeded its budget",
            VisualRenderExecutionRejection.OUTPUT_TOO_LARGE,
            LocalEditingWorkerFailureCode.RESOURCE_EXHAUSTED,
        ),
    ]:

        def refuse(
            *_args: object,
            _rejection: VisualRenderExecutionRejection = rejection,
            **_options: object,
        ) -> VisualRenderReceipt:
            raise VisualRenderExecutionRejected(_rejection)

        monkeypatch.setattr(
            "automation_tool.executor.local_editing_worker_process.execute_visual_render",
            refuse,
        )

        with pytest.raises(LocalEditingRenderRejected) as caught:
            _execute(prepared, _start_command())

        assert caught.value.code is expected, label


def test_job_refuses_an_artifact_identifier_nothing_could_look_up_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The render succeeded, so the only way to say so is a usable identifier."""
    app_data, _source = prepare_job(tmp_path)
    prepared = bootstrap(app_data)

    def render(
        _tools: object,
        _plan: object,
        _sources: object,
        _approvals: object,
        task_directory: Path,
        **_options: object,
    ) -> VisualRenderReceipt:
        (task_directory / "render.mp4").write_bytes(b"real render boundary")
        return _receipt()

    monkeypatch.setattr(
        "automation_tool.executor.local_editing_worker_process.execute_visual_render",
        render,
    )

    for label, factory in [
        ("not a uuid at all", lambda: cast(UUID, "00000000-0000-4000-8000-000000000005")),
        ("the nil uuid, which has no version", lambda: UUID(int=0)),
        ("a version 1 uuid", lambda: UUID("11111111-1111-1111-8111-111111111111")),
        ("a non-rfc-4122 variant", lambda: UUID("11111111-1111-4111-c111-111111111111")),
    ]:
        with pytest.raises(LocalEditingRenderRejected) as caught:
            execute_local_editing_job(
                prepared,
                _start_command(),
                cancel_requested=lambda: False,
                artifact_id_factory=factory,
            )

        assert caught.value.code is LocalEditingWorkerFailureCode.RENDER_FAILED, label


def test_job_refuses_a_timeline_no_render_plan_can_be_built_from(tmp_path: Path) -> None:
    """Plan construction failing is a timeline problem, not a render failure."""
    app_data, _source = prepare_job(tmp_path)
    document = render_request()
    timeline = cast(dict[str, object], document["timeline"])
    tracks = cast(list[dict[str, object]], timeline["tracks"])
    clips = cast(list[dict[str, object]], tracks[0]["clips"])
    clips[0]["startMs"] = 999_999

    _write_checkpoint(app_data, document)

    with pytest.raises(LocalEditingRenderRejected) as caught:
        _execute(bootstrap(app_data), _start_command())

    assert caught.value.code is LocalEditingWorkerFailureCode.INVALID_TIMELINE
