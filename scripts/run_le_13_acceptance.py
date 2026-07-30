#!/usr/bin/env python3
"""Run LE-13 through real media, Bailian and an isolated PostgreSQL."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
DEFAULT_SECRET = REPOSITORY_ROOT / ".local/secrets/bailian-model.json"
ACCEPTANCE_TEST = "tests/integration/test_material_understanding_real_acceptance.py"
SECRET_PATH_ENVIRONMENT = "AUTOMATION_TOOL_LE13_SECRET_PATH"
_API_KEY_PATTERN = re.compile(r"^sk-[A-Za-z0-9._-]{17,253}$")
_MAX_SECRET_BYTES = 16 * 1024


class Le13AcceptanceFailure(RuntimeError):
    """A fixed failure that does not reflect credentials or private paths."""


def read_bailian_api_key(secret_path: Path) -> str:
    """Read one explicitly selected private credential document."""
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(secret_path, flags)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > _MAX_SECRET_BYTES
            or (os.name != "nt" and stat.S_IMODE(metadata.st_mode) != 0o600)
        ):
            raise Le13AcceptanceFailure("LE-13 model credential is unavailable")
        chunks: list[bytes] = []
        remaining = _MAX_SECRET_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw_document = b"".join(chunks)
        if len(raw_document) > _MAX_SECRET_BYTES:
            raise Le13AcceptanceFailure("LE-13 model credential is unavailable")
        document = json.loads(raw_document.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        raise Le13AcceptanceFailure("LE-13 model credential is unavailable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    api_key = document.get("apiKey") if isinstance(document, dict) else None
    if (
        not isinstance(document, dict)
        or document.get("provider") != "bailian"
        or not isinstance(api_key, str)
        or _API_KEY_PATTERN.fullmatch(api_key) is None
    ):
        raise Le13AcceptanceFailure("LE-13 model credential is invalid")
    return api_key


def run_acceptance(secret_path: Path) -> None:
    """Run the isolated pytest entry without placing the key in argv or env."""
    api_key = read_bailian_api_key(secret_path)
    environment = os.environ.copy()
    environment[SECRET_PATH_ENVIRONMENT] = os.fspath(secret_path)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            ACCEPTANCE_TEST,
            "-q",
            "-s",
        ],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = completed.stdout + completed.stderr
    if api_key in combined:
        raise Le13AcceptanceFailure("LE-13 acceptance output leaked its credential")
    if completed.returncode != 0:
        raise Le13AcceptanceFailure("LE-13 real acceptance failed")
    print(completed.stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secret", type=Path, default=DEFAULT_SECRET)
    arguments = parser.parse_args()
    try:
        secret_path = Path(os.path.abspath(arguments.secret))
        run_acceptance(secret_path)
    except (OSError, Le13AcceptanceFailure) as error:
        message = (
            str(error)
            if isinstance(error, Le13AcceptanceFailure) and str(error)
            else "LE-13 real acceptance failed"
        )
        raise SystemExit(message) from None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
