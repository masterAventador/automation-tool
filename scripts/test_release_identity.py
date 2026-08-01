#!/usr/bin/env python3
"""Tests for the source identity embedded into every signed release App."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_identity  # noqa: E402
from release_identity import repository_source_facts  # noqa: E402


class RepositorySourceIdentityTests(unittest.TestCase):
    def test_source_commit_ancestry_accepts_only_a_real_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", root], check=True)
            (root / "product.py").write_text("one\n", encoding="utf-8")
            subprocess.run(["git", "-C", root, "add", "product.py"], check=True)
            commit = [
                "git",
                "-C",
                os.fspath(root),
                "-c",
                "user.name=Release Test",
                "-c",
                "user.email=release@example.invalid",
                "commit",
                "-qm",
            ]
            subprocess.run([*commit, "first"], check=True)
            first = subprocess.run(
                ["git", "-C", root, "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            (root / "product.py").write_text("two\n", encoding="utf-8")
            subprocess.run(["git", "-C", root, "add", "product.py"], check=True)
            subprocess.run([*commit, "second"], check=True)
            second = subprocess.run(
                ["git", "-C", root, "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            self.assertTrue(
                release_identity.source_commit_is_ancestor(root, first, second)
            )
            self.assertFalse(
                release_identity.source_commit_is_ancestor(root, second, first)
            )

    def test_identity_rejects_a_symlink_that_escapes_the_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "root"
            outside = base / "outside.txt"
            subprocess.run(["git", "init", "-q", root], check=True)
            outside.write_text("mutable outside input\n", encoding="utf-8")
            (root / "product.py").symlink_to(outside)
            subprocess.run(["git", "-C", root, "add", "product.py"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    root,
                    "-c",
                    "user.name=Release Test",
                    "-c",
                    "user.email=release@example.invalid",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                check=True,
            )

            with self.assertRaisesRegex(
                release_identity.ReleaseIdentityRejected,
                "symlink",
            ):
                repository_source_facts(root)

    def test_identity_allows_an_internal_relative_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "root"
            snapshot = base / "snapshot"
            subprocess.run(["git", "init", "-q", root], check=True)
            (root / "shared").mkdir()
            (root / "shared/payload.txt").write_text("reviewed\n", encoding="utf-8")
            (root / "product").symlink_to("shared", target_is_directory=True)
            subprocess.run(["git", "-C", root, "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    root,
                    "-c",
                    "user.name=Release Test",
                    "-c",
                    "user.email=release@example.invalid",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                check=True,
            )

            expected = repository_source_facts(root)
            release_identity.materialize_repository_snapshot(
                root,
                snapshot,
                expected=expected,
            )

            self.assertTrue((snapshot / "product").is_symlink())
            self.assertEqual(
                (snapshot / "product/payload.txt").read_text(encoding="utf-8"),
                "reviewed\n",
            )
            self.assertEqual(repository_source_facts(snapshot), expected)

    def test_materialized_snapshot_applies_staged_deletion_and_rename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "root"
            snapshot = base / "snapshot"
            subprocess.run(["git", "init", "-q", root], check=True)
            (root / "deleted.txt").write_text("remove me\n", encoding="utf-8")
            (root / "renamed-before.txt").write_text("keep me\n", encoding="utf-8")
            subprocess.run(["git", "-C", root, "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    root,
                    "-c",
                    "user.name=Release Test",
                    "-c",
                    "user.email=release@example.invalid",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                check=True,
            )
            subprocess.run(["git", "-C", root, "rm", "-q", "deleted.txt"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    root,
                    "mv",
                    "renamed-before.txt",
                    "renamed-after.txt",
                ],
                check=True,
            )
            expected = repository_source_facts(root)

            release_identity.materialize_repository_snapshot(
                root,
                snapshot,
                expected=expected,
            )

            self.assertFalse((snapshot / "deleted.txt").exists())
            self.assertFalse((snapshot / "renamed-before.txt").exists())
            self.assertEqual(
                (snapshot / "renamed-after.txt").read_text(encoding="utf-8"),
                "keep me\n",
            )
            self.assertEqual(repository_source_facts(snapshot), expected)

    def test_materialized_snapshot_is_detached_from_later_source_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "root"
            snapshot = base / "snapshot"
            subprocess.run(["git", "init", "-q", root], check=True)
            (root / ".gitignore").write_text("cache/\n", encoding="utf-8")
            (root / "product.py").write_text("STATE = 'reviewed'\n", encoding="utf-8")
            (root / "untracked.py").write_text("VALUE = 1\n", encoding="utf-8")
            subprocess.run(["git", "-C", root, "add", ".gitignore", "product.py"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    root,
                    "-c",
                    "user.name=Release Test",
                    "-c",
                    "user.email=release@example.invalid",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                check=True,
            )
            expected = repository_source_facts(root)

            release_identity.materialize_repository_snapshot(
                root,
                snapshot,
                expected=expected,
            )
            (root / "product.py").write_text("STATE = 'temporary-drift'\n", encoding="utf-8")
            (root / "untracked.py").write_text("VALUE = 2\n", encoding="utf-8")

            self.assertEqual(repository_source_facts(snapshot), expected)
            self.assertEqual(
                (snapshot / "product.py").read_text(encoding="utf-8"),
                "STATE = 'reviewed'\n",
            )
            self.assertEqual(
                (snapshot / "untracked.py").read_text(encoding="utf-8"),
                "VALUE = 1\n",
            )

    def test_identity_tracks_committed_and_untracked_release_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", root], check=True)
            (root / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
            (root / "tracked.txt").write_text("one\n", encoding="utf-8")
            subprocess.run(["git", "-C", root, "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    root,
                    "-c",
                    "user.name=Release Test",
                    "-c",
                    "user.email=release@example.invalid",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                check=True,
            )

            first = repository_source_facts(root)
            self.assertRegex(first.git_commit, r"^[0-9a-f]{40}$")
            self.assertRegex(first.tree_sha256, r"^[0-9a-f]{64}$")

            (root / "untracked.txt").write_text("new input\n", encoding="utf-8")
            second = repository_source_facts(root)
            self.assertNotEqual(second.tree_sha256, first.tree_sha256)
            self.assertEqual(second.git_commit, first.git_commit)

            (root / "ignored.txt").write_text("local cache\n", encoding="utf-8")
            third = repository_source_facts(root)
            self.assertEqual(third, second)

            (root / "tracked.txt").write_text("two\n", encoding="utf-8")
            fourth = repository_source_facts(root)
            self.assertNotEqual(fourth.tree_sha256, third.tree_sha256)

            (root / "tracked.txt").unlink()
            deleted = repository_source_facts(root)
            self.assertNotEqual(deleted.tree_sha256, fourth.tree_sha256)
            self.assertEqual(deleted.git_commit, fourth.git_commit)

    def test_identity_hashes_a_clean_gitlink_and_rejects_submodule_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            child = base / "child"
            root = base / "root"
            subprocess.run(["git", "init", "-q", child], check=True)
            (child / "payload.txt").write_text("locked\n", encoding="utf-8")
            subprocess.run(["git", "-C", child, "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    child,
                    "-c",
                    "user.name=Release Test",
                    "-c",
                    "user.email=release@example.invalid",
                    "commit",
                    "-qm",
                    "child fixture",
                ],
                check=True,
            )

            subprocess.run(["git", "init", "-q", root], check=True)
            (root / "tracked.txt").write_text("root\n", encoding="utf-8")
            subprocess.run(["git", "-C", root, "add", "tracked.txt"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    root,
                    "-c",
                    "protocol.file.allow=always",
                    "submodule",
                    "add",
                    "-q",
                    os.fspath(child),
                    "vendor/child",
                ],
                check=True,
            )
            subprocess.run(["git", "-C", root, "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    root,
                    "-c",
                    "user.name=Release Test",
                    "-c",
                    "user.email=release@example.invalid",
                    "commit",
                    "-qm",
                    "root fixture",
                ],
                check=True,
            )

            clean = repository_source_facts(root)
            self.assertRegex(clean.tree_sha256, r"^[0-9a-f]{64}$")

            (root / "vendor/child/payload.txt").write_text("dirty\n", encoding="utf-8")
            with self.assertRaisesRegex(Exception, "submodule"):
                repository_source_facts(root)

            subprocess.run(
                ["git", "-C", root / "vendor/child", "checkout", "--", "payload.txt"],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    root / "vendor/child",
                    "update-index",
                    "--assume-unchanged",
                    "payload.txt",
                ],
                check=True,
            )
            (root / "vendor/child/payload.txt").write_text(
                "hidden working-tree drift\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                release_identity.ReleaseIdentityRejected,
                "index visibility",
            ):
                repository_source_facts(root)

            subprocess.run(
                [
                    "git",
                    "-C",
                    root / "vendor/child",
                    "update-index",
                    "--no-assume-unchanged",
                    "payload.txt",
                ],
                check=True,
            )
            subprocess.run(
                ["git", "-C", root / "vendor/child", "checkout", "--", "payload.txt"],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    root / "vendor/child",
                    "update-index",
                    "--skip-worktree",
                    "payload.txt",
                ],
                check=True,
            )
            with self.assertRaisesRegex(
                release_identity.ReleaseIdentityRejected,
                "index visibility",
            ):
                repository_source_facts(root)

    def test_materialized_snapshot_preserves_real_submodule_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            child = base / "child"
            root = base / "root"
            snapshot = base / "snapshot"
            locked_origin = "https://github.com/example/release-fixture.git"
            subprocess.run(["git", "init", "-q", child], check=True)
            (child / "payload.txt").write_text("locked\n", encoding="utf-8")
            subprocess.run(["git", "-C", child, "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    child,
                    "-c",
                    "user.name=Release Test",
                    "-c",
                    "user.email=release@example.invalid",
                    "commit",
                    "-qm",
                    "child fixture",
                ],
                check=True,
            )
            subprocess.run(["git", "init", "-q", root], check=True)
            (root / "tracked.txt").write_text("root\n", encoding="utf-8")
            subprocess.run(["git", "-C", root, "add", "tracked.txt"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    root,
                    "-c",
                    "protocol.file.allow=always",
                    "submodule",
                    "add",
                    "-q",
                    os.fspath(child),
                    "vendor/child",
                ],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    root / "vendor/child",
                    "remote",
                    "set-url",
                    "origin",
                    locked_origin,
                ],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    root,
                    "config",
                    "--file",
                    ".gitmodules",
                    "submodule.vendor/child.url",
                    locked_origin,
                ],
                check=True,
            )
            subprocess.run(["git", "-C", root, "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    root,
                    "-c",
                    "user.name=Release Test",
                    "-c",
                    "user.email=release@example.invalid",
                    "commit",
                    "-qm",
                    "root fixture",
                ],
                check=True,
            )
            expected = repository_source_facts(root)

            release_identity.materialize_repository_snapshot(
                root,
                snapshot,
                expected=expected,
            )

            materialized = snapshot / "vendor/child"
            self.assertTrue((materialized / ".git").is_file())
            self.assertEqual(
                subprocess.run(
                    ["git", "-C", materialized, "remote", "get-url", "origin"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
                locked_origin,
            )
            self.assertEqual(
                subprocess.run(
                    ["git", "-C", materialized, "status", "--porcelain"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout,
                "",
            )
            tags = subprocess.run(
                ["git", "-C", materialized, "ls-files", "-v"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
            self.assertTrue(tags)
            self.assertTrue(all(line.startswith("H ") for line in tags))

    def test_post_acceptance_ledgers_do_not_change_the_built_source_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", root], check=True)
            (root / "src").mkdir()
            (root / "docs/development").mkdir(parents=True)
            (root / "src/product.py").write_text("STATE = 'ready'\n", encoding="utf-8")
            (root / "docs/development/EB-11.md").write_text(
                "status: RED\n",
                encoding="utf-8",
            )
            roadmap = root / "docs/embedded-browser-video-studio-roadmap.md"
            roadmap.write_text("EB-11 | RED\n", encoding="utf-8")
            subprocess.run(["git", "-C", root, "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    root,
                    "-c",
                    "user.name=Release Test",
                    "-c",
                    "user.email=release@example.invalid",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                check=True,
            )

            built = repository_source_facts(root)
            (root / "docs/development/EB-11.md").write_text(
                "status: complete\n",
                encoding="utf-8",
            )
            roadmap.write_text("EB-11 | complete\n", encoding="utf-8")
            after_acceptance = repository_source_facts(root)
            self.assertEqual(after_acceptance, built)

            (root / "src/product.py").write_text("STATE = 'changed'\n", encoding="utf-8")
            self.assertNotEqual(repository_source_facts(root), built)


if __name__ == "__main__":
    unittest.main()
