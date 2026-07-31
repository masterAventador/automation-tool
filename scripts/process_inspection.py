#!/usr/bin/env python3
"""Cross-platform process inspection for isolated acceptance runs."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Callable, Iterable
from contextlib import suppress
from typing import Final

_PROCESS_NEEDLE_ENVIRONMENT: Final = "AUTOMATION_TOOL_PROCESS_NEEDLE"


class ProcessInspectionFailed(RuntimeError):
    """The host process table could not be inspected or cleaned."""


def process_ids_matching(
    needle: str,
    *,
    platform: str = os.name,
    current_pid: int | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> set[int]:
    """Return live process ids whose command line contains ``needle``.

    Windows receives the marker through the environment rather than the
    PowerShell command line, so spaces, backslashes and shell metacharacters
    cannot change the query.
    """
    if not needle or len(needle) > 4096 or "\x00" in needle:
        raise ProcessInspectionFailed("process marker is invalid")
    if platform == "nt":
        command = [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            (
                f"$needle = $env:{_PROCESS_NEEDLE_ENVIRONMENT}; "
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.CommandLine -and $_.CommandLine.Contains($needle) } | "
                "ForEach-Object { $_.ProcessId }"
            ),
        ]
        environment = {**os.environ, _PROCESS_NEEDLE_ENVIRONMENT: needle}
    else:
        command = ["pgrep", "-f", needle]
        environment = None
    completed = runner(
        command,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
        timeout=60,
    )
    if completed.returncode != 0:
        if platform != "nt" and completed.returncode == 1:
            return set()
        raise ProcessInspectionFailed("host process inspection failed")
    inspector = os.getpid() if current_pid is None else current_pid
    return {
        process_id
        for field in completed.stdout.split()
        if field.isdigit()
        and (process_id := int(field)) > 0
        and process_id != inspector
    }


def terminate_process(
    process_id: int,
    *,
    platform: str = os.name,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    killer: Callable[[int, int], None] = os.kill,
) -> None:
    """Kill one owned process tree, tolerating a process that already exited."""
    if type(process_id) is not int or process_id <= 0 or process_id == os.getpid():
        raise ProcessInspectionFailed("process id is invalid")
    if platform == "nt":
        runner(
            ["taskkill", "/PID", str(process_id), "/T", "/F"],
            capture_output=True,
            check=False,
            timeout=60,
        )
        return
    with suppress(ProcessLookupError):
        killer(process_id, signal.SIGKILL)


def terminate_matching_processes(
    marker: str,
    *,
    baseline: Iterable[int] = (),
    timeout_seconds: float = 20,
    process_ids: Callable[[str], set[int]] = process_ids_matching,
    terminate: Callable[[int], None] = terminate_process,
    monotonic: Callable[[], float] = time.monotonic,
    pause: Callable[[float], None] = time.sleep,
) -> set[int]:
    """Terminate only matches created after ``baseline`` and return survivors."""
    if timeout_seconds <= 0:
        raise ProcessInspectionFailed("process cleanup timeout is invalid")
    preserved = set(baseline)
    owned = process_ids(marker) - preserved
    for process_id in sorted(owned):
        terminate(process_id)
    deadline = monotonic() + timeout_seconds
    remaining = process_ids(marker) - preserved
    while remaining and monotonic() < deadline:
        pause(0.2)
        remaining = process_ids(marker) - preserved
    return remaining


__all__ = [
    "ProcessInspectionFailed",
    "process_ids_matching",
    "terminate_matching_processes",
    "terminate_process",
]
