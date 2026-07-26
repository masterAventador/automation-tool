#!/usr/bin/env python3
"""A gate runner that can report success while a layer is red is worse than none.

On 2026-07-26 the release gate was run as a shell loop shaped like:

    echo "### eslint"; (cd frontend && pnpm lint 2>&1 | tail -5); echo "rc=$?"

`rc=$?` after a pipeline is the exit status of `tail`, which is always 0. The
log therefore read `rc=0` for eslint, for the backend suite and for the script
suite while all three were red — eslint had two errors, and the backend and
script suites were both dead at collection time on the same ImportError. The
run was one step away from being treated as "green, build the package"; the
only reason it was not is that the failures happened to also print words that
were noticed by eye.

That is the repository's own recurring failure mode — a check that reports
success because it never looked — reproduced in the thing that runs the checks.
These tests are what stops it happening again.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_full_regression import (  # noqa: E402
    Layer,
    LayerResult,
    failures,
    run_layer,
    summarise,
)

PASSES = Layer("passes", [sys.executable, "-c", "print('fine')"])
FAILS = Layer("fails", [sys.executable, "-c", "import sys; sys.exit(7)"])
MISSING = Layer("missing", ["a-command-that-does-not-exist-anywhere"])


class RunLayer(unittest.TestCase):
    def test_a_failing_command_keeps_its_own_exit_code(self) -> None:
        """The defect verbatim: a pipeline swallowed this and reported 0."""
        with TemporaryDirectory() as directory:
            result = run_layer(FAILS, Path(directory))

        self.assertEqual(7, result.returncode)

    def test_a_command_that_cannot_be_run_is_a_failure_not_a_skip(self) -> None:
        with TemporaryDirectory() as directory:
            result = run_layer(MISSING, Path(directory))

        self.assertNotEqual(0, result.returncode)

    def test_output_is_kept_so_a_failure_can_be_diagnosed(self) -> None:
        with TemporaryDirectory() as directory:
            result = run_layer(PASSES, Path(directory))

        self.assertIn("fine", result.output)


class Failures(unittest.TestCase):
    def test_every_non_zero_layer_is_reported(self) -> None:
        results = [
            LayerResult(PASSES, 0, ""),
            LayerResult(FAILS, 7, ""),
            LayerResult(MISSING, 127, ""),
        ]

        self.assertEqual(["fails", "missing"], [r.layer.name for r in failures(results)])

    def test_running_no_layers_at_all_is_itself_a_failure(self) -> None:
        # A run that checked nothing must never read as a pass. Every silent
        # gate in this repository has had exactly this shape.
        self.assertEqual(1, len(failures([])))


class Summarise(unittest.TestCase):
    def test_the_summary_cannot_say_green_while_a_layer_is_red(self) -> None:
        summary = summarise([LayerResult(PASSES, 0, ""), LayerResult(FAILS, 7, "")])

        self.assertIn("fails", summary)
        self.assertIn("7", summary)

    def test_a_clean_run_states_how_many_layers_ran(self) -> None:
        summary = summarise([LayerResult(PASSES, 0, ""), LayerResult(PASSES, 0, "")])

        self.assertIn("2", summary)


if __name__ == "__main__":
    unittest.main()
