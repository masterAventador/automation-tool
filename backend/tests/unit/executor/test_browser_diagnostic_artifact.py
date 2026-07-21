from __future__ import annotations

import json
import struct
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

from automation_tool.executor import browser_diagnostic_artifact as diagnostic_module
from automation_tool.executor.browser_diagnostic_artifact import (
    BROWSER_DIAGNOSTIC_SCREENSHOT_POLICY,
    BROWSER_DIAGNOSTIC_TRACE_POLICY,
    MAX_BROWSER_DIAGNOSTIC_ARTIFACTS,
    MAX_BROWSER_DIAGNOSTIC_SCREENSHOT_BYTES,
    SCREENSHOT_TIMEOUT_MILLISECONDS,
    BrowserDiagnosticArtifactBundle,
    BrowserDiagnosticArtifactRejected,
    BrowserDiagnosticArtifactStore,
    BrowserDiagnosticCapturePolicy,
    BrowserDiagnosticStage,
    BrowserDiagnosticTrigger,
    SystemBrowserDiagnosticArtifactClock,
)
from automation_tool.executor.browser_runtime import BrowserWindow
from automation_tool.executor.local_artifact import LocalArtifactRef, LocalArtifactStore

NOW = datetime(2026, 7, 21, 16, 30, tzinfo=UTC)


class Clock:
    @staticmethod
    def now() -> datetime:
        return NOW


class Ids:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> UUID:
        self.value += 1
        return UUID(f"923e4567-e89b-42d3-a456-{self.value:012d}")


def png(*, private_metadata: bytes = b"private page text") -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"

    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixels = zlib.compress(b"\x00\x00\x00\x00")
    return b"".join(
        (
            signature,
            chunk(b"IHDR", header),
            chunk(b"tEXt", private_metadata),
            chunk(b"IDAT", pixels),
            chunk(b"IEND", b""),
        )
    )


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


class Page:
    def __init__(self, payload: bytes | Exception) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def screenshot(self, **options: object) -> bytes:
        self.calls.append(options)
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def window(page: Page) -> BrowserWindow:
    return BrowserWindow(object(), cast(Any, page))


def state(tmp_path: Path) -> Path:
    directory = tmp_path / "state"
    directory.mkdir(mode=0o700, parents=True)
    return directory


def test_capture_policy_allows_only_failures_or_explicit_success_capture() -> None:
    failures_only = BrowserDiagnosticCapturePolicy(capture_successful_runs=False)
    enabled = BrowserDiagnosticCapturePolicy(capture_successful_runs=True)

    assert failures_only.trigger(failed=True) is BrowserDiagnosticTrigger.FAILURE
    assert failures_only.trigger(failed=False) is None
    assert enabled.trigger(failed=True) is BrowserDiagnosticTrigger.FAILURE
    assert enabled.trigger(failed=False) is BrowserDiagnosticTrigger.USER_ENABLED

    with pytest.raises(BrowserDiagnosticArtifactRejected):
        BrowserDiagnosticCapturePolicy(capture_successful_runs=cast(bool, 1))
    with pytest.raises(BrowserDiagnosticArtifactRejected):
        failures_only.trigger(failed=cast(bool, 0))


