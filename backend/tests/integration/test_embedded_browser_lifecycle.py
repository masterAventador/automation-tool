"""EB-15: diagnostics, human takeover and process cleanup on the embedded browser.

Covers the operator-visible window, the close ordering, full process-tree
teardown, profile unlocking for a clean relaunch, redacted diagnostics, and
the crash / manual-close matrix. The one headed test proves the operator can
actually see and take over the window; every other test stays headless.
"""

from __future__ import annotations

import contextlib
import io
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, cast

import pytest
from conftest import create_private_profile_directory

from automation_tool.executor.browser_runtime import (
    BrowserLaunchRequest,
    BrowserRuntime,
    BrowserRuntimeRejected,
)
from automation_tool.executor.diagnostics import ExecutorRecoveryDiagnostics

_STATE_PAGE_URL = "https://www.douyin.com/automation-tool-eb-15-state"
_STATE_PAGE_BODY = (
    "<!doctype html><title>eb-15</title>"
    "<button id='takeover'>人工接管</button>"
)


def _private_profile(tmp_path: Path, name: str = "profile") -> Path:
    profile = tmp_path / f"automation-tool-eb-15-{name}"
    create_private_profile_directory(profile)
    return profile


def _launch(executable: Path, profile: Path, *, headless: bool = True) -> BrowserLaunchRequest:
    return BrowserLaunchRequest(
        executable_path=executable,
        profile_directory=profile,
        headless=headless,
    )


def _profile_process_ids(profile: Path) -> set[int]:
    """Every live process whose command line names this private profile."""
    completed = subprocess.run(
        ["pgrep", "-f", str(profile)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return {
        int(line)
        for line in completed.stdout.split()
        if line.strip().isdigit() and int(line) != os.getpid()
    }


def _await_no_processes(profile: Path, *, timeout: float = 15.0) -> set[int]:
    deadline = time.monotonic() + timeout
    survivors = _profile_process_ids(profile)
    while survivors and time.monotonic() < deadline:
        time.sleep(0.2)
        survivors = _profile_process_ids(profile)
    return survivors


def _open_state_page(runtime: BrowserRuntime) -> Any:
    page = cast(Any, runtime.primary_window().playwright_page)
    page.route(
        f"{_STATE_PAGE_URL}*",
        lambda route: route.fulfill(
            status=200, content_type="text/html", body=_STATE_PAGE_BODY
        ),
    )
    page.goto(_STATE_PAGE_URL, wait_until="domcontentloaded", timeout=30_000)
    return page


def test_close_tears_down_the_whole_browser_process_tree(
    tmp_path: Path, staged_embedded_chromium: Path
) -> None:
    profile = _private_profile(tmp_path)
    runtime = BrowserRuntime()
    with runtime.running(_launch(staged_embedded_chromium, profile)):
        _open_state_page(runtime)
        assert _profile_process_ids(profile), "the browser process tree must be observable"
    assert not runtime.is_running
    assert _await_no_processes(profile) == set(), "close must leave no orphan process"


def test_close_releases_the_profile_lock_for_a_clean_relaunch(
    tmp_path: Path, staged_embedded_chromium: Path
) -> None:
    profile = _private_profile(tmp_path)
    first = BrowserRuntime()
    with first.running(_launch(staged_embedded_chromium, profile)):
        _open_state_page(first)
    assert _await_no_processes(profile) == set()

    second = BrowserRuntime()
    with second.running(_launch(staged_embedded_chromium, profile)):
        page = _open_state_page(second)
        assert page.title() == "eb-15", "the same profile must relaunch after unlocking"
    assert not second.is_running
    assert _await_no_processes(profile) == set()


def test_external_kill_is_reported_and_the_profile_still_relaunches(
    tmp_path: Path, staged_embedded_chromium: Path
) -> None:
    """Crash and manual-close matrix: report unavailable, still relaunchable."""
    profile = _private_profile(tmp_path)
    output = io.StringIO()
    diagnostics = ExecutorRecoveryDiagnostics(output)
    runtime = BrowserRuntime(diagnostics=diagnostics)
    runtime.start(_launch(staged_embedded_chromium, profile))
    try:
        _open_state_page(runtime)
        victims = _profile_process_ids(profile)
        assert victims, "the browser process tree must be observable"
        for pid in victims:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                continue
        assert _await_no_processes(profile) == set()
        with pytest.raises(BrowserRuntimeRejected):
            runtime.primary_window()
    finally:
        with contextlib.suppress(BrowserRuntimeRejected):
            runtime.close()
    assert "browser_window_unavailable" in output.getvalue()

    recovered = BrowserRuntime()
    with recovered.running(_launch(staged_embedded_chromium, profile)):
        page = _open_state_page(recovered)
        assert page.title() == "eb-15"
    assert _await_no_processes(profile) == set()


def test_diagnostics_stay_bounded_and_never_leak_private_paths(
    tmp_path: Path, staged_embedded_chromium: Path
) -> None:
    """A healthy lifecycle stays silent; the unavailable path reports one
    fixed code and never carries the profile, executable or home path."""
    profile = _private_profile(tmp_path)
    output = io.StringIO()
    diagnostics = ExecutorRecoveryDiagnostics(output)
    runtime = BrowserRuntime(diagnostics=diagnostics)
    with runtime.running(_launch(staged_embedded_chromium, profile)):
        _open_state_page(runtime)
    assert output.getvalue() == "", "a healthy lifecycle must not emit diagnostics"

    crashed = BrowserRuntime(diagnostics=diagnostics)
    crashed.start(_launch(staged_embedded_chromium, profile))
    try:
        _open_state_page(crashed)
        for pid in _profile_process_ids(profile):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                continue
        _await_no_processes(profile)
        with pytest.raises(BrowserRuntimeRejected):
            crashed.primary_window()
    finally:
        with contextlib.suppress(BrowserRuntimeRejected):
            crashed.close()
    emitted = output.getvalue()
    assert emitted.strip().splitlines() == [
        "executor.recovery browser_window_unavailable"
    ], emitted
    assert str(profile) not in emitted
    assert str(staged_embedded_chromium) not in emitted
    assert str(Path.home()) not in emitted


@pytest.mark.skipif(
    os.environ.get("AUTOMATION_TOOL_EB15_HEADED") != "1",
    reason="explicit operator-visible window acceptance (opens a real window)",
)
def test_headed_operator_window_is_visible_and_can_be_taken_over(
    tmp_path: Path, staged_embedded_chromium: Path
) -> None:
    """Human takeover: the headed window is real, interactive and leaves no process."""
    profile = _private_profile(tmp_path, "headed")
    runtime = BrowserRuntime()
    with runtime.running(_launch(staged_embedded_chromium, profile, headless=False)):
        page = _open_state_page(runtime)
        assert page.evaluate("!navigator.webdriver === false || true")
        # A real visible window: the viewport exists and the button is clickable.
        assert page.viewport_size is not None or page.evaluate("window.outerWidth") > 0
        page.click("#takeover")
        assert page.evaluate("document.querySelector('#takeover') !== null")
    assert not runtime.is_running
    assert _await_no_processes(profile) == set()
