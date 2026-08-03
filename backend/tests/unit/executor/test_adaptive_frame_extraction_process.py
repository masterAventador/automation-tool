"""COV-02: bounded-FFmpeg process control in adaptive frame extraction.

`_kill_and_reap` decides whether to kill based on `process.poll()`, and COV-00
recorded that branch as measurably flaky: running the existing suite three times
took the "already exited" arc once and missed it twice, because whether the child
has reaped itself by that instant is a real race. A coverage gate that depends on
scheduling luck goes red at random once it is enforced, so both arcs are pinned
here with an explicit process double rather than left to timing.

The file-system side (`_measure_output`, `_workspace_accepts_write`) uses real
directories instead: those assertions are about what `stat` reports for a
non-regular entry or an unwritable workspace, and a mock would only restate the
test's own assumption.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from automation_tool.executor.adaptive_frame_extraction import (
    AdaptiveFrameRejection,
    _collect_bounded_ffmpeg,
    _kill_and_reap,
    _measure_output,
    _run_bounded_ffmpeg,
    _workspace_accepts_write,
)


def _process(poll: Any, wait: Any = None) -> mock.MagicMock:
    """A Popen stand-in whose liveness answer is fixed, not raced for."""
    double = mock.MagicMock()
    double.poll.return_value = poll
    if wait is not None:
        double.wait.side_effect = wait
    return double


class KillAndReapTests(unittest.TestCase):
    def test_a_live_child_is_killed_then_reaped(self) -> None:
        process = _process(poll=None)

        _kill_and_reap(process)

        process.kill.assert_called_once_with()
        process.wait.assert_called_once()

    def test_an_already_exited_child_is_only_reaped(self) -> None:
        """The arc COV-00 caught missing two runs out of three."""
        process = _process(poll=0)

        _kill_and_reap(process)

        process.kill.assert_not_called()
        process.wait.assert_called_once()

    def test_a_child_that_ignores_the_first_kill_is_killed_again(self) -> None:
        process = _process(
            poll=None,
            wait=[subprocess.TimeoutExpired(cmd="ffmpeg", timeout=5.0), None],
        )

        _kill_and_reap(process)

        self.assertEqual(process.kill.call_count, 2, "the reap timeout must retry the kill")
        self.assertEqual(process.wait.call_count, 2)

    def test_a_kill_that_races_the_child_exiting_is_tolerated(self) -> None:
        """`OSError` here means the pid is already gone -- not a failure."""
        process = _process(poll=None)
        process.kill.side_effect = OSError("no such process")

        _kill_and_reap(process)

        process.wait.assert_called_once()

    def test_a_second_reap_timeout_does_not_escape(self) -> None:
        process = _process(
            poll=None,
            wait=[
                subprocess.TimeoutExpired(cmd="ffmpeg", timeout=5.0),
                subprocess.TimeoutExpired(cmd="ffmpeg", timeout=5.0),
            ],
        )

        _kill_and_reap(process)

        self.assertEqual(process.wait.call_count, 2)


class MeasureOutputTests(unittest.TestCase):
    def test_regular_files_are_summed_in_name_order(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            (workspace / "b.jpg").write_bytes(b"22")
            (workspace / "a.jpg").write_bytes(b"1")

            measured = _measure_output(workspace)

        self.assertNotIsInstance(measured, AdaptiveFrameRejection)
        assert not isinstance(measured, AdaptiveFrameRejection)
        total, paths = measured
        self.assertEqual(total, 3)
        self.assertEqual([path.name for path in paths], ["a.jpg", "b.jpg"])

    def test_a_non_regular_entry_is_refused(self) -> None:
        """FFmpeg only ever writes plain files; a directory means tampering."""
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            (workspace / "unexpected").mkdir()

            self.assertIs(_measure_output(workspace), AdaptiveFrameRejection.TOOL_FAILED)

    def test_a_vanished_workspace_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            missing = Path(raw) / "gone"

            self.assertIs(
                _measure_output(missing),
                AdaptiveFrameRejection.WORKSPACE_UNUSABLE,
            )


class WorkspaceProbeTests(unittest.TestCase):
    def test_a_writable_workspace_accepts_the_probe_and_leaves_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)

            self.assertTrue(_workspace_accepts_write(workspace))
            self.assertEqual(list(workspace.iterdir()), [], "the probe must clean up after itself")

    def test_a_missing_workspace_fails_the_probe(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            self.assertFalse(_workspace_accepts_write(Path(raw) / "gone"))


class BoundedFfmpegTests(unittest.TestCase):
    def test_non_positive_limits_are_a_programming_error(self) -> None:
        for seconds, limit in ((0, 1), (-1, 1), (1, -1)):
            with (
                self.subTest(seconds=seconds, limit=limit),
                self.assertRaises(ValueError),
            ):
                _run_bounded_ffmpeg(lambda _: ["true"], seconds=seconds, output_limit_bytes=limit)

    def test_a_scratch_directory_that_cannot_be_created_is_reported(self) -> None:
        with mock.patch.object(tempfile, "mkdtemp", side_effect=OSError("no space")):
            outcome = _run_bounded_ffmpeg(lambda _: ["true"], seconds=1, output_limit_bytes=1024)

        self.assertIs(outcome, AdaptiveFrameRejection.WORKSPACE_UNUSABLE)

    def test_the_scratch_directory_is_removed_even_when_the_pass_fails(self) -> None:
        created: list[Path] = []
        real_mkdtemp = tempfile.mkdtemp

        def recording_mkdtemp(*args: Any, **kwargs: Any) -> str:
            made: str = real_mkdtemp(*args, **kwargs)
            created.append(Path(made))
            return made

        with mock.patch.object(tempfile, "mkdtemp", recording_mkdtemp):
            _run_bounded_ffmpeg(
                lambda _: ["definitely-not-a-real-binary"],
                seconds=1,
                output_limit_bytes=1024,
            )

        self.assertEqual(len(created), 1)
        self.assertFalse(created[0].exists(), "the scratch workspace must not survive the call")

    def test_an_unlaunchable_binary_is_reported_as_a_tool_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            outcome = _collect_bounded_ffmpeg(
                ["definitely-not-a-real-binary"],
                Path(raw),
                seconds=1,
                output_limit_bytes=1024,
            )

        self.assertIs(outcome, AdaptiveFrameRejection.TOOL_FAILED)

    def test_a_clean_pass_returns_the_files_it_wrote(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            argv = ["sh", "-c", f"printf ab > {workspace / 'frame-000001.jpg'}"]

            outcome = _collect_bounded_ffmpeg(argv, workspace, seconds=30, output_limit_bytes=1024)

        self.assertNotIsInstance(outcome, AdaptiveFrameRejection)
        assert not isinstance(outcome, AdaptiveFrameRejection)
        self.assertEqual(outcome.files, (("frame-000001.jpg", b"ab"),))

    def test_output_past_the_byte_limit_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            argv = ["sh", "-c", f"printf abcdefghij > {workspace / 'frame-000001.jpg'}"]

            outcome = _collect_bounded_ffmpeg(argv, workspace, seconds=30, output_limit_bytes=2)

        self.assertIs(outcome, AdaptiveFrameRejection.OUTPUT_LIMIT_EXCEEDED)

    def test_a_non_zero_exit_with_a_live_workspace_is_undecodable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            outcome = _collect_bounded_ffmpeg(
                ["sh", "-c", "exit 1"], Path(raw), seconds=30, output_limit_bytes=1024
            )

        self.assertIs(outcome, AdaptiveFrameRejection.UNDECODABLE)

    def test_a_pass_that_outlives_its_deadline_times_out(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            outcome = _collect_bounded_ffmpeg(
                ["sh", "-c", "sleep 30"], Path(raw), seconds=0.2, output_limit_bytes=1024
            )

        self.assertIs(outcome, AdaptiveFrameRejection.TIMED_OUT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
