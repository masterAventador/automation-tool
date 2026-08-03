"""LE-19 T1: the concrete local smart-edit production pipeline."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from collections.abc import Callable
from dataclasses import replace
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
from automation_tool.executor.adaptive_frame_extraction import (
    AdaptiveFrameArtifact,
    AdaptiveFrameRejection,
)
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
    MaterialUnderstandingOptions,
    MaterialUnderstandingResult,
    MaterialUnderstandingShot,
)
from automation_tool.executor.motion_authoring.agent import AuthoringWorkspace
from automation_tool.executor.motion_authoring.voiceover import VoiceoverConfig
from automation_tool.executor.script_segmentation import (
    ScriptSegmentationAdapter,
    ScriptSegmentationOptions,
    ScriptSegmentationResult,
    ScriptSentence,
)
from automation_tool.executor.script_voiceover import (
    ScriptVoiceoverCancelled,
    ScriptVoiceoverClip,
    ScriptVoiceoverResult,
)
from automation_tool.executor.segment_selection import (
    VerifiedDecodableInterval,
    VerifiedDecodableMaterial,
)
from automation_tool.executor.semantic_matching import (
    SemanticCandidateScore,
    SemanticMatchingAdapter,
    SemanticMatchingOptions,
    SemanticMatchingResult,
    SemanticSentenceMatches,
)
from automation_tool.executor.smart_edit_generation import (
    SmartEditGenerationCancelled,
    SmartEditGenerationRejected,
)
from automation_tool.executor.smart_edit_media import (
    SmartEditMediaFailureCode,
    SmartEditMediaRejected,
)
from automation_tool.executor.smart_edit_pipeline import (
    LocalSmartEditGenerationPipeline,
    _LocalMaterial,
    _UnderstandingInput,
)


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
        options = _kwargs["options"]
        assert isinstance(options, MaterialUnderstandingOptions)
        assert options.enable_thinking is True
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
        enable_thinking=True,
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
            enable_thinking=False,
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
            enable_thinking=False,
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

    result = pipeline.prepare(
        (material,),
        enable_thinking=False,
        cancellation_requested=lambda: False,
    )

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
        enable_thinking=False,
        cancellation_requested=lambda: False,
    )

    assert prepared.decodable_materials == ()
    assert prepared.materials[0].duration_ms is None
    assert prepared.materials[0].shot_boundaries_ms == ()
    assert prepared.materials[0].ai_description == "产品白底主图"
    assert prepared.analysis_updates[0].material_id == material.material_id.uuid


_MODULE = "automation_tool.executor.smart_edit_pipeline"


def _video_facts() -> MaterialFacts:
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


class _Harness:
    """One registered material and a pipeline whose every model call is replaced.

    Each test then swaps back exactly the one step it is about, so a refusal can
    only have come from that step rather than from whatever else was left real.
    """

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        state = tmp_path / "state"
        state.mkdir(mode=0o700)
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.registry = MaterialPathRegistry(state_directory=state)
        self.tools = PackagedMediaTools(
            ffprobe_path=_executable(tmp_path / "ffprobe"),
            ffmpeg_path=_executable(tmp_path / "ffmpeg"),
        )
        self.pipeline = _pipeline(tmp_path, self.registry, self.tools)
        self.patch("probe_material", lambda *_args: _video_facts())
        self.patch(
            "verify_decodable_video",
            lambda *_args, **options: VerifiedDecodableMaterial(
                material_id=options["material_id"],
                content_digest=options["content_digest"],
                intervals=(VerifiedDecodableInterval(0, options["duration_ms"]),),
            ),
        )
        self.patch(
            "extract_adaptive_frames",
            lambda *_args, **_options: (AdaptiveFrameArtifact("frame-000001.jpg", 0, True, 4),),
        )
        self.patch(
            "understand_material_artifacts",
            lambda *_args, **_options: MaterialUnderstandingResult(
                request_id="understanding-request",
                description="AI 产品发布会特写",
                tags=("产品",),
                shots=(MaterialUnderstandingShot(0, 4_000, "产品特写"),),
            ),
        )
        self.patch(
            "analyze_material_speech",
            lambda *_args, **_options: MaterialSpeechAnalysis(False, (), None),
        )

    def patch(self, name: str, value: object) -> None:
        self.monkeypatch.setattr(f"{_MODULE}.{name}", value)

    def register(self, material: Material) -> Path:
        source = self.tmp_path / f"{material.material_id}.mp4"
        source.write_bytes(b"video")
        self.registry.register(material.material_id.uuid, source)
        return source

    def prepare(self, *materials: Material, **overrides: object) -> object:
        arguments: dict[str, object] = {
            "enable_thinking": False,
            "cancellation_requested": lambda: False,
        }
        arguments.update(overrides)
        return self.pipeline.prepare(materials, **arguments)  # type: ignore[arg-type]


def test_the_pipeline_refuses_collaborators_it_cannot_use(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    registry = MaterialPathRegistry(state_directory=state)
    tools = PackagedMediaTools(
        ffprobe_path=_executable(tmp_path / "ffprobe"),
        ffmpeg_path=_executable(tmp_path / "ffmpeg"),
    )
    complete = _pipeline(tmp_path, registry, tools)

    assert repr(complete) == "LocalSmartEditGenerationPipeline(<redacted>)"

    for label, field_name in [
        ("tools", "tools"),
        ("a registry", "registry"),
        ("a workspace", "workspace"),
        ("a voiceover config", "voiceover_config"),
        ("a speech analyzer factory", "audible_speech_analyzer_factory"),
        ("a clock", "now"),
        ("an identifier source", "material_id_factory"),
    ]:
        arguments = {
            name: getattr(complete, name)
            for name in (
                "tools",
                "registry",
                "workspace",
                "understanding_adapter",
                "audible_speech_analyzer_factory",
                "script_adapter",
                "semantic_adapter",
                "voiceover_config",
                "now",
                "material_id_factory",
            )
        }
        arguments[field_name] = object()
        with pytest.raises(SmartEditGenerationRejected):
            LocalSmartEditGenerationPipeline(**arguments)
        assert label


def test_preparation_refuses_a_request_it_cannot_trust(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _Harness(tmp_path, monkeypatch)
    material = _material()
    harness.register(material)

    cases: list[tuple[str, tuple[object, ...], dict[str, object]]] = [
        ("materials that are not a tuple", ([material],), {}),
        ("no materials at all", ((),), {}),
        ("something that is not a material", ((object(),),), {}),
        ("a thinking flag that is not a bool", ((material,),), {"enable_thinking": 1}),
        ("a probe that cannot be called", ((material,),), {"cancellation_requested": None}),
    ]
    for label, positional, overrides in cases:
        arguments: dict[str, object] = {
            "enable_thinking": False,
            "cancellation_requested": lambda: False,
        }
        arguments.update(overrides)
        with pytest.raises(SmartEditGenerationRejected):
            harness.pipeline.prepare(*positional, **arguments)  # type: ignore[arg-type]
        assert label


def test_one_pipeline_prepares_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The instance owns scratch state for one job; a second job needs its own."""
    harness = _Harness(tmp_path, monkeypatch)
    material = _material()
    harness.register(material)
    harness.prepare(material)

    with pytest.raises(SmartEditGenerationRejected):
        harness.prepare(material)


