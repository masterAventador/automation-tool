"""Frozen B5-07 probe; never included in the production Executor package."""

from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

from automation_tool.executor.browser_runtime import (
    BrowserLaunchRequest,
    BrowserRuntime,
    BrowserRuntimeRejected,
)

_READY = b"browser.runtime.ready\n"
_ERROR = "Packaged browser runtime is unavailable"
_HOLD_ARGUMENT = "--hold-for-process-tree-test"
_MANAGER_BROWSER_FILE = "b5-08-browser-path"
_MANAGER_PROFILE_FILE = "b5-08-profile-path"
_MANAGER_DESCENDANTS_FILE = "b5-08-descendant-pids"


def _write_manager_ready(bootstrap: dict[str, object]) -> None:
    token = bootstrap.get("local_session_token")
    if not isinstance(token, str):
        raise BrowserRuntimeRejected
    try:
        key = bytes.fromhex(token)
    except ValueError:
        raise BrowserRuntimeRejected from None
    event = "executor.healthy"
    message = b"automation-tool.local-executor-event.v1\0" + event.encode("ascii") + b"\0" + b"1.0"
    proof = base64.urlsafe_b64encode(hmac.digest(key, message, hashlib.sha256)).rstrip(b"=")
    source = json.dumps(
        {
            "authenticationProof": "atlep1." + proof.decode("ascii"),
            "event": event,
            "protocolVersion": "1.0",
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    sys.stdout.buffer.write(source + b"\n")
    sys.stdout.buffer.flush()


def _windows_descendant_process_ids(root_process_id: int) -> tuple[int, ...]:
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_snapshot = kernel32.CreateToolhelp32Snapshot
    create_snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    create_snapshot.restype = wintypes.HANDLE
    process_first = kernel32.Process32FirstW
    process_first.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    process_first.restype = wintypes.BOOL
    process_next = kernel32.Process32NextW
    process_next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    process_next.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    snapshot = create_snapshot(0x00000002, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise BrowserRuntimeRejected
    processes: list[tuple[int, int]] = []
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(ProcessEntry)
        available = process_first(snapshot, ctypes.byref(entry))
        while available:
            processes.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID)))
            available = process_next(snapshot, ctypes.byref(entry))
    finally:
        close_handle(snapshot)

    pending = [root_process_id]
    seen = {root_process_id}
    descendants: list[int] = []
    while pending:
        parent = pending.pop()
        for process_id, parent_process_id in processes:
            if parent_process_id == parent and process_id not in seen:
                seen.add(process_id)
                descendants.append(process_id)
                pending.append(process_id)
    return tuple(sorted(descendants))


def _run_manager_mode() -> int:
    runtime = BrowserRuntime()
    try:
        source = sys.stdin.buffer.readline()
        bootstrap = json.loads(source)
        if not isinstance(bootstrap, dict):
            raise BrowserRuntimeRejected
        state_source = bootstrap.get("state_directory")
        if not isinstance(state_source, str):
            raise BrowserRuntimeRejected
        state_directory = Path(state_source)
        browser_path = Path((state_directory / _MANAGER_BROWSER_FILE).read_text(encoding="utf-8"))
        profile_path = Path((state_directory / _MANAGER_PROFILE_FILE).read_text(encoding="utf-8"))
        runtime.start(
            BrowserLaunchRequest(
                executable_path=browser_path,
                profile_directory=profile_path,
            )
        )
        runtime.primary_window()
        deadline = time.monotonic() + 10
        descendants: tuple[int, ...] = ()
        while len(descendants) < 2 and time.monotonic() < deadline:
            descendants = _windows_descendant_process_ids(os.getpid())
            if len(descendants) < 2:
                time.sleep(0.05)
        if len(descendants) < 2:
            raise BrowserRuntimeRejected
        (state_directory / _MANAGER_DESCENDANTS_FILE).write_text(
            "".join(f"{process_id}\n" for process_id in descendants),
            encoding="ascii",
        )
        _write_manager_ready(bootstrap)
        time.sleep(120)
    except BrowserRuntimeRejected:
        runtime.close()
        raise
    except Exception:
        runtime.close()
        raise BrowserRuntimeRejected from None
    runtime.close()
    return 0


def main(arguments: list[str] | None = None) -> int:
    resolved = sys.argv[1:] if arguments is None else arguments
    try:
        if not resolved:
            return _run_manager_mode()
        if len(resolved) not in {2, 3} or (len(resolved) == 3 and resolved[2] != _HOLD_ARGUMENT):
            raise BrowserRuntimeRejected
        request = BrowserLaunchRequest(
            executable_path=Path(resolved[0]),
            profile_directory=Path(resolved[1]),
        )
        runtime = BrowserRuntime()
        with runtime.running(request):
            runtime.primary_window()
            extra_window = runtime.open_window()
            if len(runtime.windows()) != 2:
                raise BrowserRuntimeRejected
            runtime.close_window(extra_window)
            if len(runtime.windows()) != 1:
                raise BrowserRuntimeRejected
            sys.stdout.buffer.write(_READY)
            sys.stdout.buffer.flush()
            if len(resolved) == 3:
                sys.stdin.buffer.read(1)
    except BrowserRuntimeRejected:
        print(_ERROR, file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - frozen process entry
    raise SystemExit(main())
