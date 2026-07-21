"""Bounded local evidence for a known Douyin page-contract drift."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol, runtime_checkable
from uuid import UUID, uuid4

from automation_tool.executor.local_artifact import (
    DEFAULT_LOCAL_ARTIFACT_MINIMUM_FREE_BYTES,
    LocalArtifactPolicy,
    LocalArtifactRef,
    LocalArtifactRejected,
    LocalArtifactStore,
)
from automation_tool.protocol.limits import MAX_CROSS_RUNTIME_SEQUENCE

PAGE_DRIFT_ARTIFACT_VERSION: Final = "executor.page-drift-artifact.v1"
PAGE_DRIFT_ARTIFACT_MEDIA_TYPE: Final = "application/vnd.automation-tool.page-drift+json"
PAGE_DRIFT_ARTIFACT_DIRECTORY: Final = "artifacts/evidence/page-drift"
MAX_PAGE_DRIFT_ARTIFACT_BYTES: Final = 2_048
MAX_PAGE_DRIFT_ARTIFACTS: Final = 20
PAGE_DRIFT_ARTIFACT_RETENTION_SECONDS: Final = 30 * 24 * 60 * 60
PAGE_DRIFT_ARTIFACT_POLICY: Final = LocalArtifactPolicy(
    relative_directory=PAGE_DRIFT_ARTIFACT_DIRECTORY,
    file_extension="json",
    media_type=PAGE_DRIFT_ARTIFACT_MEDIA_TYPE,
    maximum_bytes=MAX_PAGE_DRIFT_ARTIFACT_BYTES,
    maximum_artifacts=MAX_PAGE_DRIFT_ARTIFACTS,
    retention_seconds=PAGE_DRIFT_ARTIFACT_RETENTION_SECONDS,
    minimum_free_bytes=DEFAULT_LOCAL_ARTIFACT_MINIMUM_FREE_BYTES,
)

_ALLOWED_EVIDENCE = frozenset({"page_version_unknown", "conflicting_anchors"})
_ALLOWED_STAGES = frozenset({"search"})


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
class PageDriftArtifactRef(LocalArtifactRef):
    def __post_init__(self) -> None:
        valid = False
        with suppress(LocalArtifactRejected):
            LocalArtifactRef.__post_init__(self)
            valid = (
                self.media_type == PAGE_DRIFT_ARTIFACT_MEDIA_TYPE
                and self.size_bytes <= MAX_PAGE_DRIFT_ARTIFACT_BYTES
                and self.relative_path == f"{PAGE_DRIFT_ARTIFACT_DIRECTORY}/{self.artifact_id}.json"
            )
        if not valid:
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
            self._clock = resolved_clock
            self._artifacts = LocalArtifactStore(
                root_directory=state_directory,
                policy=PAGE_DRIFT_ARTIFACT_POLICY,
                id_source=id_source,
            )
            self._artifacts.cleanup()
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

        def build_payload(artifact_id: UUID) -> bytes:
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
            return json.dumps(
                document,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")

        reference = self._artifacts.capture_generated(build_payload)
        return PageDriftArtifactRef(
            artifact_id=reference.artifact_id,
            sha256=reference.sha256,
            media_type=reference.media_type,
            size_bytes=reference.size_bytes,
            relative_path=reference.relative_path,
        )

    def _now(self) -> datetime:
        value = self._clock.now()
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError
        return value.astimezone(UTC)


__all__ = [
    "MAX_PAGE_DRIFT_ARTIFACTS",
    "MAX_PAGE_DRIFT_ARTIFACT_BYTES",
    "PAGE_DRIFT_ARTIFACT_DIRECTORY",
    "PAGE_DRIFT_ARTIFACT_MEDIA_TYPE",
    "PAGE_DRIFT_ARTIFACT_POLICY",
    "PAGE_DRIFT_ARTIFACT_RETENTION_SECONDS",
    "PAGE_DRIFT_ARTIFACT_VERSION",
    "PageDriftArtifactClock",
    "PageDriftArtifactRef",
    "PageDriftArtifactRejected",
    "PageDriftArtifactStore",
    "SystemPageDriftArtifactClock",
]
