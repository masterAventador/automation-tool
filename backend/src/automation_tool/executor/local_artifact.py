"""Private, content-addressed references for bounded Local Executor artifacts."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, cast
from uuid import RFC_4122, UUID, uuid4

MAX_LOCAL_ARTIFACT_BYTES: Final = 64 * 1024 * 1024
MAX_LOCAL_ARTIFACTS_PER_POLICY: Final = 10_000
MAX_LOCAL_ARTIFACT_RETENTION_SECONDS: Final = 365 * 24 * 60 * 60
MAX_LOCAL_ARTIFACT_MINIMUM_FREE_BYTES: Final = 4 * 1024 * 1024 * 1024
DEFAULT_LOCAL_ARTIFACT_RETENTION_SECONDS: Final = 7 * 24 * 60 * 60
DEFAULT_LOCAL_ARTIFACT_MINIMUM_FREE_BYTES: Final = 64 * 1024 * 1024

_MAX_RELATIVE_PATH_BYTES: Final = 512
_MAX_DIRECTORY_SEGMENTS: Final = 8
_DIRECTORY_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_FILE_EXTENSION = re.compile(r"^[a-z0-9][a-z0-9]{0,15}$")
_MEDIA_TYPE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class LocalArtifactRejected(RuntimeError):
    """A local artifact cannot be used without weakening its filesystem boundary."""

    def __init__(self) -> None:
        super().__init__("Local Artifact is unavailable")


@dataclass(frozen=True, slots=True)
class LocalArtifactPolicy:
    """A trusted producer's fixed namespace, media type, and resource limits."""

    relative_directory: str
    file_extension: str
    media_type: str
    maximum_bytes: int
    maximum_artifacts: int
    retention_seconds: int = DEFAULT_LOCAL_ARTIFACT_RETENTION_SECONDS
    minimum_free_bytes: int = DEFAULT_LOCAL_ARTIFACT_MINIMUM_FREE_BYTES

    def __post_init__(self) -> None:
        if (
            not _valid_relative_directory(self.relative_directory)
            or type(self.file_extension) is not str
            or _FILE_EXTENSION.fullmatch(self.file_extension) is None
            or type(self.media_type) is not str
            or _MEDIA_TYPE.fullmatch(self.media_type) is None
            or type(self.maximum_bytes) is not int
            or not 1 <= self.maximum_bytes <= MAX_LOCAL_ARTIFACT_BYTES
            or type(self.maximum_artifacts) is not int
            or not 1 <= self.maximum_artifacts <= MAX_LOCAL_ARTIFACTS_PER_POLICY
            or type(self.retention_seconds) is not int
            or not 1 <= self.retention_seconds <= MAX_LOCAL_ARTIFACT_RETENTION_SECONDS
            or type(self.minimum_free_bytes) is not int
            or not 0 <= self.minimum_free_bytes <= MAX_LOCAL_ARTIFACT_MINIMUM_FREE_BYTES
        ):
            raise LocalArtifactRejected

    def relative_path(self, artifact_id: UUID) -> str:
        if not _valid_artifact_id(artifact_id):
            raise LocalArtifactRejected
        return PurePosixPath(
            self.relative_directory,
            f"{artifact_id}.{self.file_extension}",
        ).as_posix()


@dataclass(frozen=True, slots=True)
class LocalArtifactRef:
    """Stable, path-safe metadata for bytes that remain on the user's computer."""

    artifact_id: UUID
    sha256: str
    media_type: str
    size_bytes: int
    relative_path: str

    def __post_init__(self) -> None:
        if (
            not _valid_artifact_id(self.artifact_id)
            or type(self.sha256) is not str
            or _SHA256.fullmatch(self.sha256) is None
            or type(self.media_type) is not str
            or _MEDIA_TYPE.fullmatch(self.media_type) is None
            or type(self.size_bytes) is not int
            or not 1 <= self.size_bytes <= MAX_LOCAL_ARTIFACT_BYTES
            or not _valid_artifact_relative_path(self.relative_path, self.artifact_id)
        ):
            raise LocalArtifactRejected


