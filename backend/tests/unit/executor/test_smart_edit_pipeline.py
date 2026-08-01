"""LE-19 T1: the concrete local smart-edit production pipeline."""

from __future__ import annotations

import hashlib
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest

from automation_tool.control_plane.domain import (
    DescriptionSource,
    Material,
    MaterialId,
    MaterialKind,
)
from automation_tool.executor.adaptive_frame_extraction import AdaptiveFrameArtifact
from automation_tool.executor.material_probe import (
    MaterialFacts,
    MaterialPathRegistry,
    MediaStreamFacts,
    PackagedMediaTools,
    ProbedMaterialKind,
)
from automation_tool.executor.material_speech_analysis import MaterialSpeechAnalysis
from automation_tool.executor.material_understanding import (
    MaterialUnderstandingAdapter,
    MaterialUnderstandingResult,
    MaterialUnderstandingShot,
)
from automation_tool.executor.motion_authoring.agent import AuthoringWorkspace
from automation_tool.executor.motion_authoring.voiceover import VoiceoverConfig
from automation_tool.executor.script_segmentation import (
    ScriptSegmentationAdapter,
    ScriptSegmentationResult,
    ScriptSentence,
)
from automation_tool.executor.script_voiceover import (
    ScriptVoiceoverClip,
    ScriptVoiceoverResult,
)
from automation_tool.executor.segment_selection import (
    VerifiedDecodableInterval,
    VerifiedDecodableMaterial,
)
from automation_tool.executor.semantic_matching import SemanticMatchingAdapter
from automation_tool.executor.smart_edit_generation import (
    SmartEditGenerationCancelled,
    SmartEditGenerationRejected,
)
from automation_tool.executor.smart_edit_pipeline import LocalSmartEditGenerationPipeline


def _executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return path


def _material(*, user_description: bool = False, has_audio: bool = True) -> Material:
    return Material.register(
        material_id=MaterialId.new(),
        kind=MaterialKind.VIDEO,
        duration_ms=4_000,
        width=720,
        height=1_280,
        content_digest="a" * 64,
        has_audio=has_audio,
        audio_loudness_lufs=-18.0 if has_audio else None,
        has_speech=False,
        speech_segments_ms=(),
        speech_transcript=None,
        shot_boundaries_ms=(),
        ai_description="人工写的产品描述" if user_description else None,
        ai_tags=(),
        description_source=(DescriptionSource.USER if user_description else DescriptionSource.AI),
        described_at=None,
    )


def _image_material() -> Material:
    return Material.register(
        material_id=MaterialId.new(),
        kind=MaterialKind.IMAGE,
        duration_ms=None,
        width=720,
        height=1_280,
        content_digest="b" * 64,
        has_audio=False,
        audio_loudness_lufs=None,
        has_speech=False,
        speech_segments_ms=(),
        speech_transcript=None,
        shot_boundaries_ms=(),
        ai_description=None,
        ai_tags=(),
        description_source=DescriptionSource.AI,
        described_at=None,
    )


def _pipeline(
    tmp_path: Path,
    registry: MaterialPathRegistry,
    tools: PackagedMediaTools,
) -> LocalSmartEditGenerationPipeline:
    workspace_root = tmp_path / "generation"
    workspace_root.mkdir()
    return LocalSmartEditGenerationPipeline(
        tools=tools,
        registry=registry,
        workspace=AuthoringWorkspace(workspace_root),
        understanding_adapter=cast(MaterialUnderstandingAdapter, object()),
        audible_speech_analyzer_factory=lambda _source, _approved: object(),
        script_adapter=cast(ScriptSegmentationAdapter, object()),
        semantic_adapter=cast(SemanticMatchingAdapter, object()),
        voiceover_config=VoiceoverConfig(
            base_url="https://dashscope.aliyuncs.com/api/v1",
            model_id="cosyvoice-v3-flash",
            api_key="sk-" + "x" * 32,
            voice="longxiaochun_v2",
            audio_host_suffixes=(".aliyuncs.com",),
        ),
        now=lambda: datetime(2026, 8, 1, tzinfo=UTC),
        material_id_factory=uuid4,
    )


