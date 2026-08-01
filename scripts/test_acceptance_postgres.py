#!/usr/bin/env python3
"""Focused diagnostics tests for the shared acceptance PostgreSQL lifecycle."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import acceptance_postgres  # noqa: E402


class NativePostgresDiagnosticTest(unittest.TestCase):
    def test_parent_owned_windows_root_uses_native_inherited_acl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "windows-postgres"
            environment = {
                acceptance_postgres.WINDOWS_POSTGRES_ROOT_ENVIRONMENT: str(root)
            }
            with (
                mock.patch.object(Path, "mkdir") as mkdir,
                acceptance_postgres._windows_postgres_root(environment) as yielded,
            ):
                self.assertEqual(yielded, root)

        mkdir.assert_called_once_with()

    def test_checked_command_reports_the_captured_postgres_error(self) -> None:
        failure = subprocess.CalledProcessError(
            1,
            ["initdb", "--pgdata", "isolated"],
            output="",
            stderr="fixed initdb reason",
        )
        with (
            mock.patch.object(
                acceptance_postgres.subprocess,
                "run",
                side_effect=failure,
            ),
            self.assertRaisesRegex(
                RuntimeError,
                "initdb failed: fixed initdb reason",
            ),
        ):
            acceptance_postgres._run_captured_postgres_command(
                ["initdb", "--pgdata", "isolated"],
                environment={"PATH": "trusted"},
            )


if __name__ == "__main__":
    unittest.main()
