#!/usr/bin/env python3
"""Focused privacy-boundary tests for the LE-18 real App acceptance."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend/src"))

import acceptance_postgres
import run_le_18_acceptance


class Le18AcceptanceBoundaryTest(unittest.TestCase):
    def test_only_owned_postgres_configuration_crosses_the_filter(self) -> None:
        postgres_root = acceptance_postgres.WINDOWS_POSTGRES_ROOT_ENVIRONMENT
        prepared = run_le_18_acceptance.acceptance_environment(
            {
                "AUTOMATION_TOOL_PRODUCT_SECRET": "must-not-cross",
                postgres_root: "C:/trusted/postgres-root",
                "PATH": "trusted-tools",
            }
        )
        self.assertEqual(
            prepared,
            {postgres_root: "C:/trusted/postgres-root", "PATH": "trusted-tools"},
        )

    def test_private_source_and_capabilities_are_refused_from_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "private-name.mp4"
            source.write_bytes(b"safe")
            for output in (
                str(source),
                source.name,
                "http://127.0.0.1/materials/id/content?cap=secret",
                "Authorization: Bearer secret-value",
            ):
                with (
                    self.subTest(output=output),
                    self.assertRaisesRegex(RuntimeError, "private local material"),
                ):
                    run_le_18_acceptance.assert_no_private_evidence(output, [source])

    def test_successful_delete_must_leave_source_bytes_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.png"
            source.write_bytes(b"controlled image")
            expected = run_le_18_acceptance.digest(source)
            run_le_18_acceptance.source_unchanged_after_delete(source, expected)
            source.write_bytes(b"changed")
            with self.assertRaisesRegex(RuntimeError, "changed the user source"):
                run_le_18_acceptance.source_unchanged_after_delete(source, expected)


if __name__ == "__main__":
    unittest.main()
