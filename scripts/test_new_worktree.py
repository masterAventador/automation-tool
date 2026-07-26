#!/usr/bin/env python3
"""A new worktree must be usable without re-materialising 879 MB of vendor.

Two properties this repository has already been bitten by, in that order:

* the copied submodule must be a **real directory** — a symlink is refused by
  `motion_style_freezer.py:105`, on purpose;
* it must **not share inodes** with the source. `cp -al` was used here on
  2026-07-26 and a later write in one worktree reached through the hardlink and
  corrupted the main tree's submodule.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from new_worktree import (  # noqa: E402
    clone_directory,
    gitdir_pointer,
    worktree_add_command,
)


class WorktreeAddCommand(unittest.TestCase):
    """A tree nobody can merge from is a tree that loses work.

    The script detached by default and only made a branch when asked. Two
    agents in a row committed onto that detached HEAD on 2026-07-26; both had
    to notice and run `git switch -c` themselves, and the one that did not was
    merged by raw SHA. A commit with no ref pointing at it is also collectable,
    so "detached by default" is not a neutral default — it is a way to lose
    work quietly. Since `CLAUDE.md` §8.1 makes this script the only sanctioned
    way to create a worktree, the default has to be the safe one.
    """

    def test_a_branch_named_after_the_worktree_is_created_by_default(self) -> None:
        command = worktree_add_command(Path("/repo/wt/sweep"), "origin/main", None)

        self.assertIn("-b", command)
        self.assertEqual("sweep", command[command.index("-b") + 1])

    def test_an_explicit_branch_name_wins(self) -> None:
        command = worktree_add_command(Path("/repo/wt/sweep"), "origin/main", "fix/x")

        self.assertEqual("fix/x", command[command.index("-b") + 1])

    def test_detaching_stays_available_for_read_only_trees(self) -> None:
        # A release build or a scan never commits, and a branch it would leave
        # behind is one more thing to clean up.
        command = worktree_add_command(Path("/repo/wt/scan"), "origin/main", "")

        self.assertNotIn("-b", command)


class GitdirPointer(unittest.TestCase):
    def test_matches_what_git_itself_writes_for_a_worktree_submodule(self) -> None:
        # Read off a real worktree on 2026-07-26:
        #   wt/release/vendor/hyperframes/.git contains exactly this line.
        self.assertEqual(
            "gitdir: ../../../../.git/worktrees/release/modules/vendor/hyperframes",
            gitdir_pointer("release", "vendor/hyperframes"),
        )

    def test_the_climb_out_follows_the_submodule_depth(self) -> None:
        # Two levels for `wt/<name>`, plus one per component of the submodule
        # path. A one-component submodule therefore climbs three, not four.
        self.assertEqual(
            "gitdir: ../../../.git/worktrees/ui/modules/thirdparty",
            gitdir_pointer("ui", "thirdparty"),
        )
        self.assertEqual(
            "gitdir: ../../../../../.git/worktrees/ui/modules/a/b/c",
            gitdir_pointer("ui", "a/b/c"),
        )


class CloneDirectory(unittest.TestCase):
    def _source(self, root: Path) -> Path:
        source = root / "source"
        (source / "nested").mkdir(parents=True)
        (source / "nested" / "payload").write_text("upstream", encoding="utf-8")
        return source

    def test_the_copy_is_a_real_directory_not_a_symlink(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._source(root)

            clone_directory(source, root / "copy")

            self.assertTrue((root / "copy").is_dir())
            self.assertFalse((root / "copy").is_symlink())

    def test_the_copy_does_not_share_inodes_with_the_source(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._source(root)

            clone_directory(source, root / "copy")

            original = (source / "nested" / "payload").stat().st_ino
            copied = (root / "copy" / "nested" / "payload").stat().st_ino
            self.assertNotEqual(original, copied)

    def test_writing_to_the_copy_leaves_the_source_alone(self) -> None:
        # The hardlink failure, expressed as a behaviour rather than a mechanism.
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._source(root)

            clone_directory(source, root / "copy")
            (root / "copy" / "nested" / "payload").write_text("edited", "utf-8")

            self.assertEqual(
                "upstream",
                (source / "nested" / "payload").read_text(encoding="utf-8"),
            )

    def test_the_content_arrives(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._source(root)

            clone_directory(source, root / "copy")

            self.assertEqual(
                "upstream",
                (root / "copy" / "nested" / "payload").read_text(encoding="utf-8"),
            )

    def test_it_reports_which_path_it_took(self) -> None:
        # Whether the filesystem supported cloning is worth printing: a run that
        # silently fell back to a full copy is a minute slower and the operator
        # should be able to tell which one happened.
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._source(root)

            method = clone_directory(source, root / "copy")

        self.assertIn(method, {"clone", "copy"})


if __name__ == "__main__":
    unittest.main()
