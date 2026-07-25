"""PB-05: the controlled upload boundary for one Douyin publish artifact.

Only an already known local video file may ever reach the browser file input.
The path is validated as an absolute, non-symlinked, single-link regular file
with a frozen media extension and bounded size, and its bytes are digested by
streaming through a stable file descriptor. ``revalidate`` re-proves the same
file identity and digest immediately before the upload, so a path replaced
between selection and upload is refused instead of silently published.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Final, cast

from automation_tool.protocol.safe_text import contains_control_or_bidi, is_sha256_hex

MAX_DOUYIN_PUBLISH_ARTIFACT_BYTES: Final = 4 * 1024 * 1024 * 1024
DOUYIN_PUBLISH_ARTIFACT_MEDIA_TYPES: Final = MappingProxyType(
    {
        "mov": "video/quicktime",
        "mp4": "video/mp4",
    }
)

_READ_CHUNK_BYTES: Final = 1024 * 1024
_MAX_ARTIFACT_PATH_CHARACTERS: Final = 4096


class DouyinPublishArtifactRejected(RuntimeError):
    """The requested artifact cannot be uploaded without weakening the boundary."""

    def __init__(self) -> None:
        super().__init__("douyin publish artifact is unavailable")


@dataclass(frozen=True, slots=True, repr=False)
class DouyinPublishArtifact:
    """One validated local video file bound to its streamed digest."""

    path: Path = field(repr=False)
    media_type: str
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.path, Path)
            or not self.path.is_absolute()
            or self.media_type not in set(DOUYIN_PUBLISH_ARTIFACT_MEDIA_TYPES.values())
            or type(self.size_bytes) is not int
            or not 1 <= self.size_bytes <= MAX_DOUYIN_PUBLISH_ARTIFACT_BYTES
            or not is_sha256_hex(self.sha256)
        ):
            raise DouyinPublishArtifactRejected

    def revalidate(self) -> None:
        """Re-prove the same file, size and digest right before the upload."""
        current = open_publish_artifact(self.path, maximum_bytes=self.size_bytes)
        if (
            current.sha256 != self.sha256
            or current.size_bytes != self.size_bytes
            or current.media_type != self.media_type
        ):
            raise DouyinPublishArtifactRejected

    def __repr__(self) -> str:
        return (
            "DouyinPublishArtifact("
            f"media_type={self.media_type!r}, size_bytes={self.size_bytes!r}, "
            f"sha256={self.sha256[:12]!r}…, path=<redacted>)"
        )


def open_publish_artifact(
    path: object,
    *,
    maximum_bytes: int = MAX_DOUYIN_PUBLISH_ARTIFACT_BYTES,
) -> DouyinPublishArtifact:
    """Validate one local artifact path and digest it through a stable descriptor."""
    try:
        if (
            type(maximum_bytes) is not int
            or not 1 <= maximum_bytes <= MAX_DOUYIN_PUBLISH_ARTIFACT_BYTES
        ):
            raise ValueError
        candidate = _require_artifact_path(path)
        media_type = _require_media_type(candidate)
        size_bytes, digest = _digest_stable_file(candidate, maximum_bytes)
    except DouyinPublishArtifactRejected:
        raise
    except Exception:
        raise DouyinPublishArtifactRejected from None
    return DouyinPublishArtifact(
        path=candidate,
        media_type=media_type,
        size_bytes=size_bytes,
        sha256=digest,
    )


def _require_artifact_path(source: object) -> Path:
    if not isinstance(source, Path):
        raise ValueError
    encoded = os.fspath(source)
    if (
        type(encoded) is not str
        or not encoded
        or len(encoded) > _MAX_ARTIFACT_PATH_CHARACTERS
        or contains_control_or_bidi(encoded)
        or not source.is_absolute()
        or any(part in {".", ".."} for part in source.parts)
    ):
        raise ValueError
    _reject_linked_ancestors(source.parent)
    return source


def _require_media_type(path: Path) -> str:
    suffixes = path.suffixes
    extension = path.suffix.removeprefix(".")
    if len(suffixes) != 1 or not extension or extension not in DOUYIN_PUBLISH_ARTIFACT_MEDIA_TYPES:
        raise ValueError
    return DOUYIN_PUBLISH_ARTIFACT_MEDIA_TYPES[extension]


def _reject_linked_ancestors(path: Path) -> None:
    current = path
    while True:
        if _is_reparse_point(current.lstat()):
            raise ValueError
        if current.parent == current:
            return
        current = current.parent


def _digest_stable_file(path: Path, maximum_bytes: int) -> tuple[int, str]:
    flags = os.O_RDONLY
    flags |= cast(int, getattr(os, "O_BINARY", 0))
    flags |= cast(int, getattr(os, "O_NOFOLLOW", 0))
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        _validate_regular_file(before, maximum_bytes)
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, _READ_CHUNK_BYTES))
            if not chunk:
                raise ValueError
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ValueError
        after = os.fstat(descriptor)
        if _identity(before) != _identity(after):
            raise ValueError
    finally:
        os.close(descriptor)
    reopened = path.lstat()
    _validate_regular_file(reopened, maximum_bytes)
    if _identity(after) != _identity(reopened):
        raise ValueError
    return int(after.st_size), digest.hexdigest()


def _validate_regular_file(metadata: os.stat_result, maximum_bytes: int) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or _is_reparse_point(metadata)
        or metadata.st_nlink != 1
        or not 1 <= metadata.st_size <= maximum_bytes
    ):
        raise ValueError
    if os.name != "nt" and metadata.st_uid != _current_user_id():
        raise ValueError


def _current_user_id() -> int:
    """Read the POSIX owner id; `os.getuid` does not exist on Windows."""
    return cast(Callable[[], int], vars(os)["getuid"])()


def _is_reparse_point(metadata: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(reparse_flag and attributes & reparse_flag)


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns


__all__ = [
    "DOUYIN_PUBLISH_ARTIFACT_MEDIA_TYPES",
    "MAX_DOUYIN_PUBLISH_ARTIFACT_BYTES",
    "DouyinPublishArtifact",
    "DouyinPublishArtifactRejected",
    "open_publish_artifact",
]
