#!/usr/bin/env python3
"""Run LE-14 through real human speech, Silero VAD, Bailian and PostgreSQL."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.client
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, NoReturn

from acceptance_postgres import WINDOWS_POSTGRES_ROOT_ENVIRONMENT
from run_le_13_acceptance import Le13AcceptanceFailure
from run_le_13_acceptance import (
    prepare_verified_media_toolchain as _prepare_verified_media_toolchain,
)
from run_le_13_acceptance import read_bailian_api_key as _read_bailian_api_key
from silero_vad_assets import (
    SileroVadAssetContractRejected,
    SileroVadAssetUnavailable,
)
from silero_vad_assets import ensure_silero_vad_assets as _ensure_silero_vad_assets

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
DEFAULT_SECRET = REPOSITORY_ROOT / ".local/secrets/bailian-model.json"
CONTRACT_PATH = REPOSITORY_ROOT / "contracts/quality/le-14-speech-acceptance.v1.json"
ACCEPTANCE_TEST = "tests/integration/test_material_speech_real_acceptance.py"
SECRET_PATH_ENVIRONMENT = "AUTOMATION_TOOL_LE14_SECRET_PATH"
TOOLCHAIN_ROOT_ENVIRONMENT = "AUTOMATION_TOOL_LE14_TOOLCHAIN_ROOT"
VOICE_PATH_ENVIRONMENT = "AUTOMATION_TOOL_LE14_VOICE_PATH"
CONTRACT_ID = "automation-tool.le-14-speech-acceptance.v1"
DATASET_HOMEPAGE = "https://www.openslr.org/12"
DATASET_NAME = "LibriSpeech"
DATASET_SPLIT = "dev-clean"
FIXTURE_UTTERANCE_ID = "1272-128104-0000"
FIXTURE_SOURCE_PATH = "1272/128104/1272-128104-0000.flac"
FIXTURE_SOURCE_URL = (
    "https://qianwen-res.oss-cn-beijing.aliyuncs.com/"
    "Qwen2-Audio/audio/1272-128104-0000.flac"
)
LICENSE_SOURCE_URL = DATASET_HOMEPAGE
MAXIMUM_CONTRACT_BYTES = 64 * 1024
MAXIMUM_FIXTURE_BYTES = 256 * 1024
FETCH_TIMEOUT_SECONDS = 120
ACCEPTANCE_TIMEOUT_SECONDS = 300
CLEANUP_TIMEOUT_SECONDS = 60
PROCESS_STOP_TIMEOUT_SECONDS = 10
PROCESS_KILL_TIMEOUT_SECONDS = 5
MAXIMUM_WINDOWS_DESCENDANTS = 4096
WINDOWS_CLEANUP_ROOT_PID_ENVIRONMENT = "AUTOMATION_TOOL_CLEANUP_ROOT_PID"
_PASS_SUMMARY_PATTERN = re.compile(r"^1 passed in \d+(?:\.\d+)?s$", re.MULTILINE)


class Le14AcceptanceFailure(RuntimeError):
    """A fixed failure that does not reflect credentials or private paths."""


@dataclass(frozen=True, slots=True)
class VoiceFixtureContract:
    source_url: str
    bytes: int
    sha256: str


class _RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        del req, code, msg, headers, newurl
        with contextlib.suppress(OSError, ValueError):
            fp.close()
        _reject("LE-14 speech fixture is unavailable")


def _reject(message: str) -> NoReturn:
    raise Le14AcceptanceFailure(message) from None


def _digest(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        _reject("LE-14 speech fixture contract is invalid")
    return value


def _load_fixture_contract(path: Path) -> VoiceFixtureContract:
    try:
        metadata = path.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or not 0 < metadata.st_size <= MAXIMUM_CONTRACT_BYTES
        ):
            _reject("LE-14 speech fixture contract is invalid")
        document = json.loads(path.read_bytes().decode("utf-8"))
    except Le14AcceptanceFailure:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        _reject("LE-14 speech fixture contract is invalid")
    if not isinstance(document, dict):
        _reject("LE-14 speech fixture contract is invalid")
    policy = document.get("policy")
    upstream = document.get("upstream")
    fixture = document.get("fixture")
    license_record = document.get("license")
    if (
        document.get("schemaVersion") != 1
        or document.get("id") != CONTRACT_ID
        or not isinstance(policy, dict)
        or policy
        != {
            "acceptanceOnly": True,
            "shipped": False,
            "applicationRuntimeDownloadAllowed": False,
        }
        or not isinstance(upstream, dict)
        or upstream
        != {
            "datasetHomepage": DATASET_HOMEPAGE,
            "dataset": DATASET_NAME,
            "split": DATASET_SPLIT,
            "utteranceId": FIXTURE_UTTERANCE_ID,
        }
        or not isinstance(fixture, dict)
        or fixture.get("sourcePath") != FIXTURE_SOURCE_PATH
        or fixture.get("sourceUrl") != FIXTURE_SOURCE_URL
        or fixture.get("format")
        != {
            "container": "flac",
            "codec": "flac",
            "channels": 1,
            "sampleRateHz": 16_000,
            "durationMs": 5_855,
        }
        or not isinstance(license_record, dict)
        or license_record
        != {
            "spdx": "CC-BY-4.0",
            "sourceUrl": LICENSE_SOURCE_URL,
            "attribution": (
                "LibriSpeech: an ASR corpus based on public domain audio books; "
                "Vassil Panayotov, Guoguo Chen, Daniel Povey and Sanjeev Khudanpur"
            ),
        }
    ):
        _reject("LE-14 speech fixture contract is invalid")
    byte_count = fixture.get("bytes")
    if type(byte_count) is not int or not 44 <= byte_count <= MAXIMUM_FIXTURE_BYTES:
        _reject("LE-14 speech fixture contract is invalid")
    return VoiceFixtureContract(
        source_url=FIXTURE_SOURCE_URL,
        bytes=byte_count,
        sha256=_digest(fixture.get("sha256")),
    )


def _fetch_fixture(url: str) -> bytes:
    if url != FIXTURE_SOURCE_URL:
        _reject("LE-14 speech fixture is unavailable")
    try:
        opener = urllib.request.build_opener(_RejectRedirectHandler())
        with opener.open(url, timeout=FETCH_TIMEOUT_SECONDS) as response:
            if (
                getattr(response, "status", None) != 200
                or getattr(response, "geturl", lambda: None)() != FIXTURE_SOURCE_URL
            ):
                _reject("LE-14 speech fixture is unavailable")
            payload = bytes(response.read(MAXIMUM_FIXTURE_BYTES + 1))
    except (
        OSError,
        ValueError,
        http.client.HTTPException,
        urllib.error.URLError,
    ):
        _reject("LE-14 speech fixture is unavailable")
    if len(payload) > MAXIMUM_FIXTURE_BYTES:
        _reject("LE-14 speech fixture is unavailable")
    return payload


def prepare_voice_fixture(
    destination: Path,
    *,
    contract_path: Path = CONTRACT_PATH,
    fetch: Callable[[str], bytes] | None = None,
) -> Path:
    """Fetch the one acceptance-only voice fixture into a new private file."""

    contract = _load_fixture_contract(contract_path)
    try:
        destination.lstat()
    except FileNotFoundError:
        pass
    except OSError:
        _reject("LE-14 speech fixture is unavailable")
    else:
        _reject("LE-14 speech fixture is unavailable")
    try:
        payload = (
            _fetch_fixture(contract.source_url)
            if fetch is None
            else fetch(contract.source_url)
        )
    except Le14AcceptanceFailure:
        raise
    except Exception:  # noqa: BLE001 - injected fetchers are an untrusted boundary
        _reject("LE-14 speech fixture is unavailable")
    if (
        type(payload) is not bytes
        or len(payload) != contract.bytes
        or hashlib.sha256(payload).hexdigest() != contract.sha256
    ):
        _reject("LE-14 speech fixture is unavailable")
    descriptor: int | None = None
    created = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(destination, flags, 0o600)
        created = True
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("short write")
            remaining = remaining[written:]
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        current = destination.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(current.st_mode)
            or not os.path.samestat(metadata, current)
            or metadata.st_size != contract.bytes
            or (os.name != "nt" and stat.S_IMODE(metadata.st_mode) != 0o600)
        ):
            raise OSError("fixture output changed")
    except OSError:
        if created:
            try:
                destination.unlink()
            except OSError:
                pass
        _reject("LE-14 speech fixture is unavailable")
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    return destination


def read_bailian_api_key(secret_path: Path) -> str:
    try:
        return _read_bailian_api_key(secret_path)
    except Le13AcceptanceFailure:
        _reject("LE-14 model credential is unavailable")


def prepare_verified_media_toolchain(resource_root: Path) -> Path:
    try:
        return _prepare_verified_media_toolchain(resource_root)
    except Le13AcceptanceFailure:
        _reject("LE-14 packaged media toolchain is unavailable")


def ensure_silero_vad_assets() -> Path:
    try:
        return _ensure_silero_vad_assets()
    except (OSError, SileroVadAssetContractRejected, SileroVadAssetUnavailable):
        _reject("LE-14 Silero VAD runtime is unavailable")


def _remaining_cleanup_seconds(deadline: float, maximum: float) -> float:
    return max(0.0, min(maximum, deadline - time.monotonic()))


def _windows_descendant_process_ids(
    root_pid: int,
    *,
    deadline: float | None = None,
) -> tuple[int, ...]:
    """Snapshot the bounded descendants used if Windows tree termination fails."""

    timeout = (
        PROCESS_STOP_TIMEOUT_SECONDS
        if deadline is None
        else _remaining_cleanup_seconds(deadline, PROCESS_STOP_TIMEOUT_SECONDS)
    )
    if timeout <= 0:
        return ()
    environment = os.environ.copy()
    environment[WINDOWS_CLEANUP_ROOT_PID_ENVIRONMENT] = str(root_pid)
    script = (
        "$root = [int]$env:AUTOMATION_TOOL_CLEANUP_ROOT_PID; "
        "$records = @(Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId); "
        "$owned = @($root); "
        "do { "
        "$before = $owned.Count; "
        "$parents = $owned; "
        "$owned += @($records | Where-Object { "
        "$parents -contains [int]$_.ParentProcessId "
        "} | ForEach-Object { [int]$_.ProcessId }); "
        "$owned = @($owned | Sort-Object -Unique); "
        "} while ($owned.Count -gt $before); "
        "$owned | Where-Object { $_ -ne $root } | Sort-Object -Descending"
    )
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if completed.returncode != 0:
        return ()
    descendants: list[int] = []
    for line in completed.stdout.splitlines():
        rendered = line.strip()
        if not rendered.isdigit():
            continue
        process_id = int(rendered)
        if process_id <= 0 or process_id == root_pid or process_id in descendants:
            continue
        descendants.append(process_id)
        if len(descendants) >= MAXIMUM_WINDOWS_DESCENDANTS:
            break
    return tuple(descendants)


def _taskkill_tree(
    process_id: int,
    *,
    deadline: float | None = None,
) -> bool:
    timeout = (
        CLEANUP_TIMEOUT_SECONDS
        if deadline is None
        else _remaining_cleanup_seconds(deadline, CLEANUP_TIMEOUT_SECONDS)
    )
    if timeout <= 0:
        return False
    try:
        completed = subprocess.run(
            ["taskkill", "/PID", str(process_id), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    """Stop only the process tree placed in this driver's private group."""

    if os.name == "nt":
        deadline = time.monotonic() + CLEANUP_TIMEOUT_SECONDS
        cleanup_failed = False
        descendants = _windows_descendant_process_ids(
            process.pid,
            deadline=deadline,
        )
        tree_stopped = _taskkill_tree(process.pid, deadline=deadline)
        if not tree_stopped:
            late_descendants = _windows_descendant_process_ids(
                process.pid,
                deadline=deadline,
            )
            descendants = tuple(dict.fromkeys((*late_descendants, *descendants)))
            try:
                process.kill()
            except OSError:
                cleanup_failed = True
            for process_id in descendants:
                if _remaining_cleanup_seconds(deadline, CLEANUP_TIMEOUT_SECONDS) <= 0:
                    cleanup_failed = True
                    break
                if not _taskkill_tree(process_id, deadline=deadline):
                    cleanup_failed = True
        wait_timeout = _remaining_cleanup_seconds(
            deadline,
            PROCESS_KILL_TIMEOUT_SECONDS,
        )
        if wait_timeout <= 0:
            _reject("LE-14 real acceptance failed")
        try:
            process.wait(timeout=wait_timeout)
        except (OSError, subprocess.TimeoutExpired):
            for process_id in descendants:
                if _remaining_cleanup_seconds(deadline, CLEANUP_TIMEOUT_SECONDS) <= 0:
                    cleanup_failed = True
                    break
                if not _taskkill_tree(process_id, deadline=deadline):
                    cleanup_failed = True
            try:
                process.kill()
            except OSError:
                cleanup_failed = True
            wait_timeout = _remaining_cleanup_seconds(
                deadline,
                PROCESS_KILL_TIMEOUT_SECONDS,
            )
            if wait_timeout <= 0:
                cleanup_failed = True
            else:
                try:
                    process.wait(timeout=wait_timeout)
                except (OSError, subprocess.TimeoutExpired):
                    cleanup_failed = True
        if cleanup_failed:
            _reject("LE-14 real acceptance failed")
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        with contextlib.suppress(OSError):
            process.terminate()
    try:
        process.wait(timeout=PROCESS_STOP_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        with contextlib.suppress(OSError):
            process.kill()
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        process.wait(timeout=PROCESS_KILL_TIMEOUT_SECONDS)


def _cleanup_postgres_resources(
    pytest_pid: int,
    windows_postgres_root: Path,
) -> None:
    """Remove only the Docker Compose project derived from the owned child PID."""

    if os.name == "nt":
        try:
            root_metadata = windows_postgres_root.lstat()
            data_directory = windows_postgres_root / "data"
            data_metadata = data_directory.lstat()
        except OSError:
            return
        pg_ctl = shutil.which("pg_ctl")
        if (
            stat.S_ISLNK(root_metadata.st_mode)
            or not stat.S_ISDIR(root_metadata.st_mode)
            or stat.S_ISLNK(data_metadata.st_mode)
            or not stat.S_ISDIR(data_metadata.st_mode)
            or pg_ctl is None
            or not os.path.isabs(pg_ctl)
        ):
            return
        command = [
            pg_ctl,
            "--pgdata",
            os.fspath(data_directory),
            "--mode",
            "fast",
            "--wait",
            "stop",
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=CLEANUP_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired):
            completed = None
        if completed is None or completed.returncode != 0:
            immediate_command = [*command]
            immediate_command[immediate_command.index("fast")] = "immediate"
            try:
                completed = subprocess.run(
                    immediate_command,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=CLEANUP_TIMEOUT_SECONDS,
                )
            except (OSError, subprocess.TimeoutExpired):
                _reject("LE-14 real acceptance failed")
            if completed.returncode != 0:
                _reject("LE-14 real acceptance failed")
        return
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PYTEST_")
        and key
        not in {
            SECRET_PATH_ENVIRONMENT,
            TOOLCHAIN_ROOT_ENVIRONMENT,
            VOICE_PATH_ENVIRONMENT,
        }
    }
    environment.update(
        {
            "AUTOMATION_TOOL_DEV_DB_USER": "cleanup",
            "AUTOMATION_TOOL_DEV_DB_PASSWORD": "cleanup",
            "AUTOMATION_TOOL_DEV_DB_NAME": "cleanup",
            "AUTOMATION_TOOL_DEV_DB_PORT": "1",
            "AUTOMATION_TOOL_TEST_DB_USER": "cleanup",
            "AUTOMATION_TOOL_TEST_DB_PASSWORD": "cleanup",
            "AUTOMATION_TOOL_TEST_DB_NAME": "cleanup",
            "AUTOMATION_TOOL_TEST_DB_PORT": "1",
        }
    )
    try:
        subprocess.run(
            [
                "docker",
                "compose",
                "--project-name",
                f"automation-tool-pytest-{pytest_pid}",
                "--env-file",
                os.devnull,
                "--file",
                os.fspath(REPOSITORY_ROOT / "compose.yaml"),
                "down",
                "--volumes",
                "--remove-orphans",
            ],
            cwd=REPOSITORY_ROOT,
            env=environment,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=CLEANUP_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def run_acceptance(secret_path: Path) -> None:
    """Run the isolated real acceptance without placing the key in argv or env."""

    api_key = read_bailian_api_key(secret_path)
    ensure_silero_vad_assets()
    with tempfile.TemporaryDirectory(
        prefix="automation-tool-le14-acceptance-"
    ) as directory:
        root = Path(directory)
        toolchain = prepare_verified_media_toolchain(root / "runtime").resolve()
        voice = prepare_voice_fixture(root / "human-speech.flac").resolve()
        windows_postgres_root = root / "windows-postgres"
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("PYTEST_")
        }
        environment[SECRET_PATH_ENVIRONMENT] = os.fspath(secret_path)
        environment[TOOLCHAIN_ROOT_ENVIRONMENT] = os.fspath(toolchain)
        environment[VOICE_PATH_ENVIRONMENT] = os.fspath(voice)
        environment[WINDOWS_POSTGRES_ROOT_ENVIRONMENT] = os.fspath(
            windows_postgres_root
        )
        command = [
            sys.executable,
            "-m",
            "pytest",
            ACCEPTANCE_TEST,
            "-q",
            "-s",
            "-o",
            "addopts=",
        ]
        process: subprocess.Popen[str] | None = None
        cancellation_requested = False
        cleanup_started = False
        resources_cleaned = False
        handler_restored = False

        def request_cancellation(
            _signal_number: int,
            _frame: object,
        ) -> None:
            nonlocal cancellation_requested
            cancellation_requested = True
            if process is not None and not cleanup_started:
                _reject("LE-14 real acceptance failed")

        previous_sigterm_handler = signal.signal(
            signal.SIGTERM,
            request_cancellation,
        )

        def restore_sigterm_handler() -> None:
            nonlocal handler_restored
            if not handler_restored:
                signal.signal(signal.SIGTERM, previous_sigterm_handler)
                handler_restored = True

        def cleanup_owned_resources() -> None:
            nonlocal cleanup_started, resources_cleaned
            if process is None or resources_cleaned:
                return
            cleanup_started = True
            try:
                _terminate_process_tree(process)
            finally:
                try:
                    _cleanup_postgres_resources(
                        process.pid,
                        windows_postgres_root,
                    )
                finally:
                    resources_cleaned = True

        try:
            process = subprocess.Popen(
                command,
                cwd=BACKEND_ROOT,
                env=environment,
                stdin=subprocess.DEVNULL,
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
            if cancellation_requested:
                _reject("LE-14 real acceptance failed")
            timed_out = False
            try:
                stdout, stderr = process.communicate(timeout=ACCEPTANCE_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                timed_out = True
                stdout = ""
                stderr = ""
            if timed_out:
                cleanup_owned_resources()
                _reject("LE-14 real acceptance failed")
            returncode = process.returncode
            if returncode is None:
                cleanup_owned_resources()
                _reject("LE-14 real acceptance failed")
            completed = subprocess.CompletedProcess(
                command,
                returncode,
                stdout,
                stderr,
            )
        except BaseException:
            cleanup_owned_resources()
            raise
        finally:
            restore_sigterm_handler()
    combined = completed.stdout + completed.stderr
    if api_key in combined or os.fspath(secret_path) in combined:
        _reject("LE-14 acceptance output leaked private input")
    if (
        completed.returncode != 0
        or _PASS_SUMMARY_PATTERN.search(completed.stdout) is None
    ):
        _reject("LE-14 real acceptance failed")
    print(completed.stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secret", type=Path, default=DEFAULT_SECRET)
    arguments = parser.parse_args()
    try:
        secret_path = Path(os.path.abspath(arguments.secret))
        run_acceptance(secret_path)
    except (OSError, Le14AcceptanceFailure) as error:
        message = (
            str(error)
            if isinstance(error, Le14AcceptanceFailure) and str(error)
            else "LE-14 real acceptance failed"
        )
        raise SystemExit(message) from None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
