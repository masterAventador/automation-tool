#!/usr/bin/env python3
"""PB-08 正式包浏览器解析的双平台确定性边界。"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_pb_08_acceptance import (  # noqa: E402
    declared_packaged_browser,
    package_platform,
)


class PackagePlatformTests(unittest.TestCase):
    def test_macos_targets_use_the_macos_resource_shape(self) -> None:
        self.assertEqual("macos", package_platform("macos-arm64"))

    def test_windows_targets_use_the_windows_resource_shape(self) -> None:
        self.assertEqual("windows", package_platform("windows-x86_64"))


class DeclaredPackagedBrowserTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(prefix="pb08-package-")
        self.addCleanup(self._directory.cleanup)
        self.application = Path(self._directory.name) / "installed-product"
        self.browser = self.application / "embedded-browser"
        self.executable = self.browser / "chrome-win64/chrome.exe"
        self.executable.parent.mkdir(parents=True)
        self.executable.write_bytes(b"browser")
        self.manifest = self.browser / "distribution-manifest.v1.json"
        self.write_manifest()

    def write_manifest(
        self,
        *,
        target: str = "windows-x86_64",
        version: str = "149.0.7827.55",
        executable: str = "chrome-win64/chrome.exe",
    ) -> None:
        self.manifest.write_text(
            json.dumps(
                {
                    "target": target,
                    "executable": executable,
                    "runtime": {
                        "chromium": {
                            "browser_version": version,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

    def test_windows_package_root_resolves_its_declared_browser(self) -> None:
        resolved = declared_packaged_browser(
            application=self.application,
            browser_root=self.browser,
            target_id="windows-x86_64",
            locked_version="149.0.7827.55",
        )
        self.assertEqual(self.executable.resolve(), resolved)

    def test_a_wrong_version_is_refused(self) -> None:
        self.write_manifest(version="149.0.7827.54")
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            declared_packaged_browser(
                application=self.application,
                browser_root=self.browser,
                target_id="windows-x86_64",
                locked_version="149.0.7827.55",
            )

    def test_a_missing_executable_is_refused(self) -> None:
        self.executable.unlink()
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            declared_packaged_browser(
                application=self.application,
                browser_root=self.browser,
                target_id="windows-x86_64",
                locked_version="149.0.7827.55",
            )

    def test_an_executable_outside_the_package_is_refused(self) -> None:
        outside = self.application.parent / "outside-browser.exe"
        outside.write_bytes(b"browser")
        self.write_manifest(executable="../../outside-browser.exe")
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            declared_packaged_browser(
                application=self.application,
                browser_root=self.browser,
                target_id="windows-x86_64",
                locked_version="149.0.7827.55",
            )


if __name__ == "__main__":
    unittest.main()
