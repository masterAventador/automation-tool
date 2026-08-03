"""真探针: 包内 Chromium 里量标记槽——一次会话、顺序加载、末帧读数。

判据与 PC-17 的预算探针同源: 先 seek 到零件自己时间轴的末帧再量(载入瞬间量到的是
动画飞行中的一帧, 量出来的预算描述的是没人看见的画面); 读数按 data-motion-slot
标记找槽。这里只验编排: 真正的像素读数由 SLOT_PROBE_JS 在浏览器里产生。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import cast

import pytest

from automation_tool.executor.browser_runtime import BrowserRuntime, _Playwright
from automation_tool.executor.motion_authoring.slot_overflow_probe import (
    SLOT_PROBE_JS,
    ProbeReading,
)
from automation_tool.executor.motion_authoring.slot_probe_browser import (
    SLOT_PROBE_PROFILE_DIRECTORY,
    PackagedSlotProbe,
)


class FakePage:
    def __init__(self, readings: list[dict[str, list[int]]]) -> None:
        self._readings = list(readings)
        self.navigations: list[str] = []
        self.evaluations: list[str] = []
        self.fail_evaluate = False

    def goto(self, url: str) -> None:
        self.navigations.append(url)

    def wait_for_timeout(self, _milliseconds: float) -> None:
        return None

    def evaluate(self, script: str) -> object:
        if self.fail_evaluate:
            raise RuntimeError("private page failure")
        self.evaluations.append(script)
        if "scrollWidth" in script:
            return {
                "slots": self._readings[len(self.navigations) - 1],
                "stage": [1920, 1080],
            }
        return None

    def title(self) -> str:
        return "probe"

    def close(self, **_keywords: object) -> None:
        return None


class FakeContext:
    def __init__(self, page: FakePage) -> None:
        self.pages = [page]
        self.close_calls = 0

    def on(self, _event: str, _handler: object) -> None:
        return None

    def set_default_timeout(self, _timeout: float) -> None:
        return None

    def set_default_navigation_timeout(self, _timeout: float) -> None:
        return None

    def new_page(self) -> FakePage:
        return self.pages[0]

    def close(self, *, reason: str | None = None) -> None:
        self.close_calls += 1


class FakeChromium:
    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.calls: list[dict[str, object]] = []

    def launch_persistent_context(
        self,
        user_data_dir: str | Path,
        *,
        accept_downloads: bool,
        executable_path: str | Path,
        headless: bool,
        timeout: float,
    ) -> FakeContext:
        self.calls.append(
            {
                "user_data_dir": Path(user_data_dir),
                "executable_path": Path(executable_path),
                "headless": headless,
            }
        )
        return self.context


class FakePlaywright:
    def __init__(self, chromium: FakeChromium) -> None:
        self.chromium = chromium
        self.stop_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1


def _fixture(
    tmp_path: Path, readings: list[dict[str, list[int]]]
) -> tuple[PackagedSlotProbe, FakePage, FakeContext, FakeChromium, FakePlaywright, Path]:
    executable = tmp_path / "chromium"
    executable.write_bytes(b"browser")
    executable.chmod(0o700)
    workspace = tmp_path / "job"
    workspace.mkdir()
    page = FakePage(readings)
    context = FakeContext(page)
    chromium = FakeChromium(context)
    playwright = FakePlaywright(chromium)
    probe = PackagedSlotProbe(
        browser_executable=executable,
        profile_directory=workspace / SLOT_PROBE_PROFILE_DIRECTORY,
        runtime=BrowserRuntime(lambda: cast(_Playwright, playwright)),
    )
    return probe, page, context, chromium, playwright, workspace


def _documents(tmp_path: Path, count: int) -> tuple[Path, ...]:
    documents = []
    for position in range(count):
        document = tmp_path / f"document-{position}.html"
        document.write_text("<html></html>", encoding="utf-8")
        documents.append(document)
    return tuple(documents)


def test_every_document_is_measured_by_one_headless_browser_session(
    tmp_path: Path,
) -> None:
    reading = {"12": [205, 347, 35, 32]}
    probe, page, context, chromium, playwright, workspace = _fixture(tmp_path, [reading, reading])
    first, second = _documents(tmp_path, 2)

    results = probe((first, second))

    # 一次启动、无头、私有 Profile 建在工作区下——不是每份文档一个浏览器。
    assert len(chromium.calls) == 1
    assert chromium.calls[0]["headless"] is True
    assert (
        chromium.calls[0]["user_data_dir"] == (workspace / SLOT_PROBE_PROFILE_DIRECTORY).resolve()
    )
    assert page.navigations == [first.resolve().as_uri(), second.resolve().as_uri()]
    expected = ProbeReading(slots={12: (205, 347, 35, 32)}, stage=(1920, 1080))
    assert results == [expected, expected]
    # 会话收尾: 上下文与驱动都停了。
    assert context.close_calls == 1
    assert playwright.stop_calls == 1


def test_the_timeline_is_sought_to_its_end_before_each_reading(tmp_path: Path) -> None:
    reading = {"12": [205, 347, 32, 32]}
    probe, page, *_rest = _fixture(tmp_path, [reading])
    (document,) = _documents(tmp_path, 1)

    probe((document,))

    # 每份文档两次 evaluate: 先 seek 后量, 顺序不可颠倒。
    assert len(page.evaluations) == 2
    assert "seek" in page.evaluations[0]
    assert page.evaluations[1] == SLOT_PROBE_JS


def test_readings_are_normalized_to_int_keys_pixel_tuples_and_a_stage(tmp_path: Path) -> None:
    probe, *_rest = _fixture(tmp_path, [{"12": [400, 347, 32, 32], "15": [205, 347, 32, 32]}])
    (document,) = _documents(tmp_path, 1)

    (result,) = probe((document,))

    assert result == ProbeReading(
        slots={12: (400, 347, 32, 32), 15: (205, 347, 32, 32)}, stage=(1920, 1080)
    )


def test_a_failing_page_still_stops_the_browser_and_driver(tmp_path: Path) -> None:
    probe, page, context, _chromium, playwright, _workspace = _fixture(
        tmp_path, [{"12": [205, 347, 32, 32]}]
    )
    page.fail_evaluate = True
    (document,) = _documents(tmp_path, 1)

    with pytest.raises(RuntimeError, match="private page failure"):
        probe((document,))

    assert context.close_calls == 1
    assert playwright.stop_calls == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows extended-path boundary")
def test_windows_verbatim_paths_are_native_before_playwright_receives_them(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "chromium.exe"
    executable.write_bytes(b"browser")
    workspace = tmp_path / "job"
    workspace.mkdir()
    document = tmp_path / "document.html"
    document.write_text("<html></html>", encoding="utf-8")
    page = FakePage([{"12": [205, 347, 35, 32]}])
    context = FakeContext(page)
    chromium = FakeChromium(context)
    playwright = FakePlaywright(chromium)

    def verbatim(path: Path) -> Path:
        return Path(rf"\\?\{path.resolve()}")

    probe = PackagedSlotProbe(
        browser_executable=verbatim(executable),
        profile_directory=verbatim(workspace / SLOT_PROBE_PROFILE_DIRECTORY),
        runtime=BrowserRuntime(lambda: cast(_Playwright, playwright)),
    )

    probe((verbatim(document),))

    assert chromium.calls == [
        {
            "user_data_dir": (workspace / SLOT_PROBE_PROFILE_DIRECTORY).resolve(),
            "executable_path": executable.resolve(),
            "headless": True,
        }
    ]
    assert page.navigations == [document.resolve().as_uri()]


def test_the_probe_never_prints_the_browser_or_profile_it_owns(tmp_path: Path) -> None:
    executable = tmp_path / "chromium"
    executable.write_bytes(b"browser")

    probe = PackagedSlotProbe(
        browser_executable=executable,
        profile_directory=tmp_path / SLOT_PROBE_PROFILE_DIRECTORY,
        runtime=BrowserRuntime(lambda: cast(_Playwright, object())),
    )

    assert repr(probe) == "PackagedSlotProbe(<redacted>)"
    assert str(executable) not in repr(probe)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (r"\\?\C:\Program Files\chromium\chrome.exe", r"C:\Program Files\chromium\chrome.exe"),
        (r"\\?\UNC\server\share\chromium\chrome.exe", r"\\server\share\chromium\chrome.exe"),
        (r"C:\already\native\chrome.exe", r"C:\already\native\chrome.exe"),
    ],
)
def test_the_windows_device_prefix_is_removed_before_playwright_sees_it(
    given: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Playwright rejects the verbatim form, so it is stripped on the way out.

    The three shapes are stripped differently -- a plain verbatim path loses four
    characters, a UNC one loses eight and gains back the two leading slashes, and
    a path that was never prefixed is handed over untouched. Driving this on a
    POSIX host means supplying the platform value rather than the platform: the
    function reads `os.name` and does pure string work, so the string work is
    what gets asserted. Real Windows behaviour stays with the Windows runner.
    """
    from automation_tool.executor.motion_authoring import slot_probe_browser

    monkeypatch.setattr(os, "name", "nt")
    # Built outside the patch: under `os.name == "nt"` `Path()` constructs a
    # WindowsPath, which parses these strings by different rules than the
    # PosixPath the assertion compares against.
    result = slot_probe_browser._native_path_for_playwright(cast(Path, _RawPath(given)))

    assert os.fspath(result) == expected


class _RawPath:
    """Something `os.fspath` reads as the exact string given, on any platform."""

    def __init__(self, value: str) -> None:
        self._value = value

    def __fspath__(self) -> str:
        return self._value