def test_capture_stores_a_metadata_free_redacted_png_and_fixed_trace(tmp_path: Path) -> None:
    root = state(tmp_path)
    page = Page(png())
    artifacts = BrowserDiagnosticArtifactStore(
        state_directory=root,
        clock=Clock(),
        id_source=Ids(),
    )

    bundle = artifacts.capture(
        window=window(page),
        trigger=BrowserDiagnosticTrigger.FAILURE,
        stage=BrowserDiagnosticStage.SEARCH,
        page_revision=7,
    )

    assert len(page.calls) == 1
    assert page.calls[0] == {
        "animations": "disabled",
        "caret": "hide",
        "full_page": False,
        "omit_background": False,
        "scale": "css",
        "style": cast(str, page.calls[0]["style"]),
        "timeout": SCREENSHOT_TIMEOUT_MILLISECONDS,
        "type": "png",
    }
    redaction_style = cast(str, page.calls[0]["style"])
    for required in (
        "color: transparent",
        "background-image: none",
        "iframe",
        "visibility: hidden",
    ):
        assert required in redaction_style

    screenshot_store = LocalArtifactStore(
        root_directory=root,
        policy=BROWSER_DIAGNOSTIC_SCREENSHOT_POLICY,
    )
    screenshot = screenshot_store.read(bundle.screenshot)
    assert screenshot.startswith(b"\x89PNG\r\n\x1a\n")
    assert b"private page text" not in screenshot
    assert b"tEXt" not in screenshot
    assert len(screenshot) <= MAX_BROWSER_DIAGNOSTIC_SCREENSHOT_BYTES

    trace_store = LocalArtifactStore(
        root_directory=root,
        policy=BROWSER_DIAGNOSTIC_TRACE_POLICY,
    )
    trace = json.loads(trace_store.read(bundle.trace))
    assert trace == {
        "artifact_id": str(bundle.trace.artifact_id),
        "artifact_version": "executor.browser-diagnostic-trace.v1",
        "captured_at": "2026-07-21T16:30:00Z",
        "operation": "douyin_target_discovery",
        "page_revision": 7,
        "platform": "douyin",
        "redaction_version": "browser-skeleton.v1",
        "screenshot_artifact_id": str(bundle.screenshot.artifact_id),
        "stage": "search",
        "trigger": "failure",
    }


def test_capture_limits_count_size_time_and_untrusted_callers(tmp_path: Path) -> None:
    root = state(tmp_path)
    artifacts = BrowserDiagnosticArtifactStore(
        state_directory=root,
        clock=Clock(),
        id_source=Ids(),
    )
    page = Page(png())
    for revision in range(1, MAX_BROWSER_DIAGNOSTIC_ARTIFACTS + 1):
        artifacts.capture(
            window=window(page),
            trigger=BrowserDiagnosticTrigger.USER_ENABLED,
            stage=BrowserDiagnosticStage.EXTRACTION,
            page_revision=revision,
        )
    with pytest.raises(BrowserDiagnosticArtifactRejected):
        artifacts.capture(
            window=window(page),
            trigger=BrowserDiagnosticTrigger.FAILURE,
            stage=BrowserDiagnosticStage.SEARCH,
            page_revision=MAX_BROWSER_DIAGNOSTIC_ARTIFACTS + 1,
        )

    for invalid in (
        {"window": object()},
        {"trigger": "failure"},
        {"stage": "search"},
        {"page_revision": True},
        {"page_revision": 0},
    ):
        arguments: dict[str, object] = {
            "window": window(page),
            "trigger": BrowserDiagnosticTrigger.FAILURE,
            "stage": BrowserDiagnosticStage.SEARCH,
            "page_revision": 7,
        }
        arguments.update(invalid)
        with pytest.raises(BrowserDiagnosticArtifactRejected):
            artifacts.capture(**cast(Any, arguments))

    oversized_root = state(tmp_path / "oversized")
    oversized = BrowserDiagnosticArtifactStore(
        state_directory=oversized_root,
        clock=Clock(),
        id_source=Ids(),
    )
    with pytest.raises(BrowserDiagnosticArtifactRejected):
        oversized.capture(
            window=window(Page(b"x" * (MAX_BROWSER_DIAGNOSTIC_SCREENSHOT_BYTES + 1))),
            trigger=BrowserDiagnosticTrigger.FAILURE,
            stage=BrowserDiagnosticStage.SEARCH,
            page_revision=7,
        )

    failing_root = state(tmp_path / "failing")
    failing = BrowserDiagnosticArtifactStore(
        state_directory=failing_root,
        clock=Clock(),
        id_source=Ids(),
    )
    with pytest.raises(BrowserDiagnosticArtifactRejected):
        failing.capture(
            window=window(Page(RuntimeError("private browser failure"))),
            trigger=BrowserDiagnosticTrigger.FAILURE,
            stage=BrowserDiagnosticStage.SEARCH,
            page_revision=7,
        )


