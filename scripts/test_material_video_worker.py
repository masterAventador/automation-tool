#!/usr/bin/env python3
"""Small process-boundary tests that do not replace the real frozen acceptance."""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workers/material_montage"))

import worker_main  # noqa: E402


class MaterialVideoWorkerBoundaryTest(unittest.TestCase):
    def test_rejects_missing_or_unknown_commands_without_loading_runtime(self) -> None:
        for arguments in ([], ["--unknown"], ["--probe", "extra"]):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = worker_main.main(arguments)
            self.assertEqual(result, 64)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "Material video worker command is required\n")

    def test_dependency_probe_rejects_non_startup_dependency(self) -> None:
        with self.assertRaisesRegex(ValueError, "not part of the startup set"):
            worker_main.dependency_probe("litellm")


if __name__ == "__main__":
    unittest.main()
