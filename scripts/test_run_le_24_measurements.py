from __future__ import annotations

import sys
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from le24_measurement import Le24Measurement, Le24MeasurementStep
from run_le_24_measurements import (
    _run_formal_observation,
    build_le24_report,
    collect_le24_paired_measurements,
)


class Le24MeasurementCollectionTests(unittest.TestCase):
    def test_failed_formal_observation_preserves_the_child_diagnostic(self) -> None:
        diagnostic = StringIO()
        completed = CompletedProcess(
            args=["acceptance"],
            returncode=1,
            stdout="formal App terminal failure\n",
            stderr="webdriver detail\n",
        )

        with (
            patch("run_le_24_measurements.subprocess.run", return_value=completed),
            redirect_stderr(diagnostic),
            self.assertRaisesRegex(RuntimeError, "LE-24 formal App observation failed"),
        ):
            _run_formal_observation(
                Le24MeasurementStep(
                    repetition=1,
                    material_count=1,
                    enable_thinking=False,
                )
            )

        self.assertEqual(
            diagnostic.getvalue(),
            "formal App terminal failure\nwebdriver detail\n",
        )

    def test_collects_the_fixed_alternating_plan_into_nine_pairs(self) -> None:
        calls: list[tuple[int, int, bool]] = []

        def observe(step):
            calls.append((step.repetition, step.material_count, step.enable_thinking))
            elapsed_ms = 20_000 + step.material_count * 1_000 + step.repetition
            if step.enable_thinking:
                elapsed_ms += 1_000 + step.material_count * 2_500
            return Le24Measurement(
                enable_thinking=step.enable_thinking,
                material_count=step.material_count,
                elapsed_ms=elapsed_ms,
            )

        pairs = collect_le24_paired_measurements(observe)

        self.assertEqual(len(calls), 18)
        self.assertEqual(len(pairs), 9)
        self.assertEqual(calls[:2], [(1, 1, False), (1, 1, True)])
        self.assertEqual(calls[2:4], [(1, 2, True), (1, 2, False)])
        self.assertEqual(
            pairs[0].enabled_elapsed_ms - pairs[0].disabled_elapsed_ms,
            3_500,
        )

    def test_rejects_an_observation_that_does_not_match_its_plan_step(self) -> None:
        def observe(step):
            return Le24Measurement(
                enable_thinking=not step.enable_thinking,
                material_count=step.material_count,
                elapsed_ms=10_000,
            )

        with self.assertRaisesRegex(
            RuntimeError, "LE-24 observation does not match its plan"
        ):
            collect_le24_paired_measurements(observe)


class Le24MeasurementReportTests(unittest.TestCase):
    def test_report_is_path_free_and_contains_the_reproducible_decision(self) -> None:
        def observe(step):
            elapsed_ms = 20_000 + step.material_count * 1_000 + step.repetition
            if step.enable_thinking:
                elapsed_ms += 1_000 + step.material_count * 2_500
            return Le24Measurement(
                enable_thinking=step.enable_thinking,
                material_count=step.material_count,
                elapsed_ms=elapsed_ms,
            )

        report = build_le24_report(
            collect_le24_paired_measurements(observe),
            platform="macos",
            revision="a" * 40,
        )

        self.assertEqual(report["schemaVersion"], 1)
        self.assertEqual(report["platform"], "macos")
        self.assertEqual(report["revision"], "a" * 40)
        self.assertEqual(report["repetitionsPerMode"], 3)
        self.assertEqual(len(report["pairs"]), 9)
        self.assertEqual(
            report["analysis"],
            {
                "kind": "count_based",
                "medianDeltaByMaterialCountMs": {
                    "1": 3_500,
                    "2": 6_000,
                    "3": 8_500,
                },
                "baseMs": 1_000,
                "perMaterialMs": 2_500,
                "rangeMs": None,
                "userCopy": "开启后预计约多花 1 秒 + 每条素材 3 秒。",
            },
        )
        self.assertNotIn("path", str(report).lower())


if __name__ == "__main__":
    unittest.main()
