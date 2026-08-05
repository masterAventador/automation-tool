#!/usr/bin/env python3
"""Read the process facts Windows ownership decisions are made from.

macOS gets all of this from `ps`: pid, ppid, start time, the command line, and —
with `ps eww` — the environment a process was launched with. Windows has no
single command that answers all five, so this module assembles them:

* pid / ppid come from a Toolhelp process snapshot, which is cheap and covers
  every process on the machine;
* the image path and creation time come from a limited-information handle, which
  opens for most same-user processes;
* the command line and environment come from the process parameters block, which
  needs `PROCESS_VM_READ` as well.

Everything degrades to "unknown" rather than raising. A machine-wide snapshot
walks hundreds of processes belonging to other users and to protected system
services; failing on those would make an ordinary snapshot impossible, while
reporting them as unreadable lets the ownership rules do what they already do —
decline to claim a process they cannot identify.

Measured on 2026-08-05: 255 processes, table in under a millisecond, 113 image
paths and 86 command lines readable, 15 ms for the lot.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

TH32CS_SNAPPROCESS: Final = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION: Final = 0x1000
PROCESS_VM_READ: Final = 0x0010
INVALID_HANDLE_VALUE: Final = -1
# Offsets into the 64-bit PEB and RTL_USER_PROCESS_PARAMETERS. These are stable
# published layout for x64 Windows; the module refuses to guess on any other
# architecture rather than reading a wrong address.
PEB_PROCESS_PARAMETERS_OFFSET: Final = 0x20
PARAMETERS_COMMAND_LINE_OFFSET: Final = 0x70
PARAMETERS_ENVIRONMENT_OFFSET: Final = 0x80
PARAMETERS_ENVIRONMENT_SIZE_OFFSET: Final = 0x3F0
# One megabyte of environment is far past anything real; it bounds a read whose
# length comes from another process's memory.
ENVIRONMENT_READ_LIMIT: Final = 1 << 20
ANCESTOR_WALK_LIMIT: Final = 64


class WindowsProcessesUnavailable(RuntimeError):
    """The process table itself could not be taken."""


@dataclass(frozen=True)
class WindowsProcess:
    pid: int
    ppid: int
    executable_name: str


class _ProcessEntry(ctypes.Structure):
    _fields_ = (
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    )


class _UnicodeString(ctypes.Structure):
    _fields_ = (
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", ctypes.c_void_p),
    )


class _ProcessBasicInformation(ctypes.Structure):
    _fields_ = (
        ("Reserved1", ctypes.c_void_p),
        ("PebBaseAddress", ctypes.c_void_p),
        ("Reserved2", ctypes.c_void_p * 2),
        ("UniqueProcessId", ctypes.c_void_p),
        ("Reserved3", ctypes.c_void_p),
    )


def _kernel32() -> ctypes.WinDLL:
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _ntdll() -> ctypes.WinDLL:
    return ctypes.WinDLL("ntdll", use_last_error=True)


def process_table() -> dict[int, WindowsProcess]:
    """Every process on the machine, by pid, with its parent."""
    kernel32 = _kernel32()
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot in {0, INVALID_HANDLE_VALUE, ctypes.c_void_p(-1).value}:
        raise WindowsProcessesUnavailable("the Windows process table is unavailable")
    table: dict[int, WindowsProcess] = {}
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(_ProcessEntry)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            raise WindowsProcessesUnavailable("the Windows process table is empty")
        while True:
            pid = int(entry.th32ProcessID)
            table[pid] = WindowsProcess(
                pid=pid,
                ppid=int(entry.th32ParentProcessID),
                executable_name=str(entry.szExeFile),
            )
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    return table


def ancestor_process_ids(
    process_id: int,
    *,
    table: dict[int, WindowsProcess] | None = None,
    limit: int = ANCESTOR_WALK_LIMIT,
) -> set[int]:
    """Walk upwards from `process_id`, stopping at a repeat or the root.

    A pid may be reused by a later process, so a parent chain can point back
    into itself; the visited set makes that terminate rather than spin.
    """
    resolved = process_table() if table is None else table
    ancestors: set[int] = set()
    current = process_id
    for _ in range(limit):
        record = resolved.get(current)
        if record is None or not record.ppid or record.ppid in ancestors:
            break
        ancestors.add(record.ppid)
        current = record.ppid
    return ancestors


class _Handle:
    """An open process handle that closes itself, or nothing at all."""

    def __init__(self, process_id: int, access: int) -> None:
        self._kernel32 = _kernel32()
        raw = self._kernel32.OpenProcess(access, False, process_id)
        self.value: int | None = raw or None

    def __enter__(self) -> _Handle:
        return self

    def __exit__(self, *_: object) -> None:
        if self.value is not None:
            self._kernel32.CloseHandle(wintypes.HANDLE(self.value))
            self.value = None


def image_path(process_id: int) -> str | None:
    """The executable a process is running, or None if it cannot be asked."""
    with _Handle(process_id, PROCESS_QUERY_LIMITED_INFORMATION) as handle:
        if handle.value is None:
            return None
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        ok = handle._kernel32.QueryFullProcessImageNameW(
            wintypes.HANDLE(handle.value), 0, buffer, ctypes.byref(size)
        )
        return buffer.value if ok else None


def created_at(process_id: int) -> str:
    """When a process started, as a comparable string, or "" if unknown.

    This is what makes a pid mean something. Windows reuses process ids freely,
    and every ownership decision here compares whole records — without a start
    time a recycled pid compares equal to the process this run launched, which
    is precisely what the macOS side spends `lstart` to prevent.
    """
    creation = wintypes.FILETIME()
    exited = wintypes.FILETIME()
    kernel_time = wintypes.FILETIME()
    user_time = wintypes.FILETIME()
    with _Handle(process_id, PROCESS_QUERY_LIMITED_INFORMATION) as handle:
        if handle.value is None:
            return ""
        ok = handle._kernel32.GetProcessTimes(
            wintypes.HANDLE(handle.value),
            ctypes.byref(creation),
            ctypes.byref(exited),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        )
        if not ok:
            return ""
    ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
    if not ticks:
        return ""
    # FILETIME counts 100-nanosecond intervals from 1601-01-01 UTC.
    seconds, remainder = divmod(ticks - 116_444_736_000_000_000, 10_000_000)
    moment = datetime.fromtimestamp(seconds, tz=UTC)
    return f"{moment.isoformat()}.{remainder:07d}"


def _read_memory(kernel32: ctypes.WinDLL, handle: int, address: int, size: int) -> bytes:
    if size <= 0 or not address:
        return b""
    buffer = (ctypes.c_char * size)()
    read = ctypes.c_size_t(0)
    ok = kernel32.ReadProcessMemory(
        wintypes.HANDLE(handle),
        ctypes.c_void_p(address),
        buffer,
        ctypes.c_size_t(size),
        ctypes.byref(read),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "ReadProcessMemory failed")
    return bytes(buffer[: read.value])


def _process_parameters(kernel32: ctypes.WinDLL, handle: int) -> int | None:
    information = _ProcessBasicInformation()
    written = wintypes.ULONG(0)
    status = _ntdll().NtQueryInformationProcess(
        wintypes.HANDLE(handle),
        0,
        ctypes.byref(information),
        ctypes.sizeof(information),
        ctypes.byref(written),
    )
    if status != 0:
        return None
    peb = ctypes.cast(information.PebBaseAddress, ctypes.c_void_p).value
    if not peb:
        return None
    raw = _read_memory(kernel32, handle, peb + PEB_PROCESS_PARAMETERS_OFFSET, 8)
    if len(raw) != 8:
        return None
    return int.from_bytes(raw, "little") or None


def command_line(process_id: int) -> str | None:
    """The full command line of a process, or None if it cannot be read."""
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        return None
    access = PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ
    with _Handle(process_id, access) as handle:
        if handle.value is None:
            return None
        kernel32 = handle._kernel32
        try:
            parameters = _process_parameters(kernel32, handle.value)
            if parameters is None:
                return None
            raw = _read_memory(
                kernel32,
                handle.value,
                parameters + PARAMETERS_COMMAND_LINE_OFFSET,
                ctypes.sizeof(_UnicodeString),
            )
            if len(raw) != ctypes.sizeof(_UnicodeString):
                return None
            rendered = _UnicodeString.from_buffer_copy(raw)
            address = ctypes.cast(rendered.Buffer, ctypes.c_void_p).value or 0
            payload = _read_memory(kernel32, handle.value, address, rendered.Length)
        except OSError:
            return None
    return payload.decode("utf-16-le", errors="replace") or None


def environment(process_id: int) -> dict[str, str] | None:
    """The environment a process was launched with, or None if unreadable.

    This is the Windows half of "is this reparented Chromium helper ours". The
    launcher puts a nonce in the environment; a helper that has been reparented
    away from the App still carries it, and nothing else does.
    """
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        return None
    access = PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ
    with _Handle(process_id, access) as handle:
        if handle.value is None:
            return None
        kernel32 = handle._kernel32
        try:
            parameters = _process_parameters(kernel32, handle.value)
            if parameters is None:
                return None
            address = int.from_bytes(
                _read_memory(
                    kernel32,
                    handle.value,
                    parameters + PARAMETERS_ENVIRONMENT_OFFSET,
                    8,
                ),
                "little",
            )
            declared = int.from_bytes(
                _read_memory(
                    kernel32,
                    handle.value,
                    parameters + PARAMETERS_ENVIRONMENT_SIZE_OFFSET,
                    8,
                ),
                "little",
            )
            payload = _read_memory(
                kernel32,
                handle.value,
                address,
                min(declared, ENVIRONMENT_READ_LIMIT),
            )
        except OSError:
            return None
    if not payload:
        return None
    values: dict[str, str] = {}
    for item in payload.decode("utf-16-le", errors="replace").split("\0"):
        # `=C:=C:\path` style per-drive entries start with `=`; splitting on the
        # first `=` after position 0 keeps their names intact.
        separator = item.find("=", 1)
        if separator > 0:
            values[item[:separator]] = item[separator + 1 :]
    return values or None


__all__ = [
    "WindowsProcess",
    "WindowsProcessesUnavailable",
    "ancestor_process_ids",
    "command_line",
    "created_at",
    "environment",
    "image_path",
    "process_table",
]
