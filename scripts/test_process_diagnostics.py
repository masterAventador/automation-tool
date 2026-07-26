#!/usr/bin/env python3
"""Tests for the builder-failure diagnostic.

The failure this exists for: on 2026-07-27 the Windows acceptance machine
could not build the media toolchain, and the message the operator got was
eight lines of curl progress bar. The reason — `No working C compiler found.`
— was in the same stream, a little further up, and had to be recovered by
running the builder by hand.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from process_diagnostics import builder_diagnostic  # noqa: E402


def _completed(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["builder"], returncode=1, stdout=stdout, stderr=stderr
    )


class ProgressBarsDoNotPushOutTheReason(unittest.TestCase):
    def test_a_carriage_return_progress_bar_counts_as_one_line(self) -> None:
        # What curl writes: one line, redrawn in place. `str.splitlines()`
        # treats every redraw as a line of its own — measured, twenty redraws
        # become twenty-two lines — so a tail of the last few keeps nothing but
        # the bar. Splitting on newlines only is what a terminal shows.
        progress = "\r".join(f"  {percent}% of 12.3M" for percent in range(0, 101))
        output = f"configure: No working C compiler found.\n{progress}\n"

        diagnostic = builder_diagnostic(_completed(stderr=output), lines_per_stream=3)

        self.assertIn("No working C compiler found", diagnostic)

    def test_only_the_final_state_of_a_redrawn_line_is_kept(self) -> None:
        output = "\r".join(["  1% of 12.3M", "  50% of 12.3M", "  100% of 12.3M"])

        diagnostic = builder_diagnostic(_completed(stderr=output))

        self.assertIn("100% of 12.3M", diagnostic)
        self.assertNotIn("1% of 12.3M", diagnostic)

    def test_both_streams_are_rendered_because_builders_split_across_them(
        self,
    ) -> None:
        diagnostic = builder_diagnostic(
            _completed(stdout="checking for gcc... no", stderr="configure failed")
        )

        self.assertIn("checking for gcc... no", diagnostic)
        self.assertIn("configure failed", diagnostic)

    def test_a_silent_builder_says_so_rather_than_returning_nothing(self) -> None:
        self.assertIn("no output", builder_diagnostic(_completed()))


if __name__ == "__main__":
    unittest.main()
