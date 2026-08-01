#!/usr/bin/env python3
"""Focused environment-boundary tests for the LE-17 real App acceptance."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend/src"))

import acceptance_postgres  # noqa: E402
import run_le_17_acceptance  # noqa: E402


class Le17AcceptanceEnvironmentTest(unittest.TestCase):
    def test_only_the_owned_postgres_root_crosses_the_product_config_filter(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
