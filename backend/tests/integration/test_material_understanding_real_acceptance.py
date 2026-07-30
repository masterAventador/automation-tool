"""LE-13 T4: real media → Bailian → PostgreSQL acceptance."""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import AlembicRunner
from sqlalchemy import delete, insert, select

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, os.fspath(REPOSITORY_ROOT / "scripts"))
from run_le_13_acceptance import (  # noqa: E402
    read_bailian_api_key,
)
from video_runtime_cache import cache_root  # noqa: E402

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
from automation_tool.executor.adaptive_frame_extraction import (  # noqa: E402
    AdaptiveFrameRejection,
    extract_adaptive_frames,
)
from automation_tool.executor.material_probe import (  # noqa: E402
    PackagedMediaTools,
    approve_source,
    probe_material,
)
from automation_tool.executor.material_understanding import (  # noqa: E402
    BailianMaterialUnderstandingAdapter,
    MaterialUnderstandingOptions,
    load_bailian_material_understanding_config,
    understand_material_artifacts,
)

CATALOG_PATH = REPOSITORY_ROOT / "contracts/video/bailian-model-catalog.v1.json"
SECRET_PATH_ENVIRONMENT = "AUTOMATION_TOOL_LE13_SECRET_PATH"
PACKAGED_TOOL_SUBDIRECTORY = "media-toolchain/bin"
pytestmark = pytest.mark.skipif(
    SECRET_PATH_ENVIRONMENT not in os.environ,
    reason="run through scripts/run_le_13_acceptance.py",
)


def _packaged_tools() -> PackagedMediaTools:
    suffix = ".exe" if os.name == "nt" else ""
    root = cache_root() / PACKAGED_TOOL_SUBDIRECTORY
    return PackagedMediaTools(
        ffprobe_path=root / f"ffprobe{suffix}",
        ffmpeg_path=root / f"ffmpeg{suffix}",
    )


def _write_real_material(ffmpeg: Path, target: Path) -> None:
    subprocess.run(
        [
            os.fspath(ffmpeg),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=10:duration=1",
            "-f",
            "lavfi",
            "-i",
            "smptebars=size=640x360:rate=10:duration=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=royalblue:size=640x360:rate=10:duration=1",
            "-filter_complex",
            "[0:v][1:v][2:v]concat=n=3:v=1:a=0[video]",
            "-map",
            "[video]",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            os.fspath(target),
        ],
        check=True,
        capture_output=True,
    )


@pytest.mark.asyncio
async def test_real_material_is_understood_and_atomically_written_to_postgresql(
    postgresql_url: str,
    alembic_runner: AlembicRunner,
    tmp_path: Path,
) -> None:
    secret_path = os.environ[SECRET_PATH_ENVIRONMENT]
    api_key = read_bailian_api_key(Path(secret_path))
    tools = _packaged_tools()
    source = tmp_path / "真实素材.mp4"
    output = tmp_path / "frames"
    output.mkdir(mode=0o700)
    _write_real_material(tools.ffmpeg_path, source)

    facts = probe_material(tools, source)
    assert facts.duration_ms is not None
    approved_source, approved = approve_source(source)
    artifacts = extract_adaptive_frames(
        tools,
        approved_source,
        approved,
        output,
        duration_ms=facts.duration_ms,
    )
    assert not isinstance(artifacts, AdaptiveFrameRejection)
    assert artifacts

    adapter = BailianMaterialUnderstandingAdapter(
        load_bailian_material_understanding_config(
            catalog_path=CATALOG_PATH,
            api_key=api_key,
            timeout_seconds=120,
        )
    )
    result = understand_material_artifacts(
        adapter,
        output_directory=output,
        artifacts=artifacts,
        duration_ms=facts.duration_ms,
        options=MaterialUnderstandingOptions(enable_thinking=False),
    )
    assert result.request_id
    assert not any(
        unicodedata.category(character).startswith("C") for character in result.request_id
    )
    assert result.description
    assert result.shots
    assert result.shots[-1].end_ms <= facts.duration_ms

    alembic_runner(postgresql_url, "upgrade", "head")
    database = Database.from_url(postgresql_url)
    installation_id = InstallationId.new()
    material_id = MaterialId.new()
    repository = SqlAlchemyMaterialRepository(database)
    service = MaterialService(repository=repository)
    described_at = datetime.now(UTC)
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
        stored = await service.update_understanding(
            installation_id=installation_id,
            material_id=str(material_id),
            source=DescriptionSource.AI,
            description=result.description,
            tags=result.tags,
            shot_boundaries_ms=result.shot_boundaries_ms,
            described_at=described_at,
        )

        assert stored.ai_description == result.description
        assert stored.ai_tags == result.tags
        assert stored.shot_boundaries_ms == result.shot_boundaries_ms
        assert stored.description_source is DescriptionSource.AI
        assert stored.described_at == described_at

        async with database.session() as session:
            raw = (
                (
                    await session.execute(
                        select(
                            materials.c.ai_description,
                            materials.c.ai_tags,
                            materials.c.shot_boundaries_ms,
                            materials.c.description_source,
                            materials.c.described_at,
                        ).where(materials.c.material_id == material_id.uuid)
                    )
                )
                .tuples()
                .one()
            )
        assert raw == (
            result.description,
            list(result.tags),
            list(result.shot_boundaries_ms),
            DescriptionSource.AI.value,
            described_at,
        )
        print(f"LE-13 real Bailian request id: {result.request_id}")
        print(f"LE-13 PostgreSQL material row: {material_id}")
    finally:
        async with database.session() as session:
            await session.execute(delete(materials))
            await session.execute(
                delete(installations).where(installations.c.id == installation_id.uuid)
            )
        await database.close()
