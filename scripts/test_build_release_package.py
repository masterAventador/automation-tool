#!/usr/bin/env python3
"""Tests for the one command that produces a distributable package.

These run the real `hdiutil` against inputs it must reject. Nothing here
notarises, signs or builds a bundle — those need Apple and forty minutes.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_release_package import disk_image_command  # noqa: E402


@unittest.skipUnless(sys.platform == "darwin", "hdiutil is macOS-only")
class DiskImageFailureIsExplained(unittest.TestCase):
    """A failed release build has to say why it failed.

    On 2026-07-27 this step failed during a release build and the log carried
    the exit code and nothing else. The reason (`could not access
    /Volumes/... - 操作不被允许`, a volume-name collision with a disk image
    another line had mounted) was only recovered by re-running the same
    command by hand — which is possible on a laptop and impossible on a build
    machine. `hdiutil -quiet` prints zero bytes on failure; measured.
    """

    def test_hdiutil_reports_a_reason_when_it_cannot_build_the_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            command = disk_image_command(
                volume_name="automation-tool-test",
                # No such directory, so hdiutil must refuse.
                source=Path(directory) / "absent",
                output=Path(directory) / "out.dmg",
            )
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=120
            )

        self.assertNotEqual(
            completed.returncode, 0, "hdiutil accepted a source that does not exist"
        )
        self.assertTrue(
            (completed.stdout + completed.stderr).strip(),
            "hdiutil failed without printing a reason, so a release build that "
            "fails here leaves the operator with an exit code and nothing else",
        )


if __name__ == "__main__":
    unittest.main()
