#!/usr/bin/env python3
"""Tests for the Windows process primitives EB-11 observes ownership with.

These run against real processes rather than mocks. The whole point of the
module is that it reads facts out of the operating system, and a mock of
`ReadProcessMemory` would only prove that the test author and the implementation
agree about a struct offset — which is exactly the thing most likely to be wrong.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
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
class OpenFileTests(unittest.TestCase):
    """The `lsof -p` of this host.

    EB-11 uses it for one question: does the browser process actually hold the
    Profile directory this run created, or is it merely running with a matching
    `--user-data-dir` on its command line? A command line is what a process was
    asked to do; an open handle is what it is doing.
    """

    def _scratch(self) -> Path:
        """A directory removed *after* the holder stops.

        `addCleanup` unwinds last-registered-first, so registering this before
        starting a holder is what lets the holder release the file first. A
        `with TemporaryDirectory()` block instead unwinds before any cleanup
        runs, and Windows refuses to delete a file somebody still has open.
        """
        temporary = Path(tempfile.mkdtemp(prefix="eb11-open-files-"))
        self.addCleanup(shutil.rmtree, temporary, True)
        return temporary

    def _holder(self, body: str, argument: Path) -> tuple[subprocess.Popen[str], int]:
        """Start a process that holds something open, and learn its real pid.

        `Popen.pid` is not it. A uv virtualenv's `python.exe` is a trampoline
        that execs nothing — it *spawns* the real interpreter and waits — so the
        pid this process gets back holds only the inherited stdout and cwd, and
        every handle the script actually opens belongs to a grandchild. The
        first debug run of these tests looked like the enumerator was dropping
        file handles; it was enumerating the wrong process.
        """
        holder = subprocess.Popen(
            [
                sys.executable,
                "-c",
                f"import os,sys,time\n{body}\nprint(os.getpid(), flush=True)\ntime.sleep(30)",
                str(argument),
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self._stop_holder, holder)
        assert holder.stdout is not None
        return holder, int(holder.stdout.readline().strip())

    @staticmethod
    def _stop_holder(holder: subprocess.Popen[str]) -> None:
        holder.terminate()
        try:
            holder.wait(timeout=15)
        except subprocess.TimeoutExpired:
            holder.kill()
            holder.wait(timeout=15)

    def test_a_file_a_process_has_open_is_listed_for_that_process(self) -> None:
        import windows_processes

        temporary = self._scratch()
        target = temporary / "held-open.txt"
        target.write_text("held", encoding="utf-8")
        _, process_id = self._holder("handle = open(sys.argv[1], 'rb')", target)

        found = [Path(item) for item in windows_processes.open_file_paths([process_id])]

        self.assertIn(
            target.resolve(),
            [item.resolve() for item in found],
            f"the held file is missing from {len(found)} listed paths",
        )
        # A sibling nobody opened must not appear, or the listing is not
        # per-process and proves nothing about who holds what.
        self.assertNotIn(temporary / "never-opened.txt", found)

    def test_a_directory_a_process_holds_open_is_listed(self) -> None:
        """Chromium holds the Profile *directory*, not only files inside it."""
        import windows_processes

        directory = self._scratch() / "profile"
        directory.mkdir()
        _, process_id = self._holder(
            "import ctypes\n"
            "kernel32 = ctypes.WinDLL('kernel32')\n"
            "held = kernel32.CreateFileW(\n"
            "    sys.argv[1], 0x80000000, 7, None, 3, 0x02000000, None)\n"
            "assert held != -1",
            directory,
        )

        found = [
            Path(item).resolve()
            for item in windows_processes.open_file_paths([process_id])
        ]

        self.assertIn(directory.resolve(), found)

    def test_an_exited_process_reports_nothing_rather_than_failing(self) -> None:
        import windows_processes

        child = subprocess.Popen([sys.executable, "-c", "raise SystemExit(0)"])
        child.wait(timeout=15)

        self.assertEqual(windows_processes.open_file_paths([child.pid]), [])


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
