#!/usr/bin/env python3
"""EB-17 当前发布包入口不能漂回历史验收目录。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_eb_17_acceptance as acceptance  # noqa: E402
import run_cq_03_acceptance as concurrent_acceptance  # noqa: E402
import run_pb_08_acceptance as publish_acceptance  # noqa: E402


class CurrentReleaseDefaultsTests(unittest.TestCase):
    def test_release_consumers_default_to_the_production_output(self) -> None:
        expected = (
            ROOT
            / ".local/release/cargo-target/release/bundle/macos/自动化运营工具.app"
        )
        self.assertEqual(expected, acceptance.DEFAULT_PACKAGE)
        self.assertEqual(expected, concurrent_acceptance.DEFAULT_PACKAGE)
        self.assertEqual(expected, publish_acceptance.DEFAULT_PACKAGE)


class PackagedBrowserVerificationTests(unittest.TestCase):
    def test_runtime_probe_reuses_the_release_distribution_gate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eb17-package-") as raw:
            application = Path(raw) / "产品.app"
            browser = application / "Contents/Resources/embedded-browser"
            executable = browser / "browser/bin/chrome"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"browser")
            (browser / "distribution-manifest.v1.json").write_text(
                json.dumps(
                    {
                        "target": "macos-arm64",
                        "executable": "browser/bin/chrome",
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(
                acceptance,
                "require_packaged_browser",
                return_value=browser,
                create=True,
            ) as verify:
                resolved = acceptance.packaged_browser(application, "macos-arm64")

        self.assertEqual(executable, resolved)
        verify.assert_called_once_with(
            application=application,
            target_id="macos-arm64",
            platform="macos",
        )


if __name__ == "__main__":
    unittest.main()