def test_prepare_proves_every_local_source_before_models_and_returns_updates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    registry = MaterialPathRegistry(state_directory=state)
    tools = PackagedMediaTools(
        ffprobe_path=_executable(tmp_path / "ffprobe"),
        ffmpeg_path=_executable(tmp_path / "ffmpeg"),
    )
    first = _material()
    second = _material()
    protected = _material(user_description=True)
    for material in (first, second, protected):
        source = tmp_path / f"{material.material_id}.mp4"
        source.write_bytes(b"video")
        registry.register(material.material_id.uuid, source)

    events: list[str] = []

    def probe(_tools: object, source: Path) -> MaterialFacts:
        events.append(f"probe:{source.name}")
        return MaterialFacts(
            kind=ProbedMaterialKind.VIDEO,
            duration_ms=4_000,
            width=720,
            height=1_280,
            video_codec="h264",
            audio_codec="aac",
            has_audio=True,
            audio_loudness_lufs=-18.0,
            content_digest="a" * 64,
        )

    def decode(
        _tools: object,
        _source: Path,
        _approved: os.stat_result,
        *,
        material_id: UUID,
        content_digest: str,
        duration_ms: int,
        cancellation_requested: object,
    ) -> VerifiedDecodableMaterial:
        assert callable(cancellation_requested)
        events.append("decode")
        return VerifiedDecodableMaterial(
            material_id=material_id,
            content_digest=content_digest,
            intervals=(VerifiedDecodableInterval(0, duration_ms),),
        )

    def extract(*_args: object, **_kwargs: object) -> tuple[AdaptiveFrameArtifact, ...]:
        events.append("extract")
        return (AdaptiveFrameArtifact("frame-000001.jpg", 0, True, 4),)

    def understand(*_args: object, **_kwargs: object) -> MaterialUnderstandingResult:
        events.append("understand")
        assert events.count("probe:" + protected.material_id.__str__() + ".mp4") == 1
        assert events.count("decode") == 3
        assert events.count("extract") == 2
        return MaterialUnderstandingResult(
            request_id="understanding-request",
            description="AI 产品发布会特写",
            tags=("产品",),
            shots=(MaterialUnderstandingShot(0, 4_000, "产品特写"),),
        )

    monkeypatch.setattr("automation_tool.executor.smart_edit_pipeline.probe_material", probe)
    monkeypatch.setattr(
        "automation_tool.executor.smart_edit_pipeline.verify_decodable_video", decode
    )
    monkeypatch.setattr(
        "automation_tool.executor.smart_edit_pipeline.extract_adaptive_frames", extract
    )
    monkeypatch.setattr(
        "automation_tool.executor.smart_edit_pipeline.understand_material_artifacts",
        understand,
    )
    monkeypatch.setattr(
        "automation_tool.executor.smart_edit_pipeline.analyze_material_speech",
        lambda *_args, **_kwargs: MaterialSpeechAnalysis(
            True,
            ((200, 1_200),),
            "真实原声",
        ),
    )

    result = _pipeline(tmp_path, registry, tools).prepare(
        (first, second, protected),
        cancellation_requested=lambda: False,
    )

    assert len(result.decodable_materials) == 3
    assert result.materials[0].ai_description == "AI 产品发布会特写"
    assert result.materials[0].shot_boundaries_ms == (0,)
    assert result.materials[0].has_speech is True
    assert result.materials[1].ai_description == "AI 产品发布会特写"
    assert result.materials[2].ai_description == "人工写的产品描述"
    assert events.count("understand") == 2
    assert tuple(update.material_id for update in result.analysis_updates) == (
        first.material_id.uuid,
        second.material_id.uuid,
        protected.material_id.uuid,
    )
    assert all(".mp4" not in repr(update) for update in result.analysis_updates)


def test_narration_binding_measures_private_output_and_returns_digest_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    registry = MaterialPathRegistry(state_directory=state)
    tools = PackagedMediaTools(
        ffprobe_path=_executable(tmp_path / "ffprobe"),
        ffmpeg_path=_executable(tmp_path / "ffmpeg"),
    )
    pipeline = _pipeline(tmp_path, registry, tools)
    payload = b"RIFF-private-wave"
    output = pipeline.workspace.write_bytes("voiceover/sentence-0001.wav", payload)
    script = ScriptSegmentationResult(
        request_id="script-request",
        sentences=(ScriptSentence(1, "一句旁白。"),),
    )
    voiceovers = ScriptVoiceoverResult(
        script_request_id=script.request_id,
        clips=(
            ScriptVoiceoverClip(
                sentence=script.sentences[0],
                relative_path="voiceover/sentence-0001.wav",
                duration_ms=880,
                bytes_written=len(payload),
            ),
        ),
    )
    monkeypatch.setattr(
        "automation_tool.executor.smart_edit_pipeline.read_stream_facts",
        lambda _tools, source: (
            MediaStreamFacts(
                kind=ProbedMaterialKind.AUDIO,
                duration_ms=880,
                width=None,
                height=None,
                video_codec=None,
                audio_codec="pcm_s16le",
            )
            if source == output
            else None
        ),
    )

    result = pipeline.bind_narration(
        voiceovers,
        cancellation_requested=lambda: False,
    )

    assert result.registrations[0].content_digest == hashlib.sha256(payload).hexdigest()
    assert result.registrations[0].binding == result.bindings[0]
    assert os.fspath(output) not in repr(result)


def test_prepare_maps_decode_cancel_to_generation_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    registry = MaterialPathRegistry(state_directory=state)
    tools = PackagedMediaTools(
        ffprobe_path=_executable(tmp_path / "ffprobe"),
        ffmpeg_path=_executable(tmp_path / "ffmpeg"),
    )
    material = _material()
    source = tmp_path / "cancel.mp4"
    source.write_bytes(b"video")
    registry.register(material.material_id.uuid, source)
    monkeypatch.setattr(
        "automation_tool.executor.smart_edit_pipeline.probe_material",
        lambda *_args: MaterialFacts(
            ProbedMaterialKind.VIDEO,
            4_000,
            720,
            1_280,
            "h264",
            "aac",
            True,
            -18.0,
            "a" * 64,
        ),
    )

    with pytest.raises(SmartEditGenerationCancelled):
        _pipeline(tmp_path, registry, tools).prepare(
            (material,),
            cancellation_requested=lambda: True,
        )


