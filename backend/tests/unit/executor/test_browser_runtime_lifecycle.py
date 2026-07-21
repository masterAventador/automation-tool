from __future__ import annotations

import threading
from contextlib import AbstractContextManager
from io import StringIO
from pathlib import Path
from typing import Any, cast

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automation_tool.executor.browser_runtime import (
    BrowserLaunchRequest,
    BrowserRuntime,
    BrowserRuntimeRejected,
    BrowserRuntimeTimedOut,
    BrowserWindow,
)
from automation_tool.executor.diagnostics import ExecutorRecoveryDiagnostics


class FakePage:
    def __init__(self, *, fail_close: bool = False) -> None:
        self.close_calls: list[dict[str, object]] = []
        self.closed = False
        self.fail_close = fail_close

    def close(self, *, reason: str | None = None, run_before_unload: bool = False) -> None:
        self.close_calls.append({"reason": reason, "run_before_unload": run_before_unload})
        if self.fail_close:
            raise RuntimeError("private page close failure")
        self.closed = True


class FakePageInfo:
    def __init__(self, page: FakePage) -> None:
        self.value = page


class FakeExpectedPage(AbstractContextManager[FakePageInfo]):
    def __init__(self, context: FakeContext, *, fail_timeout: bool = False) -> None:
        self.context = context
        self.fail_timeout = fail_timeout
        self.page = FakePage()

    def __enter__(self) -> FakePageInfo:
        return FakePageInfo(self.page)

    def __exit__(self, *_exception: object) -> None:
        if self.fail_timeout:
            raise PlaywrightTimeoutError("private timeout")
        self.context.pages.append(self.page)


class FakeContext:
    def __init__(
        self,
        *,
        fail_wait: bool = False,
        fail_close: bool = False,
        fail_timeout_configuration: bool = False,
        fail_new_page: bool = False,
    ) -> None:
        self.pages: list[FakePage] = [FakePage()]
        self.default_timeout: float | None = None
        self.navigation_timeout: float | None = None
        self.expect_timeouts: list[float] = []
        self.close_calls = 0
        self.fail_wait = fail_wait
        self.fail_close = fail_close
        self.fail_timeout_configuration = fail_timeout_configuration
        self.fail_new_page = fail_new_page

    def set_default_timeout(self, timeout: float) -> None:
        if self.fail_timeout_configuration:
            raise RuntimeError("private timeout configuration failure")
        self.default_timeout = timeout

    def set_default_navigation_timeout(self, timeout: float) -> None:
        self.navigation_timeout = timeout

    def new_page(self) -> FakePage:
        if self.fail_new_page:
            raise RuntimeError("private new page failure")
        page = FakePage()
        self.pages.append(page)
        return page

    def expect_page(self, *, timeout: float) -> FakeExpectedPage:
        self.expect_timeouts.append(timeout)
        return FakeExpectedPage(self, fail_timeout=self.fail_wait)

    def close(self, *, reason: str | None = None) -> None:
        self.close_calls += 1
        if self.fail_close:
            raise RuntimeError("private close failure")


class FakeChromium:
    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.calls: list[tuple[Path, dict[str, object]]] = []

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
            (
                Path(user_data_dir),
                {
                    "accept_downloads": accept_downloads,
                    "executable_path": executable_path,
                    "headless": headless,
                    "timeout": timeout,
                },
            )
        )
        return self.context


class FakePlaywright:
    def __init__(self, context: FakeContext, *, fail_stop: bool = False) -> None:
        self.chromium: Any = FakeChromium(context)
        self.stop_calls = 0
        self.fail_stop = fail_stop

    def stop(self) -> None:
        self.stop_calls += 1
        if self.fail_stop:
            raise RuntimeError("private driver failure")


def launch_paths(tmp_path: Path) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    executable = tmp_path / "trusted-browser"
    executable.write_bytes(b"browser")
    executable.chmod(0o700)
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    return executable, profile


def request(tmp_path: Path) -> BrowserLaunchRequest:
    executable, profile = launch_paths(tmp_path)
    return BrowserLaunchRequest(executable_path=executable, profile_directory=profile)


def test_runtime_owns_one_headed_context_and_applies_bounded_timeouts(tmp_path: Path) -> None:
    context = FakeContext()
    playwright = FakePlaywright(context)
    runtime = BrowserRuntime(starter=lambda: playwright)

    runtime.start(request(tmp_path))

    assert runtime.is_running
    assert playwright.chromium.calls[0][1]["headless"] is False
    assert repr(runtime) == "BrowserRuntime(<redacted>)"
    assert context.default_timeout == 15_000
    assert context.navigation_timeout == 30_000
    assert isinstance(runtime.primary_window(), BrowserWindow)
    assert repr(runtime.primary_window()) == "BrowserWindow(<redacted>)"
    assert len(runtime.windows()) == 1
    with pytest.raises(BrowserRuntimeRejected):
        runtime.start(cast(Any, object()))

    runtime.close()
    runtime.close()
    assert not runtime.is_running
    assert context.close_calls == 1
    assert playwright.stop_calls == 1


