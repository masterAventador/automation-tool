"""LE-14 T5: real human speech → Silero → Bailian → PostgreSQL."""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, insert, select

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, os.fspath(REPOSITORY_ROOT / "scripts"))
from run_le_13_acceptance import read_bailian_api_key  # type: ignore[import-not-found]  # noqa: E402,I001
from run_le_14_acceptance import (  # type: ignore[import-not-found]  # noqa: E402
    SECRET_PATH_ENVIRONMENT,
    TOOLCHAIN_ROOT_ENVIRONMENT,
    VOICE_PATH_ENVIRONMENT,
)

from automation_tool.control_plane.application.materials import MaterialService  # noqa: E402
from automation_tool.control_plane.domain import (  # noqa: E402
    DescriptionSource,
    InstallationId,
    Material,
    MaterialId,
    MaterialKind,
)
from automation_tool.control_plane.infrastructure.database import (  # noqa: E402
    Database,
    installations,
    materials,
)
from automation_tool.control_plane.infrastructure.database.material_repository import (  # noqa: E402
    SqlAlchemyMaterialRepository,
)
from automation_tool.executor.material_probe import (  # noqa: E402
    MaterialFacts,
    PackagedMediaTools,
    approve_source,
    probe_material,
)
from automation_tool.executor.material_speech_analysis import (  # noqa: E402
    MaterialSpeechAnalysis,
    analyze_material_speech,
)
from automation_tool.executor.material_speech_pipeline import (  # noqa: E402
    LocalAudibleSpeechAnalyzerFactory,
    SpeechAudioBatch,
    SpeechTranscriptionAdapter,
)
from automation_tool.executor.material_speech_transcription import (  # noqa: E402
    BAILIAN_ASR_MODEL_ID,
    BailianSpeechTranscriptionAdapter,
    load_bailian_speech_transcription_config,
)
from automation_tool.executor.silero_vad import create_silero_vad  # noqa: E402

CATALOG_PATH = REPOSITORY_ROOT / "contracts/video/bailian-model-catalog.v1.json"
AMBIENT_AUDIO = REPOSITORY_ROOT / "assets/motion-catalog-overlay/audio/ambient-tech.wav"
_REQUIRED_ENVIRONMENT = (
    SECRET_PATH_ENVIRONMENT,
    TOOLCHAIN_ROOT_ENVIRONMENT,
    VOICE_PATH_ENVIRONMENT,
)
pytestmark = pytest.mark.skipif(
    any(name not in os.environ for name in _REQUIRED_ENVIRONMENT),
    reason="run through scripts/run_le_14_acceptance.py",
)


def _packaged_tools() -> PackagedMediaTools:
    suffix = ".exe" if os.name == "nt" else ""
    root = Path(os.environ[TOOLCHAIN_ROOT_ENVIRONMENT]) / "bin"
    return PackagedMediaTools(
        ffprobe_path=root / f"ffprobe{suffix}",
        ffmpeg_path=root / f"ffmpeg{suffix}",
    )


def _mux_audio_material(
    tools: PackagedMediaTools,
    *,
    audio: Path,
    target: Path,
) -> None:
    subprocess.run(
        [
            os.fspath(tools.ffmpeg_path),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=navy:s=320x180:r=17:d=7.173",
            "-i",
            os.fspath(audio),
            "-filter_complex",
            "[1:a:0]apad=pad_dur=1[audio]",
            "-map",
            "0:v:0",
            "-map",
            "[audio]",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "flac",
            "-t",
            "6.173",
            os.fspath(target),
        ],
        check=True,
        capture_output=True,
    )


class _AsrMustNotBeCalled:
    calls = 0

    def transcribe(self, audio: SpeechAudioBatch) -> str:
        del audio
        self.calls += 1
        raise AssertionError("pure music crossed the ASR boundary")


def _analyse(
    tools: PackagedMediaTools,
    source: Path,
    *,
    asr: SpeechTranscriptionAdapter,
) -> tuple[MaterialSpeechAnalysis, MaterialFacts]:
    facts = probe_material(tools, source)
    assert facts.has_audio is True
    assert facts.duration_ms is not None
    assert facts.duration_ms % 100 != 0
    approved_source, approved = approve_source(source)
    result = analyze_material_speech(
        facts,
        audible_analyzer_factory=LocalAudibleSpeechAnalyzerFactory(
            tools=tools,
            source=approved_source,
            approved=approved,
            vad_factory=create_silero_vad,
            asr_adapter=asr,
        ),
    )
    return result, facts