@dataclass(frozen=True, slots=True)
class LocalArtifactCleanupResult:
    """Bounded cleanup facts that expose no path or artifact identity."""

    removed_artifacts: int
    removed_bytes: int
    remaining_artifacts: int


@dataclass(frozen=True, slots=True)
class _ArtifactEntry:
    artifact_id: UUID
    path: Path
    metadata: os.stat_result


class LocalArtifactStore:
    """Create and revalidate one trusted artifact namespace below a private root."""

    def __init__(
        self,
        *,
        root_directory: Path,
        policy: LocalArtifactPolicy,
        id_source: Callable[[], object] = uuid4,
    ) -> None:
        initialized = False
        with suppress(Exception):
            if (
                not isinstance(root_directory, Path)
                or not isinstance(policy, LocalArtifactPolicy)
                or not callable(id_source)
            ):
                raise ValueError
            self._root_directory = root_directory.absolute()
            _reject_linked_ancestors(self._root_directory)
            root_status = _require_private_directory(self._root_directory)
            self._root_identity = _identity(root_status)
            self._policy = policy
            self._id_source = id_source
            self._artifact_directory = _prepare_private_subdirectories(
                self._root_directory,
                PurePosixPath(policy.relative_directory).parts,
            )
            artifact_status = _require_private_directory(self._artifact_directory)
            self._artifact_directory_identity = _identity(artifact_status)
            initialized = True
        if not initialized:
            raise LocalArtifactRejected

    def capture(
        self,
        payload: bytes,
        *,
        protected_references: tuple[LocalArtifactRef, ...] = (),
    ) -> LocalArtifactRef:
        """Persist already-bounded bytes with a new stable identifier."""

        return self.capture_generated(
            lambda _artifact_id: payload,
            protected_references=protected_references,
        )

    def capture_generated(
        self,
        payload_factory: Callable[[UUID], object],
        *,
        protected_references: tuple[LocalArtifactRef, ...] = (),
    ) -> LocalArtifactRef:
        """Build fixed-schema bytes after the store chooses their stable identifier."""

        result: LocalArtifactRef | None = None
        with suppress(Exception):
            if not callable(payload_factory):
                raise ValueError
            result = self._capture_generated(payload_factory, protected_references)
        if result is None:
            raise LocalArtifactRejected
        return result

    def cleanup(
        self,
        *,
        protected_references: tuple[LocalArtifactRef, ...] = (),
    ) -> LocalArtifactCleanupResult:
        """Remove expired or pressure-selected artifacts without deleting protected facts."""

        result: LocalArtifactCleanupResult | None = None
        with suppress(Exception):
            result = self._govern(
                protected_references=protected_references,
                reserve_artifacts=0,
                reserve_bytes=0,
            )
        if result is None:
            raise LocalArtifactRejected
        return result

    def prepare_capture(
        self,
        *,
        protected_references: tuple[LocalArtifactRef, ...] = (),
    ) -> LocalArtifactCleanupResult:
        """Reserve one maximum-sized slot before a coordinated multi-artifact capture."""

        result: LocalArtifactCleanupResult | None = None
        with suppress(Exception):
            result = self._govern(
                protected_references=protected_references,
                reserve_artifacts=1,
                reserve_bytes=self._policy.maximum_bytes,
            )
        if result is None:
            raise LocalArtifactRejected
        return result

    def resolve(self, artifact_id: UUID) -> LocalArtifactRef:
        """Resolve one artifact by stable ID without exposing its absolute path."""

        result: LocalArtifactRef | None = None
        with suppress(Exception):
            if not _valid_artifact_id(artifact_id):
                raise ValueError
            self._revalidate_directories()
            self._inventory()
            relative_path = self._policy.relative_path(artifact_id)
            payload = self._read_stable(self._root_directory / relative_path)
            result = self._reference(artifact_id, relative_path, payload)
        if result is None:
            raise LocalArtifactRejected
        return result

    def read(self, reference: LocalArtifactRef) -> bytes:
        """Read bytes only when the complete caller-held reference still matches disk."""

        result: bytes | None = None
        with suppress(Exception):
            if not isinstance(reference, LocalArtifactRef):
                raise ValueError
            expected_path = self._policy.relative_path(reference.artifact_id)
            if (
                reference.media_type != self._policy.media_type
                or reference.relative_path != expected_path
                or reference.size_bytes > self._policy.maximum_bytes
            ):
                raise ValueError
            self._revalidate_directories()
            self._inventory()
            payload = self._read_stable(self._root_directory / expected_path)
            stored_reference = self._reference(reference.artifact_id, expected_path, payload)
            if (
                stored_reference.artifact_id != reference.artifact_id
                or stored_reference.sha256 != reference.sha256
                or stored_reference.media_type != reference.media_type
                or stored_reference.size_bytes != reference.size_bytes
                or stored_reference.relative_path != reference.relative_path
            ):
                raise ValueError
            result = payload
        if result is None:
            raise LocalArtifactRejected
        return result

    def list_references(self) -> tuple[LocalArtifactRef, ...]:
        """List the namespace as stable metadata without exposing absolute paths."""

        result: tuple[LocalArtifactRef, ...] | None = None
        with suppress(Exception):
            self._revalidate_directories()
            references: list[LocalArtifactRef] = []
            for artifact_id in self._inventory_ids():
                relative_path = self._policy.relative_path(artifact_id)
                payload = self._read_stable(self._root_directory / relative_path)
                references.append(self._reference(artifact_id, relative_path, payload))
            result = tuple(references)
        if result is None:
            raise LocalArtifactRejected
        return result

    def _capture_generated(
        self,
        payload_factory: Callable[[UUID], object],
        protected_references: tuple[LocalArtifactRef, ...],
    ) -> LocalArtifactRef:
        self._revalidate_directories()
        artifact_id = self._new_id()
        payload = payload_factory(artifact_id)
        if type(payload) is not bytes or not 1 <= len(payload) <= self._policy.maximum_bytes:
            raise ValueError
        self._govern(
            protected_references=protected_references,
            reserve_artifacts=1,
            reserve_bytes=len(payload),
        )
        relative_path = self._policy.relative_path(artifact_id)
        artifact_path = self._root_directory / relative_path
        self._write_exclusive(artifact_path, payload)
        stored = self._read_stable(artifact_path)
        if stored != payload:
            with suppress(OSError):
                artifact_path.unlink()
            raise ValueError
        return self._reference(artifact_id, relative_path, stored)

    def _revalidate_directories(self) -> None:
        root_status = _require_private_directory(self._root_directory)
        artifact_status = _require_private_directory(self._artifact_directory)
        if (
            _identity(root_status) != self._root_identity
            or _identity(artifact_status) != self._artifact_directory_identity
        ):
            raise ValueError

    def _govern(
        self,
        *,
        protected_references: tuple[LocalArtifactRef, ...],
        reserve_artifacts: int,
        reserve_bytes: int,
    ) -> LocalArtifactCleanupResult:
        if (
            type(protected_references) is not tuple
            or type(reserve_artifacts) is not int
            or reserve_artifacts not in {0, 1}
            or type(reserve_bytes) is not int
            or not 0 <= reserve_bytes <= self._policy.maximum_bytes
            or (reserve_artifacts == 0 and reserve_bytes != 0)
        ):
            raise ValueError
        self._revalidate_directories()
        entries = list(self._inventory_entries())
        now_nanoseconds = time.time_ns()
        if type(now_nanoseconds) is not int or now_nanoseconds < 0:
            raise ValueError
        for entry in entries:
            if entry.metadata.st_mtime_ns < 0 or entry.metadata.st_mtime_ns > now_nanoseconds:
                raise ValueError
        protected_ids = self._protected_ids(protected_references)
        removed_artifacts = 0
        removed_bytes = 0
        retention_nanoseconds = self._policy.retention_seconds * 1_000_000_000

        def remove(entry: _ArtifactEntry) -> None:
            nonlocal removed_artifacts, removed_bytes
            self._unlink_entry(entry)
            entries.remove(entry)
            removed_artifacts += 1
            removed_bytes += entry.metadata.st_size

        for entry in tuple(sorted(entries, key=_cleanup_order)):
            if (
                entry.artifact_id not in protected_ids
                and now_nanoseconds - entry.metadata.st_mtime_ns >= retention_nanoseconds
            ):
                remove(entry)

        while self._under_pressure(entries, reserve_artifacts, reserve_bytes):
            candidate = next(
                (
                    entry
                    for entry in sorted(entries, key=_cleanup_order)
                    if entry.artifact_id not in protected_ids
                ),
                None,
            )
            if candidate is None:
                raise ValueError
            remove(candidate)

        if removed_artifacts:
            _fsync_directory(self._artifact_directory)
        self._revalidate_directories()
        if self._under_pressure(entries, reserve_artifacts, reserve_bytes):
            raise ValueError
        return LocalArtifactCleanupResult(
            removed_artifacts=removed_artifacts,
            removed_bytes=removed_bytes,
            remaining_artifacts=len(entries),
        )

    def _protected_ids(
        self,
        references: tuple[LocalArtifactRef, ...],
    ) -> frozenset[UUID]:
        if len(references) > self._policy.maximum_artifacts:
            raise ValueError
        protected: set[UUID] = set()
        for reference in references:
            if not isinstance(reference, LocalArtifactRef) or reference.artifact_id in protected:
                raise ValueError
            expected_path = self._policy.relative_path(reference.artifact_id)
            if (
                reference.media_type != self._policy.media_type
                or reference.relative_path != expected_path
                or reference.size_bytes > self._policy.maximum_bytes
            ):
                raise ValueError
            payload = self._read_stable(self._root_directory / expected_path)
            if not _same_reference(
                self._reference(reference.artifact_id, expected_path, payload),
                reference,
            ):
                raise ValueError
            protected.add(reference.artifact_id)
        return frozenset(protected)

    def _under_pressure(
        self,
        entries: list[_ArtifactEntry],
        reserve_artifacts: int,
        reserve_bytes: int,
    ) -> bool:
        return (
            len(entries) + reserve_artifacts > self._policy.maximum_artifacts
            or _available_bytes(self._root_directory)
            < self._policy.minimum_free_bytes + reserve_bytes
        )

    def _unlink_entry(self, entry: _ArtifactEntry) -> None:
        self._revalidate_directories()
        current = entry.path.lstat()
        _validate_private_file(entry.path, current, self._policy.maximum_bytes)
        if _file_identity(current) != _file_identity(entry.metadata):
            raise ValueError
        entry.path.unlink()
        with suppress(FileNotFoundError):
            entry.path.lstat()
            raise ValueError
        self._revalidate_directories()

    def _inventory(self) -> int:
        return len(self._inventory_ids())

    def _inventory_ids(self) -> tuple[UUID, ...]:
        return tuple(entry.artifact_id for entry in self._inventory_entries())

    def _inventory_entries(self) -> tuple[_ArtifactEntry, ...]:
        entries: list[_ArtifactEntry] = []
        for entry in self._artifact_directory.iterdir():
            artifact_id = _artifact_id_from_name(entry.name, self._policy.file_extension)
            if artifact_id is None:
                raise ValueError
            metadata = entry.lstat()
            _validate_private_file(entry, metadata, self._policy.maximum_bytes)
            entries.append(_ArtifactEntry(artifact_id=artifact_id, path=entry, metadata=metadata))
            if len(entries) > self._policy.maximum_artifacts:
                raise ValueError
        return tuple(sorted(entries, key=lambda item: str(item.artifact_id)))

    def _new_id(self) -> UUID:
        artifact_id = self._id_source()
        if not _valid_artifact_id(artifact_id):
            raise ValueError
        return cast(UUID, artifact_id)

    def _reference(
        self,
        artifact_id: UUID,
        relative_path: str,
        payload: bytes,
    ) -> LocalArtifactRef:
        return LocalArtifactRef(
            artifact_id=artifact_id,
            sha256=hashlib.sha256(payload).hexdigest(),
            media_type=self._policy.media_type,
            size_bytes=len(payload),
            relative_path=relative_path,
        )

    def _read_stable(self, path: Path) -> bytes:
        flags = os.O_RDONLY
        flags |= cast(int, getattr(os, "O_BINARY", 0))
        flags |= cast(int, getattr(os, "O_NOFOLLOW", 0))
        descriptor = os.open(path, flags)
        try:
            before = os.fstat(descriptor)
            _validate_private_file(path, before, self._policy.maximum_bytes)
            chunks: list[bytes] = []
            remaining = before.st_size
            while remaining:
                chunk = os.read(descriptor, min(remaining, 64 * 1024))
                if not chunk:
                    raise ValueError
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise ValueError
            after = os.fstat(descriptor)
            if _file_identity(before) != _file_identity(after):
                raise ValueError
            payload = b"".join(chunks)
        finally:
            os.close(descriptor)
        reopened = path.lstat()
        _validate_private_file(path, reopened, self._policy.maximum_bytes)
        if _file_identity(after) != _file_identity(reopened):
            raise ValueError
        return payload

    @staticmethod
    def _write_exclusive(path: Path, payload: bytes) -> None:
        descriptor: int | None = None
        created = False
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= cast(int, getattr(os, "O_BINARY", 0))
            flags |= cast(int, getattr(os, "O_NOFOLLOW", 0))
            descriptor = os.open(path, flags, 0o600)
            created = True
            if os.name != "nt":  # pragma: no branch - mutually exclusive native platform path
                cast(Callable[[int, int], None], vars(os)["fchmod"])(descriptor, 0o600)
            view = memoryview(payload)
            written = 0
            while written < len(view):
                count = os.write(descriptor, view[written:])
                if count <= 0:
                    raise OSError
                written += count
            os.fsync(descriptor)
            metadata = os.fstat(descriptor)
            _validate_private_file(path, metadata, len(payload))
        except Exception:
            if descriptor is not None:
                with suppress(OSError):
                    os.close(descriptor)
                descriptor = None
            if created:
                with suppress(OSError):
                    path.unlink()
            raise
        finally:
            if descriptor is not None:
                os.close(descriptor)


