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
import tempfile
from pathlib import Path

from check_video_media_toolchain import (
    CONTRACT_PATH as MEDIA_TOOLCHAIN_CONTRACT,
)
from check_video_media_toolchain import (
    ContractError as MediaToolchainContractError,
)
from check_video_media_toolchain import (
    validate_candidate as validate_toolchain_candidate,
)
from check_video_media_toolchain import (
    validate_contract as validate_toolchain_contract,
)
from prepare_video_runtime import (
    MEDIA_TOOLCHAIN_TARGETS,
    VideoRuntimeUnavailable,
    host_platform,
)
from prepare_video_runtime import install as install_video_runtime
from prepare_video_runtime import prepare as prepare_video_runtime

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
DEFAULT_SECRET = REPOSITORY_ROOT / ".local/secrets/bailian-model.json"
ACCEPTANCE_TEST = "tests/integration/test_material_understanding_real_acceptance.py"
SECRET_PATH_ENVIRONMENT = "AUTOMATION_TOOL_LE13_SECRET_PATH"
TOOLCHAIN_ROOT_ENVIRONMENT = "AUTOMATION_TOOL_LE13_TOOLCHAIN_ROOT"
_API_KEY_PATTERN = re.compile(r"^sk-[A-Za-z0-9._-]{17,253}$")
_PASS_SUMMARY_PATTERN = re.compile(r"^1 passed in \d+(?:\.\d+)?s$", re.MULTILINE)
_MAX_SECRET_BYTES = 16 * 1024


class Le13AcceptanceFailure(RuntimeError):
    """A fixed failure that does not reflect credentials or private paths."""


def read_bailian_api_key(secret_path: Path) -> str:
    """Read one explicitly selected private credential document."""
    descriptor: int | None = None
    path_metadata: os.stat_result | None = None
    try:
        if os.name == "nt":
            path_metadata = secret_path.lstat()
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            if stat.S_ISLNK(path_metadata.st_mode) or bool(
                getattr(path_metadata, "st_file_attributes", 0) & reparse_flag
            ):
                raise Le13AcceptanceFailure("LE-13 model credential is unavailable")
            from automation_tool.executor.windows_acl import validate_private_acl

            validate_private_acl(secret_path)
        no_follow = int(getattr(os, "O_NOFOLLOW", 0))
        if os.name != "nt" and no_follow == 0:
            path_metadata = secret_path.lstat()
            if stat.S_ISLNK(path_metadata.st_mode):
                raise Le13AcceptanceFailure("LE-13 model credential is unavailable")
        flags = os.O_RDONLY | int(getattr(os, "O_CLOEXEC", 0))
        if os.name != "nt":
            flags |= no_follow
        descriptor = os.open(secret_path, flags)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > _MAX_SECRET_BYTES
            or (os.name != "nt" and stat.S_IMODE(metadata.st_mode) != 0o600)
            or (
                path_metadata is not None
                and not os.path.samestat(path_metadata, metadata)
            )
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
        final_metadata = os.fstat(descriptor)
        if (
            len(raw_document) > _MAX_SECRET_BYTES
            or final_metadata.st_size != len(raw_document)
            or not os.path.samestat(metadata, final_metadata)
        ):
            raise Le13AcceptanceFailure("LE-13 model credential is unavailable")
        document = json.loads(raw_document.decode("utf-8"))
    except (
        ImportError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ):
        raise Le13AcceptanceFailure("LE-13 model credential is unavailable") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    api_key = document.get("apiKey") if isinstance(document, dict) else None
    if (
        not isinstance(document, dict)
        or document.get("provider") != "bailian"
        or not isinstance(api_key, str)
        or _API_KEY_PATTERN.fullmatch(api_key) is None
    ):
        raise Le13AcceptanceFailure("LE-13 model credential is invalid")
    return api_key


def prepare_verified_media_toolchain(resource_root: Path) -> Path:
    """Install and verify the current pinned FFmpeg pair in one private run root."""
    try:
        platform = host_platform()
        staging = prepare_video_runtime(
            platform=platform,
            only=("media-toolchain",),
        )
        installed = install_video_runtime(
            staging=staging,
            resource_root=resource_root,
            only=("media-toolchain",),
            platform=platform,
        )
        document = json.loads(MEDIA_TOOLCHAIN_CONTRACT.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise MediaToolchainContractError("toolchain contract must be an object")
        validate_toolchain_contract(document)
        toolchain = Path(installed["media-toolchain"])
        validate_toolchain_candidate(
            toolchain,
            MEDIA_TOOLCHAIN_TARGETS[platform],
            document,
        )
        return toolchain
    except (
        KeyError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        MediaToolchainContractError,
        VideoRuntimeUnavailable,
        subprocess.TimeoutExpired,
    ):
        raise Le13AcceptanceFailure(
            "LE-13 packaged media toolchain is unavailable"
        ) from None


def run_acceptance(secret_path: Path) -> None:
    """Run the isolated pytest entry without placing the key in argv or env."""
    api_key = read_bailian_api_key(secret_path)
    with tempfile.TemporaryDirectory(
        prefix="automation-tool-le13-toolchain-"
    ) as directory:
        toolchain = prepare_verified_media_toolchain(Path(directory)).resolve()
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("PYTEST_")
        }
        environment[SECRET_PATH_ENVIRONMENT] = os.fspath(secret_path)
        environment[TOOLCHAIN_ROOT_ENVIRONMENT] = os.fspath(toolchain)
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                ACCEPTANCE_TEST,
                "-q",
                "-s",
                "-o",
                "addopts=",
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
    if (
        completed.returncode != 0
        or _PASS_SUMMARY_PATTERN.search(completed.stdout) is None
    ):
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
