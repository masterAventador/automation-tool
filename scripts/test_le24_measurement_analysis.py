from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from le24_measurement import (
    Le24PairedSample,
    build_le24_measurement_plan,
    format_le24_estimate,
    summarize_le24_measurements,
)


def samples_with_deltas(
    deltas_by_count: dict[int, tuple[int, int, int]],
) -> list[Le24PairedSample]:
    return [
        Le24PairedSample(
            material_count=material_count,
            repetition=repetition,
            disabled_elapsed_ms=20_000 + material_count * 1_000 + repetition,
            enabled_elapsed_ms=(
                20_000 + material_count * 1_000 + repetition + delta_ms
            ),
        )
        for material_count, deltas in deltas_by_count.items()
        for repetition, delta_ms in enumerate(deltas, start=1)
    ]


class Le24MeasurementPlanTests(unittest.TestCase):
    def test_plan_has_three_alternating_pairs_for_every_material_count(self) -> None:
        plan = build_le24_measurement_plan()

        self.assertEqual(len(plan), 18)
        for repetition in (1, 2, 3):
            for material_count in (1, 2, 3):
                pair = [
                    step.enable_thinking
                    for step in plan
                    if step.repetition == repetition
                    and step.material_count == material_count
                ]
                expected = (
                    [False, True]
                    if (repetition + material_count) % 2 == 0
                    else [True, False]
                )
                self.assertEqual(pair, expected)


class Le24MeasurementSummaryTests(unittest.TestCase):
    def test_stable_linear_deltas_choose_a_count_based_estimate(self) -> None:
        summary = summarize_le24_measurements(
            samples_with_deltas(
                {
                    1: (3_400, 3_500, 3_600),
                    2: (5_900, 6_000, 6_100),
                    3: (8_400, 8_500, 8_600),
                }
            )
        )

        self.assertEqual(summary.kind, "count_based")
        self.assertEqual(
            summary.median_delta_by_material_count_ms, (3_500, 6_000, 8_500)
        )
        self.assertEqual(summary.base_ms, 1_000)
        self.assertEqual(summary.per_material_ms, 2_500)
        self.assertEqual(summary.range_ms, None)
        self.assertEqual(
            format_le24_estimate(summary),
            "开启后预计约多花 1 秒 + 每条素材 3 秒。",
        )

    def test_nonlinear_deltas_choose_the_measured_median_range(self) -> None:
        summary = summarize_le24_measurements(
            samples_with_deltas(
                {
                    1: (1_900, 2_000, 2_100),
                    2: (6_900, 7_000, 7_100),
                    3: (7_400, 7_500, 7_600),
                }
            )
        )

        self.assertEqual(summary.kind, "range")
        self.assertEqual(summary.base_ms, None)
        self.assertEqual(summary.per_material_ms, None)
        self.assertEqual(summary.range_ms, (2_000, 7_500))
        self.assertEqual(
            format_le24_estimate(summary),
            "开启后预计约多花 2～8 秒。",
        )

    def test_a_negative_intercept_uses_a_range_instead_of_rewriting_the_fit(
        self,
    ) -> None:
        summary = summarize_le24_measurements(
            samples_with_deltas(
                {
                    1: (900, 1_000, 1_100),
                    2: (2_900, 3_000, 3_100),
                    3: (4_900, 5_000, 5_100),
                }
            )
        )

        self.assertEqual(summary.kind, "range")
        self.assertEqual(summary.range_ms, (1_000, 5_000))

    def test_incomplete_or_nonpositive_paired_data_is_rejected(self) -> None:
        complete = samples_with_deltas(
            {
                1: (1_000, 1_100, 1_200),
                2: (2_000, 2_100, 2_200),
                3: (3_000, 3_100, 3_200),
            }
        )
        cases = (
            complete[:-1],
            [*complete, complete[0]],
            samples_with_deltas(
                {
                    1: (1_000, 1_100, 1_200),
                    2: (2_000, 2_100, 2_200),
                    3: (-100, 0, 100),
                }
            ),
        )
        for case in cases:
            with (
                self.subTest(case=case),
                self.assertRaisesRegex(
                    RuntimeError, "LE-24 paired measurements are invalid"
                ),
            ):
                summarize_le24_measurements(case)


if __name__ == "__main__":
    unittest.main()