def _valid_artifact_id(value: object) -> bool:
    return (
        isinstance(value, UUID)
        and value.version == 4
        and value.variant == RFC_4122
        and str(value) == str(UUID(str(value)))
    )


def _valid_relative_directory(value: object) -> bool:
    if type(value) is not str or not value or len(value.encode("utf-8")) > 384:
        return False
    if "\\" in value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and 1 <= len(path.parts) <= _MAX_DIRECTORY_SEGMENTS
        and path.as_posix() == value
        and all(_DIRECTORY_SEGMENT.fullmatch(part) is not None for part in path.parts)
    )


def _valid_artifact_relative_path(value: object, artifact_id: UUID) -> bool:
    if (
        type(value) is not str
        or not value
        or "\\" in value
        or len(value.encode("utf-8")) > _MAX_RELATIVE_PATH_BYTES
    ):
        return False
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or len(path.parts) < 2:
        return False
    if not _valid_relative_directory(PurePosixPath(*path.parts[:-1]).as_posix()):
        return False
    name = path.name
    prefix = f"{artifact_id}."
    return name.startswith(prefix) and _FILE_EXTENSION.fullmatch(name[len(prefix) :]) is not None


def _artifact_id_from_name(name: str, extension: str) -> UUID | None:
    suffix = f".{extension}"
    if not name.endswith(suffix):
        return None
    source = name[: -len(suffix)]
    try:
        parsed = UUID(source)
    except ValueError:
        return None
    return parsed if _valid_artifact_id(parsed) and source == str(parsed) else None