def test_runtime_honors_only_the_rust_authorized_background_launch_flag(
    tmp_path: Path,
) -> None:
    executable, profile = launch_paths(tmp_path)
    playwright = FakePlaywright(FakeContext())
    runtime = BrowserRuntime(starter=lambda: playwright)

    runtime.start(
        BrowserLaunchRequest(
            executable_path=executable,
            profile_directory=profile,
            headless=True,
        )
    )

    assert playwright.chromium.calls[0][1]["headless"] is True
    runtime.close()

    restarted_context = FakeContext()
    restarted = FakePlaywright(restarted_context)
    runtime._starter = lambda: restarted
    runtime.start(request(tmp_path / "restart"))
    runtime.close()


def test_runtime_opens_captures_and_closes_only_its_own_windows(tmp_path: Path) -> None:
    context = FakeContext()
    runtime = BrowserRuntime(starter=lambda: FakePlaywright(context))
    runtime.start(request(tmp_path))

    opened = runtime.open_window()
    captured = runtime.capture_window(lambda: None, timeout_milliseconds=1_250)
    assert len(runtime.windows()) == 3
    assert context.expect_timeouts == [1_250]
    runtime.close_window(opened)
    assert cast(FakePage, opened.playwright_page).close_calls == [
        {"reason": "automation-tool.window-close", "run_before_unload": False}
    ]

    foreign = BrowserWindow._for_runtime(object(), FakePage())
    with pytest.raises(BrowserRuntimeRejected):
        runtime.close_window(foreign)
    runtime.close_window(captured)
    runtime.close()


def test_window_timeout_and_invalid_timeout_are_distinct_fixed_failures(tmp_path: Path) -> None:
    context = FakeContext(fail_wait=True)
    runtime = BrowserRuntime(starter=lambda: FakePlaywright(context))
    runtime.start(request(tmp_path))

    with pytest.raises(
        BrowserRuntimeTimedOut,
        match=r"^browser runtime operation timed out$",
    ):
        runtime.capture_window(lambda: None, timeout_milliseconds=250)
    for invalid in (0, 60_001, 1.5, True):
        with pytest.raises(BrowserRuntimeRejected):
            runtime.capture_window(lambda: None, timeout_milliseconds=cast(Any, invalid))
    with pytest.raises(BrowserRuntimeRejected):
        runtime.capture_window(cast(Any, None), timeout_milliseconds=250)
    context.fail_wait = False
    with pytest.raises(BrowserRuntimeTimedOut):
        runtime.capture_window(
            lambda: (_ for _ in ()).throw(BrowserRuntimeTimedOut()),
            timeout_milliseconds=250,
        )
    with pytest.raises(BrowserRuntimeRejected):
        runtime.capture_window(
            lambda: (_ for _ in ()).throw(RuntimeError("private trigger failure")),
            timeout_milliseconds=250,
        )
    runtime.close()


def test_runtime_is_thread_confined_and_rejects_use_after_close(tmp_path: Path) -> None:
    runtime = BrowserRuntime(starter=lambda: FakePlaywright(FakeContext()))
    runtime.start(request(tmp_path))
    failures: list[Exception] = []

    def use_from_foreign_thread() -> None:
        try:
            runtime.windows()
        except Exception as error:
            failures.append(error)

    thread = threading.Thread(target=use_from_foreign_thread)
    thread.start()
    thread.join()
    assert len(failures) == 1
    assert isinstance(failures[0], BrowserRuntimeRejected)

    runtime.close()
    with pytest.raises(BrowserRuntimeRejected):
        runtime.primary_window()
    with pytest.raises(BrowserRuntimeRejected):
        runtime.open_window()


def test_close_failure_still_stops_driver_and_allows_no_further_use(tmp_path: Path) -> None:
    context = FakeContext(fail_close=True)
    playwright = FakePlaywright(context, fail_stop=True)
    runtime = BrowserRuntime(starter=lambda: playwright)
    runtime.start(request(tmp_path))

    with pytest.raises(
        BrowserRuntimeRejected,
        match=r"^packaged browser runtime is unavailable$",
    ):
        runtime.close()

    assert not runtime.is_running
    assert context.close_calls == 1
    assert playwright.stop_calls == 1
    runtime.close()


