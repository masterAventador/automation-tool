"""A test class written after `unittest.main()` runs zero times and stays green.

Measured twice in two days on this repository:

- 2026-08-05, `test_build_release_package.py`: a new test class landed after
  the main guard; the suite printed its old count and OK, and the class never
  executed. Fixed in ec2fc7f7 — whose commit message spells the lesson out.
- Two commits later, `test_material_video_worker.py`: the exact same shape,
  and the four tests parked after the guard were the *entire* RED/GREEN
  evidence for the headless montage feature (REVIEW-2026-08-06, I2).

`python scripts/test_x.py` executes the module top to bottom, hits the guard,
and hands control to `unittest.main()` — which discovers only what is already
defined. Anything after the guard is dead code that looks exactly like a
passing test, so this gate refuses the shape itself: in every script-level
test file, the main guard must be the last statement of the module.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_TESTS = sorted((ROOT / "scripts").glob("test_*.py"))


def _main_guard_index(tree: ast.Module) -> int | None:
    for index, node in enumerate(tree.body):
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "__name__"
        ):
            return index
    return None


class DeadTestCodeTests(unittest.TestCase):
    def test_the_scan_sees_the_files_it_claims_to_police(self) -> None:
        """A glob that silently matches nothing would turn this gate hollow."""
        self.assertGreater(len(SCRIPT_TESTS), 10, "scripts/test_*.py glob found almost nothing")
        self.assertIn(ROOT / "scripts/test_material_video_worker.py", SCRIPT_TESTS)

    def test_no_statement_follows_the_main_guard(self) -> None:
        offenders: list[str] = []
        for path in SCRIPT_TESTS:
            if path.name == Path(__file__).name:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            guard = _main_guard_index(tree)
            if guard is None:
                continue  # Not every script test runs through a main guard.
            for node in tree.body[guard + 1 :]:
                offenders.append(f"{path.name}:{node.lineno} ({type(node).__name__})")
        self.assertEqual(
            offenders,
            [],
            "statements after `if __name__ == '__main__'` never execute when the "
            "file is run directly, so a test class parked there is permanently "
            f"green dead code — move it above the guard: {offenders}",
        )


def main() -> int:
    result = unittest.main(module=__name__, exit=False, verbosity=0).result
    if not result.wasSuccessful():
        return 1
    print(f"dead test code: {result.testsRun} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
