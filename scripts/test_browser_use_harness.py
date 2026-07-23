#!/usr/bin/env python3
"""BU-02 deterministic tests for the single-Chromium dual-mode harness.

No real browser or network: launch-plan validation is pure, and the session
factory is exercised against an injected fake `browser_use` module so the
hardened keyword surface is asserted without importing the real library.
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/browser-use-contract"))

from browser_use_harness import (  # noqa: E402
    FIXED_SESSION_KWARGS,
    HarnessRejected,
    IsolatedLaunchPlan,
    TakeoverLaunchPlan,
    create_session,
    harness_environment,
)


class _FakeBrowserSession:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


def _install_fake_browser_use() -> None:
    module = types.ModuleType("browser_use")
    module.BrowserSession = _FakeBrowserSession
    sys.modules["browser_use"] = module


class LaunchPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(prefix="bu02-test-")
        self.addCleanup(self._directory.cleanup)
        self.base = Path(self._directory.name)
        self.executable = self.base / ("chromium.exe" if os.name == "nt" else "chromium")
        self.executable.write_bytes(b"MZ" if os.name == "nt" else b"#!/bin/sh\n")
        if os.name != "nt":
            self.executable.chmod(0o755)

    def test_isolated_plan_requires_verified_executable(self) -> None:
        plan = IsolatedLaunchPlan(
            executable_path=self.executable,
            user_data_dir=self.base / "profile",
        )
        self.assertTrue(plan.headless)
        for missing in (self.base / "absent", self.base):
            with self.assertRaises(HarnessRejected):
                IsolatedLaunchPlan(
                    executable_path=missing,
                    user_data_dir=self.base / "profile-2",
                )

    def test_isolated_plan_rejects_non_executable_file(self) -> None:
        plain = self.base / ("plain.exe" if os.name == "nt" else "plain")
        plain.write_bytes(b"data")
        if os.name != "nt":
            plain.chmod(0o600)
        with self.assertRaises(HarnessRejected):
            IsolatedLaunchPlan(executable_path=plain, user_data_dir=self.base / "p")

    def test_isolated_plan_rejects_existing_non_empty_profile(self) -> None:
        used = self.base / "used-profile"
        used.mkdir()
        (used / "Cookies").write_bytes(b"x")
        with self.assertRaises(HarnessRejected):
            IsolatedLaunchPlan(executable_path=self.executable, user_data_dir=used)

    def test_takeover_plan_accepts_only_loopback_cdp(self) -> None:
        plan = TakeoverLaunchPlan(cdp_url="http://127.0.0.1:53211")
        self.assertEqual(plan.cdp_url, "http://127.0.0.1:53211")
        for invalid in (
            "http://localhost:53211",
            "http://0.0.0.0:53211",
            "http://192.168.1.4:9222",
            "https://127.0.0.1:53211",
            "ws://127.0.0.1:53211",
            "http://127.0.0.1:53211/path",
            "http://127.0.0.1",
        ):
            with self.assertRaises(HarnessRejected):
                TakeoverLaunchPlan(cdp_url=invalid)


class SessionFactoryTests(unittest.TestCase):
    def setUp(self) -> None:
        _install_fake_browser_use()
        self.addCleanup(sys.modules.pop, "browser_use", None)
        self._directory = tempfile.TemporaryDirectory(prefix="bu02-test-")
        self.addCleanup(self._directory.cleanup)
        self.base = Path(self._directory.name)
        self.executable = self.base / ("chromium.exe" if os.name == "nt" else "chromium")
        self.executable.write_bytes(b"MZ" if os.name == "nt" else b"#!/bin/sh\n")
        if os.name != "nt":
            self.executable.chmod(0o755)

    def test_isolated_session_uses_only_the_verified_path(self) -> None:
        plan = IsolatedLaunchPlan(
            executable_path=self.executable,
            user_data_dir=self.base / "profile",
        )
        session = create_session(plan)
        kwargs = session.kwargs
        self.assertEqual(kwargs["executable_path"], str(self.executable))
        self.assertEqual(kwargs["user_data_dir"], str(self.base / "profile"))
        self.assertTrue(kwargs["headless"])
        self.assertNotIn("cdp_url", kwargs)
        for key, value in FIXED_SESSION_KWARGS.items():
            self.assertEqual(kwargs[key], value)

    def test_takeover_session_uses_only_the_loopback_cdp(self) -> None:
        session = create_session(TakeoverLaunchPlan(cdp_url="http://127.0.0.1:60123"))
        kwargs = session.kwargs
        self.assertEqual(kwargs["cdp_url"], "http://127.0.0.1:60123")
        self.assertNotIn("executable_path", kwargs)
        self.assertNotIn("user_data_dir", kwargs)
        for key, value in FIXED_SESSION_KWARGS.items():
            self.assertEqual(kwargs[key], value)

    def test_fixed_kwargs_close_discovery_download_cloud_and_extensions(self) -> None:
        self.assertIs(FIXED_SESSION_KWARGS["is_local"], True)
        self.assertIs(FIXED_SESSION_KWARGS["keep_alive"], False)
        self.assertIs(FIXED_SESSION_KWARGS["enable_default_extensions"], False)
        self.assertIs(FIXED_SESSION_KWARGS["captcha_solver"], False)
        self.assertIs(FIXED_SESSION_KWARGS["highlight_elements"], False)

    def test_unknown_plan_type_is_rejected(self) -> None:
        with self.assertRaises(HarnessRejected):
            create_session(object())  # type: ignore[arg-type]


class HarnessEnvironmentTests(unittest.TestCase):
    def test_environment_disables_cloud_telemetry_and_downloads(self) -> None:
        environment = harness_environment(dict(os.environ))
        self.assertEqual(environment["BROWSER_USE_CLOUD_SYNC"], "false")
        self.assertEqual(environment["ANONYMIZED_TELEMETRY"], "false")
        self.assertNotIn("BROWSER_USE_CLOUD_API_KEY", environment)

    def test_environment_strips_cloud_credentials(self) -> None:
        polluted = dict(os.environ)
        polluted["BROWSER_USE_CLOUD_API_KEY"] = "secret"
        polluted["all_proxy"] = "socks://127.0.0.1:1080"
        polluted["HTTP_PROXY"] = "http://127.0.0.1:8080"
        polluted["https_proxy"] = "http://127.0.0.1:8080"
        polluted["NO_PROXY"] = "127.0.0.1"
        environment = harness_environment(polluted)
        self.assertNotIn("BROWSER_USE_CLOUD_API_KEY", environment)
        self.assertFalse(
            any(key.casefold().endswith("proxy") for key in environment)
        )


if __name__ == "__main__":
    unittest.main()
