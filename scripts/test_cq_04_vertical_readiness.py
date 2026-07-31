#!/usr/bin/env python3
"""CQ-04 不能把凭据存在误报成独立剪辑已接入生产 App。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from cq_04_vertical_readiness import (  # noqa: E402
    VerticalReadinessRejected,
    video_editing_production_wiring_gaps,
)


class VideoEditingProductionWiringTests(unittest.TestCase):
    def test_the_current_repository_reports_the_real_production_gap(self) -> None:
        gaps = video_editing_production_wiring_gaps(
            ROOT / "frontend/src/main.tsx",
            ROOT / "frontend/src/app/production-wiring.test.ts",
        )
        self.assertEqual(
            (
                "production App still constructs the sessionStorage editing gateway",
                "production wiring still marks the real Tauri editing gateway as expected failure",
                "production App constructs no real Tauri videoEditingGateway",
            ),
            gaps,
        )

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