def _prepare_private_subdirectories(root: Path, parts: tuple[str, ...]) -> Path:
    current = root
    for part in parts:
        current = current / part
        with suppress(FileExistsError):
            current.mkdir(mode=0o700)
        metadata = current.lstat()
        _validate_private_directory(current, metadata)
    return current


def _require_private_directory(path: Path) -> os.stat_result:
    metadata = path.lstat()
    _validate_private_directory(path, metadata)
    return metadata


def _validate_private_directory(path: Path, metadata: os.stat_result) -> None:
    if not stat.S_ISDIR(metadata.st_mode) or _is_reparse_point(metadata):
        raise ValueError
    _validate_private_owner_and_mode(metadata, 0o700)
    _validate_windows_private_acl(path)


def _validate_private_file(path: Path, metadata: os.stat_result, maximum_bytes: int) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or _is_reparse_point(metadata)
        or metadata.st_nlink != 1
        or not 1 <= metadata.st_size <= maximum_bytes
    ):
        raise ValueError
    _validate_private_owner_and_mode(metadata, 0o600)
    _validate_windows_private_acl(path)


def _validate_private_owner_and_mode(metadata: os.stat_result, expected_mode: int) -> None:
    if os.name != "nt" and (
        metadata.st_uid != cast(Callable[[], int], vars(os)["getuid"])()
        or stat.S_IMODE(metadata.st_mode) != expected_mode
    ):
        raise ValueError


