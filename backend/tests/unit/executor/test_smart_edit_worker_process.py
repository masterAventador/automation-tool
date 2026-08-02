"""LE-19 T2: private staging, response publication, commit and abort compensation."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from automation_tool.control_plane.domain import (
    DescriptionSource,
    Material,
    MaterialId,
    MaterialKind,
)
from automation_tool.executor.local_editing_worker import (
    LocalEditingScriptModelConfiguration,
    LocalEditingWorkerBootstrap,
    LocalSmartEditFailureCode,
    LocalSmartEditStartCommand,
)
from automation_tool.executor.material_probe import (
    MaterialPathRegistry,
    MaterialPathRegistryRejected,
    MaterialPathRegistryRejection,
    PackagedMediaTools,
)
from automation_tool.executor.material_speech_pipeline import LocalAudibleSpeechAnalyzer
from automation_tool.executor.motion_authoring.authoring_workspace import (
    AuthoringWorkspace,
)
from automation_tool.executor.smart_edit_generation import (
    SmartEditGenerationCancelled,
    SmartEditGenerationPipeline,
    SmartEditGenerationResult,
    SmartEditGenerationStage,
    SmartEditMaterialAnalysis,
    SmartEditNarrationRegistration,
)
from automation_tool.executor.smart_edit_worker_process import (
    LocalSmartEditStagedJob,
    LocalSmartEditWorkerCancelled,
    LocalSmartEditWorkerRejected,
    abort_smart_edit_job,
    commit_smart_edit_job,
    create_local_smart_edit_pipeline,
    prepare_smart_edit_job,
)
from automation_tool.protocol.local_editing import (
    LocalEditingTimelineDraft,
    LocalEditingTimelineParagraph,
    LocalEditingTimelineParagraphKind,
)

JOB_ID = UUID("123e4567-e89b-42d3-a456-426614174100")
VISUAL_ID = UUID("223e4567-e89b-42d3-a456-426614174101")
NARRATION_ID = UUID("323e4567-e89b-42d3-a456-426614174102")
PROMPT = "把绝密新品素材剪成一条短片"
VOICEOVER = b"private generated voiceover"


def _private(path: Path) -> Path:
    path.mkdir(parents=True, mode=0o700)
    if os.name != "nt":
        path.chmod(0o700)
    return path


def _executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return path


def _bootstrap(tmp_path: Path) -> LocalEditingWorkerBootstrap:
    app_data = _private(tmp_path / "app-data")
    tools = _private(tmp_path / "tools")
    return LocalEditingWorkerBootstrap(
        asset_root=app_data,
        media_tools=PackagedMediaTools(
            ffmpeg_path=_executable(tools / "ffmpeg"),
            ffprobe_path=_executable(tools / "ffprobe"),
        ),
        _session_token=b"x" * 32,
    )


def _material() -> Material:
    return Material.register(
        material_id=MaterialId(VISUAL_ID),
        kind=MaterialKind.VIDEO,
        duration_ms=4_000,
        width=720,
        height=1_280,
        content_digest="a" * 64,
        has_audio=False,
        audio_loudness_lufs=None,
        has_speech=False,
        speech_segments_ms=(),
        speech_transcript=None,
        shot_boundaries_ms=(0, 2_000),
        ai_description="新品发布会产品特写",
        ai_tags=("新品",),
        description_source=DescriptionSource.AI,
        described_at=datetime(2026, 8, 1, tzinfo=UTC),
    )


def _material_document(material: Material) -> dict[str, object]:
    return {
        "aiDescription": material.ai_description,
        "aiTags": list(material.ai_tags),
        "audioLoudnessLufs": material.audio_loudness_lufs,
        "contentDigest": material.content_digest,
        "describedAt": "2026-08-01T00:00:00Z",
        "descriptionSource": material.description_source.value,
        "durationMs": material.duration_ms,
        "hasAudio": material.has_audio,
        "hasSpeech": material.has_speech,
        "height": material.height,
        "kind": material.kind.value,
        "materialId": str(material.material_id),
        "shotBoundariesMs": list(material.shot_boundaries_ms),
        "speechSegmentsMs": [],
        "speechTranscript": material.speech_transcript,
        "width": material.width,
    }


def _request_path(bootstrap: LocalEditingWorkerBootstrap) -> Path:
    job = _private(bootstrap.asset_root / "local-executor" / "smart-edit" / "jobs" / str(JOB_ID))
    request = job / "request.json"
    request.write_text(
        json.dumps(
            {
                "enableThinking": False,
                "jobId": str(JOB_ID),
                "materials": [_material_document(_material())],
                "prompt": PROMPT,
                "schemaVersion": "smart-edit-generation-request.v1",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return request


def test_shipped_pipeline_builds_a_concrete_audible_speech_analyzer(tmp_path: Path) -> None:
    bootstrap = replace(
        _bootstrap(tmp_path),
        script_model=LocalEditingScriptModelConfiguration(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model_id="qwen3.7-max-2026-06-08",
            api_key="sk-" + "private" * 4,
        ),
    )
    workspace_root = _private(tmp_path / "workspace")
    source = tmp_path / "speech.mp4"
    source.write_bytes(b"speech")

    pipeline = create_local_smart_edit_pipeline(
        bootstrap,
        AuthoringWorkspace(workspace_root),
    )
    analyzer = pipeline.audible_speech_analyzer_factory(source, source.stat())

    assert isinstance(analyzer, LocalAudibleSpeechAnalyzer)


def _result() -> SmartEditGenerationResult:
    draft = LocalEditingTimelineDraft(
        paragraphs=(
            LocalEditingTimelineParagraph(
                sequence=1,
                kind=LocalEditingTimelineParagraphKind.NARRATED,
                visual_material_id=VISUAL_ID,
                audio_material_id=NARRATION_ID,
                duration_ms=800,
                visual_source_in_ms=0,
                visual_source_out_ms=800,
                caption_text="新品亮点旁白。",
            ),
        )
    )
    return SmartEditGenerationResult(
        draft=draft,
        analysis_updates=(SmartEditMaterialAnalysis.from_material(_material()),),
        narration_registrations=(
            SmartEditNarrationRegistration(
                sequence=1,
                material_id=NARRATION_ID,
                relative_path="voiceover/sentence-0001.wav",
                duration_ms=800,
                content_digest=hashlib.sha256(VOICEOVER).hexdigest(),
                bytes_written=len(VOICEOVER),
            ),
        ),
    )


class _Pipeline:
    def __init__(self, workspace: AuthoringWorkspace) -> None:
        self.workspace = workspace

    def prepare(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError

    def segment(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError

    def synthesize(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError

    def match(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError

    def bind_narration(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError


def _install_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def generate(
        pipeline: _Pipeline,
        *,
        prompt: str,
        materials: tuple[Material, ...],
        enable_thinking: bool,
        progress: object,
        cancellation_requested: object,
    ) -> SmartEditGenerationResult:
        assert prompt == PROMPT
        assert materials == (_material(),)
        assert enable_thinking is False
        assert callable(progress)
        assert callable(cancellation_requested)
        assert cancellation_requested() is False
        progress(SmartEditGenerationStage.PREPARING, 0)
        pipeline.workspace.write_bytes("voiceover/sentence-0001.wav", VOICEOVER)
        progress(SmartEditGenerationStage.COMPLETED, 1_000)
        return _result()

    monkeypatch.setattr(
        "automation_tool.executor.smart_edit_worker_process.generate_smart_edit_timeline_draft",
        generate,
    )


def test_prepare_writes_path_free_digest_bound_result_then_commit_registers_audio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap = _bootstrap(tmp_path)
    request = _request_path(bootstrap)
    _install_success(monkeypatch)
    progress: list[tuple[SmartEditGenerationStage, int]] = []

    staged = prepare_smart_edit_job(
        bootstrap,
        LocalSmartEditStartCommand(JOB_ID),
        pipeline_factory=lambda workspace: cast(SmartEditGenerationPipeline, _Pipeline(workspace)),
        cancel_requested=lambda: False,
        progress=lambda stage, value: progress.append((stage, value)),
    )

    assert isinstance(staged, LocalSmartEditStagedJob)
    assert repr(staged) == "LocalSmartEditStagedJob(<redacted>)"
    payload = staged.response_path.read_bytes()
    assert staged.result_digest == hashlib.sha256(payload).hexdigest()
    assert os.fspath(bootstrap.asset_root) not in payload.decode()
    assert PROMPT not in payload.decode()
    assert progress == [
        (SmartEditGenerationStage.PREPARING, 0),
        (SmartEditGenerationStage.COMPLETED, 1_000),
    ]

    commit_smart_edit_job(bootstrap, staged)

    assert not request.exists()
    assert not staged.response_path.exists()
    source, _approved = MaterialPathRegistry(
        state_directory=bootstrap.asset_root / "local-executor" / "state"
    ).resolve(NARRATION_ID)
    assert source.read_bytes() == VOICEOVER
    assert str(JOB_ID) in source.parts


def test_abort_removes_request_response_and_generated_files_without_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap = _bootstrap(tmp_path)
    request = _request_path(bootstrap)
    _install_success(monkeypatch)
    staged = prepare_smart_edit_job(
        bootstrap,
        LocalSmartEditStartCommand(JOB_ID),
        pipeline_factory=lambda workspace: cast(SmartEditGenerationPipeline, _Pipeline(workspace)),
        cancel_requested=lambda: False,
        progress=lambda _stage, _value: None,
    )

    abort_smart_edit_job(staged)

    assert not request.exists()
    assert not staged.response_path.exists()
    assert not staged.workspace_root.exists()
    with pytest.raises(Exception, match="material path registry rejected"):
        MaterialPathRegistry(
            state_directory=bootstrap.asset_root / "local-executor" / "state"
        ).resolve(NARRATION_ID)


def test_cancel_during_generation_aborts_every_private_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap = _bootstrap(tmp_path)
    request = _request_path(bootstrap)

    def cancelled(*_args: object, **_kwargs: object) -> SmartEditGenerationResult:
        raise SmartEditGenerationCancelled

    monkeypatch.setattr(
        "automation_tool.executor.smart_edit_worker_process.generate_smart_edit_timeline_draft",
        cancelled,
    )

    with pytest.raises(LocalSmartEditWorkerCancelled):
        prepare_smart_edit_job(
            bootstrap,
            LocalSmartEditStartCommand(JOB_ID),
            pipeline_factory=lambda workspace: cast(
                SmartEditGenerationPipeline, _Pipeline(workspace)
            ),
            cancel_requested=lambda: True,
            progress=lambda _stage, _value: None,
        )

    assert not request.exists()
    assert not request.parent.exists()


def test_commit_rejects_a_voiceover_that_changed_after_prepare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap = _bootstrap(tmp_path)
    _request_path(bootstrap)
    _install_success(monkeypatch)
    staged = prepare_smart_edit_job(
        bootstrap,
        LocalSmartEditStartCommand(JOB_ID),
        pipeline_factory=lambda workspace: cast(SmartEditGenerationPipeline, _Pipeline(workspace)),
        cancel_requested=lambda: False,
        progress=lambda _stage, _value: None,
    )
    narration = staged.workspace_root / "voiceover" / "sentence-0001.wav"
    narration.write_bytes(b"x" * len(VOICEOVER))

    with pytest.raises(LocalSmartEditWorkerRejected) as rejected:
        commit_smart_edit_job(bootstrap, staged)

    assert rejected.value.code is LocalSmartEditFailureCode.COMMIT_FAILED
    assert narration.exists()
    assert not (
        bootstrap.asset_root / "local-executor" / "generated-materials" / str(JOB_ID)
    ).exists()


def test_commit_rejects_a_result_document_that_changed_after_prepare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap = _bootstrap(tmp_path)
    _request_path(bootstrap)
    _install_success(monkeypatch)
    staged = prepare_smart_edit_job(
        bootstrap,
        LocalSmartEditStartCommand(JOB_ID),
        pipeline_factory=lambda workspace: cast(SmartEditGenerationPipeline, _Pipeline(workspace)),
        cancel_requested=lambda: False,
        progress=lambda _stage, _value: None,
    )
    staged.response_path.write_bytes(b"{}")

    with pytest.raises(LocalSmartEditWorkerRejected) as rejected:
        commit_smart_edit_job(bootstrap, staged)

    assert rejected.value.code is LocalSmartEditFailureCode.COMMIT_FAILED
    assert staged.workspace_root.exists()
    assert not (
        bootstrap.asset_root / "local-executor" / "generated-materials" / str(JOB_ID)
    ).exists()


def test_commit_rejects_a_voiceover_whose_digest_cannot_be_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap = _bootstrap(tmp_path)
    _request_path(bootstrap)
    _install_success(monkeypatch)
    staged = prepare_smart_edit_job(
        bootstrap,
        LocalSmartEditStartCommand(JOB_ID),
        pipeline_factory=lambda workspace: cast(SmartEditGenerationPipeline, _Pipeline(workspace)),
        cancel_requested=lambda: False,
        progress=lambda _stage, _value: None,
    )

    def unreadable(_source: Path) -> str:
        raise OSError("operator private path must not escape")

    monkeypatch.setattr(
        "automation_tool.executor.smart_edit_worker_process.read_content_digest",
        unreadable,
    )
    with pytest.raises(LocalSmartEditWorkerRejected) as rejected:
        commit_smart_edit_job(bootstrap, staged)

    assert rejected.value.code is LocalSmartEditFailureCode.COMMIT_FAILED
    assert rejected.value.__cause__ is None
    assert rejected.value.__context__ is None


def test_commit_cleans_durable_audio_when_registry_and_rollback_both_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap = _bootstrap(tmp_path)
    _request_path(bootstrap)
    _install_success(monkeypatch)
    staged = prepare_smart_edit_job(
        bootstrap,
        LocalSmartEditStartCommand(JOB_ID),
        pipeline_factory=lambda workspace: cast(SmartEditGenerationPipeline, _Pipeline(workspace)),
        cancel_requested=lambda: False,
        progress=lambda _stage, _value: None,
    )
    real_rename = os.rename
    renames = 0

    def rename(source: Path, target: Path) -> None:
        nonlocal renames
        renames += 1
        if renames == 1:
            real_rename(source, target)
            return
        raise OSError("rollback unavailable")

    def reject_registry(_bootstrap: LocalEditingWorkerBootstrap) -> MaterialPathRegistry:
        raise LocalSmartEditWorkerRejected(LocalSmartEditFailureCode.COMMIT_FAILED)

    monkeypatch.setattr(os, "rename", rename)
    monkeypatch.setattr(
        "automation_tool.executor.smart_edit_worker_process._state_registry",
        reject_registry,
    )
    with pytest.raises(LocalSmartEditWorkerRejected) as rejected:
        commit_smart_edit_job(bootstrap, staged)

    assert rejected.value.code is LocalSmartEditFailureCode.COMMIT_FAILED
    durable = bootstrap.asset_root / "local-executor" / "generated-materials" / str(JOB_ID)
    assert not durable.exists()


def test_commit_closes_the_registry_failure_exception_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap = _bootstrap(tmp_path)
    _request_path(bootstrap)
    _install_success(monkeypatch)
    staged = prepare_smart_edit_job(
        bootstrap,
        LocalSmartEditStartCommand(JOB_ID),
        pipeline_factory=lambda workspace: cast(SmartEditGenerationPipeline, _Pipeline(workspace)),
        cancel_requested=lambda: False,
        progress=lambda _stage, _value: None,
    )

    def reject_registry(*_args: object, **_kwargs: object) -> MaterialPathRegistry:
        raise MaterialPathRegistryRejected(MaterialPathRegistryRejection.REGISTRY_UNWRITABLE)

    monkeypatch.setattr(
        "automation_tool.executor.smart_edit_worker_process.MaterialPathRegistry",
        reject_registry,
    )
    with pytest.raises(LocalSmartEditWorkerRejected) as rejected:
        commit_smart_edit_job(bootstrap, staged)

    assert rejected.value.code is LocalSmartEditFailureCode.COMMIT_FAILED
    assert rejected.value.__cause__ is None
    assert rejected.value.__context__ is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink boundary")
def test_commit_refuses_a_symlinked_generated_material_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap = _bootstrap(tmp_path)
    _request_path(bootstrap)
    _install_success(monkeypatch)
    staged = prepare_smart_edit_job(
        bootstrap,
        LocalSmartEditStartCommand(JOB_ID),
        pipeline_factory=lambda workspace: cast(SmartEditGenerationPipeline, _Pipeline(workspace)),
        cancel_requested=lambda: False,
        progress=lambda _stage, _value: None,
    )
    outside = _private(tmp_path / "outside")
    generated = bootstrap.asset_root / "local-executor" / "generated-materials"
    generated.parent.mkdir(parents=True, exist_ok=True)
    generated.symlink_to(outside, target_is_directory=True)

    with pytest.raises(LocalSmartEditWorkerRejected) as rejected:
        commit_smart_edit_job(bootstrap, staged)

    assert rejected.value.code is LocalSmartEditFailureCode.COMMIT_FAILED
    assert list(outside.iterdir()) == []
    assert staged.workspace_root.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink boundary")
def test_prepare_refuses_a_symlinked_private_job_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap = _bootstrap(tmp_path)
    jobs = bootstrap.asset_root / "local-executor" / "smart-edit" / "jobs"
    jobs.mkdir(parents=True)
    outside_job = _private(tmp_path / "outside-job")
    job_root = jobs / str(JOB_ID)
    job_root.symlink_to(outside_job, target_is_directory=True)
    request = outside_job / "request.json"
    request.write_text(
        json.dumps(
            {
                "enableThinking": False,
                "jobId": str(JOB_ID),
                "materials": [_material_document(_material())],
                "prompt": PROMPT,
                "schemaVersion": "smart-edit-generation-request.v1",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    _install_success(monkeypatch)

    with pytest.raises(LocalSmartEditWorkerRejected) as rejected:
        prepare_smart_edit_job(
            bootstrap,
            LocalSmartEditStartCommand(JOB_ID),
            pipeline_factory=lambda workspace: cast(
                SmartEditGenerationPipeline, _Pipeline(workspace)
            ),
            cancel_requested=lambda: False,
            progress=lambda _stage, _value: None,
        )

    assert rejected.value.code is LocalSmartEditFailureCode.WORKSPACE_UNUSABLE
    assert request.exists()
    assert not (outside_job / "staging").exists()
