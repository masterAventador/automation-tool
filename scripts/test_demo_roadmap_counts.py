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

        # Asserts that this problem is reported, not that it is the only one:
        # duplicating a row also makes its section's row count disagree with
        # the overview, which is a real second defect that a later check
        # (added 2026-07-27) correctly reports. Pinning an exact problem count
        # would make every honest new check look like a regression.
        self.assertTrue(
            any("T1" in problem for problem in problems), problems
        )

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

    def test_a_subtotal_that_disagrees_with_its_own_section_is_reported(self) -> None:
        """The arithmetic can be perfect while every line of it is wrong.

        Found on 2026-07-27 by counting the sections by hand after the gate had
        just passed: the overview claimed 31 rows in one closed section and 41
        in another where the file held 29 and 42, and it claimed 11 un-closed
        against an actual 12. Every existing check was satisfied — the
        subtotals added to the total and the total matched the distinct ids —
        because a number moved out of one section and into another cancels out.
        A count that is only checked against other counts is not checked
        against the thing it is counting.
        """
        moved = CONSISTENT.replace("| 甲区 | 2 |", "| 甲区 | 3 |").replace(
            "| 乙区 | 1 |", "| 乙区 | 0 |"
        )
        reported = "\n".join(count_discrepancies(moved))
        self.assertIn("甲区", reported)
        self.assertIn("乙区", reported)

    def test_the_real_ledger_adds_up(self) -> None:
        """The live gate. This is the assertion that has to keep passing."""
        problems = count_discrepancies(LEDGER.read_text(encoding="utf-8"))

        self.assertEqual([], problems, "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
