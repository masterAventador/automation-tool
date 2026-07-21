"""Bounded, redacted browser screenshot and structural trace artifacts."""

from __future__ import annotations

import json
import struct
import zlib
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, Protocol, cast, runtime_checkable
from uuid import UUID, uuid4

from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.local_artifact import (
    DEFAULT_LOCAL_ARTIFACT_MINIMUM_FREE_BYTES,
    LocalArtifactPolicy,
    LocalArtifactRef,
    LocalArtifactStore,
)
from automation_tool.protocol.limits import MAX_CROSS_RUNTIME_SEQUENCE

MAX_BROWSER_DIAGNOSTIC_ARTIFACTS: Final = 8
MAX_BROWSER_DIAGNOSTIC_SCREENSHOT_BYTES: Final = 1024 * 1024
MAX_BROWSER_DIAGNOSTIC_TRACE_BYTES: Final = 4 * 1024
SCREENSHOT_TIMEOUT_MILLISECONDS: Final = 5_000
BROWSER_DIAGNOSTIC_RETENTION_SECONDS: Final = 7 * 24 * 60 * 60

BROWSER_DIAGNOSTIC_SCREENSHOT_POLICY: Final = LocalArtifactPolicy(
    relative_directory="artifacts/diagnostics/screenshots",
    file_extension="png",
    media_type="image/png",
    maximum_bytes=MAX_BROWSER_DIAGNOSTIC_SCREENSHOT_BYTES,
    maximum_artifacts=MAX_BROWSER_DIAGNOSTIC_ARTIFACTS,
    retention_seconds=BROWSER_DIAGNOSTIC_RETENTION_SECONDS,
    minimum_free_bytes=DEFAULT_LOCAL_ARTIFACT_MINIMUM_FREE_BYTES,
)
BROWSER_DIAGNOSTIC_TRACE_POLICY: Final = LocalArtifactPolicy(
    relative_directory="artifacts/diagnostics/traces",
    file_extension="json",
    media_type="application/vnd.automation-tool.browser-diagnostic-trace+json",
    maximum_bytes=MAX_BROWSER_DIAGNOSTIC_TRACE_BYTES,
    maximum_artifacts=MAX_BROWSER_DIAGNOSTIC_ARTIFACTS,
    retention_seconds=BROWSER_DIAGNOSTIC_RETENTION_SECONDS,
    minimum_free_bytes=DEFAULT_LOCAL_ARTIFACT_MINIMUM_FREE_BYTES,
)

_PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
_PNG_SAFE_CRITICAL_CHUNKS: Final = frozenset({b"IHDR", b"PLTE", b"IDAT", b"IEND"})
_MAX_SCREENSHOT_DIMENSION: Final = 4_096
_TRACE_VERSION: Final = "executor.browser-diagnostic-trace.v1"
_REDACTION_VERSION: Final = "browser-skeleton.v1"
_SCREENSHOT_REDACTION_STYLE: Final = """
*, *::before, *::after {
  color: transparent !important;
  text-shadow: none !important;
  caret-color: transparent !important;
  background-color: transparent !important;
  background-image: none !important;
  border-image: none !important;
  box-shadow: none !important;
  filter: none !important;
  mask-image: none !important;
  -webkit-mask-image: none !important;
}
html, body {
  background: #f3f4f6 !important;
  background-image: none !important;
}
img, picture, video, canvas, svg, iframe, object, embed,
input, textarea, select, [contenteditable="true"] {
  visibility: hidden !important;
}
""".strip()


class BrowserDiagnosticArtifactRejected(RuntimeError):
    """A browser diagnostic cannot be retained without weakening its privacy boundary."""

    def __init__(self) -> None:
        super().__init__("browser diagnostic artifact is unavailable")


class BrowserDiagnosticTrigger(StrEnum):
    FAILURE = "failure"
    USER_ENABLED = "user_enabled"


class BrowserDiagnosticStage(StrEnum):
    SEARCH = "search"
    SCROLL = "scroll"
    EXTRACTION = "extraction"


@dataclass(frozen=True, slots=True)
class BrowserDiagnosticCapturePolicy:
    capture_successful_runs: bool

    def __post_init__(self) -> None:
        if type(self.capture_successful_runs) is not bool:
            raise BrowserDiagnosticArtifactRejected

    def trigger(self, *, failed: bool) -> BrowserDiagnosticTrigger | None:
        if type(failed) is not bool:
            raise BrowserDiagnosticArtifactRejected
        if failed:
            return BrowserDiagnosticTrigger.FAILURE
        if self.capture_successful_runs:
            return BrowserDiagnosticTrigger.USER_ENABLED
        return None


