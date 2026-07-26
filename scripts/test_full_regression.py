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
    LAYERS,
    venv_python,
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


class TheBackendLayerRunsOnBothPlatforms(unittest.TestCase):
    """The backend layer has to name an interpreter that exists where it runs.

    It was the only layer with a path written into it — every other one uses
    `sys.executable` or a tool on PATH. `.venv/bin/python` is the POSIX layout;
    Windows puts it at `.venv/Scripts/python.exe`. Measured 2026-07-27 on the
    Windows acceptance machine: running this aggregate there produced a red
    backend layer that had nothing to do with the product, and the reader had
    to know that before trusting anything else in the run. A gate whose own
    failure looks exactly like the thing it guards is worse than no gate.
    """

    def test_the_interpreter_follows_the_platform_layout(self) -> None:
        self.assertEqual(venv_python("posix"), str(Path(".venv") / "bin" / "python"))
        self.assertEqual(
            venv_python("nt"), str(Path(".venv") / "Scripts" / "python.exe")
        )

    def test_the_backend_interpreter_exists_on_this_machine(self) -> None:
        # The check a string comparison cannot make. Tried first: assert no
        # layer's command *starts with* `.venv`. It fails on the fix as well as
        # on the bug, because the derived value looks exactly like the literal
        # it replaced — the difference is where it comes from, which is not
        # visible in the string.
        backend = next(layer for layer in LAYERS if layer.name == "backend")
        interpreter = ROOT / backend.directory / backend.command[0]
        self.assertTrue(
            interpreter.is_file(),
            f"the backend layer names an interpreter that is not here: {interpreter}",
        )


if __name__ == "__main__":
    unittest.main()
