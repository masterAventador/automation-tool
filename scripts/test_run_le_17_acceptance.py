#!/usr/bin/env python3
"""Focused environment-boundary tests for the LE-17 real App acceptance."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend/src"))

import acceptance_postgres  # noqa: E402
import run_le_17_acceptance  # noqa: E402


class Le17AcceptanceEnvironmentTest(unittest.TestCase):
    def test_only_the_owned_postgres_root_crosses_the_product_config_filter(
        self,
    ) -> None:
        postgres_root = acceptance_postgres.WINDOWS_POSTGRES_ROOT_ENVIRONMENT

        prepared = run_le_17_acceptance.acceptance_environment(
            {
                "AUTOMATION_TOOL_PRODUCT_SECRET": "must-not-cross",
                postgres_root: "C:/trusted/postgres-root",
                "PATH": "trusted-tools",
            }
        )

        self.assertEqual(
            prepared,
            {
                postgres_root: "C:/trusted/postgres-root",
                "PATH": "trusted-tools",
            },
        )

    def test_desktop_diagnostics_emit_only_fixed_event_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app_data = Path(temporary)
            logs = app_data / "logs"
            logs.mkdir()
            (logs / "desktop-1-42.log").write_text(
                '{"timestampUnixMs":1,"event":"startup.local.started"}\n'
                '{"timestampUnixMs":2,"event":"token=must-not-cross"}\n'
                '{"timestampUnixMs":2,"event":"token.mustnotcross"}\n'
                '{"timestampUnixMs":3,"event":"startup.local.completed","detail":"secret"}\n'
                "not-json\n",
                encoding="utf-8",
            )

            self.assertEqual(
                run_le_17_acceptance.desktop_event_diagnostics(app_data),
                "startup.local.started",
            )


if __name__ == "__main__":
    unittest.main()
