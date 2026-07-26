#!/usr/bin/env python3
"""Tests for the one command that produces a distributable package.

These run the real `hdiutil` against inputs it must reject. Nothing here
notarises, signs or builds a bundle — those need Apple and forty minutes.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import tokenize
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_release_package  # noqa: E402
from build_release_package import attach_command  # noqa: E402


class TheAppIsNotCopiedByHdiutil(unittest.TestCase):
    """`hdiutil create -srcfolder` cannot carry this application bundle.

    Measured 2026-07-27 on the release machine, with the real signed and
    notarised bundle, four ways:

    | staged payload                       | result |
    |--------------------------------------|--------|
    | `自动化运营工具.app` (staging dir)   | EPERM  |
    | the same bundle, renamed `payload`   | ok     |
    | a synthetic `Probe.app` stub         | ok     |
    | the bundle handed to `-srcfolder`    | EPERM  |
    |   directly (the pre-T84 form)        |        |

    So it is neither the name nor the size: it is a *genuine* application
    bundle, and it fails for the old form too — this is not a regression T84
    introduced. `ditto` in this process copies the same bundle onto the same
    mounted volume without complaint, so the refusal belongs to the helper
    hdiutil delegates its copying to, not to us.

    The consequence is blunt: as long as the payload goes through
    `-srcfolder`, this machine cannot produce a package at all.
    """

    def test_the_disk_image_step_does_not_delegate_the_copy_to_hdiutil(self) -> None:
        # Code only. The comments and docstrings around the replacement say
        # `-srcfolder` on purpose — that is the record of why it is gone, and
        # a check that forbids naming the mistake would delete its own reason.
        path = Path(build_release_package.__file__)
        offenders = []
        with path.open("rb") as handle:
            for token in tokenize.tokenize(handle.readline):
                if token.type in (tokenize.COMMENT, tokenize.STRING):
                    continue
                if "-srcfolder" in token.string:
                    offenders.append(f"line {token.start[0]}: {token.string}")

        self.assertEqual(
            offenders,
            [],
            "the release disk image must be filled by ditto into an attached "
            "image, not by hdiutil's own copier, which refuses this bundle",
        )


@unittest.skipUnless(sys.platform == "darwin", "hdiutil is macOS-only")
class DiskImageFailureIsExplained(unittest.TestCase):
    """A failed release build has to say why it failed.

    On 2026-07-27 this step failed during a release build and the log carried
    the exit code and nothing else, because of `-quiet`. Recovering the reason
    took re-running the command by hand — possible on a laptop, impossible on
    a build machine. `hdiutil -quiet` prints zero bytes on failure; measured.
    """

    def test_hdiutil_reports_a_reason_when_it_cannot_attach(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            command = attach_command(
                # No such image, so hdiutil must refuse.
                image=Path(directory) / "absent.dmg",
                mountpoint=Path(directory),
            )
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=120
            )

        self.assertNotEqual(
            completed.returncode, 0, "hdiutil attached an image that does not exist"
        )
        self.assertTrue(
            (completed.stdout + completed.stderr).strip(),
            "hdiutil failed without printing a reason, so a release build that "
            "fails here leaves the operator with an exit code and nothing else",
        )


@unittest.skipUnless(sys.platform == "darwin", "hdiutil is macOS-only")
class TheImageCarriesWhatWasStaged(unittest.TestCase):
    """What is staged is what the customer sees when they open the file.

    The payload here is synthetic on purpose. The bundle that provoked the
    rewrite cannot be stood in for: a synthetic `Probe.app` copies fine, only
    a genuine signed application is refused, and building one costs a full
    release run. So this covers the part a fixture can cover — that the
    assembly moves every staged entry, symlinks included, and leaves no mount
    behind — and the signed bundle is verified on the real artifact by
    `require_distributable_release`.
    """

    def test_every_staged_entry_reaches_the_volume(self) -> None:
        volume_name = "automation-tool-fixture"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "image"
            (staging / "Payload").mkdir(parents=True)
            (staging / "Payload" / "file.txt").write_text("content", encoding="utf-8")
            (staging / "Applications").symlink_to("/Applications")
            output = root / "out.dmg"

            build_release_package.fill_disk_image(
                source=staging, volume_name=volume_name, output=output
            )
            self.assertTrue(output.is_file(), "no disk image was produced")

            mountpoint = root / "mounted"
            mountpoint.mkdir()
            subprocess.run(
                [*attach_command(image=output, mountpoint=mountpoint), "-readonly"],
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
            )
            try:
                carried = sorted(path.name for path in mountpoint.iterdir())
                drag_target = (mountpoint / "Applications").readlink()
                payload = (mountpoint / "Payload" / "file.txt").read_text(
                    encoding="utf-8"
                )
            finally:
                subprocess.run(
                    ["hdiutil", "detach", os.fspath(mountpoint)],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )

        self.assertEqual(carried, ["Applications", "Payload"])
        self.assertEqual(drag_target, Path("/Applications"))
        self.assertEqual(payload, "content")
        # Nothing may be left mounted under /Volumes: parallel release lines
        # used to collide there, and a stale mount outlives the build.
        self.assertFalse(Path("/Volumes", volume_name).exists())


if __name__ == "__main__":
    unittest.main()
