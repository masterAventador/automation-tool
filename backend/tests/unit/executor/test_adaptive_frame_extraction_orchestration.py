"""COV-02: the refusal arms of the scene / supplement extraction orchestration.

Everything here is a path the module takes when something moves underneath it
mid-pass: the packaged toolchain stops validating, the source file is swapped
between two FFmpeg invocations, the supplement budget runs out, or a measurement
fails while the child is still running. Those are the arms that decide whether a
tampered run fails closed, and none of them were reached.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from automation_tool.executor import adaptive_frame_extraction as module
from automation_tool.executor.adaptive_frame_extraction import (
    AdaptiveFrameRejection,
    BoundedFfmpegOutput,
    ExtractedFrame,
    _collect_bounded_ffmpeg,
    _globally_uniform_indices,
    _open_output_workspace,
    _OutputWorkspace,
    _write_exclusive_frame,
    extract_adaptive_frame_candidates,
    extract_scene_frames,
)
from automation_tool.executor.material_probe import (
    MaterialProbeRejected,
    MaterialProbeRejection,
)

_JPEG = b"\xff\xd8ok\xff\xd9"


def _rejected() -> MaterialProbeRejected:
    return MaterialProbeRejected(MaterialProbeRejection.UNREADABLE)


def _tools() -> mock.MagicMock:
    tools = mock.MagicMock()
    tools.ffmpeg_path = Path("/usr/bin/ffmpeg")
    return tools


def _unchanged(path: Path, checked: Any) -> tuple[Path, Any]:
    return path, checked


def _one_scene() -> tuple[ExtractedFrame, ...]:
    return (ExtractedFrame(timestamp_ms=0, jpeg_bytes=_JPEG, is_scene_cut=True),)


class CurrentPathFallbackTests(unittest.TestCase):
    def test_a_workspace_with_neither_handle_nor_descriptor_uses_its_path(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            metadata = root.lstat()
            workspace = _OutputWorkspace(path=root, identity=(metadata.st_dev, metadata.st_ino))

            self.assertEqual(workspace._current_path(), root)
            workspace.close()  # nothing owned: must be a no-op, not an error


class GloballyUniformIndicesTests(unittest.TestCase):
    def test_unreachable_positions_are_skipped(self) -> None:
        """Repeated timestamps leave interior positions with no admissible parent."""
        timestamps = (0, 0, 0, 0, 0, 0, 100)

        selected = _globally_uniform_indices(timestamps, 4)

        self.assertEqual(len(selected), 4)
        self.assertEqual(selected[0], 0)
        self.assertEqual(selected[-1], len(timestamps) - 1)
        self.assertEqual(sorted(selected), list(selected), "indices must stay ordered")


class SceneExtractionRefusalTests(unittest.TestCase):
    def test_a_toolchain_that_stops_validating_is_refused(self) -> None:
        tools = _tools()
        tools.revalidate.side_effect = _rejected()

        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "clip.mp4"
            source.write_bytes(b"data")

            self.assertIs(
                extract_scene_frames(tools, source, source.lstat()),
                AdaptiveFrameRejection.TOOL_FAILED,
            )

    def test_a_source_that_changed_before_the_pass_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "clip.mp4"
            source.write_bytes(b"data")

            with mock.patch.object(module, "require_source_unchanged", side_effect=_rejected()):
                self.assertIs(
                    extract_scene_frames(_tools(), source, source.lstat()),
                    AdaptiveFrameRejection.SOURCE_UNAVAILABLE,
                )

    def test_a_source_swapped_during_the_pass_is_refused(self) -> None:
        """The second `require_source_unchanged` guards the completed run."""
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "clip.mp4"
            source.write_bytes(b"data")
            calls = {"n": 0}

            def guard(path: Path, checked: Any) -> tuple[Path, Any]:
                calls["n"] += 1
                if calls["n"] > 1:
                    raise _rejected()
                return path, checked

            with (
                mock.patch.object(module, "require_source_unchanged", guard),
                mock.patch.object(
                    module,
                    "_run_bounded_ffmpeg",
                    return_value=BoundedFfmpegOutput(files=(("scene-000000000000.jpg", _JPEG),)),
                ),
            ):
                self.assertIs(
                    extract_scene_frames(_tools(), source, source.lstat()),
                    AdaptiveFrameRejection.SOURCE_UNAVAILABLE,
                )


class SupplementLoopRefusalTests(unittest.TestCase):
    """The supplement pass re-checks the world before every seek."""

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.source = Path(self._directory.name) / "clip.mp4"
        self.source.write_bytes(b"data")
        self.approved = self.source.lstat()
        scenes = mock.patch.object(module, "extract_scene_frames", return_value=_one_scene())
        scenes.start()
        self.addCleanup(scenes.stop)

    def _candidates(self, tools: mock.MagicMock | None = None) -> Any:
        return extract_adaptive_frame_candidates(
            tools or _tools(), self.source, self.approved, duration_ms=600_000
        )

    def test_a_budget_already_spent_times_out(self) -> None:
        ticks = iter([0.0] + [10_000_000.0] * 40)

        with (
            mock.patch.object(module, "require_source_unchanged", _unchanged),
            mock.patch(
                "automation_tool.executor.adaptive_frame_extraction.time.monotonic",
                lambda: next(ticks),
            ),
        ):
            self.assertIs(self._candidates(), AdaptiveFrameRejection.TIMED_OUT)

    def test_a_toolchain_that_stops_validating_mid_loop_is_refused(self) -> None:
        tools = _tools()
        tools.revalidate.side_effect = _rejected()

        with mock.patch.object(module, "require_source_unchanged", _unchanged):
            self.assertIs(self._candidates(tools), AdaptiveFrameRejection.TOOL_FAILED)

    def test_a_source_that_changed_before_a_seek_is_refused(self) -> None:
        with mock.patch.object(module, "require_source_unchanged", side_effect=_rejected()):
            self.assertIs(self._candidates(), AdaptiveFrameRejection.SOURCE_UNAVAILABLE)

    def test_a_source_swapped_around_a_seek_is_refused(self) -> None:
        calls = {"n": 0}

        def guard(path: Path, checked: Any) -> tuple[Path, Any]:
            calls["n"] += 1
            if calls["n"] > 1:
                raise _rejected()
            return path, checked

        with (
            mock.patch.object(module, "require_source_unchanged", guard),
            mock.patch.object(
                module, "_run_bounded_ffmpeg", return_value=BoundedFfmpegOutput(files=())
            ),
        ):
            self.assertIs(self._candidates(), AdaptiveFrameRejection.SOURCE_UNAVAILABLE)

    def test_a_rejection_from_the_seek_is_propagated(self) -> None:
        with (
            mock.patch.object(module, "require_source_unchanged", _unchanged),
            mock.patch.object(
                module,
                "_run_bounded_ffmpeg",
                return_value=AdaptiveFrameRejection.UNDECODABLE,
            ),
        ):
            self.assertIs(self._candidates(), AdaptiveFrameRejection.UNDECODABLE)

    def test_an_unparseable_seek_result_is_propagated(self) -> None:
        with (
            mock.patch.object(module, "require_source_unchanged", _unchanged),
            mock.patch.object(
                module, "_run_bounded_ffmpeg", return_value=BoundedFfmpegOutput(files=())
            ),
            mock.patch.object(
                module,
                "_parse_supplement_frame",
                return_value=AdaptiveFrameRejection.TOOL_FAILED,
            ),
        ):
            self.assertIs(self._candidates(), AdaptiveFrameRejection.TOOL_FAILED)


class CollectBoundedFfmpegRefusalTests(unittest.TestCase):
    def test_a_workspace_that_cannot_be_measured_after_exit_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            measurements: list[Any] = [
                (0, ()),
                AdaptiveFrameRejection.WORKSPACE_UNUSABLE,
            ]

            def measure(_: Path) -> Any:
                return measurements.pop(0) if len(measurements) > 1 else measurements[0]

            with mock.patch.object(module, "_measure_output", measure):
                outcome = _collect_bounded_ffmpeg(
                    ["sh", "-c", "exit 0"], Path(raw), seconds=30, output_limit_bytes=1024
                )

        self.assertIs(outcome, AdaptiveFrameRejection.WORKSPACE_UNUSABLE)

    def test_a_child_killed_by_a_signal_is_a_tool_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            outcome = _collect_bounded_ffmpeg(
                ["sh", "-c", "kill -9 $$"], Path(raw), seconds=30, output_limit_bytes=1024
            )

        self.assertIs(outcome, AdaptiveFrameRejection.TOOL_FAILED)

    def test_output_measured_past_the_limit_after_exit_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            measurements: list[Any] = [
                (0, ()),
                (5000, (workspace / "frame-000001.jpg",)),
            ]

            def measure(_: Path) -> Any:
                return measurements.pop(0) if len(measurements) > 1 else measurements[0]

            with mock.patch.object(module, "_measure_output", measure):
                outcome = _collect_bounded_ffmpeg(
                    ["sh", "-c", "exit 0"], workspace, seconds=30, output_limit_bytes=10
                )

        self.assertIs(outcome, AdaptiveFrameRejection.OUTPUT_LIMIT_EXCEEDED)

    def test_a_file_that_cannot_be_read_back_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            argv = ["sh", "-c", f"printf ab > {workspace / 'frame-000001.jpg'}"]

            with mock.patch.object(Path, "read_bytes", side_effect=OSError("vanished")):
                outcome = _collect_bounded_ffmpeg(
                    argv, workspace, seconds=30, output_limit_bytes=1024
                )

        self.assertIs(outcome, AdaptiveFrameRejection.WORKSPACE_UNUSABLE)

    def test_a_measurement_failure_while_the_child_runs_kills_it(self) -> None:
        with (
            tempfile.TemporaryDirectory() as raw,
            mock.patch.object(
                module,
                "_measure_output",
                return_value=AdaptiveFrameRejection.TOOL_FAILED,
            ),
            mock.patch.object(module, "_kill_and_reap") as reap,
        ):
            outcome = _collect_bounded_ffmpeg(
                ["sh", "-c", "sleep 5"], Path(raw), seconds=30, output_limit_bytes=1024
            )

        self.assertIs(outcome, AdaptiveFrameRejection.TOOL_FAILED)
        reap.assert_called()

    def test_an_interrupted_wait_stops_the_child_before_leaving(self) -> None:
        with (
            tempfile.TemporaryDirectory() as raw,
            mock.patch.object(module, "_wait_for_bounded_output", side_effect=KeyboardInterrupt()),
            mock.patch.object(module, "_kill_and_reap") as reap,
            self.assertRaises(KeyboardInterrupt),
        ):
            _collect_bounded_ffmpeg(
                ["sh", "-c", "sleep 5"], Path(raw), seconds=30, output_limit_bytes=1024
            )

        reap.assert_called_once()


class _RefusingLog(list):  # type: ignore[type-arg]
    """A recorder that fails after the file exists but before it is logged.

    The production code appends the name immediately after opening, so nothing
    between those two statements can fail on its own. Making the append itself
    raise is the only way to reach the cleanup arm that removes a frame the
    caller never learned about -- and that arm is what stops a crashed batch
    from leaving an orphan behind.
    """

    def append(self, item: Any) -> None:
        raise OSError("cannot record the frame name")


class WriteExclusiveCleanupTests(unittest.TestCase):
    def test_a_frame_the_caller_never_learned_about_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = _open_output_workspace(root)
            assert workspace is not None
            try:
                with self.assertRaises(OSError):
                    _write_exclusive_frame(workspace, "frame-000001.jpg", _JPEG, _RefusingLog())
            finally:
                workspace.close()

            self.assertEqual(list(root.iterdir()), [], "an orphaned frame must be removed")


class OpenWorkspaceWindowsCleanupTests(unittest.TestCase):
    def test_a_failure_before_the_handle_exists_needs_no_close(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            # Constructed before the patch: inside it `Path()` yields a
            # WindowsPath whose `lstat` fails here, which would make the
            # workspace identity probe bail out long before the Windows arm and
            # leave this test asserting None for entirely the wrong reason.
            root = Path(raw)
            with (
                mock.patch.object(os, "name", "nt"),
                mock.patch.object(
                    module,
                    "_open_windows_directory_handle",
                    side_effect=OSError("denied"),
                ),
                mock.patch.object(module, "_close_windows_directory_handle") as close,
            ):
                self.assertIsNone(_open_output_workspace(root))

            close.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