@pytest.mark.asyncio
async def test_real_speech_is_transcribed_and_atomically_written_to_postgresql(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
    tmp_path: Path,
) -> None:
    tools = _packaged_tools()
    fixture = Path(os.environ[VOICE_PATH_ENVIRONMENT])

    music_source = tmp_path / "ambient-material.mkv"
    _mux_audio_material(tools, audio=AMBIENT_AUDIO, target=music_source)
    no_asr = _AsrMustNotBeCalled()
    music_result, _music_facts = _analyse(tools, music_source, asr=no_asr)
    assert music_result == MaterialSpeechAnalysis(False, (), None)
    assert no_asr.calls == 0

    api_key = read_bailian_api_key(Path(os.environ[SECRET_PATH_ENVIRONMENT]))
    asr = BailianSpeechTranscriptionAdapter(
        load_bailian_speech_transcription_config(
            catalog_path=CATALOG_PATH,
            api_key=api_key,
            timeout_seconds=90,
        )
    )
    speech_source = tmp_path / "human-speech-material.mkv"
    _mux_audio_material(tools, audio=fixture, target=speech_source)
    result, facts = _analyse(tools, speech_source, asr=asr)

    assert result.has_speech is True
    assert result.speech_segments_ms
    assert result.speech_transcript
    assert facts.duration_ms is not None
    assert all(
        0 <= start_ms < end_ms <= facts.duration_ms
        for start_ms, end_ms in result.speech_segments_ms
    )
    words = set(re.sub(r"[^A-Z0-9]+", " ", result.speech_transcript.upper()).split())
    assert {"QUILTER", "APOSTLE"} <= words
    assert {"MISTER", "MR"} & words

    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    installation_id = InstallationId.new()
    material_id = MaterialId.new()
    repository = SqlAlchemyMaterialRepository(database)
    service = MaterialService(repository=repository)
    material = Material.register(
        material_id=material_id,
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
        ai_description=None,
        ai_tags=(),
        description_source=DescriptionSource.AI,
        described_at=None,
    )
    try:
        async with database.session() as session:
            await session.execute(
                insert(installations).values(
                    id=installation_id.uuid,
                    device_public_key=secrets.token_bytes(32),
                )
            )
        await repository.save(material, installation_id)
        stored = await service.update_speech_analysis(
            installation_id=installation_id,
            material_id=str(material_id),
            has_speech=result.has_speech,
            speech_segments_ms=result.speech_segments_ms,
            speech_transcript=result.speech_transcript,
        )

        assert stored.has_speech is True
        assert stored.speech_segments_ms == result.speech_segments_ms
        assert stored.speech_transcript == result.speech_transcript
        assert stored.content_digest == material.content_digest
        assert stored.duration_ms == material.duration_ms
        assert stored.width == material.width
        assert stored.height == material.height

        async with database.session() as session:
            raw = (
                (
                    await session.execute(
                        select(
                            materials.c.has_speech,
                            materials.c.speech_segments_ms,
                            materials.c.speech_transcript,
                        ).where(
                            materials.c.installation_id == installation_id.uuid,
                            materials.c.material_id == material_id.uuid,
                        )
                    )
                )
                .tuples()
                .one()
            )
        assert raw == (
            True,
            [list(segment) for segment in result.speech_segments_ms],
            result.speech_transcript,
        )
        print(f"LE-14 real ASR model: {BAILIAN_ASR_MODEL_ID}")
        print(f"LE-14 real transcript: {result.speech_transcript}")
        print(f"LE-14 real speech segments: {result.speech_segments_ms}")
        print(f"LE-14 PostgreSQL material row: {material_id}")
    finally:
        async with database.session() as session:
            await session.execute(
                delete(materials).where(
                    materials.c.installation_id == installation_id.uuid,
                    materials.c.material_id == material_id.uuid,
                )
            )
            await session.execute(
                delete(installations).where(installations.c.id == installation_id.uuid)
            )
        await database.close()
