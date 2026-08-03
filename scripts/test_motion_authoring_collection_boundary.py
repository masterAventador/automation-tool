#!/usr/bin/env python3
"""COV-01: the motion authoring tests must sit inside the backend coverage boundary.

118 deterministic tests really execute `automation_tool.executor.motion_authoring`,
but they lived under `scripts/`, and `backend/pyproject.toml` sets
`testpaths = ["tests"]` -- so the backend coverage run never collected them and
their 359 covered points read as debt.

Moving them is only half of it. `scripts/test_motion_authoring_agent.py` is still
named in two contracts' `enforcedBy` and is still discovered by
`run_script_tests.py` (which globs `scripts/test_*.py` rather than keeping a
list), so the entry has to keep working. What must NOT happen is a second copy of
the assertions: two files drifting apart is worse than one file in the wrong
place.

These checks therefore *run* the compatibility entry rather than grepping it. A
string assertion that the entry "mentions" the canonical path would pass just as
happily against an entry that imports nothing and asserts nothing -- which is
exactly the failure mode this gate exists to catch.
"""

from __future__ import annotations

import ast
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
CANONICAL = BACKEND / "tests/unit/executor/test_motion_authoring_agent.py"
COMPAT_ENTRY = ROOT / "scripts/test_motion_authoring_agent.py"
INTERPRETER = BACKEND / ".venv/bin/python"

# The canonical module is loaded under this name so that `unittest -v` output
# names it. That string appearing in the entry's own output is the proof that the
# entry executed the canonical file, not a local copy of it.
SENTINEL_MODULE = "canonical_motion_authoring_agent_tests"

MINIMUM_TESTS = 118

_UNITTEST_RAN = re.compile(r"^Ran\s+(?P<count>\d+)\s+tests?\s+in\s+", flags=re.MULTILINE)
_PYTEST_COLLECTED = re.compile(r"(?P<count>\d+)\s+tests?\s+collected")


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
    )


class MotionAuthoringCollectionBoundaryTests(unittest.TestCase):
    def test_canonical_tests_are_collected_by_the_backend_pytest_run(self) -> None:
        """The whole point of COV-01: the backend coverage run must see them."""
        self.assertTrue(
            CANONICAL.is_file(),
            f"canonical motion authoring tests are missing at {CANONICAL}",
        )

        collected = _run(
            [
                str(INTERPRETER),
                "-m",
                "pytest",
                str(CANONICAL.relative_to(BACKEND)),
                "--collect-only",
                "-q",
                "-p",
                "no:cacheprovider",
            ],
            cwd=BACKEND,
        )
        self.assertEqual(
            collected.returncode,
            0,
            f"pytest could not collect the canonical tests:\n{collected.stdout}\n{collected.stderr}",
        )
        match = _PYTEST_COLLECTED.search(collected.stdout)
        self.assertIsNotNone(match, f"no collection count in:\n{collected.stdout}")
        assert match is not None
        self.assertGreaterEqual(
            int(match.group("count")),
            MINIMUM_TESTS,
            "the canonical file must carry the full deterministic suite",
        )

    def test_compatibility_entry_really_executes_the_canonical_suite(self) -> None:
        """`run_script_tests.py` invokes `<python> <script>`; that must still work."""
        self.assertTrue(COMPAT_ENTRY.is_file(), "compatibility entry was deleted")

        completed = _run([str(INTERPRETER), str(COMPAT_ENTRY)], cwd=ROOT)
        output = completed.stdout + completed.stderr

        self.assertEqual(completed.returncode, 0, f"compatibility entry failed:\n{output}")

        ran = _UNITTEST_RAN.search(output)
        self.assertIsNotNone(
            ran,
            "run_script_tests.py rejects a run that reports no executed-check count; "
            f"the entry printed no `Ran N tests` line:\n{output}",
        )
        assert ran is not None
        self.assertGreaterEqual(
            int(ran.group("count")),
            MINIMUM_TESTS,
            f"the entry ran fewer tests than the canonical suite:\n{output}",
        )

        self.assertIn(
            SENTINEL_MODULE,
            output,
            "the entry produced test names that do not come from the canonical "
            "module, so it is running its own copy of the assertions",
        )

    def test_compatibility_entry_holds_no_assertions_of_its_own(self) -> None:
        """One source of truth. A forwarding entry defines nothing and asserts nothing.

        Read as AST rather than grepped: a substring check for `class ` fires on
        the word appearing in a docstring and misses a definition written any
        other way. Shape is not semantics.
        """
        tree = ast.parse(COMPAT_ENTRY.read_text(encoding="utf-8"))

        defined = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
        self.assertEqual(
            defined,
            [],
            "the entry defines its own test cases instead of forwarding to the canonical file",
        )

        asserted = [
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr.startswith("assert")
        ]
        self.assertEqual(asserted, [], "assertions belong in the canonical file only")

    def test_canonical_file_no_longer_patches_sys_path(self) -> None:
        """Inside `backend/tests` the package is already importable via the venv."""
        source = CANONICAL.read_text(encoding="utf-8")

        self.assertNotIn(
            'sys.path.insert(0, str(ROOT / "backend/src"))',
            source,
            "the sys.path shim was only needed while the file lived under scripts/",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