def test_path_bearing_local_failure_is_closed_without_exception_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    registry = MaterialPathRegistry(state_directory=state)
    tools = PackagedMediaTools(
        ffprobe_path=_executable(tmp_path / "ffprobe"),
        ffmpeg_path=_executable(tmp_path / "ffmpeg"),
    )
    material = _material()
    source = tmp_path / "绝密客户素材.mp4"
    source.write_bytes(b"video")
    registry.register(material.material_id.uuid, source)

    def fail_probe(*_args: object) -> MaterialFacts:
        raise OSError(5, "private probe failure", source)

    monkeypatch.setattr(
        "automation_tool.executor.smart_edit_pipeline.probe_material",
        fail_probe,
    )

    with pytest.raises(SmartEditGenerationRejected) as captured:
        _pipeline(tmp_path, registry, tools).prepare(
            (material,),
            cancellation_requested=lambda: False,
        )

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert os.fspath(source) not in repr(captured.value)


def test_silent_source_never_constructs_the_audible_lower_funnel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    registry = MaterialPathRegistry(state_directory=state)
    tools = PackagedMediaTools(
        ffprobe_path=_executable(tmp_path / "ffprobe"),
        ffmpeg_path=_executable(tmp_path / "ffmpeg"),
    )
    material = _material(user_description=True, has_audio=False)
    source = tmp_path / "silent.mp4"
    source.write_bytes(b"video")
    registry.register(material.material_id.uuid, source)
    monkeypatch.setattr(
        "automation_tool.executor.smart_edit_pipeline.probe_material",
        lambda *_args: MaterialFacts(
            ProbedMaterialKind.VIDEO,
            4_000,
            720,
            1_280,
            "h264",
            None,
            False,
            None,
            "a" * 64,
        ),
    )
    monkeypatch.setattr(
        "automation_tool.executor.smart_edit_pipeline.verify_decodable_video",
        lambda *_args, **_kwargs: VerifiedDecodableMaterial(
            material_id=material.material_id.uuid,
            content_digest=material.content_digest,
            intervals=(VerifiedDecodableInterval(0, 4_000),),
        ),
    )
    pipeline = _pipeline(tmp_path, registry, tools)
    calls = 0

    def forbidden_factory(_source: Path, _approved: os.stat_result) -> object:
        nonlocal calls
        calls += 1
        return object()

    pipeline.audible_speech_analyzer_factory = forbidden_factory

    result = pipeline.prepare((material,), cancellation_requested=lambda: False)

    assert result.materials == (material,)
    assert calls == 0


def test_image_understanding_drops_synthetic_analysis_duration_and_shots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    registry = MaterialPathRegistry(state_directory=state)
    tools = PackagedMediaTools(
        ffprobe_path=_executable(tmp_path / "ffprobe"),
        ffmpeg_path=_executable(tmp_path / "ffmpeg"),
    )
    material = _image_material()
    source = tmp_path / "product.jpg"
    source.write_bytes(b"image")
    registry.register(material.material_id.uuid, source)
    monkeypatch.setattr(
        "automation_tool.executor.smart_edit_pipeline.probe_material",
        lambda *_args: MaterialFacts(
            ProbedMaterialKind.IMAGE,
            None,
            720,
            1_280,
            "mjpeg",
            None,
            False,
            None,
            "b" * 64,
        ),
    )
    monkeypatch.setattr(
        "automation_tool.executor.smart_edit_pipeline.verify_decodable_video",
        lambda *_args, **_kwargs: pytest.fail("an image must not enter video decoding"),
    )
    monkeypatch.setattr(
        "automation_tool.executor.smart_edit_pipeline.extract_adaptive_frames",
        lambda *_args, **_kwargs: (AdaptiveFrameArtifact("frame-000001.jpg", 0, True, 4),),
    )
    monkeypatch.setattr(
        "automation_tool.executor.smart_edit_pipeline.understand_material_artifacts",
        lambda *_args, **_kwargs: MaterialUnderstandingResult(
            request_id="image-understanding-request",
            description="产品白底主图",
            tags=("产品",),
            shots=(MaterialUnderstandingShot(0, 1, "产品主图"),),
        ),
    )

    prepared = _pipeline(tmp_path, registry, tools).prepare(
        (material,),
        cancellation_requested=lambda: False,
    )

    assert prepared.decodable_materials == ()
    assert prepared.materials[0].duration_ms is None
    assert prepared.materials[0].shot_boundaries_ms == ()
    assert prepared.materials[0].ai_description == "产品白底主图"
    assert prepared.analysis_updates[0].material_id == material.material_id.uuid