def test_a_cancellation_probe_that_cannot_be_trusted_stops_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _Harness(tmp_path, monkeypatch)
    material = _material()
    harness.register(material)

    with pytest.raises(SmartEditGenerationRejected):
        harness.prepare(material, cancellation_requested=lambda: cast(bool, 1))


def test_a_material_that_no_longer_revalidates_is_refused_before_any_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _Harness(tmp_path, monkeypatch)
    material = _material()
    harness.register(material)
    broken = Material.__new__(Material)
    for name in (
        "material_id",
        "kind",
        "duration_ms",
        "width",
        "height",
        "content_digest",
        "has_audio",
        "audio_loudness_lufs",
        "has_speech",
        "speech_segments_ms",
        "speech_transcript",
        "shot_boundaries_ms",
        "ai_description",
        "ai_tags",
        "description_source",
        "described_at",
    ):
        object.__setattr__(broken, name, getattr(material, name))
    object.__setattr__(broken, "duration_ms", 0)

    with pytest.raises(SmartEditGenerationRejected):
        harness.prepare(broken)


def test_an_audio_material_is_refused_before_it_is_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The picture lane is what this pipeline edits; sound arrives as narration."""
    harness = _Harness(tmp_path, monkeypatch)
    audio = Material.register(
        material_id=MaterialId.new(),
        kind=MaterialKind.AUDIO,
        duration_ms=4_000,
        width=None,
        height=None,
        content_digest="c" * 64,
        has_audio=True,
        audio_loudness_lufs=-18.0,
        has_speech=False,
        speech_segments_ms=(),
        speech_transcript=None,
        shot_boundaries_ms=(),
        ai_description=None,
        ai_tags=(),
        description_source=DescriptionSource.AI,
        described_at=None,
    )

    with pytest.raises(SmartEditGenerationRejected):
        harness.prepare(audio)


def test_a_local_file_that_disagrees_with_the_registered_facts_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the Control Plane recorded and what is on this disk must be one file."""
    harness = _Harness(tmp_path, monkeypatch)
    material = _material()
    harness.register(material)
    harness.patch(
        "probe_material",
        lambda *_args: replace(_video_facts(), duration_ms=9_000),
    )

    with pytest.raises(SmartEditGenerationRejected):
        harness.prepare(material)


