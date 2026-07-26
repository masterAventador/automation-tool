#!/usr/bin/env python3
"""Tests for the environment a frozen artifact is probed under.

The property these call sites are buying is that a frozen artifact carries its
own dependencies and does not reach into the machine that built it. The value
they used to buy it with, `os.defpath`, is `/bin:/usr/bin` on POSIX and
`.;C:\\bin` on Windows -- a directory that does not exist plus the caller's
current directory.

The measurement that shaped these tests is in the module docstring of
`frozen_artifact_environment`: on the Windows acceptance machine what actually
fails is the absence of `SystemRoot`, not the value of `PATH`. So the tests
that carry the weight are the ones about the environment, not the search path.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from frozen_artifact_environment import (  # noqa: E402
    frozen_artifact_environment,
    minimal_search_path,
)

WINDOWS_ROOT = "C:\\WINDOWS"


class TheWindowsValueIsUsable(unittest.TestCase):
    def test_the_search_path_starts_at_the_system_directory(self) -> None:
        # `os.defpath` names `C:\bin`, which does not exist on a Windows
        # install -- confirmed on the acceptance machine, `os.path.isdir` is
        # False. The system directory is the one place a minimal search path
        # can point at and still be a real directory on every machine.
        path = minimal_search_path(os_name="nt", system_root=WINDOWS_ROOT)

        self.assertEqual(path.split(";")[0], "C:\\WINDOWS\\System32")

    def test_the_search_path_holds_only_operating_system_directories(self) -> None:
        # "Minimal" here means "nothing the developer installed", not "nothing
        # at all". Every entry has to sit under the system root, which is the
        # line between what Windows ships and what a build machine accumulated.
        path = minimal_search_path(os_name="nt", system_root=WINDOWS_ROOT)

        for entry in path.split(";"):
            self.assertTrue(entry.startswith(WINDOWS_ROOT), entry)

    def test_the_environment_carries_the_system_root(self) -> None:
        # This is the assertion that would have caught the real Windows
        # failure. Measured on the acceptance machine: a frozen executable
        # given a correct System32 search path but no `SystemRoot` still dies
        # with WinError 10106 the moment it opens a socket, because Winsock
        # resolves its provider catalog through `%SystemRoot%`. Fixing only
        # `PATH` would have changed nothing.
        environment = frozen_artifact_environment(
            environ={"SystemRoot": WINDOWS_ROOT}, os_name="nt"
        )

        self.assertEqual(environment["SystemRoot"], WINDOWS_ROOT)

    def test_windir_alone_is_enough_to_locate_the_system_root(self) -> None:
        environment = frozen_artifact_environment(
            environ={"WINDIR": WINDOWS_ROOT}, os_name="nt"
        )

        self.assertEqual(environment["PATH"].split(";")[0], "C:\\WINDOWS\\System32")

    def test_a_missing_system_root_is_refused_rather_than_guessed(self) -> None:
        # A guessed `C:\Windows` would be wrong on the machines that do not use
        # it, and the resulting failure would look like a product defect again.
        with self.assertRaises(ValueError):
            frozen_artifact_environment(environ={}, os_name="nt")

    def test_the_temporary_directory_is_carried_when_the_machine_has_one(self) -> None:
        # Without TEMP a frozen process falls back to `%SystemRoot%\Temp` --
        # measured on the acceptance machine, which returned `C:\WINDOWS\Temp`.
        # A probe writing into a system directory is not what any of these call
        # sites asked for.
        environment = frozen_artifact_environment(
            environ={"SystemRoot": WINDOWS_ROOT, "TEMP": "C:\\Users\\x\\AppData\\Local\\Temp"},
            os_name="nt",
        )

        self.assertEqual(environment["TEMP"], "C:\\Users\\x\\AppData\\Local\\Temp")


class TheEnvironmentStaysMinimal(unittest.TestCase):
    def test_nothing_the_developer_installed_survives(self) -> None:
        environment = frozen_artifact_environment(
            environ={
                "SystemRoot": WINDOWS_ROOT,
                "PATH": "C:\\Users\\x\\.cargo\\bin;C:\\Python314",
                "VIRTUAL_ENV": "C:\\repo\\.venv",
                "PYTHONHOME": "C:\\Python314",
                "PYTHONPATH": "C:\\repo\\backend\\src",
            },
            os_name="nt",
        )

        self.assertNotIn("VIRTUAL_ENV", environment)
        self.assertNotIn("PYTHONHOME", environment)
        self.assertNotIn("PYTHONPATH", environment)
        self.assertNotIn(".cargo", environment["PATH"])

    def test_the_posix_search_path_keeps_the_value_macos_already_runs(self) -> None:
        # Measured, not assumed: `os.defpath` is `/bin:/usr/bin` on this
        # Python -- no empty entry, no current directory. POSIX never had the
        # defect, so this pins the exact string the macOS acceptance has been
        # passing all along and the migration stays a no-op there.
        self.assertEqual(minimal_search_path(os_name="posix"), "/bin:/usr/bin")

    def test_the_search_path_is_chosen_by_the_named_platform_not_the_host(self) -> None:
        # The whole defect in one line: `os.defpath` answers for whatever
        # machine is asking, and its two answers are not equally usable. A
        # function that takes the platform can be tested for both branches from
        # either host, which is how the Windows branch got checked at all.
        self.assertNotEqual(
            minimal_search_path(os_name="posix"),
            minimal_search_path(os_name="nt", system_root=WINDOWS_ROOT),
        )

    def test_the_posix_environment_is_the_search_path_and_nothing_else(self) -> None:
        environment = frozen_artifact_environment(
            environ={"HOME": "/Users/x", "VIRTUAL_ENV": "/repo/.venv"}, os_name="posix"
        )

        self.assertEqual(set(environment), {"PATH"})


def _uses_os_defpath(path: Path) -> bool:
    """True when the file reads `os.defpath` in code, not merely in prose."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return False
    return any(
        isinstance(node, ast.Attribute)
        and node.attr == "defpath"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
        for node in ast.walk(tree)
    )


class NoCallSiteStillUsesOsDefpath(unittest.TestCase):
    def test_no_script_or_backend_test_reads_os_defpath(self) -> None:
        # Derived rather than curated: a hand-written list of the three known
        # call sites is how the fourth one gets written next week. The Windows
        # value of `os.defpath` is wrong for every use this repository has for
        # it, so the honest rule is that nothing reads it at all.
        roots = (ROOT / "scripts", ROOT / "backend" / "tests")
        offenders = sorted(
            str(path.relative_to(ROOT))
            for root in roots
            if root.is_dir()
            for path in root.rglob("*.py")
            if _uses_os_defpath(path)
        )

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
