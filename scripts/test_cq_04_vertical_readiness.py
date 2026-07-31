#!/usr/bin/env python3
"""CQ-04 不能把凭据存在误报成独立剪辑已接入生产 App。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from cq_04_vertical_readiness import (  # noqa: E402
    VerticalReadinessRejected,
    video_editing_production_wiring_gaps,
)
import run_cq_04_acceptance as acceptance  # noqa: E402


class VideoEditingProductionWiringTests(unittest.TestCase):
    def test_a_skipped_package_run_does_not_claim_package_segments_passed(self) -> None:
        output = StringIO()
        with (
            patch.object(
                acceptance.sys,
                "argv",
                ["run_cq_04_acceptance.py", "--skip-package-segments"],
            ),
            patch.object(
                acceptance,
                "probe_external_conditions",
                return_value={"外部条件": False},
            ),
            patch.object(
                acceptance,
                "probe_production_readiness",
                return_value={"生产装配": True},
            ),
            patch.object(acceptance, "run_segment"),
            patch.object(acceptance, "sweep_the_ledger", return_value=78),
            redirect_stdout(output),
        ):
            self.assertEqual(acceptance.main(), 0)

        self.assertNotIn("the release-package segments", output.getvalue())
        self.assertIn("package segments were skipped", output.getvalue())

    def test_a_credential_file_is_not_reported_as_a_verified_credential(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cq04-credential-") as raw:
            credential = Path(raw) / "credential.json"
            credential.write_text("not inspected", encoding="utf-8")
            output = StringIO()
            with (
                patch.object(
                    acceptance,
                    "EXTERNAL_CONDITIONS",
                    {"阿里云剪辑密钥": credential},
                ),
                redirect_stdout(output),
            ):
                available = acceptance.probe_external_conditions()

        self.assertFalse(available["阿里云剪辑密钥"])
        self.assertIn("文件存在（有效性未验证）", output.getvalue())
        self.assertNotIn("阿里云剪辑密钥: 可用", output.getvalue())

    def test_the_current_repository_has_the_real_production_wiring(self) -> None:
        gaps = video_editing_production_wiring_gaps(
            ROOT / "frontend/src/main.tsx",
            ROOT / "frontend/src/app/production-wiring.test.ts",
            ROOT / "frontend/src-tauri/src/lib.rs",
        )
        self.assertEqual((), gaps)

    def test_a_real_tauri_gateway_without_an_expected_failure_is_ready(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cq04-wiring-") as raw:
            base = Path(raw)
            main = base / "main.tsx"
            test = base / "production-wiring.test.ts"
            main.write_text(
                "const videoEditingGateway = new TauriVideoEditingGateway();\n",
                encoding="utf-8",
            )
            test.write_text(
                'it("videoEditingGateway is handed a real Tauri gateway", () => {});\n',
                encoding="utf-8",
            )
            self.assertEqual(
                (), video_editing_production_wiring_gaps(main, test)
            )

    def test_missing_production_source_is_refused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cq04-wiring-") as raw:
            base = Path(raw)
            with self.assertRaises(VerticalReadinessRejected):
                video_editing_production_wiring_gaps(
                    base / "missing-main.tsx",
                    base / "missing-test.ts",
                )


if __name__ == "__main__":
    unittest.main()
