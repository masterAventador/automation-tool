#!/usr/bin/env python3
"""The video runtime staging tree has to be installable where readers look.

`frontend/src-tauri/tests/motion_authoring_runtime.rs` reads the motion Worker
package out of the resource directory a debug App resolves from, and when it is
absent it panics with "Build it with scripts/prepare_video_runtime.py before
running this suite."

Running that script changed nothing. It exits 0 and lays the artifacts out under
the per-machine build cache (`~/Library/Caches/automation-tool-build` on macOS);
nothing ever copied them into `target/debug`, so the remedy was a command that
succeeded without addressing the failure it was printed for. The test itself is
right to fail loudly -- its own comment records that it used to return early and
report a pass -- the pointer was what was wrong.

These tests cover the half that was missing: taking a prepared staging tree and
installing it at the layout the release resource contract declares. The staging
tree here is fabricated and stamped as current, so no Worker is built and no
byte is downloaded; what is under test is the mapping, which is what was absent.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prepare_video_runtime.py"
sys.path.insert(0, str(ROOT / "scripts"))

from video_runtime_cache import STAMP_VERSION, contract_fingerprint  # noqa: E402

MOTION_WORKER_CONTRACT = ROOT / "contracts/quality/motion-video-worker-package.v1.json"
MOTION_WORKER_SOURCE = ROOT / "workers/motion_composition/worker.mjs"
# What `release-package-resources.v1.json` says the Worker is installed as.
INSTALLED = ("motion-video-worker", "package")


def stamped_motion_worker(staging: Path) -> Path:
    """A staging tree the cache accepts as current, so nothing gets rebuilt."""
    package = staging / "motion-video-worker"
    (package / "runtime").mkdir(parents=True)
    (package / "app").mkdir(parents=True)
    (package / "runtime" / "node").write_bytes(b"#!/bin/sh\nexit 0\n")
    (package / "runtime" / "gsap.min.js").write_text("/* gsap */", encoding="utf-8")
    (package / "app" / "worker.mjs").write_text("export default 1;\n", encoding="utf-8")
    (staging / "motion-video-worker.stamp.json").write_text(
        json.dumps(
            {
                "version": STAMP_VERSION,
                "name": "motion-video-worker",
                "fingerprint": contract_fingerprint([MOTION_WORKER_CONTRACT, MOTION_WORKER_SOURCE]),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return package


def run_prepare(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class InstallIntoAResourceDirectory(unittest.TestCase):
    def test_the_worker_lands_where_the_rust_test_reads_it(self) -> None:
        with TemporaryDirectory(prefix="automation-tool-prepare-test-") as directory:
            base = Path(directory)
            staging = base / "staging"
            staging.mkdir()
            stamped_motion_worker(staging)
            resources = base / "resources"

            completed = run_prepare(
                "--root",
                str(staging),
                "--only",
                "motion-video-worker",
                "--install-into",
                str(resources),
            )

            self.assertEqual(
                0,
                completed.returncode,
                "preparing and installing one resource must succeed:\n"
                f"{completed.stdout}{completed.stderr}",
            )
            installed = resources.joinpath(*INSTALLED)
            self.assertTrue(
                (installed / "runtime" / "node").is_file(),
                f"the Worker runtime must be installed at {installed}",
            )
            self.assertTrue((installed / "app" / "worker.mjs").is_file())
            self.assertTrue(
                (installed / "runtime" / "gsap.min.js").is_file(),
                "the animation runtime the composition loads must come with it",
            )

    def test_installing_twice_replaces_rather_than_refusing(self) -> None:
        """A developer reruns cargo test; the second run must not need a rm -rf."""
        with TemporaryDirectory(prefix="automation-tool-prepare-test-") as directory:
            base = Path(directory)
            staging = base / "staging"
            staging.mkdir()
            stamped_motion_worker(staging)
            resources = base / "resources"
            common = (
                "--root",
                str(staging),
                "--only",
                "motion-video-worker",
                "--install-into",
                str(resources),
            )

            self.assertEqual(0, run_prepare(*common).returncode)
            stale = resources.joinpath(*INSTALLED, "runtime", "stale-leftover")
            stale.write_text("from an older build", encoding="utf-8")

            second = run_prepare(*common)

            self.assertEqual(
                0,
                second.returncode,
                f"a repeat install must succeed:\n{second.stdout}{second.stderr}",
            )
            self.assertFalse(stale.exists(), "a repeat install must not merge into the old tree")
            self.assertTrue(resources.joinpath(*INSTALLED, "runtime", "node").is_file())

    def test_install_replaces_a_linked_resource_root_without_touching_its_target(
        self,
    ) -> None:
        """A worktree must never follow an old resource link into main."""
        with TemporaryDirectory(prefix="automation-tool-prepare-test-") as directory:
            base = Path(directory)
            staging = base / "staging"
            staging.mkdir()
            stamped_motion_worker(staging)
            resources = base / "resources"
            resources.mkdir()
            external = base / "main-checkout-motion-worker"
            external.mkdir()
            sentinel = external / "belongs-to-main"
            sentinel.write_text("leave me alone\n", encoding="utf-8")
            (resources / "motion-video-worker").symlink_to(
                external,
                target_is_directory=True,
            )

            completed = run_prepare(
                "--root",
                str(staging),
                "--only",
                "motion-video-worker",
                "--install-into",
                str(resources),
            )

            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            installed_root = resources / "motion-video-worker"
            self.assertFalse(
                installed_root.is_symlink(),
                "the worktree resource root must be independently materialized",
            )
            self.assertTrue(installed_root.joinpath("package/runtime/node").is_file())
            self.assertEqual(
                "leave me alone\n",
                sentinel.read_text(encoding="utf-8"),
                "installing in a worktree followed the link and modified main",
            )

    def test_an_unknown_resource_name_is_refused(self) -> None:
        with TemporaryDirectory(prefix="automation-tool-prepare-test-") as directory:
            completed = run_prepare("--only", "no-such-resource", "--install-into", directory)
            self.assertNotEqual(0, completed.returncode, "a typo must not silently install nothing")
            self.assertIn("no-such-resource", completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
