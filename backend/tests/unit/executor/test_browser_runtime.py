from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import pytest

from automation_tool.executor.browser_runtime import (
    BrowserLaunchRequest,
    BrowserRuntimeRejected,
    PackagedBrowserRuntime,
)


class FakeBrowserContext:
    def __init__(self, *, fail_close: bool = False) -> None:
        self.close_calls = 0
        self.fail_close = fail_close

    def close(self) -> None:
        self.close_calls += 1
        if self.fail_close:
            raise RuntimeError("private close failure")


class FakeChromium:
    def __init__(
        self,
        context: FakeBrowserContext,
        *,
        fail_launch: bool = False,
    ) -> None:
        self.calls: list[tuple[Path, dict[str, object]]] = []
        self.context = context
        self.fail_launch = fail_launch

    def launch_persistent_context(
        self,
        user_data_dir: str | Path,
        *,
        accept_downloads: bool,
        executable_path: str | Path,
        headless: bool,
        timeout: float,
    ) -> FakeBrowserContext:
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
        if self.fail_launch:
            raise RuntimeError("private launch failure")
        return self.context


class FakePlaywright:
    def __init__(self, chromium: FakeChromium, *, fail_stop: bool = False) -> None:
        self.chromium = chromium
        self.stop_calls = 0
        self.fail_stop = fail_stop

    def stop(self) -> None:
        self.stop_calls += 1
        if self.fail_stop:
            raise RuntimeError("private stop failure")


def private_launch_paths(tmp_path: Path) -> tuple[Path, Path]:
    executable = tmp_path / "trusted-browser"
    executable.write_bytes(b"browser")
    executable.chmod(0o700)
    profile = tmp_path / "private-profile"
    profile.mkdir(mode=0o700)
    return executable, profile


def test_runtime_launches_only_an_explicit_headed_persistent_context(tmp_path: Path) -> None:
    executable, profile = private_launch_paths(tmp_path)
    context = FakeBrowserContext()
    chromium = FakeChromium(context)
    playwright = FakePlaywright(chromium)
    request = BrowserLaunchRequest(
        executable_path=executable,
        profile_directory=profile,
    )

    assert os.fspath(executable) not in repr(request)
    assert os.fspath(profile) not in repr(request)
    lease = PackagedBrowserRuntime(starter=lambda: playwright).open(request)
    assert os.fspath(executable) not in repr(lease)
    assert os.fspath(profile) not in repr(lease)
    assert chromium.calls == [
        (
            profile,
            {
                "accept_downloads": False,
                "executable_path": executable,
                "headless": False,
                "timeout": 30_000,
            },
        )
    ]

    lease.close()
    lease.close()
    assert context.close_calls == 1
    assert playwright.stop_calls == 1


def test_request_rejects_untrusted_path_shapes_before_starting_playwright(tmp_path: Path) -> None:
    executable, profile = private_launch_paths(tmp_path)
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    executable_link = tmp_path / "browser-link"
    profile_link = tmp_path / "profile-link"
    fake = FakePlaywright(FakeChromium(FakeBrowserContext()))
    runtime = PackagedBrowserRuntime(starter=lambda: fake)

    rejected = [
        (Path("relative-browser"), profile),
        (tmp_path / "missing-browser", profile),
        (profile, profile),
        (executable, Path("relative-profile")),
        (executable, tmp_path / "missing-profile"),
        (executable, outside),
    ]
    try:
        executable_link.symlink_to(executable)
        profile_link.symlink_to(profile, target_is_directory=True)
    except OSError:
        pass
    else:
        rejected.extend(((executable_link, profile), (executable, profile_link)))
    for browser_path, profile_path in rejected:
        with pytest.raises(
            BrowserRuntimeRejected,
            match=r"^packaged browser runtime is unavailable$",
        ):
            request = BrowserLaunchRequest(
                executable_path=browser_path,
                profile_directory=profile_path,
            )
            runtime.open(request)

    assert fake.chromium.calls == []
    assert fake.stop_calls == 0


def test_launch_failure_stops_playwright_and_never_reflects_private_paths(tmp_path: Path) -> None:
    executable, profile = private_launch_paths(tmp_path)
    playwright = FakePlaywright(
        FakeChromium(FakeBrowserContext(), fail_launch=True),
        fail_stop=True,
    )
    runtime = PackagedBrowserRuntime(starter=lambda: playwright)

    with pytest.raises(BrowserRuntimeRejected) as captured:
        runtime.open(
            BrowserLaunchRequest(
                executable_path=executable,
                profile_directory=profile,
            )
        )

    assert str(captured.value) == "packaged browser runtime is unavailable"
    assert os.fspath(executable) not in repr(captured.value)
    assert os.fspath(profile) not in repr(captured.value)
    assert playwright.stop_calls == 1


def test_invalid_starter_request_and_starter_failure_are_fixed_errors(tmp_path: Path) -> None:
    executable, profile = private_launch_paths(tmp_path)
    request = BrowserLaunchRequest(
        executable_path=executable,
        profile_directory=profile,
    )

    with pytest.raises(BrowserRuntimeRejected):
        PackagedBrowserRuntime(starter=cast(Any, None))
    with pytest.raises(BrowserRuntimeRejected):
        BrowserLaunchRequest(
            executable_path=cast(Any, "not-a-path"),
            profile_directory=profile,
        )
    runtime = PackagedBrowserRuntime(starter=lambda: (_ for _ in ()).throw(RuntimeError("secret")))
    with pytest.raises(BrowserRuntimeRejected):
        runtime.open(request)
    with pytest.raises(BrowserRuntimeRejected):
        PackagedBrowserRuntime(
            starter=lambda: FakePlaywright(FakeChromium(FakeBrowserContext()))
        ).open(cast(Any, object()))


def test_close_failure_still_stops_driver_and_is_a_fixed_safe_error(tmp_path: Path) -> None:
    executable, profile = private_launch_paths(tmp_path)
    context = FakeBrowserContext(fail_close=True)
    playwright = FakePlaywright(FakeChromium(context), fail_stop=True)
    lease = PackagedBrowserRuntime(starter=lambda: playwright).open(
        BrowserLaunchRequest(
            executable_path=executable,
            profile_directory=profile,
        )
    )

    with pytest.raises(BrowserRuntimeRejected) as captured:
        lease.close()

    assert str(captured.value) == "packaged browser runtime is unavailable"
    assert context.close_calls == 1
    assert playwright.stop_calls == 1
    lease.close()


def test_context_manager_closes_context_and_driver(tmp_path: Path) -> None:
    executable, profile = private_launch_paths(tmp_path)
    context = FakeBrowserContext()
    playwright = FakePlaywright(FakeChromium(context))

    with PackagedBrowserRuntime(starter=lambda: playwright).open(
        BrowserLaunchRequest(
            executable_path=executable,
            profile_directory=profile,
        )
    ):
        pass

    assert context.close_calls == 1
    assert playwright.stop_calls == 1
