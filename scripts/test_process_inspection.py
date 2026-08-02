#!/usr/bin/env python3
"""Cross-platform process inspection and owned-residue cleanup checks."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from process_inspection import (  # noqa: E402
    process_ids_matching,
    terminate_matching_processes,
)


def check_windows_query_uses_cim_without_putting_the_marker_on_the_command_line() -> (
    None
):
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "10\n20\n30\n", "")

    marker = r"C:\Users\operator\App Data\bm-08"
    found = process_ids_matching(
        marker,
        platform="nt",
        current_pid=20,
        runner=run,
    )

    assert found == {10, 30}
    command, kwargs = calls[0]
    assert command[:3] == ["powershell", "-NoProfile", "-NonInteractive"]
    assert marker not in " ".join(command)
    environment = kwargs["env"]
    assert isinstance(environment, dict)
    assert environment["AUTOMATION_TOOL_PROCESS_NEEDLE"] == marker


def check_posix_query_uses_pgrep_and_excludes_the_inspector() -> None:
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "41\n42\n", "")

    found = process_ids_matching(
        "/private/tmp/bm-08",
        platform="posix",
        current_pid=42,
        runner=run,
    )

    assert found == {41}
    assert calls == [["pgrep", "-f", "/private/tmp/bm-08"]]


def check_cleanup_terminates_only_processes_started_after_the_baseline() -> None:
    states = iter(({7, 8, 9}, {7}))
    terminated: list[int] = []
    observed: set[int] = set()

    remaining = terminate_matching_processes(
        "bm-08-marker",
        baseline={7},
        observed=observed,
        timeout_seconds=1,
        process_ids=lambda _marker: set(next(states)),
        terminate=terminated.append,
        monotonic=iter((0.0, 0.1)).__next__,
        pause=lambda _seconds: None,
    )

    assert terminated == [8, 9]
    assert observed == {8, 9}
    assert remaining == set()


def check_cleanup_detects_and_terminates_a_process_that_appears_during_cleanup() -> None:
    states = iter(({7}, {7, 11}, {7}))
    terminated: list[int] = []
    observed: set[int] = set()

    remaining = terminate_matching_processes(
        "bm-16-marker",
        baseline={7},
        observed=observed,
        timeout_seconds=1,
        process_ids=lambda _marker: set(next(states)),
        terminate=terminated.append,
        monotonic=iter((0.0, 0.1, 0.2)).__next__,
        pause=lambda _seconds: None,
    )

    assert terminated == [11]
    assert observed == {11}
    assert remaining == set()


CHECKS = (
    check_windows_query_uses_cim_without_putting_the_marker_on_the_command_line,
    check_posix_query_uses_pgrep_and_excludes_the_inspector,
    check_cleanup_terminates_only_processes_started_after_the_baseline,
    check_cleanup_detects_and_terminates_a_process_that_appears_during_cleanup,
)


def main() -> int:
    for check in CHECKS:
        check()
        print(f"ok  {check.__name__}")
    print(f"process inspection checks passed ({len(CHECKS)} checks)")
    print(f"executed checks: {len(CHECKS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