def test_a_decode_refusal_is_told_apart_from_a_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _Harness(tmp_path, monkeypatch)
    material = _material()
    harness.register(material)

    def refuse(*_args: object, **_options: object) -> VerifiedDecodableMaterial:
        raise SmartEditMediaRejected(SmartEditMediaFailureCode.UNDECODABLE)

    harness.patch("verify_decodable_video", refuse)
    with pytest.raises(SmartEditGenerationRejected):
        harness.prepare(material)

    def cancel(*_args: object, **_options: object) -> VerifiedDecodableMaterial:
        raise SmartEditMediaRejected(SmartEditMediaFailureCode.CANCELLED)

    second_root = tmp_path / "second"
    second_root.mkdir()
    harness = _Harness(second_root, monkeypatch)
    harness.register(material)
    harness.patch("verify_decodable_video", cancel)
    with pytest.raises(SmartEditGenerationCancelled):
        harness.prepare(material)


def test_frames_that_cannot_be_extracted_stop_the_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _Harness(tmp_path, monkeypatch)
    material = _material()
    harness.register(material)
    harness.patch(
        "extract_adaptive_frames",
        lambda *_args, **_options: AdaptiveFrameRejection.WORKSPACE_UNUSABLE,
    )

    with pytest.raises(SmartEditGenerationRejected):
        harness.prepare(material)


def test_scratch_frame_directories_are_removed_however_preparation_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Frames are copies of the operator's file; none may outlive the attempt."""
    created: list[Path] = []
    real_mkdtemp = tempfile.mkdtemp

    def recording_mkdtemp(*, prefix: str | None = None, **_options: object) -> str:
        raw = real_mkdtemp(prefix=prefix)
        created.append(Path(raw))
        return raw

    monkeypatch.setattr(tempfile, "mkdtemp", recording_mkdtemp)

    harness = _Harness(tmp_path, monkeypatch)
    first = _material()
    second = _material()
    harness.register(first)
    harness.register(second)
    calls = 0

    def extract_then_fail(*_args: object, **_options: object) -> object:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("extractor defect")
        return (AdaptiveFrameArtifact("frame-000001.jpg", 0, True, 4),)

    harness.patch("extract_adaptive_frames", extract_then_fail)

    with pytest.raises(SmartEditGenerationRejected):
        harness.prepare(first, second)

    assert created, "the first material should have opened a scratch directory"
    assert [directory.exists() for directory in created] == [False] * len(created)


def test_a_cancellation_during_extraction_also_removes_the_scratch_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[Path] = []
    real_mkdtemp = tempfile.mkdtemp

    def recording_mkdtemp(*, prefix: str | None = None, **_options: object) -> str:
        raw = real_mkdtemp(prefix=prefix)
        created.append(Path(raw))
        return raw

    monkeypatch.setattr(tempfile, "mkdtemp", recording_mkdtemp)

    harness = _Harness(tmp_path, monkeypatch)
    first = _material()
    second = _material()
    harness.register(first)
    harness.register(second)
    polls = 0

    def probe() -> bool:
        nonlocal polls
        polls += 1
        return polls > 3

    with pytest.raises(SmartEditGenerationCancelled):
        harness.prepare(first, second, cancellation_requested=probe)

    assert created
    assert [directory.exists() for directory in created] == [False] * len(created)


