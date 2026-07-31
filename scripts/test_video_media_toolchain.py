#!/usr/bin/env python3
"""VF-04's own self-test has to pass on the platform that runs it.

`check_video_media_toolchain.py --self-test` is the deterministic half of the
VF-04 gate: it mutates the contract in eight ways and requires each one to be
rejected, then builds a linked directory and requires the candidate validator to
refuse it. `scripts/run_vf_04_acceptance.py` runs it as its first step.

It could only ever pass on Windows. The rejection fixture is a symbolic link on
POSIX and a directory junction on Windows, and the teardown was written for the
junction alone -- `Path.rmdir()` on a symlink raises `NotADirectoryError`. The
crash lands in a `finally`, after the assertion has already succeeded, so the
gate reported a failure for a property that held.

Nothing executed this file: `backend/pyproject.toml` sets `testpaths = ["tests"]`
so pytest never collected the script, and the two CI jobs that touch the checker
pass `--candidate`, never `--self-test`. It had been red on macOS and Linux since
the day it was written. This test is what makes `scripts/run_script_tests.py`
notice next time.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_video_media_toolchain.py"

sys.path.insert(0, str(ROOT / "scripts"))

from check_video_media_toolchain import (  # noqa: E402
    create_test_directory_link,
    remove_test_directory_link,
    validate_contract,
)

EXPECTED_PRODUCTION_FILTERS = {
    "ametadata",
    "amix",
    "aresample",
    "concat",
    "crop",
    "ebur128",
    "format",
    "fps",
    "overlay",
    "scale",
    "scdet",
    "select",
    "setpts",
    "setsar",
    "settb",
    "silencedetect",
    "trim",
    "xfade",
}


class SelfTestRunsOnThisPlatform(unittest.TestCase):
    def test_the_self_test_exits_clean(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(CHECKER), "--self-test"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            0,
            completed.returncode,
            "the VF-04 self-test must pass on every platform that runs it, not "
            f"only Windows:\n{completed.stdout}{completed.stderr}",
        )
        self.assertIn("video media toolchain contract is valid", completed.stdout)

    def test_contract_and_validator_pin_every_production_filter(self) -> None:
        document = json.loads(
            (ROOT / "contracts/video/ffmpeg-toolchain.v1.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            EXPECTED_PRODUCTION_FILTERS,
            set(document["required_capabilities"]["filters"]),
        )
        validate_contract(document)


class DirectoryLinkFixture(unittest.TestCase):
    """Teardown has to remove the link and leave what it pointed at alone.

    The self-test above proves the pair works on whichever platform runs it.
    This proves the half that a passing self-test would not notice: a teardown
    that followed the link would delete the caller's directory and still exit 0.
    """

    def test_the_link_is_removed_and_the_target_survives(self) -> None:
        with TemporaryDirectory(prefix="automation-tool-vf04-linktest-") as directory:
            target = Path(directory) / "target"
            target.mkdir()
            (target / "keep.txt").write_text("keep", encoding="utf-8")
            link = Path(directory) / "linked"

            create_test_directory_link(link, target)
            self.assertTrue(link.is_dir(), "the fixture must resolve to a directory")

            remove_test_directory_link(link)

            self.assertFalse(
                link.exists() or link.is_symlink(), "the fixture must be gone"
            )
            self.assertTrue(
                (target / "keep.txt").is_file(),
                "removing the link must not delete what it pointed at",
            )


if __name__ == "__main__":
    unittest.main()
