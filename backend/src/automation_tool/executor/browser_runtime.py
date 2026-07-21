"""Packaged Playwright primitive for a Rust-authorized system browser and Profile."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from threading import get_ident
from typing import Protocol, Self, cast

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from automation_tool.executor.diagnostics import ExecutorRecoveryDiagnostics

_START_TIMEOUT_MILLISECONDS = 30_000
_ACTION_TIMEOUT_MILLISECONDS = 15_000
_NAVIGATION_TIMEOUT_MILLISECONDS = 30_000
_MAX_OPERATION_TIMEOUT_MILLISECONDS = 60_000
_MAX_PATH_CHARACTERS = 4096


class BrowserRuntimeRejected(RuntimeError):
    """The browser cannot be launched without weakening the local trust boundary."""

    def __init__(self) -> None:
        super().__init__("packaged browser runtime is unavailable")


class BrowserRuntimeTimedOut(TimeoutError):
    """A bounded browser operation did not produce its expected page fact."""

    def __init__(self) -> None:
        super().__init__("browser runtime operation timed out")


@dataclass(frozen=True, slots=True, repr=False)
class BrowserLaunchRequest:
    """Paths already authorized and held stable by the Rust BrowserRuntime caller."""

    executable_path: Path
    profile_directory: Path
    headless: bool = False

    def __post_init__(self) -> None:
        if type(self.headless) is not bool:
            raise BrowserRuntimeRejected
        self.revalidate()

    def revalidate(self) -> None:
        _require_path(self.executable_path, regular_executable=True)
        _require_path(self.profile_directory, regular_executable=False)

    def __repr__(self) -> str:
        return "BrowserLaunchRequest(<redacted>)"


class _BrowserContext(Protocol):
    @property
    def pages(self) -> list[_Page]: ...

    def set_default_timeout(self, timeout: float) -> None: ...

    def set_default_navigation_timeout(self, timeout: float) -> None: ...

    def new_page(self) -> _Page: ...

    def expect_page(self, *, timeout: float) -> _ExpectedPage: ...

    def close(self, *, reason: str | None = None) -> None: ...


class _Page(Protocol):
    def close(self, *, reason: str | None = None, run_before_unload: bool = False) -> None: ...


class _PageInfo(Protocol):
    @property
    def value(self) -> _Page: ...


class _ExpectedPage(Protocol):
    def __enter__(self) -> _PageInfo: ...

    def __exit__(self, *_exception: object) -> None: ...


class _Chromium(Protocol):
    def launch_persistent_context(
        self,
        user_data_dir: str | Path,
        *,
        accept_downloads: bool,
        executable_path: str | Path,
        headless: bool,
        timeout: float,
    ) -> _BrowserContext: ...


class _Playwright(Protocol):
    @property
    def chromium(self) -> _Chromium: ...

    def stop(self) -> None: ...


def _start_playwright() -> _Playwright:  # pragma: no cover - frozen subprocess acceptance
    return cast(_Playwright, sync_playwright().start())


class BrowserWindow:
    """Opaque ownership wrapper around one page/window in the active context."""

    def __init__(self, owner: object, page: _Page) -> None:
        self._owner = owner
        self._page = page

    @classmethod
    def _for_runtime(cls, owner: object, page: _Page) -> BrowserWindow:
        return cls(owner, page)

    @property
    def playwright_page(self) -> _Page:
        """Return the page only to Python platform adapters in this Executor process."""

        return self._page

    def __repr__(self) -> str:
        return "BrowserWindow(<redacted>)"


class BrowserRuntime:
    """Own exactly one thread-confined persistent context and all of its windows."""

    def __init__(
        self,
        starter: Callable[[], _Playwright] = _start_playwright,
        *,
        diagnostics: ExecutorRecoveryDiagnostics | None = None,
    ) -> None:
        if not callable(starter) or (
            diagnostics is not None and not isinstance(diagnostics, ExecutorRecoveryDiagnostics)
        ):
            raise BrowserRuntimeRejected
        self._starter = starter
        self._diagnostics = diagnostics
        self._owner = object()
        self._owner_thread: int | None = None
        self._context: _BrowserContext | None = None
        self._playwright: _Playwright | None = None

    def __repr__(self) -> str:
        return "BrowserRuntime(<redacted>)"

    @property
    def is_running(self) -> bool:
        return self._context is not None and self._playwright is not None

    def start(self, request: BrowserLaunchRequest) -> None:
        if self.is_running or not isinstance(request, BrowserLaunchRequest):
            raise BrowserRuntimeRejected
        request.revalidate()
        playwright: _Playwright | None = None
        context: _BrowserContext | None = None
        try:
            playwright, context = _launch_context(self._starter, request)
            context.set_default_timeout(_ACTION_TIMEOUT_MILLISECONDS)
            context.set_default_navigation_timeout(_NAVIGATION_TIMEOUT_MILLISECONDS)
        except Exception:
            _best_effort_close(context, playwright)
            self._window_unavailable()
            raise BrowserRuntimeRejected from None
        self._owner_thread = get_ident()
        self._context = context
        self._playwright = playwright
        if self._diagnostics is not None:
            self._diagnostics.browser_window_available()

    @contextmanager
    def running(self, request: BrowserLaunchRequest) -> Iterator[BrowserRuntime]:
        self.start(request)
        try:
            yield self
        finally:
            self.close()

    def windows(self) -> tuple[BrowserWindow, ...]:
        context = self._require_running()
        try:
            return tuple(self._window(page) for page in context.pages)
        except Exception:
            self._window_unavailable()
            raise BrowserRuntimeRejected from None

    def primary_window(self) -> BrowserWindow:
        context = self._require_running()
        try:
            pages = context.pages
            page = pages[0] if pages else context.new_page()
            return self._window(page)
        except Exception:
            self._window_unavailable()
            raise BrowserRuntimeRejected from None

    def open_window(self) -> BrowserWindow:
        context = self._require_running()
        try:
            return self._window(context.new_page())
        except Exception:
            self._window_unavailable()
            raise BrowserRuntimeRejected from None

    def capture_window(
        self,
        trigger: Callable[[], object],
        *,
        timeout_milliseconds: int,
    ) -> BrowserWindow:
        context = self._require_running()
        timeout = _require_operation_timeout(timeout_milliseconds)
        if not callable(trigger):
            raise BrowserRuntimeRejected
        try:
            with context.expect_page(timeout=timeout) as pending:
                trigger()
            return self._window(pending.value)
        except PlaywrightTimeoutError:
            raise BrowserRuntimeTimedOut from None
        except BrowserRuntimeTimedOut:
            raise
        except Exception:
            self._window_unavailable()
            raise BrowserRuntimeRejected from None

    def close_window(self, window: BrowserWindow) -> None:
        context = self._require_running()
        if not isinstance(window, BrowserWindow) or window._owner is not self._owner:
            raise BrowserRuntimeRejected
        try:
            if not any(page is window._page for page in context.pages):
                raise ValueError
            window._page.close(
                reason="automation-tool.window-close",
                run_before_unload=False,
            )
        except Exception:
            self._window_unavailable()
            raise BrowserRuntimeRejected from None

    def close(self) -> None:
        if not self.is_running:
            return
        self._require_owner_thread()
        context = cast(_BrowserContext, self._context)
        playwright = cast(_Playwright, self._playwright)
        self._context = None
        self._playwright = None
        self._owner_thread = None
        failed = False
        try:
            context.close(reason="automation-tool.runtime-close")
        except Exception:
            failed = True
        try:
            playwright.stop()
        except Exception:
            failed = True
        if failed:
            self._window_unavailable()
            raise BrowserRuntimeRejected

    def _require_running(self) -> _BrowserContext:
        self._require_owner_thread()
        if self._context is None or self._playwright is None:
            raise BrowserRuntimeRejected
        return self._context

    def _require_owner_thread(self) -> None:
        if self._owner_thread is not None and self._owner_thread != get_ident():
            raise BrowserRuntimeRejected

    def _window(self, page: _Page) -> BrowserWindow:
        return BrowserWindow._for_runtime(self._owner, page)

    def _window_unavailable(self) -> None:
        if self._diagnostics is not None:
            self._diagnostics.browser_window_unavailable()


class BrowserRuntimeLease:
    """Own the one persistent context and its packaged Playwright driver."""

    def __init__(self, context: _BrowserContext, playwright: _Playwright) -> None:
        self._context = context
        self._playwright = playwright
        self._closed = False

    def __repr__(self) -> str:
        return "BrowserRuntimeLease(<redacted>)"

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exception: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        failed = False
        try:
            self._context.close()
        except Exception:
            failed = True
        try:
            self._playwright.stop()
        except Exception:
            failed = True
        if failed:
            raise BrowserRuntimeRejected


class PackagedBrowserRuntime:
    """Launch no browser except the explicit executable with the explicit private Profile."""

    def __init__(self, starter: Callable[[], _Playwright] = _start_playwright) -> None:
        if not callable(starter):
            raise BrowserRuntimeRejected
        self._starter = starter

    def open(self, request: BrowserLaunchRequest) -> BrowserRuntimeLease:
        if not isinstance(request, BrowserLaunchRequest):
            raise BrowserRuntimeRejected
        try:
            request.revalidate()
            playwright, context = _launch_context(self._starter, request)
        except Exception:
            raise BrowserRuntimeRejected from None
        return BrowserRuntimeLease(context, playwright)


def _launch_context(
    starter: Callable[[], _Playwright],
    request: BrowserLaunchRequest,
) -> tuple[_Playwright, _BrowserContext]:
    playwright: _Playwright | None = None
    try:
        playwright = starter()
        context = playwright.chromium.launch_persistent_context(
            request.profile_directory,
            accept_downloads=False,
            executable_path=request.executable_path,
            headless=request.headless,
            timeout=_START_TIMEOUT_MILLISECONDS,
        )
        return playwright, context
    except Exception:
        if playwright is not None:
            with suppress(Exception):
                playwright.stop()
        raise BrowserRuntimeRejected from None


def _best_effort_close(
    context: _BrowserContext | None,
    playwright: _Playwright | None,
) -> None:
    if context is not None:
        with suppress(Exception):
            context.close(reason="automation-tool.failed-start")
    if playwright is not None:
        with suppress(Exception):
            playwright.stop()


def _require_operation_timeout(value: object) -> float:
    if type(value) is not int or not 1 <= value <= _MAX_OPERATION_TIMEOUT_MILLISECONDS:
        raise BrowserRuntimeRejected
    return float(value)


def _require_path(path: object, *, regular_executable: bool) -> None:
    if not isinstance(path, Path):
        raise BrowserRuntimeRejected
    encoded = os.fspath(path)
    if (
        not path.is_absolute()
        or not encoded
        or len(encoded) > _MAX_PATH_CHARACTERS
        or any(
            ord(character) <= 0x1F
            or 0x7F <= ord(character) <= 0x9F
            or 0x202A <= ord(character) <= 0x202E
            or 0x2066 <= ord(character) <= 0x2069
            for character in encoded
        )
        or _has_symlink_component(path)
    ):
        raise BrowserRuntimeRejected
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError:
        raise BrowserRuntimeRejected from None
    if regular_executable:
        if not stat.S_ISREG(metadata.st_mode) or not os.access(path, os.X_OK):
            raise BrowserRuntimeRejected
    elif not stat.S_ISDIR(metadata.st_mode):
        raise BrowserRuntimeRejected


def _has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            return True
    return False


__all__ = [
    "BrowserLaunchRequest",
    "BrowserRuntime",
    "BrowserRuntimeLease",
    "BrowserRuntimeRejected",
    "BrowserRuntimeTimedOut",
    "BrowserWindow",
    "PackagedBrowserRuntime",
]
