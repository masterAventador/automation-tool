#!/usr/bin/env python3
"""Failure-matrix tests for the locked intelligent-material source bundle."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from build_moneyprinter_source_bundle import (
    REPOSITORY_ROOT,
    SourceBundleError,
    build_bundle,
)

SOURCE = REPOSITORY_ROOT / "vendor/moneyprinterturbo"
OFFICIAL_ORIGIN = "https://github.com/harry0703/MoneyPrinterTurbo.git"


def run(*command: str, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True, capture_output=True)


class MoneyPrinterSourceBundleTest(unittest.TestCase):
    def clone_source(self, destination: Path) -> Path:
        clone = destination / "source"
        run("git", "clone", "--quiet", "--shared", str(SOURCE), str(clone), cwd=destination)
        run("git", "remote", "set-url", "origin", OFFICIAL_ORIGIN, cwd=clone)
        return clone

    def test_build_is_deterministic_and_does_not_touch_source(self) -> None:
        before = subprocess.check_output(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=SOURCE,
            text=True,
        )
        with tempfile.TemporaryDirectory(prefix="im01-bundle-") as directory:
            root = Path(directory)
            first = build_bundle(SOURCE, root / "first.tar")
            second = build_bundle(SOURCE, root / "second.tar")
            self.assertEqual(first, second)
        after = subprocess.check_output(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=SOURCE,
            text=True,
        )
        self.assertEqual(before, after)

    def test_rejects_tracked_modification(self) -> None:
        with tempfile.TemporaryDirectory(prefix="im01-dirty-tracked-") as directory:
            root = Path(directory)
            source = self.clone_source(root)
            with (source / "README.md").open("a", encoding="utf-8") as stream:
                stream.write("\nlocal patch\n")
            with self.assertRaisesRegex(SourceBundleError, "工作树存在修改"):
                build_bundle(source, root / "bundle.tar")

    def test_rejects_untracked_patch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="im01-dirty-untracked-") as directory:
            root = Path(directory)
            source = self.clone_source(root)
            (source / "local.patch").write_text("patch", encoding="utf-8")
            with self.assertRaisesRegex(SourceBundleError, "工作树存在修改"):
                build_bundle(source, root / "bundle.tar")

    def test_rejects_output_inside_upstream(self) -> None:
        with tempfile.TemporaryDirectory(prefix="im01-output-") as directory:
            source = self.clone_source(Path(directory))
            with self.assertRaisesRegex(SourceBundleError, "输出不能写入"):
                build_bundle(source, source / "bundle.tar")


if __name__ == "__main__":
    unittest.main()