def test_context_manager_closes_runtime_after_trigger_failure(tmp_path: Path) -> None:
    context = FakeContext()
    playwright = FakePlaywright(context)
    runtime = BrowserRuntime(starter=lambda: playwright)

    with pytest.raises(RuntimeError, match="trigger failed"), runtime.running(request(tmp_path)):
        raise RuntimeError("trigger failed")

    assert not runtime.is_running
    assert context.close_calls == 1
    assert playwright.stop_calls == 1


def test_start_and_window_failure_matrix_is_fixed_and_cleans_partial_resources(
    tmp_path: Path,
) -> None:
    with pytest.raises(BrowserRuntimeRejected):
        BrowserRuntime(starter=cast(Any, None))
    runtime = BrowserRuntime(starter=lambda: (_ for _ in ()).throw(RuntimeError("private")))
    with pytest.raises(BrowserRuntimeRejected):
        runtime.start(request(tmp_path / "starter"))

    failed_context = FakeContext(
        fail_close=True,
        fail_timeout_configuration=True,
    )
    failed_playwright = FakePlaywright(failed_context, fail_stop=True)
    runtime = BrowserRuntime(starter=lambda: failed_playwright)
    with pytest.raises(BrowserRuntimeRejected):
        runtime.start(request(tmp_path / "configuration"))
    assert failed_context.close_calls == 1
    assert failed_playwright.stop_calls == 1

    context = FakeContext()
    runtime = BrowserRuntime(starter=lambda: FakePlaywright(context))
    runtime.start(request(tmp_path / "windows"))
    context.pages = cast(Any, None)
    with pytest.raises(BrowserRuntimeRejected):
        runtime.windows()
    with pytest.raises(BrowserRuntimeRejected):
        runtime.primary_window()
    context.pages = []
    assert isinstance(runtime.primary_window(), BrowserWindow)
    context.fail_new_page = True
    with pytest.raises(BrowserRuntimeRejected):
        runtime.open_window()
    runtime.close()


def test_unavailable_window_and_later_runtime_recovery_emit_only_fixed_diagnostics(
    tmp_path: Path,
) -> None:
    output = StringIO()
    diagnostics = ExecutorRecoveryDiagnostics(output)
    first_context = FakeContext()
    runtime = BrowserRuntime(
        starter=lambda: FakePlaywright(first_context),
        diagnostics=diagnostics,
    )
    runtime.start(request(tmp_path / "first"))
    first_context.pages = cast(Any, None)

    with pytest.raises(BrowserRuntimeRejected):
        runtime.primary_window()

    first_context.pages = []
    runtime.close()
    recovered = BrowserRuntime(
        starter=lambda: FakePlaywright(FakeContext()),
        diagnostics=diagnostics,
    )
    recovered.start(request(tmp_path / "recovered"))
    recovered.close()

    assert output.getvalue().splitlines() == [
        "executor.recovery browser_window_unavailable",
        "executor.recovery browser_window_recovered",
    ]


def test_start_revalidates_browser_and_profile_paths_immediately_before_launch(
    tmp_path: Path,
) -> None:
    executable, profile = launch_paths(tmp_path)
    launch = BrowserLaunchRequest(
        executable_path=executable,
        profile_directory=profile,
    )
    executable.unlink()
    executable.mkdir()
    runtime = BrowserRuntime(starter=lambda: FakePlaywright(FakeContext()))
    with pytest.raises(BrowserRuntimeRejected):
        runtime.start(launch)

    executable.rmdir()
    executable.write_bytes(b"browser")
    executable.chmod(0o700)
    launch = BrowserLaunchRequest(
        executable_path=executable,
        profile_directory=profile,
    )
    profile.rmdir()
    profile.write_bytes(b"not a profile")
    with pytest.raises(BrowserRuntimeRejected):
        runtime.start(launch)


def test_close_window_rejects_stale_and_failed_page_close(tmp_path: Path) -> None:
    context = FakeContext()
    runtime = BrowserRuntime(starter=lambda: FakePlaywright(context))
    runtime.start(request(tmp_path))
    stale = runtime.open_window()
    context.pages.remove(cast(FakePage, stale.playwright_page))
    with pytest.raises(BrowserRuntimeRejected):
        runtime.close_window(stale)

    failed_page = FakePage(fail_close=True)
    context.pages.append(failed_page)
    with pytest.raises(BrowserRuntimeRejected):
        runtime.close_window(BrowserWindow._for_runtime(runtime._owner, failed_page))
    runtime.close()
