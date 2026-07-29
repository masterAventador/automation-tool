"""`tsc -p tsconfig.json` type-checks nothing, and it looks exactly like success.

`frontend/tsconfig.json` is a solution file: `"files": []` plus two project
references. Pointing `tsc -p` at it therefore compiles an empty file list, exits
0, prints nothing, and is indistinguishable from a clean type check of the whole
front end.

Measured on 2026-07-29: three type errors sat in
`src/platform/tauri/material-video-studio-gateway.test.ts` for a whole working
session — `modelThinking` had become a required field and those call sites were
never updated — while `npx tsc --noEmit -p tsconfig.json` was being run after
every change and reported clean each time. They surfaced only when
`run_cq_01_acceptance.py` failed at `pnpm build:tauri:video-studio-test`, which
runs the real `tsc -b`.

So this gate refuses the invocation that cannot fail. The real entrypoints are
`tsc -b` (what every `build:*` script already uses) or `-p` against one of the
leaf configs.

The same shape as the rest of this line's incidents: not a wrong answer, an
answer to a question nobody asked.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
SOLUTION = FRONTEND / "tsconfig.json"

# Any file that tells a person or a machine how to type-check this project.
SEARCHED = [
    ROOT / "CLAUDE.md",
    FRONTEND / "package.json",
    *(p for p in sorted((ROOT / "scripts").glob("*.py")) if p.name != Path(__file__).name),
    *sorted((ROOT / ".github/workflows").glob("*.yml")),
]

_HOLLOW = re.compile(r"tsc[^\n\"']*-p\s+(?:\./)?tsconfig\.json")


class TypecheckEntrypointTests(unittest.TestCase):
    def test_the_solution_config_really_is_empty(self) -> None:
        """The premise, asserted rather than assumed.

        If someone ever gives the solution file its own `include`, this gate
        should stop complaining rather than keep enforcing a rule that no longer
        describes anything.
        """
        document = json.loads(SOLUTION.read_text(encoding="utf-8"))
        self.assertEqual(
            document.get("files"),
            [],
            "tsconfig.json is expected to be a solution file with no files of "
            "its own; if that changed, this gate needs rewriting rather than "
            "keeping",
        )
        self.assertTrue(document.get("references"), "a solution file must reference projects")

    def test_nothing_instructs_anyone_to_type_check_through_the_solution_file(self) -> None:
        offenders = [
            path.relative_to(ROOT).as_posix()
            for path in SEARCHED
            if path.is_file() and _HOLLOW.search(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual(
            offenders,
            [],
            "`tsc -p tsconfig.json` compiles an empty file list and exits 0, so it "
            "reports a clean type check of nothing at all. Use `tsc -b`, or point "
            f"`-p` at tsconfig.app.json / tsconfig.node.json: {offenders}",
        )


def main() -> int:
    result = unittest.main(module=__name__, exit=False, verbosity=0).result
    if not result.wasSuccessful():
        return 1
    print(f"typecheck entrypoint: {result.testsRun} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
