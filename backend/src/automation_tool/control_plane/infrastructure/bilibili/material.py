"""Root-contained filesystem material and cover sources for PB-03.

The reader only accepts a single-segment file name below an explicitly
configured material root; traversal, separators, hidden names, symlinks and
non-regular files fail closed.  Content integrity against replacement is
enforced by the application service re-verifying the SHA-256 digest before any
upload.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path
from typing import Final

from automation_tool.control_plane.application.bilibili_archive_publishing import (
    BilibiliArchivePublishRejected,
    BilibiliCoverStat,
    BilibiliPublishMaterialStat,
)

_CHUNK_BYTES: Final = 1024 * 1024
_SAFE_NAME_PATTERN: Final = re.compile(r"^[^/\\]+\.[A-Za-z0-9]+$")


def _resolve_contained_file(root: Path, file_name: str) -> Path:
    if (
        not isinstance(root, Path)
        or not isinstance(file_name, str)
        or not 1 <= len(file_name) <= 255
        or _SAFE_NAME_PATTERN.fullmatch(file_name) is None
        or file_name.startswith(".")
        or ".." in file_name
        or not root.is_dir()
        or root.is_symlink()
    ):
        raise BilibiliArchivePublishRejected
    path = root / file_name
    if path.parent != root or path.is_symlink() or not path.is_file():
        raise BilibiliArchivePublishRejected
    return path


def _read_exact_range(path: Path, offset: int, length: int) -> bytes:
    with path.open("rb") as handle:
        handle.seek(offset)
        payload = handle.read(length)
    if len(payload) != length:
        raise BilibiliArchivePublishRejected
    return payload


def _stream_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


class FilesystemBilibiliPublishMaterial:
    """One local video material below a controlled root directory."""

    def __init__(self, *, root: Path, file_name: str, duration_seconds: int) -> None:
        if type(duration_seconds) is not int or duration_seconds < 1:
            raise BilibiliArchivePublishRejected
        self._path = _resolve_contained_file(root, file_name)
        self._file_name = file_name
        self._duration_seconds = duration_seconds

    async def stat(self) -> BilibiliPublishMaterialStat:
        try:
            size = self._path.stat().st_size
        except OSError:
            raise BilibiliArchivePublishRejected from None
        return BilibiliPublishMaterialStat(
            file_name=self._file_name,
            size_bytes=size,
            duration_seconds=self._duration_seconds,
        )

    async def sha256(self) -> str:
        try:
            return await asyncio.to_thread(_stream_sha256, self._path)
        except OSError:
            raise BilibiliArchivePublishRejected from None

    async def read_range(self, offset: int, length: int) -> bytes:
        if type(offset) is not int or type(length) is not int or offset < 0 or length < 1:
            raise BilibiliArchivePublishRejected
        try:
            size = self._path.stat().st_size
        except OSError:
            raise BilibiliArchivePublishRejected from None
        if offset + length > size:
            raise BilibiliArchivePublishRejected
        try:
            return await asyncio.to_thread(_read_exact_range, self._path, offset, length)
        except OSError:
            raise BilibiliArchivePublishRejected from None


class FilesystemBilibiliCoverSource:
    """One local cover image below a controlled root directory."""

    def __init__(self, *, root: Path, file_name: str) -> None:
        self._path = _resolve_contained_file(root, file_name)
        self._file_name = file_name

    async def describe(self) -> BilibiliCoverStat:
        try:
            size = self._path.stat().st_size
        except OSError:
            raise BilibiliArchivePublishRejected from None
        return BilibiliCoverStat(file_name=self._file_name, size_bytes=size)

    async def read(self) -> bytes:
        try:
            return await asyncio.to_thread(self._path.read_bytes)
        except OSError:
            raise BilibiliArchivePublishRejected from None


__all__ = ["FilesystemBilibiliCoverSource", "FilesystemBilibiliPublishMaterial"]
