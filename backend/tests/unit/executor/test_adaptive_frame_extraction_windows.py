"""COV-02: the Windows directory-handle helpers in adaptive frame extraction.

These four helpers wrap `kernel32` calls that the Linux coverage runner can
never execute for real. They were nonetheless completely uncovered -- not even
their `os.name != "nt"` guards had run, so the only line coverage credited to
them was the `def` itself.

The approach here is platform-value injection, the same remedy the plan names
for `windows_candidate.py`: patch `os.name` and `ctypes.CDLL`, then drive the
real function bodies. `ctypes.wintypes` imports fine on macOS/Linux (it is plain
Python; only loading `kernel32.dll` fails), so the argtype/restype assignments
and buffer handling execute unmodified.

What this proves is what a unit test can prove about a thin FFI wrapper: the
right entry point is called, the documented flags are passed, and every failure
signal the API can return is converted into `OSError` rather than silently
yielding a bogus handle or path. What it deliberately does not claim is that the
call works against a real kernel32 -- that stays with the Windows runners.
"""

from __future__ import annotations

import ctypes
import os
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from automation_tool.executor.adaptive_frame_extraction import (
    _close_windows_directory_handle,
    _open_windows_directory_handle,
    _windows_directory_path,
    _windows_last_error,
)

_INVALID_HANDLE = ctypes.c_void_p(-1).value
_VALID_HANDLE = 0x1234


def _kernel32(**functions: Any) -> mock.MagicMock:
    """A stand-in for the loaded DLL exposing only the named entry points."""
    library = mock.MagicMock()
    for name, value in functions.items():
        setattr(library, name, value)
    return library


class WindowsGuardTests(unittest.TestCase):
    """Off Windows every helper must refuse rather than attempt the call."""

    def test_each_helper_refuses_when_the_platform_is_not_windows(self) -> None:
        with mock.patch.object(os, "name", "posix"):
            for call in (
                lambda: _open_windows_directory_handle(Path("/tmp/x")),
                lambda: _windows_directory_path(_VALID_HANDLE),
                lambda: _close_windows_directory_handle(_VALID_HANDLE),
            ):
                with self.assertRaises(OSError) as caught:
                    call()
                self.assertIn("unavailable", str(caught.exception))


class OpenWindowsDirectoryHandleTests(unittest.TestCase):
    def test_a_valid_handle_is_returned_with_the_documented_flags(self) -> None:
        # Built outside the patch on purpose: `Path.__new__` picks WindowsPath
        # vs PosixPath from `os.name`, so a path constructed while the patch is
        # active stringifies with backslashes and would not compare equal to one
        # built here.
        target = Path("/tmp/workspace")
        create_file = mock.MagicMock(return_value=_VALID_HANDLE)
        with (
            mock.patch.object(os, "name", "nt"),
            mock.patch.object(ctypes, "CDLL", return_value=_kernel32(CreateFileW=create_file)),
        ):
            handle = _open_windows_directory_handle(target)

        self.assertEqual(handle, _VALID_HANDLE)
        arguments = create_file.call_args.args
        self.assertEqual(arguments[0], os.fspath(target))
        self.assertEqual(arguments[1], 0x0001, "FILE_LIST_DIRECTORY")
        self.assertEqual(arguments[2], 0x00000001 | 0x00000002, "share read|write")
        self.assertEqual(arguments[4], 3, "OPEN_EXISTING")
        self.assertEqual(
            arguments[5],
            0x02000000 | 0x00200000,
            "BACKUP_SEMANTICS|OPEN_REPARSE_POINT -- a directory handle that "
            "refuses to follow a reparse point is the whole point of this call",
        )

    def test_both_failure_sentinels_become_oserror(self) -> None:
        for sentinel in (None, _INVALID_HANDLE):
            with (
                self.subTest(sentinel=sentinel),
                mock.patch.object(os, "name", "nt"),
                mock.patch.object(
                    ctypes,
                    "CDLL",
                    return_value=_kernel32(
                        CreateFileW=mock.MagicMock(return_value=sentinel),
                        get_last_error=mock.MagicMock(return_value=5),
                    ),
                ),
                mock.patch.object(ctypes, "get_last_error", return_value=5, create=True),
                self.assertRaises(OSError),
            ):
                _open_windows_directory_handle(Path("/tmp/workspace"))