@dataclass(frozen=True, slots=True)
class BrowserDiagnosticArtifactBundle:
    screenshot: LocalArtifactRef
    trace: LocalArtifactRef

    def __post_init__(self) -> None:
        if (
            not isinstance(self.screenshot, LocalArtifactRef)
            or self.screenshot.media_type != BROWSER_DIAGNOSTIC_SCREENSHOT_POLICY.media_type
            or self.screenshot.relative_path
            != BROWSER_DIAGNOSTIC_SCREENSHOT_POLICY.relative_path(self.screenshot.artifact_id)
            or not isinstance(self.trace, LocalArtifactRef)
            or self.trace.media_type != BROWSER_DIAGNOSTIC_TRACE_POLICY.media_type
            or self.trace.relative_path
            != BROWSER_DIAGNOSTIC_TRACE_POLICY.relative_path(self.trace.artifact_id)
        ):
            raise BrowserDiagnosticArtifactRejected


@runtime_checkable
class BrowserDiagnosticArtifactClock(Protocol):
    def now(self) -> datetime: ...


class SystemBrowserDiagnosticArtifactClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class _ScreenshotPage(Protocol):
    def screenshot(self, **options: object) -> bytes: ...


class BrowserDiagnosticArtifactStore:
    """Capture only a redacted viewport PNG and fixed-schema structural trace."""

    def __init__(
        self,
        *,
        state_directory: Path,
        clock: BrowserDiagnosticArtifactClock | None = None,
        id_source: Callable[[], object] = uuid4,
    ) -> None:
        initialized = False
        with suppress(Exception):
            resolved_clock = SystemBrowserDiagnosticArtifactClock() if clock is None else clock
            if (
                not isinstance(state_directory, Path)
                or not isinstance(resolved_clock, BrowserDiagnosticArtifactClock)
                or not callable(id_source)
            ):
                raise ValueError
            self._clock = resolved_clock
            self._screenshots = LocalArtifactStore(
                root_directory=state_directory,
                policy=BROWSER_DIAGNOSTIC_SCREENSHOT_POLICY,
                id_source=id_source,
            )
            self._traces = LocalArtifactStore(
                root_directory=state_directory,
                policy=BROWSER_DIAGNOSTIC_TRACE_POLICY,
                id_source=id_source,
            )
            self._govern_artifacts(reserve_trace=False)
            initialized = True
        if not initialized:
            raise BrowserDiagnosticArtifactRejected

    def capture(
        self,
        *,
        window: BrowserWindow,
        trigger: BrowserDiagnosticTrigger,
        stage: BrowserDiagnosticStage,
        page_revision: int,
    ) -> BrowserDiagnosticArtifactBundle:
        result: BrowserDiagnosticArtifactBundle | None = None
        with suppress(Exception):
            if (
                not isinstance(window, BrowserWindow)
                or not isinstance(trigger, BrowserDiagnosticTrigger)
                or not isinstance(stage, BrowserDiagnosticStage)
                or type(page_revision) is not int
                or not 1 <= page_revision <= MAX_CROSS_RUNTIME_SEQUENCE
            ):
                raise ValueError
            protected_screenshots = self._govern_artifacts(reserve_trace=True)
            page = cast(_ScreenshotPage, window.playwright_page)
            raw_screenshot = page.screenshot(
                animations="disabled",
                caret="hide",
                full_page=False,
                omit_background=False,
                scale="css",
                style=_SCREENSHOT_REDACTION_STYLE,
                timeout=SCREENSHOT_TIMEOUT_MILLISECONDS,
                type="png",
            )
            screenshot = self._screenshots.capture(
                _sanitize_png(raw_screenshot),
                protected_references=protected_screenshots,
            )
            captured_at = self._captured_at()

            def trace_payload(artifact_id: UUID) -> bytes:
                document = {
                    "artifact_id": str(artifact_id),
                    "artifact_version": _TRACE_VERSION,
                    "captured_at": captured_at,
                    "operation": "douyin_target_discovery",
                    "page_revision": page_revision,
                    "platform": "douyin",
                    "redaction_version": _REDACTION_VERSION,
                    "screenshot_artifact_id": str(screenshot.artifact_id),
                    "stage": stage.value,
                    "trigger": trigger.value,
                }
                return json.dumps(
                    document,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")

            trace = self._traces.capture_generated(trace_payload)
            result = BrowserDiagnosticArtifactBundle(screenshot=screenshot, trace=trace)
        if result is None:
            raise BrowserDiagnosticArtifactRejected
        return result

    def _govern_artifacts(
        self,
        *,
        reserve_trace: bool,
    ) -> tuple[LocalArtifactRef, ...]:
        if type(reserve_trace) is not bool:
            raise ValueError
        if reserve_trace:
            self._traces.prepare_capture()
        else:
            self._traces.cleanup()
        protected: list[LocalArtifactRef] = []
        protected_ids: set[UUID] = set()
        for trace in self._traces.list_references():
            screenshot_id = _trace_screenshot_id(self._traces.read(trace), trace.artifact_id)
            if screenshot_id in protected_ids:
                raise ValueError
            protected.append(self._screenshots.resolve(screenshot_id))
            protected_ids.add(screenshot_id)
        result = tuple(protected)
        self._screenshots.cleanup(protected_references=result)
        return result

    def _captured_at(self) -> str:
        value = self._clock.now()
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _trace_screenshot_id(source: bytes, trace_artifact_id: UUID) -> UUID:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        document: dict[str, object] = {}
        for key, value in pairs:
            if key in document:
                raise ValueError
            document[key] = value
        return document

    document = json.loads(source, object_pairs_hook=unique_object)
    expected_keys = {
        "artifact_id",
        "artifact_version",
        "captured_at",
        "operation",
        "page_revision",
        "platform",
        "redaction_version",
        "screenshot_artifact_id",
        "stage",
        "trigger",
    }
    if not isinstance(document, dict) or set(document) != expected_keys:
        raise ValueError
    if (
        document["artifact_id"] != str(trace_artifact_id)
        or document["artifact_version"] != _TRACE_VERSION
        or document["operation"] != "douyin_target_discovery"
        or document["platform"] != "douyin"
        or document["redaction_version"] != _REDACTION_VERSION
        or document["stage"] not in {stage.value for stage in BrowserDiagnosticStage}
        or document["trigger"] not in {trigger.value for trigger in BrowserDiagnosticTrigger}
        or type(document["page_revision"]) is not int
        or not 1 <= document["page_revision"] <= MAX_CROSS_RUNTIME_SEQUENCE
        or type(document["captured_at"]) is not str
        or not 1 <= len(document["captured_at"]) <= 32
        or type(document["screenshot_artifact_id"]) is not str
    ):
        raise ValueError
    captured_source = document["captured_at"]
    captured_at = datetime.fromisoformat(captured_source.replace("Z", "+00:00"))
    if (
        not captured_source.endswith("Z")
        or captured_at.isoformat().replace("+00:00", "Z") != captured_source
    ):
        raise ValueError
    screenshot_source = document["screenshot_artifact_id"]
    screenshot_id = UUID(screenshot_source)
    if screenshot_id.version != 4 or str(screenshot_id) != screenshot_source:
        raise ValueError
    return screenshot_id


def _sanitize_png(source: object) -> bytes:
    if (
        type(source) is not bytes
        or not 1 <= len(source) <= MAX_BROWSER_DIAGNOSTIC_SCREENSHOT_BYTES
        or not source.startswith(_PNG_SIGNATURE)
    ):
        raise ValueError
    offset = len(_PNG_SIGNATURE)
    output = bytearray(_PNG_SIGNATURE)
    seen_header = False
    seen_pixels = False
    seen_end = False
    while offset < len(source):
        if seen_end or len(source) - offset < 12:
            raise ValueError
        length = struct.unpack(">I", source[offset : offset + 4])[0]
        chunk_end = offset + 12 + length
        if chunk_end > len(source):
            raise ValueError
        kind = source[offset + 4 : offset + 8]
        payload = source[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", source[offset + 8 + length : chunk_end])[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != expected_crc:
            raise ValueError
        if not seen_header:
            if kind != b"IHDR" or length != 13:
                raise ValueError
            width, height = struct.unpack(">II", payload[:8])
            if (
                not 1 <= width <= _MAX_SCREENSHOT_DIMENSION
                or not 1 <= height <= _MAX_SCREENSHOT_DIMENSION
            ):
                raise ValueError
            seen_header = True
        if kind == b"IDAT":
            seen_pixels = True
        if kind == b"IEND":
            if length != 0:
                raise ValueError
            seen_end = True
        if kind in _PNG_SAFE_CRITICAL_CHUNKS:
            output.extend(source[offset:chunk_end])
        elif kind[:1].isupper():
            raise ValueError
        offset = chunk_end
    if not seen_header or not seen_pixels or not seen_end:
        raise ValueError
    return bytes(output)


__all__ = [
    "BROWSER_DIAGNOSTIC_RETENTION_SECONDS",
    "BROWSER_DIAGNOSTIC_SCREENSHOT_POLICY",
    "BROWSER_DIAGNOSTIC_TRACE_POLICY",
    "MAX_BROWSER_DIAGNOSTIC_ARTIFACTS",
    "MAX_BROWSER_DIAGNOSTIC_SCREENSHOT_BYTES",
    "MAX_BROWSER_DIAGNOSTIC_TRACE_BYTES",
    "SCREENSHOT_TIMEOUT_MILLISECONDS",
    "BrowserDiagnosticArtifactBundle",
    "BrowserDiagnosticArtifactClock",
    "BrowserDiagnosticArtifactRejected",
    "BrowserDiagnosticArtifactStore",
    "BrowserDiagnosticCapturePolicy",
    "BrowserDiagnosticStage",
    "BrowserDiagnosticTrigger",
    "SystemBrowserDiagnosticArtifactClock",
]
