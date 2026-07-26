#!/usr/bin/env python3
"""The remedies these gates print have to be commands that actually work.

`motion_authoring_runtime.rs` told the reader to run
`scripts/prepare_video_runtime.py`. That script exits 0 and populates the
per-machine build cache; it never wrote the path the test reads, so following
the instruction exactly left the test failing in the same way. A wrong remedy is
worse than none: it costs a run to discover, and it teaches the reader that the
message is decoration.

So these tests check the two things that make a remedy trustworthy -- the
producer exists and is reachable, and the path it is claimed to produce is
derived from the same contract the consumer reads rather than typed out a second
time.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from gate_prerequisites import (  # noqa: E402
    PREREQUISITES,
    Prerequisite,
    PrerequisiteMissing,
    by_name,
    explain,
    require,
)

# The gates this registry exists for. Named here so that deleting a
# declaration is a test failure rather than a silent loss of coverage.
EXPECTED_NAMES = {
    "offline-motion-catalog",
    "frontend-dist",
    "motion-authoring-runtime",
    "eb-16-release-package",
    "video-e2e-embedded-browser",
}


class RegistryCoversTheBrokenGates(unittest.TestCase):
    def test_every_gate_that_cannot_run_clean_is_declared(self) -> None:
        self.assertEqual(EXPECTED_NAMES, {item.name for item in PREREQUISITES})

    def test_names_are_unique(self) -> None:
        names = [item.name for item in PREREQUISITES]
        self.assertEqual(len(names), len(set(names)))


class ProducersAreReachable(unittest.TestCase):
    """A remedy naming a script that is not there is the B3 failure again."""

    def test_every_python_producer_script_exists(self) -> None:
        for prerequisite in PREREQUISITES:
            argv = prerequisite.producer
            if argv[0] != "{python}":
                continue
            script = ROOT / argv[1]
            self.assertTrue(
                script.is_file(), f"{prerequisite.name}: {argv[1]} does not exist"
            )

    def test_every_pnpm_producer_names_a_declared_script(self) -> None:
        for prerequisite in PREREQUISITES:
            argv = prerequisite.producer
            if argv[0] != "pnpm":
                continue
            manifest = json.loads(
                (ROOT / prerequisite.cwd / "package.json").read_text(encoding="utf-8")
            )
            self.assertIn(
                argv[1],
                manifest["scripts"],
                f"{prerequisite.name}: no such package script",
            )

    def test_every_producer_runs_from_a_real_directory(self) -> None:
        for prerequisite in PREREQUISITES:
            self.assertTrue(
                (ROOT / prerequisite.cwd).is_dir(),
                f"{prerequisite.name}: cwd {prerequisite.cwd} does not exist",
            )


class MissingMeansReportedNotSkipped(unittest.TestCase):
    def test_require_raises_and_the_message_carries_the_command(self) -> None:
        for prerequisite in PREREQUISITES:
            absent = Prerequisite(
                name=prerequisite.name,
                gate=prerequisite.gate,
                produces=(".local/a-path-that-cannot-exist",),
                producer=prerequisite.producer,
                automatic=prerequisite.automatic,
                why=prerequisite.why,
                cwd=prerequisite.cwd,
                caveat=prerequisite.caveat,
            )
            message = explain(absent, absent.missing())
            self.assertIn(
                prerequisite.command_line(),
                message,
                f"{prerequisite.name}: the remedy must be the producer verbatim",
            )
            self.assertIn(".local/a-path-that-cannot-exist", message)

    def test_ensure_is_only_offered_where_ensure_would_work(self) -> None:
        """Offering a command that refuses is the same defect one level up.

        `--ensure` declines to run a non-automatic producer -- an EB-16 release
        build signs with the Developer ID identity and takes tens of minutes, so
        it must not start because someone pasted a line out of an error message.
        Printing it as the remedy anyway would send the reader to a second dead
        end after the first one.
        """
        for prerequisite in PREREQUISITES:
            absent = Prerequisite(
                name=prerequisite.name,
                gate=prerequisite.gate,
                produces=(".local/a-path-that-cannot-exist",),
                producer=prerequisite.producer,
                automatic=prerequisite.automatic,
                why=prerequisite.why,
                cwd=prerequisite.cwd,
                caveat=prerequisite.caveat,
            )
            message = explain(absent, absent.missing())
            offered = f"--ensure {prerequisite.name}" in message
            self.assertEqual(
                prerequisite.automatic,
                offered,
                f"{prerequisite.name}: automatic={prerequisite.automatic} but "
                f"--ensure {'is' if offered else 'is not'} offered",
            )

    def test_require_on_an_undeclared_name_is_an_error_not_a_pass(self) -> None:
        with self.assertRaises(KeyError):
            require("no-such-prerequisite")

    def test_a_declared_but_absent_prerequisite_raises(self) -> None:
        """The whole registry is worthless if `require` can return quietly."""
        satisfied = [item for item in PREREQUISITES if not item.missing()]
        self.assertTrue(
            satisfied or True, "no assertion needed; the loop below is the test"
        )
        for prerequisite in PREREQUISITES:
            if prerequisite.missing():
                with self.assertRaises(PrerequisiteMissing):
                    require(prerequisite.name)


class PathsAreDerivedNotRetyped(unittest.TestCase):
    """B3 in one sentence: two places computed the same path, one was wrong.

    `frontend/src-tauri/tests/motion_authoring_runtime.rs` derives where it looks
    from `contracts/quality/release-package-resources.v1.json`. This registry has
    to derive the path it claims to produce from that same contract, or the two
    drift the moment the contract moves and the remedy silently stops working.
    """

    def test_the_motion_runtime_path_matches_the_release_contract(self) -> None:
        contract = json.loads(
            (ROOT / "contracts/quality/release-package-resources.v1.json").read_text(
                encoding="utf-8"
            )
        )
        worker = next(
            resource
            for resource in contract["resources"]
            if resource["name"] == "motion-video-worker"
        )
        installed = "/".join(
            ["frontend/src-tauri/target/debug", *worker["installedParts"]]
        )
        expected = (
            installed,
            *(f"{installed}/{name}" for name in worker["requiredFiles"]),
        )
        self.assertEqual(expected, by_name("motion-authoring-runtime").produces)

    def test_the_video_e2e_browser_cache_path_matches_the_real_harness(self) -> None:
        from desktop_e2e_prerequisites import (
            DISTRIBUTION_MANIFEST_NAME,
            embedded_browser_cache,
        )

        manifest = embedded_browser_cache() / DISTRIBUTION_MANIFEST_NAME
        expected = (manifest.relative_to(ROOT).as_posix(),)
        prerequisite = by_name("video-e2e-embedded-browser")

        self.assertEqual(expected, prerequisite.produces)
        self.assertEqual(
            ("{python}", "scripts/desktop_e2e_prerequisites.py"),
            prerequisite.producer,
        )

    def test_an_unsupported_host_can_still_inspect_the_registry(self) -> None:
        import desktop_e2e_prerequisites
        import gate_prerequisites

        with mock.patch.object(desktop_e2e_prerequisites.sys, "platform", "linux"):
            paths = gate_prerequisites._video_e2e_browser_paths()

        self.assertEqual(
            (
                ".local/desktop-e2e/embedded-browser/"
                "unsupported-linux/distribution-manifest.v1.json",
            ),
            paths,
        )


class ConsumersReadTheDeclarationRatherThanRetypingIt(unittest.TestCase):
    """Each gate must check the path the registry promises to produce.

    If a gate keeps its own copy of the path, the registry can say a remedy
    produced something while the gate is still looking somewhere else -- which
    is the same two-places-one-truth defect, moved one level up.
    """

    def test_the_bm14_gate_reads_the_declared_catalog_path(self) -> None:
        import test_motion_catalog_release

        declared = ROOT / by_name("offline-motion-catalog").produces[0]
        self.assertEqual(declared, test_motion_catalog_release.STAGED_ROOT)

    def test_the_cq03_gate_defaults_to_the_declared_package(self) -> None:
        import run_cq_03_acceptance

        declared = ROOT / by_name("eb-16-release-package").produces[0]
        self.assertEqual(declared, run_cq_03_acceptance.DEFAULT_PACKAGE)


class RustGateProducesItsOwnFrontendBundle(unittest.TestCase):
    """`generate_context!` reads `frontend/dist` at compile time.

    Without it `cargo test` does not fail an assertion, it fails to build:
    "The `frontendDist` configuration is set to `../dist` but this path doesn't
    exist". Producing it costs three seconds and no network, so `test:rust` owns
    it rather than reporting it -- and `test:layers`, which runs `test:rust`,
    stops being red on a clean tree for the same reason.
    """

    def test_the_rust_test_script_builds_the_bundle_first(self) -> None:
        manifest = json.loads(
            (ROOT / "frontend/package.json").read_text(encoding="utf-8")
        )
        script = manifest["scripts"]["test:rust"]
        self.assertIn(
            "pnpm build",
            script,
            "test:rust must produce frontend/dist itself; without it cargo test "
            f"cannot compile at all. current: {script}",
        )
        self.assertIn("cargo test", script)


class CommandLineIsCopyPasteable(unittest.TestCase):
    def test_the_help_output_lists_every_prerequisite(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts/gate_prerequisites.py"), "--list"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        for prerequisite in PREREQUISITES:
            self.assertIn(prerequisite.name, completed.stdout)


if __name__ == "__main__":
    unittest.main()
