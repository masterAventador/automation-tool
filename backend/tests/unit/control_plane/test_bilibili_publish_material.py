"""PB-03: root-contained filesystem material and cover source tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from automation_tool.control_plane.application.bilibili_archive_publishing import (
    BilibiliArchivePublishRejected,
)
from automation_tool.control_plane.infrastructure.bilibili.material import (
    FilesystemBilibiliCoverSource,
    FilesystemBilibiliPublishMaterial,
    _read_exact_range,
)


def write_material(root: Path, name: str = "demo.mp4", content: bytes = b"demo-bytes") -> Path:
    path = root / name
    path.write_bytes(content)
    return path


@pytest.mark.asyncio
async def test_material_reports_size_digest_and_ranges(tmp_path: Path) -> None:
    content = bytes(range(256)) * 8
    write_material(tmp_path, content=content)
    material = FilesystemBilibiliPublishMaterial(
        root=tmp_path, file_name="demo.mp4", duration_seconds=90
    )
    stat = await material.stat()
    assert stat.file_name == "demo.mp4"
    assert stat.size_bytes == len(content)
    assert stat.duration_seconds == 90
    assert await material.sha256() == hashlib.sha256(content).hexdigest()
    assert await material.read_range(0, 4) == content[:4]
    assert await material.read_range(10, 100) == content[10:110]


@pytest.mark.asyncio
async def test_material_read_range_rejects_out_of_bounds(tmp_path: Path) -> None:
    write_material(tmp_path, content=b"0123456789")
    material = FilesystemBilibiliPublishMaterial(
        root=tmp_path, file_name="demo.mp4", duration_seconds=90
    )
    for offset, length in ((-1, 4), (0, 0), (0, 11), (8, 3)):
        with pytest.raises(BilibiliArchivePublishRejected):
            await material.read_range(offset, length)


def test_material_rejects_escaping_or_unsafe_names(tmp_path: Path) -> None:
    write_material(tmp_path)
    for name in (
        "../demo.mp4",
        "sub/demo.mp4",
        "sub\\demo.mp4",
        ".hidden.mp4",
        "demo",
        "",
        "/etc/passwd",
    ):
        with pytest.raises(BilibiliArchivePublishRejected):
            FilesystemBilibiliPublishMaterial(root=tmp_path, file_name=name, duration_seconds=90)


def test_material_rejects_missing_root_missing_file_and_symlinks(tmp_path: Path) -> None:
    with pytest.raises(BilibiliArchivePublishRejected):
        FilesystemBilibiliPublishMaterial(
            root=tmp_path / "missing", file_name="demo.mp4", duration_seconds=90
        )
    with pytest.raises(BilibiliArchivePublishRejected):
        FilesystemBilibiliPublishMaterial(root=tmp_path, file_name="demo.mp4", duration_seconds=90)
    target = write_material(tmp_path, name="target.mp4")
    (tmp_path / "link.mp4").symlink_to(target)
    with pytest.raises(BilibiliArchivePublishRejected):
        FilesystemBilibiliPublishMaterial(root=tmp_path, file_name="link.mp4", duration_seconds=90)
    (tmp_path / "dir.mp4").mkdir()
    with pytest.raises(BilibiliArchivePublishRejected):
        FilesystemBilibiliPublishMaterial(root=tmp_path, file_name="dir.mp4", duration_seconds=90)


def test_material_rejects_invalid_duration(tmp_path: Path) -> None:
    write_material(tmp_path)
    for duration in (0, -1):
        with pytest.raises(BilibiliArchivePublishRejected):
            FilesystemBilibiliPublishMaterial(
                root=tmp_path, file_name="demo.mp4", duration_seconds=duration
            )


@pytest.mark.asyncio
async def test_cover_source_reports_and_reads_bytes(tmp_path: Path) -> None:
    (tmp_path / "cover.png").write_bytes(b"png-bytes")
    cover = FilesystemBilibiliCoverSource(root=tmp_path, file_name="cover.png")
    stat = await cover.describe()
    assert stat.file_name == "cover.png"
    assert stat.size_bytes == 9
    assert await cover.read() == b"png-bytes"


def test_cover_source_rejects_escaping_names(tmp_path: Path) -> None:
    (tmp_path / "cover.png").write_bytes(b"png-bytes")
    for name in ("../cover.png", "sub/cover.png", ".cover.png", "cover"):
        with pytest.raises(BilibiliArchivePublishRejected):
            FilesystemBilibiliCoverSource(root=tmp_path, file_name=name)


@pytest.mark.asyncio
async def test_material_fails_closed_when_the_file_disappears(tmp_path: Path) -> None:
    path = write_material(tmp_path)
    material = FilesystemBilibiliPublishMaterial(
        root=tmp_path, file_name="demo.mp4", duration_seconds=90
    )
    path.unlink()
    with pytest.raises(BilibiliArchivePublishRejected):
        await material.stat()
    with pytest.raises(BilibiliArchivePublishRejected):
        await material.sha256()
    with pytest.raises(BilibiliArchivePublishRejected):
        await material.read_range(0, 4)


@pytest.mark.asyncio
async def test_cover_source_fails_closed_when_the_file_disappears(tmp_path: Path) -> None:
    path = tmp_path / "cover.png"
    path.write_bytes(b"png-bytes")
    cover = FilesystemBilibiliCoverSource(root=tmp_path, file_name="cover.png")
    path.unlink()
    with pytest.raises(BilibiliArchivePublishRejected):
        await cover.describe()
    with pytest.raises(BilibiliArchivePublishRejected):
        await cover.read()


def test_short_reads_from_a_truncated_file_fail_closed(tmp_path: Path) -> None:
    path = write_material(tmp_path, content=b"0123456789")
    with pytest.raises(BilibiliArchivePublishRejected):
        _read_exact_range(path, 8, 5)


@pytest.mark.asyncio
async def test_unreadable_file_fails_closed_on_read_range(tmp_path: Path) -> None:
    path = write_material(tmp_path, content=b"0123456789")
    material = FilesystemBilibiliPublishMaterial(
        root=tmp_path, file_name="demo.mp4", duration_seconds=90
    )
    path.chmod(0o000)
    try:
        with pytest.raises(BilibiliArchivePublishRejected):
            await material.read_range(0, 4)
    finally:
        path.chmod(0o600)
