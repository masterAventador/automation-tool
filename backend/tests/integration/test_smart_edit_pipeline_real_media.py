"""LE-19 T1 acceptance: real packaged decoding enters the smart-edit pipeline."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from automation_tool.control_plane.domain import (
    DescriptionSource,
    Material,
    MaterialId,
    MaterialKind,
)
from automation_tool.executor.material_probe import (
    MaterialPathRegistry,
    PackagedMediaTools,
    probe_material,
)
from automation_tool.executor.material_understanding import MaterialUnderstandingAdapter
from automation_tool.executor.motion_authoring.agent import AuthoringWorkspace
from automation_tool.executor.motion_authoring.voiceover import VoiceoverConfig
from automation_tool.executor.script_segmentation import ScriptSegmentationAdapter
from automation_tool.executor.semantic_matching import SemanticMatchingAdapter
from automation_tool.executor.smart_edit_pipeline import LocalSmartEditGenerationPipeline

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
TOOLCHAIN_BIN = REPOSITORY_ROOT / "frontend/src-tauri/target/debug/media-toolchain/bin"


def _tools() -> PackagedMediaTools:
    suffix = ".exe" if os.name == "nt" else ""
    return PackagedMediaTools(
        ffprobe_path=TOOLCHAIN_BIN / f"ffprobe{suffix}",
        ffmpeg_path=TOOLCHAIN_BIN / f"ffmpeg{suffix}",
    )


@pytest.mark.skipif(
    not (TOOLCHAIN_BIN / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")).is_file(),
    reason="packaged media toolchain has not been built",
)
def test_real_h264_picture_is_fully_decoded_before_prepare_succeeds(tmp_path: Path) -> None:
    tools = _tools()
    source = tmp_path / "真实产品素材.mp4"
    subprocess.run(
        [
            os.fspath(tools.ffmpeg_path),
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=17",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            os.fspath(source),
        ],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    facts = probe_material(tools, source)
    material = Material.register(
        material_id=MaterialId.new(),
        kind=MaterialKind(facts.kind.value),
        duration_ms=facts.duration_ms,
        width=facts.width,
        height=facts.height,
        content_digest=facts.content_digest,
        has_audio=facts.has_audio,
        audio_loudness_lufs=facts.audio_loudness_lufs,
        has_speech=False,
        speech_segments_ms=(),
        speech_transcript=None,
        shot_boundaries_ms=(),
        ai_description="人工确认的彩色测试画面",
        ai_tags=(),
        description_source=DescriptionSource.USER,
        described_at=None,
    )
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    registry = MaterialPathRegistry(state_directory=state)
    registry.register(material.material_id.uuid, source)
    generation = tmp_path / "generation"
    generation.mkdir(mode=0o700)
    pipeline = LocalSmartEditGenerationPipeline(
        tools=tools,
        registry=registry,
        workspace=AuthoringWorkspace(generation),
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

    prepared = pipeline.prepare((material,), cancellation_requested=lambda: False)

    assert prepared.materials == (material,)
    assert prepared.analysis_updates == ()
    assert len(prepared.decodable_materials) == 1
    assert prepared.decodable_materials[0].content_digest == facts.content_digest
    assert tuple(
        (interval.start_ms, interval.end_ms)
        for interval in prepared.decodable_materials[0].intervals
    ) == ((0, facts.duration_ms),)
