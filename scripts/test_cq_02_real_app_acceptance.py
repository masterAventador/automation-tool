#!/usr/bin/env python3
"""CQ-02 real-App accessibility-tree acceptance contract tests."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/run_cq_02_acceptance.py"
SPEC = ROOT / "frontend/e2e-tauri/upstream-name-leak.spec.ts"
OWNERSHIP = ROOT / "scripts/acceptance_driver_ownership.v1.json"


class RealAppAccessibilityAcceptanceTests(unittest.TestCase):
    def test_real_app_runner_and_spec_are_not_replaced_by_the_harness(self) -> None:
        self.assertTrue(RUNNER.is_file(), "CQ-02 real App runner is missing")
        self.assertTrue(SPEC.is_file(), "CQ-02 real App WebDriver spec is missing")

        runner = RUNNER.read_text(encoding="utf-8")
        self.assertIn("video_studio_startup_harness", runner)
        self.assertIn("build:tauri:video-studio-test", runner)
        self.assertIn("./e2e-tauri/upstream-name-leak.spec.ts", runner)
        self.assertIn("require_real_app_capture", runner)

    def test_spec_reads_w3c_computed_accessibility_facts(self) -> None:
        source = SPEC.read_text(encoding="utf-8")

        self.assertIn(".getComputedLabel()", source)
        self.assertIn(".getComputedRole()", source)
        self.assertIn("document.title", source)
        self.assertNotIn('querySelectorAll("[aria-label]', source)
        self.assertNotIn("ariaSnapshot", source)

    def test_runner_has_explicit_slow_desktop_ownership(self) -> None:
        registry = json.loads(OWNERSHIP.read_text(encoding="utf-8"))
        profile = registry["blockedProfiles"]["manual_hidden_desktop_app"]

        self.assertIn("run_cq_02_acceptance.py", profile["drivers"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
