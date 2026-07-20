"""Bounded local evidence for a known Douyin page-contract drift."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Final, Protocol, cast, runtime_checkable
from uuid import RFC_4122, UUID, uuid4

from automation_tool.protocol.limits import MAX_CROSS_RUNTIME_SEQUENCE

PAGE_DRIFT_ARTIFACT_VERSION: Final = "executor.page-drift-artifact.v1"
PAGE_DRIFT_ARTIFACT_MEDIA_TYPE: Final = "application/vnd.automation-tool.page-drift+json"
PAGE_DRIFT_ARTIFACT_DIRECTORY: Final = "page-drift-artifacts"
MAX_PAGE_DRIFT_ARTIFACT_BYTES: Final = 2_048
MAX_PAGE_DRIFT_ARTIFACTS: Final = 20

_ALLOWED_EVIDENCE = frozenset({"page_version_unknown", "conflicting_anchors"})
_ALLOWED_STAGES = frozenset({"search"})
_ARTIFACT_NAME = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.json$"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PageDriftArtifactRejected(RuntimeError):
    """A local page-drift artifact cannot be stored without weakening its boundary."""

    def __init__(self) -> None:
        super().__init__("page drift artifact is unavailable")


@runtime_checkable
class PageDriftArtifactClock(Protocol):
    def now(self) -> datetime: ...


class SystemPageDriftArtifactClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class PageDriftArtifactRef:
    artifact_id: UUID
    sha256: str
    media_type: str
    size_bytes: int
    relative_path: str

    def __post_init__(self) -> None:
        expected_path = PurePosixPath(
            PAGE_DRIFT_ARTIFACT_DIRECTORY,
            f"{self.artifact_id}.json",
        ).as_posix()
        if (
            not isinstance(self.artifact_id, UUID)
            or self.artifact_id.version != 4
            or self.artifact_id.variant != RFC_4122
            or type(self.sha256) is not str
            or _SHA256.fullmatch(self.sha256) is None
            or self.media_type != PAGE_DRIFT_ARTIFACT_MEDIA_TYPE
            or type(self.size_bytes) is not int
            or not 1 <= self.size_bytes <= MAX_PAGE_DRIFT_ARTIFACT_BYTES
            or self.relative_path != expected_path
        ):
            raise PageDriftArtifactRejected


class PageDriftArtifactStore:
    """Write only fixed-schema drift facts beneath the private Executor state root."""

    def __init__(
        self,
        *,
        state_directory: Path,
        clock: PageDriftArtifactClock | None = None,
        id_source: Callable[[], object] = uuid4,
    ) -> None:
        try:
            resolved_clock = SystemPageDriftArtifactClock() if clock is None else clock
            if (
                not isinstance(state_directory, Path)
                or not isinstance(resolved_clock, PageDriftArtifactClock)
                or not callable(id_source)
            ):
                raise ValueError
            self._state_directory = state_directory.absolute()
            state_status = self._require_private_directory(self._state_directory)
            self._state_identity = (state_status.st_dev, state_status.st_ino)
            self._artifact_directory = self._state_directory / PAGE_DRIFT_ARTIFACT_DIRECTORY
            self._clock = resolved_clock
            self._id_source = id_source
        except Exception:
            raise PageDriftArtifactRejected from None

    def capture(
        self,
        *,
        evidence: str,
        page_revision: int,
        stage: str,
    ) -> PageDriftArtifactRef:
        result: PageDriftArtifactRef | None = None
        with suppress(Exception):
            result = self._capture(
                evidence=evidence,
                page_revision=page_revision,
                stage=stage,
            )
        if result is None:
            raise PageDriftArtifactRejected from None
        return result

    def _capture(
        self,
        *,
        evidence: str,
        page_revision: int,
        stage: str,
    ) -> PageDriftArtifactRef:
        if (
            type(evidence) is not str
            or evidence not in _ALLOWED_EVIDENCE
            or type(page_revision) is not int
            or not 1 <= page_revision <= MAX_CROSS_RUNTIME_SEQUENCE
            or type(stage) is not str
            or stage not in _ALLOWED_STAGES
        ):
            raise ValueError
        self._revalidate_state_directory()
        self._prepare_artifact_directory()
        if self._artifact_count() >= MAX_PAGE_DRIFT_ARTIFACTS:
            raise ValueError
        artifact_id = self._new_id()
        observed_at = self._now()
        document = {
            "artifact_id": str(artifact_id),
            "artifact_version": PAGE_DRIFT_ARTIFACT_VERSION,
            "evidence": evidence,
            "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
            "operation": "douyin_target_discovery",
            "page_revision": page_revision,
            "platform": "douyin",
            "stage": stage,
        }
        payload = json.dumps(
            document,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        artifact_name = f"{artifact_id}.json"
        artifact_path = self._artifact_directory / artifact_name
        self._write_exclusive(artifact_path, payload)
        relative_path = PurePosixPath(PAGE_DRIFT_ARTIFACT_DIRECTORY, artifact_name).as_posix()
        return PageDriftArtifactRef(
            artifact_id=artifact_id,
            sha256=hashlib.sha256(payload).hexdigest(),
            media_type=PAGE_DRIFT_ARTIFACT_MEDIA_TYPE,
            size_bytes=len(payload),
            relative_path=relative_path,
        )

    @staticmethod
    def _require_private_directory(path: Path) -> os.stat_result:
        status = path.lstat()
        if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
            raise ValueError
        return status

    def _revalidate_state_directory(self) -> None:
        status = self._require_private_directory(self._state_directory)
        if (status.st_dev, status.st_ino) != self._state_identity:
            raise ValueError

    def _prepare_artifact_directory(self) -> None:
        with suppress(FileExistsError):
            self._artifact_directory.mkdir(mode=0o700)
        self._require_private_directory(self._artifact_directory)
        if os.name != "nt":  # pragma: no branch - platform-specific permission API
            self._artifact_directory.chmod(0o700)

    def _artifact_count(self) -> int:
        count = 0
        for entry in self._artifact_directory.iterdir():
            status = entry.lstat()
            if (
                not _ARTIFACT_NAME.fullmatch(entry.name)
                or stat.S_ISLNK(status.st_mode)
                or not stat.S_ISREG(status.st_mode)
                or not 1 <= status.st_size <= MAX_PAGE_DRIFT_ARTIFACT_BYTES
            ):
                raise ValueError
            count += 1
            if count > MAX_PAGE_DRIFT_ARTIFACTS:
                raise ValueError
        return count

    def _new_id(self) -> UUID:
        value = self._id_source()
        if not isinstance(value, UUID) or value.version != 4 or value.variant != RFC_4122:
            raise ValueError
        return value

    def _now(self) -> datetime:
        value = self._clock.now()
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError
        return value.astimezone(UTC)

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
            with os.fdopen(descriptor, "wb", closefd=True) as output:
                descriptor = None
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            if os.name != "nt":  # pragma: no branch - platform-specific permission API
                path.chmod(0o600)
        except Exception:
            if descriptor is not None:
                with suppress(OSError):
                    os.close(descriptor)
            if created:
                with suppress(OSError):
                    path.unlink()
            raise


__all__ = [
    "MAX_PAGE_DRIFT_ARTIFACTS",
    "MAX_PAGE_DRIFT_ARTIFACT_BYTES",
    "PAGE_DRIFT_ARTIFACT_DIRECTORY",
    "PAGE_DRIFT_ARTIFACT_MEDIA_TYPE",
    "PAGE_DRIFT_ARTIFACT_VERSION",
    "PageDriftArtifactClock",
    "PageDriftArtifactRef",
    "PageDriftArtifactRejected",
    "PageDriftArtifactStore",
    "SystemPageDriftArtifactClock",
]
