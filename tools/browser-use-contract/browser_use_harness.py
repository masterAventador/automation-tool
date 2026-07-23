#!/usr/bin/env python3
"""BU-02: hardened dual-mode Browser Use session harness for one Chromium.

Only two launch shapes exist, both fail closed:

- Isolated validation: a Rust-verified `executable_path` plus a fresh private
  temporary profile. No browser discovery, no default install location, no
  download fallback — a missing or non-executable path is rejected before any
  Browser Use object is created.
- Operations takeover: a random-loopback `cdp_url` handed over by the current
  `BrowserSurfaceLease` owner. Only `http://127.0.0.1:<port>` is accepted;
  localhost aliases, non-loopback hosts, TLS/WebSocket schemes and path
  suffixes are all rejected.

The session keyword surface is a fixed constant: local-only, no keep-alive,
no default extensions, no captcha solver, no highlighting. The process
environment for any harness launch disables Browser Use cloud sync and
telemetry and strips cloud credentials.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:  # pragma: no cover - typing only
    from browser_use import BrowserSession

_CDP_URL_PATTERN: Final = re.compile(r"^http://127\.0\.0\.1:\d{2,5}$")

FIXED_SESSION_KWARGS: Final = MappingProxyType(
    {
        "is_local": True,
        "keep_alive": False,
        "enable_default_extensions": False,
        "captcha_solver": False,
        "highlight_elements": False,
    }
)

_FORBIDDEN_ENVIRONMENT_KEYS: Final = frozenset(
    {
        "all_proxy",
        "anonymized_telemetry",
        "browser_use_cloud_api_key",
        "browser_use_cloud_sync",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
)


class HarnessRejected(RuntimeError):
    """The launch plan or session request violates the closed harness policy."""


def _reject(message: str) -> None:
    raise HarnessRejected(f"browser use harness rejected: {message}")


def _is_native_executable(path: Path) -> bool:
    if not path.is_file():
        return False
    if os.name != "nt":
        return bool(path.stat().st_mode & 0o111)
    if path.suffix.casefold() != ".exe":
        return False
    try:
        with path.open("rb") as executable:
            return executable.read(2) == b"MZ"
    except OSError:
        return False


@dataclass(frozen=True)
class IsolatedLaunchPlan:
    """Isolated validation launch: verified executable + fresh private profile."""

    executable_path: Path
    user_data_dir: Path
    headless: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.executable_path, Path) or not isinstance(
            self.user_data_dir, Path
        ):
            _reject("paths must be Path values")
        if not _is_native_executable(self.executable_path):
            _reject("executable path is not a verified executable file")
        if self.user_data_dir.exists() and (
            not self.user_data_dir.is_dir() or any(self.user_data_dir.iterdir())
        ):
            _reject("isolated profile directory must be fresh and empty")
        if type(self.headless) is not bool:
            _reject("headless must be a boolean")


@dataclass(frozen=True)
class TakeoverLaunchPlan:
    """Operations takeover launch: random loopback CDP endpoint only."""

    cdp_url: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.cdp_url) is not str or _CDP_URL_PATTERN.fullmatch(self.cdp_url) is None:
            _reject("cdp url must be http://127.0.0.1:<port> exactly")


def harness_environment(base: dict[str, str]) -> dict[str, str]:
    """Return a launch environment with cloud, telemetry and creds disabled."""
    if not isinstance(base, dict):
        _reject("environment must be a dict")
    environment = {
        key: value
        for key, value in base.items()
        if key.casefold() not in _FORBIDDEN_ENVIRONMENT_KEYS
    }
    environment["BROWSER_USE_CLOUD_SYNC"] = "false"
    environment["ANONYMIZED_TELEMETRY"] = "false"
    return environment


def create_session(plan: IsolatedLaunchPlan | TakeoverLaunchPlan) -> BrowserSession:
    """Build one hardened BrowserSession from a validated launch plan."""
    from browser_use import BrowserSession

    if isinstance(plan, IsolatedLaunchPlan):
        return BrowserSession(
            executable_path=str(plan.executable_path),
            user_data_dir=str(plan.user_data_dir),
            headless=plan.headless,
            **FIXED_SESSION_KWARGS,
        )
    if isinstance(plan, TakeoverLaunchPlan):
        return BrowserSession(
            cdp_url=plan.cdp_url,
            headless=True,
            **FIXED_SESSION_KWARGS,
        )
    _reject("unknown launch plan type")
    raise AssertionError("unreachable")


__all__ = [
    "FIXED_SESSION_KWARGS",
    "HarnessRejected",
    "IsolatedLaunchPlan",
    "TakeoverLaunchPlan",
    "create_session",
    "harness_environment",
]
