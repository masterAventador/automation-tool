"""LE-15 T4: real Bailian segmentation → TTS → packaged ffprobe."""

from __future__ import annotations

import os
import sys
import unicodedata
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, os.fspath(REPOSITORY_ROOT / "scripts"))
from run_le_15_acceptance import (  # noqa: E402
    SECRET_PATH_ENVIRONMENT,
    TOOLCHAIN_ROOT_ENVIRONMENT,
    read_bailian_api_key,
)

from automation_tool.executor.material_probe import (  # noqa: E402
    MediaStreamFacts,
    PackagedMediaTools,
    ProbedMaterialKind,
    read_stream_facts,
)
from automation_tool.executor.motion_authoring.agent import (  # noqa: E402
    AuthoringWorkspace,
)
from automation_tool.executor.motion_authoring.voiceover import (  # noqa: E402
    voiceover_config_from_catalog,
)
from automation_tool.executor.script_segmentation import (  # noqa: E402
    BailianScriptSegmentationAdapter,
    ScriptSegmentationOptions,
    load_bailian_script_segmentation_config,
    segment_script,
)
from automation_tool.executor.script_voiceover import (  # noqa: E402
    synthesize_script_voiceovers,
)

CATALOG_PATH = REPOSITORY_ROOT / "contracts/video/bailian-model-catalog.v1.json"
FIXED_SCRIPT_INPUT = "智能剪辑让视频创作更高效。"
pytestmark = pytest.mark.skipif(
    any(name not in os.environ for name in (SECRET_PATH_ENVIRONMENT, TOOLCHAIN_ROOT_ENVIRONMENT)),
    reason="run through scripts/run_le_15_acceptance.py",
)


def _packaged_tools() -> PackagedMediaTools:
    suffix = ".exe" if os.name == "nt" else ""
    root = Path(os.environ[TOOLCHAIN_ROOT_ENVIRONMENT]) / "bin"
    return PackagedMediaTools(
        ffprobe_path=root / f"ffprobe{suffix}",
        ffmpeg_path=root / f"ffmpeg{suffix}",
    )


def test_real_script_and_tts_audio_round_trip_through_packaged_ffprobe(
    tmp_path: Path,
) -> None:
    api_key = read_bailian_api_key(Path(os.environ[SECRET_PATH_ENVIRONMENT]))
    tools = _packaged_tools()
    segmentation_config = load_bailian_script_segmentation_config(
        catalog_path=CATALOG_PATH,
        api_key=api_key,
        model_id=None,
        timeout_seconds=120,
    )
    script = segment_script(
        BailianScriptSegmentationAdapter(segmentation_config),
        FIXED_SCRIPT_INPUT,
        options=ScriptSegmentationOptions(enable_thinking=False),
    )
    assert 1 <= len(script.sentences) <= 4
    assert script.sentences[0].sequence == 1
    assert script.request_id
    assert not any(
        unicodedata.category(character).startswith("C") for character in script.request_id
    )

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(mode=0o700)
    voiceover_config = voiceover_config_from_catalog(
        catalog_path=CATALOG_PATH,
        api_key=api_key,
    )
    voiceovers = synthesize_script_voiceovers(
        script,
        config=voiceover_config,
        workspace=AuthoringWorkspace(workspace_root),
        tools=tools,
    )

    assert len(voiceovers.clips) == len(script.sentences)
    for sequence, clip in enumerate(voiceovers.clips, start=1):
        assert clip.relative_path == f"voiceover/sentence-{sequence:04d}.wav"
        assert clip.bytes_written > 0
        assert clip.duration_ms > 0
        audio_path = workspace_root / clip.relative_path
        assert audio_path.is_file()
        assert not audio_path.is_symlink()
        assert audio_path.stat().st_size == clip.bytes_written
        facts = read_stream_facts(tools, audio_path)
        assert isinstance(facts, MediaStreamFacts)
        assert facts.kind is ProbedMaterialKind.AUDIO
        assert facts.duration_ms == clip.duration_ms

    print(f"LE-15 real script model: {segmentation_config.model_id}")
    print(f"LE-15 real script request id: {script.request_id}")
    print(f"LE-15 real TTS model: {voiceover_config.model_id}")
    print(
        "LE-15 measured voiceover: "
        f"sentences={len(voiceovers.clips)}, "
        f"bytes={tuple(clip.bytes_written for clip in voiceovers.clips)}, "
        f"duration_ms={tuple(clip.duration_ms for clip in voiceovers.clips)}"
    )
