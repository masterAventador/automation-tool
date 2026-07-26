#!/usr/bin/env python3
"""A contract that names a file must name a file that exists.

On 2026-07-26 the motion authoring agent moved from `tools/motion-authoring/`
into `backend/src/automation_tool/executor/motion_authoring/`. The cleanup
commit deleted the copy left at the old path and left four references pointing
at it. One of them — a Node contract test — failed loudly with ENOENT. The
other three sat inside contract JSON, in the `definedIn` list that declares
which files the contract governs, and said nothing at all: a contract whose
governed-file list points at a path that does not exist governs nothing, and
looks exactly like one that governs everything.

That silence is the failure mode this repository keeps meeting under different
names. This test is the noise.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_contract_declared_paths import (  # noqa: E402
    CHECKED_KEYS,
    collect_broken_declarations,
)


def write_contract(root: Path, name: str, body: dict[str, object]) -> None:
    path = root / "contracts" / "video" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")


class CollectBrokenDeclarations(unittest.TestCase):
    def test_reports_a_definedIn_entry_whose_file_is_missing(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_contract(
                root,
                "moved.v1.json",
                {"definedIn": ["tools/gone/agent.py"]},
            )

            broken = collect_broken_declarations(root)

        self.assertEqual(
            [("contracts/video/moved.v1.json", "definedIn", "tools/gone/agent.py")],
            broken,
        )

    def test_accepts_a_definedIn_entry_whose_file_exists(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src" / "agent.py").write_text("", encoding="utf-8")
            write_contract(root, "present.v1.json", {"definedIn": ["src/agent.py"]})

            self.assertEqual([], collect_broken_declarations(root))

    def test_reports_a_missing_enforcedBy_script(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_contract(
                root,
                "unenforced.v1.json",
                {"enforcedBy": "scripts/test_gone.py"},
            )

            broken = collect_broken_declarations(root)

        self.assertEqual(
            [
                (
                    "contracts/video/unenforced.v1.json",
                    "enforcedBy",
                    "scripts/test_gone.py",
                )
            ],
            broken,
        )

    def test_a_directory_satisfies_a_declaration(self) -> None:
        # `definedIn` names a package directory in at least one contract.
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pkg" / "inner").mkdir(parents=True)
            write_contract(root, "package.v1.json", {"definedIn": ["pkg/inner"]})

            self.assertEqual([], collect_broken_declarations(root))

    def test_ignores_keys_whose_paths_are_not_repository_relative(self) -> None:
        # `path` is relative to a bundle root, `healthPath` is a URL path and
        # `excludedGlobs` holds globs. Checking them against the repository
        # would produce hundreds of false alarms, so they are out of scope —
        # and the checker says so rather than letting anyone assume otherwise.
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_contract(
                root,
                "other-keys.v1.json",
                {
                    "path": "_internal/runtime.dat",
                    "healthPath": "/api/v1/health",
                    "excludedGlobs": ["**/*.test.*"],
                },
            )

            self.assertEqual([], collect_broken_declarations(root))

    def test_the_checked_key_set_is_declared_and_non_empty(self) -> None:
        # A curated list goes stale silently. This one at least has to be
        # visible: the checker names the keys it covers so the blind spot is
        # stated rather than assumed away.
        self.assertIn("definedIn", CHECKED_KEYS)
        self.assertIn("enforcedBy", CHECKED_KEYS)


class Repository(unittest.TestCase):
    def test_no_contract_in_this_repository_names_a_missing_file(self) -> None:
        broken = collect_broken_declarations(ROOT)

        self.assertEqual(
            [],
            broken,
            "a contract names a file that does not exist; either the file moved "
            "and the contract was not updated, or the contract is governing "
            "nothing:\n"
            + "\n".join(f"  {c}\n    {k}: {v}" for c, k, v in broken),
        )


if __name__ == "__main__":
    unittest.main()
