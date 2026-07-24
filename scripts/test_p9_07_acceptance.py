#!/usr/bin/env python3
"""Deterministic tests for the P9-07 embedded-Chromium device gate."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run_p9_07_acceptance as acceptance


class P907AcceptanceTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Authenticode is Windows-only")
    def test_unsigned_candidate_is_rejected_before_installation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="automation-tool-p907-unsigned-") as directory:
            candidate = Path(directory) / "unsigned-installer.exe"
            candidate.write_bytes(b"MZ")

            with self.assertRaisesRegex(
                RuntimeError,
                "valid timestamped Authenticode signer",
            ):
                acceptance.require_authenticode(candidate)

    def test_embedded_browser_manifest_resolves_only_the_windows_chromium(self) -> None:
        with tempfile.TemporaryDirectory(prefix="automation-tool-p907-browser-") as directory:
            install_root = Path(directory)
            distribution = install_root / acceptance.EMBEDDED_BROWSER_RESOURCE
            executable = distribution / "chrome-win64/chrome.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"MZ")
            (distribution / acceptance.EMBEDDED_BROWSER_MANIFEST).write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "policy": "fail_closed",
                        "target": "windows-x86_64",
                        "executable": "chrome-win64/chrome.exe",
                    }
                ),
                encoding="utf-8",
                newline="\n",
            )

            self.assertEqual(
                acceptance.embedded_browser_executable(install_root),
                executable.resolve(strict=True),
            )

            (distribution / acceptance.EMBEDDED_BROWSER_MANIFEST).write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "policy": "fail_closed",
                        "target": "macos-arm64",
                        "executable": "chrome-win64/chrome.exe",
                    }
                ),
                encoding="utf-8",
                newline="\n",
            )
            with self.assertRaisesRegex(RuntimeError, "distribution manifest"):
                acceptance.embedded_browser_executable(install_root)

    def test_profile_command_requires_the_new_private_douyin_uuid_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="automation-tool-p907-profile-") as directory:
            app_data = Path(directory) / "应用 数据"
            expected_root = app_data / acceptance.EMBEDDED_PROFILE_ROOT
            profile = expected_root / "douyin/3ca7dcbc-9a68-41e3-b10c-702d9b30cae4"
            command = f'"chrome.exe" "--user-data-dir={profile}" --remote-debugging-pipe'

            self.assertTrue(acceptance.command_uses_private_profile(command, expected_root))
            self.assertFalse(
                acceptance.command_uses_private_profile(
                    f'"chrome.exe" "--user-data-dir={expected_root}"',
                    expected_root,
                )
            )
            self.assertFalse(
                acceptance.command_uses_private_profile(
                    f'"chrome.exe" "--user-data-dir={app_data / "browser-profiles"}"',
                    expected_root,
                )
            )

    def test_runtime_rejects_a_system_browser_or_wrong_executable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="automation-tool-p907-runtime-") as directory:
            app_data = Path(directory) / "AppData"
            profile = (
                app_data
                / acceptance.EMBEDDED_PROFILE_ROOT
                / "douyin/3ca7dcbc-9a68-41e3-b10c-702d9b30cae4"
            )
            browser = Path(directory) / "install/embedded-browser/chrome-win64/chrome.exe"
            records = [
                acceptance.ProcessRecord("", "app.exe", "app.exe", 0, 100),
                acceptance.ProcessRecord(
                    "", "automation-tool-executor.exe", "automation-tool-executor.exe", 100, 101
                ),
                acceptance.ProcessRecord(
                    f'"{browser}" "--user-data-dir={profile}"',
                    str(browser),
                    "chrome.exe",
                    101,
                    102,
                ),
            ]
            window = acceptance.WindowFacts(awareness=2, height=800, width=1200, window_dpi=120)

            with (
                patch.object(acceptance, "private_app_data", return_value=app_data),
                patch.object(acceptance, "process_snapshot", return_value=records),
                patch.object(acceptance, "is_process_in_job", return_value=True),
            ):
                runtime, _ = acceptance.sample_runtime_facts(100, window, browser)
                self.assertEqual(runtime.browser, "embedded-chromium")

                records.append(
                    acceptance.ProcessRecord(
                        '"C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe"',
                        "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
                        "msedge.exe",
                        100,
                        103,
                    )
                )
                with self.assertRaisesRegex(RuntimeError, "packaged Chromium"):
                    acceptance.sample_runtime_facts(100, window, browser)


if __name__ == "__main__":
    unittest.main()
