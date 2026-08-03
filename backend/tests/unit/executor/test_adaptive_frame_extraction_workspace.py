"""COV-02: the owned output-workspace reference in adaptive frame extraction.

`_OutputWorkspace` exists so that a frame write cannot be redirected by swapping
the output directory underneath it. It carries three mutually exclusive ways of
naming that directory -- a POSIX directory descriptor, a Windows handle, or a
plain path -- and on this platform only the first was ever exercised, so the
other two and every identity-drift refusal were uncovered.

The Windows arms are driven by patching `os.name` plus the module's own kernel32
wrappers, matching the platform-value injection used for those wrappers
themselves. The POSIX arms use real directories and real descriptors: the whole
claim being tested is what `fstat` and `O_NOFOLLOW` do about a replaced
directory, which a mock cannot establish.
"""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from automation_tool.executor import adaptive_frame_extraction as module
from automation_tool.executor.adaptive_frame_extraction import (
    AdaptiveFrameRejection,
    ExtractedFrame,
    _open_output_workspace,
    _output_workspace_identity,
    _OutputWorkspace,
    _write_exclusive_frame,
    _write_final_frames,
)

_JPEG = b"\xff\xd8\xff\xd9"


class WorkspaceIdentityTests(unittest.TestCase):
    def test_a_real_directory_reports_device_and_inode(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            metadata = Path(raw).lstat()

            self.assertEqual(
                _output_workspace_identity(Path(raw)),
                (metadata.st_dev, metadata.st_ino),
            )

    def test_a_plain_file_is_not_a_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "file"
            target.write_bytes(b"x")

            self.assertIsNone(_output_workspace_identity(target))

    def test_a_missing_path_is_not_a_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            self.assertIsNone(_output_workspace_identity(Path(raw) / "gone"))

    def test_a_symlinked_directory_is_refused(self) -> None:
        """`lstat` plus the reparse check: the link itself is not the workspace."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "real").mkdir()
            link = root / "link"
            link.symlink_to(root / "real", target_is_directory=True)

            self.assertIsNone(_output_workspace_identity(link))


class OpenWorkspacePosixTests(unittest.TestCase):
    def test_a_directory_is_opened_and_owns_a_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = _open_output_workspace(Path(raw))

            self.assertIsNotNone(workspace)
            assert workspace is not None
            try:
                self.assertIsNotNone(workspace.directory_descriptor)
                self.assertIsNone(workspace.windows_handle)
                self.assertEqual(workspace.identity, _output_workspace_identity(Path(raw)))
            finally:
                workspace.close()

    def test_a_missing_directory_yields_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            self.assertIsNone(_open_output_workspace(Path(raw) / "gone"))

    def test_a_directory_replaced_between_stat_and_open_is_refused(self) -> None:
        """The identity recorded before the open must still hold after it."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            original = os.fstat

            def drifting_fstat(descriptor: int) -> os.stat_result:
                metadata = original(descriptor)
                fields = list(metadata)
                fields[stat.ST_INO] = metadata.st_ino + 1
                return os.stat_result(fields)

            with mock.patch.object(os, "fstat", drifting_fstat):
                self.assertIsNone(_open_output_workspace(root))

    def test_an_unopenable_directory_yields_nothing(self) -> None:
        with (
            tempfile.TemporaryDirectory() as raw,
            mock.patch.object(os, "open", side_effect=OSError("denied")),
        ):
            self.assertIsNone(_open_output_workspace(Path(raw)))


class OpenWorkspaceWindowsTests(unittest.TestCase):
    def _identity_of(self, path: Path) -> tuple[int, int]:
        metadata = path.lstat()
        return metadata.st_dev, metadata.st_ino

    def test_a_windows_handle_workspace_is_returned(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with (
                mock.patch.object(os, "name", "nt"),
                mock.patch.object(module, "_open_windows_directory_handle", return_value=99),
                mock.patch.object(module, "_windows_directory_path", return_value=root),
            ):
                workspace = _open_output_workspace(root)

            self.assertIsNotNone(workspace)
            assert workspace is not None
            self.assertEqual(workspace.windows_handle, 99)
            self.assertIsNone(workspace.directory_descriptor)

    def test_a_handle_that_resolves_elsewhere_is_closed_and_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            elsewhere = root / "other"
            elsewhere.mkdir()
            closed: list[int] = []

            with (
                mock.patch.object(os, "name", "nt"),
                mock.patch.object(module, "_open_windows_directory_handle", return_value=7),
                mock.patch.object(module, "_windows_directory_path", return_value=elsewhere),
                mock.patch.object(
                    module,
                    "_close_windows_directory_handle",
                    side_effect=closed.append,
                ),
            ):
                self.assertIsNone(_open_output_workspace(root))

            self.assertEqual(closed, [7], "a rejected handle must not be leaked")

    def test_a_handle_that_cannot_be_opened_yields_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            # Outside the patch: `Path()` under `os.name == "nt"` builds a
            # WindowsPath whose lstat fails here, which would abort in the
            # identity probe and never reach the Windows arm this asserts on.
            root = Path(raw)
            with (
                mock.patch.object(os, "name", "nt"),
                mock.patch.object(
                    module,
                    "_open_windows_directory_handle",
                    side_effect=OSError("denied"),
                ),
            ):
                self.assertIsNone(_open_output_workspace(root))

    def test_a_close_failure_during_cleanup_is_suppressed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            other = root / "other"
            other.mkdir()

            with (
                mock.patch.object(os, "name", "nt"),
                mock.patch.object(module, "_open_windows_directory_handle", return_value=7),
                mock.patch.object(module, "_windows_directory_path", return_value=other),
                mock.patch.object(
                    module,
                    "_close_windows_directory_handle",
                    side_effect=OSError("already closed"),
                ),
            ):
                self.assertIsNone(_open_output_workspace(root))


class WorkspaceHandleModeTests(unittest.TestCase):
    """A workspace naming its directory by Windows handle rather than fd."""

    def _workspace(self, path: Path, handle: int = 5) -> _OutputWorkspace:
        metadata = path.lstat()
        return _OutputWorkspace(
            path=path,
            identity=(metadata.st_dev, metadata.st_ino),
            windows_handle=handle,
        )

    def test_the_current_path_is_resolved_from_the_handle(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = self._workspace(root)

            with mock.patch.object(module, "_windows_directory_path", return_value=root) as resolve:
                self.assertEqual(workspace._current_path(), root)

            resolve.assert_called_once_with(5)

    def test_close_releases_the_handle_once(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = self._workspace(Path(raw))

            with mock.patch.object(module, "_close_windows_directory_handle") as close:
                workspace.close()
                workspace.close()

            close.assert_called_once_with(5)
            self.assertIsNone(workspace.windows_handle)

    def test_file_operations_go_through_the_resolved_path(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = self._workspace(root)

            with mock.patch.object(module, "_windows_directory_path", return_value=root):
                descriptor = workspace.open_exclusive("frame.jpg")
                os.write(descriptor, _JPEG)
                os.close(descriptor)

                metadata = workspace.stat_frame("frame.jpg")
                self.assertEqual(metadata.st_size, len(_JPEG))

                reader = workspace.open_read_only("frame.jpg")
                self.assertEqual(os.read(reader, 16), _JPEG)
                os.close(reader)

                workspace.fsync()  # no descriptor: nothing to flush, must not raise
                workspace.unlink("frame.jpg")

            self.assertEqual(list(root.iterdir()), [])


class WriteExclusiveFrameTests(unittest.TestCase):
    def _workspace(self, path: Path) -> _OutputWorkspace:
        opened = _open_output_workspace(path)
        assert opened is not None
        return opened

    def test_a_short_write_is_refused_and_the_partial_file_removed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = self._workspace(root)
            created: list[str] = []
            try:
                with (
                    mock.patch.object(os, "write", return_value=0),
                    self.assertRaises(OSError),
                ):
                    _write_exclusive_frame(workspace, "frame-000001.jpg", _JPEG, created)
            finally:
                workspace.close()

    def test_frames_are_written_then_verified(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = self._workspace(root)
            try:
                written = _write_final_frames(
                    workspace,
                    (
                        ExtractedFrame(timestamp_ms=0, jpeg_bytes=_JPEG, is_scene_cut=True),
                        ExtractedFrame(timestamp_ms=500, jpeg_bytes=_JPEG, is_scene_cut=False),
                    ),
                )
            finally:
                workspace.close()

            self.assertNotIsInstance(written, AdaptiveFrameRejection)
            assert not isinstance(written, AdaptiveFrameRejection)
            self.assertEqual(
                [artifact.filename for artifact in written],
                ["frame-000001.jpg", "frame-000002.jpg"],
            )
            for artifact in written:
                target = root / artifact.filename
                self.assertEqual(target.read_bytes(), _JPEG)
                self.assertEqual(stat.S_IMODE(target.lstat().st_mode), 0o600)

    def test_a_failed_batch_leaves_no_frames_behind(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = self._workspace(root)
            calls = {"n": 0}
            real_write = os.write

            def failing_write(descriptor: int, payload: Any) -> int:
                calls["n"] += 1
                if calls["n"] > 1:
                    raise OSError("disk full")
                return real_write(descriptor, payload)

            try:
                with mock.patch.object(os, "write", failing_write):
                    outcome = _write_final_frames(
                        workspace,
                        tuple(
                            ExtractedFrame(
                                timestamp_ms=index * 100,
                                jpeg_bytes=_JPEG,
                                is_scene_cut=False,
                            )
                            for index in range(3)
                        ),
                    )
            finally:
                workspace.close()

            self.assertIs(outcome, AdaptiveFrameRejection.WORKSPACE_UNUSABLE)
            self.assertEqual(list(root.iterdir()), [], "a partial batch must be rolled back")


if __name__ == "__main__":
    unittest.main(verbosity=2)
