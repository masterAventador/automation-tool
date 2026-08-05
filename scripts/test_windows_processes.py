#!/usr/bin/env python3
"""Tests for the Windows process primitives EB-11 observes ownership with.

These run against real processes rather than mocks. The whole point of the
module is that it reads facts out of the operating system, and a mock of
`ReadProcessMemory` would only prove that the test author and the implementation
agree about a struct offset — which is exactly the thing most likely to be wrong.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

WINDOWS_ONLY = unittest.skipUnless(os.name == "nt", "Windows process primitives")


@WINDOWS_ONLY
class ProcessTableTests(unittest.TestCase):
    def test_the_table_holds_this_process_and_its_parent(self) -> None:
        import windows_processes

        table = windows_processes.process_table()

        self.assertIn(os.getpid(), table)
        self.assertEqual(table[os.getpid()].pid, os.getpid())
        self.assertIn(table[os.getpid()].ppid, table)

    def test_ancestry_walks_upwards_and_stops(self) -> None:
        import windows_processes

        table = windows_processes.process_table()
        ancestors = windows_processes.ancestor_process_ids(os.getpid(), table=table)

        self.assertIn(table[os.getpid()].ppid, ancestors)
        self.assertNotIn(os.getpid(), ancestors)


@WINDOWS_ONLY
class ProcessFactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.marker = "eb11-process-probe"
        self.child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)", f"--{self.marker}"],
            env=dict(os.environ, AUTOMATION_TOOL_TEST_MARKER=self.marker),
        )
        self.addCleanup(self._stop)
        # The parameters block is filled by the loader, not by CreateProcess, so
        # a process sampled the instant it appears can legitimately have none.
        for _ in range(50):
            import windows_processes

            if windows_processes.command_line(self.child.pid):
                return
            time.sleep(0.1)

    def _stop(self) -> None:
        self.child.terminate()
        try:
            self.child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.child.kill()
            self.child.wait(timeout=15)

    def test_the_command_line_carries_the_arguments_it_was_given(self) -> None:
        import windows_processes

        rendered = windows_processes.command_line(self.child.pid)

        self.assertIsNotNone(rendered)
        assert rendered is not None
        self.assertIn(f"--{self.marker}", rendered)

    def test_the_environment_carries_what_the_launcher_put_there(self) -> None:
        """This is what makes a reparented helper still ours.

        macOS reads it with `ps eww`; the Windows answer is the same fact from
        the process parameters block. Without it, a Chromium helper that has
        been reparented is indistinguishable from another instance's.
        """
        import windows_processes

        values = windows_processes.environment(self.child.pid)

        self.assertIsNotNone(values)
        assert values is not None
        self.assertEqual(values.get("AUTOMATION_TOOL_TEST_MARKER"), self.marker)

    def test_the_image_path_is_the_executable_that_is_running(self) -> None:
        import windows_processes

        image = windows_processes.image_path(self.child.pid)

        self.assertIsNotNone(image)
        assert image is not None
        self.assertEqual(Path(image).resolve(), Path(sys.executable).resolve())

    def test_creation_time_distinguishes_a_recycled_process_id(self) -> None:
        """A pid on its own cannot say "still the same process".

        Windows reuses process ids, and every ownership decision here compares
        whole records. If creation time were constant or missing, a recycled pid
        would compare equal to the one this run started, which is the failure the
        macOS side spends `lstart` to avoid.
        """
        import windows_processes

        first = windows_processes.created_at(self.child.pid)
        self.assertNotEqual(first, "")
        self.assertEqual(first, windows_processes.created_at(self.child.pid))

        time.sleep(0.05)
        later = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
        try:
            self.assertNotEqual(first, windows_processes.created_at(later.pid))
        finally:
            later.terminate()
            later.wait(timeout=15)


@WINDOWS_ONLY
class UnreachableProcessTests(unittest.TestCase):
    """A process we cannot read is not an error; it is a process we cannot own.

    EB-11 walks the whole machine's process table, and most of it belongs to
    other users or to protected system processes. Raising on those would make an
    ordinary snapshot fail; reporting them as unreadable lets the ownership rules
    do what they already do — refuse to claim them.
    """

    def test_a_process_id_that_does_not_exist_reads_as_unknown(self) -> None:
        import windows_processes

        # 0xFFFFFFF0: above any real pid, and not the pseudo-handle -1.
        absent = 0xFFFFFFF0
        self.assertIsNone(windows_processes.command_line(absent))
        self.assertIsNone(windows_processes.environment(absent))
        self.assertIsNone(windows_processes.image_path(absent))
        self.assertEqual(windows_processes.created_at(absent), "")

    def test_a_protected_system_process_does_not_break_a_snapshot(self) -> None:
        import windows_processes

        table = windows_processes.process_table()
        readable = sum(
            1 for pid in table if windows_processes.command_line(pid) is not None
        )

        # Some are readable and some are not; both halves must be non-empty or
        # this test would pass on an implementation that never reads anything.
        self.assertGreater(readable, 0)
        self.assertLess(readable, len(table))


if __name__ == "__main__":
    unittest.main()
