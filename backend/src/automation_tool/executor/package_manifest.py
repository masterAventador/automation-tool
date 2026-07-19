"""Deterministic signed inventory for a frozen Local Executor directory."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

EXECUTOR_MANIFEST_FILE_NAME = "executor-manifest.v1.json"
EXECUTOR_SIGNATURE_FILE_NAME = "executor-manifest.v1.sig"
MANIFEST_VERSION = "1"
SIGNATURE_PREFIX = b"atems1"
MAX_PACKAGE_FILES = 10_000
MAX_PACKAGE_BYTES = 8 * 1024 * 1024 * 1024
_DIGEST_DOMAIN = b"automation-tool.executor-package.v1\0"
_BUFFER_SIZE = 1024 * 1024
_SUPPORTED_PLATFORMS = frozenset(("macos", "windows"))
_SUPPORTED_ARCHITECTURES = frozenset(("aarch64", "x86_64"))
_BUILD_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_SEMVER_PATTERN = re.compile(
    r"(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)"
    r"(?:-(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)
_RESERVED_METADATA = frozenset((EXECUTOR_MANIFEST_FILE_NAME, EXECUTOR_SIGNATURE_FILE_NAME))
_WINDOWS_FORBIDDEN_PATH_CHARACTERS = frozenset('\\"*:<>?|')


class ExecutorManifestRejected(ValueError):
    """Fixed failure boundary for build-time package inventory generation."""

    def __init__(self) -> None:
        super().__init__("Executor manifest is rejected")


@dataclass(frozen=True, slots=True)
class ExecutorPackageFile:
    path: str
    size: int
    sha256: str

    def as_document(self) -> dict[str, object]:
        return {"path": self.path, "sha256": self.sha256, "size": self.size}


@dataclass(frozen=True, slots=True)
class SignedExecutorManifest:
    manifest_bytes: bytes
    signature_bytes: bytes
    signature_envelope: bytes


def _reject() -> ExecutorManifestRejected:
    return ExecutorManifestRejected()


def _valid_semver(value: str) -> bool:
    return len(value) <= 64 and _SEMVER_PATTERN.fullmatch(value) is not None


def _validate_identity(
    *,
    executor_version: str,
    build_id: str,
    target_platform: str,
    target_architecture: str,
) -> str:
    if (
        not _valid_semver(executor_version)
        or _BUILD_ID_PATTERN.fullmatch(build_id) is None
        or target_platform not in _SUPPORTED_PLATFORMS
        or target_architecture not in _SUPPORTED_ARCHITECTURES
    ):
        raise _reject()
    suffix = ".exe" if target_platform == "windows" else ""
    return f"automation-tool-executor{suffix}"


def _portable_relative_path(root: Path, path: Path) -> str:
    relative = path.relative_to(root)
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise _reject()
    rendered = relative.as_posix()
    try:
        encoded = rendered.encode("ascii")
    except UnicodeEncodeError:
        raise _reject() from None
    if (
        len(encoded) > 4096
        or any(len(part.encode("ascii")) > 255 for part in relative.parts)
        or any(
            ord(character) < 32 or character in _WINDOWS_FORBIDDEN_PATH_CHARACTERS
            for character in rendered
        )
    ):
        raise _reject()
    return rendered


def _same_file_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        stat.S_ISREG(left.st_mode)
        and stat.S_ISREG(right.st_mode)
        and left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and left.st_size == right.st_size
    )


def _hash_stable_regular_file(path: Path, expected: os.stat_result) -> tuple[int, str]:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        opened = os.fstat(source.fileno())
        if not _same_file_identity(expected, opened):
            raise _reject()
        while chunk := source.read(_BUFFER_SIZE):
            digest.update(chunk)
        after = os.fstat(source.fileno())
    current = path.lstat()
    if not _same_file_identity(opened, after) or not _same_file_identity(opened, current):
        raise _reject()
    return opened.st_size, digest.hexdigest()


def _collect_package_files(bundle_directory: Path) -> tuple[ExecutorPackageFile, ...]:
    try:
        root_metadata = bundle_directory.lstat()
        if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
            raise _reject()
        candidates = sorted(
            bundle_directory.rglob("*"),
            key=lambda candidate: candidate.relative_to(bundle_directory).as_posix(),
        )
        files: list[ExecutorPackageFile] = []
        for candidate in candidates:
            metadata = candidate.lstat()
            relative = _portable_relative_path(bundle_directory, candidate)
            if stat.S_ISLNK(metadata.st_mode):
                raise _reject()
            if stat.S_ISDIR(metadata.st_mode):
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise _reject()
            if relative in _RESERVED_METADATA:
                continue
            size, sha256 = _hash_stable_regular_file(candidate, metadata)
            files.append(ExecutorPackageFile(path=relative, size=size, sha256=sha256))
            if len(files) > MAX_PACKAGE_FILES:
                raise _reject()
        return tuple(files)
    except ExecutorManifestRejected:
        raise
    except (OSError, OverflowError, ValueError):
        raise _reject() from None


def _inventory_digest(files: tuple[ExecutorPackageFile, ...]) -> str:
    digest = hashlib.sha256(_DIGEST_DOMAIN)
    for package_file in files:
        path = package_file.path.encode("ascii")
        digest.update(len(path).to_bytes(4, "big"))
        digest.update(path)
        digest.update(package_file.size.to_bytes(8, "big"))
        digest.update(bytes.fromhex(package_file.sha256))
    return digest.hexdigest()


def _canonical_manifest_bytes(document: dict[str, object]) -> bytes:
    return (
        json.dumps(document, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode("ascii")


def _signature_envelope(signature: bytes) -> bytes:
    encoded = base64.urlsafe_b64encode(signature).rstrip(b"=")
    return SIGNATURE_PREFIX + b"." + encoded + b"\n"


def write_signed_executor_manifest(
    *,
    bundle_directory: Path,
    executor_version: str,
    build_id: str,
    target_platform: str,
    target_architecture: str,
    signing_private_key: bytes,
) -> SignedExecutorManifest:
    """Inventory and sign one complete onedir payload with an offline Ed25519 seed."""

    entrypoint = _validate_identity(
        executor_version=executor_version,
        build_id=build_id,
        target_platform=target_platform,
        target_architecture=target_architecture,
    )
    if len(signing_private_key) != 32:
        raise _reject()
    files = _collect_package_files(bundle_directory)
    entry = next((package_file for package_file in files if package_file.path == entrypoint), None)
    package_size = sum(package_file.size for package_file in files)
    if entry is None or entry.size == 0 or package_size > MAX_PACKAGE_BYTES:
        raise _reject()
    document: dict[str, object] = {
        "architecture": target_architecture,
        "build_id": build_id,
        "entrypoint": entrypoint,
        "executor_version": executor_version,
        "files": [package_file.as_document() for package_file in files],
        "manifest_version": MANIFEST_VERSION,
        "package_sha256": _inventory_digest(files),
        "package_size": package_size,
        "platform": target_platform,
    }
    manifest_bytes = _canonical_manifest_bytes(document)
    try:
        signature_bytes = Ed25519PrivateKey.from_private_bytes(signing_private_key).sign(
            manifest_bytes
        )
        signature_envelope = _signature_envelope(signature_bytes)
        (bundle_directory / EXECUTOR_MANIFEST_FILE_NAME).write_bytes(manifest_bytes)
        (bundle_directory / EXECUTOR_SIGNATURE_FILE_NAME).write_bytes(signature_envelope)
    except OSError:
        raise _reject() from None
    return SignedExecutorManifest(
        manifest_bytes=manifest_bytes,
        signature_bytes=signature_bytes,
        signature_envelope=signature_envelope,
    )


def _parser() -> argparse.ArgumentParser:  # pragma: no cover - verified through the real CLI
    parser = argparse.ArgumentParser(description="Generate a signed Local Executor manifest")
    parser.add_argument("--bundle-dir", required=True, type=Path)
    parser.add_argument("--executor-version", required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--platform", required=True, dest="target_platform")
    parser.add_argument("--architecture", required=True, dest="target_architecture")
    return parser


def _read_signing_key(
    source: BinaryIO,
) -> bytes:  # pragma: no cover - verified through the real CLI
    key = source.read(33)
    if len(key) != 32:
        raise _reject()
    return key


def main() -> None:  # pragma: no cover - verified through the real CLI
    arguments = _parser().parse_args()
    try:
        signing_key = _read_signing_key(sys.stdin.buffer)
        write_signed_executor_manifest(
            bundle_directory=arguments.bundle_dir,
            executor_version=arguments.executor_version,
            build_id=arguments.build_id,
            target_platform=arguments.target_platform,
            target_architecture=arguments.target_architecture,
            signing_private_key=signing_key,
        )
    except ExecutorManifestRejected:
        sys.stderr.write("Executor manifest generation failed\n")
        raise SystemExit(2) from None
    sys.stdout.write("Executor manifest generated\n")


if __name__ == "__main__":  # pragma: no cover - verified through the real CLI
    main()