def _validate_windows_private_acl(path: Path) -> None:
    if os.name == "nt":
        from automation_tool.executor.windows_acl import validate_private_acl

        validate_private_acl(path)


def _reject_linked_ancestors(path: Path) -> None:
    current = path
    while True:
        if current.exists() and _is_reparse_point(current.lstat()):
            raise ValueError
        if current.parent == current:
            return
        current = current.parent


def _is_reparse_point(metadata: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(reparse_flag and attributes & reparse_flag)


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns


def _cleanup_order(entry: _ArtifactEntry) -> tuple[int, str]:
    return entry.metadata.st_mtime_ns, str(entry.artifact_id)


def _same_reference(left: LocalArtifactRef, right: LocalArtifactRef) -> bool:
    return (
        left.artifact_id == right.artifact_id
        and left.sha256 == right.sha256
        and left.media_type == right.media_type
        and left.size_bytes == right.size_bytes
        and left.relative_path == right.relative_path
    )


def _available_bytes(path: Path) -> int:
    free = shutil.disk_usage(path).free
    if type(free) is not int or free < 0:
        raise ValueError
    return free


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | cast(int, getattr(os, "O_DIRECTORY", 0))
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "DEFAULT_LOCAL_ARTIFACT_MINIMUM_FREE_BYTES",
    "DEFAULT_LOCAL_ARTIFACT_RETENTION_SECONDS",
    "MAX_LOCAL_ARTIFACTS_PER_POLICY",
    "MAX_LOCAL_ARTIFACT_BYTES",
    "MAX_LOCAL_ARTIFACT_MINIMUM_FREE_BYTES",
    "MAX_LOCAL_ARTIFACT_RETENTION_SECONDS",
    "LocalArtifactCleanupResult",
    "LocalArtifactPolicy",
    "LocalArtifactRef",
    "LocalArtifactRejected",
    "LocalArtifactStore",
]