def test_store_and_bundle_reject_malformed_dependencies_without_reflection(tmp_path: Path) -> None:
    root = state(tmp_path)
    for arguments in (
        {"state_directory": cast(Path, "private")},
        {"state_directory": root, "clock": cast(Any, object())},
        {"state_directory": root, "id_source": cast(Any, None)},
    ):
        with pytest.raises(
            BrowserDiagnosticArtifactRejected,
            match=r"^browser diagnostic artifact is unavailable$",
        ):
            BrowserDiagnosticArtifactStore(**arguments)

    assert SystemBrowserDiagnosticArtifactClock().now().utcoffset() is not None
    store = BrowserDiagnosticArtifactStore(state_directory=root, clock=Clock(), id_source=Ids())
    bundle = store.capture(
        window=window(Page(png())),
        trigger=BrowserDiagnosticTrigger.FAILURE,
        stage=BrowserDiagnosticStage.SEARCH,
        page_revision=7,
    )
    bad_media = LocalArtifactRef(
        artifact_id=bundle.screenshot.artifact_id,
        sha256=bundle.screenshot.sha256,
        media_type="image/jpeg",
        size_bytes=bundle.screenshot.size_bytes,
        relative_path=bundle.screenshot.relative_path,
    )
    bad_directory = LocalArtifactRef(
        artifact_id=bundle.screenshot.artifact_id,
        sha256=bundle.screenshot.sha256,
        media_type=bundle.screenshot.media_type,
        size_bytes=bundle.screenshot.size_bytes,
        relative_path=f"private/{bundle.screenshot.artifact_id}.png",
    )
    for screenshot, trace in (
        (cast(LocalArtifactRef, object()), bundle.trace),
        (bad_media, bundle.trace),
        (bad_directory, bundle.trace),
        (bundle.screenshot, cast(LocalArtifactRef, object())),
        (bundle.screenshot, bad_media),
        (bundle.screenshot, bad_directory),
    ):
        with pytest.raises(BrowserDiagnosticArtifactRejected):
            BrowserDiagnosticArtifactBundle(screenshot=screenshot, trace=trace)


def test_capture_rejects_invalid_clock_and_png_structures(tmp_path: Path) -> None:
    class InvalidClock:
        def __init__(self, value: object) -> None:
            self.value = value

        def now(self) -> datetime:
            return cast(datetime, self.value)

    for index, value in enumerate((object(), datetime(2026, 7, 21, 16, 30))):
        root = state(tmp_path / f"clock-{index}")
        store = BrowserDiagnosticArtifactStore(
            state_directory=root,
            clock=InvalidClock(value),
            id_source=Ids(),
        )
        with pytest.raises(BrowserDiagnosticArtifactRejected):
            store.capture(
                window=window(Page(png())),
                trigger=BrowserDiagnosticTrigger.FAILURE,
                stage=BrowserDiagnosticStage.SEARCH,
                page_revision=7,
            )

    signature = b"\x89PNG\r\n\x1a\n"
    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixels = zlib.compress(b"\x00\x00\x00\x00")
    valid_header = png_chunk(b"IHDR", header)
    valid_pixels = png_chunk(b"IDAT", pixels)
    valid_end = png_chunk(b"IEND", b"")
    bad_crc = bytearray(valid_header)
    bad_crc[-1] ^= 1
    malformed = (
        None,
        b"",
        b"x" * (MAX_BROWSER_DIAGNOSTIC_SCREENSHOT_BYTES + 1),
        b"not-png",
        signature + valid_header + valid_pixels + valid_end + b"x",
        signature + b"x",
        signature + struct.pack(">I", 50) + b"IHDR" + b"xxxx",
        signature + bytes(bad_crc) + valid_pixels + valid_end,
        signature + png_chunk(b"IDAT", pixels) + valid_end,
        signature + png_chunk(b"IHDR", header[:-1]) + valid_pixels + valid_end,
        signature
        + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 0, 1, 8, 2, 0, 0, 0))
        + valid_pixels
        + valid_end,
        signature + valid_header + valid_pixels + png_chunk(b"IEND", b"x"),
        signature + valid_header + png_chunk(b"ABCD", b"x") + valid_pixels + valid_end,
        signature + valid_header + valid_end,
        signature + valid_header + valid_pixels,
    )
    for source in malformed:
        with pytest.raises(ValueError):
            diagnostic_module._sanitize_png(source)
