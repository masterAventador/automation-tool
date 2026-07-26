#!/usr/bin/env python3
"""A packaged JavaScript runtime has to actually run JavaScript.

The failure this guards against shipped on 2026-07-26: both `node` binaries in
a signed, notarised package aborted during V8 initialisation because the
hardened runtime was applied without `com.apple.security.cs.allow-jit`. Every
existing gate passed, because every existing gate only looked at the files.
"""

from __future__ import annotations

import os
import stat
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_packaged_javascript_runtimes import (  # noqa: E402
    collect_runtime_failures,
    find_javascript_runtimes,
)


def make_runtime(bundle: Path, relative: str, script: str) -> Path:
    path = bundle / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


WORKING = "#!/bin/sh\nexit 0\n"
# Prints a version and exits 0, then dies on anything that needs the engine.
# This is exactly the shape of the real defect: `node --version` succeeded on a
# binary that could not evaluate a single expression.
VERSION_ONLY = '#!/bin/sh\nif [ "$1" = "--version" ]; then echo v22.0.0; exit 0; fi\nexit 133\n'


class FindJavascriptRuntimes(unittest.TestCase):
    def test_finds_a_runtime_nested_anywhere_in_the_bundle(self) -> None:
        with TemporaryDirectory() as directory:
            bundle = Path(directory)
            expected = make_runtime(
                bundle, "Contents/Resources/worker/runtime/node", WORKING
            )

            self.assertEqual([expected], find_javascript_runtimes(bundle))

    def test_ignores_a_file_that_is_not_executable(self) -> None:
        with TemporaryDirectory() as directory:
            bundle = Path(directory)
            path = bundle / "Contents/Resources/node"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("not a program", encoding="utf-8")
            path.chmod(0o644)

            self.assertEqual([], find_javascript_runtimes(bundle))


class CollectRuntimeFailures(unittest.TestCase):
    def test_a_runtime_that_cannot_evaluate_an_expression_is_reported(self) -> None:
        with TemporaryDirectory() as directory:
            bundle = Path(directory)
            make_runtime(bundle, "Contents/Resources/a/node", VERSION_ONLY)

            failures = collect_runtime_failures(bundle)

        self.assertEqual(1, len(failures), failures)
        self.assertEqual(133, failures[0].returncode)

    def test_a_working_runtime_is_not_reported(self) -> None:
        with TemporaryDirectory() as directory:
            bundle = Path(directory)
            make_runtime(bundle, "Contents/Resources/a/node", WORKING)

            self.assertEqual([], collect_runtime_failures(bundle))

    def test_finding_no_runtime_at_all_is_itself_a_failure(self) -> None:
        # A scan that matches nothing must not read as a pass. Every silent
        # gate in this repository has had exactly this shape: the check ran,
        # found nothing to check, and reported success.
        with TemporaryDirectory() as directory:
            bundle = Path(directory)
            (bundle / "Contents/Resources").mkdir(parents=True)

            failures = collect_runtime_failures(bundle)

        self.assertEqual(1, len(failures), failures)
        self.assertIn("no JavaScript runtime", failures[0].output)

    def test_every_broken_runtime_is_reported_not_just_the_first(self) -> None:
        with TemporaryDirectory() as directory:
            bundle = Path(directory)
            make_runtime(bundle, "Contents/Resources/a/node", VERSION_ONLY)
            make_runtime(bundle, "Contents/Resources/b/node", VERSION_ONLY)

            self.assertEqual(2, len(collect_runtime_failures(bundle)))


class SigningContract(unittest.TestCase):
    def test_every_component_shipping_a_runtime_declares_allow_jit(self) -> None:
        """The contract half of the same defect.

        Measured 2026-07-26 with a control group: the same `node`, whole
        directory copied so its sibling dylibs stayed intact, signed ad-hoc
        with `--options runtime` and no entitlements exits 133 with no output;
        signed with `--options runtime` plus allow-jit it exits 0 and prints.
        """
        import json

        contract = json.loads(
            (ROOT / "contracts/quality/macos-release-signing.v1.json").read_text(
                encoding="utf-8"
            )
        )
        components = contract["components"]
        shipping_a_runtime = ("local-executor", "motion-video-worker")

        missing = []
        for name in shipping_a_runtime:
            entitlements = components.get(name, {}).get("entitlements") or {}
            reasons = entitlements.get("reasons") or {}
            if "com.apple.security.cs.allow-jit" not in reasons:
                missing.append(name)

        self.assertEqual(
            [],
            missing,
            "these components ship a Node runtime and are signed with the "
            "hardened runtime, so without allow-jit their V8 cannot start: "
            f"{missing}",
        )



class SilentSkips(unittest.TestCase):
    """The two ways this gate quietly ignored a runtime.

    A package audit on 2026-07-26 built one good and three bad packages and got
    `all 1 ... evaluate an expression` with exit 0: three runtimes that exit 133
    — the exact code from the original incident — were skipped without a word.
    Two of the three were skipped by this file's own filters.

    That is the failure mode this gate was written to end, reproduced inside the
    gate. `os.access(X_OK)` and the symlink test read like tidy hygiene; both are
    silent drops.
    """

    def test_a_runtime_without_its_executable_bit_is_a_failure_not_a_skip(self) -> None:
        with TemporaryDirectory() as directory:
            bundle = Path(directory)
            path = bundle / "Contents/Resources/a/node"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(WORKING, encoding="utf-8")
            path.chmod(0o644)

            failures = collect_runtime_failures(bundle)

        self.assertEqual(1, len(failures), failures)
        self.assertIn("not executable", failures[0].output)

    def test_a_symlinked_runtime_is_a_failure_not_a_skip(self) -> None:
        with TemporaryDirectory() as directory:
            bundle = Path(directory)
            real = make_runtime(bundle, "Contents/Resources/real/node", WORKING)
            link = bundle / "Contents/Resources/b/node"
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(real)

            failures = collect_runtime_failures(bundle)

        self.assertEqual(1, len(failures), failures)
        self.assertIn("symlink", failures[0].output)


class JitGrantCoverage(unittest.TestCase):
    def test_the_report_names_every_binary_granted_allow_jit(self) -> None:
        """State the gap instead of leaving it invisible.

        The audit found eleven binaries carrying `allow-jit` in the shipped
        package while this gate exercised two. Nine unexercised JIT grants is
        not necessarily wrong — but it must be a number someone can read, not
        something you discover by building a broken package on purpose.
        """
        from check_packaged_javascript_runtimes import summarise_jit_grants

        with TemporaryDirectory() as directory:
            bundle = Path(directory)
            make_runtime(bundle, "Contents/Resources/a/node", WORKING)

            summary = summarise_jit_grants(bundle, exercised=1)

        self.assertIn("exercised 1", summary)
        self.assertIn("allow-jit", summary)

if __name__ == "__main__":
    unittest.main()