def test_understanding_input_and_need_must_agree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Frames extracted for a material nobody needed described are a defect, not spare work."""
    harness = _Harness(tmp_path, monkeypatch)
    material = _material()
    harness.register(material)
    prepared_input = _UnderstandingInput(tmp_path, (), 4_000)
    local = _LocalMaterial(
        material, tmp_path / "source.mp4", tmp_path.lstat(), _video_facts(), None
    )
    described = _material(user_description=True)
    described_local = _LocalMaterial(
        described, tmp_path / "source.mp4", tmp_path.lstat(), _video_facts(), None
    )

    with pytest.raises(SmartEditGenerationRejected):
        harness.pipeline._understand_if_needed(
            described_local,
            prepared_input,
            enable_thinking=False,
            cancellation_requested=lambda: False,
        )

    with pytest.raises(SmartEditGenerationRejected):
        harness.pipeline._understand_if_needed(
            local,
            None,
            enable_thinking=False,
            cancellation_requested=lambda: False,
        )


def test_a_clock_that_answers_with_a_useless_moment_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`described_at` is persisted and compared; a naive or absent one cannot be."""
    material = _material()

    for label, clock in [
        ("a clock that is not a clock", lambda: cast(datetime, "2026-08-01")),
        ("a moment with no zone", lambda: datetime(2026, 8, 1)),
    ]:
        root = tmp_path / label.replace(" ", "-")
        root.mkdir()
        harness = _Harness(root, monkeypatch)
        harness.register(material)
        harness.pipeline.now = clock

        with pytest.raises(SmartEditGenerationRejected):
            harness.prepare(material)


def test_an_understanding_adapter_that_fails_stops_the_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _Harness(tmp_path, monkeypatch)
    material = _material()
    harness.register(material)

    def refuse(*_args: object, **_options: object) -> object:
        raise RuntimeError("adapter defect")

    harness.patch("understand_material_artifacts", refuse)

    with pytest.raises(SmartEditGenerationRejected):
        harness.prepare(material)


def test_speech_analysis_reads_the_proven_local_file_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lower funnel is only ever built against the file this run proved."""
    harness = _Harness(tmp_path, monkeypatch)
    material = _material()
    source = harness.register(material)
    seen: list[tuple[Path, int]] = []
    harness.pipeline.audible_speech_analyzer_factory = lambda path, approved: seen.append(
        (path, approved.st_ino)
    )

    def analyze(_facts: object, *, audible_analyzer_factory: Callable[[], object]) -> object:
        audible_analyzer_factory()
        raise RuntimeError("analyzer defect")

    harness.patch("analyze_material_speech", analyze)

    with pytest.raises(SmartEditGenerationRejected):
        harness.prepare(material)

    assert seen == [(source.resolve(), source.stat().st_ino)]


def _script() -> ScriptSegmentationResult:
    return ScriptSegmentationResult(
        request_id="script-request",
        sentences=(ScriptSentence(1, "一句旁白。"),),
    )


def _voiceovers(bytes_written: int, duration_ms: int = 880) -> ScriptVoiceoverResult:
    script = _script()
    return ScriptVoiceoverResult(
        script_request_id=script.request_id,
        clips=(
            ScriptVoiceoverClip(
                sentence=script.sentences[0],
                relative_path="voiceover/sentence-0001.wav",
                duration_ms=duration_ms,
                bytes_written=bytes_written,
            ),
        ),
    )


def test_segmentation_passes_the_thinking_choice_through_to_the_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _Harness(tmp_path, monkeypatch)
    seen: dict[str, object] = {}
    script = _script()

    def segment(adapter: object, prompt: str, *, options: object) -> ScriptSegmentationResult:
        seen.update({"adapter": adapter, "prompt": prompt, "options": options})
        return script

    harness.patch("segment_script", segment)

    assert harness.pipeline.segment("剪一条片子。", enable_thinking=True) is script
    assert seen["adapter"] is harness.pipeline.script_adapter
    assert seen["prompt"] == "剪一条片子。"
    assert seen["options"] == ScriptSegmentationOptions(enable_thinking=True)


def test_synthesis_checks_for_cancellation_on_both_sides_of_the_billable_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _Harness(tmp_path, monkeypatch)
    result = _voiceovers(17)
    harness.patch("synthesize_script_voiceovers", lambda *_args, **_options: result)

    assert harness.pipeline.synthesize(_script(), cancellation_requested=lambda: False) is result

    polls = 0

    def after() -> bool:
        nonlocal polls
        polls += 1
        return polls > 1

    with pytest.raises(SmartEditGenerationCancelled):
        harness.pipeline.synthesize(_script(), cancellation_requested=after)
    assert polls == 2


def test_a_cancelled_synthesis_is_reported_as_a_generation_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One vocabulary reaches the caller; the voiceover module's is not it."""
    harness = _Harness(tmp_path, monkeypatch)

    def cancel(*_args: object, **_options: object) -> ScriptVoiceoverResult:
        raise ScriptVoiceoverCancelled

    harness.patch("synthesize_script_voiceovers", cancel)

    with pytest.raises(SmartEditGenerationCancelled):
        harness.pipeline.synthesize(_script(), cancellation_requested=lambda: False)


