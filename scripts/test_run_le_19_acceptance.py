from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from le24_measurement import (
    Le24Measurement,
    parse_le24_measurement,
    read_le24_measurement_request,
)


class Le24MeasurementRequestTests(unittest.TestCase):
    def test_absent_measurement_environment_uses_normal_le19_journey(self) -> None:
        self.assertIsNone(read_le24_measurement_request({"PATH": "/usr/bin"}))

    def test_measurement_environment_is_strict_and_paired(self) -> None:
        self.assertEqual(
            read_le24_measurement_request(
                {
                    "AUTOMATION_TOOL_LE24_MEASURE_THINKING": "enabled",
                    "AUTOMATION_TOOL_LE24_MATERIAL_COUNT": "3",
                }
            ),
            (True, 3),
        )
        for environment in (
            {"AUTOMATION_TOOL_LE24_MEASURE_THINKING": "enabled"},
            {"AUTOMATION_TOOL_LE24_MATERIAL_COUNT": "1"},
            {
                "AUTOMATION_TOOL_LE24_MEASURE_THINKING": "true",
                "AUTOMATION_TOOL_LE24_MATERIAL_COUNT": "1",
            },
            {
                "AUTOMATION_TOOL_LE24_MEASURE_THINKING": "disabled",
                "AUTOMATION_TOOL_LE24_MATERIAL_COUNT": "0",
            },
        ):
            with (
                self.subTest(environment=environment),
                self.assertRaisesRegex(
                    RuntimeError, "LE-24 measurement request is invalid"
                ),
            ):
                read_le24_measurement_request(environment)


class Le24MeasurementOutputTests(unittest.TestCase):
    def test_accepts_one_wdio_prefixed_exact_measurement(self) -> None:
        output = (
            "[0-0] journey output\n"
            '[0-0] LE24_MEASUREMENT {"schemaVersion":1,'
            '"enableThinking":true,"materialCount":2,"elapsedMs":1234}\n'
        )

        self.assertEqual(
            parse_le24_measurement(
                output, expected_enable_thinking=True, expected_material_count=2
            ),
            Le24Measurement(
                enable_thinking=True,
                material_count=2,
                elapsed_ms=1234,
            ),
        )

    def test_rejects_missing_duplicate_mismatched_or_inexact_measurement(self) -> None:
        valid = (
            'LE24_MEASUREMENT {"schemaVersion":1,"enableThinking":false,'
            '"materialCount":1,"elapsedMs":500}\n'
        )
        cases = (
            "",
            valid + valid,
            valid.replace('"materialCount":1', '"materialCount":2'),
            valid.replace('"elapsedMs":500', '"elapsedMs":0'),
            valid.replace('"schemaVersion":1', '"schemaVersion":2'),
            valid.replace('"elapsedMs":500', '"elapsedMs":500,"path":"private"'),
        )
        for output in cases:
            with (
                self.subTest(output=output),
                self.assertRaisesRegex(
                    RuntimeError, "LE-24 measurement output is invalid"
                ),
            ):
                parse_le24_measurement(
                    output,
                    expected_enable_thinking=False,
                    expected_material_count=1,
                )


if __name__ == "__main__":
    unittest.main()
