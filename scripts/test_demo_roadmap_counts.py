#!/usr/bin/env python3
"""A ledger that cannot be counted will be miscounted, and nobody will notice.

`docs/demo-sprint-roadmap.md` drifted twice, and both times the wrong number was
reported to a human as fact:

- a task id sitting in two status tables, so every walk of the tables
  double-counted it;
- section headers carrying their own counts (`### ✅ 视频与内容（10）`) while the
  section held twelve rows — the same number in two places, one maintained.

Neither drift broke anything that ran, which is precisely why they survived.
These tests are the thing that notices.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_demo_roadmap_counts import (  # noqa: E402
    LEDGER,
    count_discrepancies,
)

CONSISTENT = """# 台账

## 进度总览

| 小节 | 数量 |
|---|---:|
| 甲区 | 2 |
| 乙区 | 1 |
| **小计：已收口** | **2** |
| **小计：未收口** | **1** |
| **去重后总计** | **3** |

## 甲区

| ID | 任务 |
|---|---|
| T1 | 一 |
| T2 | 二 |

## 乙区

| ID | 任务 |
|---|---|
| T3 | 三 |
"""


class CountDiscrepancies(unittest.TestCase):
    def test_a_consistent_ledger_reports_nothing(self) -> None:
        self.assertEqual([], count_discrepancies(CONSISTENT))

    def test_a_task_id_in_two_tables_is_reported(self) -> None:
        # The original defect: the same id counted twice by anything that
        # walks the tables, with no signal that it happened.
        drifted = CONSISTENT.replace("| T3 | 三 |", "| T3 | 三 |\n| T1 | 一 |")

        problems = count_discrepancies(drifted)

        self.assertEqual(1, len(problems), problems)
        self.assertIn("T1", problems[0])

    def test_a_total_that_disagrees_with_the_tables_is_reported(self) -> None:
        drifted = CONSISTENT.replace("| **去重后总计** | **3** |", "| **去重后总计** | **7** |")

        problems = count_discrepancies(drifted)

        self.assertTrue(
            any("7" in problem and "3" in problem for problem in problems), problems
        )

    def test_subtotals_that_do_not_add_up_to_the_total_are_reported(self) -> None:
        drifted = CONSISTENT.replace("| **小计：未收口** | **1** |", "| **小计：未收口** | **5** |")

        problems = count_discrepancies(drifted)

        self.assertTrue(any("小计" in problem for problem in problems), problems)

    def test_a_section_header_carrying_its_own_count_is_reported(self) -> None:
        # Two copies of one number is the drift generator itself, so it is a
        # failure even while the two copies still agree.
        drifted = CONSISTENT.replace("## 甲区", "## 甲区（2）")

        problems = count_discrepancies(drifted)

        self.assertTrue(any("自带计数" in problem for problem in problems), problems)

    def test_a_header_carrying_a_task_range_is_still_legal(self) -> None:
        # `（T73～T86）` names which ids live there; it is not a count and must
        # not be swept up by the rule above.
        ranged = CONSISTENT.replace("## 甲区", "## 甲区（T1～T2）")

        self.assertEqual([], count_discrepancies(ranged))

    def test_the_real_ledger_adds_up(self) -> None:
        """The live gate. This is the assertion that has to keep passing."""
        problems = count_discrepancies(LEDGER.read_text(encoding="utf-8"))

        self.assertEqual([], problems, "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
