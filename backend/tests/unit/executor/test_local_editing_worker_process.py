"""The packaged Worker turns one private App job bundle into a real render call."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

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
from automation_tool.executor.visual_render_execution import VisualRenderReceipt
from automation_tool.executor.visual_rendering import VisualRenderSourceBinding
from automation_tool.protocol.local_rendering import LocalEditingVisualRenderPlan

JOB_ID = UUID("00000000-0000-4000-8000-000000000001")
PROJECT_ID = UUID("00000000-0000-4000-8000-000000000002")
TIMELINE_ID = UUID("00000000-0000-4000-8000-000000000003")
MATERIAL_ID = UUID("00000000-0000-4000-8000-000000000004")
ARTIFACT_ID = UUID("00000000-0000-4000-8000-000000000005")


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
