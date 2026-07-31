#!/usr/bin/env python3
"""Run LE-15 through real Bailian segmentation, TTS and packaged ffprobe."""

from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NoReturn

from run_le_14_acceptance import (
    Le14AcceptanceFailure,
)
from run_le_14_acceptance import (
    _terminate_process_tree as _terminate_owned_process_tree,
)
from run_le_14_acceptance import (
    prepare_verified_media_toolchain as _prepare_verified_media_toolchain,
)
from run_le_14_acceptance import (
    read_bailian_api_key as _read_bailian_api_key,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
DEFAULT_SECRET = REPOSITORY_ROOT / ".local/secrets/bailian-model.json"
ACCEPTANCE_TEST = "tests/integration/test_script_voiceover_real_acceptance.py"
SECRET_PATH_ENVIRONMENT = "AUTOMATION_TOOL_LE15_SECRET_PATH"
TOOLCHAIN_ROOT_ENVIRONMENT = "AUTOMATION_TOOL_LE15_TOOLCHAIN_ROOT"
ACCEPTANCE_TIMEOUT_SECONDS = 300
_PASS_SUMMARY_PATTERN = re.compile(r"^1 passed in \d+(?:\.\d+)?s$", re.MULTILINE)


class Le15AcceptanceFailure(RuntimeError):
    """A fixed failure that reflects no credential or private path."""


def _reject(message: str) -> NoReturn:
    raise Le15AcceptanceFailure(message) from None


def read_bailian_api_key(secret_path: Path) -> str:
    """Reuse the reviewed private credential boundary from LE-13/14."""

    try:
        return _read_bailian_api_key(secret_path)
    except Le14AcceptanceFailure:
        _reject("LE-15 model credential is unavailable")


def prepare_verified_media_toolchain(resource_root: Path) -> Path:
    """Reuse the reviewed current-input media-toolchain preparation."""

    try:
        return _prepare_verified_media_toolchain(resource_root)
    except Le14AcceptanceFailure:
        _reject("LE-15 packaged media toolchain is unavailable")


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    try:
        _terminate_owned_process_tree(process)
    except (OSError, Le14AcceptanceFailure):
        _reject("LE-15 real acceptance failed")


def run_acceptance(secret_path: Path) -> None:
    """Run one isolated real test with paths, never the key, in its environment."""

    api_key = read_bailian_api_key(secret_path)
    completed: subprocess.CompletedProcess[str] | None = None
    acceptance_succeeded = False
    with tempfile.TemporaryDirectory(
        prefix="automation-tool-le15-acceptance-"
    ) as directory:
        acceptance_root = Path(directory).resolve()
        prepared_toolchain = prepare_verified_media_toolchain(
            acceptance_root / "runtime"
        )
        toolchain = prepared_toolchain.resolve()
        pytest_root = acceptance_root / "pytest"
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("PYTEST_", "PYTHON"))
        }
        environment[SECRET_PATH_ENVIRONMENT] = os.fspath(secret_path)
        environment[TOOLCHAIN_ROOT_ENVIRONMENT] = os.fspath(toolchain)
        command = [
            sys.executable,
            "-m",
            "pytest",
            ACCEPTANCE_TEST,
            "-q",
            "-s",
            "--basetemp",
            os.fspath(pytest_root),
            "-o",
            "addopts=",
        ]
        process: subprocess.Popen[str] | None = None
        cancellation_requested = False
        cleanup_started = False
        handler_restored = False

        def request_cancellation(
            _signal_number: int,
            _frame: object,
        ) -> None:
            nonlocal cancellation_requested
            cancellation_requested = True
            if process is not None and not cleanup_started:
                _reject("LE-15 real acceptance failed")

        previous_sigterm_handler = signal.signal(signal.SIGTERM, request_cancellation)

        def restore_sigterm_handler() -> None:
            nonlocal handler_restored
            if not handler_restored:
                signal.signal(signal.SIGTERM, previous_sigterm_handler)
                handler_restored = True

        def cleanup_owned_process() -> None:
            nonlocal cleanup_started
            if process is None or cleanup_started:
                return
            cleanup_started = True
            _terminate_process_tree(process)

        timed_out = False
        launch_failed = False
        try:
            try:
                process = subprocess.Popen(
                    command,
                    cwd=BACKEND_ROOT,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    creationflags=(
                        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                        if os.name == "nt"
                        else 0
                    ),
                    start_new_session=os.name != "nt",
                )
            except OSError:
                launch_failed = True
            if not launch_failed:
                assert process is not None
                if cancellation_requested:
                    _reject("LE-15 real acceptance failed")
                try:
                    stdout, stderr = process.communicate(
                        timeout=ACCEPTANCE_TIMEOUT_SECONDS
                    )
                except subprocess.TimeoutExpired:
                    timed_out = True
                if not timed_out:
                    completed = subprocess.CompletedProcess(
                        command,
                        process.returncode,
                        stdout,
                        stderr,
                    )
                    combined = completed.stdout + completed.stderr
                    acceptance_succeeded = not (
                        api_key in combined
                        or os.fspath(secret_path) in combined
                        or os.fspath(acceptance_root) in combined
                        or os.fspath(prepared_toolchain) in combined
                        or os.fspath(toolchain) in combined
                        or completed.returncode != 0
                        or _PASS_SUMMARY_PATTERN.search(completed.stdout) is None
                    )
                    if not acceptance_succeeded:
                        cleanup_owned_process()
        except BaseException:
            cleanup_owned_process()
            raise
        finally:
            restore_sigterm_handler()
        if timed_out:
            cleanup_owned_process()
    if launch_failed or timed_out or completed is None or not acceptance_succeeded:
        _reject("LE-15 real acceptance failed")
    print(completed.stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secret", type=Path, default=DEFAULT_SECRET)
    arguments = parser.parse_args()
    try:
        secret_path = Path(os.path.abspath(arguments.secret))
        run_acceptance(secret_path)
    except (OSError, Le15AcceptanceFailure) as error:
        message = (
            str(error)
            if isinstance(error, Le15AcceptanceFailure) and str(error)
            else "LE-15 real acceptance failed"
        )
        raise SystemExit(message) from None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
