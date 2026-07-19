"""Packaged Playwright primitive for a Rust-authorized system browser and Profile."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Self, cast

from playwright.sync_api import sync_playwright

_START_TIMEOUT_MILLISECONDS = 30_000
_MAX_PATH_CHARACTERS = 4096


class BrowserRuntimeRejected(RuntimeError):
    """The browser cannot be launched without weakening the local trust boundary."""

    def __init__(self) -> None:
        super().__init__("packaged browser runtime is unavailable")


@dataclass(frozen=True, slots=True, repr=False)
class BrowserLaunchRequest:
    """Paths already authorized and held stable by the Rust BrowserRuntime caller."""

    executable_path: Path
    profile_directory: Path

    def __post_init__(self) -> None:
        _require_path(self.executable_path, regular_executable=True)
        _require_path(self.profile_directory, regular_executable=False)

    def __repr__(self) -> str:
        return "BrowserLaunchRequest(<redacted>)"


class _BrowserContext(Protocol):
    def close(self) -> None: ...


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
        playwright: _Playwright | None = None
        try:
            playwright = self._starter()
            context = playwright.chromium.launch_persistent_context(
                request.profile_directory,
                accept_downloads=False,
                executable_path=request.executable_path,
                headless=False,
                timeout=_START_TIMEOUT_MILLISECONDS,
            )
        except Exception:
            if playwright is not None:
                with suppress(Exception):
                    playwright.stop()
            raise BrowserRuntimeRejected from None
        return BrowserRuntimeLease(context, playwright)


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
    "BrowserRuntimeLease",
    "BrowserRuntimeRejected",
    "PackagedBrowserRuntime",
]