class WindowsDirectoryPathTests(unittest.TestCase):
    def test_the_resolved_final_path_is_returned(self) -> None:
        # The production code probes the required length first, then allocates
        # `required + 1`; the second call must report fewer characters than the
        # buffer holds or the path is treated as truncated.
        final_path = "C:\\out"

        def get_final_path(handle: int, buffer: Any, size: int, flags: int) -> int:
            if buffer is None:
                return len(final_path)
            buffer.value = final_path
            return len(final_path)

        with (
            mock.patch.object(os, "name", "nt"),
            mock.patch.object(
                ctypes,
                "CDLL",
                return_value=_kernel32(GetFinalPathNameByHandleW=get_final_path),
            ),
        ):
            resolved = _windows_directory_path(_VALID_HANDLE)

        self.assertEqual(os.fspath(resolved), final_path)

    def test_a_zero_length_probe_is_rejected(self) -> None:
        with (
            mock.patch.object(os, "name", "nt"),
            mock.patch.object(
                ctypes,
                "CDLL",
                return_value=_kernel32(
                    GetFinalPathNameByHandleW=mock.MagicMock(return_value=0),
                ),
            ),
            mock.patch.object(ctypes, "get_last_error", return_value=2, create=True),
            self.assertRaises(OSError),
        ):
            _windows_directory_path(_VALID_HANDLE)

    def test_a_second_call_that_writes_nothing_or_overflows_is_rejected(self) -> None:
        # `written == 0` is failure; `written >= len(buffer)` means the path grew
        # between the two calls and the buffer no longer holds all of it. Both
        # must refuse instead of returning a truncated path.
        for written in (0, 999):
            calls = {"n": 0}

            # Both loop-carried names are bound as defaults: without that the
            # closure would read whatever the last iteration left behind.
            def get_final_path(
                handle: int,
                buffer: Any,
                size: int,
                flags: int,
                _w: int = written,
                _calls: dict[str, int] = calls,
            ) -> int:
                _calls["n"] += 1
                return 8 if _calls["n"] == 1 else _w

            with (
                self.subTest(written=written),
                mock.patch.object(os, "name", "nt"),
                mock.patch.object(
                    ctypes,
                    "CDLL",
                    return_value=_kernel32(GetFinalPathNameByHandleW=get_final_path),
                ),
                mock.patch.object(ctypes, "get_last_error", return_value=2, create=True),
                self.assertRaises(OSError),
            ):
                _windows_directory_path(_VALID_HANDLE)


class CloseWindowsDirectoryHandleTests(unittest.TestCase):
    def test_a_successful_close_returns_none(self) -> None:
        close_handle = mock.MagicMock(return_value=1)
        with (
            mock.patch.object(os, "name", "nt"),
            mock.patch.object(ctypes, "CDLL", return_value=_kernel32(CloseHandle=close_handle)),
        ):
            _close_windows_directory_handle(_VALID_HANDLE)
        close_handle.assert_called_once_with(_VALID_HANDLE)

    def test_a_failed_close_raises_rather_than_leaking_the_handle_silently(self) -> None:
        with (
            mock.patch.object(os, "name", "nt"),
            mock.patch.object(
                ctypes,
                "CDLL",
                return_value=_kernel32(CloseHandle=mock.MagicMock(return_value=0)),
            ),
            mock.patch.object(ctypes, "get_last_error", return_value=6, create=True),
            self.assertRaises(OSError),
        ):
            _close_windows_directory_handle(_VALID_HANDLE)


class WindowsLastErrorTests(unittest.TestCase):
    def test_an_integer_error_code_is_returned(self) -> None:
        module = mock.MagicMock()
        module.get_last_error = mock.MagicMock(return_value=32)
        self.assertEqual(_windows_last_error(module), 32)

    def test_a_non_integer_error_code_collapses_to_zero(self) -> None:
        """A bool is an int subclass; the production check is `type(...) is int`."""
        module = mock.MagicMock()
        module.get_last_error = mock.MagicMock(return_value=True)
        self.assertEqual(_windows_last_error(module), 0)

    def test_a_module_without_the_getter_is_refused(self) -> None:
        class _NoGetter:
            pass

        with self.assertRaises(OSError):
            _windows_last_error(_NoGetter())


if __name__ == "__main__":
    unittest.main(verbosity=2)
