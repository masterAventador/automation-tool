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
from unittest import mock
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_packaged_javascript_runtimes  # noqa: E402
from check_packaged_javascript_runtimes import (  # noqa: E402
    collect_runtime_failures,
    find_embedded_browsers,
    find_javascript_runtimes,
    format_jit_grant_summary,
    probe_embedded_browsers,
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

# Where a Chrome for Testing bundle puts its browser process and its helpers.
# The helper is the one that holds the page's V8 isolate, so it lives under
# `Frameworks/` and must not be mistaken for a second browser to launch.
BROWSER_RELATIVE = (
    "Contents/Resources/embedded-browser/chrome-mac-arm64/"
    "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
)
HELPER_RELATIVE = (
    "Contents/Resources/embedded-browser/chrome-mac-arm64/"
    "Google Chrome for Testing.app/Contents/Frameworks/"
    "Google Chrome for Testing Framework.framework/Versions/149.0.0.0/Helpers/"
    "Google Chrome for Testing Helper (Renderer).app/Contents/MacOS/"
    "Google Chrome for Testing Helper (Renderer)"
)
# The browser-shaped version of the same defect: `--version` answers, the
# engine does not. The stderr is the text the real hardened-runtime failure
# writes, because a gate that swallows it makes its reader re-run the command
# by hand — which is exactly what happened on 2026-07-26.
VERSION_ONLY_BROWSER = (
    "#!/bin/sh\n"
    'if [ "$1" = "--version" ]; then echo "Google Chrome for Testing 149.0.0.0"; exit 0; fi\n'
    'echo "Check failed: false. V8 aborted inside Isolate::Init" >&2\n'
    "exit 133\n"
)
# What a real Chromium does when its renderer helper carries the hardened
# runtime without allow-jit: measured on the 2026-07-26 package, the browser
# process goes down with SIGKILL and says nothing at all.
SUICIDAL_BROWSER = "#!/bin/sh\nkill -9 $$\n"
# Speaks, then hangs forever without opening a DevTools endpoint. Whatever it
# said has to reach the report even though it never exits on its own.
# The sleep deliberately outlasts every timeout in the gate: a report that
# only arrives because the fixture happened to exit on its own has proved
# nothing about a browser that hangs indefinitely.
TALKATIVE_HANGING_BROWSER = (
    "#!/bin/sh\n"
    'echo "Check failed: false. V8 aborted inside Isolate::Init" >&2\n'
    "exec sleep 600\n"
)


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
        summary = format_jit_grant_summary(
            granted=[Path("/pkg/node"), Path("/pkg/chrome")],
            exercised=[Path("/pkg/node")],
        )

        self.assertIn("exercised 1", summary)
        self.assertIn("allow-jit", summary)

    def test_the_report_names_the_grants_it_did_not_exercise(self) -> None:
        """A count alone still hides *which* binary went unchecked.

        `exercised 2 of 11` was true and useless: it took building a broken
        package on purpose to learn that the embedded Chromium was one of the
        nine. Naming them is the difference between a number and a lead.
        """
        summary = format_jit_grant_summary(
            granted=[Path("/pkg/node"), Path("/pkg/chrome")],
            exercised=[Path("/pkg/node")],
        )

        self.assertIn("not exercised", summary)
        self.assertIn("chrome", summary)

    def test_full_coverage_says_so_instead_of_listing_nothing(self) -> None:
        summary = format_jit_grant_summary(
            granted=[Path("/pkg/node")], exercised=[Path("/pkg/node")]
        )

        self.assertNotIn("not exercised", summary)


class FindEmbeddedBrowsers(unittest.TestCase):
    """The largest JavaScript engine in the package, found by its own layout."""

    def test_finds_the_browser_executable_and_not_its_helpers(self) -> None:
        with TemporaryDirectory() as directory:
            bundle = Path(directory)
            expected = make_runtime(bundle, BROWSER_RELATIVE, WORKING)
            make_runtime(bundle, HELPER_RELATIVE, WORKING)

            self.assertEqual([expected], find_embedded_browsers(bundle))

    def test_a_package_with_no_embedded_browser_finds_nothing(self) -> None:
        with TemporaryDirectory() as directory:
            bundle = Path(directory)
            make_runtime(bundle, "Contents/Resources/a/node", WORKING)

            self.assertEqual([], find_embedded_browsers(bundle))


class ProbeEmbeddedBrowsers(unittest.TestCase):
    """Launching is not evidence. Evaluating is.

    `node --version` succeeded on a binary that could not run JavaScript, and a
    browser is the same story one process deeper: the parent can start, print a
    version and answer for a while, while the renderer — the process that
    actually holds a V8 isolate, and the one the signing contract grants
    allow-jit for — dies the moment a page is created.
    """

    def test_a_browser_that_dies_instead_of_serving_devtools_is_reported(self) -> None:
        with TemporaryDirectory() as directory:
            bundle = Path(directory)
            make_runtime(bundle, BROWSER_RELATIVE, VERSION_ONLY_BROWSER)

            probe = probe_embedded_browsers(bundle)

        self.assertEqual(1, len(probe.failures), probe.failures)
        self.assertEqual(133, probe.failures[0].returncode)

    def test_the_failure_carries_the_stderr_that_explains_it(self) -> None:
        """The 2026-07-26 incident lost its own cause to `capture_output=True`.

        The real reason — V8 aborting inside `Isolate::Init` — was recovered by
        a human re-running the command by hand. A gate that reports only
        "it failed" has made its reader do that work again.
        """
        with TemporaryDirectory() as directory:
            bundle = Path(directory)
            make_runtime(bundle, BROWSER_RELATIVE, VERSION_ONLY_BROWSER)

            probe = probe_embedded_browsers(bundle)

        self.assertEqual(1, len(probe.failures), probe.failures)
        self.assertIn("Isolate::Init", probe.failures[0].output)
        self.assertIn(BROWSER_RELATIVE, probe.failures[0].path)

    def test_a_package_without_an_embedded_browser_is_a_failure_not_a_pass(self) -> None:
        # Same shape as `test_finding_no_runtime_at_all_is_itself_a_failure`.
        # A browser gate that finds no browser has checked nothing.
        with TemporaryDirectory() as directory:
            bundle = Path(directory)
            (bundle / "Contents/Resources").mkdir(parents=True)

            probe = probe_embedded_browsers(bundle)

        self.assertEqual(1, len(probe.failures), probe.failures)
        self.assertIn("no embedded browser", probe.failures[0].output)

    def test_a_browser_killed_by_a_signal_says_which_signal(self) -> None:
        """`exit -9` is a number the reader has to decode. Name it.

        Measured on a real package: a Chromium whose renderer helper was signed
        with the hardened runtime and no allow-jit takes the whole browser down
        with SIGKILL. "exit -9" is the entire diagnosis unless the gate spells
        it out.
        """
        with TemporaryDirectory() as directory:
            bundle = Path(directory)
            make_runtime(bundle, BROWSER_RELATIVE, SUICIDAL_BROWSER)

            probe = probe_embedded_browsers(bundle)

        self.assertEqual(1, len(probe.failures), probe.failures)
        self.assertIn("SIGKILL", probe.failures[0].output)

    def test_output_survives_a_browser_that_had_to_be_terminated(self) -> None:
        """The incident's real mistake, one layer up.

        On 2026-07-26 `capture_output=True` swallowed the abort message. A pipe
        that is only drained after the process exits has the same defect for a
        browser that never exits: it hangs, the gate gives up, and whatever the
        browser said about why is discarded with the pipe.
        """
        with TemporaryDirectory() as directory:
            bundle = Path(directory)
            make_runtime(bundle, BROWSER_RELATIVE, TALKATIVE_HANGING_BROWSER)

            with mock.patch.object(
                check_packaged_javascript_runtimes,
                "BROWSER_LAUNCH_TIMEOUT_SECONDS",
                2,
            ):
                probe = probe_embedded_browsers(bundle)

        self.assertEqual(1, len(probe.failures), probe.failures)
        self.assertIn("Isolate::Init", probe.failures[0].output)

    def test_a_failed_probe_reports_no_binary_as_exercised(self) -> None:
        # The coverage number must never count a binary that did not run.
        with TemporaryDirectory() as directory:
            bundle = Path(directory)
            make_runtime(bundle, BROWSER_RELATIVE, VERSION_ONLY_BROWSER)

            probe = probe_embedded_browsers(bundle)

        self.assertEqual((), probe.executed)


if __name__ == "__main__":
    unittest.main()