def test_matching_checks_for_cancellation_on_both_sides_of_the_billable_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _Harness(tmp_path, monkeypatch)
    material = _material()
    matches = SemanticMatchingResult(
        request_ids=("match-request",),
        sentences=(
            SemanticSentenceMatches(
                sequence=1,
                candidates=(
                    SemanticCandidateScore(
                        material_id=material.material_id,
                        score=91,
                        qualified=True,
                    ),
                ),
            ),
        ),
    )
    seen: dict[str, object] = {}

    def match(adapter: object, script: object, materials: object, *, options: object) -> object:
        seen.update({"adapter": adapter, "materials": materials, "options": options})
        return matches

    harness.patch("match_script_materials", match)

    assert (
        harness.pipeline.match(
            _script(),
            (material,),
            enable_thinking=True,
            cancellation_requested=lambda: False,
        )
        is matches
    )
    assert seen["adapter"] is harness.pipeline.semantic_adapter
    assert seen["materials"] == (material,)
    assert seen["options"] == SemanticMatchingOptions(enable_thinking=True)

    polls = 0

    def after() -> bool:
        nonlocal polls
        polls += 1
        return polls > 1

    with pytest.raises(SmartEditGenerationCancelled):
        harness.pipeline.match(
            _script(),
            (material,),
            enable_thinking=True,
            cancellation_requested=after,
        )
    assert polls == 2


def test_narration_binding_refuses_voiceovers_of_the_wrong_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _Harness(tmp_path, monkeypatch)

    with pytest.raises(SmartEditGenerationRejected):
        harness.pipeline.bind_narration(
            cast(ScriptVoiceoverResult, object()),
            cancellation_requested=lambda: False,
        )


def test_narration_binding_refuses_an_output_that_is_not_the_file_it_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The synthesiser reported a size; anything else on disk is a different file."""
    harness = _Harness(tmp_path, monkeypatch)
    payload = b"RIFF-private-wave"
    output = harness.pipeline.workspace.write_bytes("voiceover/sentence-0001.wav", payload)

    with pytest.raises(SmartEditGenerationRejected):
        harness.pipeline.bind_narration(
            _voiceovers(len(payload) + 1),
            cancellation_requested=lambda: False,
        )

    elsewhere = tmp_path / "elsewhere.wav"
    elsewhere.write_bytes(payload)
    output.unlink()
    output.symlink_to(elsewhere)

    with pytest.raises(SmartEditGenerationRejected):
        harness.pipeline.bind_narration(
            _voiceovers(len(payload)),
            cancellation_requested=lambda: False,
        )


def test_narration_binding_fails_closed_when_the_output_cannot_be_measured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _Harness(tmp_path, monkeypatch)
    payload = b"RIFF-private-wave"
    harness.pipeline.workspace.write_bytes("voiceover/sentence-0001.wav", payload)

    def refuse(*_args: object, **_options: object) -> MediaStreamFacts:
        raise RuntimeError("probe defect")

    harness.patch("read_stream_facts", refuse)

    with pytest.raises(SmartEditGenerationRejected):
        harness.pipeline.bind_narration(
            _voiceovers(len(payload)),
            cancellation_requested=lambda: False,
        )


def test_narration_binding_refuses_output_that_is_not_the_audio_it_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _Harness(tmp_path, monkeypatch)
    payload = b"RIFF-private-wave"
    harness.pipeline.workspace.write_bytes("voiceover/sentence-0001.wav", payload)

    def facts(kind: ProbedMaterialKind, duration_ms: int | None) -> MediaStreamFacts:
        return MediaStreamFacts(
            kind=kind,
            duration_ms=duration_ms,
            width=None,
            height=None,
            video_codec=None,
            audio_codec="pcm_s16le",
        )

    cases: list[tuple[str, object, object]] = [
        ("a picture where audio was expected", facts(ProbedMaterialKind.VIDEO, 880), uuid4),
        ("audio of a different length", facts(ProbedMaterialKind.AUDIO, 900), uuid4),
        (
            "an identifier that is not one",
            facts(ProbedMaterialKind.AUDIO, 880),
            lambda: cast(UUID, "not-a-uuid"),
        ),
    ]
    for label, stream_facts, identifier in cases:
        harness.patch("read_stream_facts", lambda *_args, _facts=stream_facts: _facts)
        harness.pipeline.material_id_factory = cast(Callable[[], UUID], identifier)

        with pytest.raises(SmartEditGenerationRejected):
            harness.pipeline.bind_narration(
                _voiceovers(len(payload)),
                cancellation_requested=lambda: False,
            )
        assert label
