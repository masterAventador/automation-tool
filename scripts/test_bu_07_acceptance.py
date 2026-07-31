#!/usr/bin/env python3
"""BU-07 正式包入口与重复次数的确定性边界。"""

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

from run_bu_07_acceptance import (  # noqa: E402
    declared_packaged_browser,
    repeat_count,
)


class RepeatCountTests(unittest.TestCase):
    def test_three_runs_are_accepted(self) -> None:
        self.assertEqual(3, repeat_count("3"))

    def test_zero_runs_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            repeat_count("0")

    def test_an_unbounded_run_count_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            repeat_count("11")


class PackagedBrowserTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory(prefix="bu07-package-")
        self.addCleanup(self._directory.cleanup)
        self.application = Path(self._directory.name) / "产品.app"
        self.browser = self.application / "Contents/Resources/embedded-browser"
        self.executable = self.browser / "browser/bin/chrome"
        self.executable.parent.mkdir(parents=True)
        self.executable.write_bytes(b"browser")
        self.manifest = self.browser / "distribution-manifest.v1.json"
        self.write_manifest()

    def write_manifest(
        self,
        *,
        target: str = "macos-arm64",
        version: str = "149.0.7827.55",
        executable: str = "browser/bin/chrome",
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

    def test_declared_browser_must_match_target_version_and_package_root(self) -> None:
        resolved = declared_packaged_browser(
            application=self.application,
            browser_root=self.browser,
            target_id="macos-arm64",
            locked_version="149.0.7827.55",
        )
        self.assertEqual(self.executable.resolve(), resolved)

    def test_a_wrong_target_is_refused(self) -> None:
        self.write_manifest(target="windows-x86_64")
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            declared_packaged_browser(
                application=self.application,
                browser_root=self.browser,
                target_id="macos-arm64",
                locked_version="149.0.7827.55",
            )

    def test_a_wrong_version_is_refused(self) -> None:
        self.write_manifest(version="149.0.7827.54")
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            declared_packaged_browser(
                application=self.application,
                browser_root=self.browser,
                target_id="macos-arm64",
                locked_version="149.0.7827.55",
            )

    def test_a_missing_executable_is_refused(self) -> None:
        self.executable.unlink()
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            declared_packaged_browser(
                application=self.application,
                browser_root=self.browser,
                target_id="macos-arm64",
                locked_version="149.0.7827.55",
            )

    def test_an_executable_outside_the_package_is_refused(self) -> None:
        outside = self.application.parent / "outside-browser"
        outside.write_bytes(b"browser")
        self.write_manifest(executable="../../../../outside-browser")
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            declared_packaged_browser(
                application=self.application,
                browser_root=self.browser,
                target_id="macos-arm64",
                locked_version="149.0.7827.55",
            )


if __name__ == "__main__":
    unittest.main()
