from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from automation_tool.executor.package_manifest import (
    EXECUTOR_MANIFEST_FILE_NAME,
    EXECUTOR_SIGNATURE_FILE_NAME,
)

PRIVATE_SIGNING_KEY = bytes(range(32))


def command(bundle: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "automation_tool.executor.package_manifest",
        "--bundle-dir",
        str(bundle),
        "--executor-version",
        "1.2.3-rc.1+demo",
        "--build-id",
        "commit-0123456789abcdef",
        "--platform",
        "macos",
        "--architecture",
        "aarch64",
    ]


def test_manifest_cli_reads_the_offline_key_only_from_stdin(tmp_path: Path) -> None:
    bundle = tmp_path / "automation-tool-executor"
    bundle.mkdir()
    (bundle / "automation-tool-executor").write_bytes(b"frozen-entrypoint")

    completed = subprocess.run(
        command(bundle),
        input=PRIVATE_SIGNING_KEY,
        capture_output=True,
        check=False,
        timeout=20,
    )

    assert completed.returncode == 0
    assert completed.stdout == b"Executor manifest generated\n"
    assert completed.stderr == b""
    assert PRIVATE_SIGNING_KEY not in completed.stdout
    assert PRIVATE_SIGNING_KEY not in completed.stderr
    document = json.loads((bundle / EXECUTOR_MANIFEST_FILE_NAME).read_bytes())
    assert document["executor_version"] == "1.2.3-rc.1+demo"
    assert (bundle / EXECUTOR_SIGNATURE_FILE_NAME).read_bytes().startswith(b"atems1.")


def test_manifest_cli_fails_closed_without_writing_partial_metadata(tmp_path: Path) -> None:
    bundle = tmp_path / "automation-tool-executor"
    bundle.mkdir()
    (bundle / "automation-tool-executor").write_bytes(b"frozen-entrypoint")

    completed = subprocess.run(
        command(bundle),
        input=b"private-key-is-not-32-bytes",
        capture_output=True,
        check=False,
        timeout=20,
    )

    assert completed.returncode == 2
    assert completed.stdout == b""
    assert completed.stderr == b"Executor manifest generation failed\n"
    assert b"private-key" not in completed.stderr
    assert not (bundle / EXECUTOR_MANIFEST_FILE_NAME).exists()
    assert not (bundle / EXECUTOR_SIGNATURE_FILE_NAME).exists()
